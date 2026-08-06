# Clawdio

_Hear what a Claude Code session is doing without watching the terminal._

---

Clawdio is a local audio daemon. Claude Code hook events (tool calls, subagents, failures, compaction, context pressure) arrive over UDP or HTTP on port 9753, and the daemon turns them into a generative ambient soundscape: a pad that breathes, rain that thickens when the agent is busy, a wooden knock on a failure, a descending chime when it stops. A session becomes audible in the background while you work on something else. Research prototype, formerly claude-geiger. Needs python3 + numpy + scipy; `sounddevice` is only for live playback, offline `--render` runs without it.

The sound layer is a swappable **theme**. `ambient` (default) is the pad + rain + bloom soundscape. `geiger` (legacy v1, `SONIFIER_THEME=geiger`) is the original Geiger-counter click train and needs no scipy. Everything else, ingress, ports, hooks, CLI, config, is the same across themes.

## Quickstart

```bash
uv run src/sonifier.py --check    # zero setup: prints config + capability JSON
python3 src/sonifier.py           # live: bind port 9753, play audio (needs sounddevice)
```

`uv run` reads the PEP 723 header in `src/sonifier.py` and builds a venv on the fly, so `--check` works with nothing installed. For live audio, install the deps and let Claude Code start the daemon for you (below).

## Install (for AI agents)

A non-interactive checklist. Each step has a command and an explicit success signal. Nothing here needs a tty; nothing modifies real settings until step 3.

1. **Confirm the daemon runs at all.** No install, no venv.

   ```bash
   uv run src/sonifier.py --check
   ```

   Success: JSON on stdout, exit 0, with `"port": 9753` and `"theme": "ambient"`. `"audio_device_available"` may be `false` on a headless box; that is fine, live playback is optional.

2. **Install the Python deps** (for live playback and to skip the per-run `uv` build).

   ```bash
   python3 -m pip install -r requirements.txt   # numpy, scipy, sounddevice
   ```

   Success: exit 0. `numpy` and `scipy` are required; `sounddevice` is only for live audio.

3. **Wire the hooks into Claude Code settings.** Pick a scope.

   ```bash
   ./install.sh --project --yes    # this repo's .claude/settings.json only
   ./install.sh --global --yes     # $CLAUDE_CONFIG_DIR/settings.json (or ~/.claude), every project
   ```

   The installer hard-fails (exit 1) if numpy is missing, so a green run means the daemon can render. It backs up the existing `settings.json` to `settings.json.bak.<timestamp>` before touching it, merges additively (your own hooks for the same event are kept), and prints which interpreter it will use (`<repo>/.venv/bin/python` if present, else `python3`, the same rule the autostart hook follows). Re-running changes nothing and writes no new `.bak`. To preview without writing anything:

   ```bash
   ./install.sh --project --dry-run    # validates existing settings.json is valid JSON, changes nothing
   ```

   Success: `merged hooks into <path>` or `already up to date; no changes`.

4. **Re-check that the config the daemon will boot with is what you expect.**

   ```bash
   python3 src/sonifier.py --check
   ```

   Success: exit 0, `"theme"` and `"port"` match your intent.

5. **Smoke-test the audio engine offline**, no hardware needed.

   ```bash
   python3 src/sonifier.py --render demos/demo-session-v2.jsonl /tmp/smoke.wav --seed 7
   ```

   Success: `rendered N events -> /tmp/smoke.wav`, exit 0, a ~180s stereo WAV on disk.

After this, start Claude Code from the repo (for `--project`) or any project (for `--global`): the `SessionStart` hook launches the daemon if nothing answers on 9753, and every other hook fires a UDP datagram at it.

Unknown flags to `sonifier.py` exit 2 instead of starting the daemon. `python3 src/sonifier.py --help` prints the usage.

