#!/usr/bin/env python3
"""lab.py -- in-process render harness for tuning the ambient theme.

Not a deliverable; a fast loop for the verifier's listenability work.
    python3 tools/lab.py steady 60      -> render 60s of constant medium
                                           activity and run the battery
    python3 tools/lab.py idle 40
    python3 tools/lab.py layers         -> per-layer RMS + octave bands
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SONIFIER_QUIET", "1")

import numpy as np  # noqa: E402
import sonifier as S  # noqa: E402
from analyze_render import (run_battery, arc, print_arc, mono, rms_db,  # noqa: E402
                            welch_psd, octave_bands, spectral_slope, db)

SR = S.SAMPLE_RATE
BS = S.BLOCKSIZE


def render(theme, dur_s, script=None):
    """script: list of (t, event dict). Returns (n,2) float32."""
    script = sorted(script or [], key=lambda x: x[0])
    nblocks = int(dur_s * SR / BS)
    out = np.zeros((nblocks * BS, 2), dtype=np.float32)
    i = 0
    for b in range(nblocks):
        t_end = (b + 1) * BS / SR
        while i < len(script) and script[i][0] < t_end:
            theme.handle_event(script[i][1])
            i += 1
        out[b * BS:(b + 1) * BS] = theme.render_block(BS)
    return out


def ev(name, **kw):
    d = {"hook_event_name": name, "session_id": "lab"}
    d.update(kw)
    return d


def steady_script(dur, period=2.0, tools=("Read", "Edit", "Grep", "Write"), seed=5):
    # Jittered (exponential) spacing, not a metronome: a perfectly periodic
    # event stream would inject a deterministic AM component at 1/period Hz,
    # which is exactly what section 7 item 3 is looking for, and no real
    # session is periodic.
    rng = np.random.default_rng(seed)
    s = [(0.0, ev("SessionStart"))]
    t = 1.0
    k = 0
    while t < dur - 2:
        tn = tools[k % len(tools)]
        s.append((t, ev("PreToolUse", tool_name=tn, tool_input={"file_path": "x.py"})))
        s.append((t + 0.3, ev("PostToolUse", tool_name=tn, tool_input={"file_path": "x.py"})))
        t += period * float(rng.exponential(1.0)) * 0.9 + 0.15 * period
        k += 1
    return s


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "steady"
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    seed = int(os.environ.get("LAB_SEED", "1234"))
    th = S.AmbientTheme(seed=seed, quiet=True, volume=1.0)
    if mode == "steady":
        script = steady_script(dur, period=float(os.environ.get("LAB_PERIOD", "2.0")))
    elif mode == "idle":
        script = [(0.0, ev("SessionStart"))]
    elif mode == "hot":
        script = steady_script(dur, period=0.8)
    else:
        raise SystemExit("mode?")
    t0 = time.perf_counter()
    x = render(th, dur, script)
    wall = time.perf_counter() - t0
    print(f"# {mode} {dur:.0f}s seed={seed}  render {wall:.1f}s "
          f"({wall / (dur * SR / BS) * 1000:.2f} ms/block)  "
          f"peak={db(np.max(np.abs(x))):.2f} rms={rms_db(mono(x)):.2f}")
    m = mono(x[int(5 * SR):])
    f, p = welch_psd(m, SR)
    cf, bdb = octave_bands(f, p)
    slope, _, _, dev = spectral_slope(m, SR)
    print("  bands: " + "  ".join(f"{c:.0f}Hz {d:.1f}" for c, d in zip(cf, bdb)))
    print(f"  slope {slope:+.2f} dB/oct  maxdev {np.max(np.abs(dev)):.2f}")
    rep = run_battery(x, SR, steady=(float(os.environ.get("LAB_T0", "25")), dur - 1))
    print(rep.render())
    if os.environ.get("LAB_ARC"):
        print_arc(arc(x, SR, 10.0))
    if len(sys.argv) > 3:
        from analyze_render import load_wav  # noqa
        import wave
        pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        with wave.open(sys.argv[3], "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        print(f"# wrote {sys.argv[3]}")


if __name__ == "__main__":
    main()
