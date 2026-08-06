#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy", "scipy"]
# ///
"""complaint_checks.py -- listener-complaint regression suite for Clawdio.

The v2 blind listener (earphones, no context) said, verbatim:

    "confusing; a little anxiety; like a dark cave; can't tell if birds or
     drops; a far-away bing puts me under pressure; white noise + birds
     confusing; left/right difference; not regular; maybe under the sea;
     feels isolated, confused, lost."  ... and of the demo: "too fast,
     losing control."

BRIEF-v2.2.md turns those into design changes. This tool turns them into
MEASUREMENTS, so a future re-tune cannot silently reintroduce one of them
while still passing the section-7 acceptance battery. Every check below is
named after the phrase it exists to prevent.

Run it on the render the listener will actually hear:

    python3 tools/complaint_checks.py realistic-pace-v22.wav \\
        --events demos/realistic-session.jsonl

Exit code 0 if every check that ran passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
from scipy import signal as sps

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from analyze_render import (  # noqa: E402
    Report, centroid_and_hf, db, load_events, load_wav, lr_balance_worst_5s,
    mono, onset_events, rms_db, short_term_rms_db, stereo_correlation,
)

# Optional: importing the engine lets the "cave" check measure the room's
# RT60 off a real impulse response instead of guessing it from program
# material (which is not separable -- see analyze_render.n3_rt60_tail).
try:
    import sonifier as _engine
except Exception:  # pragma: no cover - engine import is best-effort
    _engine = None


# --------------------------------------------------------------------------
# thresholds -- every one of these traces to a listener phrase
# --------------------------------------------------------------------------

CAVE_RT60_MAX_S = 2.2          # BRIEF-v2.2 section 7 N3
CAVE_CENTROID_MIN_HZ = 350.0   # BRIEF-v2.2 section 7 item 2'
CAVE_BED_PRESENCE_MIN_DB = -40.0   # quietest 5 s while the session is alive
# Calibrated against a POSITIVE CONTROL: the same render re-generated with
# v2-style downward sine-chirp grains substituted for v2.2's noise ticks.
#   grain bank, 168 grains/12 seeds: v2.2 min 0.107 med 0.245  |  v2 chirp 0.0000
#   in-mix, vs local bed:       v2.2 median ratio 1.44      |  v2 chirp 0.43
#   in-mix ridge correlation:   v2.2 min r = -0.76          |  v2 chirp -0.92
BIRDS_BANK_FLATNESS_MIN = 0.06     # grain bank measured directly; the v2.2
                                   # minimum over 12 seeds is 0.107, a chirp is 0.000
BIRDS_FLATNESS_RATIO_MIN = 0.70    # in-mix, median vs the local bed's own flatness
BIRDS_CHIRP_MIN_R = -0.85          # ridge Pearson r; a real chirp sweeps monotonically
BING_EMBED_CAP_DB = 10.0
BING_LONELY_BED_MIN_DB = -40.0
LR_MAX_DB = 1.0
CORR_RANGE = (0.5, 0.9)
FAST_DROP_RATE_CAP_PER_S = 7.0
REGULAR_STABILITY_MAX_DB = 2.5


# --------------------------------------------------------------------------
# "like a dark cave" / "maybe under the sea" / "feels isolated"
# --------------------------------------------------------------------------


def check_cave(x, sr, rep, steady, events):
    """Three independent ways a mix reads as a cave: too much tail, too
    little brightness, or too little bed under the events."""
    m = mono(x)

    # -- RT60, measured off the engine's own reverb impulse response
    # (Schroeder backward integration, T20 extrapolated). This is the only
    # honest way to get RT60: on program material the room tail and the
    # gesture release envelopes are not separable.
    if _engine is None:
        rep.add("cave/RT60", f"<= {CAVE_RT60_MAX_S} s",
                "n/a - engine not importable", None)
    else:
        fv = _engine.Freeverb(sr)
        block = getattr(_engine, "BLOCKSIZE", 256)
        nb = int(math.ceil(8.0 * sr / block))
        imp = np.zeros(block)
        imp[0] = 1.0
        sil = np.zeros(block)
        ir = np.concatenate([fv.process_block(imp if b == 0 else sil)
                             for b in range(nb)], axis=0).mean(axis=1)
        energy = np.asarray(ir, dtype=np.float64) ** 2
        cum = np.cumsum(energy[::-1])[::-1]
        cum_db = 10.0 * np.log10(cum / (cum[0] + 1e-30) + 1e-30)
        t5 = int(np.argmax(cum_db <= -5.0)) / sr
        t25 = int(np.argmax(cum_db <= -25.0)) / sr
        rt60 = (t25 - t5) * 3.0
        rep.add("cave/RT60", f"<= {CAVE_RT60_MAX_S} s", f"{rt60:.2f} s",
                rt60 <= CAVE_RT60_MAX_S,
                note="Freeverb impulse response, Schroeder T20 x3")

    # -- brightness
    seg = m[int(steady[0] * sr):int(steady[1] * sr)] if steady else m
    cen, _ = centroid_and_hf(seg, sr)
    rep.add("cave/centroid", f">= {CAVE_CENTROID_MIN_HZ:.0f} Hz", f"{cen:.0f} Hz",
            cen >= CAVE_CENTROID_MIN_HZ,
            note=f"steady window {steady[0]:.0f}-{steady[1]:.0f}s" if steady else "whole render")

    # -- bed presence: the quietest 5 s WHILE THE SESSION IS ALIVE must still
    # be clearly there. "Isolated / lost" was a void with occasional sounds;
    # a continuously humming warm engine is the control anchor (section 2).
    t0, t1 = _alive_span(m, sr, events)
    if t1 - t0 < 6.0:
        rep.add("cave/bed presence", f">= {CAVE_BED_PRESENCE_MIN_DB} dBFS",
                "n/a - alive span shorter than 6 s", None)
    else:
        win = int(5.0 * sr)
        hop = int(1.0 * sr)
        alive = m[int(t0 * sr):int(t1 * sr)]
        levels = [rms_db(alive[s:s + win]) for s in range(0, len(alive) - win + 1, hop)]
        quietest = min(levels) if levels else -120.0
        rep.add("cave/bed presence", f">= {CAVE_BED_PRESENCE_MIN_DB} dBFS",
                f"{quietest:.1f} dBFS", quietest >= CAVE_BED_PRESENCE_MIN_DB,
                note=f"quietest 5 s inside the alive span {t0:.0f}-{t1:.0f}s")


def _alive_span(m, sr, events):
    """[start, end] of the session-is-running part of the render: after the
    SessionStart fade has settled, before any SessionEnd release begins."""
    dur = len(m) / sr
    t0 = 10.0
    t1 = dur
    if events:
        end_t = next((t for t, e in events
                      if e.get("hook_event_name") == "SessionEnd"), None)
        if end_t is not None:
            t1 = min(t1, end_t - 0.5)
    return t0, max(t0, t1)


# --------------------------------------------------------------------------
# "can't tell if birds or drops" / "white noise + birds confusing"
# --------------------------------------------------------------------------


def _isolated_onsets(m, sr, min_gap_s=1.0):
    ons = [t for t, _ in onset_events(m, sr)]
    out = []
    for i, t in enumerate(ons):
        prev_ok = i == 0 or (t - ons[i - 1]) >= min_gap_s
        next_ok = i == len(ons) - 1 or (ons[i + 1] - t) >= min_gap_s
        if prev_ok and next_ok:
            out.append(t)
    return out


def _spectral_flatness(seg, sr, lo=800.0, hi=8000.0):
    """Wiener entropy (geometric/arithmetic mean of the power spectrum) in
    the drop's band. A sine chirp concentrates into one bin -> ~0.0;
    filtered noise spreads -> well above 0."""
    w = np.hanning(len(seg))
    p = np.abs(np.fft.rfft(seg * w)) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    band = (f >= lo) & (f <= hi)
    p = p[band]
    p = p[p > 0]
    if len(p) < 8:
        return 0.0
    return float(np.exp(np.mean(np.log(p))) / np.mean(p))


def _chirp_slope(seg, sr):
    """Hz/s slope of the spectral-peak ridge across the grain. v2's drops
    were DOWNWARD sine chirps -- the bird-call signature -- which show a
    large negative slope here. A noise tick's ridge is stationary/noisy."""
    nper = 128
    f, t, Z = sps.spectrogram(seg, fs=sr, nperseg=nper, noverlap=nper - 16,
                              window="hann", mode="magnitude")
    band = (f >= 500.0) & (f <= 8000.0)
    f, Z = f[band], Z[band]
    if Z.shape[1] < 4:
        return 0.0
    # only frames carrying real energy (the grain is 4-10 ms long)
    frame_e = Z.sum(axis=0)
    keep = frame_e > 0.25 * frame_e.max()
    if keep.sum() < 3:
        return 0.0
    ridge = f[np.argmax(Z[:, keep], axis=0)]
    tt = t[keep]
    if np.ptp(tt) <= 0:
        return 0.0
    slope, _ = np.polyfit(tt, ridge, 1)
    return float(slope)


