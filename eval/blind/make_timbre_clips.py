#!/usr/bin/env python3
"""Blind test round 3: 4 candidate drop timbres, matched loudness, shuffled
to neutral labels P/Q/R/S.

Standalone (numpy only for synthesis; scipy optional, falls back to a pure
python biquad loop for the control's RBJ bandpass -- same fallback pattern
sonifier.py uses). Does NOT import sonifier.py (that module is being
refactored by another agent); parameters for the control clip are cribbed
by hand from sonifier.py's _render_one_drop_variant / _render_knock
(read-only reference, see comments below).

Usage: .venv/bin/python eval/blind/make_timbre_clips.py
"""
import math
import shutil
import subprocess
import wave

import numpy as np

SR = 48000
DUR_S = 25.0
SEED = 20260804

OUT_DIR = __file__.rsplit("/", 1)[0]


# -- shared: sparse Poisson tap schedule --------------------------------------

def make_tap_times(rng, dur, rate=0.5, min_gap=0.3):
    """Poisson-ish tap times at ~rate taps/s with an enforced min gap."""
    times = []
    t = rng.exponential(1.0 / rate)
    while t < dur:
        times.append(t)
        t += max(min_gap, rng.exponential(1.0 / rate))
    return times


# -- shared: light Schroeder reverb (comb + allpass), cribbed conceptually ----
# from sonifier.py's Freeverb section (comb/allpass delay-line reverb), but
# reimplemented standalone/short since that file is read-only reference.

def schroeder_reverb(x, sr, mix=0.12):
    comb_delays_ms = [29.7, 37.1, 41.1, 43.7]
    comb_decay = 0.78
    out = np.zeros_like(x)
    for d_ms in comb_delays_ms:
        d = max(1, int(sr * d_ms / 1000))
        buf = np.zeros(len(x) + d)
        buf[d:] = x
        y = np.zeros_like(buf)
        for i in range(d, len(buf)):
            y[i] = buf[i] + comb_decay * y[i - d]
        out += y[d:d + len(x)]
    out /= len(comb_delays_ms)
    # single allpass for diffusion
    d = int(sr * 5.0 / 1000)
    g = 0.5
    buf = np.zeros(len(out) + d)
    buf[d:] = out
    y = np.zeros_like(buf)
    for i in range(d, len(buf)):
        y[i] = -g * buf[i] + buf[i - d] + g * y[i - d]
    wet = y[d:d + len(x)]
    return x + mix * wet


def rbj_bandpass(x, center, q, sr):
    """RBJ constant-skirt bandpass biquad -- same math as sonifier.py's
    _rbj_bandpass (read-only reference), reimplemented standalone here."""
    w0 = 2 * np.pi * center / sr
    alpha = np.sin(w0) / (2.0 * max(q, 0.5))
    cosw0 = np.cos(w0)
    b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1 + alpha, -2 * cosw0, 1 - alpha
    b = [b0 / a0, b1 / a0, b2 / a0]
    a = [1.0, a1 / a0, a2 / a0]
    try:
        from scipy.signal import lfilter
        return lfilter(b, a, x)
    except ImportError:
        y = np.zeros_like(x)
        x1 = x2 = y1 = y2 = 0.0
        for i in range(len(x)):
            x0 = x[i]
            y0 = b[0] * x0 + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
            y[i] = y0
            x2, x1 = x1, x0
            y2, y1 = y1, y0
        return y


def raised_cosine_attack(n):
    if n <= 0:
        return np.ones(max(n, 0))
    return 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)


# -- candidate A: damped woodblock (modal synthesis) --------------------------

