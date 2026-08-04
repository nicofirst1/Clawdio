#!/usr/bin/env python3
"""simulate_session.py — replay a realistic Claude Code session's worth of
hook events at the claude-geiger sonifier daemon, or dump them as an
offline render script.

Two modes:

1. Live fire (default): sends each event as a UDP datagram (optionally HTTP
   POST /event with --http) to 127.0.0.1:<port>, real-time paced according
   to each event's scheduled offset and --speed. Good for demoing/testing
   the running daemon (`python3 src/sonifier.py`, run from the repo root)
   without needing a real Claude Code session.

2. --emit-jsonl FILE: instead of firing events live, writes the same event
   sequence as a render-script (one JSON object per line: {"t": <sec>,
   "event": {<hook JSON>}}), consumable by:
       python3 src/sonifier.py --render FILE out.wav

Stdlib only (uses urllib for --http, socket for UDP, json, argparse, time).

Sequence (see build_timeline()): SessionStart -> UserPromptSubmit -> a burst
of Read/Grep pre/post pairs -> Write/Edit activity -> a Bash test run that
fails (PostToolUseFailure) -> a retry that succeeds -> SubagentStart with
parallel tool events + SubagentStop -> PreCompact -> a ContextPressure ramp
0.3 -> 0.9 -> Stop. Total span is controlled by TOTAL_SPAN_S (~60s by
default; demos/demo-session.jsonl uses a longer, denser variant, see
build_demo_timeline()).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request
import urllib.error

DEFAULT_PORT = 9753
DEFAULT_HOST = "127.0.0.1"
SESSION_ID = "sim-session-0001"

# --------------------------------------------------------------------------
# Timeline construction
# --------------------------------------------------------------------------


def _ev(hook_event_name, **fields):
    d = {"hook_event_name": hook_event_name, "session_id": SESSION_ID}
    d.update(fields)
    return d


def _tool_pair(t0, tool_name, tool_input, dt=0.35, fail=False):
    """Return [(t, event), ...] for a PreToolUse/PostToolUse(Failure) pair."""
    out = [
        (t0, _ev("PreToolUse", tool_name=tool_name, tool_input=tool_input)),
    ]
    if fail:
        out.append(
            (
                t0 + dt,
                _ev(
                    "PostToolUseFailure",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    error="non-zero exit status",
                ),
            )
        )
    else:
        out.append(
            (t0 + dt, _ev("PostToolUse", tool_name=tool_name, tool_input=tool_input))
        )
    return out


def build_timeline():
    """~60s standard session used for --speed live-fire demos."""
    events = []

    events.append((0.0, _ev("SessionStart")))
    events.append((0.5, _ev("UserPromptSubmit", prompt="fix the failing auth tests")))

    # Read/Grep burst: gentle, dense read-phase (t=2..14)
    t = 2.0
    reads = [
        ("Grep", {"pattern": "def test_auth", "path": "tests/"}),
        ("Read", {"file_path": "tests/test_auth.py"}),
        ("Read", {"file_path": "src/auth.py"}),
        ("Glob", {"pattern": "src/**/*.py"}),
        ("Read", {"file_path": "src/session.py"}),
        ("Grep", {"pattern": "class AuthError", "path": "src/"}),
    ]
    for name, inp in reads:
        events.extend(_tool_pair(t, name, inp, dt=0.4))
        t += 2.0

    # Write/Edit activity: hot mixed phase (t=~16..26)
    t = 16.0
    edits = [
        ("Edit", {"file_path": "src/auth.py", "old_string": "def login", "new_string": "def login"}),
        ("Edit", {"file_path": "src/session.py", "old_string": "timeout=30", "new_string": "timeout=60"}),
        ("Write", {"file_path": "tests/test_auth.py", "content": "# updated tests"}),
    ]
    for name, inp in edits:
        events.extend(_tool_pair(t, name, inp, dt=0.3))
        t += 1.6

    # Bash test run that FAILS
    t = 26.0
    events.extend(
        _tool_pair(t, "Bash", {"command": "pytest tests/test_auth.py -q"}, dt=1.2, fail=True)
    )

    # A quick look at the failure, then a fix edit
    t = 29.0
    events.extend(_tool_pair(t, "Read", {"file_path": "tests/test_auth.py"}, dt=0.4))
    t = 31.0
    events.extend(
        _tool_pair(t, "Edit", {"file_path": "src/auth.py", "old_string": "return None", "new_string": "raise AuthError"}, dt=0.35)
    )

    # Retry: Bash test run that SUCCEEDS
    t = 33.5
    events.extend(
        _tool_pair(t, "Bash", {"command": "pytest tests/test_auth.py -q"}, dt=1.4, fail=False)
    )

    # SubagentStart + parallel tool events + SubagentStop (t=~37..47)
    t = 37.0
    events.append((t, _ev("SubagentStart", agent_type="Explore", agent_id="sub-1")))
    t += 0.3
    parallel = [
        ("Grep", {"pattern": "TODO", "path": "src/"}),
        ("Read", {"file_path": "src/config.py"}),
        ("Bash", {"command": "ruff check src/"}),
    ]
    for name, inp in parallel:
        events.extend(_tool_pair(t, name, inp, dt=0.5))
        t += 1.1
    events.append((t + 0.5, _ev("SubagentStop", agent_type="Explore", agent_id="sub-1")))

    # PreCompact
    t = 49.0
    events.append((t, _ev("PreCompact", trigger="auto")))

    # ContextPressure ramp 0.3 -> 0.9 (t=50..56)
    ramp_start, ramp_end = 50.0, 56.0
    steps = 7
    for i in range(steps):
        frac = i / (steps - 1)
        tt = ramp_start + frac * (ramp_end - ramp_start)
        fill = 0.3 + frac * (0.97 - 0.3)
        events.append((tt, _ev("ContextPressure", fill=round(fill, 3))))

    # Stop
    events.append((58.0, _ev("Stop")))
    events.append((58.2, _ev("SessionEnd", reason="completed")))

    events.sort(key=lambda x: x[0])
    return events


def build_demo_timeline():
    """~75s showcase script exercising every sound, for demo-session.jsonl.

    Phases: idle silence at start, gentle read phase, hot mixed phase, a
    failure, subagent phase, compaction + context-pressure ramp, done
    chime, trailing silence.
    """
    events = []

    events.append((0.0, _ev("SessionStart")))

    # Idle silence: nothing happens t=0..6 (daemon's activity decays to 0,
    # demonstrating pure silence/near-silence at rest).

    events.append((6.0, _ev("UserPromptSubmit", prompt="add rate limiting to the API and make sure tests pass")))

    # Gentle read phase: sparse Read/Grep pairs, t=7..22 (~2.5s apart)
    t = 7.0
    reads = [
        ("Grep", {"pattern": "class RateLimiter", "path": "src/"}),
        ("Read", {"file_path": "src/api.py"}),
        ("Glob", {"pattern": "src/**/*.py"}),
        ("Read", {"file_path": "src/middleware.py"}),
        ("Grep", {"pattern": "def handle_request", "path": "src/"}),
        ("Read", {"file_path": "docs/api.md"}),
    ]
    for name, inp in reads:
        events.extend(_tool_pair(t, name, inp, dt=0.4))
        t += 2.5

    # Hot mixed phase: dense Read/Grep/Write/Edit/Bash interleave, t=23..38
    t = 23.0
    hot = [
        ("Edit", {"file_path": "src/middleware.py", "old_string": "pass", "new_string": "self.limiter = RateLimiter()"}),
        ("Read", {"file_path": "src/config.py"}),
        ("Write", {"file_path": "src/rate_limiter.py", "content": "class RateLimiter: ..."}),
        ("Grep", {"pattern": "RateLimiter", "path": "tests/"}),
        ("Edit", {"file_path": "src/api.py", "old_string": "def handle", "new_string": "@rate_limited\ndef handle"}),
        ("Bash", {"command": "ruff check src/"}),
        ("Read", {"file_path": "src/rate_limiter.py"}),
        ("Edit", {"file_path": "src/rate_limiter.py", "old_string": "window=60", "new_string": "window=30"}),
    ]
    for name, inp in hot:
        events.extend(_tool_pair(t, name, inp, dt=0.3))
        t += 1.4

    # A failure: Bash test run fails, t=~39
    t = 39.5
    events.extend(
        _tool_pair(t, "Bash", {"command": "pytest tests/test_rate_limiter.py -q"}, dt=1.3, fail=True)
    )
    events.append((t + 1.6, _ev("Notification", message="Test run failed, review output")))

    # Fix + retry succeeds, t=~44..48
    t = 44.0
    events.extend(_tool_pair(t, "Read", {"file_path": "tests/test_rate_limiter.py"}, dt=0.4))
    t = 45.5
    events.extend(
        _tool_pair(t, "Edit", {"file_path": "src/rate_limiter.py", "old_string": "capacity=10", "new_string": "capacity=100"}, dt=0.35)
    )
    t = 47.5
    events.extend(
        _tool_pair(t, "Bash", {"command": "pytest tests/test_rate_limiter.py -q"}, dt=1.5, fail=False)
    )

    # Subagent phase: SubagentStart + parallel tools + SubagentStop, t=~50..60
    t = 50.5
    events.append((t, _ev("SubagentStart", agent_type="Explore", agent_id="sub-demo-1")))
    t += 0.3
    parallel = [
        ("Grep", {"pattern": "TODO", "path": "src/"}),
        ("Read", {"file_path": "src/db.py"}),
        ("Bash", {"command": "grep -r deprecated src/"}),
        ("Read", {"file_path": "src/utils.py"}),
    ]
    for name, inp in parallel:
        events.extend(_tool_pair(t, name, inp, dt=0.5))
        t += 1.0
    events.append((t + 0.4, _ev("SubagentStop", agent_type="Explore", agent_id="sub-demo-1")))
    t += 1.5

    # PermissionRequest, t=~62
    events.append((t, _ev("PermissionRequest", tool_name="Bash", tool_input={"command": "git commit -am 'add rate limiting'"})))
    t += 1.0

    # Compaction + context-pressure ramp, t=~63..70
    events.append((t, _ev("PreCompact", trigger="auto")))
    ramp_start, ramp_end = t + 0.5, t + 7.0
    steps = 8
    for i in range(steps):
        frac = i / (steps - 1)
        tt = ramp_start + frac * (ramp_end - ramp_start)
        fill = 0.25 + frac * (0.95 - 0.25)
        events.append((tt, _ev("ContextPressure", fill=round(fill, 3))))

    # Done chime, t=~71
    events.append((71.0, _ev("Stop")))
    events.append((71.2, _ev("SessionEnd", reason="completed")))

    # Trailing silence: no events t=71.2..75 (daemon decays to idle).

    events.sort(key=lambda x: x[0])
    return events


def build_v2_demo_timeline():
    """~177s storyboard for demo-session-v2.jsonl (BRIEF-v2.md section 8),
    exercising every AmbientTheme gesture:

    0-10s    session start + idle bloom (no tool events -> L3 self-plays)
    10-40s   gentle read/browse phase (sparse Read/Grep, thin drizzle)
    40-82s   active build (dense writes+execs -> rain thickens, notes bloom)
    ~85s     a failure (knock, bass shades to vi, room darkens)
    86-95s   diagnosis reads, then the retry Bash SUCCEEDS at ~95.5 -> light
             returns (the shading clears on a success of the tool that failed)
    105-140s subagent phase (2 stems fade in) + context pressure ramp
             0.3 -> 0.97 (sub-bass weather gathers, master LP closes)
    ~145s    PreCompact settle gesture + pressure release
    150-166s wind-down: permission request, one last commit, user thanks
    166.5s   Stop cadence, bed eases to idle
    174s     SessionEnd -> 4s fade; render tail ends at ~177s

    Pacing note (v2 verification): the read phase is deliberately sparser
    (4.0s spacing) and the build phase denser (2.0s) than the first cut, so
    the per-10s RMS arc is monotonic idle < read < build instead of flat.
    """
    events = []
    events.append((0.0, _ev("SessionStart")))

    events.append((10.0, _ev("UserPromptSubmit", prompt="add pagination to the search API")))

    # gentle read/browse phase, t=11..40 (4.0s apart -- deliberately airy)
    t = 11.0
    reads = [
        ("Grep", {"pattern": "def search", "path": "src/"}),
        ("Read", {"file_path": "src/search.py"}),
        ("Glob", {"pattern": "src/**/*.py"}),
        ("Read", {"file_path": "src/models.py"}),
        ("Grep", {"pattern": "class Paginator", "path": "src/"}),
        ("Read", {"file_path": "docs/api.md"}),
        ("WebSearch", {"query": "cursor pagination best practices"}),
        ("Read", {"file_path": "src/db.py"}),
    ]
    for name, inp in reads:
        events.extend(_tool_pair(t, name, inp, dt=0.4))
        t += 4.0

    # active build phase, t=43..82: dense write/edit/exec interleave
    t = 43.0
    hot = [
        ("Write", {"file_path": "src/pagination.py", "content": "class Paginator: ..."}),
        ("Read", {"file_path": "src/pagination.py"}),
        ("Edit", {"file_path": "src/search.py", "old_string": "def search(q)", "new_string": "def search(q, cursor=None)"}),
        ("Bash", {"command": "ruff check src/"}),
        ("Edit", {"file_path": "src/models.py", "old_string": "class Result", "new_string": "class Result(Paginated)"}),
        ("Write", {"file_path": "tests/test_pagination.py", "content": "# pagination tests"}),
        ("Grep", {"pattern": "Paginator", "path": "tests/"}),
        ("Edit", {"file_path": "src/pagination.py", "old_string": "page_size=20", "new_string": "page_size=50"}),
        ("Bash", {"command": "python3 -m pyflakes src/"}),
        ("Edit", {"file_path": "src/db.py", "old_string": "LIMIT 20", "new_string": "LIMIT ?"}),
        ("Write", {"file_path": "src/pagination.py", "content": "class Paginator: ...  # v2"}),
        ("Read", {"file_path": "src/db.py"}),
        ("Edit", {"file_path": "src/search.py", "old_string": "return rows", "new_string": "return page(rows)"}),
        ("Write", {"file_path": "docs/api.md", "content": "## Pagination"}),
        ("Bash", {"command": "ruff format src/"}),
        ("Edit", {"file_path": "tests/test_pagination.py", "old_string": "assert 1", "new_string": "assert page.next"}),
        ("Write", {"file_path": "src/db.py", "content": "# cursor helpers"}),
        ("Edit", {"file_path": "src/pagination.py", "old_string": "None", "new_string": "cursor"}),
        ("Read", {"file_path": "src/models.py"}),
        ("Edit", {"file_path": "src/models.py", "old_string": "id", "new_string": "id, cursor"}),
    ]
    for name, inp in hot:
        events.extend(_tool_pair(t, name, inp, dt=0.3))
        t += 2.0  # 20 events * 2.0 = 40s, lands at t=83

    # failure, ~t=85 (knock + bass shades to vi + master LP closes 300 Hz)
    t = 85.0
    events.extend(_tool_pair(t, "Bash", {"command": "pytest tests/test_pagination.py -q"}, dt=1.3, fail=True))
    events.append((t + 2.0, _ev("Notification", message="Test run failed, review output")))

    # diagnose, then the retry of the FAILED tool succeeds at ~95.5s: that is
    # what lifts the filter back up ("audible resolution").
    events.extend(_tool_pair(90.0, "Read", {"file_path": "tests/test_pagination.py"}, dt=0.4))
    events.extend(_tool_pair(92.0, "Edit", {"file_path": "src/pagination.py", "old_string": "cursor=0", "new_string": "cursor=None"}, dt=0.35))
    events.extend(_tool_pair(94.0, "Bash", {"command": "pytest tests/test_pagination.py -q"}, dt=1.5, fail=False))

    # subagent phase (2 subagents) + context-pressure ramp, t=105..140
    events.append((105.0, _ev("SubagentStart", agent_type="Explore", agent_id="sub-v2-1")))
    events.append((107.0, _ev("SubagentStart", agent_type="Review", agent_id="sub-v2-2")))
    t = 106.0
    parallel = [
        ("Grep", {"pattern": "TODO", "path": "src/"}),
        ("Read", {"file_path": "src/utils.py"}),
        ("Bash", {"command": "grep -r deprecated src/"}),
        ("Read", {"file_path": "src/config.py"}),
        ("Edit", {"file_path": "src/pagination.py", "old_string": "# TODO docstring", "new_string": '"""Cursor-based pagination."""'}),
        ("Read", {"file_path": "tests/test_pagination.py"}),
        ("Grep", {"pattern": "cursor", "path": "tests/"}),
        ("Edit", {"file_path": "src/utils.py", "old_string": "def enc", "new_string": "def encode_cursor"}),
        ("Read", {"file_path": "src/search.py"}),
        ("Bash", {"command": "pytest -q"}),
    ]
    for name, inp in parallel:
        events.extend(_tool_pair(t, name, inp, dt=0.5))
        t += 3.0  # ~30s of parallel activity, through the whole subagent phase

    ramp_start, ramp_end = 108.0, 137.0
    steps = 10
    for i in range(steps):
        frac = i / (steps - 1)
        tt = ramp_start + frac * (ramp_end - ramp_start)
        fill = 0.3 + frac * (0.97 - 0.3)
        events.append((tt, _ev("ContextPressure", fill=round(fill, 3))))

    events.append((136.0, _ev("SubagentStop", agent_type="Review", agent_id="sub-v2-2")))
    events.append((139.0, _ev("SubagentStop", agent_type="Explore", agent_id="sub-v2-1")))

    # PreCompact settle + pressure release, ~t=145
    events.append((145.0, _ev("PreCompact", trigger="auto")))
    release_start, release_end = 146.0, 150.0
    steps = 5
    for i in range(steps):
        frac = i / (steps - 1)
        tt = release_start + frac * (release_end - release_start)
        fill = 0.97 - frac * (0.97 - 0.15)
        events.append((tt, _ev("ContextPressure", fill=round(fill, 3))))

    # wind-down, t=150..166
    events.append((154.0, _ev("PermissionRequest", tool_name="Bash", tool_input={"command": "git commit -am 'add pagination'"})))
    events.extend(_tool_pair(158.0, "Bash", {"command": "git commit -am 'add pagination'"}, dt=0.6))
    events.append((163.0, _ev("UserPromptSubmit", prompt="looks good, thanks")))

    # Stop cadence at 166.5 with room to ring out and ease toward idle before
    # SessionEnd at 174 (the v1 cut fired Stop 0.3s before SessionEnd, so the
    # cadence was chopped off mid-phrase by the end fade).
    events.append((166.5, _ev("Stop")))
    events.append((174.0, _ev("SessionEnd", reason="completed")))

    # run_render appends its own +3s tail -> ~177s total, of which the last
    # ~1.5s is true digital silence (silence = off, and it means something).

    events.sort(key=lambda x: x[0])
    return events


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def send_udp(sock, host, port, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendto(data, (host, port))


def send_http(host, port, obj, timeout=2.0):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}/event",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        print(f"[simulate_session] HTTP send failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def run_live(events, host, port, speed, use_http, verbose):
    sock = None
    if not use_http:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    start_wall = time.monotonic()
    for t, ev in events:
        target_wall = start_wall + (t / speed)
        now = time.monotonic()
        delay = target_wall - now
        if delay > 0:
            time.sleep(delay)
        if verbose:
            print(f"t={t:6.2f}s  {ev.get('hook_event_name'):20s} "
                  f"{ev.get('tool_name', ''):10s}", file=sys.stderr)
        if use_http:
            send_http(host, port, ev)
        else:
            send_udp(sock, host, port, ev)

    if sock is not None:
        sock.close()
    print(f"[simulate_session] sent {len(events)} events "
          f"({'HTTP' if use_http else 'UDP'} -> {host}:{port})", file=sys.stderr)


def run_emit_jsonl(events, out_path):
    prev_t = None
    with open(out_path, "w") as f:
        for t, ev in events:
            if prev_t is not None and t < prev_t:
                raise AssertionError("timeline not monotonic")
            prev_t = t
            f.write(json.dumps({"t": round(t, 3), "event": ev}) + "\n")
    print(f"[simulate_session] wrote {len(events)} events to {out_path}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Fire (or emit as a render script) a simulated Claude Code session."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="daemon host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None,
                         help="daemon port (default: $SONIFIER_PORT or 9753)")
    parser.add_argument("--speed", type=float, default=1.0,
                         help="playback speed multiplier for live fire (e.g. 4.0 = 4x realtime; ignored with --emit-jsonl)")
    parser.add_argument("--http", action="store_true",
                         help="use HTTP POST /event instead of UDP for live fire")
    parser.add_argument("--emit-jsonl", metavar="FILE", default=None,
                         help="write the event sequence as a render-script (events.jsonl) instead of firing it live")
    parser.add_argument("--demo", action="store_true",
                         help="use the longer ~75s showcase timeline (build_demo_timeline) instead of the ~60s standard one")
    parser.add_argument("--v2-demo", action="store_true",
                         help="use the ~175s v2 (AmbientTheme) showcase timeline (build_v2_demo_timeline) exercising every gesture in BRIEF-v2.md section 8 -- overrides --demo")
    parser.add_argument("--verbose", action="store_true", help="print each event as it's sent (live mode only)")
    args = parser.parse_args(argv)

    port = args.port
    if port is None:
        import os
        port = int(os.environ.get("SONIFIER_PORT", DEFAULT_PORT))

    if args.v2_demo:
        events = build_v2_demo_timeline()
    elif args.demo:
        events = build_demo_timeline()
    else:
        events = build_timeline()

    if args.emit_jsonl:
        run_emit_jsonl(events, args.emit_jsonl)
        return 0

    if args.speed <= 0:
        print("--speed must be > 0", file=sys.stderr)
        return 2

    run_live(events, args.host, port, args.speed, args.http, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
