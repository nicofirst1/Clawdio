#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy", "scipy"]
# ///
"""analyze_render.py -- BRIEF-v2.md/BRIEF-v2.2.md section 7 acceptance battery
+ listenability proxies for claude-geiger v2/v2.2 renders.

v2.2 amends section 7 items 1, 2, 7 (spectral slope band + conformity,
centroid floor+ceiling, stereo correlation + L-R balance) and adds items
N1-N4 (drop-rate cap, embedding rule, RT60, eventfulness ordering) -- see
BRIEF-v2.2.md section 7. The N1-N4 checks require --events (they need to
know where in the render specific gestures/phases happen).

Usage:
    python3 tools/analyze_render.py RENDER.wav [--window A:B] [--arc] [--json]
    python3 tools/analyze_render.py RENDER.wav --steady 40:80   # steady-state window
    python3 tools/analyze_render.py RENDER.wav --events events.jsonl --steady 40:80

Every section-7/7' item (+ N1-N4 when --events is given) is reported as
PASS/FAIL/N/A with the measured number. The "--arc" mode additionally prints
the per-10s RMS/centroid/onset arc used for listenability tuning (part B of
the verification checklist).

Exit code 0 if all *run* checks pass, 1 otherwise.

Implementation note: the actual measurement code lives in the tools/analysis/
package (io_utils, spectral, modulation, battery, info_checks, n_checks,
arc). This module is the CLI entry point and a backward-compat facade that
re-exports every previously-public name so existing callers (tools/lab.py,
tools/complaint_checks.py, tests/test_ambient.py) keep working unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from analysis.arc import arc, print_arc
from analysis.battery import Report, run_battery
from analysis.info_checks import (
    band_energy, busiest_window, info_checks, load_events, window_tool_count,
)
from analysis.io_utils import db, load_wav, mono, rms, rms_db
from analysis.modulation import (
    ONSET_FRAME, ONSET_HOP, ONSET_MEDWIN_S, ONSET_REFRACTORY_S,
    crest_windows, env_mod_coherent, env_mod_depth, lr_balance_worst_5s,
    mono_comb_check, onset_count, onset_events, short_term_rms_db,
    stereo_correlation,
)
from analysis.n_checks import (
    N2_BASELINE_OFFSETS, N2_WIN_S, N4_FADEIN_SKIP_S, N4_IDLE_MIN_DUR_S,
    _EMBEDDING_EVENTS, eventfulness_proxy, n1_drop_rate_cap,
    n2_embedding_rule, n3_rt60_tail, n4_eventfulness_ordering, spearman,
)
from analysis.spectral import (
    DRONE_FUNDAMENTALS, brightness_ratio, centroid_and_hf, octave_bands,
    spectral_flatness, spectral_slope, tonal_prominence, welch_psd,
)

SR_DEFAULT = 48000


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--steady", default=None,
                    help="A:B seconds -- steady active window for the strict battery")
    ap.add_argument("--arc", action="store_true", help="print per-10s energy arc")
    ap.add_argument("--arc-win", type=float, default=10.0)
    ap.add_argument("--events", default=None,
                    help="event jsonl -- enables the section 7 item 9 information checks "
                         "and the v2.2 N1-N4 checks")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    x, sr = load_wav(args.wav)
    dur = len(x) / sr
    steady = None
    if args.steady:
        a, b = args.steady.split(":")
        steady = (float(a), float(b))
    print(f"# {args.wav}  {dur:.2f}s  {sr} Hz  peak={db(np.max(np.abs(x))):.2f} dBFS  "
          f"rms={rms_db(mono(x)):.2f} dBFS"
          + (f"  steady={steady[0]:.0f}-{steady[1]:.0f}s" if steady else ""))
    rep = run_battery(x, sr, steady=steady)
    arc_rows = arc(x, sr, args.arc_win)
    if args.events:
        events = load_events(args.events)
        info_checks(x, sr, events, rep, arc_rows)
        # v2.2 NEW criteria (BRIEF-v2.2.md section 7)
        n1_drop_rate_cap(x, sr, events, rep)
        n2_embedding_rule(x, sr, events, rep)
        n3_rt60_tail(x, sr, events, rep)
        n4_eventfulness_ordering(x, sr, events, rep, arc_rows)
    print(rep.render())
    if args.arc:
        print()
        print_arc(arc_rows)
    if args.json:
        print(json.dumps(rep.rows, indent=1))
    return 0 if rep.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
