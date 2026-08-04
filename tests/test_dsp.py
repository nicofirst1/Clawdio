"""Plain-pytest DSP tests for sonifier.py. No audio device required -- all
tests drive EngineState/render_block directly (or via run_render) in
offline/virtual-clock mode.
"""

import json
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import sonifier  # noqa: E402


SR = sonifier.SAMPLE_RATE
BLOCK = sonifier.BLOCKSIZE


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_state(seed=0, clicks=True, chimes=True, drone=False, volume=1.0):
    return sonifier.EngineState(
        sr=SR,
        volume=volume,
        mute=False,
        clicks_enabled=clicks,
        chimes_enabled=chimes,
        drone_enabled=drone,
        quiet=True,
        seed=seed,
    )


def render_n_blocks(state, n_blocks):
    """Render n_blocks worth of audio and return the concatenated buffer."""
    chunks = []
    for _ in range(n_blocks):
        chunks.append(sonifier.render_block(state, BLOCK))
    return np.concatenate(chunks, axis=0)


def render_events_offline(events, duration_s, seed=0, **state_kwargs):
    """events: list of (t_seconds, event_dict). Drives render_block with a
    virtual clock exactly like run_render(), returning the full (n,2) buffer.
    """
    state = make_state(seed=seed, **state_kwargs)
    events = sorted(events, key=lambda x: x[0])
    n_blocks = int(math.ceil(duration_s * SR / BLOCK))
    out = np.zeros((n_blocks * BLOCK, 2), dtype=np.float32)
    ev_idx = 0
    block_dur = BLOCK / SR
    block_start_t = 0.0
    for b in range(n_blocks):
        block_end_t = block_start_t + block_dur
        while ev_idx < len(events) and events[ev_idx][0] < block_end_t:
            state.handle_event(events[ev_idx][1])
            ev_idx += 1
        out[b * BLOCK:(b + 1) * BLOCK, :] = sonifier.render_block(state, BLOCK)
        block_start_t = block_end_t
    return out, state


def render_constant_activity(activity, duration_s, seed=0, clicks=True, chimes=False,
                              drone=False, tool_class=sonifier.CLASS_READ):
    """Render click train with activity pinned to a constant value each
    block (bypassing the leaky-integrator decay), for click-rate testing."""
    state = make_state(seed=seed, clicks=clicks, chimes=chimes, drone=drone)
    state.current_class = tool_class
    n_blocks = int(math.ceil(duration_s * SR / BLOCK))
    chunks = []
    for _ in range(n_blocks):
        state.activity = activity
        chunks.append(sonifier.render_block(state, BLOCK))
    return np.concatenate(chunks, axis=0)


def count_onsets(mono_or_stereo, threshold=0.04, min_gap_s=0.0015, sr=SR):
    """Count rising-edge threshold crossings (click grain onsets) with a
    refractory gap so a single grain isn't counted multiple times."""
    if mono_or_stereo.ndim == 2:
        sig = np.max(np.abs(mono_or_stereo), axis=1)
    else:
        sig = np.abs(mono_or_stereo)
    above = sig > threshold
    onsets = 0
    min_gap = int(min_gap_s * sr)
    last_onset = -min_gap - 1
    for i in range(1, len(above)):
        if above[i] and not above[i - 1] and (i - last_onset) > min_gap:
            onsets += 1
            last_onset = i
    return onsets


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_nonsilent_when_activity_high():
    audio = render_constant_activity(activity=1.0, duration_s=2.0, seed=1)
    peak = np.max(np.abs(audio))
    assert peak > 1e-2, f"expected audible output at high activity, peak={peak}"


def test_silence_when_idle_drone_off_after_chimes_decay():
    # Burst of activity + a failure chime, then a long idle stretch with no
    # further events. Drone stays off (default). After the leaky integrator
    # decays to zero (tau=3s -> snaps to 0 well within ~15s) and chimes
    # (max ~0.6s) finish, the tail should be true silence.
    events = [
        (0.0, {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {}}),
        (0.1, {"hook_event_name": "PostToolUse", "tool_name": "Write"}),
        (0.2, {"hook_event_name": "PostToolUseFailure", "tool_name": "Write"}),
        (0.3, {"hook_event_name": "Stop"}),
    ]
    audio, state = render_events_offline(events, duration_s=25.0, seed=2, drone=False)
    tail = audio[int(20.0 * SR):]
    peak = np.max(np.abs(tail))
    assert peak < 1e-4, f"expected near-silence in idle tail, peak={peak}"


