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
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import wave

import numpy as np
from scipy import signal as sps

SR_DEFAULT = 48000

# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------


def load_wav(path):
    with wave.open(path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sw != 2:
        raise SystemExit(f"expected 16-bit wav, got sampwidth={sw}")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    data = data.reshape(-1, nch)
    if nch == 1:
        data = np.repeat(data, 2, axis=1)
    return data, sr


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-12))


def rms(x):
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def rms_db(x):
    return db(rms(x))


def mono(x):
    return 0.5 * (x[:, 0] + x[:, 1])


# --------------------------------------------------------------------------
# Spectral helpers
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Modulation / envelope
# --------------------------------------------------------------------------


def env_mod_depth(x, sr, band, env_lp=None, nfft_s=4.0):
    """Modulation depth of the Hilbert envelope inside `band` (Hz).

    depth = 2*|X(f)| / mean(env)  (peak-to-mean sinusoidal AM depth), taken as
    the max over single FFT components in the band, averaged over windows.
    """
    lo, hi = band
    env = np.abs(sps.hilbert(x))
    if env_lp is not None:
        b, a = sps.butter(2, env_lp / (sr / 2.0), btype="low")
        env = sps.filtfilt(b, a, env)
    n = int(nfft_s * sr)
    if n < 64 or len(env) < n:
        n = len(env)
    depths = []
    for start in range(0, len(env) - n + 1, n):
        seg = env[start:start + n]
        mean = float(np.mean(seg))
        if mean <= 1e-9:
            continue
        w = np.hanning(len(seg))
        acg = 1.0 / np.mean(w)  # amplitude coherent gain correction
        spec = np.fft.rfft((seg - mean) * w) / len(seg) * 2.0 * acg
        fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
        m = (fr >= lo) & (fr <= hi)
        if not np.any(m):
            continue
        depths.append(float(np.max(np.abs(spec[m]))) / mean)
    if not depths:
        return 0.0
    return float(np.max(depths))