def _ridge_r(seg, sr):
    """Pearson r of the spectral-peak ridge against time. A downward sine
    chirp sweeps monotonically and scores near -1; a noise tick's ridge
    wanders and scores near 0. This is the discriminating quantity -- the
    raw Hz/s SLOPE is not, because a 10 ms noise grain's ridge jumps by
    kilohertz between frames and can show a larger |slope| than a real
    chirp while being completely non-monotonic."""
    nper = 128
    f, t, Z = sps.spectrogram(seg, fs=sr, nperseg=nper, noverlap=nper - 16,
                              window="hann", mode="magnitude")
    band = (f >= 500.0) & (f <= 8000.0)
    f, Z = f[band], Z[band]
    if Z.shape[1] < 4:
        return 0.0
    frame_e = Z.sum(axis=0)
    keep = frame_e > 0.25 * frame_e.max()
    if keep.sum() < 3:
        return 0.0
    ridge = f[np.argmax(Z[:, keep], axis=0)]
    tt = t[keep]
    if np.std(ridge) < 1e-9 or np.std(tt) < 1e-9:
        return 0.0
    return float(np.corrcoef(tt, ridge)[0, 1])


def check_birds(x, sr, rep, seed=0):
    """Is a rain tap distinguishable from a bird call?

    The complaint (v2: "birds or drops?") is about two DIFFERENT failure
    modes that both read as wildlife: (a) a downward sine chirp (v2's own
    drop timbre -- a literal bird-call signature), and (b) broadband noise
    with no identity of its own reading as generic "hiss/chaos" (round-2/3
    blind evidence against v2.2's noise-tick default). v2.2 fixed (a) by
    making every drop broadband noise, which is why this check's "grain is
    noise" / "in-mix noise-not-tone" legs assert HIGH spectral flatness --
    that was the right invariant for THAT timbre. v2.3's default (a damped
    modal click, tonal by design -- see docs/research/BRIEF-v2.3.md) fixes (b)
    by moving the other direction, so those two legs would fail it for
    being exactly what it's supposed to be. The invariant that actually
    traces to the complaint, for ANY timbre, is "not a downward chirp"
    (leg 3, ridge r) plus -- for a tonal timbre -- "the pitch is a clean
    held/decaying tone, not a swept one" (checked the same way). The
    flatness legs only apply while the shipped default is the noise timbre;
    they report N/A (not a silent pass) for tonal timbres rather than being
    skipped outright, so a future re-tune back to a noise timbre is still
    caught if it regresses.
    """
    drop_timbre = getattr(getattr(_engine, "AMBIENT_CONFIG", None), "drop_timbre", "noise") \
        if _engine is not None else "noise"
    is_noise_timbre = drop_timbre == "noise"

    # -- leg 1: the grain bank itself
    if _engine is None:
        rep.add("birds/grain is noise", f"flatness >= {BIRDS_BANK_FLATNESS_MIN}",
                "n/a - engine not importable", None)
    elif not is_noise_timbre:
        rep.add("birds/grain is noise", f"flatness >= {BIRDS_BANK_FLATNESS_MIN}",
                f"n/a - drop_timbre={drop_timbre!r} is tonal by design", None,
                note="only applies to the noise-tick timbre; see check_birds docstring")
    else:
        rng = np.random.default_rng(seed)
        bank = _engine._build_drop_bank(rng, sr, timbre=drop_timbre)
        flats = []
        for g in bank:
            w = np.zeros(int(0.016 * sr))
            w[:min(len(g), len(w))] = g[:len(w)]
            flats.append(_spectral_flatness(w, sr))
        worst = float(np.min(flats))
        rep.add("birds/grain is noise", f"flatness >= {BIRDS_BANK_FLATNESS_MIN}",
                f"{worst:.3f} (worst of {len(flats)} bank grains)",
                worst >= BIRDS_BANK_FLATNESS_MIN,
                note=f"median {np.median(flats):.3f}; a v2 downward sine chirp measures 0.000")

    # -- legs 2/3: in the render
    m = mono(x)
    times = _isolated_onsets(m, sr)
    ratios, rs = [], []
    for t in times:
        i0, i1 = int((t - 0.002) * sr), int((t + 0.014) * sr)
        b0, b1 = int((t - 0.8) * sr), int((t - 0.784) * sr)
        if i0 < 0 or b0 < 0 or i1 > len(m):
            continue
        seg = m[i0:i1]
        bed_flat = _spectral_flatness(m[b0:b1], sr)
        ratios.append(_spectral_flatness(seg, sr) / max(bed_flat, 1e-9))
        rs.append(_ridge_r(seg, sr))
    if not ratios:
        rep.add("birds/in-mix noise-not-tone", f"median ratio >= {BIRDS_FLATNESS_RATIO_MIN}",
                "n/a - no isolated onsets in render", None)
        rep.add("birds/no-downward-chirp", f"ridge r >= {BIRDS_CHIRP_MIN_R}",
                "n/a - no isolated onsets in render", None)
        return
    if is_noise_timbre:
        med_ratio = float(np.median(ratios))
        rep.add("birds/in-mix noise-not-tone", f"median ratio >= {BIRDS_FLATNESS_RATIO_MIN}",
                f"{med_ratio:.2f} (n={len(ratios)})", med_ratio >= BIRDS_FLATNESS_RATIO_MIN,
                note="onset-window spectral flatness / local bed flatness; "
                     "v2-chirp positive control measures 0.43")
    else:
        med_ratio = float(np.median(ratios))
        rep.add("birds/in-mix noise-not-tone", f"median ratio >= {BIRDS_FLATNESS_RATIO_MIN}",
                f"n/a - drop_timbre={drop_timbre!r} is tonal by design ({med_ratio:.2f})", None,
                note="only applies to the noise-tick timbre; see check_birds docstring")
    worst_r = float(np.min(rs))
    rep.add("birds/no-downward-chirp", f"ridge r >= {BIRDS_CHIRP_MIN_R}",
            f"{worst_r:+.2f} (most monotone of {len(rs)})", worst_r >= BIRDS_CHIRP_MIN_R,
            note=f"median {np.median(rs):+.2f}; v2-chirp positive control reaches -0.92")


