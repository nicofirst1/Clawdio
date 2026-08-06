"""Ambient sound layer building blocks: musical helpers, DSP utils
(_onepole_lp, _dc_blocker, etc.), Freeverb, voice-pool helpers, drop/FM
synthesis, AmbientConfig, StemLayer, WeatherLayer, BedLayer, RainLayer,
BloomLayer. Split out of sonifier.py; see sonifier.py for the module
overview."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

try:
    from scipy import signal as _sp_signal  # type: ignore
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - environment dependent
    _sp_signal = None
    _HAVE_SCIPY = False

from config import SAMPLE_RATE, CLASS_READ, CLASS_WRITE, CLASS_EXEC
from dsp import Slew, _mono_to_stereo
from logging_setup import get_logger

log = get_logger("sonifier")



if not _HAVE_SCIPY:
    log.warning(
        "scipy is not importable; AmbientTheme requires it (Freeverb damping "
        "/ rain & bed filtering). Install with:\n"
        "    pip install scipy --break-system-packages\n"
        "GeigerTheme (SONIFIER_THEME=geiger) still works without scipy."
    )

# -- musical framework (brief section 1) -------------------------------------

_PENT_DEGREES = (0, 2, 4, 7, 9)


def _midi_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def _build_pentatonic_pool():
    """C major pentatonic over 3 octaves from C3 (MIDI 48); weight lower
    octaves more heavily. The brief says p ~ 1/(1+octave); v2 verification
    uses 1/(1+octave)**1.6, i.e. a stronger low-register bias, because the
    top octave was the only thing in the mix able to break the section 7
    tonal-prominence ceiling."""
    notes, weights = [], []
    for o in range(3):
        w = 1.0 / (1 + o) ** 1.6
        for d in _PENT_DEGREES:
            notes.append(48 + 12 * o + d)
            weights.append(w)
    notes = np.array(notes, dtype=np.int64)
    weights = np.array(weights, dtype=np.float64)
    weights /= weights.sum()
    return notes, weights


AMBIENT_NOTE_POOL, AMBIENT_NOTE_WEIGHTS = _build_pentatonic_pool()
AMBIENT_NOTE_POOL_HZ = np.array([_midi_hz(m) for m in AMBIENT_NOTE_POOL])
# v2.2 section 4: idle self-play notes are restricted to mid register (C3-A4,
# MIDI 48-69 -- "never the top octave alone"). AMBIENT_NOTE_POOL is built
# octave-by-octave ascending (see _build_pentatonic_pool), so pool index also
# happens to be pitch-ascending order -- used below for the stepwise-motion
# bias (brief section 6: next note within +-2 pool steps of the previous).
_MID_REGISTER_MASK = (AMBIENT_NOTE_POOL >= 48) & (AMBIENT_NOTE_POOL <= 69)
AMBIENT_MID_REGISTER_IDXS = np.flatnonzero(_MID_REGISTER_MASK)
_mid_w = AMBIENT_NOTE_WEIGHTS[AMBIENT_MID_REGISTER_IDXS]
AMBIENT_MID_REGISTER_WEIGHTS = _mid_w / _mid_w.sum()
STEPWISE_BIAS_STEPS = 2       # +-2 pool steps
STEPWISE_BIAS_PROB = 0.7      # probability of biasing toward that neighborhood
BLOOM_IDLE_SLOW_ACTIVITY_THRESH = 0.15  # activity_slow below this = "idle self-play"

ROOT_C1 = _midi_hz(24)   # 32.7 Hz, sub-bass weather drone
ROOT_C2 = _midi_hz(36)   # 65.41 Hz, L1 bed fundamental
ROOT_C3 = _midi_hz(48)   # 130.8 Hz, L1 bed quiet upper layer
ROOT_C4 = _midi_hz(60)   # 261.6 Hz, L4 subagent shimmer stem
ROOT_G2 = _midi_hz(43)   # 98.0 Hz, L4 second subagent stem (perfect 5th)
ROOT_G3 = _midi_hz(55)   # 196.0 Hz, v2.2 brightness-lift mid bed layer partner to C3

MAX_AMBIENT_VOICES = 10  # brief section 5: voice allocator cap
ACTIVITY_BUMP = {CLASS_READ: 0.26, CLASS_WRITE: 0.44, CLASS_EXEC: 0.46}

# -- v2.2 pacing overhaul (BRIEF-v2.2.md section 1) --------------------------
# Listener evidence: "too fast / losing control" traced to a 1.3-exponent
# rate map with discrete drops running up to ~40/s -- past where auditory
# counting works at all. v2.2 replaces that with a *compressive* (log) map
# hard-capped at a low discrete rate, and pushes everything above the point
# where taps stop being countable into a continuous wash instead.
DROP_RATE_MIN = 0.0          # R_min: idle is event-driven only, no floor hiss
DROP_RATE_CAP = 6.0          # R_cap: discrete drops/s (auditory counting breaks ~4-6/s)
DROP_RATE_LOG_BASE = 10.0    # log(1+9a)/log(10): a=1 -> exactly R_cap
# v2.3 half-density (blind round 2: halving density won the same-session blind
# ranking against v2 and v2.2, though timbre -- not density -- turned out to
# be the dominant complaint; see research/BRIEF-v2.3.md). These three knobs
# replicate eval/make_clips.py's "c3_v22_half_density" Block C variant exactly:
# pacing floor and coalescing window both doubled, rate map scaled 0.5x
# (DROP_RATE_SCALE below, applied in _drop_rate_from_activity).
DROP_MIN_GAP_S = 0.300       # pacing floor: no two onsets (any source) closer than this (v2.2: 0.150)
BURST_COALESCE_WINDOW_S = 0.500   # inter-onset < this merges into 1 weighted drop (v2.2: 0.250)
DROP_RATE_SCALE = 0.5        # v2.3: half-density scale on the whole compressive map (v2.2: 1.0)
BURST_COALESCE_STEP_DB = 2.5      # weight added per merged (suppressed) event
BURST_COALESCE_MAX_DB = 7.0       # cap on accumulated coalescing weight
RATE_SLEW_TAU_S = 2.2        # >= 2s per brief: rate parameter itself is slewed
RATE_HYSTERESIS = 0.35       # drops/s: ignore small target wobbles (no lurching)
# Crossfade point: the activity level `a` at which the compressive rate map
# reaches 5 drops/s (of the 6/s cap) -- from here up, extra activity thickens
# the continuous rain-wash bed instead of adding more discrete taps.
WASH_CROSSFADE_RATE = 5.0
WASH_CROSSFADE_A = (10.0 ** (WASH_CROSSFADE_RATE / DROP_RATE_CAP) - 1.0) / 9.0
NOTE_MIN_GAP_S = 2.5          # melodic notes: min gap when busy (idle self-play stays ~1/45s)
DROP_PAN_LIMIT = 0.35         # brief section 5: per-drop pan constrained to +-0.35
NOTE_PAN_LIMIT = 0.20         # melodic notes constrained to +-0.20
MS_MAX_SIDE_OVER_MID = 0.5    # brief section 5: S <= 0.5*M

# v2.4 state legibility (research/BRIEF-v2.4.md): two independent listeners
# confirmed the same defect -- the Stop cadence doesn't read as conclusive
# and idle-after-Stop is indistinguishable from idle-during-work. done_cadence
# ="v22" reproduces the exact legacy Stop handling (regression guard, same
# trick as drop_timbre="noise"); "v24" is the new default. SETTLED_* controls
# the post-Stop "waiting for user" window: deeper/longer bed dip than the
# ordinary idle ladder, and a much sparser bloom rate.
DONE_CADENCE_MODES = ("v22", "v24")
SETTLED_HOLD_S = 20.0          # v22 only: fixed hard timeout before falling back to
                                # the ordinary idle ladder (legacy behavior, unchanged).
                                # v24 does NOT time out on this -- settled instead holds
                                # for as long as the agent stays idle since Stop, and
                                # only exits when a new event fires (see
                                # BedLayer._bed_target_db). SETTLED_HOLD_S still sets the
                                # marker value used to detect "no event since Stop" for
                                # v24, it just isn't a v24 cutoff anymore.
SETTLED_BED_DB = -38.0         # settled bed target (v2.2's post-Stop hold was -33,
                                # then rose back to -30 after only 6s -- the opposite
                                # of "quieter/waiting"); -38 sits between the >90s and
                                # >600s idle-ladder rungs, calibrated in BRIEF-v2.4.md
SETTLED_BED_TAU_S = 8.0        # glide time into the settled level
SETTLED_BLOOM_RATE_SCALE = 0.35  # bloom's self-play rate is scaled by this while
                                  # settled (on top of the existing idle rate) --
                                  # "very sparse", not silent


def _drop_rate_from_activity(a, scale=None):
    """Compressive (log) discrete-drop rate map, brief section 1:
    rate = R_min + (R_cap - R_min) * log(1 + 9a) / log(10), a in [0,1],
    scaled by `scale` (default DROP_RATE_SCALE; v2.3 half-density: 0.5x)."""
    if scale is None:
        scale = DROP_RATE_SCALE
    a = max(0.0, min(1.0, a))
    return scale * (DROP_RATE_MIN + (DROP_RATE_CAP - DROP_RATE_MIN) * (
        math.log(1.0 + 9.0 * a) / math.log(DROP_RATE_LOG_BASE)))


def _ou_step(x, mean, tau, sigma, dt, rng):
    """Exact-discretization Ornstein-Uhlenbeck update (mean-reverting random
    walk). tau = time constant (s), sigma = stationary std dev."""
    if tau <= 0:
        return mean
    a = math.exp(-dt / tau)
    return mean + (x - mean) * a + sigma * math.sqrt(max(0.0, 1.0 - a * a)) * rng.standard_normal()


def _raised_cosine_attack(n):
    if n <= 1:
        return np.ones(max(n, 0))
    return 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)


def _rbj_bandpass(x, center, q, sr=SAMPLE_RATE):
    """Vectorized RBJ constant-skirt bandpass biquad (scipy.signal.lfilter).
    Same math as GeigerTheme's _make_click_grain resonator, just applied via
    lfilter instead of a per-sample python loop (this is on the ambient
    critical path so it needs to be fast)."""
    w0 = 2 * np.pi * center / sr
    alpha = np.sin(w0) / (2.0 * max(q, 0.5))
    cosw0 = np.cos(w0)
    b0, b1, b2 = alpha, 0.0, -alpha
    a0, a1, a2 = 1 + alpha, -2 * cosw0, 1 - alpha
    b = [b0 / a0, b1 / a0, b2 / a0]
    a = [1.0, a1 / a0, a2 / a0]
    if _HAVE_SCIPY:
        return _sp_signal.lfilter(b, a, x)
    # Rare fallback path (scipy missing): small python loop, only ever used
    # for short (~5-20ms) pre-rendered grains, not the per-block hot path.
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        x0 = x[i]
        y0 = b[0] * x0 + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        y[i] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return y


def _onepole_lp_coeffs(cutoff, sr=SAMPLE_RATE):
    cutoff = max(20.0, min(cutoff, sr * 0.45))
    alpha = math.exp(-2 * math.pi * cutoff / sr)
    return [1.0 - alpha], [1.0, -alpha]


def _dc_blocker(x, state_x1, state_y1, r=0.9975):
    """y[n] = x[n] - x[n-1] + r*y[n-1], applied per-channel. x: (n,2).

    Vectorized through scipy.signal.lfilter (b=[1,-1], a=[1,-r]) with the
    direct-form-II-transposed state derived in closed form from the caller's
    (x[-1], y[-1]) pair: z0 = -a1*y1 + b1*x1 = r*y1 - x1. This is the exact
    same recursion as the reference python loop below (verified bit-for-bit
    in tests/test_ambient.py) but ~0.5ms/block cheaper -- it was the single
    largest term in the per-block budget."""
    n = x.shape[0]
    if _HAVE_SCIPY:
        y = np.empty_like(x)
        for ch in range(x.shape[1]):
            zi = np.array([r * state_y1[ch] - state_x1[ch]])
            y[:, ch], zf = _sp_signal.lfilter([1.0, -1.0], [1.0, -r], x[:, ch], zi=zi)
        return y, x[n - 1].copy(), y[n - 1].copy()
    y = np.empty_like(x)
    x1 = state_x1
    y1 = state_y1
    for i in range(n):
        cur_x = x[i].copy()
        y[i] = cur_x - x1 + r * y1
        x1 = cur_x
        y1 = y[i]
    return y, x1, y1


# -- shared Freeverb (brief section 4, exact constants) -----------------------

FV_COMB_L = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
FV_COMB_R = tuple(d + 23 for d in FV_COMB_L)
FV_ALLPASS_L = (556, 441, 341, 225)
FV_ALLPASS_R = tuple(d + 23 for d in FV_ALLPASS_L)
# v2.2 "warm room" (BRIEF-v2.2.md section 2): listener evidence was "dark
# cave"/"under the sea"/"isolated" -- a room this big and this reflective
# reads as a cathedral/cave, not a small warm room. roomsize 0.90 -> 0.78
# (feedback 0.953 -> ~0.918) and damp 0.35 -> 0.45 shrink the space and
# soften the reflections; combined with AMBIENT_WET_GAIN below (wet -6dB)
# this targets RT60 ~= 1.0-2.2s (was ~3-4s).
FV_FEEDBACK = 0.918
FV_DAMP = 0.45
FV_ALLPASS_G = 0.5
FV_INPUT_GAIN = 0.015
FV_DENORM = 1e-20


class Freeverb:
    """Classic Freeverb topology: 8 parallel damped feedback combs per
    channel into 4 series allpasses per channel. Per-block ring-buffer
    implementation per BRIEF-v2.md section 4: all comb delays are >=
    BLOCKSIZE so comb reads/writes have no intra-block feedback dependency
    (fully vectorizable with fancy indexing); the damping one-pole uses
    scipy.signal.lfilter with persisted zi state; allpasses with delay <
    blocksize are processed in <=delay sub-chunks so each chunk's reads
    only ever depend on a strictly earlier chunk's writes."""

    def __init__(self, sr=SAMPLE_RATE):
        self.sr = sr
        self.comb_buf_L = [np.zeros(d, dtype=np.float64) for d in FV_COMB_L]
        self.comb_buf_R = [np.zeros(d, dtype=np.float64) for d in FV_COMB_R]
        self.comb_idx_L = [0] * len(FV_COMB_L)
        self.comb_idx_R = [0] * len(FV_COMB_R)
        self.comb_fs_L = [0.0] * len(FV_COMB_L)
        self.comb_fs_R = [0.0] * len(FV_COMB_R)
        self.ap_buf_L = [np.zeros(d, dtype=np.float64) for d in FV_ALLPASS_L]
        self.ap_buf_R = [np.zeros(d, dtype=np.float64) for d in FV_ALLPASS_R]
        self.ap_idx_L = [0] * len(FV_ALLPASS_L)
        self.ap_idx_R = [0] * len(FV_ALLPASS_R)

    def _comb_block(self, buf, idx, filterstore, delay, x):
        n = len(x)
        pos = (idx + np.arange(n)) % delay
        bufout = buf[pos]
        damp1, damp2 = FV_DAMP, 1.0 - FV_DAMP
        if _HAVE_SCIPY:
            # zi for y[n] = damp2*x[n] + damp1*y[n-1] in DF2T form is simply
            # [damp1 * y[-1]]; computing it directly avoids 16 lfiltic calls
            # per block (lfiltic was ~0.2ms/block of pure overhead).
            zi = np.array([damp1 * filterstore])
            fs, _zf = _sp_signal.lfilter([damp2], [1.0, -damp1], bufout, zi=zi)
        else:  # pragma: no cover - scipy present in this project's env
            fs = np.empty(n)
            y = filterstore
            for i in range(n):
                y = damp2 * bufout[i] + damp1 * y
                fs[i] = y
        new_fs = float(fs[-1]) if n else filterstore
        buf[pos] = x + fs * FV_FEEDBACK + FV_DENORM
        return bufout, (idx + n) % delay, new_fs

    def _allpass_block(self, buf, idx, delay, x):
        n = len(x)
        out = np.empty(n)
        cursor = idx
        i0 = 0
        while i0 < n:
            chunk = min(delay, n - i0)
            xin = x[i0:i0 + chunk]
            pos = (cursor + np.arange(chunk)) % delay
            bufout = buf[pos]
            out[i0:i0 + chunk] = -xin + bufout
            buf[pos] = xin + bufout * FV_ALLPASS_G + FV_DENORM
            cursor = (cursor + chunk) % delay
            i0 += chunk
        return out, cursor

    def process_block(self, mono_in):
        """mono_in: (n,) float64. Returns wet stereo (n,2) float64."""
        n = len(mono_in)
        x = mono_in * FV_INPUT_GAIN
        sum_l = np.zeros(n)
        for i, delay in enumerate(FV_COMB_L):
            bufout, self.comb_idx_L[i], self.comb_fs_L[i] = self._comb_block(
                self.comb_buf_L[i], self.comb_idx_L[i], self.comb_fs_L[i], delay, x)
            sum_l += bufout
        sum_r = np.zeros(n)
        for i, delay in enumerate(FV_COMB_R):
            bufout, self.comb_idx_R[i], self.comb_fs_R[i] = self._comb_block(
                self.comb_buf_R[i], self.comb_idx_R[i], self.comb_fs_R[i], delay, x)
            sum_r += bufout
        out_l = sum_l
        for i, delay in enumerate(FV_ALLPASS_L):
            out_l, self.ap_idx_L[i] = self._allpass_block(self.ap_buf_L[i], self.ap_idx_L[i], delay, out_l)
        out_r = sum_r
        for i, delay in enumerate(FV_ALLPASS_R):
            out_r, self.ap_idx_R[i] = self._allpass_block(self.ap_buf_R[i], self.ap_idx_R[i], delay, out_r)
        return np.stack([out_l, out_r], axis=1)


