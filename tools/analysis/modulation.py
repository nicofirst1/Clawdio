"""Envelope modulation, crest/level, stereo, and onset-detection helpers --
split out of analyze_render.py."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sps

from analysis.io_utils import mono, rms, rms_db
from analysis.spectral import welch_psd


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