# --------------------------------------------------------------------------
# "a far-away bing puts me under pressure"
# --------------------------------------------------------------------------

_PITCHED_ONE_SHOTS = ("UserPromptSubmit", "Stop", "Notification",
                      "PermissionRequest", "PreCompact", "SessionStart")


def check_bing(x, sr, rep, events):
    """Two conditions, both required. The gesture must not stick out of the
    local peak envelope (embedding), AND it must never fire over a bed that
    has gone quiet -- "far-away bing" is as much about the silence around it
    as about its own level."""
    if not events:
        rep.add("bing/embedded", f"<= bed peak +{BING_EMBED_CAP_DB:.0f} dB",
                "n/a - no event script", None)
        rep.add("bing/never-lonely", f"bed >= {BING_LONELY_BED_MIN_DB} dBFS",
                "n/a - no event script", None)
        return
    m = mono(x)
    dur = len(m) / sr
    worst_exc, worst_exc_row = None, None
    worst_bed, worst_bed_row = None, None
    n = 0
    for t, ev in events:
        name = ev.get("hook_event_name")
        if name not in _PITCHED_ONE_SHOTS or t < 4.0 or t + 0.6 > dur or t < 2.6:
            continue
        pk = lambda o: db(float(np.max(np.abs(  # noqa: E731
            m[int((t + o) * sr):int((t + o + 0.6) * sr)]))))
        base = float(np.median([pk(o) for o in (-2.6, -1.8, -1.0)]))
        exc = pk(0.0) - base
        bed = float(np.median([rms_db(m[int((t + o) * sr):int((t + o + 0.6) * sr)])
                               for o in (-2.6, -1.8, -1.0)]))
        n += 1
        if worst_exc is None or exc > worst_exc:
            worst_exc, worst_exc_row = exc, (name, t)
        if worst_bed is None or bed < worst_bed:
            worst_bed, worst_bed_row = bed, (name, t)
    if n == 0:
        rep.add("bing/embedded", f"<= bed peak +{BING_EMBED_CAP_DB:.0f} dB",
                "n/a - no pitched one-shots with enough context", None)
        rep.add("bing/never-lonely", f"bed >= {BING_LONELY_BED_MIN_DB} dBFS",
                "n/a - no pitched one-shots with enough context", None)
        return
    rep.add("bing/embedded", f"<= bed peak +{BING_EMBED_CAP_DB:.0f} dB",
            f"{worst_exc:+.1f} dB ({worst_exc_row[0]}@{worst_exc_row[1]:.0f}s)",
            worst_exc <= BING_EMBED_CAP_DB, note=f"checked {n} gestures")
    rep.add("bing/never-lonely", f"bed >= {BING_LONELY_BED_MIN_DB} dBFS",
            f"{worst_bed:.1f} dBFS ({worst_bed_row[0]}@{worst_bed_row[1]:.0f}s)",
            worst_bed >= BING_LONELY_BED_MIN_DB,
            note="bed level under the quietest-bedded pitched gesture")