def render_woodblock(rng, velocity=0.7, sr=SR):
    """2-3 exponentially-decaying inharmonic modes, fundamental 800-1200 Hz,
    decay < 150 ms. Same modal-synthesis idea as sonifier.py's _render_knock
    (inharmonic mode ratios + noise contact transient) but pitched up an
    octave-plus into the "woodblock tick" range per the brief, read-only
    reference not copied verbatim."""
    f0 = rng.uniform(800.0, 1200.0)
    ratios = [1.0, 1.47, 2.09]
    amps = [1.0, 0.45, 0.22]
    tau = rng.uniform(0.05, 0.15)
    dur = min(0.3, 6 * tau)
    n = max(int(0.01 * sr), int(dur * sr))
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for i, (r, a) in enumerate(zip(ratios, amps)):
        phase = 0.4 * i  # decorrelate mode phases (avoid coherent peak sum)
        sig += a * np.sin(2 * np.pi * f0 * r * t + phase) * np.exp(-t / tau)
    # brief contact transient: tiny noise burst at onset, tightly damped
    transient_n = min(n, int(0.003 * sr))
    transient = rng.standard_normal(transient_n) * np.exp(-np.arange(transient_n) / sr / 0.0015)
    sig[:transient_n] += 0.15 * transient
    attack_n = min(n, max(1, int(0.001 * sr)))
    env = np.ones(n)
    env[:attack_n] = raised_cosine_attack(attack_n)
    sig *= env * (0.5 + 0.6 * velocity)
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


# -- candidate B: muted marimba / soft mallet ----------------------------------

def render_marimba(rng, velocity=0.7, sr=SR):
    """Fundamental 400-700 Hz, longer decay than the woodblock, darker
    (fewer/quieter high modes), softened attack (short raised-cosine ramp
    instead of an instant onset -- this is what "mutes" the mallet)."""
    f0 = rng.uniform(400.0, 700.0)
    ratios = [1.0, 2.76, 5.4]  # bar-like inharmonic partials, marimba-ish
    amps = [1.0, 0.28, 0.10]
    tau = rng.uniform(0.18, 0.32)
    dur = min(0.5, 6 * tau)
    n = max(int(0.02 * sr), int(dur * sr))
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for i, (r, a) in enumerate(zip(ratios, amps)):
        phase = 0.6 * i
        sig += a * np.sin(2 * np.pi * f0 * r * t + phase) * np.exp(-t / tau)
    attack_n = min(n, max(1, int(0.006 * sr)))  # soft mallet attack
    env = np.ones(n)
    env[:attack_n] = raised_cosine_attack(attack_n)
    sig *= env * (0.5 + 0.6 * velocity)
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


# -- candidate C: water-drop "plink" (no chirp) --------------------------------

def render_plink(rng, velocity=0.7, sr=SR):
    """Single decaying sine + small (<5%) downward pitch envelope -- NOT a
    sweep/chirp, just enough pitch droop to read as a drop of liquid rather
    than a steady tone -- plus a quiet damped body resonance an octave down."""
    f0 = rng.uniform(1000.0, 1600.0)
    tau = rng.uniform(0.08, 0.14)
    dur = min(0.25, 6 * tau)
    n = max(int(0.01 * sr), int(dur * sr))
    t = np.arange(n) / sr
    pitch_drop = rng.uniform(0.02, 0.045)  # <5% downward, per brief
    inst_freq = f0 * (1.0 - pitch_drop * (1.0 - np.exp(-t / (tau * 0.6))))
    phase = 2 * np.pi * np.cumsum(inst_freq) / sr
    sig = np.sin(phase) * np.exp(-t / tau)
    body = 0.25 * np.sin(2 * np.pi * (f0 / 2.0) * t) * np.exp(-t / (tau * 1.6))
    sig = sig + body
    attack_n = min(n, max(1, int(0.0008 * sr)))
    env = np.ones(n)
    env[:attack_n] = raised_cosine_attack(attack_n)
    sig *= env * (0.5 + 0.6 * velocity)
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


# -- candidate D: CONTROL, v2.2 noise tick (crib from sonifier.py) ------------

def render_noise_tick(rng, velocity=0.7, sr=SR):
    """Control: reproduces sonifier.py's _render_one_drop_variant verbatim
    parameters (read-only reference) -- 4-10ms white noise burst through an
    RBJ bandpass 1.8-3.5kHz Q 2-4, sharp exp decay tau 1.5-4ms. velocity is
    accepted for a uniform call signature but this grain has no velocity
    dependence in the original either."""
    dur = rng.uniform(0.004, 0.010)
    n = max(4, int(dur * sr))
    noise = rng.standard_normal(n)
    center = 10.0 ** rng.uniform(math.log10(1800.0), math.log10(3500.0))
    q = rng.uniform(2.0, 4.0)
    sig = rbj_bandpass(noise, center, q, sr)
    tau = rng.uniform(0.0015, 0.004)
    env = np.exp(-np.arange(n) / sr / tau)
    sig = sig * env
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