def test_drone_stays_off_by_default_even_with_context_pressure():
    events = [(0.0, {"hook_event_name": "ContextPressure", "fill": 1.0})]
    # drone=False (default) explicitly.
    audio, state = render_events_offline(events, duration_s=5.0, seed=3, drone=False,
                                          clicks=False, chimes=False)
    assert np.max(np.abs(audio)) < 1e-6, "drone must stay off unless SONIFIER_DRONE=1"


def test_drone_audible_when_enabled_and_pressure_high():
    events = [(0.0, {"hook_event_name": "ContextPressure", "fill": 1.0})]
    audio, state = render_events_offline(events, duration_s=5.0, seed=3, drone=True,
                                          clicks=False, chimes=False)
    tail = audio[int(3.0 * SR):]
    assert np.max(np.abs(tail)) > 1e-3, "expected audible drone once slewed up at high fill"


def test_failure_chime_present_rms_spike_near_scripted_time():
    events = [
        (2.0, {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}),
    ]
    audio, state = render_events_offline(events, duration_s=5.0, seed=4, clicks=False,
                                          drone=False, chimes=True)
    mono = np.max(np.abs(audio), axis=1)

    def window_rms(center_s, half_width_s=0.25):
        lo = max(0, int((center_s - half_width_s) * SR))
        hi = min(len(mono), int((center_s + half_width_s) * SR))
        seg = mono[lo:hi]
        return float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) if len(seg) else 0.0

    rms_at_event = window_rms(2.1)
    rms_far_before = window_rms(0.5)
    rms_far_after = window_rms(4.5)
    assert rms_at_event > 0.02, f"expected chime energy near t=2.0, got {rms_at_event}"
    assert rms_at_event > rms_far_before * 5
    assert rms_at_event > rms_far_after * 5


def test_click_rate_monotonic_in_activity():
    low = render_constant_activity(activity=0.05, duration_s=4.0, seed=5, chimes=False)
    high = render_constant_activity(activity=1.0, duration_s=4.0, seed=5, chimes=False)
    low_onsets = count_onsets(low)
    high_onsets = count_onsets(high)
    assert high_onsets > low_onsets, (low_onsets, high_onsets)
    # sanity: roughly in the ballpark of the spec'd rates (0.5+14.5*a^1.5).
    # low rate ~0.66/s over 4s ~= 2-3 onsets; high rate ~15/s over 4s ~= 60.
    assert high_onsets > low_onsets * 3


def test_click_rate_capped_at_25_with_subagent_register():
    state = make_state(seed=6, clicks=True, chimes=False, drone=False)
    state.subagent_refcount = 1
    n_blocks = int(math.ceil(6.0 * SR / BLOCK))
    chunks = []
    for _ in range(n_blocks):
        state.activity = 1.0
        chunks.append(sonifier.render_block(state, BLOCK))
    audio = np.concatenate(chunks, axis=0)
    # min_gap_s wider than a single grain's ring-out (~12ms max) so one
    # physical click isn't counted as multiple onsets via its own ringing.
    onsets = count_onsets(audio, min_gap_s=0.008)
    rate = onsets / 6.0
    assert rate <= sonifier.MAX_CLICK_RATE + 5, f"click rate {rate}/s exceeds cap generously"


def test_malformed_event_ignored_no_exception():
    state = make_state(seed=7)
    bad_inputs = [
        None,
        42,
        "just a string",
        [],
        {},
        {"hook_event_name": None},
        {"hook_event_name": 12345},
        {"hook_event_name": "TotallyUnknownEvent"},
        {"tool_name": "Read"},  # missing hook_event_name
        {"hook_event_name": "ContextPressure", "fill": "not-a-number"},
        {"hook_event_name": "PreToolUse", "tool_name": None, "tool_input": "not-a-dict"},
    ]
    for bad in bad_inputs:
        state.handle_event(bad)  # must not raise
    # engine should still be renderable afterwards
    block = sonifier.render_block(state, BLOCK)
    assert block.shape == (BLOCK, 2)
    assert np.all(np.isfinite(block))


