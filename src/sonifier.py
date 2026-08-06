#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy",
#     "scipy",
# ]
# [tool.uv]
# # sounddevice is OPTIONAL: only required for live audio playback (not --render).
# ///
"""Clawdio sonifier.

Real-time audio sonification daemon for Claude Code. Turns Claude Code hook
events into an audio soundscape describing the session. As of v2 the default
sound is "ambient" (generative pad + rain + melodic bloom, see
AmbientTheme / BRIEF-v2.md); the original v1 Geiger-counter click-train sound
is preserved verbatim as GeigerTheme and selectable via SONIFIER_THEME=geiger.

Usage:
    python3 sonifier.py                          # live mode (needs sounddevice)
    python3 sonifier.py --render events.jsonl out.wav [--seed N]
    python3 sonifier.py --check                  # print config/capability JSON

Env SONIFIER_THEME selects the sound layer: "ambient" (default) or "geiger"
(legacy v1 sound). Everything else (ingress, ports, CLI, hooks) is theme-
agnostic and unchanged between v1 and v2.

See the module docstrings on render_block / EngineState (GeigerTheme) /
AmbientTheme / classify for the core DSP + mapping logic.

This module is now a thin entry point + backward-compat facade: the actual
implementation lives in sibling modules (config, classify, dsp, geiger,
ambient_layers, ambient, io_modes), split out for maintainability. Every
name that used to live directly in this file is re-exported here so
existing callers (tests/, tools/, eval/, hooks/) keep working unchanged.
"""

from __future__ import annotations

import sys

from logging_setup import configure as _configure_logging, get_logger

log = get_logger("sonifier")

# --------------------------------------------------------------------------
# Re-exports (backward-compat facade) -- see the respective modules for
# implementation and docstrings.
# --------------------------------------------------------------------------

from config import (  # noqa: E402,F401
    VERSION,
    SAMPLE_RATE, BLOCKSIZE, TAU_ACTIVITY, SLEW_TAU, MAX_CLICK_RATE,
    MAX_BODY_BYTES, MAX_ACTIVE_CHIMES, HTTP_READ_TIMEOUT,
    CLASS_READ, CLASS_WRITE, CLASS_EXEC, TOOL_CLASS,
    READ_BASH_CMDS, GIT_READ_SUBCOMMANDS, TIMBRE,
    THEME_GEIGER, THEME_AMBIENT,
    _env_float, _env_bool_flag, _env_int, _env_theme, load_config,
)
from classify import classify, _classify_bash_command, SessionTracker  # noqa: E402,F401
from dsp import (  # noqa: E402,F401
    Slew, _env_adsr, _sine,
    render_failure_chime, render_done_chime, render_attention_chime,
    render_swish, render_compact_chime,
    _limit_ms_ratio, _mono_to_stereo, build_chime_bank, _make_click_grain,
)
from geiger import (  # noqa: E402,F401
    EngineState, GeigerTheme, render_block,
    _click_rate_from_activity, _emit_click, _RENDER_FAULT_REPORTED,
    _geiger_render_block, _current_click_rates, _render_clicks,
    _render_chimes, _render_drone,
)
from ambient_layers import (  # noqa: E402,F401
    _PENT_DEGREES, _midi_hz, _build_pentatonic_pool,
    AMBIENT_NOTE_POOL, AMBIENT_NOTE_WEIGHTS, AMBIENT_NOTE_POOL_HZ,
    AMBIENT_MID_REGISTER_IDXS, AMBIENT_MID_REGISTER_WEIGHTS,
    STEPWISE_BIAS_STEPS, STEPWISE_BIAS_PROB, BLOOM_IDLE_SLOW_ACTIVITY_THRESH,
    ROOT_C1, ROOT_C2, ROOT_C3, ROOT_C4, ROOT_G2, ROOT_G3,
    MAX_AMBIENT_VOICES, ACTIVITY_BUMP,
    DROP_RATE_MIN, DROP_RATE_CAP, DROP_RATE_LOG_BASE, DROP_MIN_GAP_S,
    BURST_COALESCE_WINDOW_S, DROP_RATE_SCALE, BURST_COALESCE_STEP_DB,
    BURST_COALESCE_MAX_DB, RATE_SLEW_TAU_S, RATE_HYSTERESIS,
    WASH_CROSSFADE_RATE, WASH_CROSSFADE_A, NOTE_MIN_GAP_S, DROP_PAN_LIMIT,
    NOTE_PAN_LIMIT, MS_MAX_SIDE_OVER_MID,
    DONE_CADENCE_MODES, SETTLED_HOLD_S, SETTLED_BED_DB, SETTLED_BED_TAU_S,
    SETTLED_BLOOM_RATE_SCALE,
    _drop_rate_from_activity, _ou_step, _raised_cosine_attack,
    _rbj_bandpass, _onepole_lp_coeffs, _dc_blocker,
    FV_COMB_L, FV_COMB_R, FV_ALLPASS_L, FV_ALLPASS_R, FV_FEEDBACK, FV_DAMP,
    FV_ALLPASS_G, FV_INPUT_GAIN, FV_DENORM, Freeverb,
    MAX_PENDING_VOICES, MAX_VOICE_TAILS, _voice_pool_add, _mix_voices,
    DROP_TIMBRES, _render_drop_woodblock, _render_drop_marimba,
    _render_drop_plink, _render_one_drop_variant, _DROP_RENDER_FNS,
    _build_drop_bank, _render_fm_note, _render_knock, _nearest_pool_note,
    _build_cadence_notes, _build_cadence_notes_v24, _pick_write_register_note,
    BED_OU_TAU, BED_OU_EXCURSION_SCALE, BED_CUTOFF_OU_SIGMA_OCT,
    BED_GAIN_OU_SIGMA_DB, SHIMMER_OU_TAU, SHIMMER_OU_SIGMA,
    MIDLAYER_OU_TAU, MIDLAYER_OU_SIGMA, MIDLAYER_FREQS, MIDLAYER_LP_HZ,
    BED_LP_BASE_HZ, BED_LP_MAX_HZ, SHIMMER_HARMONICS, SHIMMER_SUS2_SLOT,
    SHIMMER_FIFTH_SLOT, SHIMMER_SUS4_RATIO, _VI_RATIO,
    SUPERSAW_VOICES, SUPERSAW_CENTS, SUPERSAW_AMPS,
    AMBIENT_WET_GAIN, AMBIENT_DRY_GAIN, AMBIENT_MASTER_HEADROOM_DB,
    BED_CAL_DB, MIDLAYER_CAL_DB, AIR_CAL_DB, DROP_CAL_DB,
    DROP_AMP_SPREAD_DB, NOTE_EMBED_CAP_DB, NOTE_EMBED_CAP_IDLE_DB,
    KNOCK_EMBED_CAP_DB, DUCK_DEPTH_DB, DUCK_ATTACK_S, DUCK_HOLD_S,
    DUCK_RELEASE_S, DUCK_TOTAL_S, DUCK_SMOOTH_S, NOTE_REVERB_SEND_DB,
    NOTE_DIRECT_FRAC, NOTE_REVERB_FRAC, SUBBASS_CAL_DB,
    STEM_CAL_DB, STEM_DETUNE_CENTS, STEM_LP_HZ, SUBAGENT_PRESENCE_DECAY_S,
    WHOOSH_CAL_DB, AIR_TILT_LO_HZ, AIR_TILT_HI_IDLE_HZ, AIR_TILT_HI_ACTIVE_HZ,
    AIR_HP_HZ, AIR_DECORR, ACTIVITY_MED_TAU, AIR_GLOOM_DEPTH,
    AIR_FLOOR_OFFSET_DB, AIR_ACTIVITY_RANGE_DB, AIR_WASH_BOOST_RANGE_DB,
    AIR_WASH_BOOST_HZ, AIR_V23_LEVEL_CUT_DB, AIR_V23_HARD_CEILING_HZ,
    _PINK_POLES, AmbientConfig, AMBIENT_CONFIG, _air_norm_factor, _db_to_lin,
    StemLayer, WeatherLayer, BedLayer, RainLayer, BloomLayer,
)
from ambient import AmbientTheme  # noqa: E402,F401
from io_modes import (  # noqa: E402,F401
    _build_theme_state, run_render, _write_wav,
    _EventHTTPHandler, _make_http_server, _udp_recv_loop, run_live, run_check,
    sd, _SD_IMPORT_ERROR,
)

