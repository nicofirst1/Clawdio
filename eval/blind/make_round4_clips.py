#!/usr/bin/env python3
"""Round 4 blind kit: full-mix in-context timbre comparison.

Round 3 (isolated 25s taps) found no winner among woodblock/marimba/plink and
concluded timbre selection has to be redone IN CONTEXT (candidate embedded in
the full ambient mix, not a dry tap loop) and as forced ranking only, per its
own methodology conclusions (research/blind-round3-2026-08-04.md).

Renders 4 full-mix variants of the SAME session (the Block C "flip-point
probe" script from eval/make_clips.py -- calm read/grep -> busy writes -> a
hot Bash burst -> back to busy -> Stop, ~50s):

  - v2.2 control: the shipped v2.2 mapping (noise-tick drops, old density
    knobs, no air-bed cut) -- reproduces the exact pre-v2.3 engine behavior
    by overriding the module globals to their v2.2 values around the render
    call, same technique eval/make_clips.py's build_block_c uses for its own
    A/B variants.
  - v2.3 x 3: the current shipped v2.3 engine (half-density, air-bed cut)
    with drop_timbre set to each of woodblock (v2.3 default), marimba, plink.

Shuffles to neutral labels and writes eval/blind/answer-key-round4.txt.

Usage: .venv/bin/python eval/blind/make_round4_clips.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
os.environ.setdefault("CLAUDIO_QUIET", "1")
os.environ["CLAUDIO_THEME"] = "ambient"

import numpy as np  # noqa: E402
from ambient_layers import (  # noqa: E402
    AIR_V23_HARD_CEILING_HZ, AIR_V23_LEVEL_CUT_DB, AMBIENT_CONFIG,
    BURST_COALESCE_WINDOW_S, DROP_MIN_GAP_S, DROP_RATE_SCALE,
)
from io_modes import run_render  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SEED = 20260804  # same "date as seed" convention as make_timbre_clips.py


def ev(t, name, **kw):
    d = {"hook_event_name": name}
    d.update(kw)
    return {"t": round(t, 3), "event": d}


def pacing_session_events():
    """Same ~50s calm->busy->hot-burst->calm script as
    eval/make_clips.py's _pacing_session_events (Block C), reproduced here
    (read-only reference, not imported, to keep this script standalone)."""
    events = [ev(0.0, "SessionStart"), ev(1.0, "UserPromptSubmit", prompt="do a bunch of stuff")]
    t = 4.0
    plan = [("Read", 3.0)] * 3 + [("Write", 1.2)] * 5 + [("Bash", 0.35)] * 12 + [("Edit", 1.5)] * 4
    for tool, gap in plan:
        events.append(ev(t, "PreToolUse", tool_name=tool, tool_input={"file_path": "z.py", "command": "pytest"}))
        events.append(ev(t + min(0.4, gap * 0.5), "PostToolUse", tool_name=tool))
        t += gap
    events.append(ev(t + 2.0, "Stop"))
    return events


def write_jsonl(events, path):
    with open(path, "w") as f:
        for e in sorted(events, key=lambda x: x["t"]):
            f.write(json.dumps(e) + "\n")


def render(name, events, seed, drop_timbre=None, v22_mode=False):
    """v22_mode=True temporarily overrides the module globals the way
    eval/make_clips.py's build_block_c does, so the control clip reproduces
    the exact shipped v2.2 engine behavior rather than v2.3's new defaults."""
    global DROP_MIN_GAP_S, BURST_COALESCE_WINDOW_S, DROP_RATE_SCALE
    global AIR_V23_LEVEL_CUT_DB, AIR_V23_HARD_CEILING_HZ

    jsonl_path = os.path.join(OUT_DIR, f"{name}.jsonl")
    wav_path = os.path.join(OUT_DIR, f"{name}.wav")
    write_jsonl(events, jsonl_path)

    saved = dict(
        drop_min_gap=DROP_MIN_GAP_S,
        coalesce_win=BURST_COALESCE_WINDOW_S,
        rate_scale=DROP_RATE_SCALE,
        air_cut=AIR_V23_LEVEL_CUT_DB,
        air_ceiling=AIR_V23_HARD_CEILING_HZ,
        cfg_timbre=AMBIENT_CONFIG.drop_timbre,
    )
    try:
        if v22_mode:
            DROP_MIN_GAP_S = 0.150
            BURST_COALESCE_WINDOW_S = 0.250
            DROP_RATE_SCALE = 1.0
            AIR_V23_LEVEL_CUT_DB = 0.0
            AIR_V23_HARD_CEILING_HZ = 1.0e9  # effectively uncapped
            AMBIENT_CONFIG.drop_timbre = "noise"
        elif drop_timbre is not None:
            AMBIENT_CONFIG.drop_timbre = drop_timbre
        run_render(jsonl_path, wav_path, seed=seed)
    finally:
        DROP_MIN_GAP_S = saved["drop_min_gap"]
        BURST_COALESCE_WINDOW_S = saved["coalesce_win"]
        DROP_RATE_SCALE = saved["rate_scale"]
        AIR_V23_LEVEL_CUT_DB = saved["air_cut"]
        AIR_V23_HARD_CEILING_HZ = saved["air_ceiling"]
        AMBIENT_CONFIG.drop_timbre = saved["cfg_timbre"]

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
    events = pacing_session_events()

    # (variant name, seed, kwargs for render())
    variants = [
        ("v22_control", 401, dict(v22_mode=True)),
        ("v23_woodblock", 402, dict(drop_timbre="woodblock")),
        ("v23_marimba", 403, dict(drop_timbre="marimba")),
        ("v23_plink", 404, dict(drop_timbre="plink")),
    ]

    paths = {}
    for name, seed, kw in variants:
        print(f"rendering {name} (seed={seed})...")
        paths[name] = render(name, events, seed, **kw)

    # shuffle-assign to neutral labels
    labels = ["W", "X", "Y", "Z"]
    names = [v[0] for v in variants]
    rng = np.random.default_rng(SEED)
    shuffled = names.copy()
    rng.shuffle(shuffled)
    mapping = dict(zip(labels, shuffled))

    answer_key_path = os.path.join(OUT_DIR, "answer-key-round4.txt")
    with open(answer_key_path, "w") as f:
        for label, name in mapping.items():
            f.write(f"{label} = {name}\n")

    # rename the rendered files to their shuffled labels (neutral filenames
    # for the HTML player, same as round3.html's P/Q/R/S)
    for label, name in mapping.items():
        src = paths[name]
        ext = os.path.splitext(src)[1]
        dst = os.path.join(OUT_DIR, f"round4_{label}{ext}")
        os.replace(src, dst)
        print(f"  {label} = {name} -> {os.path.basename(dst)}")

    print(f"\nanswer key written to {answer_key_path}")


if __name__ == "__main__":
    main()