def test_malformed_json_lines_ignored_in_render(tmp_path):
    events_path = tmp_path / "events.jsonl"
    out_path = tmp_path / "out.wav"
    lines = [
        json.dumps({"t": 0.0, "event": {"hook_event_name": "SessionStart"}}),
        "not json at all {{{",
        json.dumps({"t": 0.5, "event": {"hook_event_name": "UnknownThing"}}),
        json.dumps({"no_t_field": True}),
        json.dumps({"t": 1.0, "event": {"hook_event_name": "Stop"}}),
    ]
    events_path.write_text("\n".join(lines) + "\n")
    # must not raise
    sonifier.run_render(str(events_path), str(out_path), seed=8)
    assert out_path.exists()
    assert out_path.stat().st_size > 44  # more than just a WAV header


def test_render_block_never_raises_on_broken_state():
    state = make_state(seed=9)
    state.activity = float("nan")
    block = sonifier.render_block(state, BLOCK)
    assert block.shape == (BLOCK, 2)
    # render_block must guarantee finite (silence-on-error) output
    assert np.all(np.isfinite(block))


# --------------------------------------------------------------------------
# classify() unit tests
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name,tool_input,expected", [
    ("Read", {"file_path": "/a.py"}, sonifier.CLASS_READ),
    ("Glob", {"pattern": "*.py"}, sonifier.CLASS_READ),
    ("Grep", {"pattern": "foo"}, sonifier.CLASS_READ),
    ("WebFetch", {"url": "http://x"}, sonifier.CLASS_READ),
    ("WebSearch", {"query": "x"}, sonifier.CLASS_READ),
    ("Write", {"file_path": "/a.py"}, sonifier.CLASS_WRITE),
    ("Edit", {"file_path": "/a.py"}, sonifier.CLASS_WRITE),
    ("NotebookEdit", {"file_path": "/a.ipynb"}, sonifier.CLASS_WRITE),
    ("Task", {}, None),
    ("Agent", {}, None),
])
def test_classify_known_tools(tool_name, tool_input, expected):
    assert sonifier.classify(tool_name, tool_input) == expected


@pytest.mark.parametrize("cmd,expected", [
    ("ls -la", sonifier.CLASS_READ),
    ("cat file.txt", sonifier.CLASS_READ),
    ("grep foo bar.txt", sonifier.CLASS_READ),
    ("rg foo", sonifier.CLASS_READ),
    ("find . -name x", sonifier.CLASS_READ),
    ("head -n 5 file", sonifier.CLASS_READ),
    ("tail -f log", sonifier.CLASS_READ),
    ("jq '.foo' file.json", sonifier.CLASS_READ),
    ("git status", sonifier.CLASS_READ),
    ("git log --oneline", sonifier.CLASS_READ),
    ("git diff HEAD", sonifier.CLASS_READ),
    ("git commit -m 'x'", sonifier.CLASS_EXEC),
    ("git push origin main", sonifier.CLASS_EXEC),
    ("rm -rf /tmp/x", sonifier.CLASS_EXEC),
    ("npm install", sonifier.CLASS_EXEC),
    ("python3 script.py", sonifier.CLASS_EXEC),
    ("/usr/bin/cat file", sonifier.CLASS_READ),
])
def test_classify_bash_commands(cmd, expected):
    assert sonifier.classify("Bash", {"command": cmd}) == expected


def test_classify_bash_no_command():
    assert sonifier.classify("Bash", {}) == sonifier.CLASS_EXEC
    assert sonifier.classify("Bash", None) == sonifier.CLASS_EXEC


def test_classify_unknown_tool_name_defaults_exec():
    assert sonifier.classify("SomeRandomFutureTool", {}) == sonifier.CLASS_EXEC


def test_classify_none_tool_name():
    assert sonifier.classify(None, {}) is None
    assert sonifier.classify("", {}) is None


# --------------------------------------------------------------------------
# tool timbre mapping via events
# --------------------------------------------------------------------------

def test_pretooluse_bumps_activity_and_sets_class():
    state = make_state(seed=10, chimes=False)
    assert state.activity == 0.0
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {}})
    assert state.activity == pytest.approx(0.35, abs=1e-9)
    assert state.current_class == sonifier.CLASS_WRITE
    assert state.pending_immediate_click is True