# --------------------------------------------------------------------------
# "left/right difference"
# --------------------------------------------------------------------------


def check_lr(x, sr, rep):
    worst = lr_balance_worst_5s(x, sr)
    rep.add("L-R/balance", f"<= {LR_MAX_DB} dB per 5 s", f"{worst:.2f} dB",
            worst <= LR_MAX_DB)
    corr = stereo_correlation(x)
    rep.add("L-R/correlation", f"{CORR_RANGE[0]}..{CORR_RANGE[1]}", f"{corr:.3f}",
            CORR_RANGE[0] <= corr <= CORR_RANGE[1],
            note="below the range = a wash so wide it splits; above = mono-collapsed")


# --------------------------------------------------------------------------
# "too fast, losing control"
# --------------------------------------------------------------------------


def check_too_fast(x, sr, rep):
    """Two legs: the render never exceeds the rate cap, and the rate MAP in
    the engine is compressive (BRIEF-v2.2 section 1) rather than the
    expansive a**1.3 that produced the complaint."""
    m = mono(x)
    times = np.array([t for t, _ in onset_events(m, sr)])
    if len(times) == 0:
        rep.add("too-fast/rate cap", f"<= {FAST_DROP_RATE_CAP_PER_S:.0f}/s in any 2 s",
                "0 onsets", True)
    else:
        worst = max(int(np.sum((times >= t0) & (times < t0 + 2.0))) for t0 in times)
        rep.add("too-fast/rate cap", f"<= {FAST_DROP_RATE_CAP_PER_S:.0f}/s in any 2 s",
                f"{worst / 2.0:.1f}/s ({worst} onsets/2 s)",
                worst <= 2 * FAST_DROP_RATE_CAP_PER_S)

    if _engine is None or not hasattr(_engine, "_drop_rate_from_activity"):
        rep.add("too-fast/compressive map", "concave, capped", "n/a - engine not importable",
                None)
        return
    f = _engine._drop_rate_from_activity
    a = np.linspace(0.0, 1.0, 101)
    r = np.array([f(v) for v in a])
    # concave (compressive): every second difference <= 0, within fp slop
    d2 = np.diff(r, 2)
    concave = bool(np.all(d2 <= 1e-9))
    monotone = bool(np.all(np.diff(r) >= -1e-9))
    capped = float(r.max()) <= FAST_DROP_RATE_CAP_PER_S + 1e-9
    # half-activity should already deliver most of the range -- that is what
    # "compressive" buys: the loud end is not where all the resolution is.
    half_frac = (f(0.5) - r[0]) / max(r[-1] - r[0], 1e-9)
    rep.add("too-fast/compressive map", "concave, monotone, cap <= 6/s",
            f"max {r.max():.2f}/s, r(0.5)={f(0.5):.2f}/s = {100 * half_frac:.0f}% of range",
            concave and monotone and capped,
            note=f"concave={concave} monotone={monotone} capped={capped}; "
                 f"v2's expansive a**1.3 map reached ~40/s")