try:
    from scipy import signal as _sp_signal  # type: ignore  # noqa: F401
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover - environment dependent
    _sp_signal = None
    _HAVE_SCIPY = False


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

USAGE = """\
usage: sonifier.py [--check | --render IN.jsonl OUT.wav [--seed N] | --help]

  (no args)               run the live daemon (needs sounddevice)
  --check                 print config/capability JSON, exit
  --render IN.jsonl OUT.wav [--seed N]
                          offline render, no audio hardware needed
  -h, --help              show this message and exit

Key env vars: SONIFIER_PORT (9753), SONIFIER_THEME (ambient|geiger),
SONIFIER_VOLUME, SONIFIER_MUTE=1, SONIFIER_IDLE_EXIT_MIN, SONIFIER_LOG_DIR.
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if "--help" in argv or "-h" in argv:
        print(USAGE, end="")
        return 0

    known_flags = ("--check", "--render", "--seed")
    for arg in argv:
        if arg.startswith("--") and arg not in known_flags:
            print(f"unknown flag: {arg}", file=sys.stderr)
            print(USAGE, end="", file=sys.stderr)
            return 2

    _configure_logging(quiet=_env_bool_flag("SONIFIER_QUIET", False))

    if "--check" in argv:
        return run_check()

    if "--render" in argv:
        idx = argv.index("--render")
        try:
            events_path = argv[idx + 1]
            out_path = argv[idx + 2]
        except IndexError:
            print("usage: sonifier.py --render events.jsonl out.wav [--seed N]", file=sys.stderr)
            return 2
        seed = 0
        if "--seed" in argv:
            sidx = argv.index("--seed")
            try:
                seed = int(argv[sidx + 1])
            except (IndexError, ValueError):
                seed = 0
        run_render(events_path, out_path, seed=seed)
        return 0

    run_live()
    return 0


if __name__ == "__main__":
    sys.exit(main())