# -- one-shot voice pool (drops, bloom notes, gestures) ------------------------

MAX_PENDING_VOICES = 64
MAX_VOICE_TAILS = 6      # concurrent 3ms steal-fade remainders


def _voice_pool_add(pool, voice, sr=SAMPLE_RATE):
    """Append a one-shot voice {"buf": (m,2) f32/f64, "pos": int, "bus": str}.

    Implements the brief's "voice allocator max 10 voices steal-oldest with
    3 ms fade": at capacity the oldest LIVE voice is truncated to a <=3 ms
    faded remainder so it ends cleanly instead of clicking. Those remainders
    are marked "stolen"; they die within one block, they do NOT count against
    the live-voice cap, and they are themselves hard-capped by
    MAX_VOICE_TAILS.

    (v2 verification bug: the original version popped the victim and then
    re-inserted it AND appended the newcomer, so every add at capacity grew
    the list by one. Under a live event flood -- 6 ingress threads, which is
    what an over-eager hook config looks like -- the pool reached 1333
    entries and the per-block mixing cost grew without bound. The cap has to
    be enforced on a count that the steal itself does not inflate.)
    """
    live = [v for v in pool if not v.get("stolen")]
    if len(live) >= MAX_AMBIENT_VOICES:
        victim = live[0]
        try:
            pool.remove(victim)
        except ValueError:  # pragma: no cover - concurrent reap
            victim = None
        if victim is not None:
            buf, pos = victim["buf"], victim["pos"]
            remaining = len(buf) - pos
            fade_n = min(remaining, max(1, int(0.003 * sr)))
            tails = [v for v in pool if v.get("stolen")]
            if fade_n > 0 and len(tails) < MAX_VOICE_TAILS:
                tail = buf[pos:pos + fade_n].copy()
                fade = np.linspace(1.0, 0.0, fade_n)[:, None].astype(tail.dtype)
                tail *= fade
                victim["buf"] = tail
                victim["pos"] = 0
                victim["stolen"] = True
                pool.append(victim)
            # else: already carrying the maximum number of dying tails, so the
            # victim is dropped outright rather than unbounding the pool.
    pool.append(voice)


def _mix_voices(pool, n):
    """Mix all voices into (reverb_bus, direct_bus), reaping finished ones.
    Mirrors GeigerTheme._render_chimes's ingress-safe list mutation pattern:
    only ever mutate the same list object in place."""
    reverb_bus = np.zeros((n, 2), dtype=np.float64)
    direct_bus = np.zeros((n, 2), dtype=np.float64)
    count = len(pool)
    for i in range(count):
        v = pool[i]
        buf, pos = v["buf"], v["pos"]
        remaining = len(buf) - pos
        if remaining <= 0:
            continue
        wn = min(remaining, n)
        seg = buf[pos:pos + wn]
        if v.get("bus") == "direct":
            direct_bus[:wn] += seg
        else:
            reverb_bus[:wn] += seg
        v["pos"] += wn
    for i in range(count - 1, -1, -1):
        if pool[i]["pos"] >= len(pool[i]["buf"]):
            del pool[i]
    return reverb_bus, direct_bus


# -- L2: rain drop grain bank (brief-v2.2 section 3; v2.3 timbre, see
# research/BRIEF-v2.3.md and lit-review-annoyance rec #3) ---------------------

DROP_TIMBRES = ("woodblock", "marimba", "plink", "noise")


def _render_drop_woodblock(rng, sr=SAMPLE_RATE):
    """v2.3 default: damped woodblock modal click, same modal-synthesis
    family as _render_knock (PostToolUseFailure) but pitched up an
    octave-plus into the "tick" range and much shorter (<150ms vs the
    knock's 70-110ms tau). Round-2/3 blind evidence: noise-tick drops read
    as "white noise/chaos"; lit-review rec #3 recommends reusing the
    project's existing damped-modal primitive for routine drops instead of
    a broadband noise burst. Mode ratios/decay cribbed from
    eval/blind/make_timbre_clips.py's render_woodblock (round-3 winning
    candidate family, read-only reference -- reimplemented here so the
    engine doesn't depend on the eval script)."""
    f0 = rng.uniform(800.0, 1200.0)
    ratios = (1.0, 1.47, 2.09)
    amps = (1.0, 0.45, 0.22)
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
    env[:attack_n] = _raised_cosine_attack(attack_n)
    sig *= env
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


def _render_drop_marimba(rng, sr=SAMPLE_RATE):
    """v2.3 A/B candidate: muted marimba/soft mallet, fundamental 400-700 Hz,
    longer/darker decay and a soft raised-cosine attack. Cribbed from
    eval/blind/make_timbre_clips.py's render_marimba (read-only
    reference)."""
    f0 = rng.uniform(400.0, 700.0)
    ratios = (1.0, 2.76, 5.4)  # bar-like inharmonic partials, marimba-ish
    amps = (1.0, 0.28, 0.10)
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
    env[:attack_n] = _raised_cosine_attack(attack_n)
    sig *= env
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


def _render_drop_plink(rng, sr=SAMPLE_RATE):
    """v2.3 A/B candidate: water-drop "plink" -- single decaying sine with a
    small (<5%) downward pitch envelope (not a chirp/sweep, just enough
    droop to read as a liquid drop) plus a quiet damped body resonance an
    octave down. Cribbed from eval/blind/make_timbre_clips.py's
    render_plink (read-only reference)."""
    f0 = rng.uniform(1000.0, 1600.0)
    tau = rng.uniform(0.08, 0.14)
    dur = min(0.25, 6 * tau)
    n = max(int(0.01 * sr), int(dur * sr))
    t = np.arange(n) / sr
    pitch_drop = rng.uniform(0.02, 0.045)  # <5% downward
    inst_freq = f0 * (1.0 - pitch_drop * (1.0 - np.exp(-t / (tau * 0.6))))
    phase = 2 * np.pi * np.cumsum(inst_freq) / sr
    sig = np.sin(phase) * np.exp(-t / tau)
    body = 0.25 * np.sin(2 * np.pi * (f0 / 2.0) * t) * np.exp(-t / (tau * 1.6))
    sig = sig + body
    attack_n = min(n, max(1, int(0.0008 * sr)))
    env = np.ones(n)
    env[:attack_n] = _raised_cosine_attack(attack_n)
    sig *= env
    peak = float(np.max(np.abs(sig))) + 1e-9
    return (sig / peak).astype(np.float64)


def _render_one_drop_variant(rng, sr=SAMPLE_RATE):
    """v2.2 legacy timbre, kept for A/B (drop_timbre="noise"): a filtered-
    noise "tick", NOT a sine chirp. v2 listener evidence: the downward sine
    chirp reads as a bird-call signature and was confusable with melodic
    notes ("birds or drops?"). v2.2 killed the chirp entirely -- every drop
    a 4-10 ms burst of white noise through an RBJ bandpass (1.8-3.5 kHz, Q
    2-4) with a sharp exponential decay. v2.3 replaced this as the DEFAULT
    (round-2/3 blind evidence: this timbre itself reads as "white noise/
    chaos" in the full mix), but it's kept selectable since it's the
    measured control for the drop_timbre A/B. Returned mono, peak-
    normalized."""
    dur = rng.uniform(0.004, 0.010)
    n = max(4, int(dur * sr))
    noise = rng.standard_normal(n)
    center = 10.0 ** rng.uniform(math.log10(1800.0), math.log10(3500.0))
    q = rng.uniform(2.0, 4.0)
    sig = _rbj_bandpass(noise, center, q, sr)
    tau = rng.uniform(0.0015, 0.004)  # sharp exponential decay
    env = np.exp(-np.arange(n) / sr / tau)
    sig = sig * env
    peak = float(np.max(np.abs(sig))) + 1e-9
    sig = sig / peak
    return sig.astype(np.float64)


_DROP_RENDER_FNS = {
    "woodblock": _render_drop_woodblock,
    "marimba": _render_drop_marimba,
    "plink": _render_drop_plink,
    "noise": _render_one_drop_variant,
}