# --------------------------------------------------------------------------
# "not regular" / "lost"
# --------------------------------------------------------------------------


def check_regular(x, sr, rep, steady):
    m = mono(x)
    if steady:
        seg = m[int(steady[0] * sr):int(steady[1] * sr)]
        where = f"steady window {steady[0]:.0f}-{steady[1]:.0f}s"
    else:
        seg = m
        where = "whole render"
    st = short_term_rms_db(seg, sr, 3.0, 1.0)
    spread = float(np.max(st) - np.min(st)) if len(st) >= 2 else 0.0
    rep.add("not-regular/bed stability", f"<= {REGULAR_STABILITY_MAX_DB} dB (3 s RMS)",
            f"{spread:.2f} dB", spread <= REGULAR_STABILITY_MAX_DB,
            note=f"{where}; min {np.min(st):.1f} max {np.max(st):.1f} dBFS")


# --------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--events", default=None, help="event jsonl for the render")
    ap.add_argument("--steady", default=None,
                    help="A:B seconds -- a constant-activity window (defaults to "
                         "the middle third of the render)")
    args = ap.parse_args(argv)

    x, sr = load_wav(args.wav)
    dur = len(x) / sr
    if args.steady:
        a, b = args.steady.split(":")
        steady = (float(a), float(b))
    else:
        steady = (dur / 3.0, 2.0 * dur / 3.0)
    events = load_events(args.events) if args.events else []

    print(f"# complaint suite: {args.wav}  {dur:.2f}s  {sr} Hz  "
          f"peak={db(np.max(np.abs(x))):.2f} dBFS  rms={rms_db(mono(x)):.2f} dBFS")
    rep = Report()
    check_cave(x, sr, rep, steady, events)
    check_birds(x, sr, rep)
    check_bing(x, sr, rep, events)
    check_lr(x, sr, rep)
    check_too_fast(x, sr, rep)
    check_regular(x, sr, rep, steady)
    print(rep.render())
    return 0 if rep.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