TIMBRES = {
    "woodblock": render_woodblock,
    "marimba": render_marimba,
    "plink": render_plink,
    "noise_tick_control": render_noise_tick,
}


def render_clip(name, render_fn, rng, dur=DUR_S, sr=SR):
    n_total = int(dur * sr)
    out = np.zeros(n_total)
    tap_times = make_tap_times(rng, dur)
    for tt in tap_times:
        velocity = float(np.clip(rng.normal(0.7, 0.15), 0.3, 1.0))
        grain = render_fn(rng, velocity, sr)
        start = int(tt * sr)
        end = min(n_total, start + len(grain))
        out[start:end] += grain[: end - start]
    out = schroeder_reverb(out, sr, mix=0.12)
    return out


def normalize_rms(x, target_rms):
    cur = float(np.sqrt(np.mean(x ** 2))) + 1e-12
    return x * (target_rms / cur)


def spectral_centroid(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    return float(np.sum(freqs * spec) / (np.sum(spec) + 1e-12))


def write_wav(path, x, sr):
    x = np.clip(x, -1.0, 1.0)
    pcm = (x * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def encode(path_wav, path_mp3):
    have_ffmpeg = shutil.which("ffmpeg") is not None
    if have_ffmpeg:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", path_wav,
             "-codec:a", "libmp3lame", "-q:a", "2", path_mp3],
            check=True,
        )
        return path_mp3
    return path_wav


def main():
    rng_master = np.random.default_rng(SEED)

    clips = {}
    for name, fn in TIMBRES.items():
        rng = np.random.default_rng(rng_master.integers(0, 2**32 - 1))
        clips[name] = render_clip(name, fn, rng)

    # loudness match: normalize every clip to the same target RMS
    target_rms = 0.05
    for name in clips:
        clips[name] = normalize_rms(clips[name], target_rms)

    # shuffle-assign to neutral labels
    labels = ["P", "Q", "R", "S"]
    names = list(TIMBRES.keys())
    shuffle_rng = np.random.default_rng(SEED)
    shuffled = names.copy()
    shuffle_rng.shuffle(shuffled)
    mapping = dict(zip(labels, shuffled))

    report_rows = []
    for label, name in mapping.items():
        x = clips[name]
        wav_path = f"{OUT_DIR}/{label}.wav"
        mp3_path = f"{OUT_DIR}/{label}.mp3"
        write_wav(wav_path, x, SR)
        final_path = encode(wav_path, mp3_path)
        if final_path == mp3_path:
            import os
            os.remove(wav_path)
        rms = float(np.sqrt(np.mean(x ** 2)))
        rms_db = 20 * math.log10(rms + 1e-12)
        centroid = spectral_centroid(x, SR)
        dur = len(x) / SR
        report_rows.append((label, name, final_path, dur, rms, rms_db, centroid))

    with open(f"{OUT_DIR}/answer-key-round3.txt", "w") as f:
        for label, name in mapping.items():
            f.write(f"{label} = {name}\n")

    print(f"{'Label':6}{'Timbre':22}{'File':10}{'Dur(s)':9}{'RMS':10}{'RMS(dB)':10}{'Centroid(Hz)':14}")
    for label, name, path, dur, rms, rms_db, centroid in sorted(report_rows):
        fname = path.rsplit("/", 1)[-1]
        print(f"{label:6}{name:22}{fname:10}{dur:<9.2f}{rms:<10.5f}{rms_db:<10.2f}{centroid:<14.1f}")

    rms_dbs = [r[5] for r in report_rows]
    spread = max(rms_dbs) - min(rms_dbs)
    print(f"\nRMS spread across clips: {spread:.3f} dB (target: within 1 dB)")


if __name__ == "__main__":
    main()