def _build_drop_bank(rng, sr=SAMPLE_RATE, count=14, timbre="woodblock"):
    """timbre="noise" reproduces the exact v2.2 grain bank (same RNG call
    sequence as the pre-v2.3 _build_drop_bank(rng, sr, count)) -- this is
    the regression guard for the v2.3 timbre change: render with
    drop_timbre="noise" and the output must stay bit-identical."""
    render_fn = _DROP_RENDER_FNS.get(timbre, _render_drop_woodblock)
    return [render_fn(rng, sr) for _ in range(count)]


# -- L3: 2-op FM e-piano / bell voice (brief section 2) ------------------------

def _render_fm_note(rng, freq, velocity, sr=SAMPLE_RATE, bell=False, i_peak_override=None):
    # +-9 cents of per-note detune. Musically this is just "a real instrument
    # is never exactly in tune"; numerically it spreads each pitch's energy
    # over several analysis bins instead of stacking every repetition of the
    # same pool note into one razor-thin spectral line, which is what pushed
    # section 7 item 5 (tonal prominence) over 12 dB.
    freq = freq * (2.0 ** (rng.uniform(-9.0, 9.0) / 1200.0))
    ratio = 3.5 if bell else 1.0
    fm = freq * ratio
    if i_peak_override is not None:
        i_peak = i_peak_override
    elif bell:
        # BRIEF: "soft bell accent voice r = 3.5, I <= 2". The v2 builder's
        # 1.2 + velocity*2.0 reached I = 3.2, which pushes the r=3.5 sidebands
        # up past 4 kHz where this mix's bed is 45 dB down -- audibly a phone
        # alert rather than a wind chime.
        i_peak = min(2.0, 0.8 + velocity * 1.5)
    else:
        i_peak = 0.5 + velocity * 2.0
    # Pitch-dependent decay: a struck/plucked resonator's high notes die away
    # much faster than its low ones, and modelling that (a) sounds like an
    # instrument rather than a sine generator, (b) stops repeated high pool
    # notes from summing into a narrow 17 dB spectral line over a 30s window
    # (section 7 item 5). v2.2 section 4 (embedding rule): "notes shortened
    # (decay tau <= 1.0 s)" -- a long tail was part of what made a note read
    # as a separate, distant event rather than something embedded in the bed.
    tau_env = rng.uniform(0.5, 1.0) * min(1.6, max(0.35, (330.0 / max(freq, 20.0)) ** 0.55))
    tau_env = min(tau_env, 1.0)
    dur = min(2.2, 4.0 * tau_env)
    n = max(int(0.03 * sr), int(dur * sr))
    t = np.arange(n) / sr
    mod_tau = rng.uniform(0.15, 0.3)
    idx_env = i_peak * np.exp(-t / mod_tau)
    mod = idx_env * np.sin(2 * np.pi * fm * t)
    sig = np.sin(2 * np.pi * freq * t + mod)
    amp_env = np.exp(-t / max(tau_env, 0.05))
    attack_s = rng.uniform(0.005, 0.008)
    attack_n = min(n, max(1, int(attack_s * sr)))
    amp_env[:attack_n] = _raised_cosine_attack(attack_n)
    # Register tilt: high notes softer. Equal-loudness alone justifies it
    # (880 Hz is perceptually much louder than 130 Hz at equal SPL), and it
    # keeps the top-octave pool notes from poking out of a bed whose own
    # spectrum is falling at ~4 dB/oct.
    reg_db = max(-4.5, min(1.5, -3.0 * math.log2(max(freq, 20.0) / 330.0)))
    sig = sig * amp_env * (0.35 + 0.55 * velocity) * _db_to_lin(reg_db)
    return sig.astype(np.float64)


def _render_knock(rng, velocity=0.7, sr=SAMPLE_RATE, pitch_factor=1.0):
    """Low wooden knock: modal woodblock, modes ratio 1:1.47:2.09:2.56 on
    155 Hz, tau 70-110 ms, noise contact transient. Deliberately localized in
    80-400 Hz.

    v2.2 VERIFIER reshape -- "make the knock detectable by SPECTRAL CONTRAST
    (dry, low, distinct) rather than level". Three changes, each buying
    80-400 Hz envelope level at ZERO extra peak level (the embedding cap is a
    PEAK cap, so anything that lowers this gesture's own crest factor is free
    in-band energy):

      * base 190 -> 155 Hz. At 190 the top mode landed at 190*2.56 = 486 Hz,
        i.e. OUTSIDE the 80-400 Hz band the gesture is supposed to occupy
        (and outside where "low wooden knock" lives). At 155 all four modes
        (155/228/324/397 Hz) are inside it.
      * decorrelated mode phases. Four modes starting at phase 0 sum
        coherently into a single sample-one spike of amplitude 2.18, and
        peak-normalising against THAT spike threw away most of the modal
        body's level. Fixed per-mode phase offsets (fixed, not random, so
        renders stay deterministic) drop the initial peak without touching
        the modal decay.
      * tau 30-80 -> 70-110 ms. Criterion 9c measures a 50 ms-smoothed
        envelope; a 30 ms mode is half-decayed before the window closes.

    Measured effect: +3.6 dB of 80-400 Hz envelope peak for the same peak
    amplitude, which is what lets KNOCK_EMBED_CAP_DB come back down from the
    builder's +23 to +16 without losing criterion 9c."""
    # pitch_factor is the BRIEF-v2.5 per-session slot transpose; slot 0 passes
    # 1.0 so `base * 1.0 == base` bit-identically (byte-identical single-
    # session render). The modal ratios ride the shifted base, so the whole
    # knock transposes coherently rather than detuning.
    base = 155.0 * pitch_factor
    ratios = (1.0, 1.47, 2.09, 2.56)
    mode_phases = (0.0, 0.31, 0.63, 0.17)
    dur = 0.5
    n = int(dur * sr)
    t = np.arange(n) / sr
    sig = np.zeros(n)
    for i, r in enumerate(ratios):
        tau = rng.uniform(0.07, 0.11)
        amp = 0.6 ** i
        sig += amp * np.sin(2 * np.pi * (base * r * t + mode_phases[i])) * np.exp(-t / tau)
    # Contact transient: short, and lowpassed. A raw broadband burst made the
    # knock read as a click/thump artifact -- it lit up the 1.5-6 kHz band as
    # much as the 80-400 Hz modal body, so the gesture did not localise as the
    # low wooden knock the brief asks for.
    noise_n = max(4, int(0.005 * sr))
    contact = rng.standard_normal(noise_n) * np.exp(-np.arange(noise_n) / sr / 0.0018)
    if _HAVE_SCIPY:
        b_c, a_c = _onepole_lp_coeffs(800.0, sr)
        contact = _sp_signal.lfilter(b_c, a_c, contact)
        contact = contact / (float(np.max(np.abs(contact))) + 1e-9)
    # `velocity` now shapes the CONTACT (how hard the strike sounds) rather
    # than scaling the whole gesture's level. The gesture is peak-normalised
    # to exactly 1.0 so that its caller's embedding cap
    # (KNOCK_EMBED_CAP_DB) is an exact peak-over-bed figure rather than one
    # silently 1.4 dB under its own stated value.
    sig[:noise_n] += contact * (0.14 + 0.22 * velocity)
    peak = float(np.max(np.abs(sig))) + 1e-9
    sig = sig / peak
    return sig.astype(np.float64)


def _nearest_pool_note(target_midi):
    idx = int(np.argmin(np.abs(AMBIENT_NOTE_POOL - target_midi)))
    return int(AMBIENT_NOTE_POOL[idx]), float(AMBIENT_NOTE_POOL_HZ[idx])


def _build_cadence_notes(rng, n_notes=None):
    """v2.2 legacy: 2-4 note descending pentatonic sequence landing on C or G.
    Kept verbatim for done_cadence="v22" (see AmbientConfig.done_cadence /
    research/BRIEF-v2.4.md) -- this is why round 2's baseline eval found the
    Stop cadence didn't read as conclusive: landing on G (the dominant, not
    the tonic) half the time is a half-cadence, not a resolution."""
    if n_notes is None:
        n_notes = int(rng.integers(2, 5))
    land = int(rng.choice([60, 67]))  # C4 or G4
    seq_desc = [land]
    cur = land
    for _ in range(n_notes - 1):
        cur = cur - int(rng.choice([2, 3, 4, 5]))
        seq_desc.append(cur)
    seq_desc = seq_desc[::-1]  # play in time order: highest-first, ending on land
    return [_nearest_pool_note(m)[1] for m in seq_desc]


def _build_cadence_notes_v24(rng, n_notes=None):
    """v2.4 authentic cadence: 3-4 note descending pentatonic sequence that
    ALWAYS lands on C4 (the tonic -- never G, the dominant/half-cadence),
    per research/BRIEF-v2.4.md. This is the melodic voice of the gesture;
    AmbientTheme.handle_event pairs it with a simultaneous bass-register
    root landing (ROOT_C2/C3, the pad's own fundamental) so the resolution
    reads harmonically, not just melodically -- the "authentic cadence"
    (V-I with a root-position tonic landing) tonal listeners actually parse
    as conclusive, vs. v2.2's melody-only descent."""
    if n_notes is None:
        n_notes = int(rng.integers(3, 5))
    land = 60  # C4 -- tonic only, never the dominant
    seq_desc = [land]
    cur = land
    for _ in range(n_notes - 1):
        cur = cur - int(rng.choice([2, 3, 4, 5]))
        seq_desc.append(cur)
    seq_desc = seq_desc[::-1]  # play in time order: highest-first, ending on land
    return [_nearest_pool_note(m)[1] for m in seq_desc]


def _pick_write_register_note(rng):
    """Write-tool-triggered mid-register note, 250Hz-1kHz per the anti-
    masking register plan. Returns (midi, hz)."""
    mask = (AMBIENT_NOTE_POOL_HZ >= 250.0) & (AMBIENT_NOTE_POOL_HZ < 1000.0)
    if not np.any(mask):
        return int(AMBIENT_NOTE_POOL[0]), float(AMBIENT_NOTE_POOL_HZ[0])
    idxs = np.flatnonzero(mask)
    weights = AMBIENT_NOTE_WEIGHTS[idxs]
    weights = weights / weights.sum()
    k = int(rng.choice(idxs, p=weights))
    return int(AMBIENT_NOTE_POOL[k]), float(AMBIENT_NOTE_POOL_HZ[k])


# -- AmbientTheme --------------------------------------------------------------

BED_OU_TAU = 6.0
# v2.2 section 2: "the bed must be STEADY: OU drift stays but excursions
# halved." Listener evidence ("not regular", "lost") pointed at a bed whose
# own level/cutoff wander was itself a source of unpredictability on top of
# an already-sparse mix. Halving these sigmas (0.5x) keeps the slow organic
# drift but stops it from ever reading as instability.
BED_OU_EXCURSION_SCALE = 0.5
BED_CUTOFF_OU_SIGMA_OCT = BED_OU_EXCURSION_SCALE * 0.3 * math.sqrt(2.0 / BED_OU_TAU)
BED_GAIN_OU_SIGMA_DB = BED_OU_EXCURSION_SCALE * 2.0 * math.sqrt(2.0 / BED_OU_TAU)
SHIMMER_OU_TAU = 3.5
SHIMMER_OU_SIGMA = BED_OU_EXCURSION_SCALE * 0.3 * math.sqrt(2.0 / SHIMMER_OU_TAU)
# v2.2 brightness lift (section 2): a soft C3+G3 mid layer, independent from
# the low bed/shimmer stack, to help pull the mix's spectral centroid up out
# of the "dark cave" register (v2 measured ~157 Hz) toward the 350-1200 Hz
# target band without adding harshness.
MIDLAYER_OU_TAU = 4.5
MIDLAYER_OU_SIGMA = 0.35 * math.sqrt(2.0 / MIDLAYER_OU_TAU)
# VERIFIER measurement note (why the builder's render still measured 321-339
# Hz against the 350 Hz floor): the brief's literal "add C3+G3" content sits
# at 130.8/196 Hz, i.e. INSIDE the 125-250 Hz octave band that was already
# the mix's single dominant band (46% of total power, measured on the
# realistic render). Adding there buys warmth but moves the power-weighted
# centroid by ~nothing. Voicing this layer an octave up (C4+G4) was tried and
# does raise the centroid (+18 Hz) but puts a +4 dB spike in the 500 Hz
# octave band, eating 1.4 dB of the +-6 dB conformity budget for less lift
# than the air-tilt change below delivers for free. So the mid layer keeps
# the brief's voicing and the brightness lift is carried by (a) the air
# layer's tilt corner and (b) the low pad's own lowpass corner -- see
# AIR_TILT_LO_HZ and BED_LP_BASE_HZ.
# NOTE: len(MIDLAYER_FREQS) is the single source of truth for the mid layer's
# OU/phase state size (see AmbientTheme.__init__); adding a third voice here
# works without touching __init__.
MIDLAYER_FREQS = np.array([ROOT_C3, ROOT_G3])  # 130.8 / 196.0 Hz
MIDLAYER_LP_HZ = 2200.0
# Low pad lowpass corner. v2 used a fixed 700 Hz base (a very muffled pad --
# a direct contributor to "dark cave"); v2.2 opens it so the saw keeps a few
# audible harmonics. The OU walk (bed_cutoff_oct) still rides on top and the
# hard ceiling keeps it out of "buzzy".
BED_LP_BASE_HZ = 1150.0
BED_LP_MAX_HZ = 3200.0
# Harmonics [1,2,3,4,6,8] of the bed root (brief section 2 L1) plus one
# gated "color" partial at 9/4 (a D, i.e. the sus2 degree) that is silent
# unless a Notification/PermissionRequest sus2 recoloring is active.
SHIMMER_HARMONICS = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 2.25)
SHIMMER_SUS2_SLOT = 6
SHIMMER_FIFTH_SLOT = 2       # the 3rd harmonic (a G): swapped to 8/3 (an F)
SHIMMER_SUS4_RATIO = 8.0 / 3.0
_VI_RATIO = 2.0 ** (-3.0 / 12.0)   # C -> A below (I -> vi), the "darker room"
SUPERSAW_VOICES = 7
SUPERSAW_CENTS = np.linspace(-7.0, 7.0, SUPERSAW_VOICES)
SUPERSAW_AMPS = np.where(np.abs(SUPERSAW_CENTS) < 1e-6, 1.0, 0.7)

