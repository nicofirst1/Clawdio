#!/usr/bin/env python3
"""Round 5 blind kit: DONE-state COMPREHENSION test (not preference).

research/BRIEF-v2.4.md's design goal is that three states -- WORKING,
DONE/waiting for user, SESSION OVER -- are unmistakably distinct by ear
alone. Two independent listeners already confirmed the specific defect this
retests: docs/research/eval-v22-baseline-2026-08-04.md's b01/b02/a07 (clean
Stop-cadence endings) were all misread as "still going", and live listening
separately confirmed the same thing. This round asks the SAME comprehension
question the baseline eval did ("how did it end?") against a session
template built to isolate the ending as the only variable.

Renders 4 ~35s clips, same work prefix (a calm read/grep phase then a busy
write/edit phase, ~15s), differing ONLY in the ending:

  (a) success_v24    -- Stop with the v2.4 authentic cadence, then a 20s
                        settled-idle tail (research/BRIEF-v2.4.md default).
  (b) failure_stop   -- a PostToolUseFailure knock partway through, THEN a
                        Stop (v2.4 cadence) + settled-idle tail -- tests
                        whether the cadence still reads as resolved after a
                        mid-session dip (the baseline eval's b03 finding:
                        listeners caught the dip but not the recovery).
  (c) still_going    -- cut off mid-work, no Stop at all -- the "still
                        going" control condition.
  (d) success_v22    -- the SAME success ending as (a), but rendered in
                        done_cadence="v22" legacy mode -- the hidden control
                        that reproduces the original baseline-eval defect
                        (descend-to-C-or-G melody only, no bass root, no
                        settled idle) for direct comparison.

Shuffles to neutral labels and writes answer-key-round5.txt.

Usage: .venv/bin/python eval/blind/round5/make_round5_clips.py
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
import sonifier as S  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED = 20260804  # same "date as seed" convention as round3/round4 kits

WORK_PLAN = [("Read", 2.2), ("Grep", 1.8), ("Read", 2.0),
             ("Write", 1.6), ("Edit", 1.4), ("Write", 1.5)]  # ~14.5s of work


def ev(t, name, **kw):
    d = {"hook_event_name": name}
    d.update(kw)
    return {"t": round(t, 3), "event": d}


def work_prefix():
    """Shared calm-read -> busy-write session opening, identical across all
    4 clips so the ending is the only variable."""
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="do a bunch of stuff")]
    t = 3.0
    for tool, gap in WORK_PLAN:
        inp = {"command": "pytest"} if tool == "Bash" else {"file_path": "z.py"}
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input=inp))
        events.append(ev(t + min(0.4, gap * 0.5), "PostToolUse", tool_name=tool))
        t += gap
    return events, t


def build_success(fail_first=False):
    events, t = work_prefix()
    if fail_first:
        events.append(ev(t, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest"}))
        t += 0.3
        events.append(ev(t, "PostToolUseFailure", tool_name="Bash"))
        t += 1.0
        events.append(ev(t, "PreToolUse", tool_name="Bash", tool_input={"command": "pytest"}))
        t += 0.3
        events.append(ev(t, "PostToolUse", tool_name="Bash"))
        t += 1.5
    stop_t = t + 1.0
    events.append(ev(stop_t, "Stop"))
    # duration-extender: unrecognized event name, silently ignored by
    # handle_event, just here so run_render's duration covers the full 20s
    # settled-idle tail after Stop (duration = last event's t + 3s tail).
    events.append(ev(stop_t + 17.0, "_DurationMarker"))
    return events


def build_still_going():
    events, t = work_prefix()
    # no Stop -- just keep working right up to the clip's natural end, long
    # enough to match the other 3 clips' ~35s length (run_render's duration
    # is last-event-t + 3s tail, so the last tool call needs to land ~15s
    # further out than the others' Stop does).
    more_work = [("Edit", 1.8), ("Write", 1.6), ("Bash", 1.4), ("Read", 1.9),
                 ("Edit", 1.7), ("Write", 1.5), ("Grep", 1.6), ("Edit", 1.8)]
    for tool, gap in more_work:
        inp = {"command": "pytest"} if tool == "Bash" else {"file_path": "z.py"}
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input=inp))
        events.append(ev(t + min(0.4, gap * 0.5), "PostToolUse", tool_name=tool))
        t += gap
    return events


def write_jsonl(events, path):
    with open(path, "w") as f:
        for e in sorted(events, key=lambda x: x["t"]):
            f.write(json.dumps(e) + "\n")


def render(name, events, seed, v22_mode=False):
    jsonl_path = os.path.join(OUT_DIR, f"{name}.jsonl")
    wav_path = os.path.join(OUT_DIR, f"{name}.wav")
    write_jsonl(events, jsonl_path)

    saved_cadence = S.AMBIENT_CONFIG.done_cadence
    try:
        S.AMBIENT_CONFIG.done_cadence = "v22" if v22_mode else "v24"
        S.run_render(jsonl_path, wav_path, seed=seed)
    finally:
        S.AMBIENT_CONFIG.done_cadence = saved_cadence

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
    variants = [
        ("success_v24", build_success(fail_first=False), 501, dict(v22_mode=False)),
        ("failure_stop", build_success(fail_first=True), 502, dict(v22_mode=False)),
        ("still_going", build_still_going(), 503, dict()),
        ("success_v22", build_success(fail_first=False), 504, dict(v22_mode=True)),
    ]

    paths = {}
    for name, events, seed, kw in variants:
        print(f"rendering {name} (seed={seed})...")
        paths[name] = render(name, events, seed, **kw)

    labels = ["M", "N", "O", "P"]
    names = [v[0] for v in variants]
    rng = np.random.default_rng(SEED)
    shuffled = names.copy()
    rng.shuffle(shuffled)
    mapping = dict(zip(labels, shuffled))

    answer_key_path = os.path.join(OUT_DIR, "answer-key-round5.txt")
    ground_truth = {
        "success_v24": "Succeeded",
        "failure_stop": "Succeeded",  # failed once, retried, then a clean Stop
        "still_going": "Still going",
        "success_v22": "Succeeded",  # hidden control -- same true ending as success_v24
    }
    with open(answer_key_path, "w") as f:
        for label, name in mapping.items():
            f.write(f"{label} = {name} (ground truth: {ground_truth[name]})\n")

    for label, name in mapping.items():
        src = paths[name]
        ext = os.path.splitext(src)[1]
        dst = os.path.join(OUT_DIR, f"round5_{label}{ext}")
        os.replace(src, dst)
        print(f"  {label} = {name} -> {os.path.basename(dst)}")

    print(f"\nanswer key written to {answer_key_path}")


if __name__ == "__main__":
    main()