def test_posttoolusefailure_no_bump_but_chime():
    state = make_state(seed=11)
    state.handle_event({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"})
    assert state.activity == 0.0
    assert len(state.active_chimes) == 1


def test_stop_winds_down_activity_and_plays_done_chime():
    state = make_state(seed=12)
    state.activity = 1.0
    state.handle_event({"hook_event_name": "Stop"})
    assert state.activity == pytest.approx(0.3, abs=1e-9)
    assert len(state.active_chimes) == 1


def test_subagent_refcount_tracks_start_stop():
    state = make_state(seed=13)
    assert state.subagent_refcount == 0
    state.handle_event({"hook_event_name": "SubagentStart"})
    assert state.subagent_refcount == 1
    state.handle_event({"hook_event_name": "SubagentStart"})
    assert state.subagent_refcount == 2
    state.handle_event({"hook_event_name": "SubagentStop"})
    assert state.subagent_refcount == 1
    state.handle_event({"hook_event_name": "SubagentStop"})
    state.handle_event({"hook_event_name": "SubagentStop"})  # extra stop must not go negative
    assert state.subagent_refcount == 0


# --------------------------------------------------------------------------
# regression tests for defects found in verification
# --------------------------------------------------------------------------

def test_no_stray_click_before_any_event():
    """Regression: the click scheduler used to be armed at construction and
    fired once at ~t=0.05s even with activity==0, putting a lone audible
    click into what is contractually the silent idle head."""
    state = make_state(seed=3, chimes=False, drone=False)
    audio = render_n_blocks(state, int(math.ceil(3.0 * SR / BLOCK)))
    assert np.max(np.abs(audio)) == 0.0, "idle head before any event must be digital silence"


def test_no_stray_click_after_activity_reaches_zero():
    """Regression: once activity snapped to 0 the already-armed Poisson
    countdown still fired one last click into the idle tail."""
    events = [
        (0.0, {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}),
        (0.5, {"hook_event_name": "SessionEnd"}),
    ]
    audio, state = render_events_offline(events, duration_s=12.0, seed=4,
                                          chimes=False, drone=False)
    assert state.activity == 0.0
    tail = audio[int(3.0 * SR):]
    assert np.max(np.abs(tail)) == 0.0, "tail after SessionEnd must be digital silence"


def test_click_train_resumes_after_idle_silence():
    """The zero-rate path must park the scheduler, not disable it forever."""
    state = make_state(seed=5, chimes=False, drone=False)
    render_n_blocks(state, int(math.ceil(2.0 * SR / BLOCK)))  # silent idle
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {}})
    audio = render_n_blocks(state, int(math.ceil(2.0 * SR / BLOCK)))
    assert count_onsets(audio) > 0, "clicks must resume once activity returns"


def test_active_chimes_bounded_under_event_flood():
    """Regression: unbounded active_chimes -- 5000 queued chimes was ~1GB of
    buffers and blew the audio callback's deadline."""
    state = make_state(seed=6)
    for _ in range(5000):
        state.handle_event({"hook_event_name": "Notification"})
    assert len(state.active_chimes) <= sonifier.MAX_ACTIVE_CHIMES
    block = sonifier.render_block(state, BLOCK)
    assert np.all(np.isfinite(block))


def test_session_end_releases_drone():
    """Regression: drone_x survived SessionEnd, so the daemon kept droning
    for the whole idle-exit window after the session was over."""
    events = [
        (0.0, {"hook_event_name": "ContextPressure", "fill": 1.0}),
        (4.0, {"hook_event_name": "SessionEnd"}),
    ]
    audio, state = render_events_offline(events, duration_s=12.0, seed=7,
                                          drone=True, clicks=False, chimes=False)
    assert state.drone_x == 0.0
    assert np.max(np.abs(audio[int(2.0 * SR):int(4.0 * SR)])) > 1e-3, "drone should be up before SessionEnd"
    assert np.max(np.abs(audio[int(10.0 * SR):])) < 1e-3, "drone must fade out after SessionEnd"


def test_session_start_clears_stale_drone():
    state = make_state(seed=8)
    state.handle_event({"hook_event_name": "ContextPressure", "fill": 0.9})
    assert state.drone_x == pytest.approx(0.9)
    state.handle_event({"hook_event_name": "SessionStart"})
    assert state.drone_x == 0.0


