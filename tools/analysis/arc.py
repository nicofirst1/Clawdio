"""Arc / listenability -- split out of analyze_render.py."""

from __future__ import annotations

import numpy as np

from analysis.io_utils import db, mono, rms_db
from analysis.modulation import onset_count
from analysis.spectral import centroid_and_hf


def arc(x, sr, win_s=10.0):
    rows = []
    n = int(win_s * sr)
    for s in range(0, len(x), n):
        seg = x[s:s + n]
        if len(seg) < sr // 2:
            continue
        ms_ = mono(seg)
        cen, hf = centroid_and_hf(ms_, sr)
        rows.append(dict(t0=s / sr, t1=(s + len(seg)) / sr, rms_db=rms_db(ms_),
                         peak_db=db(np.max(np.abs(ms_))), centroid=cen,
                         onsets=onset_count(ms_, sr)))
    return rows


def print_arc(rows):
    print(f"{'t0':>6} {'t1':>6} {'RMS dB':>8} {'peak dB':>8} {'cent Hz':>8} {'onset/s':>8}")
    for r in rows:
        print(f"{r['t0']:6.0f} {r['t1']:6.0f} {r['rms_db']:8.2f} {r['peak_db']:8.2f} "
              f"{r['centroid']:8.0f} {r['onsets']:8.1f}")