# -- mix calibration ----------------------------------------------------------
# All *_CAL_DB values are trims that make each layer's nominal dBFS label
# (the numbers in BRIEF-v2.md/v2.2.md section 2) correspond to that layer's
# measured RMS at the output. They were set by measurement, not by
# ear-guessing: see VERIFICATION.md "tuning changes" and tools/lab.py.
#
# v2.2 "warm room" (BRIEF-v2.2.md section 2): wet level -6dB vs v2, dry
# fraction up -- shrinks the reflective space AND makes the direct signal
# (not the room) carry more of the perceived loudness, which is what turns
# a "distant bing under pressure" into something embedded in the room.
AMBIENT_WET_GAIN = 0.85 * (10.0 ** (-6.0 / 20.0))  # -6dB vs v2's 0.85 (~0.426)
AMBIENT_DRY_GAIN = 1.30    # v2 was 1.0; "dry fraction up" per section 2
AMBIENT_MASTER_HEADROOM_DB = -15.5  # master trim: at SONIFIER_VOLUME=1.0 the
                            # active-state program sits at about -23 dBFS RMS /
                            # -8 dBFS peak (a sane ambient master), and the
                            # daemon's 0.5 default lands 6 dB below that.
BED_CAL_DB = 19.0           # L1 supersaw+shimmer pad
MIDLAYER_CAL_DB = 21.0      # v2.2 C3+G3 brightness-lift layer (see _render_bed)
AIR_CAL_DB = 19.0           # L1b continuous shaped-noise bed (see _render_air).
                            # v2.2: +2dB vs v2's 11.5 -- section 2 "bed presence
                            # = the control anchor" raises the always-on floor.
# VERIFIER re-tune. Measured on the builder's v2.2 render: with DROP_CAL_DB
# = -12, a median "write" drop peaked at -38.6 dB in its own 1.2-6 kHz band
# while the bed sat at -36.3 dB in the SAME band -- i.e. the rain taps were
# 2 dB UNDER the bed, and a "read" drop 7 dB under. The section-2 bed-presence
# raise had quietly buried L2. That is the true root cause of the N4/9b
# failures (the onset detector was reading bed texture because there was
# barely any drop to read), and it also means the mapping carried almost no
# audible information. Recalibrated so a median drop peaks ~7 dB over the
# bed's own band level and even the quietest class/amp combination clears it.
DROP_CAL_DB = 2.0           # L2 rain grains (peak-normalized at synthesis)
# Per-drop random level. v2's +-6 dB (12 dB range) put the bottom of the
# distribution below the bed no matter where the mean sits; +-4 dB keeps the
# "real rain is not a metronome" variation without inaudible taps.
DROP_AMP_SPREAD_DB = 4.0
# v2.2 embedding rule (section 4): note/knock peak levels are no longer a
# fixed trim -- they track the CURRENT calibrated bed level at spawn time
# (bed_level_db.value + BED_CAL_DB is this mix's measured bed dBFS reference)
# plus a fixed dB cap over it. See _spawn_note / _render_knock call sites.
NOTE_EMBED_CAP_DB = 10.0    # pitched one-shot peak <= bed RMS + 10 dB
NOTE_EMBED_CAP_IDLE_DB = 6.0  # idle self-play notes: <= bed + 6 dB (section 4)
KNOCK_EMBED_CAP_DB = 16.0   # brief section 4 says +14 dB. The builder shipped
                            # +23 dB because +14 could not make criterion 9c's
                            # ">=6 dB localized 80-400 Hz transient" over the
                            # section-2-raised bed -- i.e. it bought knock
                            # salience with LEVEL, which is the "alarm" failure
                            # mode v2.2 exists to remove. v2.2 final buys it with
                            # CONTRAST instead (AmbientTheme._duck_block: the
                            # sustained layers dip DUCK_DEPTH_DB for ~0.45 s
                            # around the knock, a "room pause"), so the cap comes
                            # back down. +16 rather than the literal +14 is the
                            # one remaining 2 dB of deliberate slack, needed
                            # because the knock is a 30-80 ms modal decay whose
                            # RMS-over-a-window is far below its peak.
# "Room pause" duck around the failure knock -- see _duck_block().
DUCK_DEPTH_DB = 3.0         # brief-relative: verifier mandate says 2-3 dB
DUCK_ATTACK_S = 0.025
DUCK_HOLD_S = 0.15
DUCK_RELEASE_S = 0.275
DUCK_TOTAL_S = DUCK_ATTACK_S + DUCK_HOLD_S + DUCK_RELEASE_S   # 0.45 s
DUCK_SMOOTH_S = 0.006       # one-pole on the shaped envelope (see _duck_block)
NOTE_REVERB_SEND_DB = -6.0  # v2.2: note reverb send -6dB vs v2 (mostly direct)
NOTE_DIRECT_FRAC = 0.80     # notes are now mostly a direct signal, not 100% wet
NOTE_REVERB_FRAC = 0.15     # secondary send, ~-6dB vs a fully-wet v2 note
SUBBASS_CAL_DB = 18.0       # L5 sub-bass weather drone (same label-vs-mix
                            # calibration story as the stems)
STEM_CAL_DB = 18.0
STEM_DETUNE_CENTS = np.array([-9.0, 0.0, 9.0])
STEM_LP_HZ = 520.0
# ponytail: max gap between subagent-tagged events before we consider that
# subagent gone. SubagentStart/Stop are unreliable (observed 4 starts vs 13
# stops in a real daemon log), so presence is driven by ANY event carrying
# an agent_id instead of matched start/stop counting. Subagents can pause
# several seconds "thinking" between tool calls -- 12s is a guess at
# covering that without the stem lingering long after the subagent is
# actually done; tune if the stem cuts out mid-subagent or lingers too long.
SUBAGENT_PRESENCE_DECAY_S = 12.0
WHOOSH_CAL_DB = 30.0        # L2 bash-in-flight swell: the brief's -34 dBFS is
                            # again relative to its own -26 dBFS bed; raw, the
                            # swell measured 26 dB under this mix's 252 Hz band
                            # (i.e. not there at all)          # L4 subagent stems. The brief's "-32 dBFS" label is
                            # relative to its own "-26 dBFS bed"; against this
                            # mix's calibrated bed the raw -32 dB gain landed
                            # 23 dB under the bed, i.e. inaudible -- and an
                            # inaudible stem defeats the whole point of L4.

# L1b "air": the continuous broadband bed. BRIEF section 2 only specifies a
# pink rain bed that fades in with activity; measurement showed that without
# a permanently present broadband layer the mix cannot meet section 7 at all
# (slope -1.3 dB/oct vs -3..-6 target, 26 dB tonal peaks with nothing to
# raise the local spectral median, 20 dB 3s-RMS swings because sporadic
# grains/notes were carrying the whole energy). The air layer is pink noise
# tilted by two one-poles: flat per octave below AIR_TILT_LO, -6 dB/oct
# between the poles, -12 dB/oct above AIR_TILT_HI -> an octave-band fit of
# about -5 dB/oct, which is the middle of the brief's target range.
# v2.2 brightness lift (section 2): raise both tilt corners so more of the
# air layer's energy sits above ~350 Hz (v2 measured a 157 Hz mix centroid,
# far below the new [350, 1200] Hz target band).
# VERIFIER re-tune (v2.2 final). The air layer carries ~6 dB more power than
# every other layer combined, so the MIX centroid is essentially the AIR
# centroid pulled down by the low pad. Measured attribution on the realistic
# render's steady window: air 345 Hz @ -18 dB, low pad 165 Hz @ -24 dB, mix
# 309 Hz. The only knob with real authority over the centroid is therefore
# this layer's tilt corner, NOT the mid-bed voicing. Moving the lower corner
# 400 -> 1100 Hz (pink stays flat-per-octave up to 1100 instead of rolling
# off from 400) is what moves the mix from 309/341 Hz to 470-500 Hz.
#
# The upper corner comes DOWN at the same time (3800/6200 -> 2600/4000).
# Two reasons: (a) it keeps the octave-band slope inside 1a' (a corner-up-only
# change flattened it to -2.8 dB/oct, outside the -6..-3 window); (b) the
# drop grains live at 1.8-3.5 kHz, and lifting broadband bed energy INTO the
# drop band both masks the taps perceptually and degrades the onset detector
# the N1/N4/9b criteria depend on. Brightness goes in below the taps, not
# on top of them.
AIR_TILT_LO_HZ = 1100.0
AIR_TILT_HI_IDLE_HZ = 2600.0    # activity opens this up: brighter = busier
AIR_TILT_HI_ACTIVE_HZ = 4000.0
AIR_HP_HZ = 90.0                # v2.2: raised from v2's 34Hz (keeps sub-35Hz
                                # rumble out of L5's sub-bass slot). The builder's
                                # 115Hz was a second attempt at buying brightness
                                # here; with the tilt corner doing that job
                                # properly, 90Hz is preferable -- it puts back the
                                # 88-177Hz band energy whose absence was the
                                # largest single band-conformity deviation (1b').
AIR_DECORR = 0.62               # per-channel uncorrelated part -> r ~ 0.72
ACTIVITY_MED_TAU = 10.0         # bed-level activity envelope (see _render_air)
AIR_GLOOM_DEPTH = 0.5           # failure darkening applied to the air's top end
AIR_FLOOR_OFFSET_DB = -1.0      # air level relative to the L1 pad's state level
AIR_ACTIVITY_RANGE_DB = 5.0     # how far activity opens the air up
AIR_WASH_BOOST_RANGE_DB = 2.5   # v2.2 section 1: extra air gain from the
                                # discrete-drop-rate wash crossfade (heavy,
                                # sustained activity beyond the ~5/s cap point
                                # thickens the wash instead of adding drops)
AIR_WASH_BOOST_HZ = 300.0      # extra brightening applied under the same crossfade
# v2.3 air-bed shrink (round-2 decision + lit-review rec #4: "hard-lowpass
# and/or shrink the continuous air bed" -- Brain.fm/Eno/Endel corroboration
# that a continuous broadband layer underperforms structured material for
# sustained listening). Two independent levers, applied on top of the v2.2
# tuning above rather than reworking it (that tuning already fights a real
# tension between the section-7 centroid FLOOR (>=350Hz, needs high-frequency
# content somewhere) and the drop-grain band (1.8-3.5kHz, must not be masked)
# -- see the "VERIFIER re-tune" comment above; lowering AIR_TILT_HI further
# risks re-breaking that floor, so the level cut is the primary lever):
AIR_V23_LEVEL_CUT_DB = -4.0     # extra trim on top of AIR_CAL_DB, both idle and active
AIR_V23_HARD_CEILING_HZ = 2800.0  # hard lowpass ceiling on the air's own upper
                                   # tilt corner (was uncapped at 4000Hz active).
                                   # Calibrated against tools/analyze_render.py's
                                   # flatness/brightness criteria (rec #1-2): this
                                   # value gets both metrics to ~20-25% below the
                                   # measured v2.2 baseline while keeping the
                                   # section 7 centroid floor (>=350Hz) comfortably
                                   # clear on every render checked.

_PINK_POLES = ((0.99765, 0.0990460), (0.96300, 0.2965164), (0.57000, 1.0526913))