def test_nonfinite_activity_does_not_wedge_click_scheduler():
    """NaN activity used to make every Poisson comparison False forever."""
    state = make_state(seed=9, chimes=False, drone=False)
    state.activity = float("nan")
    sonifier.render_block(state, BLOCK)
    assert math.isfinite(state.activity)
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                        "tool_input": {"command": "make"}})
    audio = render_n_blocks(state, int(math.ceil(2.0 * SR / BLOCK)))
    assert count_onsets(audio) > 0


def test_http_rejects_oversized_content_length_without_blocking():
    """Regression: a declared-but-unsent 1GiB Content-Length pinned a server
    thread indefinitely and would buffer the whole body."""
    import socket as _socket
    import threading as _threading
    import time as _time

    state = make_state(seed=10)
    httpd = sonifier._make_http_server(state, 0)  # ephemeral port
    port = httpd.server_address[1]
    _threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        s = _socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /event HTTP/1.1\r\nHost: x\r\n"
                  b"Content-Length: 1073741824\r\n\r\n")
        s.settimeout(5)
        t0 = _time.time()
        resp = s.recv(64)
        elapsed = _time.time() - t0
        s.close()
        assert b"413" in resp, resp
        assert elapsed < 2.0, f"handler took {elapsed}s to refuse"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_event_and_health_roundtrip():
    import threading as _threading
    import urllib.request as _req

    state = make_state(seed=11, chimes=False)
    httpd = sonifier._make_http_server(state, 0)
    port = httpd.server_address[1]
    _threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        body = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {}}).encode()
        r = _req.Request(f"http://127.0.0.1:{port}/event", data=body, method="POST")
        assert _req.urlopen(r, timeout=5).status == 200
        health = json.loads(_req.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).read())
        assert health["ok"] is True
        assert health["activity"] > 0.0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_udp_ingress_survives_malformed_datagrams():
    import socket as _socket
    import threading as _threading
    import time as _time

    state = make_state(seed=12)
    stop = _threading.Event()
    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    t = _threading.Thread(target=sonifier._udp_recv_loop, args=(state, port, stop), daemon=True)
    t.start()

    def _wait_until(cond, deadline_s=2.0, interval_s=0.01):
        end = _time.monotonic() + deadline_s
        while _time.monotonic() < end:
            if cond():
                return True
            _time.sleep(interval_s)
        return cond()

    u = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        for junk in (b"", b"{", b"not json", b"\xff\xfe\x00", b"[]", b"null",
                     b'{"hook_event_name":123}', b"A" * 8000):
            u.sendto(junk, ("127.0.0.1", port))
        valid = json.dumps({"hook_event_name": "PostToolUse"}).encode()
        # Resend the valid marker packet while polling: the recv thread's
        # bind() races this send, so a single fire-and-forget send could land
        # before the socket is up. Cheap and avoids a fixed startup sleep.
        def _registered():
            if state.activity <= 0.0:
                u.sendto(valid, ("127.0.0.1", port))
            return state.activity > 0.0
        assert _wait_until(_registered), "valid event after junk must still register"
        assert t.is_alive(), "UDP loop must survive malformed input"
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_env_bool_flag_accepts_off_and_no():
    for val, expected in [("0", False), ("false", False), ("off", False), ("no", False),
                          ("OFF", False), ("1", True), ("true", True), ("yes", True)]:
        os.environ["SONIFIER_TEST_FLAG"] = val
        try:
            assert sonifier._env_bool_flag("SONIFIER_TEST_FLAG", True) is expected, val
        finally:
            del os.environ["SONIFIER_TEST_FLAG"]


def test_render_is_deterministic_for_a_fixed_seed(tmp_path):
    events_path = tmp_path / "e.jsonl"
    events_path.write_text("\n".join(
        json.dumps({"t": t, "event": ev}) for t, ev in [
            (0.0, {"hook_event_name": "SessionStart"}),
            (0.5, {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}}),
            (1.0, {"hook_event_name": "PostToolUseFailure", "tool_name": "Read"}),
            (2.0, {"hook_event_name": "Stop"}),
        ]) + "\n")
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    sonifier.run_render(str(events_path), str(a), seed=7)
    sonifier.run_render(str(events_path), str(b), seed=7)
    assert a.read_bytes() == b.read_bytes()


def test_render_missing_file_exits_cleanly(tmp_path):
    with pytest.raises(SystemExit) as ei:
        sonifier.run_render(str(tmp_path / "nope.jsonl"), str(tmp_path / "o.wav"))
    assert ei.value.code == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
