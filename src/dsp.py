"""DSP: Slew, chime synthesis (render_*_chime, build_chime_bank), click
grain synthesis. """

from __future__ import annotations

import math

import numpy as np

from config import SAMPLE_RATE, SLEW_TAU


# --------------------------------------------------------------------------
# One-pole smoothing helper
# --------------------------------------------------------------------------

class Slew:
    """One-pole lag ('slew') toward a target value, tau in seconds."""

    __slots__ = ("value", "target", "tau")

    def __init__(self, value=0.0, tau=SLEW_TAU):
        self.value = float(value)
        self.target = float(value)
        self.tau = float(tau)

    def step(self, dt):
        if self.tau <= 0:
            self.value = self.target
        else:
            coeff = 1.0 - math.exp(-dt / self.tau)
            self.value += (self.target - self.value) * coeff
        return self.value


# --------------------------------------------------------------------------
# Chime synthesis (pre-rendered at startup)
# --------------------------------------------------------------------------

def _env_adsr(n, sr, attack_s, decay_s):
    env = np.ones(n, dtype=np.float64)
    a = max(1, int(attack_s * sr))
    a = min(a, n)
    if a > 0:
        env[:a] = np.linspace(0.0, 1.0, a, endpoint=True)
    # exponential decay applied over remaining samples after attack
    tail = n - a
    if tail > 0:
        t = np.arange(tail) / sr
        decay = np.exp(-t / max(decay_s, 1e-4))
        env[a:] *= decay
    return env


def _sine(freq, n, sr, phase=0.0):
    t = np.arange(n) / sr
    return np.sin(2 * np.pi * freq * t + phase)


def render_failure_chime(sr=SAMPLE_RATE):
    """Falling minor-2nd dyad with roughness: two detuned sines a semitone
    apart, sharp attack, ~350ms decay, slight overdrive. Most salient sound
    in the system."""
    dur = 0.42
    n = int(dur * sr)
    f0 = 622.25  # D#5-ish
    f1 = f0 * (2 ** (-1 / 12.0))  # falling minor second
    # slight downward glide for "falling" character
    t = np.arange(n) / sr
    glide0 = f0 * (1.0 - 0.12 * (t / dur))
    glide1 = f1 * (1.0 - 0.12 * (t / dur))
    phase0 = 2 * np.pi * np.cumsum(glide0) / sr
    phase1 = 2 * np.pi * np.cumsum(glide1) / sr
    s0 = np.sin(phase0)
    s1 = np.sin(phase1 + 0.3)
    sig = 0.6 * s0 + 0.6 * s1
    env = _env_adsr(n, sr, attack_s=0.005, decay_s=0.35)
    sig = sig * env
    # slight overdrive (soft clip) for roughness/edge
    sig = np.tanh(sig * 2.2)
    sig *= 1.0  # full salience reference level
    return _mono_to_stereo(sig.astype(np.float32), pan=0.0)


def render_done_chime(sr=SAMPLE_RATE):
    """Rising 2-note consonant chime: perfect fifth, C5->G5, soft attack,
    <400ms, quiet (~-6dB vs failure)."""
    dur = 0.38
    n = int(dur * sr)
    note_n = n // 2
    c5 = 523.25
    g5 = c5 * 1.5  # perfect fifth
    seg1 = _sine(c5, note_n, sr) * _env_adsr(note_n, sr, 0.02, 0.16)
    seg2 = _sine(g5, n - note_n, sr) * _env_adsr(n - note_n, sr, 0.02, 0.18)
    sig = np.concatenate([seg1, seg2])
    sig *= 0.5011872336272722  # -6dB
    return _mono_to_stereo(sig.astype(np.float32), pan=0.0)


def render_attention_chime(sr=SAMPLE_RATE):
    """Two soft FM-bell strikes, medium salience, ~500ms total."""
    dur_each = 0.22
    gap = 0.08
    n_each = int(dur_each * sr)
    n_gap = int(gap * sr)

    def bell(n):
        t = np.arange(n) / sr
        carrier = 880.0
        mod = 1320.0
        mod_index = 2.2 * np.exp(-t / 0.08)
        sig = np.sin(2 * np.pi * carrier * t + mod_index * np.sin(2 * np.pi * mod * t))
        env = _env_adsr(n, sr, 0.01, 0.14)
        return sig * env

    b = bell(n_each)
    silence = np.zeros(n_gap)
    sig = np.concatenate([b, silence, b * 0.85])
    sig *= 0.7
    return _mono_to_stereo(sig.astype(np.float32), pan=0.0)


