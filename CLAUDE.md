# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Claudio (formerly claude-geiger): a local audio daemon (`src/sonifier.py`) that turns Claude Code hook events (UDP/HTTP JSON on port 9753) into a generative ambient soundscape, so a session is audible without watching the terminal. Research prototype. Requires python3 + numpy + scipy; `sounddevice` only for live playback (offline `--render` works without it).

## Commands

```bash
# Tests (pytest; no config file: tests insert src/ on sys.path themselves)
python3 -m pytest tests/ -v
python3 -m pytest tests/test_ambient.py -k test_failure_knock_localized_transient -v

# Run the daemon live (or let the SessionStart hook autostart it)
python3 src/sonifier.py
python3 src/sonifier.py --check            # print config/capability JSON, exit

# Offline render (no audio hardware needed): the main way to verify DSP changes
python3 src/sonifier.py --render demos/demo-session-v2.jsonl /tmp/out.wav --seed 7

# Fire a realistic simulated session at a running daemon
python3 src/simulate_session.py [--speed 4] [--http]

# Analysis / tuning loop (run from repo root; lab.py expects src/ + tools/ on path)
python3 tools/lab.py steady 60             # render a scenario + run the metric battery
python3 tools/analyze_render.py <wav>      # psychoacoustic metrics on a render
python3 tools/complaint_checks.py          # regression checks for known listener complaints

# Install hooks into Claude Code settings
./install.sh [--global|--project] [--yes|--no-merge|--dry-run]
```

## Architecture

- **`src/` is split into one module per concern** (`sonifier.py` re-exports every name, so callers in tests/, tools/, eval/, hooks/ keep working):
  - `sonifier.py` (190 ln) entry point + backward-compat re-export facade + CLI arg parsing
  - `config.py` constants, env-var helpers, config-file layer, `load_config()`/`save_config()`
  - `classify.py` event `classify()` decision table (tool -> read/write/exec, subagent, etc.); also `SessionTracker`, which tracks live `session_id`s so multi-session daemons don't let one session's `SessionEnd` silence a room another session is still using, and owns the per-session gesture voice-slot table (pan/pitch, BRIEF-v2.5)
  - `dsp.py` shared DSP primitives: chime/click grain builders, ADSR, stereo helpers
  - `geiger.py` `GeigerTheme` (legacy v1 click-train), `EngineState`, `render_block`
  - `ambient_layers.py` the AmbientTheme layers: bed, rain, bloom, stems, weather, Freeverb
  - `ambient.py` `AmbientTheme` (assembles the layers into one theme)
  - `themes.py` theme packs: `PACK_SCHEMA`, `list_packs()`/`load_pack()`, JSON overrides of `AmbientConfig`
  - `io_modes.py` ingress (UDP + `ThreadingHTTPServer`), `run_render`/`run_live`/`run_check`
  - `logging_setup.py` centralised logging (`get_logger`, console + optional rotating file)
  - `simulate_session.py` fires or emits a simulated session
- **Ingress** (`io_modes.py`): UDP + HTTP on `SONIFIER_PORT`. Everything outside the theme (ingress, ports, CLI, env contract) is theme-agnostic.
- **Config layer** (`config.py`): defaults < env vars < config file; the daemon logs the file path at startup.
- **Themes**: `AmbientTheme` (default) and `GeigerTheme` (legacy v1, `SONIFIER_THEME=geiger`, no scipy needed). AmbientTheme degrades gracefully without scipy (filters become no-ops), never crashes.
- **Multi-session gating**: both themes only zero activity / fade to silence on `SessionEnd` when `SessionTracker` shows no other tracked session still live, so a second open window doesn't get cut off by the first one closing.
- **Determinism**: `--seed` makes renders reproducible; tests and eval clips depend on this. Audio is generated per-block (`render_block`, 48 kHz, blocksize 256); the render path and live path share the same engine.
- **Design is spec-driven**: `docs/research/BRIEF-v2*.md` are the authoritative synthesis specs per version; `docs/PROJECT.md` is the dossier/design history. Changes to the sound should trace to a brief; blind-listener feedback rounds live in `eval/`.
- **Hooks**: `hooks/send-event.sh` fire-and-forget UDP per hook event; `hooks/autostart-daemon.sh` launches the daemon on SessionStart if 9753 isn't answering. This repo's own `.claude/settings.json` wires them up, so a sonifier may be running while you edit it. To hear changes: `curl -X POST http://127.0.0.1:9753/restart` re-execs the daemon in place; no pkill needed.
- **Tests are deliberately lenient** (non-silence, rough levels, determinism, no-NaN) to avoid RNG flakiness. The strict psychoacoustic battery lives in `tools/analyze_render.py`, run against renders, not in pytest.

## Env contract (main knobs)

`SONIFIER_PORT` (9753), `SONIFIER_THEME` (ambient|geiger), `SONIFIER_THEME_PACK` (ambient theme pack name, restart key, see `docs/THEMES.md`), `SONIFIER_VOLUME`, `SONIFIER_MUTE=1`, `SONIFIER_IDLE_EXIT_MIN`, `SONIFIER_CLICKS`, `SONIFIER_CHIMES`, `SONIFIER_DRONE`, `SONIFIER_QUIET`, `SONIFIER_CONFIG` (config-file path), `SONIFIER_LOG_LEVEL`, `SONIFIER_LOG_FILE` (rotating debug log), `SONIFIER_LOG_DIR` (where `hooks/autostart-daemon.sh` writes `sonifier.log`).
