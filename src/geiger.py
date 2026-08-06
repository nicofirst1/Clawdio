"""GeigerTheme (v1) sound engine: EngineState (alias GeigerTheme), the
module-level render_block dispatcher, and click/chime/drone rendering.
Split out of sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from config import (
    SAMPLE_RATE, BLOCKSIZE, TAU_ACTIVITY, MAX_CLICK_RATE, MAX_ACTIVE_CHIMES,
    CLASS_READ, TIMBRE,
)
from classify import classify, SessionTracker, SubagentPresenceTracker, SUBAGENT_PRESENCE_DECAY_S
from dsp import Slew, build_chime_bank, _mono_to_stereo, _make_click_grain
from logging_setup import get_logger

log = get_logger("sonifier")


# --------------------------------------------------------------------------
# Engine state
# --------------------------------------------------------------------------

@dataclass
class EngineState:
    sr: int = SAMPLE_RATE
    volume: float = 0.5
    mute: bool = False
    clicks_enabled: bool = True
    chimes_enabled: bool = True
    drone_enabled: bool = False
    quiet: bool = False
    seed: int = 0

    # activity leaky integrator
    activity: float = 0.0
    current_class: str = CLASS_READ

    # click scheduling (main + subagent streams)
    # _rng is owned exclusively by the audio/render thread. Ingress threads
    # must NOT touch it (np.random.Generator is not thread-safe); they use
    # _chime_rng instead. See _play_chime.
    _rng: np.random.Generator = field(default=None, repr=False)
    _chime_rng: np.random.Generator = field(default=None, repr=False)
    next_click_dt_main: float = 0.05
    time_to_next_click_main: float = 0.05
    next_click_dt_sub: float = 0.05
    time_to_next_click_sub: float = 0.05

    subagent_refcount: int = 0
    subagent_presence: SubagentPresenceTracker = field(
        default_factory=SubagentPresenceTracker, repr=False
    )

    # drone
    drone_x: float = 0.0
    drone_cutoff_lag: Slew = field(default=None, repr=False)
    drone_gain_lag: Slew = field(default=None, repr=False)
    drone_phase: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    drone_lp_state: float = 0.0

    # chime playback: list of dicts {buf, pos, gain}
    active_chimes: list = field(default_factory=list, repr=False)
    chime_bank: dict = field(default_factory=dict, repr=False)

    # pending one-shot immediate click (PreToolUse "click immediately once")
    pending_immediate_click: bool = False

    t: float = 0.0  # wall/virtual clock, seconds since start
    last_event_t: float = 0.0
    sessions: SessionTracker = field(default_factory=SessionTracker, repr=False)

    def __post_init__(self):
        if self._rng is None:
            self._rng = np.random.default_rng(self.seed)
        if self._chime_rng is None:
            # Separate stream so ingress-thread chime jitter never races the
            # audio thread's click jitter on the same bit generator.
            self._chime_rng = np.random.default_rng(self.seed + 0x9E3779B9)
        if self.drone_cutoff_lag is None:
            self.drone_cutoff_lag = Slew(200.0, tau=1.0)
        if self.drone_gain_lag is None:
            self.drone_gain_lag = Slew(0.0, tau=1.0)
        if not self.chime_bank:
            self.chime_bank = build_chime_bank(self.sr)

    # -- event handling ----------------------------------------------------

    def handle_event(self, ev: dict):
        if not isinstance(ev, dict):
            return
        name = ev.get("hook_event_name")
        if not isinstance(name, str):
            return
        self.last_event_t = self.t
        tool_name = ev.get("tool_name")
        tool_input = ev.get("tool_input")
        session_id = ev.get("session_id")
        if name != "SessionEnd":
            self.sessions.note(session_id, self.t)

        agent_id = ev.get("agent_id")
        if agent_id:
            self.subagent_presence.note(agent_id, self.t)
        else:
            # No agent_id on this event, but still re-evaluate presence/decay
            # so a fade-out happens on schedule instead of only on the next
            # tagged event (mirrors ambient.py's recheck_presence wiring).
            self.subagent_presence.active_count(self.t)

        if name == "PreToolUse":
            cls = classify(tool_name, tool_input)
            if cls is not None:
                self.current_class = cls
            self._bump_activity(0.35)
            self.pending_immediate_click = True
        elif name == "PostToolUse":
            self._bump_activity(0.15)
        elif name == "PostToolUseFailure":
            self._play_chime("failure")
        elif name == "UserPromptSubmit":
            self._bump_activity(0.2)
        elif name == "Stop":
            self._play_chime("done")
            self.activity *= 0.3
        elif name in ("Notification", "PermissionRequest"):
            self._play_chime("attention")
        elif name == "SubagentStart":
            self.subagent_refcount += 1
            self._play_chime("spawn")
        elif name == "SubagentStop":
            self.subagent_refcount = max(0, self.subagent_refcount - 1)
            self.subagent_presence.drop(ev.get("agent_id"))
            self._play_chime("despawn")
        elif name == "PreCompact":
            self._play_chime("compact")
        elif name == "SessionStart":
            # New session => fresh context window, so release any drone left
            # over from a previous session on this long-lived daemon.
            self.drone_x = 0.0
        elif name == "SessionEnd":
            self.sessions.end(session_id)
            # Multi-session: another tracked session is still live, so this
            # isn't the daemon's only user leaving -- skip the zeroing below
            # (a session finishing is still visible via subagent_refcount/
            # activity decay, just not a hard reset out from under the other
            # session).
            if self.sessions.live_count(self.t) == 0:
                self.activity = 0.0
                # Release the context-pressure drone too. Without this the
                # daemon keeps droning at the last observed fill for the
                # whole SONIFIER_IDLE_EXIT_MIN window (default 30 min) after
                # the session is over.
                self.drone_x = 0.0
        elif name == "ContextPressure":
            self.set_pressure(ev.get("fill"))
        else:
            return

        log.debug("event=%s tool=%s activity=%.3f", name, tool_name, self.activity)

    def set_pressure(self, fill):
        """Part of the small cross-theme interface: handle_event(evt) /
        set_pressure(fill) / render_block(). GeigerTheme's ContextPressure
        handling factored out here so both themes expose the same method."""
        try:
            fill = float(fill)
        except (TypeError, ValueError):
            return
        self.drone_x = max(0.0, min(1.0, fill))

    def _bump_activity(self, amount):
        self.activity += amount

    def _play_chime(self, name):
        if not self.chimes_enabled:
            return
        buf = self.chime_bank.get(name)
        if buf is None:
            return
        # Bound concurrency: an event flood (or a pathological hook loop)
        # must not grow active_chimes without limit -- each queued chime is
        # ~200KB and costs the audio callback per-block mixing work, so an
        # unbounded queue is both a memory leak and an xrun generator.
        if len(self.active_chimes) >= MAX_ACTIVE_CHIMES:
            return
        # micro-variation: +/-20 cents pitch (resample), +/-10% duration.
        # Uses _chime_rng (ingress-thread-owned), never _rng.
        cents = self._chime_rng.uniform(-20, 20)
        rate = 2 ** (cents / 1200.0)
        dur_scale = self._chime_rng.uniform(0.9, 1.1)
        resample_factor = rate * dur_scale
        n = len(buf)
        if abs(resample_factor - 1.0) > 1e-6 and n > 4:
            new_n = max(4, int(n / resample_factor))
            idx = np.linspace(0, n - 1, new_n)
            left = np.interp(idx, np.arange(n), buf[:, 0])
            right = np.interp(idx, np.arange(n), buf[:, 1])
            varied = np.stack([left, right], axis=1).astype(np.float32)
        else:
            varied = buf
        self.active_chimes.append({"buf": varied, "pos": 0})

    # -- per-block time update ---------------------------------------------

    def advance_time(self, dt):
        self.t += dt
        # Defensive: a non-finite activity would poison every downstream
        # rate/interval computation and silently wedge the click scheduler
        # (NaN <= x is always False, so the Poisson loop would never fire
        # again). Snap it back to a sane value instead.
        if not math.isfinite(self.activity):
            self.activity = 0.0
        if self.activity > 0:
            self.activity *= math.exp(-dt / TAU_ACTIVITY)
            # Snap-to-zero threshold: below this the ~0.5clicks/s formula
            # floor is already indistinguishable from itself, so snapping
            # here just lets the engine reach literal silence in finite
            # time instead of only asymptotically.
            if self.activity < 1e-3:
                self.activity = 0.0
        self.drone_cutoff_lag.target = 200.0 * (40.0 ** self.drone_x)
        gain_target = 0.0
        if self.drone_x > 0.5:
            gain_target = min(1.0, (self.drone_x - 0.5) / 0.5) * 0.35
        self.drone_gain_lag.target = gain_target
        self.drone_cutoff_lag.step(dt)
        self.drone_gain_lag.step(dt)

    # theme interface: render_block(n) -> (n, 2) float32. Bound onto the
    # class below, once _geiger_render_block (its implementation, unchanged
    # from v1) is defined.


# GeigerTheme is the v2 name for the (unchanged) v1 sound engine. EngineState
# is kept as the primary name for backward compatibility with existing code/
# tests that construct it directly.
GeigerTheme = EngineState


# --------------------------------------------------------------------------
# render_block: the pure-ish per-block audio function (GeigerTheme's
# implementation -- exactly the v1 logic, just bound as EngineState.render_
# block below instead of called as a free function).
# --------------------------------------------------------------------------

def _click_rate_from_activity(a):
    # Deviation from literal spec formula: at exactly-zero activity (fully
    # idle, e.g. before any events or long after Stop/SessionEnd) the rate
    # is forced to 0 so the engine can reach true silence. For any positive
    # activity the spec's "0.5 + 14.5*a^1.5" floor/curve applies unchanged.
    if a <= 0.0:
        return 0.0
    a_clamped = min(max(a, 0.0), 1.0)
    return 0.5 + 14.5 * (a_clamped ** 1.5)


def _emit_click(state, out, start_idx, cls, pan=0.0, freq_mult=1.0, gain_mult=1.0):
    timbre = TIMBRE[cls]
    rng = state._rng
    amp_db = rng.uniform(-6.0, 6.0)
    amp_jit = 10 ** (amp_db / 20.0)
    freq_jit = rng.uniform(0.9, 1.1)
    dur_jit = rng.uniform(0.8, 1.2)
    center = timbre["center"] * freq_jit * freq_mult
    decay = timbre["decay"] * dur_jit
    amp = timbre["amp"] * amp_jit * gain_mult
    grain = _make_click_grain(rng, center, timbre["q"], decay, amp, sr=state.sr)
    n = len(grain)
    stereo = _mono_to_stereo(grain, pan=pan)
    avail = out.shape[0] - start_idx
    if avail <= 0:
        return
    write_n = min(n, avail)
    out[start_idx:start_idx + write_n, :] += stereo[:write_n, :]


_RENDER_FAULT_REPORTED = False


def _geiger_render_block(state: EngineState, n=BLOCKSIZE) -> np.ndarray:
    """Render one block of audio (n, 2) float32 given current EngineState.

    Mutates state (scheduling counters, chime playheads, drone phase, time)
    as a side effect -- this is the per-block "tick" of the engine. This is
    GeigerTheme's render_block implementation (identical to v1); it is bound
    onto the class as `EngineState.render_block` right after definition.
    """
    out = np.zeros((n, 2), dtype=np.float32)
    sr = state.sr
    dt_block = n / sr

    try:
        # ---- CLICK TRAIN ----
        if state.clicks_enabled:
            _render_clicks(state, out, n)
        else:
            # Clicks disabled live (web UI) with a click already pending:
            # drop it rather than let it fire stale on re-enable.
            state.pending_immediate_click = False

        # ---- CHIMES ----
        if state.chimes_enabled and state.active_chimes:
            _render_chimes(state, out, n)

        # ---- DRONE ----
        if state.drone_enabled:
            _render_drone(state, out, n)

        # advance clock / integrators for this block
        state.advance_time(dt_block)

        # ---- MASTER BUS ----
        vol = 0.0 if state.mute else state.volume
        out *= vol
        np.tanh(out, out=out)
    except Exception as exc:
        # Never let a DSP bug propagate into the audio callback (that would
        # kill the stream); emit silence for this block instead. Report once
        # so a persistent fault is diagnosable rather than silently mute.
        global _RENDER_FAULT_REPORTED
        if not _RENDER_FAULT_REPORTED:
            _RENDER_FAULT_REPORTED = True
            log.error(
                "render_block fault (silencing this block; further "
                "occurrences suppressed): %r", exc
            )
        out = np.zeros((n, 2), dtype=np.float32)
        try:
            state.advance_time(dt_block)
        except Exception:
            pass

    return out


# Bind onto the class: this is the "render_block(n) -> (n,2) float32" leg of
# the small cross-theme interface (handle_event / set_pressure / render_
# block). AmbientTheme defines its own render_block method below.
EngineState.render_block = _geiger_render_block


def render_block(state, n=BLOCKSIZE) -> np.ndarray:
    """Module-level dispatcher: renders one block via whichever theme
    `state` belongs to (GeigerTheme / EngineState or AmbientTheme). Kept as
    a free function for backward compatibility -- v1 code/tests call
    `sonifier.render_block(state, BLOCK)`."""
    return state.render_block(n)


def _current_click_rates(state):
    base_rate = _click_rate_from_activity(state.activity)
    active_count = max(state.subagent_presence.active_count(state.t), state.subagent_refcount)
    if active_count > 0:
        # split budget between main + subagent register, cap combined.
        sub_rate = base_rate * 0.6
    else:
        sub_rate = 0.0
    total = base_rate + sub_rate
    if total > MAX_CLICK_RATE:
        scale = MAX_CLICK_RATE / total
        base_rate *= scale
        sub_rate *= scale
    return base_rate, sub_rate


def _render_clicks(state, out, n):
    sr = state.sr
    block_dur = n / sr

    if state.pending_immediate_click:
        cls = state.current_class
        _emit_click(state, out, 0, cls, pan=0.0)
        state.pending_immediate_click = False
        state.time_to_next_click_main = -math.log(state._rng.uniform(1e-9, 1.0)) / max(
            _click_rate_from_activity(state.activity), 1e-6
        )

    base_rate, sub_rate = _current_click_rates(state)

    # main stream
    if base_rate > 0:
        while state.time_to_next_click_main <= block_dur:
            idx = int(state.time_to_next_click_main * sr)
            idx = min(idx, n - 1)
            _emit_click(state, out, idx, state.current_class, pan=0.0)
            interval = -math.log(max(state._rng.uniform(), 1e-12)) / base_rate
            state.time_to_next_click_main += interval
        state.time_to_next_click_main -= block_dur
        if state.time_to_next_click_main < 0:
            state.time_to_next_click_main = 0.0
    else:
        # Rate is exactly zero (fully idle). Emitting the already-armed
        # click here would put a lone stray click into what is contractually
        # silence, so instead just hold the scheduler armed one block out:
        # it fires promptly once activity returns, and stays silent until
        # then.
        if state.time_to_next_click_main < block_dur:
            state.time_to_next_click_main = block_dur

    # subagent register stream
    if sub_rate > 0:
        while state.time_to_next_click_sub <= block_dur:
            idx = int(state.time_to_next_click_sub * sr)
            idx = min(idx, n - 1)
            _emit_click(
                state, out, idx, state.current_class, pan=0.4, freq_mult=1.5, gain_mult=0.8
            )
            rate = max(sub_rate, 1e-6)
            interval = -math.log(max(state._rng.uniform(), 1e-12)) / rate
            state.time_to_next_click_sub += interval
        state.time_to_next_click_sub -= block_dur
        if state.time_to_next_click_sub < 0:
            state.time_to_next_click_sub = 0.0
    else:
        # keep the sub scheduler primed at block boundary so it doesn't
        # burst the instant a subagent appears.
        if state.time_to_next_click_sub < block_dur:
            state.time_to_next_click_sub = block_dur


def _render_chimes(state, out, n):
    # NOTE: active_chimes is appended to by ingress threads and consumed
    # here on the audio thread. We deliberately mutate the SAME list object
    # (never rebind state.active_chimes) and only ever index below the
    # snapshot length taken at block start -- a concurrent list.append is
    # atomic under the GIL and lands past that bound, so a chime can never
    # be silently lost by a read-modify-write race.
    chimes = state.active_chimes
    count = len(chimes)
    for i in range(count):
        c = chimes[i]
        buf = c["buf"]
        pos = c["pos"]
        remaining = len(buf) - pos
        if remaining <= 0:
            continue
        write_n = min(remaining, n)
        out[:write_n, :] += buf[pos:pos + write_n, :]
        c["pos"] += write_n
    # Reap finished chimes, back-to-front so earlier indices stay valid.
    for i in range(count - 1, -1, -1):
        c = chimes[i]
        if c["pos"] >= len(c["buf"]):
            del chimes[i]


def _render_drone(state, out, n):
    sr = state.sr
    gain = state.drone_gain_lag.value
    if gain <= 1e-6:
        return
    freqs = (55.0, 55.35, 110.4)
    t = np.arange(n) / sr
    sig = np.zeros(n, dtype=np.float64)
    for i, f in enumerate(freqs):
        phase0 = state.drone_phase[i]
        # sawtooth via phase accumulation
        phase = phase0 + 2 * np.pi * f * t
        saw = 2.0 * (((phase / (2 * np.pi)) % 1.0)) - 1.0
        sig += saw / len(freqs)
        state.drone_phase[i] = (phase0 + 2 * np.pi * f * n / sr) % (2 * np.pi)

    cutoff = max(state.drone_cutoff_lag.value, 20.0)
    alpha = 1.0 - math.exp(-2 * math.pi * cutoff / sr)
    y = state.drone_lp_state
    filtered = np.empty(n, dtype=np.float64)
    for i in range(n):
        y = y + alpha * (sig[i] - y)
        filtered[i] = y
    state.drone_lp_state = y

    filtered *= gain
    stereo = _mono_to_stereo(filtered.astype(np.float32), pan=0.0)
    out += stereo


###############################################################################
# AmbientTheme -- v2 sound layer (see BRIEF-v2.md). New DEFAULT theme.
