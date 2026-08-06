#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy",
#     "scipy",
# ]
# [tool.uv]
# # sounddevice is OPTIONAL: only required for live audio playback (not --render).
# ///
"""Claudio sonifier.

Real-time audio sonification daemon for Claude Code. Turns Claude Code hook
events into an audio soundscape describing the session. As of v2 the default
sound is "ambient" (generative pad + rain + melodic bloom, see
AmbientTheme / BRIEF-v2.md); the original v1 Geiger-counter click-train sound
is preserved verbatim as GeigerTheme and selectable via SONIFIER_THEME=geiger.

Usage:
    python3 sonifier.py                          # live mode (needs sounddevice)
    python3 sonifier.py --render events.jsonl out.wav [--seed N]
    python3 sonifier.py --check                  # print config/capability JSON

Env SONIFIER_THEME selects the sound layer: "ambient" (default) or "geiger"
(legacy v1 sound). Everything else (ingress, ports, CLI, hooks) is theme-
agnostic and unchanged between v1 and v2.

See the module docstrings on render_block / EngineState (GeigerTheme) /
AmbientTheme / classify for the core DSP + mapping logic.

This module is the CLI entry point; the implementation lives in sibling
modules (config, classify, dsp, geiger, ambient_layers, ambient, themes,
io_modes) split out for maintainability. Callers that used to do
`from sonifier import X` should import X from the module that owns it
directly.
"""

from __future__ import annotations

import argparse
import sys

from config import _env_bool_flag
from io_modes import run_check, run_live, run_render
from logging_setup import configure as _configure_logging, get_logger

log = get_logger("sonifier")


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

USAGE = """\
usage: sonifier.py [--check | --render IN.jsonl OUT.wav [--seed N] | --help]

  (no args)               run the live daemon (needs sounddevice)
  --check                 print config/capability JSON, exit
  --render IN.jsonl OUT.wav [--seed N]
                          offline render, no audio hardware needed
  -h, --help              show this message and exit

Key env vars: SONIFIER_PORT (9753), SONIFIER_THEME (ambient|geiger),
SONIFIER_VOLUME, SONIFIER_MUTE=1, SONIFIER_IDLE_EXIT_MIN, SONIFIER_LOG_DIR.
"""


class _ArgParser(argparse.ArgumentParser):
    def error(self, message):
        print(f"unknown flag: {message}", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        raise SystemExit(2)

    def print_help(self, file=None):
        print(USAGE, end="", file=file)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    parser = _ArgParser(add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", nargs=2, metavar=("IN", "OUT"))
    parser.add_argument("--seed", type=int, default=0)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code

    if args.help:
        print(USAGE, end="")
        return 0

    _configure_logging(quiet=_env_bool_flag("SONIFIER_QUIET", False))

    if args.check:
        return run_check()

    if args.render:
        events_path, out_path = args.render
        run_render(events_path, out_path, seed=args.seed)
        return 0

    run_live()
    return 0


if __name__ == "__main__":
    sys.exit(main())
