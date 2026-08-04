# claude-geiger (agent-sonifier)

Generative ambient audio sonification for Claude Code sessions. Claude Code hook events (tool calls, subagents, failures, compaction, context pressure, ...) are piped over UDP/HTTP to a small local daemon (`src/sonifier.py`) that turns them into a soundscape, so you can hear roughly what an agent session is doing without watching the terminal. This is a research spike / prototype, not a polished product — expect rough edges.

As of v2 the sound layer is a swappable **theme**:

- **`ambient`** (default): a generative pad + rain + melodic-bloom soundscape designed to be pleasant for hours of background listening (see `docs/research/BRIEF-v2.md` and "How it sounds" below).
- **`geiger`** (legacy v1, `SONIFIER_THEME=geiger`): the original Geiger- counter click train + chimes + optional drone.

Everything else — ingress (UDP/HTTP), ports, hooks, CLI (`--render`, `--check`, `--seed`), the rest of the env contract — is identical between the two themes; only the sound layer changes.

## Install

1. Check dependencies and prepare hook scripts:

   ```
   ./install.sh
   ```

   This checks for `python3`, `numpy`, and `scipy` (all required — `scipy` is used by the default `ambient` theme's Freeverb/filtering; the legacy `geiger` theme runs without it), notes that `sounddevice` is optional (only needed for live audio playback — offline `--render` works without it), and makes `hooks/*.sh` executable.

2. Wire the hooks into Claude Code. If you have `jq` installed, `install.sh` offers to merge `hooks/settings-snippet.json` into `~/.claude/settings.json` for you (always backing up the existing file to `settings.json.bak.<timestamp>` first — it never overwrites blind, and merging is additive: any hooks you already had for the same event are kept, not replaced). While merging it rewrites the snippet's `${CLAUDE_PROJECT_DIR}/hooks/...` commands to this project's absolute path, because `~/.claude/settings.json` applies to _every_ project and a project-relative command would break in all the others. Without `jq`, or if you decline, it prints the same instructions for a manual merge. You can also run it non-interactively:

   ```
   ./install.sh --yes            # auto-merge without prompting
   ./install.sh --no-merge       # never touch settings.json, just print instructions
   ./install.sh --dry-run        # show what would happen, change nothing
   ```

3. Start (or just use) Claude Code from this project directory. The `SessionStart` hook auto-launches `src/sonifier.py` in the background (`nohup`, detached) if nothing is already answering on `127.0.0.1:9753`; every other hook fires a UDP datagram at it. You can also start it by hand:
   ```
   python3 src/sonifier.py
   ```

## How it sounds

### `ambient` theme (default, v2.2 "Warm Room")

Five audible layers sharing one Freeverb "room" (see `docs/research/BRIEF-v2.md` for the full synthesis spec and `docs/research/BRIEF-v2.2.md` for the amendment below): a supersaw+shimmer bed pad (plus a v2.2 mid-register C3+G3 warmth layer) plus a continuous shaped-noise "air" bed, both always on while the session is alive, a rain-grain stream that's the direct replacement for v1's clicks (individual drops per tool event plus a Poisson rate driven by the same activity leaky-integrator v1 used), a slow self-playing FM e-piano melodic "bloom" (Eno/Bloom-style — it plays quietly on its own when idle, faster when busy), subagent "stem" pads that fade in/out with subagent presence, and a context-pressure sub-bass "weather" drone. Everything is built from a fixed C-major-pentatonic note pool, so any coincidence of notes is consonant by construction.

**v2.2 amendment ("Warm Room")** — the first blind-listener pass on v2 came back "dark cave / isolated / confusing / anxiety-inducing" (earphones, n=1; see `docs/research/BRIEF-v2.2.md` section 0 for the verbatim quotes). v2.2 fixes the six root causes it identified, without touching the legacy `geiger` theme or the layer architecture above:

- **Pacing**: discrete-drop rate is now a _compressive_ (logarithmic) map of activity, hard-capped at **6 drops/sec** (was an exponent-1.3 map reaching ~40/sec). Activity beyond the cap doesn't add more drops — it crossfades into a thicker continuous rain-wash bed instead ("heavier work = thicker rain", not "faster taps"). Events within 250ms of each other coalesce into one slightly-louder drop (1 event = 1 drop, never 2-3 stacked). Minimum inter-drop gap 150ms, melodic notes 2.5s.
- **Room**: the Freeverb room shrank (roomsize 0.78, damp 0.45, wet -6dB vs v2) — target RT60 ~1.0-2.2s, a warm room instead of a cathedral/cave.
- **Brightness**: mix spectral centroid raised toward 350-1200Hz (v2 measured ~157Hz) via the new mid-register bed layer and an opened master lowpass (~6kHz default).
- **Bed presence**: the bed — the "control anchor" — is louder and steadier (active ≈-30dBFS RMS, idle ≈-36dBFS, OU drift excursions halved) so there's a continuously-audible "engine humming", never a void with occasional sounds.
- **Unambiguous drops**: rain drops are now short filtered-noise ticks (was a downward sine chirp, which read as a bird call to the blind listener) — reads as rain/typewriter, not wildlife.
- **No more lonely bings**: the r=3.5 FM bell voice (the "far-away bing" that put the listener "under pressure") is deleted from the routine Stop/PreCompact/bloom flow entirely; a softened version survives ONLY for `Notification`/`PermissionRequest`, where "something needs you" is the intended message. All pitched one-shots now sit embedded on the bed (peak ≤ bed + 10dB, knock exempt) instead of floating over near-silence.
- **Stereo discipline**: drop pan ±0.35, note pan ±0.2 (was full-width random), mid/side ratio limited — fixes the "left/right difference" listener complaint.
- **Predictability**: the self-playing melodic bloom is now biased toward stepwise motion (next note within ±2 pool steps of the previous one) instead of freely random pool draws.

| Signal                                           | Sound                                                                                                                                                                                                                                         |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Session alive, no recent events                  | bed pad continues (fading quieter over minutes, but never disappearing — v2.2 raised the idle floor), melodic bloom self-plays roughly one note every ~45s, mid-register and stepwise — audibly alive, never digital silence                  |
| `PreToolUse` (Read/Grep/Glob/WebFetch/WebSearch) | 1 noise-tick rain drop (bright, ~1.8-3.5kHz band)                                                                                                                                                                                             |
| `PreToolUse` (Write/Edit/NotebookEdit)           | 1 rain drop; a chance of a mid-register melodic note ("making things"), embedded on the bed                                                                                                                                                   |
| `PreToolUse` (Bash, exec-class)                  | 1 lower-pitched rain drop (center freq ×0.7); a soft 150-400Hz noise "whoosh" swells in while the command runs                                                                                                                                |
| Sustained tool activity                          | rain-drop rate rises on a compressive log curve, hard-capped at 6/sec; activity beyond that thickens the continuous rain-wash bed instead of adding more discrete drops — the "instant activity" readout, now legible instead of overwhelming |
| `PostToolUseFailure`                             | a low wooden "knock" transient (modal, ~190Hz) and the room quietly darkens (bass shades toward the relative minor, master lowpass drops) — no stinger, no volume spike                                                                       |
| `Stop`                                           | a quiet 2-4 note descending pentatonic cadence landing on C or G, centered (no random pan), embedded on the bed; any failure-darkening lifts back over ~5s                                                                                    |
| `Notification` / `PermissionRequest`             | a "held breath": bed briefly dips to idle level plus one soft two-strike high wind-chime figure (the one place the softened bell voice survives)                                                                                              |
| `UserPromptSubmit`                               | one soft mid acknowledgment note, bed swells back to active level                                                                                                                                                                             |
| `SubagentStart` / `SubagentStop`                 | a shimmer-pad "stem" (and, with 2+ subagents, a low fifth-drone stem) fades in/out — presence itself is the signal, no spawn swish                                                                                                            |
| `PreCompact`                                     | a soft 2-note descending "settling" gesture, no bell                                                                                                                                                                                          |
| `ContextPressure` (`fill` 0.0-1.0)               | above ~0.5: a sub-bass drone fades in and the whole mix's lowpass slides down (a gathering-weather feel); above ~0.85 the bed recolors slightly darker                                                                                        |
| `SessionStart`                                   | bed fades in over ~3s plus one low root note                                                                                                                                                                                                  |
| `SessionEnd`                                     | everything fades over ~4s to true digital silence (`--render` extends the tail to 6s so the release fits in the file)                                                                                                                         |

### `geiger` theme (legacy v1, `SONIFIER_THEME=geiger`)

| Signal                                           | Sound                                                                              |
| ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| Idle / no recent activity                        | near-silence (activity decays with a ~3s leaky integrator)                         |
| `PreToolUse` (Read/Grep/Glob/WebFetch/WebSearch) | soft "read" click timbre                                                           |
| `PreToolUse` (Write/Edit/NotebookEdit)           | brighter "write" click timbre                                                      |
| `PreToolUse` (Bash, non-read subcommand)         | "exec" click timbre                                                                |
| `PostToolUse`                                    | small activity bump (raises click rate)                                            |
| `PostToolUseFailure`                             | sharp falling minor-2nd dyad — the most salient sound in the system                |
| `Stop`                                           | rising perfect-fifth "done" chime, activity decays afterward                       |
| `Notification` / `PermissionRequest`             | soft two-strike FM-bell "attention" chime                                          |
| `SubagentStart` / `SubagentStop`                 | spawn / despawn chime pair                                                         |
| `PreCompact`                                     | distinct "compaction" chime                                                        |
| `ContextPressure` (synthetic, `fill` 0.0-1.0)    | low drone fades in and darkens as fill rises (off by default)                      |
| `UserPromptSubmit`                               | small activity bump                                                                |
| `SessionEnd`                                     | activity drops to 0 and the drone is released — winds down to true digital silence |
| Click rate overall                               | Poisson process, rate follows the activity integrator, capped at 25 clicks/sec     |

## Config (env vars)

| Var                      | Default                  | Meaning                                                                                                                                                                                                                        |
| ------------------------ | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SONIFIER_THEME`         | `ambient`                | sound layer: `ambient` (v2, default) or `geiger` (legacy v1)                                                                                                                                                                   |
| `SONIFIER_PORT`          | `9753`                   | UDP/HTTP listen port                                                                                                                                                                                                           |
| `SONIFIER_HOST`          | `127.0.0.1`              | host the _hook scripts_ send to (the daemon itself binds `0.0.0.0`)                                                                                                                                                            |
| `SONIFIER_VOLUME`        | `0.5`                    | master volume, 0.0-1.0. The `ambient` theme is calibrated so `1.0` is a normal master level (about -23 dBFS RMS / -6 dBFS peak in the active state); the `0.5` default is a background-listening level 6 dB under that         |
| `SONIFIER_MUTE`          | off                      | mute all audio output (daemon still runs, still answers `/health`)                                                                                                                                                             |
| `SONIFIER_CLICKS`        | on                       | `geiger`: enable/disable the click train. `ambient`: enable/disable the L2 rain layer (per-event drops + activity-driven Poisson bed)                                                                                          |
| `SONIFIER_CHIMES`        | on                       | `geiger`: enable/disable one-shot chimes. `ambient`: enable/disable discrete gestures (knock, cadence, notification chime, ack note, settling gesture)                                                                         |
| `SONIFIER_DRONE`         | off                      | `geiger`: enable/disable the context-pressure drone. `ambient`: **unused** — the L5 context-pressure "weather" layer is event-driven (`ContextPressure`) and on by default in ambient, per docs/research/BRIEF-v2.md section 8 |
| `SONIFIER_IDLE_EXIT_MIN` | `30`                     | minutes of inactivity before the daemon exits on its own                                                                                                                                                                       |
| `SONIFIER_QUIET`         | off                      | suppress the daemon's per-event stderr logging                                                                                                                                                                                 |
| `SONIFIER_LOG_DIR`       | `$TMPDIR` or `/tmp`      | where `hooks/autostart-daemon.sh` writes `sonifier.log`                                                                                                                                                                        |
| `SONIFIER_PY`            | `<repo>/src/sonifier.py` | override the daemon path `hooks/autostart-daemon.sh` launches                                                                                                                                                                  |

Boolean vars are off for `0`/`false`/`off`/`no` (case-insensitive) and on for any other non-empty value.

## CLI

| Command                                                          | What it does                                                                                |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `python3 src/sonifier.py`                                        | live mode: bind `SONIFIER_PORT`, play audio (needs `sounddevice`)                           |
| `python3 src/sonifier.py --render events.jsonl out.wav`          | offline render to a 48kHz stereo 16-bit WAV; no audio device needed                         |
| `python3 src/sonifier.py --render events.jsonl out.wav --seed 7` | same, with a fixed RNG seed for byte-reproducible output                                    |
| `python3 src/sonifier.py --check`                                | print config/capability JSON (port, env config, whether an audio device is usable) and exit |

`events.jsonl` is one JSON object per line, `{"t": <seconds>, "event": {<hook JSON>}}`. Lines that don't parse, or that lack `t`/`event`, are skipped. The rendered duration is the last event's `t` plus 3 seconds of tail.

## HTTP / UDP interface

The daemon listens on `SONIFIER_PORT` (default `9753`) for both:

- `POST /event` — body is the raw Claude Code hook JSON. Always answers `200`; bodies over 1 MiB are refused with `413`. Malformed JSON is dropped silently rather than erroring the hook.
- a UDP datagram on the same port with the same JSON body (what `hooks/send-event.sh` uses — fire-and-forget, never blocks the hook).
- `GET /health` — `{"ok": true, "activity": <float>}`.

## Uninstall

1. Remove the `hooks` entries that point at this project's `hooks/send-event.sh` / `hooks/autostart-daemon.sh` from `~/.claude/settings.json` (or restore one of the `settings.json.bak.<timestamp>` backups `install.sh` made).
2. Kill the running daemon if it's still up: `pkill -f sonifier.py`, or let it exit on its own after `SONIFIER_IDLE_EXIT_MIN` minutes of silence.
3. Delete this directory.

## Troubleshooting

- **Ambient theme sounds thin/unfiltered or missing rain-bed/whoosh/weather layers**: `scipy` isn't installed. `pip install scipy --break-system-packages`. AmbientTheme degrades gracefully without it (never crashes — several filters just become no-ops) rather than failing; `SONIFIER_THEME=geiger` doesn't need scipy at all.
- **No sound / `sounddevice` missing**: live playback needs `pip install sounddevice --break-system-packages` and a working PortAudio-capable output device. Offline rendering (`--render`) doesn't need `sounddevice` at all — use it to sanity-check the DSP/mapping without any audio hardware.
- **Port busy**: another process (or a previous daemon instance) is already on `9753`. Either kill it, or run both the daemon and the hooks with a different port: `SONIFIER_PORT=9800 python3 src/sonifier.py` and export `SONIFIER_PORT=9800` in your shell/`settings.json` `env` before Claude Code launches hooks, so `hooks/send-event.sh` picks it up too.
- **Too loud / want it silent**: `SONIFIER_MUTE=1` mutes output while keeping the daemon (and `/health`) alive; `SONIFIER_VOLUME=0.1` just turns it down.
- **Hooks seem to fire but nothing happens**: check `${TMPDIR:-/tmp}/sonifier.log` (or wherever `SONIFIER_LOG_DIR` points) for the daemon's stderr; run `python3 src/sonifier.py --check` to print its config/capability JSON.

## Try it without Claude Code

`src/simulate_session.py` fires a realistic ~60s simulated session at a running daemon, real-time paced:

```
python3 src/sonifier.py &                 # start the daemon
python3 src/simulate_session.py           # fire events at it over UDP
python3 src/simulate_session.py --speed 4 # 4x realtime
python3 src/simulate_session.py --http    # use HTTP POST /event instead of UDP
```

Or skip the daemon entirely and render straight to a `.wav` file offline. Three renders of the current (v2.2 "Warm Room") engine are checked in under `demos/` as MP3s, all rendered with `SONIFIER_VOLUME=1.0 --seed 7`:

| artifact                       | script                          | duration | what it is                                                              |
| ------------------------------ | ------------------------------- | -------- | ----------------------------------------------------------------------- |
| `demos/realistic-pace-v22.mp3` | `demos/realistic-session.jsonl` | 176.54 s | a real-cadence session (23 events / 176 s) — the blind-listening render |
| `demos/focus-loop-v22.mp3`     | `demos/focus-session-v2.jsonl`  | 60.41 s  | 60 s of steady medium activity: ordinary work, no failures              |
| `demos/demo-v22.mp3`           | `demos/demo-session-v2.jsonl`   | 180.02 s | the full storyboard — every layer and gesture                           |

The v2-era renders (`demo-v2.*`, `focus-loop-v2.*`, `realistic-pace-v2.mp3`, and the v1 `demo.*`) have been removed: they reflected the sound v2.2 exists to fix (too-fast pacing, 157 Hz centroid, sine-chirp drops, lonely chimes) and keeping them around invited listening to the wrong thing. Regenerate any of them from the scripts below, or use `eval/make_clips.py`, which covers the same ground with fixed seeds.

- `demos/demo-session-v2.jsonl` — a 180s ambient showcase (default `ambient` theme) exercising every layer and gesture in `docs/research/BRIEF-v2.md` section 8: idle self-playing bloom, a gentle read phase, an active build phase (rain thickens, notes bloom), a failure (knock + room pause, room darkens) and recovery, a two-subagent phase with a rising context-pressure ramp, a `PreCompact` settle + pressure release, and a wind-down Stop cadence fading to true silence:
  ```
  SONIFIER_VOLUME=1.0 python3 src/sonifier.py --render demos/demo-session-v2.jsonl /tmp/demo-v22.wav --seed 7
  ```
- `demos/focus-session-v2.jsonl` — a 60s "steady focus" loop: constant medium activity, no failures, i.e. what the theme sounds like during ordinary work:
  ```
  SONIFIER_VOLUME=1.0 python3 src/sonifier.py --render demos/focus-session-v2.jsonl /tmp/focus-loop-v22.wav --seed 7
  ```
- `demos/realistic-session.jsonl` — a 176s session at a real agent's cadence (~0.13 tool events/s). This is the render used for blind listening, because the storyboard demo deliberately packs more into 3 minutes than a real session ever contains:
  ```
  SONIFIER_VOLUME=1.0 python3 src/sonifier.py --render demos/realistic-session.jsonl /tmp/realistic-pace-v22.wav --seed 7
  ```
- `demos/demo-session.jsonl` — the original ~75s v1 showcase (render it with `SONIFIER_THEME=geiger` to hear the legacy click-train sound it was built for; it also renders fine under `ambient`, just mapped through the new sound layer):
  ```
  SONIFIER_THEME=geiger python3 src/sonifier.py --render demos/demo-session.jsonl /tmp/demo.wav
  ```

You can also generate your own render script instead of using a bundled one:

```
python3 src/simulate_session.py --emit-jsonl my-session.jsonl
python3 src/simulate_session.py --demo --emit-jsonl my-demo.jsonl
python3 src/simulate_session.py --v2-demo --emit-jsonl my-v2-demo.jsonl
python3 src/sonifier.py --render my-session.jsonl /tmp/out.wav
```

## Tests and verification tooling

```
python3 -m pytest tests/ -q          # 97 tests (geiger DSP + ambient theme, incl. 14 v2.2 regression tests)
```

`tools/analyze_render.py` is the reusable acceptance battery: it runs every numeric criterion in `docs/research/BRIEF-v2.md`/`docs/research/BRIEF-v2.2.md` section 7 against a rendered WAV and prints a PASS/FAIL table, plus (with `--arc`) the per-10s RMS / centroid / rain-onset-density arc used to check that a render follows its storyboard. As of v2.2 it also runs the amended criteria (items marked `(v2.2)`: slope over 125Hz-8kHz, centroid floor+ceiling, tightened stereo correlation, 5s L-R balance) plus four new checks that need `--events`: N1 (drop-rate cap), N2 (embedding rule, measured peak-vs-peak), N3 (RT60 estimate — always N/A on real program material, see the code comment for why; the room's RT60 is measured off an impulse response by `tools/complaint_checks.py` and by the test suite), N4 (activity eventfulness ordering: busy > calm > idle), N5 (failure "room pause" depth), and `8b` (loudness stability measured inside automatically-detected constant-activity stretches rather than a caller-chosen window).

`tools/complaint_checks.py` is the listener-complaint regression suite: it encodes the v2 blind listener's verbatim feedback ("dark cave", "birds or drops", "far-away bing", "left/right difference", "too fast", "not regular") as measurements, so a future re-tune cannot reintroduce one of them while still passing the section-7 battery. Its bird/chirp thresholds are calibrated against a positive control — the same render regenerated with v2's sine-chirp grains.

```
python3 tools/complaint_checks.py realistic-pace-v22.wav \
        --events demos/realistic-session.jsonl --steady 60:120
```

```
python3 tools/analyze_render.py demo-v22.wav --steady 50:80 \
        --events demos/demo-session-v2.jsonl --arc
```

- `--steady A:B` picks the steady-state window the strict psychoacoustic items are measured on (section 7 is defined for constant-machine-state active material; pick a window that's actually flat in the `--arc` output — sessions with a lot of scripted activity swing, like `demos/demo-session-v2.jsonl`, don't have one single window that's steady for their whole duration, only local plateaus). Windows containing scripted gestures are reported separately, see `docs/research/VERIFICATION.md`.
- `--events FILE` enables the information checks (item 9) and the v2.2 N1-N4 checks, which need to know when the scripted failure/SessionEnd/activity changes happened.

`tools/lab.py` is a faster in-process harness for the same measurements when tuning (`python3 tools/lab.py steady 60`).

## Evaluation kit (v2.2, `eval/`)

A protocol + tooling for turning household/blind listeners into scored, unbiased data points on the ambient theme, per `docs/research/BRIEF-v2.2.md` section 8:

```
python3 eval/make_clips.py            # generates eval/clips/ (WAV+MP3, fixed seeds)
```

- `eval/README.md` — the listening protocol: within-subject design, order rotation, and the verbatim anti-priming script to read to listeners.
- `eval/make_clips.py` — generates all clips from the engine at fixed seeds: **Block A** (8 × 10-15s vocabulary clips — sparse/dense rain, write-notes, knock, subagent choir, pressure weather, done cadence, needs-you chime), **Block B** (4 × 60-90s scenario clips with ground-truth timestamp files — calm+success, busy+success, failure+recovery, busy+subagents+unresolved), **Block C** (the same short session rendered 3x with only the drop rate-mapping law changed — old v2 exponential map, shipped v2.2 compressive map, v2.2 at half density — the pacing "flip-point probe").
- `eval/scoring-sheet.html` — a self-contained, printable response sheet (one page per clip): 4 comprehension probes, 8 semantic-differential pairs, the ISO 12913 8-item circumplex, an ICBEN 0-10 annoyance item, and 2 control items. Open it directly in a browser (`Print / Save as PDF` button included) — no build step, no external resources.
- `eval/score.py` — scores transcribed listener responses: information- transfer score/clip, affect score, ISOPleasant/ISOEventful (exact ISO 12913 circumplex-projection formulas), cross-listener medians, per-listener trajectories, and a sign-test helper for the Block C pairwise comparisons. Run with no arguments to see it score a built-in fabricated example (also doubles as a self-test).
- `eval/answer-key.md` — ground truth for every clip (kept separate from the scoring sheet; only consult it after responses are collected).
