#!/usr/bin/env python3
"""Round 6 blind kit: per-session voice-slot LATERALIZATION test (not
preference), per docs/research/BRIEF-v2.5.md section 6.

BRIEF-v2.5 gives each live session a stable voice slot (pan + pitch offset)
for its discrete gestures only (failure knock, Stop cadence, needs-you
chime, ack note). Slot 0 (center, first session seen) reproduces the old
single-session behavior exactly; slot 1 (2nd session seen) is hard left,
pan=-0.40, pitch -2 semitones (src/classify.py SLOT_PALETTE). This round
asks listeners the two acceptance-criterion-2 comprehension questions
directly on two-session renders:

  "Which side finished?"    -- Stop cadence on exactly one session.
  "Which side needs you?"   -- needs-you chime on exactly one session.

Each has a "full" arm (pan + pitch, code as committed) and a "pan-only"
control arm (every SLOT_PALETTE pitch offset zeroed in-process, per BRIEF
section 3's fallback plan -- src/ is NOT modified, classify.SLOT_PALETTE is
monkeypatched before the render and restored after). Two more clips are
v2.4-style controls where voicing gives no left/right cue at all (single
session, or both sessions finishing together) -- the correct answer there
is "can't tell", mirroring round5's hidden-control pattern.

session_id ordering controls slot assignment: SessionTracker hands out
slots first-free, keyed to first-seen order (src/classify.py). Session A's
SessionStart is always the first event in the file, so A always claims slot
0 (center) and B claims slot 1 (left) the moment its own first event is
noted -- see BRIEF-v2.5 section 2.1's palette table.

Renders each scenario+arm to wav, then mp3 if ffmpeg is available (same
fallback round5 uses), shuffles to neutral single-letter labels, and writes
answer-key-round6.txt.

Usage: python3 eval/blind/round6/make_round6_clips.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "src"))
os.environ.setdefault("SONIFIER_QUIET", "1")
os.environ["SONIFIER_THEME"] = "ambient"

import numpy as np  # noqa: E402
from io_modes import run_render  # noqa: E402
import classify  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED = 20260806  # "date as seed" convention, round3-5 kits

SESSION_A = "round6-session-A"
SESSION_B = "round6-session-B"

WORK_PLAN = [("Read", 2.2), ("Grep", 1.8), ("Read", 2.0),
             ("Write", 1.6), ("Edit", 1.4), ("Write", 1.5)]  # ~14.5s of work


def ev(t, name, session_id, **kw):
    d = {"hook_event_name": name, "session_id": session_id}
    d.update(kw)
    return {"t": round(t, 3), "event": d}


def work_events(t0, t1, session_id):
    """Fills [t0, t1) with alternating tool calls for one session, so both
    sessions sound "busy" throughout -- the gesture under test is the only
    difference between them, matching round5's isolate-the-variable style."""
    events = []
    t = t0
    i = 0
    while t < t1:
        tool, gap = WORK_PLAN[i % len(WORK_PLAN)]
        inp = {"command": "pytest"} if tool == "Bash" else {"file_path": "z.py"}
        events.append(ev(t, "PreToolUse", session_id, tool_name=tool, tool_input=inp))
        events.append(ev(t + min(0.4, gap * 0.5), "PostToolUse", session_id, tool_name=tool))
        t += gap
        i += 1
    return events


def two_session_base(duration, gesture_session, other_only=False):
    """Two interleaved sessions, both working for the whole clip. session A
    starts first (t=0) so it always claims slot 0/center; session B starts
    at t=0.6 so it always claims slot 1/left (BRIEF-v2.5 section 2.1) --
    first-free allocation is order-of-first-event, not id or side. Returns
    events with no terminal gesture; callers add the Stop/chime under test."""
    events = [
        ev(0.0, "SessionStart", SESSION_A),
        ev(0.3, "UserPromptSubmit", SESSION_A, prompt="work on session A"),
        ev(0.6, "SessionStart", SESSION_B),
        ev(0.9, "UserPromptSubmit", SESSION_B, prompt="work on session B"),
    ]
    events += work_events(2.0, duration - 3.0, SESSION_A)
    events += work_events(2.3, duration - 3.0, SESSION_B)
    return events


def build_which_finished(duration=32.0, stop_session=SESSION_B):
    """(b) "which side finished?" -- stop_session works, then goes quiet and
    plays the Stop cadence at stop_t (a clean ending, round5-style, so the
    cadence isn't buried under its own session's tool-call activity bumps);
    the other session keeps working right through the tail, unresolved."""
    stop_t = duration - 6.0
    other = SESSION_A if stop_session == SESSION_B else SESSION_B
    events = [
        ev(0.0, "SessionStart", SESSION_A),
        ev(0.3, "UserPromptSubmit", SESSION_A, prompt="work on session A"),
        ev(0.6, "SessionStart", SESSION_B),
        ev(0.9, "UserPromptSubmit", SESSION_B, prompt="work on session B"),
    ]
    events += work_events(2.0, stop_t - 1.5, stop_session)  # quiets before its own Stop
    events += work_events(2.3, duration - 0.5, other)  # keeps working through the tail
    events.append(ev(stop_t, "Stop", stop_session))
    return events, duration


