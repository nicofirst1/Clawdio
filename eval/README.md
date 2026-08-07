# claude-geiger evaluation kit

Turns household/blind listeners into scored, unbiased data points on the v2.2 "Warm Room" ambient theme. Within-subject design: every listener hears every clip; only presentation ORDER varies between listeners.

## Contents

- `make_clips.py` — generates all audio clips from the engine at fixed seeds. Run once to populate `clips/`:
  ```
  python3 eval/make_clips.py
  ```
  Produces `.wav` (always) and `.mp3` (if `ffmpeg` is on PATH) for each clip, plus `.jsonl` event scripts and, for Block B, `.groundtruth.txt` / `.groundtruth.json` sidecar files (kept OUT of what listeners see — see `answer-key.md`).
- `scoring-sheet.html` — printable/self-contained 1-page-per-clip response form. Open directly in a browser or print to PDF, one copy per listener per session.
- `score.py` — enter each listener's filled-in responses (as a small Python dict or JSON — see the file's own `__main__` example) and compute information-transfer score, affect score, ISOPleasant/ISOEventful, cross-listener medians, per-listener trajectories, and an A/B sign test for Block C.
- `answer-key.md` — ground truth for every clip (event timelines, expected outcomes). NEVER shown to listeners; used only by the scorer after responses are collected.
- `clips/` — generated output (audio + scripts + ground truth), created by `make_clips.py`. The `.wav`/`.mp3` are git-ignored (regenerable, too big for git); run `make_clips.py` after cloning to populate them.

## Blocks

- **Block A — vocabulary** (8 clips, ~10-15 s each): isolated sound identities in near-neutral context. Tests whether a listener can name _what kind of thing_ they're hearing without a story around it. `a01_sparse_rain`, `a02_dense_rain`, `a03_write_notes`, `a04_knock`, `a05_subagent_choir`, `a06_pressure_weather`, `a07_done_cadence`, `a08_needs_you_chime`.
- **Block B — scenarios** (4 clips, ~60-90 s each): full sessions with a narrative arc. Tests information transfer (can the listener reconstruct what happened) and affect (how did it feel). `b01_calm_success`, `b02_busy_success`, `b03_failure_recovery`, `b04_busy_subagents_unresolved` (deliberately cuts off mid-session — tests whether the listener correctly reports "still going" rather than inventing an ending).
- **Block C — pacing flip-point probe** (3 clips, same underlying session, ~34 s each). Measured dispatch density (engine ground truth, not the acoustic proxy):

  | clip                  | mapping                                                                          | drops/s | worst 2 s window |
  | --------------------- | -------------------------------------------------------------------------------- | ------- | ---------------- |
  | `c1_v2_mapping`       | v2's expansive law `2 + 38·a^1.3`, 20 ms pacing floor, no burst coalescing       | 14.09   | 26.5/s           |
  | `c2_v22_mapping`      | **shipped v2.2**: compressive map capped at 6/s, 150 ms floor, 250 ms coalescing | 0.73    | 3.0/s            |
  | `c3_v22_half_density` | v2.2 at half density: half rate, 300 ms floor, 500 ms coalescing                 | 0.37    | 1.5/s            |

  `c1` is what the v2 blind listener heard and called "too fast, losing control"; `c2` is what ships; `c3` brackets it on the sparse side. Play in randomized order per listener; ask which felt controllable vs frantic during the busy burst. This is an A/B(/C) forced-choice, not a comprehension test.

  (v2.2 verification note: before this pass `c3` dispatched _exactly the same 25 drops_ as `c2` — "half density" only halved the Poisson rain clock, and at this script's activity almost every tap is event-triggered — and `c1` was throttled to 4.3 drops/s by v2.2's own pacing floor, so the probe had two distinct points instead of three and its "too fast" control was already most of the way to the shipped setting.)

## Protocol

1. **Environment**: quiet room, earphones (not room speakers — the v2 listener report that motivated v2.2 was earphone-based and stereo artifacts only show up that way).
2. **Order rotation**: rotate clip order across listeners to cancel fatigue/order effects. Within Block A, randomize the 8 clips. Within Block B, randomize the 4 clips. Block C's 3 variants are always randomized per listener (this is the whole point of the block — nobody should know which variant is "new"). Suggested overall order: Block A, short break, Block B, short break, Block C. Do not reuse the same randomization for every listener.
3. **Anti-priming script** — read this verbatim to every listener before Block A, and do not elaborate on it:

   > "You'll hear short recordings of sounds a computer program makes while it works. There are no right or wrong answers — we're testing the sounds, not you."

   Do not mention "AI coding assistant," "Claude," rain, drops, notes, knocks, or any of the vocabulary used internally — that would prime the very associations the test is trying to measure.

4. **Playback**: play each clip once. Listeners may ask for a single repeat if they missed the start (note this on the sheet — a needed repeat is itself a data point). Do not pause/scrub within a clip.
5. **Response**: after each clip, listener fills in one page of `scoring-sheet.html` (or the experimenter transcribes verbally-given answers onto it) before the next clip plays. Block A clips still get a sheet, but with only the vocabulary-relevant probes filled in (the 4 comprehension probes and the semantic differential/circumplex block apply to Block A too — they describe the sound in isolation; there's just no "outcome" to name).
6. **Block C**: after all 3 variants play (in the listener's randomized order), ask the two flip-point questions (see `scoring-sheet.html` Block C page) once, comparing across the three, not per-clip.
7. **No debrief before all listeners are done** — don't tell listener N what listener N-1 said; this is a between-listener design for independence, even though it's within-subject for clips.

## After collecting responses

1. Transcribe each listener's sheet into the format `score.py` expects (see its `__main__` block for a worked example / template).
2. Run `python3 eval/score.py <responses.json>` (or edit the inline example) to get per-clip information-transfer and affect scores, ISOPleasant/ISOEventful, medians across listeners, and the Block C sign test.
3. Compare against `answer-key.md` only at this stage, never earlier.
