"""Spectral analysis helpers -- split out of analyze_render.py."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sps


def welch_psd(x, sr, nperseg=8192):
    nperseg = min(nperseg, len(x))
    f, p = sps.welch(x, fs=sr, nperseg=nperseg, noverlap=nperseg // 2,
                     window="hann", detrend=False, scaling="density")
    return f, p


def octave_bands(f, p, lo=63.0, hi=8000.0):
    """Return (center_freqs, band_level_db) for octave bands lo..hi."""
    centers = []
    c = lo
    while c <= hi * 1.001:
        centers.append(c)
        c *= 2.0
    out_f, out_db = [], []
    for c in centers:
        f1, f2 = c / math.sqrt(2.0), c * math.sqrt(2.0)
        m = (f >= f1) & (f < f2)
        if not np.any(m):
            continue
        # band power (integrate PSD)
        power = float(np.trapezoid(p[m], f[m])) if hasattr(np, "trapezoid") else float(np.trapz(p[m], f[m]))
        out_f.append(c)
        out_db.append(10.0 * math.log10(max(power, 1e-30)))
    return np.array(out_f), np.array(out_db)


def spectral_slope(x, sr, lo=125.0, hi=8000.0):
    """v2.2 amendment (BRIEF-v2.2.md section 7 item 1'): band range narrowed
    to 125 Hz-8 kHz (was 63 Hz-8 kHz) -- the brightness-lift work concentrates
    below 125 Hz on the sub-bass/root-drone region that isn't part of the
    "warm room" character the slope target is trying to capture."""
    f, p = welch_psd(x, sr)
    cf, bdb = octave_bands(f, p, lo=lo, hi=hi)
    oct_idx = np.log2(cf / cf[0])
    slope, intercept = np.polyfit(oct_idx, bdb, 1)
    fit = slope * oct_idx + intercept
    dev = bdb - fit
    return slope, cf, bdb, dev


def centroid_and_hf(x, sr, hf_cut=5000.0):
    f, p = welch_psd(x, sr)
    m = f >= 20.0
    f, p = f[m], p[m]
    tot = float(np.sum(p))
    cen = float(np.sum(f * p) / max(tot, 1e-30))
    hf = float(np.sum(p[f > hf_cut]) / max(tot, 1e-30))
    return cen, hf


# v2.3 additions (lit-review-annoyance-2026-08-04.md recs #1-2): measurable
# "hiss/annoyance" proxies, added after blind rounds 2-3 found timbre (not
# density) was the dominant complaint against v2.2. Thresholds calibrated the
# same way every other criterion in this file is: measure the failing
# control (v2.2's shipped render) first, then set the target ~20% below it --
# see docs/research/BRIEF-v2.3.md for the exact measured numbers this was
# calibrated against (demos/realistic-session.jsonl, steady window, v2.2 vs v2.3).


def spectral_flatness(x, sr):
    """Wiener entropy: geometric mean / arithmetic mean of the power
    spectrum. ~1.0 for white noise, ~0 for pure tones/harmonic content --
    the direct proxy for "reads as noise vs. reads as tone" (lit-review
    rec #1)."""
    f, p = welch_psd(x, sr)
    p = np.maximum(p, 1e-30)
    gm = float(np.exp(np.mean(np.log(p))))
    am = float(np.mean(p))
    return gm / am


def brightness_ratio(x, sr, cut=3000.0):
    """Fraction of spectral power above `cut` Hz -- a coarse, cheap proxy for
    Zwicker sharpness (lit-review rec #2) without a full Bark-scale
    transform. Targets the specific "hiss" register the v2.2 noise-tick
    drops and air bed occupied (README: drop grains centered 1.8-3.5kHz)."""
    f, p = welch_psd(x, sr)
    tot = float(np.sum(p))
    hf = float(np.sum(p[f > cut]))
    return hf / max(tot, 1e-30)


# Fundamentals of the theme's *intended* sustained layers (BRIEF section 2):
# C1 sub-bass weather, C2/C3 bed pad, G2 + C4 subagent stems. Section 7 item 5
# exempts "the intended drone fundamental region" (<=15 dB rather than <=12).
DRONE_FUNDAMENTALS = (32.70, 65.41, 98.00, 130.81, 261.63)


def tonal_prominence(x, sr, rel_bw=0.10, fmin=40.0, fmax=16000.0):
    """Max dB by which a narrowband bin exceeds the local (+-10% freq) median.
    Returns (max_excess_db_outside_drone, freq, max_excess_in_drone_region)."""
    f, p = welch_psd(x, sr, nperseg=16384)
    m = (f >= fmin) & (f <= fmax)
    f, p = f[m], p[m]
    pdb = 10.0 * np.log10(np.maximum(p, 1e-30))
    worst_out, worst_out_f = -99.0, 0.0
    worst_drone, worst_drone_f = -99.0, 0.0
    # drone region: fundamentals + low harmonics of C1/C2 bed (<= ~200 Hz)
    for i in range(len(f)):
        fi = f[i]
        lo, hi = fi * (1 - rel_bw), fi * (1 + rel_bw)
        j0 = np.searchsorted(f, lo)
        j1 = np.searchsorted(f, hi)
        if j1 - j0 < 8:
            continue
        med = np.median(pdb[j0:j1])
        exc = pdb[i] - med
        if fi <= 200.0 or any(abs(fi - d) <= 0.02 * d for d in DRONE_FUNDAMENTALS):
            if exc > worst_drone:
                worst_drone, worst_drone_f = exc, fi
        else:
            if exc > worst_out:
                worst_out, worst_out_f = exc, fi
    return worst_out, worst_out_f, worst_drone, worst_drone_f