To undo everything, see [Uninstall](#uninstall).

## HTTP and UDP surface

The daemon binds `0.0.0.0:SONIFIER_PORT` (default 9753) and speaks both HTTP and UDP.

| Endpoint        | Scope         | What it does                                                                                                                                   |
| --------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /event`   | LAN-open      | Body is the raw Claude Code hook JSON. Always answers `200`; bodies over 1 MiB get `413`; malformed JSON is dropped, never errors the hook.    |
| UDP datagram    | LAN-open      | Same JSON body, same port. What `hooks/send-event.sh` uses, fire-and-forget, never blocks the hook.                                            |
| `GET /health`   | LAN-open      | `{"ok": true, "activity": <float>}`.                                                                                                           |
| `GET /config`   | loopback only | Current config, its file path, and which keys need a restart.                                                                                  |
| `POST /config`  | loopback only | Merge a JSON object of config keys; live keys (volume, mute, clicks, chimes, drone) apply at once, the rest on restart.                        |
| `POST /restart` | loopback only | Re-exec the daemon in place so restart-only keys (theme, port) take effect.                                                                    |
| `GET /`         | loopback only | A static web control panel served from `web/`. Open `http://127.0.0.1:9753/` in a browser to see the config as a rack unit and change it live. |

Config mutation and the panel are loopback-only because the daemon has no auth; only event ingress is open to the LAN.

## Config

Every knob is an env var. A config file (written by the web panel) sits on top.

| Var                      | Default                         | Meaning                                                                                                          |
| ------------------------ | ------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `SONIFIER_THEME`         | `ambient`                       | sound layer: `ambient` (v2) or `geiger` (legacy v1)                                                              |
| `SONIFIER_PORT`          | `9753`                          | UDP/HTTP listen port                                                                                             |
| `SONIFIER_VOLUME`        | `0.5`                           | master volume 0.0-1.0; `1.0` is a normal master level, the `0.5` default is 6 dB under, for background listening |
| `SONIFIER_MUTE`          | off                             | mute output; daemon keeps running and answering `/health`                                                        |
| `SONIFIER_IDLE_EXIT_MIN` | `30`                            | minutes of silence before the daemon exits on its own                                                            |
| `SONIFIER_CLICKS`        | on                              | `geiger`: the click train. `ambient`: the rain layer                                                             |
| `SONIFIER_CHIMES`        | on                              | `geiger`: one-shot chimes. `ambient`: discrete gestures (knock, cadence, chime, ack)                             |
| `SONIFIER_DRONE`         | off                             | `geiger`: the context-pressure drone. `ambient`: unused (weather is event-driven)                                |
| `SONIFIER_QUIET`         | off                             | suppress per-event stderr logging                                                                                |
| `SONIFIER_LOG_DIR`       | `$TMPDIR` or `/tmp`             | where `hooks/autostart-daemon.sh` writes `sonifier.log`                                                          |
| `SONIFIER_LOG_LEVEL`     | `INFO`                          | console log level: DEBUG/INFO/WARNING/ERROR (WARNING under `SONIFIER_QUIET`)                                     |
| `SONIFIER_LOG_FILE`      | unset                           | path to a rotating debug log (2 MB x 3); always captures DEBUG                                                   |
| `SONIFIER_CONFIG`        | `~/.config/clawdio/config.json` | override the config file path                                                                                    |

Boolean vars are off for `0`/`false`/`off`/`no` (case-insensitive), on for any other non-empty value.

**Config-file precedence.** The daemon reads `~/.config/clawdio/config.json` (falling back read-only to the old `~/.config/agent-sonifier/config.json`), and `SONIFIER_CONFIG` overrides that path. **File values override env vars**, not the other way around. This is intentional: the web panel writes the file, so a change you make in the panel wins over a stale env var. The daemon logs the file path at startup when the file exists.

## Themes

`ambient` (default, v2.4) is five layers sharing one Freeverb room, all built from a fixed C-major-pentatonic pool so any coincidence of notes is consonant: an always-on bed pad + air bed, a rain-grain stream (one drop per tool event plus an activity-driven Poisson rate, capped so heavy work thickens the rain rather than speeding up the taps), a self-playing melodic bloom, subagent stem pads that fade with subagent presence, and a context-pressure sub-bass drone. A failure is a low wooden knock and the room darkens; `Stop` is a short descending cadence; `Notification` is a held breath and a soft chime. The full event-to-sound table and the synthesis spec live in `docs/research/BRIEF-v2*.md`.

`geiger` (`SONIFIER_THEME=geiger`) is the v1 sound: a Poisson click train whose rate follows the activity integrator, read/write/exec click timbres, a falling minor-second dyad on failure, a rising fifth on `Stop`. It needs no scipy.

## Offline render and demos

`--render` turns an event script into a WAV with no audio hardware, and is the main way to verify a DSP change.

```bash
python3 src/sonifier.py --render events.jsonl out.wav          # 48kHz stereo 16-bit WAV
python3 src/sonifier.py --render events.jsonl out.wav --seed 7 # fixed RNG, byte-reproducible
```

`events.jsonl` is one JSON object per line: `{"t": <seconds>, "event": {<hook JSON>}}`. Lines that do not parse, or lack `t`/`event`, are skipped. Rendered duration is the last event's `t` plus a 3s tail (6s when the script ends the session, so the ambient release resolves into real silence).

Three demo renders of the current engine ship under `demos/`, all rendered with `SONIFIER_VOLUME=1.0 --seed 7`:

| render script                   | what it is                                                                          |
| ------------------------------- | ----------------------------------------------------------------------------------- |
| `demos/realistic-session.jsonl` | a real-cadence session (~176s), the blind-listening render                          |
| `demos/focus-session-v2.jsonl`  | 60s of steady medium activity: ordinary work, no failures                           |
| `demos/demo-session-v2.jsonl`   | the full 180s storyboard: every layer and gesture                                   |
| `demos/demo-session.jsonl`      | the original v1 showcase (render with `SONIFIER_THEME=geiger` for the legacy sound) |

You can generate your own script instead of using a bundled one:

```bash
python3 src/simulate_session.py --emit-jsonl my-session.jsonl
python3 src/sonifier.py --render my-session.jsonl /tmp/out.wav
```

Or fire a realistic ~60s session at a running daemon in real time:

```bash
python3 src/simulate_session.py            # over UDP
python3 src/simulate_session.py --speed 4  # 4x realtime
python3 src/simulate_session.py --http     # HTTP POST /event instead of UDP
```

## Tests and tooling

```bash
python3 -m pytest tests/ -q    # 126 tests: ambient theme, geiger DSP, config API
```

Tests are lenient on purpose (non-silence, rough levels, determinism, no-NaN) to avoid RNG flakiness. The strict psychoacoustic battery runs against renders, not in pytest:

```bash
python3 tools/lab.py steady 60                     # render a scenario + run the metric battery in-process
python3 tools/analyze_render.py out.wav --arc      # PASS/FAIL table for the BRIEF section-7 criteria
python3 tools/complaint_checks.py out.wav \
        --events demos/realistic-session.jsonl     # regression checks for known listener complaints
```

`tools/analyze_render.py` scores a render against the numeric criteria in the briefs; `--steady A:B` picks the constant-activity window the strict items measure on, `--events FILE` enables the information and drop-rate checks. `tools/complaint_checks.py` encodes the v2 blind listener's verbatim feedback ("dark cave", "far-away bing", "too fast") as measurements, so a re-tune cannot reintroduce one. `tools/lab.py` is the faster in-process harness for the same measurements while tuning.

The design is spec-driven: `docs/research/BRIEF-v2*.md` are the authoritative synthesis specs, `docs/PROJECT.md` is the design history, and the blind-listening evaluation kit lives in `eval/` (see `eval/README.md`).

## Uninstall

```bash
./install.sh --uninstall --project    # or --global, same scope as install
```

Surgical: it removes only this repo's hook entries (send-event / autostart-daemon) from the target `settings.json`, backs the file up first, and is a no-op if there is nothing to remove. Then kill a running daemon if you want it gone now:

```bash
pkill -f sonifier.py    # or let it exit after SONIFIER_IDLE_EXIT_MIN minutes of silence
```

## Troubleshooting

- **Ambient sounds thin or missing layers**: scipy is not installed. `pip install -r requirements.txt`. AmbientTheme degrades gracefully without it (filters become no-ops), never crashes; `geiger` needs no scipy.
- **No sound**: live playback needs `sounddevice` and a working PortAudio output device. Offline `--render` never needs it.
- **Port busy**: another process is on 9753. Kill it, or run daemon and hooks with `SONIFIER_PORT=9800` (export it before Claude Code launches hooks so `send-event.sh` picks it up too).
- **Hooks fire but nothing happens**: check `${TMPDIR:-/tmp}/sonifier.log`, or run `python3 src/sonifier.py --check`.
