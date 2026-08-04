"""Section 7 acceptance battery -- split out of analyze_render.py."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal as sps

from analysis.io_utils import mono, rms, rms_db
from analysis.modulation import (
    crest_windows, env_mod_coherent, env_mod_depth, lr_balance_worst_5s,
    mono_comb_check, short_term_rms_db, stereo_correlation,
)
from analysis.spectral import centroid_and_hf, spectral_flatness, spectral_slope, brightness_ratio, tonal_prominence


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