def build_which_needs_you(duration=30.0, notify_session=SESSION_B):
    """(a) "which side needs you?" -- notify_session fires the needs-you
    chime (Notification) partway through, both sessions keep working."""
    notify_t = duration * 0.55
    events = two_session_base(duration, notify_session)
    events.append(ev(notify_t, "Notification", notify_session))
    return events, duration


def build_control_single_session(duration=28.0):
    """v2.4-style control: ONE session only. Slot 0/center by construction
    (BRIEF-v2.5 section 2.2) -- no left/right cue exists, correct answer is
    "can't tell" for both questions."""
    events = [
        ev(0.0, "SessionStart", SESSION_A),
        ev(0.5, "UserPromptSubmit", SESSION_A, prompt="work alone"),
    ]
    events += work_events(2.0, duration - 8.0, SESSION_A)
    events.append(ev(duration - 6.0, "Stop", SESSION_A))
    return events, duration


def build_control_both_finish(duration=30.0):
    """Control: BOTH sessions play the Stop cadence, seconds apart. A cue
    exists (A center, B left) but it does not answer "which ONE side
    finished" -- both did. Correct answer: "can't tell" / both."""
    events = two_session_base(duration, None)
    stop_t = duration - 6.0
    events.append(ev(stop_t, "Stop", SESSION_A))
    events.append(ev(stop_t + 1.2, "Stop", SESSION_B))
    return events, duration


def write_jsonl(events, path):
    with open(path, "w") as f:
        for e in sorted(events, key=lambda x: x["t"]):
            f.write(json.dumps(e) + "\n")


def render(name, events, seed, pan_only=False):
    jsonl_path = os.path.join(OUT_DIR, f"{name}.jsonl")
    wav_path = os.path.join(OUT_DIR, f"{name}.wav")
    write_jsonl(events, jsonl_path)

    # BRIEF-v2.5 section 3 fallback / section 6 control arm: zero every
    # pitch-offset column of the slot palette IN-PROCESS, leaving pan
    # untouched. src/classify.py itself is never modified -- SessionTracker
    # reads the module-level SLOT_PALETTE global at call time, so patching
    # it here before the render (and restoring after) is enough.
    saved_palette = classify.SLOT_PALETTE
    try:
        if pan_only:
            classify.SLOT_PALETTE = tuple((pan, 0) for pan, _semi in saved_palette)
        run_render(jsonl_path, wav_path, seed=seed)
    finally:
        classify.SLOT_PALETTE = saved_palette

    mp3_path = os.path.join(OUT_DIR, f"{name}.mp3")
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
             "-codec:a", "libmp3lame", "-b:a", "192k", mp3_path],
            check=True,
        )
        os.remove(wav_path)
        return mp3_path
    return wav_path


def main():
    finished_events, finished_dur = build_which_finished()
    needs_events, needs_dur = build_which_needs_you()
    ctrl_single_events, ctrl_single_dur = build_control_single_session()
    ctrl_both_events, ctrl_both_dur = build_control_both_finish()

    # session_id ordering (two_session_base: A's SessionStart at t=0, B's at
    # t=0.6) means A always claims slot 0 (center) and B always claims slot
    # 1 (pan -0.40 = LEFT, SLOT_PALETTE[1]). The gesture-under-test always
    # plays on session B here, so the correct answer is always "Left".
    #
    # (name, events, seed, pan_only, question, correct_answer, gesture_t, gesture_session)
    variants = [
        ("finished_full", finished_events, 601, False,
         "finished", "Left", finished_dur - 6.0, SESSION_B),
        ("finished_panonly", finished_events, 602, True,
         "finished", "Left", finished_dur - 6.0, SESSION_B),
        ("needsyou_full", needs_events, 603, False,
         "needsyou", "Left", needs_dur * 0.55, SESSION_B),
        ("needsyou_panonly", needs_events, 604, True,
         "needsyou", "Left", needs_dur * 0.55, SESSION_B),
        ("control_single", ctrl_single_events, 605, False,
         "both", "Can't tell", None, None),
        ("control_both_finish", ctrl_both_events, 606, False,
         "finished", "Can't tell", None, None),
    ]

    paths = {}
    for name, events, seed, pan_only, *_ in variants:
        print(f"rendering {name} (seed={seed}, pan_only={pan_only})...")
        paths[name] = render(name, events, seed, pan_only=pan_only)

    labels = ["M", "N", "O", "P", "Q", "R"]
    names = [v[0] for v in variants]
    rng = np.random.default_rng(SEED)
    shuffled = names.copy()
    rng.shuffle(shuffled)
    mapping = dict(zip(labels, shuffled))

    meta = {v[0]: v for v in variants}
    answer_key_path = os.path.join(OUT_DIR, "answer-key-round6.txt")
    with open(answer_key_path, "w") as f:
        for label, name in mapping.items():
            _, _, _, pan_only, question, answer, gesture_t, gesture_session = meta[name]
            arm = "pan-only" if pan_only else ("full" if question != "both" else "n/a")
            f.write(
                f"{label} = {name} (arm: {arm}; question: {question}; "
                f"ground truth: {answer})\n"
            )

    for label, name in mapping.items():
        src = paths[name]
        ext = os.path.splitext(src)[1]
        dst = os.path.join(OUT_DIR, f"round6_{label}{ext}")
        os.replace(src, dst)
        print(f"  {label} = {name} -> {os.path.basename(dst)}")

    print(f"\nanswer key written to {answer_key_path}")


if __name__ == "__main__":
    main()