def env_mod_coherent(x, sr, band, env_lp=None, nfft_s=4.0, med_bins=24,
                     ratio_gate=4.0):
    """Depth of *coherent* (tonal) envelope modulation inside `band`.

    Rationale: env_mod_depth() above takes the single largest envelope-spectrum
    component, which for any stochastic texture (rain grains, noise beds)
    measures the texture's own shot-noise floor rather than a modulator --
    narrowband-filtered or sparse-impulsive noise shows tens of percent by that
    measure while sounding perfectly smooth. Psychoacoustic fluctuation-
    strength / roughness models are defined for a *periodic* modulator, so the
    quantity that matters for "no LFO, no tremolo, no roughness" is how far a
    component stands out of its own local envelope-spectrum floor. Here the
    envelope power spectrum is averaged over windows and the local median is
    subtracted before converting back to a modulation depth; white/pink noise
    and Poisson grain streams score ~0, a real 4 Hz tremolo of depth d scores d.
    """
    lo, hi = band
    env = np.abs(sps.hilbert(x))
    if env_lp is not None:
        b, a = sps.butter(2, env_lp / (sr / 2.0), btype="low")
        env = sps.filtfilt(b, a, env)
    n = int(nfft_s * sr)
    if n < 64 or len(env) < n:
        n = len(env)
    acc = None
    nwin = 0
    for start in range(0, len(env) - n + 1, n // 2):
        seg = env[start:start + n]
        mean = float(np.mean(seg))
        if mean <= 1e-9:
            continue
        w = np.hanning(len(seg))
        acg = 1.0 / np.mean(w)
        spec = np.fft.rfft((seg / mean - 1.0) * w) / len(seg) * 2.0 * acg
        p = np.abs(spec) ** 2
        acc = p if acc is None else acc + p
        nwin += 1
    if not nwin:
        return 0.0
    p = acc / nwin
    fr = np.fft.rfftfreq(n, 1.0 / sr)
    m = np.where((fr >= lo) & (fr <= hi))[0]
    if len(m) == 0:
        return 0.0
    # A bin only counts as a coherent modulator if it stands clear of its own
    # local floor. Measured separation on synthetic references (60 s):
    # white noise 1.6x, Poisson 30/s grain stream 1.2x, a real 10% tremolo
    # 159-621x. The 4.0x gate sits in that two-orders-of-magnitude gap.
    best = 0.0
    for k in m:
        j0, j1 = max(0, k - med_bins), min(len(p), k + med_bins + 1)
        med = float(np.median(p[j0:j1]))
        if med > 0 and p[k] / med < ratio_gate:
            continue
        best = max(best, p[k] - med)
    return float(math.sqrt(max(best, 0.0)))


def crest_windows(x, sr, win_s=1.0):
    n = int(win_s * sr)
    out = []
    for s in range(0, len(x) - n + 1, n):
        seg = x[s:s + n]
        r = rms(seg)
        pk = float(np.max(np.abs(seg)))
        if r <= 1e-9:
            continue
        out.append(20 * math.log10(pk / r))
    return np.array(out)


def short_term_rms_db(x, sr, win_s=3.0, hop_s=1.0):
    n = int(win_s * sr)
    h = int(hop_s * sr)
    out = []
    for s in range(0, max(1, len(x) - n + 1), h):
        out.append(rms_db(x[s:s + n]))
    return np.array(out)


def stereo_correlation(x):
    l, r = x[:, 0], x[:, 1]
    if np.std(l) < 1e-9 or np.std(r) < 1e-9:
        return 1.0
    return float(np.corrcoef(l, r)[0, 1])


def lr_balance_worst_5s(x, sr, win_s=5.0, hop_s=2.5):
    """v2.2 NEW criterion 7': worst-case |L-R| RMS-dB difference over any 5s
    window. Returns the max |L_db - R_db| across all windows (0.0 if the
    render is shorter than one window)."""
    n = int(win_s * sr)
    h = int(hop_s * sr)
    if len(x) < n:
        n = len(x)
        h = max(1, n)
    worst = 0.0
    for s in range(0, max(1, len(x) - n + 1), max(1, h)):
        seg = x[s:s + n]
        l_db = rms_db(seg[:, 0])
        r_db = rms_db(seg[:, 1])
        worst = max(worst, abs(l_db - r_db))
    return worst


def mono_comb_check(x, sr):
    """Compare mono-sum spectrum with the average of the two channel spectra.
    Returns the worst notch (dB) in 100 Hz-8 kHz after smoothing."""
    f, pl = welch_psd(x[:, 0], sr)
    _, pr = welch_psd(x[:, 1], sr)
    _, pm = welch_psd(mono(x), sr)
    avg = 0.5 * (pl + pr)
    m = (f >= 100.0) & (f <= 8000.0)
    d = 10 * np.log10(np.maximum(pm[m], 1e-30)) - 10 * np.log10(np.maximum(avg[m], 1e-30))
    # smooth over ~1/6 octave to ignore Welch bin noise
    k = 9
    d = np.convolve(d, np.ones(k) / k, mode="same")[k:-k]
    return float(np.min(d)) if len(d) else 0.0


ONSET_FRAME = 128        # 2.7 ms -- matched to the 4-10 ms drop grain
ONSET_HOP = 64           # 1.3 ms
ONSET_MEDWIN_S = 0.4     # local-bed estimator span
ONSET_REFRACTORY_S = 0.10  # < engine's DROP_MIN_GAP_S (0.150 s)


def onset_events(x, sr, lo=1200.0, hi=6000.0, prom_db=6.0):
    """Rain-drop onset detector. Returns [(time_s, excess_db)].

    Detection function = frame log-energy in the drop-grain band MINUS a
    running 0.4 s median of itself, i.e. how far this instant stands above
    the *local bed level*; peaks above `prom_db` with a 100 ms refractory.

    v2.2 VERIFIER REWRITE -- the previous version used scipy prominence on
    raw frame log-energy with no bed reference, and it did not work on a
    v2.2 mix. Ground truth (engine instrumented to log every
    _spawn_one_drop) on the v2.2 renders:

        render      GT drops   old detector
        idle-only          0   172 onsets (2.46/s)  <- 100% false positives
        a=1.0 stress     128    77 onsets (1.67/s)  <- 40% miss rate

    and the prominence distributions of the two were identical (6.0-9.9 dB
    both), i.e. it had no discriminative power at all: it was measuring the
    bed's own 2.7 ms-scale log-energy fluctuation (measured sigma 1.2 dB,
    p99 excursion +3.7 dB), which is an unavoidable property of any noise
    bed and got worse as v2.2 raised the bed. Every N1/N4/9b anomaly the
    builder reported traces back to this.

    The bed-relative formulation is immune to bed level by construction.
    Re-validated against the same ground truth at prom_db=6.0:

        idle-only   0 GT ->   2 detections over 70 s (0.03/s)
        focus      21 GT ->  16 detections, recall 0.67, precision 0.88
        realistic  10 GT ->  16 detections, recall 1.00, precision 0.62
        stress    155 GT -> 110 detections, recall 0.71, precision 1.00

    Recall is < 1 because the quietest quarter of the drop amplitude
    distribution genuinely sits within a few dB of the bed; that is a
    uniform, level-independent undercount, so the *ordering* and
    *correlation* quantities N4/9b are built on are unaffected. N1 is a
    ceiling check, where the residual over-counting on `realistic`
    (non-drop onsets: notes, the knock) errs in the safe direction. The
    engine-side cap is additionally asserted against dispatch ground truth
    in tests/test_ambient.py::test_v22_drop_rate_never_exceeds_cap_under_flood.
    """
    nyq = sr / 2.0
    b, a = sps.butter(2, [lo / nyq, min(hi, nyq * 0.99) / nyq], btype="band")
    y = sps.lfilter(b, a, x)
    hop, frame = ONSET_HOP, ONSET_FRAME
    nfr = (len(y) - frame) // hop
    if nfr <= 8:
        return []
    idx = np.arange(nfr) * hop
    seg = np.lib.stride_tricks.sliding_window_view(y, frame)[idx]
    e = 10.0 * np.log10(np.mean(seg * seg, axis=1) + 1e-14)
    fps = sr / hop
    k = int(ONSET_MEDWIN_S * fps) | 1
    k = min(k, (len(e) - 1) | 1)
    if k < 3:
        return []
    bed = sps.medfilt(e, kernel_size=k)
    d = e - bed
    peaks, _ = sps.find_peaks(d, height=prom_db,
                              distance=max(1, int(ONSET_REFRACTORY_S * fps)))
    times = (peaks * hop + frame / 2.0) / sr
    return list(zip(times.tolist(), d[peaks].tolist()))


def onset_count(x, sr, lo=1200.0, hi=6000.0, prom_db=6.0):
    """Rain-drop transients per second (see onset_events for the detector)."""
    events = onset_events(x, sr, lo=lo, hi=hi, prom_db=prom_db)
    dur = len(x) / sr
    return len(events) / dur if dur > 0 else 0.0


# --------------------------------------------------------------------------
# Section 7 battery
# --------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.rows = []

    def add(self, item, target, measured, ok, note=""):
        # ok=None => N/A (the render does not contain the thing this item
        # measures, e.g. no scripted failure in a steady focus loop). N/A
        # rows are reported but do not count against the verdict.
        self.rows.append(dict(item=item, target=target, measured=measured,
                              ok=None if ok is None else bool(ok), note=note))

    def ok(self):
        return all(r["ok"] for r in self.rows if r["ok"] is not None)

    def render(self):
        w1 = max(len(r["item"]) for r in self.rows) + 1
        w2 = max(len(r["target"]) for r in self.rows) + 1
        w3 = max(len(r["measured"]) for r in self.rows) + 1
        lines = [f"{'CHECK'.ljust(w1)}| {'TARGET'.ljust(w2)}| {'MEASURED'.ljust(w3)}| RESULT"]
        lines.append("-" * (w1 + w2 + w3 + 14))
        for r in self.rows:
            verdict = "N/A " if r["ok"] is None else ("PASS" if r["ok"] else "FAIL")
            lines.append(f"{r['item'].ljust(w1)}| {r['target'].ljust(w2)}| "
                         f"{r['measured'].ljust(w3)}| {verdict}"
                         + (f"  {r['note']}" if r["note"] else ""))
        return "\n".join(lines)


def run_battery(x, sr, steady=None, label=""):
    """x: (n,2). steady: (t0,t1) seconds of steady active state."""
    rep = Report()
    m = mono(x)

    if steady is not None:
        s0, s1 = int(steady[0] * sr), int(steady[1] * sr)
        xs = x[s0:s1]
    else:
        xs = x
    ms = mono(xs)

    # 10. no NaN/inf (run first: everything else depends on it)
    finite = bool(np.all(np.isfinite(x)))
    rep.add("10 finite", "no NaN/inf", "finite" if finite else "NON-FINITE", finite)

    # 1'. spectral slope (BRIEF-v2.2.md section 7 item 1': -4.5+-1.5 dB/oct
    # over 125Hz-8kHz (was 63Hz-8kHz), band conformity +-6dB (was +-5dB)).
    slope, cf, bdb, dev = spectral_slope(ms, sr, lo=125.0, hi=8000.0)
    band_ok = float(np.max(np.abs(dev)))
    rep.add("1a' slope (v2.2)", "-6..-3 dB/oct (125Hz-8k)", f"{slope:+.2f}", -6.0 <= slope <= -3.0)
    rep.add("1b' band conformity (v2.2)", "<= 6.0 dB", f"{band_ok:.2f}", band_ok <= 6.0,
            note="bands " + " ".join(f"{c:.0f}:{d:+.1f}" for c, d in zip(cf, dev)))

    # 2'. HF fraction unchanged; centroid amended to a FLOOR+ceiling band
    # [350, 1200] Hz (was just "<=1500Hz") -- the direct fix for the v2
    # listener evidence "dark cave" (measured centroid ~157 Hz).
    cen, hf = centroid_and_hf(ms, sr)
    rep.add("2a HF>5k fraction", "<= 10%", f"{100 * hf:.2f}%", hf <= 0.10)
    rep.add("2b' centroid (v2.2)", "350..1200 Hz", f"{cen:.0f} Hz", 350.0 <= cen <= 1200.0)

    # 1c/1d (v2.3, lit-review-annoyance recs #1-2): spectral flatness and
    # >3kHz brightness ratio. Thresholds are 20% below the measured v2.2
    # shipped render on demos/realistic-session.jsonl's steady window (flatness
    # 0.001095, brightness 0.005118 -- see docs/research/BRIEF-v2.3.md); v2.2
    # fails both, v2.3 passes both, by construction of the threshold.
    flat = spectral_flatness(ms, sr)
    rep.add("1c spectral flatness (v2.3)", "<= 0.000876", f"{flat:.6f}", flat <= 0.000876,
            note="Wiener entropy, geometric/arithmetic mean of PSD; v2.2 control measures ~0.001095")
    bright = brightness_ratio(ms, sr)
    rep.add("1d brightness >3kHz (v2.3)", "<= 0.41%", f"{100 * bright:.2f}%", bright <= 0.004095,
            note="fraction of spectral power above 3kHz; v2.2 control measures ~0.51%")

    # 3. slow AM 0.5-10 Hz (coherent component; raw max reported alongside)
    d_slow = env_mod_coherent(ms, sr, (0.5, 10.0), env_lp=30.0, nfft_s=4.0)
    d_slow_raw = env_mod_depth(ms, sr, (0.5, 10.0), env_lp=30.0, nfft_s=4.0)
    rep.add("3 slow AM 0.5-10Hz", "<= 10%", f"{100 * d_slow:.2f}%", d_slow <= 0.10,
            note=f"(raw max component incl. stochastic floor: {100 * d_slow_raw:.1f}%)")

    # 4. roughness 20-150 Hz.
    # Measured on the signal highpassed at 200 Hz. Roughness is a within-
    # critical-band beating phenomenon in the mid/high range; on a full-range
    # signal the Hilbert envelope of ANY harmonic bass note necessarily
    # carries a component at its own fundamental (65 Hz for this theme's C2
    # drone), which lands inside the 20-150 Hz window and reads as 20%+
    # "roughness" for a signal that is by construction a clean periodic tone.
    # Highpassing first removes that measurement artifact while still
    # catching real roughness (partials 20-150 Hz apart beating in the mid
    # band). The unfiltered figure is reported in the note for reference.
    bhp, ahp = sps.butter(2, 200.0 / (sr / 2.0), btype="high")
    ms_hp = sps.lfilter(bhp, ahp, ms)
    d_rough = env_mod_coherent(ms_hp, sr, (20.0, 150.0), env_lp=400.0, nfft_s=1.0)
    d_rough_raw = env_mod_depth(ms_hp, sr, (20.0, 150.0), env_lp=400.0, nfft_s=1.0)
    rep.add("4 roughness AM 20-150Hz", "<= 10%", f"{100 * d_rough:.2f}%", d_rough <= 0.10,
            note=f"(raw max incl. grain shot-noise floor: {100 * d_rough_raw:.1f}%)")

    # 5. tonal prominence
    exc, exf, dexc, dexf = tonal_prominence(ms, sr)
    rep.add("5a tonal prominence", "<= 12 dB", f"{exc:.1f} dB @{exf:.0f}Hz", exc <= 12.0)
    rep.add("5b drone fundamental", "<= 15 dB", f"{dexc:.1f} dB @{dexf:.0f}Hz", dexc <= 15.0)

    # 6. crest factor
    cw = crest_windows(ms, sr, 1.0)
    lt_cf = 20 * math.log10(float(np.max(np.abs(ms))) / max(rms(ms), 1e-12))
    if len(cw):
        cf_med = float(np.median(cw))
        cf_lo, cf_hi = float(np.percentile(cw, 5)), float(np.percentile(cw, 95))
        rep.add("6a crest (median 1s)", "8..14 dB", f"{cf_med:.1f} dB", 8.0 <= cf_med <= 14.0,
                note=f"p5={cf_lo:.1f} p95={cf_hi:.1f}")
        over = float(np.max(cw) - lt_cf)
        rep.add("6b crest excursion", "<= 6 dB over LT", f"{over:.1f} dB", over <= 6.0,
                note=f"LT CF={lt_cf:.1f}")

    # 7'. stereo: correlation tightened to 0.5-0.9 (was 0.3-0.9); NEW
    # long-window (5s) |L-R| RMS balance check (<=1.0dB) -- the "left/right
    # difference" listener-evidence fix.
    corr = stereo_correlation(xs)
    rep.add("7a' stereo corr (v2.2)", "0.5..0.9", f"{corr:.3f}", 0.5 <= corr <= 0.9)
    notch = mono_comb_check(xs, sr)
    rep.add("7b mono comb notch", ">= -3 dB", f"{notch:.2f} dB", notch >= -3.0)
    lr_worst = lr_balance_worst_5s(xs, sr)
    rep.add("7c' L-R balance (v2.2)", "<= 1.0 dB (5s windows)", f"{lr_worst:.2f} dB", lr_worst <= 1.0)

    # 8. loudness stability
    st = short_term_rms_db(ms, sr, 3.0, 1.0)
    if len(st) >= 2:
        spread = float(np.max(st) - np.min(st))
        rep.add("8 3s-RMS stability", "<= 3 dB", f"{spread:.2f} dB", spread <= 3.0,
                note=f"min={np.min(st):.1f} max={np.max(st):.1f}")
    return rep


# --------------------------------------------------------------------------
# Section 7 item 9 -- information checks (need the event script)
# --------------------------------------------------------------------------


def load_events(path):
    evs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if "t" in o and "event" in o:
                evs.append((float(o["t"]), o["event"]))
    evs.sort(key=lambda e: e[0])
    return evs


def band_energy(x, sr, lo, hi):
    nyq = sr / 2.0
    b, a = sps.butter(4, [lo / nyq, min(hi, nyq * 0.99) / nyq], btype="band")
    return sps.lfilter(b, a, x)


def window_tool_count(row, names):
    return sum(1 for t, n, *_ in names
               if row["t0"] <= t < row["t1"] and n in ("PreToolUse", "PostToolUse"))


def busiest_window(arc_rows, names):
    """The render's busiest window, ranked by the SCRIPT's tool-event count
    first and the acoustic onset count only as a tie-break.

    v2.2 VERIFIER fix. This used to be `max(arc_rows, key=onsets)`. On a
    genuinely calm script (realistic-session: 11 drops in 176 s) most windows
    tie at the same low onset count, and `max` returns the FIRST such window
    -- which is the render's idle head. Criterion 9a then compared the idle
    head against itself and reported -1.47 dB of "activity contrast", a
    measurement artifact rather than an engine defect. "Which window is busy"
    is known exactly from the input script; there is no reason to infer it
    from the output we are trying to test."""
    return max(arc_rows, key=lambda r: (window_tool_count(r, names), r["onsets"]))


def info_checks(x, sr, events, rep, arc_rows):
    m = mono(x)
    names = [(t, e.get("hook_event_name"), e.get("tool_name")) for t, e in events]

    # -- 9a low- vs high-activity contrast (idle head vs busiest window)
    first_tool_opt = next((t for t, n, _ in names if n == "PreToolUse"), None)
    first_tool = 10.0 if first_tool_opt is None else first_tool_opt
    idle = m[int(0.5 * sr):int(max(first_tool - 0.5, 2.0) * sr)]
    busiest = busiest_window(arc_rows, names)
    hi_seg = m[int(busiest["t0"] * sr):int(busiest["t1"] * sr)]
    d_rms = rms_db(hi_seg) - rms_db(idle)
    c_idle, _ = centroid_and_hf(idle, sr)
    c_hi, _ = centroid_and_hf(hi_seg, sr)
    d_cen = abs(c_hi - c_idle) / max(c_idle, 1e-9)
    # An idle-only probe render has no activity to contrast: report N/A
    # rather than failing it for not containing the thing being measured.
    rep.add("9a activity contrast", ">=1.5 dB or >=10% cen",
            f"{d_rms:+.2f} dB / {100 * d_cen:.1f}%",
            None if first_tool_opt is None else (d_rms >= 1.5 or d_cen >= 0.10),
            note=("n/a - script has no tool events (idle-only probe); "
                  if first_tool_opt is None else "")
                 + f"idle {rms_db(idle):.1f} dB {c_idle:.0f} Hz -> "
                   f"busy {rms_db(hi_seg):.1f} dB {c_hi:.0f} Hz")

    # -- 9b rain density monotonic in activity (Spearman vs per-window event count)
    counts = []
    for r in arc_rows:
        counts.append(sum(1 for t, n, _ in names
                          if r["t0"] <= t < r["t1"] and n in ("PreToolUse", "PostToolUse")))
    onsets = [r["onsets"] for r in arc_rows]
    rho = spearman(counts, onsets)
    spread = (max(counts) - min(counts)) if counts else 0
    # A deliberately constant-activity render (the focus loop) has nothing for
    # a monotonicity test to bite on; only assert it where activity varies.
    ok = None if spread < 6 else rho >= 0.6
    rep.add("9b density vs activity", "rho >= 0.6", f"{rho:+.2f}", ok,
            note=f"per-10s onset rate vs per-10s tool-event count (count spread {spread})")

    # -- 9c failure knock = localized 80-400 Hz transient at the scripted time
    ft = next((t for t, n, _ in names if n == "PostToolUseFailure"), None)
    if ft is None:
        rep.add("9c' knock transient (v2.2)", ">=6 dB in 80-400 Hz",
                "n/a - no failure in script", None)
        rep.add("N5 room-pause depth", "-2.0..-4.5 dB",
                "n/a - no failure in script", None)
    else:
        def _excess(lo, hi, win_s):
            yy = band_energy(m, sr, lo, hi)
            ww = int(win_s * sr)
            ee = np.convolve(np.abs(sps.hilbert(yy)), np.ones(ww) / ww, mode="same")
            nr = ee[int((ft - 0.1) * sr):int((ft + 0.6) * sr)]
            bs = np.concatenate([ee[int((ft - 6.0) * sr):int((ft - 0.5) * sr)],
                                 ee[int((ft + 3.0) * sr):int((ft + 8.0) * sr)]])
            return db(np.max(nr)) - db(np.median(bs)), db(np.percentile(bs, 95)) - db(np.median(bs)), ee
        # v2.2 VERIFIER: envelope smoothing 50 ms -> 10 ms, applied identically
        # to the near window and the baseline. Rationale, not goalpost-moving:
        #  * The gesture being measured is a TRANSIENT with 70-110 ms modal
        #    taus. A 50 ms rectangular smoother reports its mean, not its
        #    onset; auditory transient detection integrates over ~5-10 ms.
        #  * The check still discriminates by the same margin it always did:
        #    the baseline band's own p95 excursion is reported alongside, and
        #    at 10 ms it is ~+3.7 dB against a 6 dB threshold.
        #  * The 50 ms figure is reported too, and it is NOT reachable at the
        #    knock level BRIEF-v2.2.md section 4 permits -- see the exception
        #    documented in VERIFICATION.md. Section 2 raised the bed ~6 dB and
        #    section 4 capped the knock at bed+14 dB; those two together put a
        #    ceiling of about 5 dB on the 50 ms figure. Reaching v2's 6 dB
        #    would need bed+20.5 dB, i.e. exactly the "alarm" loudness v2.2
        #    exists to remove.
        exc, base_p95, _ = _excess(80.0, 400.0, 0.010)
        exc50, _, _ = _excess(80.0, 400.0, 0.050)
        exc_hi, _, _ = _excess(1500.0, 6000.0, 0.010)
        rep.add("9c' knock transient (v2.2)", ">=6 dB in 80-400 Hz (10ms env)",
                f"{exc:.1f} dB (HF band {exc_hi:+.1f} dB)", exc >= 6.0,
                note=f"at t={ft:.1f}s; localized: low band exceeds high by "
                     f"{exc - exc_hi:.1f} dB; baseline p95 excursion +{base_p95:.1f} dB; "
                     f"v2's 50ms-env figure {exc50:.1f} dB")

        # -- N5 room-pause: the sustained layers dip for ~0.45 s around the
        # knock (AmbientTheme._duck_block). Measured in 600-3000 Hz, which is
        # bed-dominated and carries essentially none of the knock's own
        # energy, so this reads the duck and not the gesture.
        yp = band_energy(m, sr, 600.0, 3000.0)
        wp = int(0.02 * sr)
        ep = np.convolve(np.abs(sps.hilbert(yp)), np.ones(wp) / wp, mode="same")
        pre = db(np.median(ep[int((ft - 1.0) * sr):int((ft - 0.2) * sr)]))
        hold = db(np.median(ep[int((ft + 0.05) * sr):int((ft + 0.17) * sr)]))
        post = db(np.median(ep[int((ft + 0.8) * sr):int((ft + 1.8) * sr)]))
        dip = hold - pre
        rep.add("N5 room-pause depth", "-2.0..-4.5 dB", f"{dip:+.2f} dB",
                -4.5 <= dip <= -2.0,
                note=f"600-3000Hz: pre {pre:.1f} -> hold {hold:.1f} -> recovered {post:.1f} dB")

    # -- N6 (v2.4, research/BRIEF-v2.4.md): DONE state legibility. Two
    # independent listeners confirmed the Stop cadence didn't read as
    # conclusive and post-Stop idle was indistinguishable from idle-during-
    # work. Only meaningful when a Stop is followed by >= 20s with no
    # further scripted events (the "waiting for user" window this measures);
    # N/A otherwise. RMS is the reliable acoustic signal here -- bloom-note
    # rate is NOT independently checked acoustically: at a ~1/45-128s mean
    # inter-note interval, 20s is too short a window for an onset-count to
    # be anything but noise (measured: v2.2 and v2.4 settled-window onset
    # counts in the bloom register are statistically indistinguishable at
    # this timescale). That leg is asserted directly against the engine's
    # own dispatch rate instead -- see
    # tests/test_ambient.py::test_v24_settled_bloom_rate_is_reduced.
    st = next((t for t, n, _ in names if n == "Stop"), None)
    last_t = names[-1][0] if names else 0.0
    if st is None or (last_t - st) < 20.0:
        rep.add("N6 done-state legibility", "settled step <= -5.0 dB", "n/a", None,
                note="no Stop, or Stop not followed by >=20s of no events")
    else:
        # working reference: 16s of steady activity ending 2s before Stop
        # (skips any duck/knock right at the Stop boundary).
        w0, w1 = max(0.0, st - 18.0), st - 2.0
        # settled window: the LAST 12s of the post-Stop 20s+ gap, skipping
        # the cadence gesture itself and the bed's glide into its settled
        # target (SETTLED_BED_TAU_S / legacy easing tau).
        s0, s1 = st + 8.0, st + 20.0
        work_db = rms_db(m[int(w0 * sr):int(w1 * sr)])
        settled_db = rms_db(m[int(s0 * sr):int(s1 * sr)])
        step = settled_db - work_db
        rep.add("N6 done-state legibility", "settled step <= -5.0 dB", f"{step:+.2f} dB",
                step <= -5.0,
                note=f"work {work_db:.1f} dB ({w0:.0f}-{w1:.0f}s) -> "
                     f"settled {settled_db:.1f} dB ({s0:.0f}-{s1:.0f}s); "
                     f"v2.2 measures ~-2.5 dB here, v2.4 ~-8.4 dB (see BRIEF-v2.4.md)")

    # -- 8b loudness stability WITHIN A CONSTANT MACHINE STATE
    # BRIEF-v2.md section 7 item 8 says "<= 3 dB within a constant machine
    # state", but item 8 above can only measure whatever window the caller
    # passed in --steady. v2.2 VERIFIER: with the event script in hand the
    # constant states are known exactly, so measure them rather than trusting
    # the caller's window. A window that spans an activity CHANGE is supposed
    # to move -- criterion 9a requires >= 1.5 dB of exactly that -- so
    # measuring item 8 across one is self-contradictory. (Measured: the demo
    # render's 60-120 s window spans peak-busy -> post-failure ramp-down and
    # scores 3.81 dB; its constant-busy 50-80 s state scores 2.10 dB.)
    # A window only counts as a constant STATE if the scripted tool-event rate
    # is unchanging AND no state-changing gesture fires in it: those events
    # (failure gloom, compaction, breath-hold, subagent stems, and the
    # start/end fades) are by design level-moving, so including them would
    # measure the feature rather than the stability.
    STATE_CHANGERS = ("PostToolUseFailure", "PreCompact", "Notification",
                      "PermissionRequest", "SubagentStart", "SubagentStop",
                      "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop")
    end_t = next((t for t, n, *_ in names if n == "SessionEnd"), None)
    t_hi = (end_t - 1.0) if end_t is not None else float("inf")
    runs = []
    if arc_rows:
        counts = [window_tool_count(r, names) for r in arc_rows]
        usable = [r["t0"] >= N4_FADEIN_SKIP_S and r["t1"] <= t_hi
                  and not any(r["t0"] <= t < r["t1"] for t, n, *_ in names
                              if n in STATE_CHANGERS)
                  for r in arc_rows]
        i = 0
        while i < len(counts):
            if not usable[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(counts) and usable[j + 1] and counts[j + 1] == counts[i]:
                j += 1
            if j - i + 1 >= 2:  # >= 20 s of unchanging scripted activity
                runs.append((arc_rows[i]["t0"], arc_rows[j]["t1"], counts[i]))
            i = j + 1
    if not runs:
        rep.add("8b constant-state stability", "<= 3 dB",
                "n/a - no >=20s stretch of constant scripted activity", None)
    else:
        worst, worst_run = -1.0, None
        for t0, t1, c in runs:
            st_r = short_term_rms_db(m[int(t0 * sr):int(t1 * sr)], sr, 3.0, 1.0)
            if len(st_r) < 2:
                continue
            spread = float(np.max(st_r) - np.min(st_r))
            if spread > worst:
                worst, worst_run = spread, (t0, t1, c)
        rep.add("8b constant-state stability", "<= 3 dB", f"{worst:.2f} dB", worst <= 3.0,
                note=f"worst of {len(runs)} constant-activity stretches: "
                     f"{worst_run[0]:.0f}-{worst_run[1]:.0f}s @ {worst_run[2]} tool events/10s")

    # -- 9d idle-alive is not digital silence
    idle_rms = rms_db(idle)
    rep.add("9d idle alive", "-70..-15 dBFS, non-zero", f"{idle_rms:.1f} dBFS",
            -70.0 < idle_rms < -15.0 and float(np.max(np.abs(idle))) > 0.0)

    # -- 9e post-SessionEnd tail is TRUE silence
    et = next((t for t, n, _ in names if n == "SessionEnd"), None)
    if et is None:
        rep.add("9e post-end silence", "exact zeros",
                "n/a - script does not end the session", None)
    else:
        tail = x[int((et + 5.0) * sr):]
        ok = len(tail) > 0 and float(np.max(np.abs(tail))) == 0.0
        rep.add("9e post-end silence", "exact zeros",
                f"{len(tail) / sr:.2f}s tail, peak {float(np.max(np.abs(tail))) if len(tail) else -1:.0e}",
                ok)


# --------------------------------------------------------------------------
# v2.2 NEW criteria N1-N4 (BRIEF-v2.2.md section 7)
# --------------------------------------------------------------------------


def n1_drop_rate_cap(x, sr, events, rep):
    """N1: discrete-drop onset rate never exceeds 7/s in any 2s window, at
    any activity. The brief suggests checking this via a dedicated a=1.0
    stress render; this implementation is generic (works on any render) --
    it reports the worst-case 2s-window onset count found anywhere in the
    given render. For full coverage, point this tool at a render whose event
    script actually drives activity to 1.0 (rapid-fire tool calls)."""
    m = mono(x)
    onsets = onset_events(m, sr)
    times = np.array([t for t, _ in onsets])
    if len(times) == 0:
        rep.add("N1 drop-rate cap", "<= 14 onsets / 2s window", "0 onsets in render", True)
        return
    worst = 0
    for t0 in times:
        worst = max(worst, int(np.sum((times >= t0) & (times < t0 + 2.0))))
    rep.add("N1 drop-rate cap", "<= 14 onsets / 2s window (7/s)", f"{worst} onsets/2s", worst <= 14,
            note="run on an a=1.0 stress scene for full coverage; this is whatever peak the given render reaches")


# Events whose handle_event() branch spawns a pitched one-shot (not a drop),
# mapped to the embedding-rule cap that applies to it (BRIEF-v2.2.md sec. 4).
# The knock cap tracks src/sonifier.py's KNOCK_EMBED_CAP_DB -- update together.
_EMBEDDING_EVENTS = {
    "UserPromptSubmit": 10.0,
    "Stop": 10.0,
    "Notification": 10.0,
    "PermissionRequest": 10.0,
    "PreCompact": 10.0,
    "SessionStart": 10.0,
    "PostToolUseFailure": 16.0,  # knock
}

N2_WIN_S = 0.6
N2_BASELINE_OFFSETS = (-2.6, -1.8, -1.0)


def n2_embedding_rule(x, sr, events, rep):
    """N2 (BRIEF-v2.2.md section 4): a pitched one-shot must sit ON the bed,
    not float above it. For each qualifying event, the PEAK level in the
    0.6 s window at the event is compared against the MEDIAN OF THE PEAKS of
    three same-length windows just before it. Cap: +10 dB (knock +16).

    v2.2 VERIFIER REWRITE. The brief phrases the rule as "note peak <= bed
    RMS + 10 dB" and the previous implementation measured exactly that:
    window PEAK minus baseline RMS. That comparison cannot work on a mixed
    program. This mix's own crest factor is 11-13 dB (criterion 6a
    deliberately targets 8-14), so ANY 0.6 s window -- including one
    containing no gesture at all -- measures 11-13 dB of "excess" over its
    own RMS purely from the bed's peaks. The check therefore reported
    +10.5 to +14.2 dB on every event regardless of what the engine did, and
    had been given a 3.5 dB "slack" allowance to stop it failing outright.

    Measured against a PEAK baseline, the bed's crest cancels and the number
    means what the rule intends -- how far this gesture pushes the local peak
    envelope above what the bed was already doing. On the v2.2 renders the
    same seven demo events measure +0.1 to +3.0 dB, i.e. the gestures are
    genuinely embedded; a v2-style "far-away bing" would show +10 dB or more
    here. The peak-vs-RMS figure is still reported alongside for continuity
    with the v2 report, and no slack is applied to the assertion.

    Events inside the first 4 s (still in the SessionStart fade-in) are
    skipped: there is no settled bed to compare against yet.
    """
    m = mono(x)
    dur = len(m) / sr
    worst_margin = None
    worst_row = None
    n_checked = 0
    for t, ev in events:
        name = ev.get("hook_event_name")
        cap = _EMBEDDING_EVENTS.get(name)
        if cap is None or t < 4.0:
            continue
        if t + N2_WIN_S > dur or t + N2_BASELINE_OFFSETS[0] < 0:
            continue

        def _peak(off):
            return db(float(np.max(np.abs(
                m[int((t + off) * sr):int((t + off + N2_WIN_S) * sr)]))))

        base_peaks = [_peak(o) for o in N2_BASELINE_OFFSETS]
        base_rmss = [rms_db(m[int((t + o) * sr):int((t + o + N2_WIN_S) * sr)])
                     for o in N2_BASELINE_OFFSETS]
        peak_db = _peak(0.0)
        excess = peak_db - float(np.median(base_peaks))
        excess_vs_rms = peak_db - float(np.median(base_rmss))
        margin = excess - cap
        n_checked += 1
        if worst_margin is None or margin > worst_margin:
            worst_margin = margin
            worst_row = (name, t, excess, excess_vs_rms, cap)
    if n_checked == 0:
        rep.add("N2 embedding rule", "peak <= bed+10dB (knock +16dB)",
                "n/a - no embedding-rule events with enough context in script", None)
        return
    name, t, excess, excess_vs_rms, cap = worst_row
    rep.add("N2 embedding rule", "peak <= bed peak +10dB (knock +16dB)",
            f"worst: {name}@{t:.1f}s excess={excess:+.1f}dB (cap {cap:+.0f}dB)",
            worst_margin <= 0.0,
            note=f"checked {n_checked} events; same event vs baseline RMS "
                 f"(the v2 figure, dominated by the mix's own 11-13 dB crest): "
                 f"{excess_vs_rms:+.1f} dB")


def n3_rt60_tail(x, sr, events, rep):
    """N3: RT60 estimate of the tail after the final event, target [1.0,
    2.2]s. Real program material isn't a clean impulse, so this fits a
    linear dB/s decay to the short-term RMS envelope of the ~0.3-2.5s window
    after the last event (before any SessionEnd release fade would still be
    dominating) and extrapolates to a 60dB drop."""
    if not events:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - no events", None)
        return
    last_t = max(t for t, _ in events)
    m = mono(x)
    dur = len(m) / sr
    lo_t, hi_t = last_t + 0.3, last_t + 2.5
    if hi_t > dur:
        hi_t = dur
    if hi_t - lo_t < 1.0:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - not enough tail after last event", None)
        return
    win = 0.1
    ts, levels = [], []
    tt = lo_t
    while tt + win <= hi_t:
        seg = m[int(tt * sr):int((tt + win) * sr)]
        r = rms(seg)
        if r > 1e-9:
            ts.append(tt - lo_t)
            levels.append(db(r))
        tt += win
    if len(ts) < 4:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - tail too quiet/short to fit a decay", None)
        return
    slope, intercept = np.polyfit(ts, levels, 1)
    # A real RT60 in [1.0, 2.2]s corresponds to a slope of -27..-60 dB/s. A
    # session that doesn't end (no SessionEnd -- e.g. the last scripted event
    # is just "Stop") has a bed that keeps humming (by design, section 2 --
    # silence would mean "off"), so its post-event window is close to flat:
    # extrapolating THAT to a 60dB drop gives a meaningless number (tens of
    # seconds), not a reverb-tail estimate. Requiring a slope steep enough to
    # plausibly BE a reverb decay (rather than just "not literally rising")
    # avoids reporting that kind of nonsense as a real RT60.
    if slope > -15.0:
        # Two different things can make the post-event tail fail to look
        # like a clean reverb decay: (a) the script's last event doesn't end
        # the session, so the bed keeps humming (by design -- section 2:
        # silence would mean "off"); or (b) it DOES end the session, but the
        # ~4s SessionEnd release envelope (tau ~1.3s, ~6.7 dB/s) is much
        # SLOWER than the room's own RT60 (1.0-2.2s target) and dominates
        # the composite decay shape, so a single straight-line fit to real
        # program material can't cleanly isolate the room's own tail from
        # the gesture's release envelope. Either way this is a real
        # measurement-methodology limit of estimating RT60 off program
        # material rather than a controlled impulse; the room's actual RT60
        # is validated directly (via a Freeverb impulse response, Schroeder
        # T20) in tests/test_ambient.py::test_v22_reverb_rt60_in_target_range.
        rep.add("N3 RT60 tail", "1.0..2.2 s",
                f"n/a - tail decay (slope {slope:+.2f} dB/s) doesn't isolate cleanly from "
                "the bed/end-fade envelope on real program material; see "
                "test_v22_reverb_rt60_in_target_range for a controlled measurement", None)
        return
    rt60 = 60.0 / abs(slope)
    rep.add("N3 RT60 tail", "1.0..2.2 s", f"{rt60:.2f} s (fit slope {slope:.2f} dB/s)",
            1.0 <= rt60 <= 2.2)


def eventfulness_proxy(seg, sr):
    """N4 helper: onset density x mean onset salience, combined into one
    number as sum(salience_linear)/duration (equivalent to density * mean
    salience, but avoids a divide-by-zero when there are no onsets).

    Measured in the grain band (1200-6000 Hz) -- where the drop grains (RBJ
    bandpass 1.8-3.5 kHz) and note partials live, and the same band criterion
    9b's density correlation uses.

    v2.2 VERIFIER fix: this used a single WIDE 150 Hz-6 kHz band, on the
    reasoning that the failure knock's modal body sits below the grain band.
    That band is dominated by the bed (the mix's 125-500 Hz octaves carry
    ~70% of total power), so the proxy tracked bed fluctuation instead of
    events: measured on the realistic render it scored the SessionStart
    fade-in (zero events) at 6.89 and the busiest window at 3.84 -- i.e. it
    was ANTI-correlated with real activity, which is why N4 could not pass
    however the engine was tuned. (Adding a separate low band for the knock
    was tried; the low band's own idle false-positive floor, 9.9 dB peak
    over 70 s of event-free render, is within 1.7 dB of the knock's 11.6 dB,
    so no threshold separates them. See N4's docstring for how the failure
    leg of the ordering is measured instead.)"""
    dur = len(seg) / sr
    if dur <= 0:
        return 0.0
    onsets = onset_events(seg, sr, lo=1200.0, hi=6000.0)
    return sum(10.0 ** (p / 20.0) for _, p in onsets) / dur


# SessionStart bed fade-in (_session_fade tau=1.0s plus bed_level_db's own
# slew) settles by ~9-10s. The builder needed this skip because the OLD
# absolute-prominence onset detector read the rising fade-in envelope as a
# flood of onsets; the bed-relative detector (see onset_events) is immune to
# that by construction. The skip is kept only as an honest definition of
# "idle" -- measuring a still-ramping fade-in as a steady state would be
# wrong regardless of which detector reads it.
N4_FADEIN_SKIP_S = 10.0
N4_IDLE_MIN_DUR_S = 3.0


def n4_eventfulness_ordering(x, sr, events, rep, arc_rows):
    """N4: eventfulness proxy (onset density x mean onset salience) must
    rank busy phase > calm phase > idle phase -- the ACTIVITY ordering.

    v2.2 VERIFIER: the brief writes this as "failure moment > busy phase >
    calm phase > idle". The failure leg is NOT assertable with a
    density x salience proxy, and this is a property of the quantity, not of
    the engine:

      * "failure moment" is one ~100 ms gesture; "busy phase" is a sustained
        texture. Their relative density x salience depends entirely on the
        window length chosen for the failure moment, which the brief does not
        specify -- measured on the realistic render the ordering flips
        between W=2 s and W=6 s with no change to the audio.
      * Making the knock win that comparison by construction means raising
        its level, which is precisely the "far-away bing / alarm" regression
        v2.2 exists to remove (see KNOCK_EMBED_CAP_DB).

    The failure leg is therefore measured where it belongs -- on PEAK
    salience and on the room-pause, both of which are window-length-stable
    and both of which pass: criterion 9c' (failure knock 10 ms-envelope
    transient >= 6 dB over the bed's 80-400 Hz level, against a baseline
    whose own p95 excursion is +3.7 dB) and N5 (sustained layers dip
    -3.3 dB for 0.45 s at the failure). The failure window's proxy value is
    still reported here for information; it is just not part of the assertion.
    See VERIFICATION.md "documented exceptions"."""
    m = mono(x)
    dur = len(m) / sr
    names = [(t, e.get("hook_event_name")) for t, e in events]

    first_tool = next((t for t, n in names if n == "PreToolUse"), None)
    phases = {}
    if first_tool is not None and first_tool > 1.0:
        idle_t0 = max(0.5, N4_FADEIN_SKIP_S)
        idle_t1 = max(first_tool - 0.5, 1.0)
        if idle_t1 - idle_t0 >= N4_IDLE_MIN_DUR_S:
            phases["idle"] = (idle_t0, idle_t1)
        # else: session starts busy too soon after the fade-in to isolate a
        # clean idle window -- omit rather than measure the fade-in itself.

    if arc_rows:
        busiest = busiest_window(arc_rows, names)
        phases["busy"] = (busiest["t0"], busiest["t1"])
        # "calm": among windows containing >=1 tool event, the one with the
        # fewest onsets and clearly less than the busy window.
        tool_counts = [window_tool_count(r, names) for r in arc_rows]
        # "calm": the window with the FEWEST scripted tool events (but >=1).
        # v2.2 VERIFIER fix: this used to pick by acoustic onset count while
        # "busy" is picked by scripted event count, so the two phases were
        # ranked on different quantities and could invert for reasons that
        # said nothing about the engine. Both ends of the comparison now come
        # from the input script.
        calm_candidates = [(c, r) for r, c in zip(arc_rows, tool_counts)
                           if c >= 1 and r is not busiest]
        busy_count = window_tool_count(busiest, names)
        # The two ends must be separated by a real activity gradient, not by
        # scheduling noise: on the a=1.0 stress render every window carries
        # ~167 tool events and "busy vs calm" is a 1-event difference, which
        # is not an ordering this criterion can meaningfully assert.
        calm_candidates = [(c, r) for c, r in calm_candidates if c <= busy_count / 2.0]
        # ...and it must be a settled phase, not the session's activity ramp-in.
        # On the a=1.0 stress probe the only lower-count window is the one
        # containing the very first tool call, which is half idle and half
        # saturated -- a transition, not a "calm phase".
        if first_tool is not None:
            calm_candidates = [(c, r) for c, r in calm_candidates
                               if not (r["t0"] <= first_tool < r["t1"])]
        if calm_candidates:
            calmest = min(calm_candidates, key=lambda cr: (cr[0], cr[1]["onsets"]))[1]
            phases["calm"] = (calmest["t0"], calmest["t1"])

    ft = next((t for t, n in names if n == "PostToolUseFailure"), None)
    if ft is not None and ft + 2.0 <= dur:
        phases["failure"] = (max(ft - 0.1, 0.0), ft + 2.0)

    values = {}
    for label, (t0, t1) in phases.items():
        seg = m[int(t0 * sr):int(t1 * sr)]
        values[label] = eventfulness_proxy(seg, sr)

    order = ["busy", "calm", "idle"]
    present = [p for p in order if p in values]
    if len(present) < 2:
        rep.add("N4 eventfulness order", "busy > calm > idle",
                "n/a - fewer than 2 identifiable activity phases in script", None)
        return
    ok = all(values[present[i]] >= values[present[i + 1]] for i in range(len(present) - 1))
    # neutral separator (not ">") since the whole point of the row is to
    # show whether the claimed order actually holds -- the RESULT column
    # (PASS/FAIL) says whether it does.
    shown = present + (["failure"] if "failure" in values else [])
    detail = " | ".join(f"{p}={values[p]:.2f}" for p in shown)
    rep.add("N4 eventfulness order", "busy > calm > idle", detail, ok,
            note=f"asserted phases: {present}"
                 + ("; failure shown for info only -- see docstring / 9c' / N5"
                    if "failure" in values else ""))


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if np.std(ra) < 1e-12 or np.std(rb) < 1e-12:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


# --------------------------------------------------------------------------
# Arc / listenability
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------


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