def render_swish(sr=SAMPLE_RATE, rising=True):
    """Tiny 120ms filtered-noise-sweep portal swish, very quiet."""
    dur = 0.12
    n = int(dur * sr)
    noise = np.random.default_rng(0 if rising else 1).standard_normal(n)
    # simple time-varying one-pole lowpass sweep for the "sweep" character
    f_start, f_end = (400.0, 3200.0) if rising else (3200.0, 400.0)
    out = np.zeros(n)
    y_prev = 0.0
    for i in range(n):
        frac = i / max(n - 1, 1)
        f = f_start + (f_end - f_start) * frac
        alpha = 1.0 - math.exp(-2 * math.pi * f / sr)
        y_prev = y_prev + alpha * (noise[i] - y_prev)
        out[i] = y_prev
    env = _env_adsr(n, sr, 0.01, 0.06)
    sig = out * env
    sig /= (np.max(np.abs(sig)) + 1e-9)
    sig *= 0.28
    return _mono_to_stereo(sig.astype(np.float32), pan=0.0)


def render_compact_chime(sr=SAMPLE_RATE):
    """Soft downward 'settling' glissando, 600ms."""
    dur = 0.6
    n = int(dur * sr)
    t = np.arange(n) / sr
    f_start, f_end = 660.0, 220.0
    freq = f_start * (f_end / f_start) ** (t / dur)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    sig = np.sin(phase) * 0.6 + 0.25 * np.sin(2 * phase)
    env = _env_adsr(n, sr, 0.02, 0.45)
    sig = sig * env * 0.5
    return _mono_to_stereo(sig.astype(np.float32), pan=0.0)


def _limit_ms_ratio(x, max_ratio=0.5):
    """Brief-v2.2 section 5: "mid/side ratio limited (S <= 0.5*M)". A block-
    level (not per-sample) scalar attenuation of the side signal keeps this
    smooth/artifact-free while still holding the ratio everywhere it's
    measured (any window >= one block). x: (n,2) -> (n,2)."""
    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1])
    mid_rms = float(np.sqrt(np.mean(mid * mid))) + 1e-12
    side_rms = float(np.sqrt(np.mean(side * side)))
    limit = max_ratio * mid_rms
    if side_rms > limit:
        side = side * (limit / (side_rms + 1e-12))
    out = np.empty_like(x)
    out[:, 0] = mid + side
    out[:, 1] = mid - side
    return out


def _mono_to_stereo(sig, pan=0.0):
    """pan in [-1, 1], 0 = center."""
    left_gain = math.sqrt(0.5 * (1.0 - pan))
    right_gain = math.sqrt(0.5 * (1.0 + pan))
    out = np.empty((len(sig), 2), dtype=np.float32)
    out[:, 0] = sig * left_gain
    out[:, 1] = sig * right_gain
    return out


def build_chime_bank(sr=SAMPLE_RATE):
    return {
        "failure": render_failure_chime(sr),
        "done": render_done_chime(sr),
        "attention": render_attention_chime(sr),
        "spawn": render_swish(sr, rising=True),
        "despawn": render_swish(sr, rising=False),
        "compact": render_compact_chime(sr),
    }


# --------------------------------------------------------------------------
# Click grain synthesis
# --------------------------------------------------------------------------

def _make_click_grain(rng, center, q, decay, amp, sr=SAMPLE_RATE):
    """Render one click grain: white-noise burst -> exp envelope -> resonant
    2-pole bandpass. Per-click randomization applied by caller via center/q/
    decay/amp jitter before calling this."""
    dur = rng.uniform(0.003, 0.006) * (decay / 0.004 if decay else 1.0)
    dur = min(max(dur, 0.002), 0.012)
    n = max(4, int(dur * sr))
    noise = rng.standard_normal(n)
    t = np.arange(n) / sr
    env = np.exp(-t / max(decay, 1e-4))
    burst = noise * env

    # 2-pole resonant bandpass (simple biquad-ish resonator via difference eq)
    # Use a standard RBJ bandpass biquad.
    w0 = 2 * np.pi * center / sr
    alpha = np.sin(w0) / (2.0 * max(q, 0.5))
    cosw0 = np.cos(w0)
    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1 + alpha
    a1 = -2 * cosw0
    a2 = 1 - alpha
    b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    out = np.zeros(n, dtype=np.float64)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(n):
        x0 = burst[i]
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out[i] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0

    peak = np.max(np.abs(out)) + 1e-9
    out = (out / peak) * amp
    return out.astype(np.float32)
