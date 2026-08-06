"""AmbientTheme: v2 default sound layer (generative pad + rain + melodic
bloom). Split out of sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

import collections
import math

import numpy as np

from config import (
    SAMPLE_RATE, BLOCKSIZE, TAU_ACTIVITY, CLASS_READ, CLASS_WRITE,
)
from classify import classify, SessionTracker
from dsp import Slew, _mono_to_stereo, _limit_ms_ratio
import geiger  # for the shared _RENDER_FAULT_REPORTED fault-suppression flag
from ambient_layers import (
    _HAVE_SCIPY, _sp_signal,
    AMBIENT_CONFIG, AMBIENT_DRY_GAIN, AMBIENT_MASTER_HEADROOM_DB,
    AMBIENT_WET_GAIN, ACTIVITY_BUMP, ACTIVITY_MED_TAU, BED_CAL_DB,
    DUCK_ATTACK_S, DUCK_DEPTH_DB, DUCK_HOLD_S, DUCK_RELEASE_S,
    DUCK_SMOOTH_S, DUCK_TOTAL_S, KNOCK_EMBED_CAP_DB, MAX_PENDING_VOICES,
    MS_MAX_SIDE_OVER_MID, NOTE_DIRECT_FRAC, NOTE_EMBED_CAP_DB,
    NOTE_PAN_LIMIT, NOTE_REVERB_FRAC, ROOT_C2,
    BedLayer, BloomLayer, Freeverb, RainLayer, StemLayer, WeatherLayer,
    _build_cadence_notes, _build_cadence_notes_v24, _db_to_lin,
    _dc_blocker, _midi_hz, _mix_voices, _onepole_lp_coeffs,
    _pick_write_register_note, _render_fm_note, _render_knock,
    _voice_pool_add,
)
from logging_setup import get_logger

log = get_logger("sonifier")


class AmbientTheme:
    """v2 default sound: generative ambient layers (BRIEF-v2.md). Exposes
    the same small interface as GeigerTheme: handle_event(evt), set_pressure
    (fill), render_block(n) -> (n,2) float32.

    Thread-safety mirrors GeigerTheme/v1 for the RNGs only: `_rng` is owned
    exclusively by the render/audio thread (continuous layers: bed OU walks,
    rain Poisson bed, bloom scheduler). `_ingress_rng` is a separate stream
    used only inside handle_event (HTTP/UDP ingress threads in live mode) to
    synthesize ready-made one-shot buffers (drops, gestures) that are then
    appended to plain python lists -- the same "ingress thread only ever
    list.append" pattern v1 uses for chimes, so the RNG streams themselves
    never need locks. In --render mode handle_event and render_block run on
    the same thread, so this split doesn't affect determinism (both
    generators are seeded from `seed` and called in a fixed, seed-only-
    dependent order).

   
    """

    def __init__(self, sr=SAMPLE_RATE, volume=0.5, mute=False, clicks_enabled=True,
                 chimes_enabled=True, drone_enabled=False, quiet=False, seed=0,
                 cfg=None):
        self.cfg = cfg if cfg is not None else AMBIENT_CONFIG
        self.sr = sr
        self.volume = volume
        self.mute = mute
        # DEVIATION: SONIFIER_CLICKS gates L2 (rain: the v1 "click" analog),
        # SONIFIER_CHIMES gates discrete gestures (the v1 "chime" analog:
        # knock, cadence, notification chime, ack note, settling gesture).
        # SONIFIER_DRONE is accepted for signature/env compatibility but is
        # NOT used to gate L5 weather -- per brief section 8 the pressure
        # layer "is part of ambient theme, on by default there" (event-
        # driven by ContextPressure, same as v1's drone_x plumbing).
        self.rain_enabled = clicks_enabled
        self.gestures_enabled = chimes_enabled
        self.drone_enabled = drone_enabled  # unused for gating; kept for parity
        self.quiet = quiet
        self.seed = seed

        self._rng = np.random.default_rng(seed)
        self._ingress_rng = np.random.default_rng(seed + 0x9E3779B9)

        self.t = 0.0
        self.last_event_t = 0.0
        self.activity = 0.0
        self.activity_med = 0.0
        self.activity_slow = 0.0
        self.current_class = CLASS_READ

        self.session_started = False
        self.session_start_t = None
        self.session_ended = False
        self.session_end_t = None
        self.sessions = SessionTracker()

        # Voice pool (drops + bloom notes + gestures). Built before the layer
        # constructors below since they pass self._queue_voice (a bound
        # method) into RainLayer/BloomLayer's constructors and that method
        # reads self._pending. `voices` is owned exclusively by the render/
        # audio thread. Everything that wants to start a voice -- including
        # handle_event on an HTTP/UDP ingress thread -- appends a ready-made
        # buffer to the bounded `_pending` deque instead; the render thread
        # drains it at the top of each block (_collect_voices).
        # deque.append/popleft are individually atomic under the GIL, so this
        # needs no lock, and unlike the previous design the ingress thread
        # never does pop(0)/insert(0) on the list the mixer is walking (that
        # could re-index a voice mid-mix or resurrect an already-mixed one).
        # MAX_PENDING_VOICES bounds an event flood the same way
        # MAX_ACTIVE_CHIMES does for v1.
        self.voices = []
        self._pending = collections.deque()

        # _lp_zi is a shared one-pole-filter-state dict: BedLayer's own bed/
        # midlayer lowpass stages, StemLayer's stem lowpass stages, and the
        # master bus's send/air lowpass all key into the SAME dict (distinct
        # string keys per stage), so it stays here rather than being owned
        # by any one layer.
        self._lp_zi = {}

        # Shared per-pitch refractory dict (write-triggered notes below and
        # BloomLayer's self-play must not double-fire the same pitch).
        self._note_refractory = {}  # midi -> last played self.t

        # Layer construction order matters: each layer's constructor draws
        # from self._rng (bed_phase/shimmer_phase/midlayer_phase, then the
        # drop bank, then stem_phase), and that draw order must stay
        # identical to the pre-split single-__init__ sequence for
        # byte-identical renders under a fixed seed.
        self.bed = BedLayer(self._rng, self._apply_lp_stage, sr,
                           done_cadence=self.cfg.done_cadence)
        self.rain = RainLayer(self._rng, self._queue_voice, sr, self.rain_enabled,
                               drop_timbre=self.cfg.drop_timbre)
        self.bloom = BloomLayer(self._rng, self._spawn_note, self._note_refractory, sr)
        self.stem = StemLayer(self._rng, self._apply_lp_stage, sr)
        self.weather = WeatherLayer(sr)

        # L5 context-pressure weather (shared/master-bus state; see
        # WeatherLayer docstring for why fill/fail-penalty stay here)
        self.fill = 0.0
        self.fill_smooth = Slew(0.0, tau=5.0)
        # master_lp_cutoff/fail_penalty_slew are driven from fill_smooth,
        # which ALREADY carries the brief's "slew tau >= 5 s". Giving them a
        # second 5 s lag of their own cascaded into ~10 s of effective
        # smoothing: measured on the demo, the sub-bass drone was still 20 dB
        # below its target at the moment of peak context pressure and the
        # master lowpass was 800 Hz behind, so the whole L5 weather layer
        # arrived after the weather had passed. 0.5 s here is just enough to
        # keep the coefficient changes zipper-free.
        self.master_lp_cutoff = Slew(6000.0, tau=0.5)
        self.fail_penalty_hz = 0.0
        self.fail_penalty_slew = Slew(0.0, tau=1.5)

        # "Room pause" duck (see _duck_block): a mix-bus concern applied to
        # multiple sustained layers' output, not owned by any one of them.
        self._duck_start_t = None
        self._duck_gain_z = 1.0

        # freeverb + master DC blocker state
        self.reverb = Freeverb(sr)
        self._dc_x1 = np.zeros(2)
        self._dc_y1 = np.zeros(2)
        self._session_fade = Slew(0.0, tau=1.0)  # 3s-ish fade-in
        self._end_fade = Slew(1.0, tau=1.3)      # 4s-ish fade-out on SessionEnd

    # -- interface: set_pressure -------------------------------------------

    def set_pressure(self, fill):
        try:
            fill = float(fill)
        except (TypeError, ValueError):
            return
        self.fill = max(0.0, min(1.0, fill))

    # -- interface: handle_event --------------------------------------------

    def handle_event(self, ev):
        if not isinstance(ev, dict):
            return
        name = ev.get("hook_event_name")
        if not isinstance(name, str):
            return
        self.last_event_t = self.t
        tool_name = ev.get("tool_name")
        tool_input = ev.get("tool_input")
        session_id = ev.get("session_id")
        # SessionStart needs the live count from BEFORE this session is
        # noted, to test the 0 -> 1 transition cleanly; every other branch is
        # order-insensitive, so note() happens here for all of them and
        # SessionStart/SessionEnd below read/mutate the tracker themselves.
        was_live_before = self.sessions.live_count(self.t) > 0
        if name != "SessionStart":
            self.sessions.note(session_id, self.t)
        # BRIEF-v2.5: this session's voice slot for its discrete gestures
        # (failure knock, Stop cadence, needs-you chime, ack note). Read once
        # here and baked into whatever gesture this event queues. slot_pan is
        # the equal-power pan; slot_semi the pitch transpose. For the only
        # live session -- and any event without a session_id -- this is
        # (0.0, 0), i.e. the legacy center path (byte-identical single-session
        # render). SessionStart's own note()/slot-assign happens in its branch
        # below (after the count check), so its chime is out of scope by
        # construction and stays center, per the v2.5 scope.
        slot_pan, slot_semi = self.sessions.slot_of(session_id)
        slot_pitch = 2.0 ** (slot_semi / 12.0)  # 1.0 when slot_semi == 0
        # The chime and ack note are randomly panned within +-NOTE_PAN_LIMIT
        # today (pan=None). For the center slot we MUST keep that random draw
        # untouched (byte-identical single-session render); an off-center slot
        # replaces it with the fixed slot pan. The knock and cadence are
        # pan=0.0-explicit, so they take slot_pan directly with no such dance.
        slot_gesture_pan = None if slot_pan == 0.0 else slot_pan
        agent_id = ev.get("agent_id")
        if agent_id:
            self.stem.note_presence(agent_id, self.t)
        else:
            # No agent_id on this event, but still re-check presence so a
            # previously-tagged subagent's entry expires/fades on schedule
            # instead of only fading on its next tagged event.
            self.stem.recheck_presence(self.t)
        rng = self._ingress_rng

        if name == "PreToolUse":
            cls = classify(tool_name, tool_input)
            if cls is not None:
                self.current_class = cls
            # Class-weighted activity bump (v1 used a flat 0.35). A Write or a
            # Bash run IS more work than a Read, and without this the demo's
            # "gentle browse" and "active build" phases differ only in tool
            # class, not in energy -- the storyboard arc has to be audible as
            # loudness, not just as timbre.
            self.activity += ACTIVITY_BUMP.get(cls, 0.35)
            self.rain.handle_pretooluse(rng, cls, tool_name, self.t, self.fill_smooth.value)
            if cls == CLASS_WRITE and rng.random() < 0.30 and self.gestures_enabled:
                midi, hz = _pick_write_register_note(rng)
                # Same per-pitch refractory the bloom scheduler uses: without
                # it a burst of Writes could hammer one pool note repeatedly,
                # which is both musically dull and the fastest way to build a
                # prominent tone out of the bed.
                if self.t - self._note_refractory.get(midi, -999.0) >= 6.0:
                    self._note_refractory[midi] = self.t
                    self._spawn_note(rng, hz, velocity=0.5)
        elif name == "PostToolUse":
            self.activity += 0.15
            # "the room got darker ... stays unresolved until next Stop/
            # success" (BRIEF section 3). Success = the tool that failed now
            # works; unrelated tool calls in between do not clear it, so the
            # shading actually tracks the failure instead of evaporating on
            # the next Read.
            if (self.fail_penalty_hz > 0.0 or self.bed.bass_shaded_vi):
                if self.bed.handle_posttooluse_recovery(tool_name):
                    self.fail_penalty_hz = 0.0
                    self.fail_penalty_slew.tau = 4.0
                    self.fail_penalty_slew.target = 0.0
            self.rain.handle_posttooluse(tool_name)
        elif name == "PostToolUseFailure":
            self.rain.handle_failure()
            # "Room pause": the sustained layers dip for ~0.45 s so the knock
            # reads by contrast rather than by level (see _duck_block).
            self._duck_start_t = self.t
            if self.gestures_enabled:
                # v2.2 embedding rule: knock peak <= bed RMS + KNOCK_EMBED_CAP_DB,
                # relative to the CURRENT calibrated bed reference (not a fixed trim).
                bed_ref_db = self.bed.bed_level_db.value + BED_CAL_DB
                knock = _render_knock(rng, velocity=0.75, sr=self.sr,
                                      pitch_factor=slot_pitch) * _db_to_lin(
                    bed_ref_db + KNOCK_EMBED_CAP_DB)
                direct = _mono_to_stereo((knock * 0.85).astype(np.float64), pan=slot_pan)
                self._queue_voice({"buf": direct, "pos": 0, "bus": "direct"})
                send = _mono_to_stereo((knock * 0.15).astype(np.float64), pan=slot_pan)
                self._queue_voice({"buf": send, "pos": 0, "bus": "reverb"})
            self.bed.handle_failure(tool_name)
            # BRIEF: "repeated failures each pull master LP down another
            # 300 Hz (floor 1.8 kHz)". 300 Hz off a 6 kHz one-pole is
            # inaudible (measured: 0.0 dB change in the 2-6 kHz band), so the
            # FIRST failure takes a real step and subsequent ones add the
            # brief's increments on top. Combined with the I->vi bass shading
            # and the air-brightness gloom below, one failure now reads as
            # "the room got darker" rather than as nothing at all.
            step = 2400.0 if self.fail_penalty_hz <= 0.0 else 700.0
            self.fail_penalty_hz = min(4200.0, self.fail_penalty_hz + step)
            self.fail_penalty_slew.tau = 2.0
            self.fail_penalty_slew.target = self.fail_penalty_hz
        elif name == "UserPromptSubmit":
            self.activity += 0.2
            if self.gestures_enabled:
                pitch_midi = int(rng.choice([55, 57]))  # G3 / A3
                self._spawn_note(rng, _midi_hz(pitch_midi), velocity=0.35, gain_db=2.0,
                                 pan=slot_gesture_pan, pitch_factor=slot_pitch)
        elif name == "Stop":
            self.bed.handle_stop(self.t)
            if self.gestures_enabled:
                cadence_spacing = 0.26
                if self.cfg.done_cadence == "v22":
                    notes = _build_cadence_notes(rng)
                else:
                    # v2.4 authentic cadence (research/BRIEF-v2.4.md): the
                    # melody always lands on the tonic (C4, never the
                    # dominant G4 -- see _build_cadence_notes_v24), paired
                    # with a simultaneous bass-register root landing
                    # (ROOT_C2, the pad's own fundamental) timed to sound at
                    # the same instant as the melody's final note. A root-
                    # position tonic in the bass under a melodic resolution
                    # is what makes a cadence read as "authentic" (resolved)
                    # rather than just "a melody that stopped" -- exactly
                    # the two-listener-confirmed defect this version fixes.
                    notes = _build_cadence_notes_v24(rng)
                    land_delay_s = (len(notes) - 1) * cadence_spacing
                    self._spawn_note(rng, ROOT_C2, velocity=0.5, delay_s=land_delay_s,
                                     gain_db=2.0, pan=slot_pan, pitch_factor=slot_pitch)
                # brief-v2.2 section 5: "knock/cadence center" (pan=0.0) for
                # the sole session; a slotted session places its cadence at
                # slot_pan / transposes it by the slot offset (BRIEF-v2.5).
                self._spawn_note_sequence(rng, notes, velocity=0.4, spacing=cadence_spacing,
                                          gain_db=-2.0, pan=slot_pan, pitch_factor=slot_pitch)
            self.activity *= 0.3
            self.fail_penalty_hz = 0.0
            self.fail_penalty_slew.tau = 5.0
            self.fail_penalty_slew.target = 0.0
        elif name in ("Notification", "PermissionRequest"):
            self.bed.handle_notification(self.t)
            if self.gestures_enabled:
                # BRIEF suggests A5/E5; dropped an octave to A4/E4 during v2
                # verification. At A5 the r=3.5 mod puts the chime's upper
                # sideband at ~3.96 kHz, 33 dB clear of a bed that is -49 dB
                # in that octave: measurably the most prominent tone in the
                # whole render and exactly the "alarming" character the brief
                # says this gesture must not have. An octave down it lands at
                # ~2 kHz, still distinct and inviting, no longer piercing.
                a4 = _midi_hz(69)
                e4 = _midi_hz(64)
                self._spawn_note(rng, a4, velocity=0.3, bell=True, i_peak=1.2, gain_db=5.0,
                                 pan=slot_gesture_pan, pitch_factor=slot_pitch)
                self._spawn_note(rng, e4, velocity=0.3, bell=True, i_peak=1.2,
                                 delay_s=0.18, gain_db=5.0,
                                 pan=slot_gesture_pan, pitch_factor=slot_pitch)
        elif name == "SubagentStart":
            self.stem.handle_subagent_start(self.t)
        elif name == "SubagentStop":
            self.stem.handle_subagent_stop(agent_id, self.t)
        elif name == "PreCompact":
            if self.gestures_enabled:
                c3 = _midi_hz(48)
                a2 = _midi_hz(45)
                self._spawn_note_sequence(rng, [c3, a2], velocity=0.3, spacing=0.55, gain_db=4.0)
        elif name == "SessionStart":
            # Multi-session: a second (or later) window opening must not
            # duck/re-fade a soundscape another live session is still using.
            # was_live_before was captured pre-note above, so this is exactly
            # the 0 -> 1 transition, not "is this session_id new".
            first_live_session = not was_live_before
            self.sessions.note(session_id, self.t)
            self.session_started = True
            self.session_start_t = self.t
            self.session_ended = False
            self.session_end_t = None
            if first_live_session:
                self._session_fade.value = 0.0
                self._session_fade.target = 1.0
                self._session_fade.tau = 1.0
                self._end_fade.value = 1.0
                self._end_fade.target = 1.0
            if self.gestures_enabled:
                self._spawn_note(rng, ROOT_C2, velocity=0.3, gain_db=4.0)
        elif name == "SessionEnd":
            self.sessions.end(session_id)
            others_live = self.sessions.live_count(self.t) > 0
            # Multi-session: another tracked session is still live, so this
            # is one session finishing, not the daemon's only user leaving --
            # skip the global zeroing/fade-to-silence entirely (the Stop-like
            # cadence gesture above still plays; a session finishing is
            # information). Single/last session: exactly today's behavior.
            if not others_live:
                self.session_ended = True
                self.session_end_t = self.t
                # Brief section 3: "Everything fades to true silence over
                # 4 s." tau 1.3 => -24 dB at 4 s, and render_block hard-zeros
                # at 4.6 s (by which point the fade is -31 dB, so the step to
                # true zero is ~-55 dBFS in absolute terms: inaudible, no
                # click). run_render extends its tail to 6 s when the last
                # event is SessionEnd so a real 4 s release actually fits
                # inside the rendered file.
                self._end_fade.tau = 1.3
                self._end_fade.target = 0.0
                self.activity = 0.0
        elif name == "ContextPressure":
            self.set_pressure(ev.get("fill"))
        else:
            return

        log.debug(
            "event=%s tool=%s activity=%.3f theme=ambient", name, tool_name, self.activity
        )

    # -- gesture/voice spawn helpers (ingress-thread safe: only use the `rng` --
    # -- passed in, and only ever deque.append onto self._pending) ------------

    def _queue_voice(self, voice):
        """Called from either thread. Bounded append-only handoff."""
        if len(self._pending) >= MAX_PENDING_VOICES:
            return
        self._pending.append(voice)

    def _collect_voices(self):
        """Render thread only: drain _pending into the capped voice pool."""
        pend = self._pending
        for _ in range(len(pend)):
            try:
                voice = pend.popleft()
            except IndexError:
                break
            _voice_pool_add(self.voices, voice, self.sr)

    def _spawn_note(self, rng, freq, velocity=0.4, bell=False, i_peak=None, pan=None,
                    delay_s=0.0, gain_db=0.0, embed_cap_db=NOTE_EMBED_CAP_DB,
                    pitch_factor=1.0):
        # pitch_factor is the BRIEF-v2.5 per-session slot transpose. Slot 0
        # (and every non-gesture caller) passes 1.0, so `freq * 1.0 == freq`
        # bit-identically -- the single-session render stays byte-identical.
        freq = freq * pitch_factor
        sig = _render_fm_note(rng, freq, velocity, sr=self.sr, bell=bell, i_peak_override=i_peak)
        if pan is None:
            # brief-v2.2 section 5: melodic notes constrained to +-0.20 (was
            # +-0.5) -- part of the "left/right difference" fix.
            pan = rng.uniform(-NOTE_PAN_LIMIT, NOTE_PAN_LIMIT)
        # brief-v2.2 section 4 embedding rule: "any pitched one-shot must sit
        # ON the bed: note peak level <= bed RMS + 10 dB". This is now
        # relative to the CURRENT calibrated bed reference rather than a
        # fixed trim, so it holds at idle (quiet bed) as much as when busy.
        # `gain_db` may only ever pull a note quieter than the cap, never
        # push it past -- that headroom is exactly what made the v2
        # Notification chime read as "a far-away bing under pressure".
        bed_ref_db = self.bed.bed_level_db.value + BED_CAL_DB
        eff_cap_db = min(embed_cap_db, embed_cap_db + gain_db)
        sig = sig * _db_to_lin(bed_ref_db + eff_cap_db)
        stereo = _mono_to_stereo(sig.astype(np.float64), pan=pan)
        if delay_s > 0:
            lead = np.zeros((int(delay_s * self.sr), 2))
            stereo = np.concatenate([lead, stereo], axis=0)
        # brief-v2.2 section 4: "note reverb send -6 dB vs v2" -- v2 sent a
        # note's full signal into the shared room (100% wet). Notes are now
        # mostly direct (embedded in the room, not floating above it) with a
        # reduced, secondary reverb send for spatial glue.
        self._queue_voice({"buf": stereo * NOTE_DIRECT_FRAC, "pos": 0, "bus": "direct"})
        self._queue_voice({"buf": stereo * NOTE_REVERB_FRAC, "pos": 0, "bus": "reverb"})

    def _spawn_note_sequence(self, rng, freqs, velocity=0.4, spacing=0.2, gain_db=0.0, pan=None,
                             pitch_factor=1.0):
        for i, f in enumerate(freqs):
            self._spawn_note(rng, f, velocity=velocity, delay_s=i * spacing, gain_db=gain_db,
                             pan=pan, pitch_factor=pitch_factor)

    # -- render_block: theme interface leg -----------------------------------

    def render_block(self, n=BLOCKSIZE):
        sr = self.sr
        dt = n / sr

        if self.session_ended and self.session_end_t is not None and (self.t - self.session_end_t) > 4.6:
            # Fully faded (4 s fade + margin): true digital silence, and cheap.
            # Silence means "off", so it has to be real silence, not a floor.
            self.t += dt
            return np.zeros((n, 2), dtype=np.float32)
        if not self.session_started:
            self.t += dt
            return np.zeros((n, 2), dtype=np.float32)

        try:
            self._sanitize()
            # Two buses: `send` goes to the shared room (bed pad, stems, rain
            # grains, bloom notes); `dry` is everything, including the layers
            # that stay out of the room (the air bed is already diffuse, so
            # reverberating it just smears it and eats headroom).
            self._collect_voices()
            send = np.zeros((n, 2), dtype=np.float64)
            self.bed.render(send, n, dt, self.t, self.last_event_t, self.session_start_t,
                             self.fill_smooth.value)
            self.stem.render(send, n, dt)
            # v2.4: bed's own settled-until window (post-Stop, "v24" mode
            # only) also gates the bloom rate -- see BloomLayer.render's
            # `settled` docstring note. Mirrors BedLayer._bed_target_db's
            # exit condition exactly (last_event_t, not a hard `t <` cutoff)
            # so bed and bloom never fall out of settled at different times
            # -- a bed/bloom mismatch here is the original v2.2 "waking up"
            # defect this cadence work fixes.
            settled = (self.bed.done_cadence != "v22"
                      and self.bed.easing_after_stop_until is not None
                      and self.last_event_t < self.bed.easing_after_stop_until)
            self.activity_slow = self.bloom.render(n, dt, self.t, self.activity,
                                                    self.activity_slow, settled=settled)
            # "Room pause" duck (see _duck_block): applied to the SUSTAINED
            # layers only -- bed pad, stems, air, sub-bass -- and never to the
            # voice buses, so the knock itself is untouched and gains its
            # salience from contrast instead of from level.
            duck = self._duck_block(n)
            if duck is not None:
                send *= duck
            reverb_send, direct_bus = _mix_voices(self.voices, n)
            send += reverb_send

            air = np.zeros((n, 2), dtype=np.float64)
            self.rain.render_rain(air, n, dt, self.t, self.activity, self.current_class,
                                   self.fill_smooth.value)
            air += self.rain.render_air(n, dt, self.activity_med, self.bed.bed_level_db.value,
                                         self.fail_penalty_slew.value)
            if duck is not None:
                air *= duck

            cutoff = self._update_master_lowpass(dt)
            send = self._apply_master_bus_lp(send, "send", cutoff)
            air = self._apply_master_bus_lp(air, "air", cutoff)
            mono_in = 0.5 * (send[:, 0] + send[:, 1])
            wet = self.reverb.process_block(mono_in) * AMBIENT_WET_GAIN

            out = wet + (send + air) * AMBIENT_DRY_GAIN + direct_bus
            sub = self.weather.render(n, dt, self.fill_smooth.value)
            out += sub * duck if duck is not None else sub

            # brief-v2.2 section 5: mid/side ratio limited (S <= 0.5*M) --
            # the long-window |L-R| stereo-balance criterion, on top of the
            # narrower per-drop/per-note pan limits above.
            out = _limit_ms_ratio(out, MS_MAX_SIDE_OVER_MID)

            # session start/end envelope
            self._session_fade.step(dt)
            self._end_fade.step(dt)
            out *= self._session_fade.value * self._end_fade.value

            # master chain: headroom -> tanh soft clip -> DC blocker -> hard clip
            out *= _db_to_lin(AMBIENT_MASTER_HEADROOM_DB)
            out = np.tanh(out * 0.6) / 0.6
            out, self._dc_x1, self._dc_y1 = _dc_blocker(out, self._dc_x1, self._dc_y1)
            np.clip(out, -0.99, 0.99, out=out)

            vol = 0.0 if self.mute else self.volume
            out *= vol

            self._advance(dt)
            result = out.astype(np.float32)
            if not np.all(np.isfinite(result)):
                raise FloatingPointError("non-finite ambient output")
            return result
        except Exception as exc:
            # Shared with GeigerTheme's render_block fault flag (geiger.py)
            # so either theme's first fault suppresses both, matching the
            # pre-split single-module behavior.
            if not geiger._RENDER_FAULT_REPORTED:
                geiger._RENDER_FAULT_REPORTED = True
                log.error(
                    "ambient render_block fault (silencing this block; further "
                    "occurrences suppressed): %r", exc
                )
            try:
                self._advance(dt)
            except Exception:
                pass
            return np.zeros((n, 2), dtype=np.float32)

    # -- per-layer render helpers ---------------------------------------------

    def _sanitize(self):
        if not math.isfinite(self.activity):
            self.activity = 0.0
        if not math.isfinite(self.activity_slow):
            self.activity_slow = 0.0
        if not math.isfinite(self.activity_med):
            self.activity_med = 0.0
        if not math.isfinite(self.fill):
            self.fill = 0.0
        bed = self.bed
        if not math.isfinite(bed.bed_cutoff_oct):
            bed.bed_cutoff_oct = 0.0
        if not math.isfinite(bed.bed_gain_db):
            bed.bed_gain_db = 0.0
        if not np.all(np.isfinite(bed.shimmer_amp_x)):
            bed.shimmer_amp_x[:] = 0.0
        if not np.all(np.isfinite(bed.bed_phase)):
            bed.bed_phase[:] = 0.0
        if not np.all(np.isfinite(bed.shimmer_phase)):
            bed.shimmer_phase[:] = 0.0
        if not np.all(np.isfinite(bed.midlayer_amp_x)):
            bed.midlayer_amp_x[:] = 0.0
        if not np.all(np.isfinite(bed.midlayer_phase)):
            bed.midlayer_phase[:] = 0.0

    def _duck_block(self, n):
        """"Room pause" gain envelope, or None when no duck is running.

        v2.2 VERIFIER change, replacing the builder's KNOCK_EMBED_CAP_DB=23
        (a documented deviation from the brief's +14 dB that existed purely
        to keep criterion 9c's 6 dB knock transient measurable over the
        raised bed). Winning the 9c/N4 salience contest by LEVEL is exactly
        the move that turns a knock back into an alarm -- the listener
        complaint v2.2 exists to fix.

        So the knock now wins by CONTRAST instead: for ~0.45 s the sustained
        layers (bed pad, stems, air, sub-bass) dip DUCK_DEPTH_DB and come
        back. A room going quiet for a moment is a strong, non-startling
        "something happened" cue -- and unlike a louder knock it also reads
        as meaning, not as urgency. It costs the knock nothing in level:
        KNOCK_EMBED_CAP_DB is back down to 16 dB.

        Not a compressor: this is a one-shot, event-triggered, fixed-shape
        envelope with a hard 0.45 s length and a raised-cosine attack and
        release, so it cannot chatter, cannot re-trigger inside itself
        (`_duck_start_t` is only re-armed by a new event, and a new event
        during a duck simply restarts the same fixed shape), and has no
        level-dependent feedback path that could pump.
        """
        if self._duck_start_t is None and self._duck_gain_z >= 1.0 - 1e-4:
            self._duck_gain_z = 1.0
            return None
        if self._duck_start_t is not None and self.t - self._duck_start_t >= DUCK_TOTAL_S:
            self._duck_start_t = None
        if self._duck_start_t is None:
            g = np.ones(n)
        else:
            u = (self.t - self._duck_start_t) + np.arange(n) / self.sr
            d = _db_to_lin(-DUCK_DEPTH_DB)
            g = np.ones(n)
            a = DUCK_ATTACK_S
            h = a + DUCK_HOLD_S
            r = h + DUCK_RELEASE_S
            m = (u >= 0.0) & (u < a)
            g[m] = 1.0 + (d - 1.0) * (0.5 - 0.5 * np.cos(np.pi * u[m] / a))
            m = (u >= a) & (u < h)
            g[m] = d
            m = (u >= h) & (u < r)
            g[m] = d + (1.0 - d) * (0.5 - 0.5 * np.cos(np.pi * (u[m] - h) / DUCK_RELEASE_S))
        # One-pole smoothing of the shaped envelope. The shape's own corners
        # (25 ms attack, 275 ms release) are far slower than this 6 ms time
        # constant, so it is preserved; what it removes is the ONE case the
        # open-loop shape cannot handle -- a second failure arriving while a
        # duck is still running restarts the envelope at u=0, which without
        # smoothing steps the gain discontinuously (up to 3 dB in one sample)
        # and clicks. With it, any restart becomes a ~6 ms glide.
        if not _HAVE_SCIPY:
            # No scipy: skip the one-pole smoothing and use the raw shaped
            # envelope unsmoothed (rare restart-discontinuity click is a
            # fair trade for not crashing the render path -- degrade
            # gracefully contract).
            self._duck_gain_z = float(g[-1])
            return g[:, None]
        alpha = 1.0 - math.exp(-1.0 / (DUCK_SMOOTH_S * self.sr))
        # vectorised one-pole: y[i] = y[i-1] + alpha*(g[i]-y[i-1])
        y, self._duck_gain_z = _sp_signal.lfilter(
            [alpha], [1.0, -(1.0 - alpha)], g,
            zi=np.array([(1.0 - alpha) * self._duck_gain_z]))
        self._duck_gain_z = float(y[-1])
        return y[:, None]

    def _apply_lp_stage(self, x, key, cutoff):
        """Shared one-pole filter-state application: BedLayer's bed/midlayer
        lowpass stages, StemLayer's stem lowpass stages, and the master
        bus's send/air lowpass all key into the SAME self._lp_zi dict
        (distinct string keys per stage), so this stays on AmbientTheme
        rather than being owned by any one layer."""
        b, a = _onepole_lp_coeffs(cutoff, self.sr)
        zi = self._lp_zi.get(key)
        if zi is None or not _HAVE_SCIPY:
            if _HAVE_SCIPY:
                zi = _sp_signal.lfiltic(b, a, [0.0])
            else:
                return x  # rare fallback: no filtering rather than a slow loop here
        y, zf = _sp_signal.lfilter(b, a, x, zi=zi)
        self._lp_zi[key] = zf
        return y

    def _update_master_lowpass(self, dt):
        """Advance the L5 pressure/failure state and return this block's
        master cutoff (None = bypass)."""
        self.fill_smooth.target = self.fill
        self.fill_smooth.step(dt)
        f = self.fill_smooth.value
        self.fail_penalty_slew.step(dt)

        if f <= 0.5:
            cutoff_target = 6000.0
        else:
            frac = min(1.0, (f - 0.5) / 0.5)
            cutoff_target = 6000.0 + (2500.0 - 6000.0) * frac
        cutoff_target = max(1800.0, cutoff_target - self.fail_penalty_slew.value)
        self.master_lp_cutoff.target = cutoff_target
        self.master_lp_cutoff.step(dt)
        if self.master_lp_cutoff.value >= 5900.0:
            return None  # essentially bypassed; skip the filter calls
        return self.master_lp_cutoff.value

    def _apply_master_bus_lp(self, buf, tag, cutoff):
        if cutoff is None:
            return buf
        for ch in range(2):
            buf[:, ch] = self._apply_lp_stage(buf[:, ch], f"master_{tag}_{ch}", cutoff)
        return buf

    def _advance(self, dt):
        self.t += dt
        alpha = 1.0 - math.exp(-dt / ACTIVITY_MED_TAU)
        self.activity_med += (max(0.0, min(1.0, self.activity)) - self.activity_med) * alpha
        if not math.isfinite(self.activity):
            self.activity = 0.0
        if self.activity > 0:
            self.activity *= math.exp(-dt / TAU_ACTIVITY)
            if self.activity < 1e-3:
                self.activity = 0.0


