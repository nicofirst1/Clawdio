# BRIEF-v2.4 — "State Legibility" (2026-08-04)

v2.4 targets one specific, now twice-confirmed defect: the DONE state (the agent has stopped and is waiting for you) is not audibly distinct from the WORKING state. It touches only the Stop cadence and the post-Stop idle behavior — density and drop timbre (still pending round 4's listener ranking, `research/blind-round4-TEMPLATE.md`) are untouched.

## 1. The evidence (two independent listeners)

**Listener 1 — scored baseline** (`docs/research/eval-v22-baseline-2026-08-04.md`, a separate N=1 scored-sheet protocol from the round 2-4 blind-ranking series). Three separate clips exercising the same "clean Stop ending" condition were all misread as unresolved:

- `b01_calm_success` and `b02_busy_success` (both end with a Stop cadence, no failures) were marked "still going / can't tell / not finished" rather than "succeeded".
- `a07_done_cadence` (the dedicated Block A vocabulary clip for the wrap-up gesture in isolation) was _also_ marked outcome=unresolved.
- `b03_failure_recovery` (fails once, recovers, then a clean Stop) was marked "failed" — the listener caught the failure dip but not the recovery/resolution signal afterward.

The baseline report's own conclusion, verbatim: _"the resolving/Stop cadence is not read as conclusive across three separate clips. This is the single clearest actionable finding in this dataset — worth prioritizing over pacing/timbre work for v2.3 if it holds up with more listeners."_ It held up enough to get bumped to v2.3 planning at the time (pacing/timbre had its own open questions from round 2) but was never acted on.

**Listener 2 — live listening.** A second, independent pass listening to the daemon live (not a scored clip) confirms the same defect from the other direction: Stop "just continues" — idle-after-Stop is not perceptibly different from idle-during-work, so there is no audible signal that anything happened at all when a session ends cleanly.

Two independent methodologies (scored comprehension probes on isolated clips vs. live ambient listening) landing on the same specific defect is a much stronger signal than either alone — this is why it takes priority over finishing the round-4 timbre question.

## 2. Design: three audibly distinct states

1. **WORKING** — unchanged v2.3 behavior (taps, activity-driven bed/rain/bloom).
2. **DONE / waiting for user** (post-`Stop`) — two changes, both gated by the new `AmbientConfig.done_cadence` switch (`"v22"` legacy | `"v24"` new, default `"v24"`):
   - **Authentic cadence gesture.** v2.2's cadence (`_build_cadence_notes`) was a 2-4 note descending pentatonic melody landing on **C4 or G4** — i.e. a coin-flip between a full cadence (tonic) and a half-cadence (dominant). A half-cadence is _supposed_ to sound unresolved in tonal music — it is the "and..." gesture, not "the end." That coin-flip is a direct, mechanical explanation for "doesn't read as conclusive." v2.4's `_build_cadence_notes_v24` always lands the melody on **C4** (the tonic), and `AmbientTheme.handle_event`'s `Stop` branch pairs it with a **simultaneous bass-register root note** (`ROOT_C2`, the pad's own fundamental) timed to land at the same instant as the melody's final note. A root-position tonic in the bass under a melodic resolution is what turns "a melody that stopped" into an _authentic cadence_ (V→I) — the harmonic gesture tonal listeners actually parse as "over," not just a melodic one.
   - **Settled idle.** v2.2's post-Stop handling (`BedLayer.handle_stop`/`_bed_target_db`) held the bed at -33dB for only 6 seconds, after which it fell back into the _ordinary_ idle ladder — whose first rung (`idle_dur < 90s` → -30dB) is actually _louder_ than the 6s hold, i.e. within 6 seconds of Stop the bed was already trending back toward its working-state level. v2.4 replaces this with a `SETTLED_HOLD_S` (20s) window at `SETTLED_BED_DB` (-38dB, glide `SETTLED_BED_TAU_S=8s`) — deeper than either idle-ladder rung the old hold could fall into, sustained long enough to actually register as "the room went quiet" rather than a blip. The self-playing bloom rate is independently scaled by `SETTLED_BLOOM_RATE_SCALE` (0.35×) during the same window (`BloomLayer.render(..., settled=True)`) — present, audibly alive, but noticeably sparser than ordinary idle-during-work.
3. **SESSION OVER** (`SessionEnd`) — unchanged: full fade to true digital silence.

## 3. Regression guard: `done_cadence="v22"` reproduces v2.3 exactly

`done_cadence="v22"` is not a stub — it is the literal legacy code path (`_build_cadence_notes`, the `-33.0, 5.0` bed-hold branch, `handle_stop`'s `6.0`s window, no bloom-rate throttling), selected by a runtime branch rather than deleted. Verified two ways:

- `sonifier.run_render()` on `demos/realistic-session.jsonl` with `AMBIENT_CONFIG.done_cadence = "v22"` produces an **MD5-identical** WAV to the pre-v2.4 baseline render (`23da42cf04da3aa79e21ea35c57a2276`, seed=7) — same trick `drop_timbre="noise"` used for the v2.3 timbre regression guard.
- `tests/test_ambient.py::test_v24_done_cadence_v22_matches_legacy_stop_handling` pins the fast in-process check (same `-33.0/6.0` hold constants reachable).

## 4. The objective gate

Per-listener acoustic evidence is "vibes"; the gate is a number calibrated against a real render, same pattern `tools/complaint_checks.py` and v2.3's flatness/brightness criteria already use (measure the failing control first, set the threshold with real margin on both sides).

Test session: 20 tool events over ~37s of steady work, then `Stop`, then 20s+ with no further events (`tools/analyze_render.py`'s new **N6 done-state legibility** criterion, added to `run_battery`/`info_checks`; only runs when a Stop is followed by >=20s of silence, N/A otherwise).

**RMS step (work window vs. the last 12s of the post-Stop 20s window, which skips the cadence gesture and the bed's glide into its settled target):**

|                             | work RMS  | settled RMS | step         |
| --------------------------- | --------- | ----------- | ------------ |
| v2.2 (`done_cadence="v22"`) | -28.33 dB | -30.81 dB   | **-2.49 dB** |
| v2.4 (default)              | -28.33 dB | -36.74 dB   | **-8.41 dB** |

Threshold: **settled step ≤ -5.0 dB**. v2.2 fails (-2.49 > -5.0); v2.4 passes (-8.41 ≤ -5.0) — both by construction of a threshold sitting cleanly between the two measured numbers, not tuned to make v2.4 pass.

**Bloom-note rate**: NOT checked acoustically. At bloom's mean inter-note interval (~45s idle, ~129s settled), a 20s window is too short for an onset-count to be signal rather than noise — measured directly: v2.2 and v2.4's settled-window onset counts in the bloom register (130-1050Hz) were statistically indistinguishable at every prominence threshold tried (2 to 125 "onsets" depending on threshold, moving together for both variants). This is exactly the floor-effect problem round 3's methodology notes already flagged for short-window sparse-event measurement. Instead, `tests/test_ambient.py::test_v24_settled_bloom_rate_is_reduced` asserts directly against the engine's own dispatch count over a 10-simulated-minute window (same technique `test_activity_high_vs_low_render_differ` already uses for drop dispatches): settled-mode self-play fires measurably less often than ordinary idle self-play.

`tools/complaint_checks.py` and the full `tools/analyze_render.py` battery both still all-PASS on v2.4 renders (checked against `demos/realistic-session.jsonl` and `demos/demo-session-v2.jsonl`; the latter has two pre-existing, v2.4-unrelated FAILs on the v2.3 flatness/ brightness criteria — calibrated against `realistic-session.jsonl` specifically, not this busier storyboard session — confirmed present and identical before this change too).

## 5. Round 5: does the fix actually work?

`eval/blind/round5/` re-tests the exact comprehension question the baseline eval failed on ("how did it end?"), COMPREHENSION not preference. Four ~30-40s clips, same work-phase template, differing only in the ending: a clean v2.4 Stop, a failure-then-recovery v2.4 Stop (retests the baseline's b03 finding), a cut-off-mid-work "still going" control, and a hidden `done_cadence="v22"` control (the exact original defect, for direct comparison). `eval/blind/round5/round5.html` asks per clip: Succeeded / Failed / Still going, plus a confidence rating and free comment — directly comparable against the baseline eval's own outcome-comprehension question. Not yet run with a listener.

## 6. Sources

- `docs/research/eval-v22-baseline-2026-08-04.md` — scored-sheet baseline, source of the b01/b02/a07/b03 findings in §1.
- Live-listening pass (undocumented prior to this brief) — the "Stop just continues" observation in §1.
- `docs/research/BRIEF-v2.3.md` — the layer/config structure v2.4 builds on (`AmbientConfig`, `BedLayer`/`BloomLayer` as owned-state layer classes).
