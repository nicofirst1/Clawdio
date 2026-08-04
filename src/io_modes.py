"""Offline render / live daemon / --check modes: run_render(), run_live(),
run_check(), the HTTP+UDP event ingress server, and theme-state factory.
Split out of sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

try:
    import sounddevice as sd  # type: ignore
    _SD_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment dependent
    sd = None
    _SD_IMPORT_ERROR = exc

from config import SAMPLE_RATE, BLOCKSIZE, MAX_BODY_BYTES, HTTP_READ_TIMEOUT, THEME_GEIGER, load_config
from geiger import GeigerTheme, render_block
from ambient import AmbientTheme
from logging_setup import get_logger

log = get_logger("sonifier")


def _build_theme_state(cfg, seed):
    """Theme factory used by both run_render and run_live: picks GeigerTheme
    or AmbientTheme per cfg["theme"] (SONIFIER_THEME, default "ambient")."""
    kwargs = dict(
        sr=SAMPLE_RATE,
        volume=cfg["volume"],
        mute=cfg["mute"],
        clicks_enabled=cfg["clicks"],
        chimes_enabled=cfg["chimes"],
        drone_enabled=cfg["drone"],
        quiet=cfg["quiet"],
        seed=seed,
    )
    if cfg["theme"] == THEME_GEIGER:
        return GeigerTheme(**kwargs)
    return AmbientTheme(**kwargs)


# --------------------------------------------------------------------------
# Render (offline) mode
# --------------------------------------------------------------------------

def run_render(events_path, out_path, seed=0):
    events = []
    try:
        f = open(events_path, "r")
    except OSError as exc:
        log.error("cannot read events file %r: %s", events_path, exc)
        raise SystemExit(2)
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            t = obj.get("t")
            ev = obj.get("event")
            if t is None or ev is None:
                continue
            try:
                t = float(t)
            except (TypeError, ValueError):
                continue
            events.append((t, ev))
    events.sort(key=lambda x: x[0])

    cfg = load_config()
    state = _build_theme_state(cfg, seed)

    last_t = events[-1][0] if events else 0.0
    # Tail: 3 s normally; 6 s for the ambient theme when the script actually
    # ends the session, so its 4 s SessionEnd release has room to resolve into
    # real silence inside the rendered file (and the file then demonstrates
    # that silence == off). GeigerTheme keeps the v1 3 s tail exactly, so v1
    # renders stay byte-identical.
    ends = bool(events) and events[-1][1].get("hook_event_name") == "SessionEnd"
    duration = last_t + (6.0 if (ends and cfg["theme"] != THEME_GEIGER) else 3.0)
    total_samples = int(math.ceil(duration * SAMPLE_RATE))
    n_blocks = int(math.ceil(total_samples / BLOCKSIZE))

    out_buf = np.zeros((n_blocks * BLOCKSIZE, 2), dtype=np.float32)

    ev_idx = 0
    block_start_t = 0.0
    block_dur = BLOCKSIZE / SAMPLE_RATE
    for b in range(n_blocks):
        block_end_t = block_start_t + block_dur
        while ev_idx < len(events) and events[ev_idx][0] < block_end_t:
            state.handle_event(events[ev_idx][1])
            ev_idx += 1
        block = render_block(state, BLOCKSIZE)
        out_buf[b * BLOCKSIZE:(b + 1) * BLOCKSIZE, :] = block
        block_start_t = block_end_t

    out_buf = out_buf[:total_samples]
    _write_wav(out_path, out_buf, SAMPLE_RATE)
    log.info(
        "rendered %d events -> %s (%.2fs, %d samples)",
        len(events), out_path, duration, total_samples,
    )


def _write_wav(path, stereo_f32, sr):
    clipped = np.clip(stereo_f32, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())


# --------------------------------------------------------------------------
# Live mode: HTTP + UDP ingress, sounddevice output
# --------------------------------------------------------------------------

class _EventHTTPHandler(BaseHTTPRequestHandler):
    state: EngineState = None  # set by server factory
    protocol_version = "HTTP/1.0"  # no keep-alive: don't pin a thread per client
    timeout = HTTP_READ_TIMEOUT  # bound a slow/stalled client's socket reads

    def log_message(self, fmt, *args):  # silence default logging
        pass

    def do_POST(self):
        if self.path != "/event":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length < 0:
            length = 0
        if length > MAX_BODY_BYTES:
            # A hostile or buggy client can declare an arbitrarily large
            # Content-Length; reading it would block this thread and buffer
            # gigabytes. Hook payloads are small JSON objects, so refuse.
            self.send_response(413)
            self.end_headers()
            return
        try:
            body = self.rfile.read(length) if length > 0 else b""
        except Exception:
            return
        try:
            ev = json.loads(body.decode("utf-8"))
            self.state.handle_event(ev)
        except Exception:
            pass
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({"ok": True, "activity": float(self.state.activity)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()


def _make_http_server(state, port):
    handler_cls = type("BoundHandler", (_EventHTTPHandler,), {"state": state})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    return httpd


def _udp_recv_loop(state, port, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        log.error("UDP bind failed on port %s: %s", port, e)
        return
    sock.settimeout(0.5)
    while not stop_event.is_set():
        try:
            data, _addr = sock.recvfrom(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            ev = json.loads(data.decode("utf-8"))
            state.handle_event(ev)
        except Exception:
            log.debug("dropped malformed UDP datagram (%d bytes)", len(data), exc_info=True)
            continue
    sock.close()


def run_live():
    # Detach from the launching terminal's session/process group. The
    # autostart hook backgrounds us with nohup (blocks SIGHUP) but when the
    # hook itself runs non-interactively (no job control), the child stays
    # in the hook's process group rather than getting its own -- so a
    # terminal closing can still SIGHUP/SIGTERM the whole group and take us
    # with it even though nohup alone should have been enough. os.setsid()
    # makes us our own session/group leader, independent of any controlling
    # terminal. Safe to call unconditionally: run_live() is only ever the
    # live daemon entry point (--check/--render never reach here), and it
    # always starts as a regular (non-leader) child process from main().
    try:
        os.setsid()
    except OSError:
        pass  # already a session leader (e.g. re-exec, manual `setsid` launch)

    if sd is None:
        log.error(
            "sounddevice is not available (%s). Install it with:\n"
            "    pip install sounddevice --break-system-packages\n"
            "and ensure a PortAudio-capable audio device is present. "
            "Offline rendering (--render) works without sounddevice.",
            _SD_IMPORT_ERROR,
        )
        sys.exit(1)

    cfg = load_config()
    state = _build_theme_state(cfg, seed=int(time.time()))

    stop_event = threading.Event()
    idle_exit_s = cfg["idle_exit_min"] * 60.0

    def callback(outdata, frames, time_info, status):
        try:
            block = render_block(state, frames)
            outdata[:] = block
        except Exception:
            outdata[:] = np.zeros((frames, 2), dtype=np.float32)

    try:
        httpd = _make_http_server(state, cfg["port"])
    except OSError as exc:
        log.error(
            "cannot listen on port %s: %s\n"
            "Another sonifier (or unrelated process) is probably already "
            "using it. Either stop it (pkill -f sonifier.py) or pick another "
            "port with SONIFIER_PORT=9800 -- remember to export the same "
            "SONIFIER_PORT for hooks/send-event.sh so events still reach it.",
            cfg["port"], exc,
        )
        sys.exit(1)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    udp_thread = threading.Thread(
        target=_udp_recv_loop, args=(state, cfg["port"], stop_event), daemon=True
    )
    http_thread.start()
    udp_thread.start()

    log.info(
        "listening on port %s (HTTP POST /event, UDP, GET /health)", cfg["port"]
    )

    try:
        with sd.OutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCKSIZE,
            channels=2,
            dtype="float32",
            callback=callback,
        ):
            while True:
                time.sleep(1.0)
                if state.t - state.last_event_t > idle_exit_s and state.last_event_t > 0:
                    log.info("idle timeout reached, exiting")
                    break
                if state.last_event_t == 0.0 and state.t > idle_exit_s:
                    log.info("idle timeout reached, exiting")
                    break
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.shutdown()
        httpd.server_close()


# --------------------------------------------------------------------------
# --check mode
# --------------------------------------------------------------------------

def run_check():
    cfg = load_config()
    result = dict(cfg)
    result["sample_rate"] = SAMPLE_RATE
    result["blocksize"] = BLOCKSIZE
    result["sounddevice_importable"] = sd is not None
    device_ok = False
    device_error = None
    if sd is not None:
        try:
            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=2)
            device_ok = True
        except Exception as e:
            device_error = str(e)
    else:
        device_error = str(_SD_IMPORT_ERROR) if _SD_IMPORT_ERROR else "sounddevice not installed"
    result["audio_device_available"] = device_ok
    if device_error:
        result["audio_device_error"] = device_error
    print(json.dumps(result, indent=2))
    return 0