@dataclass
class AmbientConfig:
    """Every AmbientTheme tuning constant, gathered into one object.

    Fields mirror the module-level constants above 1:1 (same names, same
    values, same comments) -- those constants remain the single source of
    truth (several free functions outside AmbientTheme, e.g.
    _air_norm_factor, and the test suite reference them directly by name),
    this dataclass just gives AmbientTheme one object to own and pass
    around instead of reaching out to ~60 module globals. One instance
    (AMBIENT_CONFIG) is built at import time and handed to
    AmbientTheme.__init__ as `cfg`; there is no env/file loading, only the
    defaults below (which are literally the module constants).
    """

    # -- OU walk / bed shape --
    BED_OU_TAU: float = BED_OU_TAU
    BED_OU_EXCURSION_SCALE: float = BED_OU_EXCURSION_SCALE
    BED_CUTOFF_OU_SIGMA_OCT: float = BED_CUTOFF_OU_SIGMA_OCT
    BED_GAIN_OU_SIGMA_DB: float = BED_GAIN_OU_SIGMA_DB
    SHIMMER_OU_TAU: float = SHIMMER_OU_TAU
    SHIMMER_OU_SIGMA: float = SHIMMER_OU_SIGMA
    # v2.2 brightness lift (section 2): a soft C3+G3 mid layer, independent
    # from the low bed/shimmer stack, to help pull the mix's spectral
    # centroid up out of the "dark cave" register (v2 measured ~157 Hz)
    # toward the 350-1200 Hz target band without adding harshness.
    MIDLAYER_OU_TAU: float = MIDLAYER_OU_TAU
    MIDLAYER_OU_SIGMA: float = MIDLAYER_OU_SIGMA
    MIDLAYER_FREQS: np.ndarray = field(default_factory=lambda: MIDLAYER_FREQS)
    MIDLAYER_LP_HZ: float = MIDLAYER_LP_HZ
    # Low pad lowpass corner. v2 used a fixed 700 Hz base (a very muffled
    # pad -- a direct contributor to "dark cave"); v2.2 opens it so the saw
    # keeps a few audible harmonics. The OU walk (bed_cutoff_oct) still
    # rides on top and the hard ceiling keeps it out of "buzzy".
    BED_LP_BASE_HZ: float = BED_LP_BASE_HZ
    BED_LP_MAX_HZ: float = BED_LP_MAX_HZ
    SHIMMER_HARMONICS: tuple = SHIMMER_HARMONICS
    SHIMMER_SUS2_SLOT: int = SHIMMER_SUS2_SLOT
    SHIMMER_FIFTH_SLOT: int = SHIMMER_FIFTH_SLOT
    SHIMMER_SUS4_RATIO: float = SHIMMER_SUS4_RATIO
    VI_RATIO: float = _VI_RATIO   # C -> A below (I -> vi), the "darker room"
    SUPERSAW_VOICES: int = SUPERSAW_VOICES
    SUPERSAW_CENTS: np.ndarray = field(default_factory=lambda: SUPERSAW_CENTS)
    SUPERSAW_AMPS: np.ndarray = field(default_factory=lambda: SUPERSAW_AMPS)

    # -- mix calibration --
    # All *_CAL_DB values are trims that make each layer's nominal dBFS
    # label (the numbers in BRIEF-v2.md/v2.2.md section 2) correspond to
    # that layer's measured RMS at the output. They were set by
    # measurement, not by ear-guessing: see VERIFICATION.md "tuning
    # changes" and tools/lab.py.
    AMBIENT_WET_GAIN: float = AMBIENT_WET_GAIN
    AMBIENT_DRY_GAIN: float = AMBIENT_DRY_GAIN
    AMBIENT_MASTER_HEADROOM_DB: float = AMBIENT_MASTER_HEADROOM_DB
    BED_CAL_DB: float = BED_CAL_DB
    MIDLAYER_CAL_DB: float = MIDLAYER_CAL_DB
    AIR_CAL_DB: float = AIR_CAL_DB
    DROP_CAL_DB: float = DROP_CAL_DB
    DROP_AMP_SPREAD_DB: float = DROP_AMP_SPREAD_DB
    # v2.3 half-density (blind round 2 c3 knobs -- see _drop_rate_from_activity
    # and research/BRIEF-v2.3.md)
    DROP_MIN_GAP_S: float = DROP_MIN_GAP_S
    BURST_COALESCE_WINDOW_S: float = BURST_COALESCE_WINDOW_S
    DROP_RATE_SCALE: float = DROP_RATE_SCALE
    # v2.3 drop timbre (round-2/3 blind evidence + lit-review rec #3): one of
    # DROP_TIMBRES ("woodblock" default, "marimba"/"plink" A/B candidates,
    # "noise" = exact v2.2 legacy grain, kept for regression/A-B comparison).
    drop_timbre: str = "woodblock"
    # v2.4 state legibility (two-listener evidence -- research/BRIEF-v2.4.md):
    # one of DONE_CADENCE_MODES. "v24" (default): authentic cadence (melody
    # lands on the tonic + a simultaneous bass-register root note) followed
    # by a settled idle (deeper bed dip, sparser bloom). "v22": exact legacy
    # Stop handling, kept for regression/A-B comparison.
    done_cadence: str = "v24"
    SETTLED_HOLD_S: float = SETTLED_HOLD_S
    SETTLED_BED_DB: float = SETTLED_BED_DB
    SETTLED_BED_TAU_S: float = SETTLED_BED_TAU_S
    SETTLED_BLOOM_RATE_SCALE: float = SETTLED_BLOOM_RATE_SCALE
    NOTE_EMBED_CAP_DB: float = NOTE_EMBED_CAP_DB
    NOTE_EMBED_CAP_IDLE_DB: float = NOTE_EMBED_CAP_IDLE_DB
    KNOCK_EMBED_CAP_DB: float = KNOCK_EMBED_CAP_DB
    DUCK_DEPTH_DB: float = DUCK_DEPTH_DB
    DUCK_ATTACK_S: float = DUCK_ATTACK_S
    DUCK_HOLD_S: float = DUCK_HOLD_S
    DUCK_RELEASE_S: float = DUCK_RELEASE_S
    DUCK_TOTAL_S: float = DUCK_TOTAL_S
    DUCK_SMOOTH_S: float = DUCK_SMOOTH_S
    NOTE_REVERB_SEND_DB: float = NOTE_REVERB_SEND_DB
    NOTE_DIRECT_FRAC: float = NOTE_DIRECT_FRAC
    NOTE_REVERB_FRAC: float = NOTE_REVERB_FRAC
    SUBBASS_CAL_DB: float = SUBBASS_CAL_DB
    STEM_CAL_DB: float = STEM_CAL_DB
    STEM_DETUNE_CENTS: np.ndarray = field(default_factory=lambda: STEM_DETUNE_CENTS)
    STEM_LP_HZ: float = STEM_LP_HZ
    WHOOSH_CAL_DB: float = WHOOSH_CAL_DB

    # -- L1b "air" continuous shaped-noise bed --
    AIR_TILT_LO_HZ: float = AIR_TILT_LO_HZ
    AIR_TILT_HI_IDLE_HZ: float = AIR_TILT_HI_IDLE_HZ
    AIR_TILT_HI_ACTIVE_HZ: float = AIR_TILT_HI_ACTIVE_HZ
    AIR_HP_HZ: float = AIR_HP_HZ
    AIR_DECORR: float = AIR_DECORR
    ACTIVITY_MED_TAU: float = ACTIVITY_MED_TAU
    AIR_GLOOM_DEPTH: float = AIR_GLOOM_DEPTH
    AIR_FLOOR_OFFSET_DB: float = AIR_FLOOR_OFFSET_DB
    AIR_ACTIVITY_RANGE_DB: float = AIR_ACTIVITY_RANGE_DB
    AIR_WASH_BOOST_RANGE_DB: float = AIR_WASH_BOOST_RANGE_DB
    AIR_WASH_BOOST_HZ: float = AIR_WASH_BOOST_HZ
    # v2.3 air-bed shrink (round-2 decision + lit-review rec #4)
    AIR_V23_LEVEL_CUT_DB: float = AIR_V23_LEVEL_CUT_DB
    AIR_V23_HARD_CEILING_HZ: float = AIR_V23_HARD_CEILING_HZ
    PINK_POLES: tuple = _PINK_POLES


# Single instance, defaults only (no env/file loading) -- see AmbientConfig
# docstring. Passed into AmbientTheme.__init__ as `cfg`.
AMBIENT_CONFIG = AmbientConfig()


def _air_norm_factor(sr, hi_cut, hp_ba):
    """RMS normalisation for the air chain (white -> pink -> LP_lo -> LP_hi ->
    HP), computed analytically from the cascaded frequency response so it is
    deterministic and seed-independent (no RNG, no warm-up render)."""
    if not _HAVE_SCIPY:
        return 1.0
    nfft = 4096
    w = np.linspace(0, math.pi, nfft, endpoint=False)
    z = np.exp(-1j * w)
    h = np.full(nfft, 0.1848, dtype=np.complex128)
    for pole, gain in _PINK_POLES:
        h += gain / (1.0 - pole * z)
    for b, a in (_onepole_lp_coeffs(AIR_TILT_LO_HZ, sr),
                 _onepole_lp_coeffs(hi_cut, sr),
                 hp_ba):
        num = np.polyval(np.asarray(b)[::-1], z)
        den = np.polyval(np.asarray(a)[::-1], z)
        h *= num / den
    power = float(np.mean(np.abs(h) ** 2))
    return 1.0 / math.sqrt(max(power, 1e-30))


def _db_to_lin(db):
    return 10.0 ** (db / 20.0)


