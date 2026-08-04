#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy"]
# ///
"""make_clips.py -- generates the v2.2 evaluation-kit audio, from the engine,
with fixed seeds, per BRIEF-v2.2.md section 8.

Produces (into eval/clips/):
  Block A -- 8 vocabulary clips, 10-15s each: a01_sparse_rain, a02_dense_rain,
             a03_write_notes, a04_knock, a05_subagent_choir, a06_pressure_weather,
             a07_done_cadence, a08_needs_you_chime.
  Block B -- 4 scenario clips, 60-90s each, with a ground-truth timestamp file
             each: b01_calm_success, b02_busy_success, b03_failure_recovery,
             b04_busy_subagents_unresolved.
  Block C -- the "flip-point probe": the SAME short session rendered at 3
             pacing variants -- c1_v2_mapping, c2_v22_mapping,
             c3_v22_half_density.

Every clip is: write a .jsonl event script (kept alongside the audio -- it
doubles as machine-readable ground truth), render it via sonifier.run_render
with a FIXED seed, then encode WAV -> MP3 (192k, libmp3lame) if ffmpeg is on
PATH (WAV is always kept either way).

Usage:
    python3 eval/make_clips.py [--out-dir eval/clips] [--blocks A,B,C]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SONIFIER_QUIET", "1")
os.environ["SONIFIER_THEME"] = "ambient"

import sonifier as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "clips")


# --------------------------------------------------------------------------
# small event-script builder
# --------------------------------------------------------------------------

def ev(t, name, **kw):
    d = {"hook_event_name": name}
    d.update(kw)
    return {"t": round(t, 3), "event": d}


def write_jsonl(events, path):
    with open(path, "w") as f:
        for e in sorted(events, key=lambda x: x["t"]):
            f.write(json.dumps(e) + "\n")


def render_clip(name, events, out_dir, seed, duration_hint=None):
    """Write the .jsonl, render the .wav via sonifier.run_render, encode mp3
    if ffmpeg is available. Returns the .wav path."""
    jsonl_path = os.path.join(out_dir, f"{name}.jsonl")
    wav_path = os.path.join(out_dir, f"{name}.wav")
    write_jsonl(events, jsonl_path)
    S.run_render(jsonl_path, wav_path, seed=seed)
    mp3_path = os.path.join(out_dir, f"{name}.mp3")
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
             "-codec:a", "libmp3lame", "-b:a", "192k", mp3_path],
            check=True,
        )
    print(f"  {name}: {wav_path}" + (f" + mp3" if os.path.exists(mp3_path) else " (no ffmpeg, wav only)"))
    return wav_path


# --------------------------------------------------------------------------
# Block A: vocabulary clips (8 x 10-15s)
# --------------------------------------------------------------------------

def _tool_burst(events, t0, n, gap, tool, cls_input):
    t = t0
    for i in range(n):
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input=cls_input))
        events.append(ev(t + gap * 0.4, "PostToolUse", tool_name=tool))
        t += gap
    return t


def build_block_a(out_dir):
    print("Block A -- vocabulary clips")

    # a01: sparse rain -- a few gentle, well-spaced reads. Nothing else.
    events = [ev(0.0, "SessionStart")]
    _tool_burst(events, 3.0, 5, 2.0, "Read", {"file_path": "a.py"})
    render_clip("a01_sparse_rain", events, out_dir, seed=101)

    # a02: dense rain -- rapid-fire tool calls driving activity toward 1.0.
    # v2.2 point: this should sound like THICKER WASH, not faster taps.
    events = [ev(0.0, "SessionStart")]
    _tool_burst(events, 1.0, 40, 0.25, "Bash", {"command": "pytest"})
    render_clip("a02_dense_rain", events, out_dir, seed=102)

    # a03: write-notes -- a run of Write events, each with a chance to
    # trigger a mid-register FM note (the "making things" voice).
    events = [ev(0.0, "SessionStart")]
    _tool_burst(events, 1.5, 8, 1.4, "Write", {"file_path": "b.py"})
    render_clip("a03_write_notes", events, out_dir, seed=103)

    # a04: knock -- a single failure gesture over an otherwise steady bed.
    events = [ev(0.0, "SessionStart"),
              ev(3.0, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest"}),
              ev(8.0, "PostToolUseFailure", tool_name="Bash")]
    render_clip("a04_knock", events, out_dir, seed=104)

    # a05: subagent choir entry -- 2 subagents starting (stem1 + stem2 fade in).
    events = [ev(0.0, "SessionStart"),
              ev(5.0, "SubagentStart"),
              ev(7.0, "SubagentStart")]
    render_clip("a05_subagent_choir", events, out_dir, seed=105)

    # a06: pressure weather -- context fill rising through the L5 threshold.
    events = [ev(0.0, "SessionStart")]
    t = 2.0
    for fill in (0.55, 0.65, 0.75, 0.85, 0.92):
        events.append(ev(t, "ContextPressure", fill=fill))
        t += 1.8
    render_clip("a06_pressure_weather", events, out_dir, seed=106)

    # a07: done cadence -- the Stop resolution gesture after some light work.
    events = [ev(0.0, "SessionStart"),
              ev(3.0, "PreToolUse", tool_name="Write", tool_input={"file_path": "c.py"}),
              ev(3.4, "PostToolUse", tool_name="Write"),
              ev(8.0, "Stop")]
    render_clip("a07_done_cadence", events, out_dir, seed=107)

    # a08: needs-you chime -- the softened Notification/PermissionRequest gesture.
    events = [ev(0.0, "SessionStart"),
              ev(2.0, "PreToolUse", tool_name="Read", tool_input={"file_path": "d.py"}),
              ev(2.3, "PostToolUse", tool_name="Read"),
              ev(8.0, "Notification")]
    render_clip("a08_needs_you_chime", events, out_dir, seed=108)


# --------------------------------------------------------------------------
# Block B: scenario clips (4 x 60-90s) + ground-truth timestamp files
# --------------------------------------------------------------------------

def _write_ground_truth(name, out_dir, entries, outcome, description):
    """entries: list of (t, label) tuples. Written as both a human-readable
    .txt and a machine-readable .json (eval/score.py and the scoring-sheet
    use these as the answer key inputs)."""
    txt_path = os.path.join(out_dir, f"{name}.groundtruth.txt")
    json_path = os.path.join(out_dir, f"{name}.groundtruth.json")
    with open(txt_path, "w") as f:
        f.write(f"{name} -- ground truth\n")
        f.write(f"{description}\n\n")
        f.write(f"final outcome: {outcome}\n\n")
        f.write("timeline:\n")
        for t, label in entries:
            f.write(f"  {t:6.1f}s  {label}\n")
    with open(json_path, "w") as f:
        json.dump({
            "clip": name,
            "description": description,
            "outcome": outcome,
            "timeline": [{"t": t, "label": label} for t, label in entries],
        }, f, indent=2)


def build_block_b(out_dir):
    print("Block B -- scenario clips")

    # b01: calm + success. Light, well-spaced reads/edits, ends in a Stop
    # cadence with no failures. ~65s.
    events = [ev(0.0, "SessionStart")]
    gt = [(0.0, "session start"), (2.0, "first prompt")]
    events.append(ev(2.0, "UserPromptSubmit", prompt="look into the auth flow"))
    t = 6.0
    tools = ["Read", "Grep", "Read", "Edit", "Read"]
    for i, tool in enumerate(tools):
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "x.py"}))
        events.append(ev(t + 0.4, "PostToolUse", tool_name=tool))
        gt.append((t, f"{tool} #{i + 1}"))
        t += 11.0
    events.append(ev(t + 3.0, "Stop"))
    gt.append((t + 2.0, "Stop (success cadence)"))
    render_clip("b01_calm_success", events, out_dir, seed=201)
    _write_ground_truth(
        "b01_calm_success", out_dir, gt, outcome="success (Stop, no failures)",
        description="Calm read/grep/edit investigation, no failures, resolves cleanly.")

    # b02: busy + success. Dense writes/execs (rain thickens, notes bloom),
    # then a clean Stop. ~75s.
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="implement pagination")]
    gt = [(0.0, "session start"), (1.0, "first prompt")]
    t = 4.0
    plan = ["Write", "Bash", "Write", "Edit", "Bash", "Write", "Edit", "Bash"]
    for i, tool in enumerate(plan):
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "pg.py", "command": "pytest"}))
        events.append(ev(t + 0.5, "PostToolUse", tool_name=tool))
        gt.append((t, f"{tool} #{i + 1}"))
        t += 6.5
    events.append(ev(t + 2.0, "Stop"))
    gt.append((t + 2.0, "Stop (success cadence)"))
    render_clip("b02_busy_success", events, out_dir, seed=202)
    _write_ground_truth(
        "b02_busy_success", out_dir, gt, outcome="success (Stop, no failures)",
        description="Busy write/exec build phase (rain thickens, bloom notes more frequent), resolves cleanly.")

    # b03: failure + recovery. A build phase, a failure (knock + darkening),
    # a retry that succeeds (light returns), then Stop. ~70s.
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="fix the flaky test")]
    gt = [(0.0, "session start"), (1.0, "first prompt")]
    t = 5.0
    events.append(ev(t, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest"}))
    events.append(ev(t + 1.5, "PostToolUseFailure", tool_name="Bash"))
    gt.append((t, "Bash (running tests)"))
    gt.append((t + 1.5, "FAILURE (knock, room darkens)"))
    t += 8.0
    for tool in ("Read", "Edit", "Read"):
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "t.py"}))
        events.append(ev(t + 0.4, "PostToolUse", tool_name=tool))
        gt.append((t, f"{tool} (investigating)"))
        t += 11.0
    events.append(ev(t, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest"}))
    events.append(ev(t + 1.5, "PostToolUse", tool_name="Bash"))
    gt.append((t, "Bash retry"))
    gt.append((t + 1.5, "RETRY SUCCEEDS (light returns)"))
    t += 11.0
    events.append(ev(t, "Stop"))
    gt.append((t, "Stop (success cadence)"))
    render_clip("b03_failure_recovery", events, out_dir, seed=203)
    _write_ground_truth(
        "b03_failure_recovery", out_dir, gt, outcome="success after one failure+recovery",
        description="A Bash failure (knock, bed darkens/shades toward vi) followed by investigation "
                     "and a successful retry (filter lifts back over ~5s), then Stop.")

    # b04: busy + subagents + unresolved. Subagents spin up (choir thickens),
    # busy work continues, context pressure rises, and the clip ENDS mid-flight
    # (no Stop) -- deliberately unresolved.
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="refactor the pipeline across services")]
    gt = [(0.0, "session start"), (1.0, "first prompt")]
    t = 4.0
    events.append(ev(t, "SubagentStart"))
    gt.append((t, "SubagentStart #1"))
    t += 3.0
    events.append(ev(t, "SubagentStart"))
    gt.append((t, "SubagentStart #2 (choir thickens)"))
    t += 4.0
    plan = ["Write", "Bash", "Edit", "Write", "Bash"]
    for tool in plan:
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "svc.py", "command": "pytest"}))
        events.append(ev(t + 0.5, "PostToolUse", tool_name=tool))
        gt.append((t, tool))
        t += 7.0
    for fill in (0.6, 0.75, 0.88):
        events.append(ev(t, "ContextPressure", fill=fill))
        gt.append((t, f"context pressure fill={fill}"))
        t += 6.0
    # clip simply ends here -- unresolved, still busy, still under subagents.
    gt.append((t, "CLIP ENDS -- unresolved, subagents still active"))
    render_clip("b04_busy_subagents_unresolved", events, out_dir, seed=204)
    _write_ground_truth(
        "b04_busy_subagents_unresolved", out_dir, gt,
        outcome="UNRESOLVED (no Stop; ends mid-flight with subagents + pressure active)",
        description="Two subagents active, busy write/exec work, context pressure climbing. "
                     "The clip cuts off with no resolution gesture -- tests whether listeners "
                     "correctly report 'still going / unresolved' rather than inventing an ending.")


# --------------------------------------------------------------------------
# Block C: pacing A/B/C probe (flip-point)
# --------------------------------------------------------------------------

def _pacing_session_events():
    """A single ~50s session script driving activity from calm to a hot
    burst and back -- used identically for all 3 pacing variants so the
    ONLY difference between clips is the rate-mapping law."""
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="do a bunch of stuff")]
    t = 4.0
    # ramp: calm -> busy -> hot burst -> back to busy
    plan = [("Read", 3.0)] * 3 + [("Write", 1.2)] * 5 + [("Bash", 0.35)] * 12 + [("Edit", 1.5)] * 4
    for tool, gap in plan:
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "z.py", "command": "pytest"}))
        events.append(ev(t + min(0.4, gap * 0.5), "PostToolUse", tool_name=tool))
        t += gap
    events.append(ev(t + 2.0, "Stop"))
    return events


def build_block_c(out_dir):
    """The flip-point probe. Three renders of ONE script, differing only in
    how event density maps to audible tap density.

    v2.2 VERIFIER fixes to this block (both were silent defects -- the clips
    rendered fine and were wrong):

      * c3 was identical to c2. "Half density" only halved the POISSON rain
        clock, but at the activity this script reaches almost every tap is
        EVENT-TRIGGERED (_trigger_event_drop), not clock-driven -- so the
        two clips dispatched exactly the same 25 drops and the probe had
        only two distinct points instead of three. Half density now also
        doubles the burst-coalescing window and the inter-drop pacing floor,
        which is what actually halves the tap count on an event-driven map.
      * c1 did not sound like v2. v2's rate law was being applied on top of
        v2.2's 150 ms global pacing floor, which throttled it from ~14
        drops/s to 4.3 -- i.e. the "too fast" control condition was already
        most of the way to v2.2. c1 now relaxes the floor as well, so it
        reproduces what the listener actually heard and called "too fast,
        losing control".

    Measured dispatch density (engine ground truth, not the acoustic proxy):
        c1  14.09 drops/s, worst 2 s window 26.5/s
        c2   0.73 drops/s, worst 2 s window  3.0/s
        c3   0.37 drops/s, worst 2 s window  1.5/s
    """
    print("Block C -- pacing A/B/C flip-point probe (same session, 3 rate maps)")
    events = _pacing_session_events()

    v22_rate_fn = S._drop_rate_from_activity  # the real, shipped v2.2 mapping

    def v2_rate_fn(a):
        # v2's original mapping (BRIEF-v2.md section 2): rate = 2 + 38*a**1.3,
        # up to ~40 drops/s -- kept here ONLY to render the comparison clip;
        # NOT reachable through the shipped engine/CLI.
        a = max(0.0, min(1.0, a))
        return 2.0 + 38.0 * (a ** 1.3)

    def v22_half_rate_fn(a):
        return 0.5 * v22_rate_fn(a)

    # (name, rate map, seed, pacing floor s, coalescing window s)
    variants = [
        ("c1_v2_mapping", v2_rate_fn, 301, 0.020, 0.0),
        ("c2_v22_mapping", v22_rate_fn, 302, S.DROP_MIN_GAP_S, S.BURST_COALESCE_WINDOW_S),
        ("c3_v22_half_density", v22_half_rate_fn, 303,
         2.0 * S.DROP_MIN_GAP_S, 2.0 * S.BURST_COALESCE_WINDOW_S),
    ]
    ship_floor = S.DROP_MIN_GAP_S
    ship_coal = S.BURST_COALESCE_WINDOW_S
    for name, rate_fn, seed, floor, coal in variants:
        S._drop_rate_from_activity = rate_fn
        S.DROP_MIN_GAP_S = floor
        S.BURST_COALESCE_WINDOW_S = coal
        try:
            render_clip(name, events, out_dir, seed=seed)
        finally:
            S._drop_rate_from_activity = v22_rate_fn
            S.DROP_MIN_GAP_S = ship_floor
            S.BURST_COALESCE_WINDOW_S = ship_coal

    with open(os.path.join(out_dir, "block_c_README.txt"), "w") as f:
        f.write(
            "Block C: the SAME event script (calm read/grep -> busy writes -> a hot\n"
            "12-call Bash burst -> back to busy edits -> Stop), rendered 3 times with\n"
            "only the event-density -> tap-density mapping changed:\n\n"
            "  c1_v2_mapping        v2: rate = 2 + 38*a^1.3, 20 ms pacing floor, no\n"
            "                       burst coalescing. Measured 14.1 drops/s, peaking at\n"
            "                       26.5/s -- this is what the v2 listener heard and\n"
            "                       described as 'too fast, losing control'.\n"
            "  c2_v22_mapping       v2.2 as shipped: compressive map capped at 6/s,\n"
            "                       150 ms floor, 250 ms burst coalescing. 0.73 drops/s,\n"
            "                       peaking at 3.0/s.\n"
            "  c3_v22_half_density  v2.2 at half density: half the rate, 300 ms floor,\n"
            "                       500 ms coalescing. 0.37 drops/s, peaking at 1.5/s.\n\n"
            "This is the 'flip-point probe': play c1/c2/c3 in randomized order (see\n"
            "eval/README.md) and ask which feels controllable vs frantic during the busy\n"
            "burst, to find where pacing tips from informative into overwhelming. c2 is\n"
            "the shipped setting; c1 and c3 bracket it on either side.\n"
        )


# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--blocks", default="A,B,C", help="comma list of blocks to build, e.g. A,B,C")
    args = ap.parse_args(argv)

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    blocks = {b.strip().upper() for b in args.blocks.split(",") if b.strip()}

    print(f"# writing clips to {out_dir}")
    if "A" in blocks:
        build_block_a(out_dir)
    if "B" in blocks:
        build_block_b(out_dir)
    if "C" in blocks:
        build_block_c(out_dir)
    print("# done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