class StemLayer:
    """L4 subagent "stem" pads: shimmer-pad + low-fifth-drone voices that
    fade in/out with subagent presence (brief section 5). Owns its own gain/
    phase/pan state. apply_lp_stage is a callback into AmbientTheme's shared
    _lp_zi filter-state dict (also used by BedLayer and the master bus) --
    a genuinely shared resource, passed in rather than duplicated."""

    def __init__(self, rng, apply_lp_stage, sr, cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self.apply_lp_stage = apply_lp_stage
        self.sr = sr
        self.subagent_refcount = 0
        self._presence = {}  # agent_id -> last_seen t (virtual clock)
        self.stem1_gain = Slew(0.0, tau=1.7)   # ~5s equal-power-ish fade-in
        self.stem2_gain = Slew(0.0, tau=1.7)
        self.stem_phase = rng.random(2)
        self.stem_pan = 0.3
        self._stem_pan_toggle = False

    def render(self, dry, n, dt):
        self.stem1_gain.step(dt)
        self.stem2_gain.step(dt)
        if self.stem1_gain.value < 1e-4 and self.stem2_gain.value < 1e-4:
            return
        t = np.arange(n) / self.sr
        if self.stem1_gain.value > 1e-4:
            # 3-voice detuned saw ("supersaw an octave above bed") through the
            # same kind of 2-pole lowpass the L1 pad uses. The v2 builder's
            # single NAIVE saw with no filter at all put a 32 dB spectral
            # spike at 9.4 kHz (harmonic 36 of C4) into an otherwise -4 dB/oct
            # mix -- audibly buzzy, and a section 7 item 5 failure whenever a
            # subagent was running.
            ph0 = self.stem_phase[0]
            freqs = ROOT_C4 * (2.0 ** (STEM_DETUNE_CENTS / 1200.0))
            frac = np.mod(ph0 + t[:, None] * freqs[None, :], 1.0)
            saw = np.sum(2.0 * frac - 1.0, axis=1) / len(freqs)
            self.stem_phase[0] = float(np.mod(ph0 + ROOT_C4 * n / self.sr, 1.0))
            saw = self.apply_lp_stage(saw, "stem1_lp1", self.cfg.STEM_LP_HZ)
            saw = self.apply_lp_stage(saw, "stem1_lp2", self.cfg.STEM_LP_HZ)
            gain = _db_to_lin(-32.0 + self.cfg.STEM_CAL_DB) * self.stem1_gain.value
            pan = self.stem_pan
            stereo = _mono_to_stereo(saw.astype(np.float64), pan=pan)
            dry += stereo * gain
        if self.stem2_gain.value > 1e-4:
            ph0 = self.stem_phase[1]
            phase = ph0 + ROOT_G2 * t / 1.0
            sig = np.sin(2 * np.pi * phase)
            self.stem_phase[1] = float(np.mod(ph0 + ROOT_G2 * n / self.sr, 1.0))
            gain = _db_to_lin(-34.0 + self.cfg.STEM_CAL_DB) * self.stem2_gain.value
            stereo = _mono_to_stereo(sig.astype(np.float64), pan=-self.stem_pan)
            dry += stereo * gain

    # -- handle_event handlers (called from AmbientTheme.handle_event) ------

    def note_presence(self, agent_id, t):
        """Called on EVERY event that carries a non-empty agent_id, not just
        SubagentStart/Stop -- a plain PreToolUse from inside a subagent keeps
        the stem alive even when SubagentStart never arrived (see
        SUBAGENT_PRESENCE_DECAY_S)."""
        if not agent_id:
            return
        self._presence[agent_id] = t
        self._apply_active_count(t)

    def recheck_presence(self, t):
        """Re-evaluate presence/decay without adding a new agent_id -- called
        on events that don't carry one, so a stem fade-out happens on
        schedule rather than only on the next tagged event."""
        self._apply_active_count(t)

    def _active_presence_count(self, t):
        expired = [aid for aid, seen in self._presence.items()
                   if t - seen > SUBAGENT_PRESENCE_DECAY_S]
        for aid in expired:
            del self._presence[aid]
        return len(self._presence)

    def _apply_active_count(self, t):
        """Effective active count = max(presence-timer count, legacy
        refcount) so sessions with no agent_id (older Claude Code, or a
        manual SubagentStart/Stop-only path) still work."""
        count = max(self._active_presence_count(t), self.subagent_refcount)
        if count >= 1:
            self.stem1_gain.tau = 1.7
            self.stem1_gain.target = 1.0
        else:
            self.stem1_gain.tau = 2.7
            self.stem1_gain.target = 0.0
        if count >= 2:
            self.stem2_gain.tau = 1.7
            self.stem2_gain.target = 1.0
        else:
            self.stem2_gain.tau = 2.7
            self.stem2_gain.target = 0.0

    def handle_subagent_start(self, t=0.0):
        self.subagent_refcount += 1
        self._stem_pan_toggle = not self._stem_pan_toggle
        self.stem_pan = 0.3 if self._stem_pan_toggle else -0.3
        self._apply_active_count(t)

    def handle_subagent_stop(self, agent_id=None, t=0.0):
        self.subagent_refcount = max(0, self.subagent_refcount - 1)
        if agent_id:
            # Clean stop WITH agent_id: drop it immediately for a fast fade
            # instead of waiting out the presence decay.
            self._presence.pop(agent_id, None)
        self._apply_active_count(t)


class WeatherLayer:
    """L5 context-pressure sub-bass drone (brief section 8). Owns its own
    phase/gain state; fill_smooth/fail_penalty_slew (which this layer's
    target level depends on) are driven by the shared master-lowpass update
    (AmbientTheme._update_master_lowpass, a mix-bus concern that also
    touches non-weather buses), so those stay on AmbientTheme and this
    layer's fill_smooth value is passed in at render time."""

    def __init__(self, sr, cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self.sr = sr
        self.subbass_phase = 0.0
        self.subbass_gain = Slew(-80.0, tau=0.5)

    def render(self, n, dt, fill_smooth_value):
        f = fill_smooth_value
        target_db = -80.0
        if f > 0.5:
            # frac**0.7 rather than frac: with a linear ramp the drone is
            # still 10 dB under its nominal level at fill 0.9, i.e. inaudible
            # under the bed's own low-frequency rumble for the entire range a
            # real session spends most of its time in.
            frac = min(1.0, (f - 0.5) / 0.5) ** 0.7
            # Range -55 -> -30 dBFS rather than -80 -> -30. The bottom 25 dB
            # of the brief's range is spent below this mix's own 22-44 Hz
            # rumble floor, so a linear ramp from -80 meant the drone only
            # became audible in the last few percent of fill -- i.e. never,
            # for the fill values a real session actually sits at.
            target_db = -55.0 + frac * (-30.0 - (-55.0))
        self.subbass_gain.target = target_db
        self.subbass_gain.step(dt)
        if self.subbass_gain.value < -70.0:
            return np.zeros((n, 2))
        t = np.arange(n) / self.sr
        phase = self.subbass_phase + ROOT_C1 * t / 1.0
        sig = np.sin(2 * np.pi * phase) + 0.3 * np.sin(4 * np.pi * phase)
        self.subbass_phase = float(np.mod(self.subbass_phase + ROOT_C1 * n / self.sr, 1.0))
        gain = _db_to_lin(self.subbass_gain.value + self.cfg.SUBBASS_CAL_DB)
        out = np.zeros((n, 2))
        out[:, 0] = sig * gain
        out[:, 1] = sig * gain
        return out


class BedLayer:
    """L1 bed pad: supersaw + additive shimmer + v2.2 C3/G3 mid-register
    warmth layer (brief section 2). Owns the bed's own state (level, root
    shading, sus2/sus4 recoloring, failure-shading flags) and draws from the
    render-thread RNG passed in at construction (shared with the other
    continuous layers, same call-order position render_block always called
    the bed render from). bed_level_db/bed_root_ratio are read by
    AmbientTheme (note/knock embedding-cap calc) and by RainLayer's air
    sub-layer (bed_level_db only) -- legitimate cross-layer READS of this
    layer's own public state, not a back-reference out of it."""

    def __init__(self, rng, apply_lp_stage, sr, done_cadence="v24", cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self._rng = rng
        self._apply_lp_stage = apply_lp_stage
        self.sr = sr
        self.done_cadence = done_cadence

        self.bed_phase = np.zeros((2, 2, SUPERSAW_VOICES))  # [bed(C2/C3), ch(L/R), voice]
        self.bed_phase[:] = rng.random(self.bed_phase.shape)
        self.bed_lr_cents = np.array([-1.0, 1.0])
        # Precomputed (28,) voice frequencies and the (28,2) mixdown matrix so
        # the whole supersaw is one matmul per block (see render).
        freqs = np.zeros((2, 2, SUPERSAW_VOICES))
        mix = np.zeros((2, 2, SUPERSAW_VOICES, 2))
        for bed_i, f0 in enumerate((ROOT_C2, ROOT_C3)):
            bed_gain = 1.0 if bed_i == 0 else 0.35
            for ch in range(2):
                for v in range(SUPERSAW_VOICES):
                    cents = SUPERSAW_CENTS[v] + self.bed_lr_cents[ch]
                    freqs[bed_i, ch, v] = f0 * (2.0 ** (cents / 1200.0))
                    mix[bed_i, ch, v, ch] = SUPERSAW_AMPS[v] * bed_gain / SUPERSAW_VOICES
        self._bed_freqs = freqs.reshape(-1)
        self._bed_mix = mix.reshape(-1, 2)
        self.bed_cutoff_oct = 0.0
        self.bed_gain_db = 0.0
        self.shimmer_phase = rng.random((len(SHIMMER_HARMONICS), 2))
        self.shimmer_amp_x = np.zeros(len(SHIMMER_HARMONICS))  # OU state, multiplicative +-30%
        self.bed_level_db = Slew(-80.0, tau=1.5)
        self.bed_root_ratio = Slew(1.0, tau=3.0)   # I <-> vi shading
        self.sus2_amt = Slew(0.0, tau=1.2)         # Notification recoloring
        self.sus4_amt = Slew(0.0, tau=4.0)         # fill > 0.85 recoloring
        self.holding_breath_until = None
        self.easing_after_stop_until = None
        self.sus2_until = None
        # v2.2 brightness-lift mid layer (C3+G3): two independent OU amplitude
        # walks, same tambura-shimmer treatment as the low additive layer, but
        # its own state so it can be gated/leveled independently.
        # sized from MIDLAYER_FREQS, not a hard-coded 2: a 3-voice mid layer
        # used to render as SILENCE (shape mismatch -> exception -> the
        # render_block fault handler zeroes the block) instead of erroring.
        self.midlayer_phase = rng.random((len(MIDLAYER_FREQS), 2))
        self.midlayer_amp_x = np.zeros(len(MIDLAYER_FREQS))

        # PostToolUseFailure shading: bass_shaded_vi/failed_tool are read
        # only here (bed_root_ratio's I->vi shade target); handle_event sets
        # them via handle_failure/handle_recovery/handle_stop below.
        self.bass_shaded_vi = False
        self.failed_tool = None

    def _bed_target_db(self, t, last_event_t, session_start_t):
        # v2.2 section 2 "bed presence = the control anchor": listener
        # evidence was "dark cave"/"isolated"/"lost" -- a bed that all but
        # disappears at idle reads as a void, not a machine quietly running.
        # Active target raised so the bed is clearly audible under
        # everything; idle raised too so it stays present on earphones.
        if self.holding_breath_until is not None and t < self.holding_breath_until:
            return -33.0, 2.0
        if self.done_cadence == "v22":
            if self.easing_after_stop_until is not None and t < self.easing_after_stop_until:
                return -33.0, 5.0
        elif self.easing_after_stop_until is not None and last_event_t < self.easing_after_stop_until:
            # v2.4 "settled" idle (research/BRIEF-v2.4.md): deeper and longer
            # than the legacy 6s/-33dB hold, which fell back to the ordinary
            # idle ladder (-30dB, same as "idle but recently working") after
            # only 6s -- the opposite of a distinct "waiting for you" state.
            # Gated on last_event_t (not a hard `t <` timeout): a fixed timer
            # that falls through to the LOUDER idle ladder is the same
            # "gets louder while waiting" defect this cadence work fixed
            # elsewhere, just delayed to t=SETTLED_HOLD_S post-Stop. Settled
            # now holds for as long as the agent stays idle since Stop, and
            # only exits when a genuinely new event advances last_event_t.
            return self.cfg.SETTLED_BED_DB, self.cfg.SETTLED_BED_TAU_S
        since_start = t - (session_start_t or 0.0)
        idle_dur = t - last_event_t
        if since_start < 3.0:
            return -30.0, 1.0
        if idle_dur < 90.0:
            return -30.0, 1.5
        elif idle_dur < 600.0:
            return -36.0, 10.0
        else:
            return -40.0, 15.0

    def render(self, dry, n, dt, t, last_event_t, session_start_t, fill_smooth_value):
        sr = self.sr
        rng = self._rng
        self.bed_cutoff_oct = _ou_step(self.bed_cutoff_oct, 0.0, BED_OU_TAU, BED_CUTOFF_OU_SIGMA_OCT, dt, rng)
        self.bed_gain_db = _ou_step(self.bed_gain_db, 0.0, BED_OU_TAU, BED_GAIN_OU_SIGMA_DB, dt, rng)
        for k in range(len(SHIMMER_HARMONICS)):
            self.shimmer_amp_x[k] = _ou_step(self.shimmer_amp_x[k], 0.0, SHIMMER_OU_TAU, SHIMMER_OU_SIGMA, dt, rng)

        target_db, tau = self._bed_target_db(t, last_event_t, session_start_t)
        self.bed_level_db.target = target_db
        self.bed_level_db.tau = tau
        self.bed_level_db.step(dt)

        tv = np.arange(n) / sr
        cutoff = max(150.0, min(self.cfg.BED_LP_MAX_HZ, self.cfg.BED_LP_BASE_HZ * (2.0 ** self.bed_cutoff_oct)))

        # Root shading: PostToolUseFailure pulls the bed root C -> A (I -> vi,
        # BRIEF section 3 "the room got darker"), released on Stop / on the
        # first successful tool use after the failure. Glided (Slew tau 3s) so
        # it reads as a slow recoloring, never as a pitch bend.
        self.bed_root_ratio.target = _VI_RATIO if self.bass_shaded_vi else 1.0
        self.bed_root_ratio.step(dt)
        root_mult = self.bed_root_ratio.value

        # Vectorized supersaw: all 2 beds x 2 channels x 7 voices in one
        # (n, 28) phase matrix + a single (28, 2) mixdown matmul. The
        # per-voice python loop this replaces was ~0.5ms/block.
        freqs = self._bed_freqs * root_mult                    # (28,)
        ph = self.bed_phase.reshape(-1)                        # (28,)
        frac = np.mod(ph[None, :] + tv[:, None] * freqs[None, :], 1.0)
        stereo_saw = (2.0 * frac - 1.0) @ self._bed_mix        # (n, 2)
        self.bed_phase = np.mod(ph + freqs * n / sr, 1.0).reshape(self.bed_phase.shape)

        # Additive shimmer (tambura-style overtone walk). sus4 recoloring
        # above fill 0.85 swaps the 3rd-harmonic G for a slightly flatter
        # partial -- see _shimmer_ratios().
        harm, gate = self._shimmer_ratios(dt, fill_smooth_value, t)
        amp = (1.0 / (harm * harm)) * gate * (
            1.0 + 0.9 * np.clip(self.shimmer_amp_x, -1.0, 1.0))
        sfreq = harm * ROOT_C2 * root_mult
        sph = self.shimmer_phase                               # (K,2)
        phase = sph[None, :, :] + tv[:, None, None] * sfreq[None, :, None]
        shimmer = np.einsum("nkc,k->nc", np.sin(2 * np.pi * phase), amp * 0.5)
        self.shimmer_phase = np.mod(sph + (sfreq * n / sr)[:, None], 1.0)

        bed_raw = stereo_saw * 0.6 + shimmer * 0.4

        # 2nd-order lowpass (cascade of 2 one-poles => critically-damped-ish, Q<=1)
        for ch in range(2):
            key1, key2 = f"bed_lp1_{ch}", f"bed_lp2_{ch}"
            col = bed_raw[:, ch]
            col = self._apply_lp_stage(col, key1, cutoff)
            col = self._apply_lp_stage(col, key2, cutoff)
            bed_raw[:, ch] = col

        gain = _db_to_lin(self.bed_level_db.value + self.bed_gain_db + self.cfg.BED_CAL_DB)
        dry += bed_raw * gain

        # v2.2 brightness lift (section 2): a soft, independent C3+G3 mid
        # layer -- "bed gets a mid-register warm layer" -- to help raise the
        # full-mix spectral centroid out of the 157 Hz "dark cave" register
        # measured in v2, toward the [350, 1200] Hz v2.2 target band. Kept on
        # its own gain trim (MIDLAYER_CAL_DB) and OU walk so it doesn't
        # disturb the already-balanced low pad/shimmer stack.
        for k in range(len(MIDLAYER_FREQS)):
            self.midlayer_amp_x[k] = _ou_step(
                self.midlayer_amp_x[k], 0.0, MIDLAYER_OU_TAU, MIDLAYER_OU_SIGMA, dt, rng)
        mid_freqs = MIDLAYER_FREQS * root_mult
        mid_amp = 1.0 + 0.9 * np.clip(self.midlayer_amp_x, -1.0, 1.0)
        mph = self.midlayer_phase                              # (K,2)
        mphase = mph[None, :, :] + tv[:, None, None] * mid_freqs[None, :, None]
        midlayer = np.einsum("nkc,k->nc", np.sin(2 * np.pi * mphase), mid_amp * 0.5)
        self.midlayer_phase = np.mod(mph + (mid_freqs * n / sr)[:, None], 1.0)
        for ch in range(2):
            midlayer[:, ch] = self._apply_lp_stage(midlayer[:, ch], f"midlayer_lp_{ch}",
                                                   self.cfg.MIDLAYER_LP_HZ)
        mid_gain = _db_to_lin(self.bed_level_db.value + self.cfg.MIDLAYER_CAL_DB)
        dry += midlayer * mid_gain

    def _shimmer_ratios(self, dt, fill_smooth_value, t):
        """Harmonic ratios for the additive layer, including the two glided
        recolorings from the brief: sus4 above fill 0.85 (the 5th slides to a
        4th) and sus2 during a Notification hold (a D partial fades in).
        Ratios are glided rather than switched so the sine phases stay
        continuous -- a hard ratio switch would click."""
        self.sus4_amt.target = 1.0 if fill_smooth_value > 0.85 else 0.0
        self.sus4_amt.step(dt)
        active_sus2 = self.sus2_until is not None and t < self.sus2_until
        self.sus2_amt.target = 1.0 if active_sus2 else 0.0
        self.sus2_amt.step(dt)
        r = np.array(SHIMMER_HARMONICS, dtype=np.float64)
        r[SHIMMER_FIFTH_SLOT] = 3.0 + self.sus4_amt.value * (SHIMMER_SUS4_RATIO - 3.0)
        gate = np.ones(len(SHIMMER_HARMONICS))
        gate[SHIMMER_SUS2_SLOT] = self.sus2_amt.value
        return r, gate

    # -- handle_event per-branch handlers (called from AmbientTheme.handle_event,
    # in the exact original branch order) -----------------------------------

    def handle_posttooluse_recovery(self, tool_name):
        """PostToolUse's failure-recovery clear: 'success = the tool that
        failed now works' (BRIEF section 3). Returns True if it cleared the
        shading (so the caller can also clear fail_penalty, which stays on
        AmbientTheme -- it drives the master lowpass, not just the bed)."""
        if (self.failed_tool is None or tool_name == self.failed_tool):
            self.bass_shaded_vi = False
            self.failed_tool = None
            return True
        return False

    def handle_failure(self, tool_name):
        self.bass_shaded_vi = True
        self.failed_tool = tool_name

    def handle_stop(self, t):
        self.failed_tool = None
        self.bass_shaded_vi = False
        hold_s = 6.0 if self.done_cadence == "v22" else SETTLED_HOLD_S
        self.easing_after_stop_until = t + hold_s

    def handle_notification(self, t):
        self.holding_breath_until = t + 2.5
        self.sus2_until = t + 3.0


class RainLayer:
    """L2 rain grain stream (per-event drops + activity-driven Poisson bed)
    plus the L1b "air" continuous shaped-noise bed the brief folds into the
    same rain-gets-closer information channel (see render_air's own
    comment). Owns its own drop bank, pacing/coalescing clocks, whoosh, and
    air-bed state; draws from the render-thread RNG passed in at
    construction (shared with the other continuous layers, in the same
    call-order position render_block always called rain/air from).
    dispatch_drop/spawn_one_drop/trigger_event_drop are public: they are
    called directly from AmbientTheme.handle_event (an ingress thread,
    passing its own _ingress_rng) and monkeypatched directly by the test
    suite."""

    def __init__(self, rng, queue_voice, sr, rain_enabled, drop_timbre="woodblock", cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self._rng = rng
        self._queue_voice = queue_voice
        self.sr = sr
        self.rain_enabled = rain_enabled

        self.drop_bank = _build_drop_bank(rng, sr, timbre=drop_timbre)
        self.rain_next_dt = 0.05
        self.rain_bed_gain_db = Slew(-80.0, tau=2.0)
        self._pink_zi = None
        self.whoosh_active = False
        self.whoosh_gain = Slew(0.0, tau=1.6)
        self._whoosh_bp = None
        if _HAVE_SCIPY:
            nyq = sr / 2.0
            try:
                self._whoosh_bp = _sp_signal.butter(2, [150.0 / nyq, 400.0 / nyq], btype="band")
            except Exception:
                self._whoosh_bp = None
        self._whoosh_zi = None
        # v2.2 pacing overhaul (section 1): the discrete-drop rate itself is
        # slewed (tau >= 2s) with hysteresis so activity bursts can't lurch
        # the texture; onsets from ANY source (Poisson rain clock or a
        # per-event "instant twitch") share one 150ms pacing floor; and
        # per-event triggers additionally coalesce inside a 250ms window into
        # one weighted drop instead of stacking N drops per event.
        self.rain_rate = Slew(0.0, tau=RATE_SLEW_TAU_S)
        self._last_any_onset_t = -999.0        # global 150ms pacing floor
        self._last_event_onset_t = -999.0      # 250ms burst-coalescing clock
        self._event_coalesce_bonus_db = 0.0
        self.wash_excess = 0.0                  # activity beyond the ~5/s crossfade point

        # L1b air state
        self.air_gain_db = Slew(-80.0, tau=1.5)
        self.air_cut_hz = Slew(self.cfg.AIR_TILT_HI_IDLE_HZ, tau=2.5)
        self._air_zi = {}
        if _HAVE_SCIPY:
            self._air_hp = _sp_signal.butter(2, AIR_HP_HZ / (sr / 2.0), btype="high")
        else:  # pragma: no cover
            self._air_hp = ([1.0], [1.0])
        self._air_norm = _air_norm_factor(sr, self.cfg.AIR_TILT_HI_IDLE_HZ, self._air_hp)

    def spawn_one_drop(self, rng, cls, fill_smooth_value, extra_gain_db=0.0):
        """Render and queue exactly one drop voice (the coalesced unit --
        brief-v2.2 section 1: "1 event = 1 drop (weighted)"). `extra_gain_db`
        carries any accumulated burst-coalescing weight."""
        idx = int(rng.integers(0, len(self.drop_bank)))
        base = self.drop_bank[idx]
        # Register split by tool class (brief-v2.2 section 3): read ->
        # brighter/quieter; exec -> lower center freq (x0.7) + slightly
        # longer (achieved by the same resampling stretching duration).
        pitch_mult, gain_db = 1.0, 0.0
        if cls == CLASS_READ:
            # -2 dB, not v2's -4: "read -> brighter/quieter" (brief section 3)
            # still holds relative to write/exec, but the pitch shift already
            # moves a read tap's energy up out of the bed's densest region, and
            # -4 on top of that put the quiet tail of the class under the bed.
            pitch_mult, gain_db = 1.3, -2.0
        elif cls == CLASS_EXEC:
            pitch_mult = 0.7
        amp_db = rng.uniform(-self.cfg.DROP_AMP_SPREAD_DB, self.cfg.DROP_AMP_SPREAD_DB)
        gain = _db_to_lin(gain_db + amp_db + extra_gain_db + self.cfg.DROP_CAL_DB)
        n = len(base)
        if abs(pitch_mult - 1.0) > 1e-6 and n > 4:
            new_n = max(4, int(n / pitch_mult))
            idxs = np.linspace(0, n - 1, new_n)
            sig = np.interp(idxs, np.arange(n), base)
        else:
            sig = base.copy()
        if fill_smooth_value > 0.85 and _HAVE_SCIPY:
            cutoff = 4500.0 + (2000.0 - 4500.0) * min(1.0, (fill_smooth_value - 0.85) / 0.15)
            b, a = _onepole_lp_coeffs(cutoff, self.sr)
            sig = _sp_signal.lfilter(b, a, sig)
        # brief-v2.2 section 5: per-drop pan constrained to +-0.35 (was full
        # width +-1.0 -- listener evidence: "left/right difference").
        pan = rng.uniform(-DROP_PAN_LIMIT, DROP_PAN_LIMIT)
        stereo = _mono_to_stereo((sig * gain).astype(np.float64), pan=pan)
        self._queue_voice({"buf": stereo, "pos": 0, "bus": "reverb"})

    def dispatch_drop(self, rng, cls, t, fill_smooth_value, extra_gain_db=0.0):
        """Actually spawn a drop onset, enforcing the global pacing floor
        (brief-v2.2 section 1: "min inter-drop gap 150 ms") across BOTH the
        Poisson rain clock and per-event triggers. Onsets closer than the
        floor are silently absorbed rather than spawned -- this, combined
        with the compressive rate map, is what keeps N1 (never >7 onsets/s)
        satisfied even under an a=1.0 flood."""
        if t - self._last_any_onset_t < self.cfg.DROP_MIN_GAP_S:
            return False
        self.spawn_one_drop(rng, cls, fill_smooth_value, extra_gain_db=extra_gain_db)
        self._last_any_onset_t = t
        return True

    def trigger_event_drop(self, rng, cls, t, fill_smooth_value):
        """Per-tool-event "instant twitch" drop trigger (handle_event side).
        Brief-v2.2 section 1 burst coalescing: events arriving < 250 ms apart
        merge into ONE weighted drop instead of stacking one drop per event."""
        if t - self._last_event_onset_t < self.cfg.BURST_COALESCE_WINDOW_S:
            self._event_coalesce_bonus_db = min(
                BURST_COALESCE_MAX_DB, self._event_coalesce_bonus_db + BURST_COALESCE_STEP_DB)
            return
        # v2.2 VERIFIER fix: dispatch_drop can REFUSE (the 150 ms global
        # pacing floor, which the Poisson rain clock shares). The previous
        # version ignored the return value and cleared the accumulated
        # coalescing weight and advanced the 250 ms clock anyway -- so an
        # event that landed inside another onset's pacing floor produced NO
        # drop AND silently threw away every merged event's weight with it.
        # Under a flood that is exactly the case that fires most often. On a
        # refusal, treat the event as merged instead: keep the weight (and
        # add this event's own step) for the next drop that does get through.
        if self.dispatch_drop(rng, cls, t, fill_smooth_value,
                               extra_gain_db=self._event_coalesce_bonus_db):
            self._event_coalesce_bonus_db = 0.0
            self._last_event_onset_t = t
        else:
            self._event_coalesce_bonus_db = min(
                BURST_COALESCE_MAX_DB, self._event_coalesce_bonus_db + BURST_COALESCE_STEP_DB)

    def render_rain(self, dry, n, dt, t, activity, current_class, fill_smooth_value):
        sr = self.sr
        # v2.2 section 1 pacing overhaul. v2's rate map (2 + 38*a**1.3, up to
        # ~40 drops/s) was the direct cause of "too fast / losing control" --
        # well past the ~4-6/s point where auditory counting breaks down. The
        # replacement is compressive (log) and hard-capped at 6 discrete
        # drops/s; the rate parameter itself is slewed (tau >= 2s) with
        # hysteresis so a burst can't lurch the texture.
        a_raw = max(0.0, activity)   # unclamped: can exceed 1 under a flood
        a = min(1.0, a_raw)
        raw_rate = _drop_rate_from_activity(a, scale=self.cfg.DROP_RATE_SCALE)
        if abs(raw_rate - self.rain_rate.target) > RATE_HYSTERESIS:
            self.rain_rate.target = raw_rate
        self.rain_rate.step(dt)
        rate = max(1e-4, self.rain_rate.value)
        # Discrete->wash crossfade: activity beyond the point where the
        # compressive map reaches ~5/s (of the 6/s cap) does not add more
        # drops -- it thickens the continuous wash bed instead (render_air
        # reads self.wash_excess). Uses the UNCLAMPED activity so a sustained
        # flood (a_raw >> 1) keeps growing the wash even once the discrete
        # rate itself is pinned at the cap.
        self.wash_excess = max(0.0, a_raw - WASH_CROSSFADE_A)
        if self.rain_enabled and rate > 0:
            block_dur = n / sr
            while self.rain_next_dt <= block_dur:
                self.dispatch_drop(self._rng, current_class, t, fill_smooth_value)
                interval = -math.log(max(self._rng.random(), 1e-12)) / rate
                self.rain_next_dt += interval
            self.rain_next_dt -= block_dur
            if self.rain_next_dt < 0:
                self.rain_next_dt = 0.0
        else:
            self.rain_next_dt = max(self.rain_next_dt, n / sr)

        # NOTE: the brief's separate "light pink-noise rain bed that fades in
        # with activity" is folded into the L1b air layer (render_air), whose
        # level AND brightness both track activity -- the same information
        # channel, but as one always-present generator instead of a second
        # noise source appearing out of nowhere at a=0+. Two independent pink
        # generators also cannot share _pink_zi, and a bed that switches on at
        # -80 dB is exactly the kind of discontinuity section 7 item 8 flags.

        # bash whoosh: 150-400Hz filtered-noise swell while a Bash tool is in flight
        self.whoosh_gain.step(dt)
        if _HAVE_SCIPY and self._whoosh_bp is not None and self.whoosh_gain.value > 1e-4:
            b, a_ = self._whoosh_bp
            zi = self._whoosh_zi
            if zi is None:
                zi = _sp_signal.lfiltic(b, a_, [0.0])
            noise = self._rng.standard_normal(n)
            filtered, zf = _sp_signal.lfilter(b, a_, noise, zi=zi)
            self._whoosh_zi = zf
            gain = _db_to_lin(-34.0 + self.cfg.WHOOSH_CAL_DB) * self.whoosh_gain.value
            dry[:, 0] += filtered * gain
            dry[:, 1] += filtered * gain

    def pink_noise_block(self, white):
        """Paul Kellet 3-pole pink noise approximation, vectorized via lfilter.

        `white` may be (n,) or (n, k); the filter state adapts to its shape on
        first use, so the same helper serves both the mono rain bed and the
        3-channel air generator."""
        poles = ((0.99765, 0.0990460), (0.96300, 0.2965164), (0.57000, 1.0526913))
        shape = None if white.ndim == 1 else (1,) + white.shape[1:]
        if self._pink_zi is None or self._pink_zi[0] is Ellipsis:
            self._pink_zi = [None, None, None]
        total = white * 0.1848
        for i, (pole, gain) in enumerate(poles):
            zi = self._pink_zi[i]
            if zi is None or (shape is None) != (np.ndim(zi) == 1):
                zi = np.zeros(1) if shape is None else np.zeros(shape)
            y, zf = _sp_signal.lfilter([gain], [1.0, -pole], white, zi=zi, axis=0)
            self._pink_zi[i] = zf
            total = total + y
        return total

    def render_air(self, n, dt, activity_med, bed_level_db_value, fail_penalty_slew_value):
        """L1b continuous shaped-noise bed. Returns (n,2).

        One pink generator feeding three decorrelated streams: a common
        (centre) stream plus one independent stream per channel at AIR_DECORR,
        which fixes the interchannel correlation at a known value inside the
        brief's 0.3-0.9 window instead of leaving it to whatever the reverb
        happens to produce. Brightness (the upper tilt pole) and level both
        follow activity: that is the "rain gets closer" information channel
        and it moves both the RMS and the spectral centroid, which is what
        section 7 item 9 asks for."""
        if not _HAVE_SCIPY:
            return np.zeros((n, 2))
        # The air level follows a MEDIUM-smoothed activity envelope (tau 10 s),
        # not the tau-3s one that drives grain density. Individual tool calls
        # must not pump the bed: the instant twitch is the grains' job, and a
        # bed that tracked every PreToolUse swung short-term (3 s) RMS by
        # 4.3 dB, failing section 7 item 8 inside a single constant state.
        a = max(0.0, min(1.0, activity_med))
        # v2.2 section 1 wash crossfade: sustained activity beyond the point
        # where the discrete-drop rate map saturates (~5/s) thickens this
        # layer further instead of adding more taps -- "heavy work = thicker
        # wash, not faster taps". Normalized so a moderate flood (excess~=0.5)
        # already reaches the full extra boost.
        wash_boost = min(1.0, self.wash_excess / 0.5)
        # level: tracks the bed's own state envelope, opened up by activity.
        # v2.3: AIR_V23_LEVEL_CUT_DB shrinks the whole envelope (round-2
        # decision + lit-review rec #4 -- the continuous noise bed was the
        # dominant contributor to the "white noise/chaos" blind-round-2
        # complaint, corroborated by round-3's isolated control clip).
        floor_db = bed_level_db_value + self.cfg.AIR_FLOOR_OFFSET_DB + self.cfg.AIR_V23_LEVEL_CUT_DB
        self.air_gain_db.target = floor_db + self.cfg.AIR_ACTIVITY_RANGE_DB * (a ** 0.7) + (
            AIR_WASH_BOOST_RANGE_DB * wash_boost)
        self.air_gain_db.step(dt)
        # "gloom": a failure also dims the bed's own top end, which is where
        # most of this mix's 2-6 kHz energy lives. The master one-pole alone
        # could not deliver an audible darkening.
        gloom = 1.0 - self.cfg.AIR_GLOOM_DEPTH * min(1.0, fail_penalty_slew_value / 2400.0)
        # v2.3: hard ceiling on top of the existing activity-adaptive tilt --
        # "hard-lowpass ... the continuous air bed" per the round-2 decision.
        # Applied as a min() rather than lowering AIR_TILT_HI_ACTIVE_HZ itself
        # so the idle/low-activity register (which already sits under the
        # ceiling) is untouched and the section 7 centroid floor (>=350Hz)
        # tuning above isn't disturbed -- only the busiest, brightest end of
        # the activity range is clipped.
        self.air_cut_hz.target = min(self.cfg.AIR_V23_HARD_CEILING_HZ, (self.cfg.AIR_TILT_HI_IDLE_HZ + (
            self.cfg.AIR_TILT_HI_ACTIVE_HZ - self.cfg.AIR_TILT_HI_IDLE_HZ) * (a ** 0.7)
            + AIR_WASH_BOOST_HZ * wash_boost) * gloom)
        self.air_cut_hz.step(dt)
        if self.air_gain_db.value < -70.0:
            return np.zeros((n, 2))

        white = self._rng.standard_normal((n, 3))
        x = self.pink_noise_block(white)
        x = self.air_stage(x, "lo", *_onepole_lp_coeffs(AIR_TILT_LO_HZ, self.sr), 3)
        x = self.air_stage(x, "hi", *_onepole_lp_coeffs(self.air_cut_hz.value, self.sr), 3)
        b_hp, a_hp = self._air_hp
        x = self.air_stage(x, "hp", b_hp, a_hp, 3)
        gain = _db_to_lin(self.air_gain_db.value + self.cfg.AIR_CAL_DB) * self._air_norm
        out = np.empty((n, 2))
        out[:, 0] = (x[:, 0] + AIR_DECORR * x[:, 1]) * gain
        out[:, 1] = (x[:, 0] + AIR_DECORR * x[:, 2]) * gain
        return out

    def air_stage(self, x, key, b, a, nch):
        zi = self._air_zi.get(key)
        if zi is None or zi.shape[-1] != nch:
            zi = np.zeros((max(len(a), len(b)) - 1, nch))
        y, zf = _sp_signal.lfilter(b, a, x, zi=zi, axis=0)
        self._air_zi[key] = zf
        return y

    # -- handle_event handlers (called from AmbientTheme.handle_event, in the
    # exact original branch order) -------------------------------------------

    def handle_pretooluse(self, rng, cls, tool_name, t, fill_smooth_value):
        if self.rain_enabled:
            # brief-v2.2 section 1: "1 event = 1 drop (weighted)" -- no
            # more stacking 1-3 drops per event. Bursts closer than 250ms
            # coalesce into one weighted onset (trigger_event_drop).
            self.trigger_event_drop(rng, cls, t, fill_smooth_value)
        if tool_name == "Bash" and cls == CLASS_EXEC:
            self.whoosh_active = True
            self.whoosh_gain.tau = 1.6
            self.whoosh_gain.target = 1.0

    def handle_posttooluse(self, tool_name):
        if tool_name == "Bash":
            self.whoosh_active = False
            self.whoosh_gain.tau = 1.8
            self.whoosh_gain.target = 0.0

    def handle_failure(self):
        self.whoosh_active = False
        self.whoosh_gain.tau = 1.8
        self.whoosh_gain.target = 0.0


class BloomLayer:
    """L3 self-playing FM e-piano "bloom" melody (brief section 6). Owns its
    own scheduler/pool-selection state. Note spawning goes through the
    `spawn_note` callback passed at construction (AmbientTheme's shared note-
    spawning facility -- not bloom-exclusive: rain's write-note and
    handle_event's Stop/PreCompact/Notification gestures use it too) and the
    per-pitch refractory dict is likewise shared (a write-triggered note and
    a self-play note must not double-fire the same pitch), so both are
    passed in rather than owned here. Draws from the render-thread RNG
    passed in at construction, in the same call-order position render_block
    always called the bloom scheduler from."""

    def __init__(self, rng, spawn_note, note_refractory, sr, cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self._rng = rng
        self._spawn_note = spawn_note
        self._note_refractory = note_refractory
        self.sr = sr

        self.next_note_dt = 3.0
        self.last_note_t = -999.0
        self._last_bloom_pool_idx = None  # v2.2 section 6: stepwise motion bias

    def render(self, n, dt, t, activity, activity_slow, settled=False):
        # second, slower-smoothed activity envelope (tau 15s) driving L3 rate
        a_target = max(0.0, min(1.0, activity))
        alpha = 1.0 - math.exp(-dt / 15.0)
        activity_slow += (a_target - activity_slow) * alpha

        idle_rate = 1.0 / 45.0
        busy_rate = 1.0 / 2.5
        rate = idle_rate + (busy_rate - idle_rate) * activity_slow
        # v2.4 "settled" idle (research/BRIEF-v2.4.md): self-play drops to
        # very sparse (not silent) right after Stop, distinct from ordinary
        # idle-during-work -- one of the two audible legs of the DONE state
        # (the other is BedLayer's deeper settled bed dip).
        if settled:
            rate *= self.cfg.SETTLED_BLOOM_RATE_SCALE
        rate = max(rate, 1e-4)

        block_dur = n / self.sr
        self.next_note_dt -= block_dur
        if self.next_note_dt <= 0:
            self._maybe_fire_note(t, activity_slow)
            interval = -math.log(max(self._rng.random(), 1e-12)) / rate
            self.next_note_dt = interval
        return activity_slow

    def _pick_idx(self, rng, idle_mode):
        """Pick a pool index. Brief-v2.2 section 6: melodic pool selection is
        biased toward stepwise motion (next note within +-2 pool steps of the
        previous) so the bloom reads as intentional phrasing rather than
        random scatter. idle_mode restricts the candidate set to the mid
        register (section 4: "never the top octave alone" at idle)."""
        if idle_mode:
            candidates = AMBIENT_MID_REGISTER_IDXS
            weights = AMBIENT_MID_REGISTER_WEIGHTS
        else:
            candidates = np.arange(len(AMBIENT_NOTE_POOL))
            weights = AMBIENT_NOTE_WEIGHTS
        last_idx = self._last_bloom_pool_idx
        if last_idx is not None and rng.random() < STEPWISE_BIAS_PROB:
            lo, hi = last_idx - STEPWISE_BIAS_STEPS, last_idx + STEPWISE_BIAS_STEPS
            near = candidates[(candidates >= lo) & (candidates <= hi)]
            if len(near) > 0:
                near_w = AMBIENT_NOTE_WEIGHTS[near]
                near_w = near_w / near_w.sum()
                return int(rng.choice(near, p=near_w))
        return int(rng.choice(candidates, p=weights))

    def _maybe_fire_note(self, t, activity_slow):
        # brief-v2.2 section 1 pacing floor: melodic notes min gap 2.5s when
        # busy (idle self-play keeps its own ~1/45s Poisson clock, which is
        # already far looser than this floor).
        if t - self.last_note_t < NOTE_MIN_GAP_S:
            return  # scheduler will retry next Poisson tick
        rng = self._rng
        idle_mode = activity_slow < BLOOM_IDLE_SLOW_ACTIVITY_THRESH
        for _attempt in range(3):
            idx = self._pick_idx(rng, idle_mode)
            midi = int(AMBIENT_NOTE_POOL[idx])
            last = self._note_refractory.get(midi, -999.0)
            if t - last >= 10.0:
                break
        else:
            return
        velocity = 0.3 + 0.5 * (rng.random() ** 2)
        freq = _midi_hz(midi)
        # v2.2 section 3: "delete r=3.5 bell from routine flow" -- the bell
        # voice is now ONLY used for the Notification/PermissionRequest
        # gesture (handle_event), never from self-play.
        embed_cap = self.cfg.NOTE_EMBED_CAP_IDLE_DB if idle_mode else self.cfg.NOTE_EMBED_CAP_DB
        self._spawn_note(rng, freq, velocity=velocity, bell=False, embed_cap_db=embed_cap)
        self._note_refractory[midi] = t
        self.last_note_t = t
        self._last_bloom_pool_idx = idx
        if not idle_mode and rng.random() < 0.15:
            extra = int(rng.integers(1, 3))
            for _ in range(extra):
                idx2 = self._pick_idx(rng, idle_mode)
                midi2 = int(AMBIENT_NOTE_POOL[idx2])
                spacing = rng.uniform(0.08, 0.25)
                self._spawn_note(rng, _midi_hz(midi2), velocity=velocity * 0.8, delay_s=spacing)
                self._last_bloom_pool_idx = idx2


