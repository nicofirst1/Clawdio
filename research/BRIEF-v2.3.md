# BRIEF-v2.3 — "Third Timbre" (2026-08-04)

v2.3 amends the v2.2 "Warm Room" mix (`research/BRIEF-v2.2.md`) in response to blind-listening evidence that v2.2's own fix for round-1's complaint introduced a new one, plus a literature review of measurable annoyance metrics. It changes audio behavior deliberately — unlike the `AmbientTheme` layer-ownership refactor that preceded it, this is not a bit-identity- preserving change. Every criterion in this document is: `tools/analyze_render.py` and `tools/complaint_checks.py` both still all-PASS on the shipped v2.3 render, and the new metrics show a measured, not asserted, improvement over v2.2.

## 1. What changed, and why

### 1.1 Half-density pacing defaults

`DROP_MIN_GAP_S` 0.150 → 0.300s, `BURST_COALESCE_WINDOW_S` 0.250 → 0.500s, and the compressive rate map scaled 0.5× via a new `DROP_RATE_SCALE` constant. These are exactly `eval/make_clips.py`'s Block C `c3_v22_half_density` variant, now adopted as the shipped default rather than an A/B probe.

**Why**: `research/blind-round2-2026-08-04.md` ran the same session under three pacing laws (v2 original, v2.2 shipped, v2.2 half-density) single-blind, same-session. Half-density (`Y`) ranked best (busy 6/7 vs 7/7 for the other two). But the effect was small — busy only moved 7→6 — and the round's free comment ("the white noise and chaos") pointed at timbre, not density, as the dominant complaint. Density confirmed the right direction but was not sufficient on its own.

### 1.2 Drop timbre: woodblock modal click replaces the noise tick

New `AmbientConfig.drop_timbre` field: `"woodblock"` (new default), `"marimba"`, `"plink"` (A/B candidates), `"noise"` (exact v2.2 legacy synthesis, kept for comparison — verified bit-identical to the pre-v2.3 grain bank, see §2).

**Why**: v2.2's noise-tick drop (a filtered-noise burst, 1.8-3.5kHz bandpass) was itself the fix for v2's downward-sine-chirp complaint ("sounds like a bird call"). Round 2's blind free comment ("white noise and chaos") shows that fix traded one complaint for another. `research/lit-review-annoyance-2026-08-04.md` reviews calm-technology and earcon HCI literature and finds a consistent throughline: damped, pitched/percussive transients (a struck, resonant, decaying timbre) read as informative-but-tolerable, while undamped broadband noise bursts read as alarm/static — independent of event rate. Rec #3: reuse the project's own existing damped-modal-synthesis primitive (the `PostToolUseFailure` "knock", already in the engine) at a higher, brighter modal frequency for routine drops, instead of building a third timbre family from scratch.

Round 3 (`research/blind-round3-2026-08-04.md`) rendered isolated 25s dry-tap loops of three modal candidates (woodblock, muted marimba, water-drop plink) plus the noise-tick control, single-blind. The control was confirmed worst via free comment ("number s is the worst"), reproducing round 2's diagnosis in isolation. But all four clips bottomed out at 1/7 on both Likert scales — a floor effect (25s of isolated dry taps is unpleasant regardless of timbre) that carried no discriminating signal between the three candidates. Round 3's own conclusion: redo the selection **in context** (full mix, not isolated taps) and as **forced ranking only** (see `research/blind-round4-TEMPLATE.md` / `eval/blind/make_round4_clips.py`, §4 below). Woodblock ships as the default in the interim because it's the closest sibling to the engine's existing `_render_knock` primitive (same modal-synthesis family, transposed up and shortened), not because round 3 picked a winner — it explicitly didn't.

### 1.3 Air-bed shrink

New `AIR_V23_LEVEL_CUT_DB` (-4dB on top of the existing level envelope) and `AIR_V23_HARD_CEILING_HZ` (2.8kHz hard ceiling on the activity-adaptive upper tilt corner, applied via `min()` so the idle/low-activity register and the v2.2 section-7 centroid-floor tuning are untouched — only the busiest, brightest end of the activity range is clipped).

**Why**: round 2's decision explicitly named the continuous air bed as a second lever alongside timbre ("cut or hard-lowpass the air layer"). Lit-review rec #4 corroborates from commercial practice: Brain.fm's own published comparisons treat continuous pink/broadband noise as an inferior baseline condition (not a design target) versus amplitude-modulated musical material; Eno/Bloom and Endel are both stem/note-based architectures, not noise-bed architectures. Measured effect on `realistic-session.jsonl`'s steady window: mix-level spectral flatness and >3kHz brightness both fall ~20-23% versus the v2.2 baseline (exact numbers in §3).

### 1.4 New measurable criteria

`tools/analyze_render.py` gains two criteria per lit-review recs #1-2: **spectral flatness** (Wiener entropy — geometric/arithmetic mean of the power spectrum, direct proxy for "reads as noise vs. reads as tone") and **brightness ratio** (fraction of spectral power above 3kHz — a coarse, cheap proxy for Zwicker sharpness without a full Bark-scale transform, targeting the specific register the v2.2 noise-tick drops and air bed occupied). Full Zwicker psychoacoustic annoyance (loudness/sharpness/ roughness/fluctuation-strength composite) needs an ISO 532-1 filter bank — heavier machinery than the project wants; the lit review explicitly recommends skipping roughness/fluctuation-strength/tone-to-noise-ratio for now since the complaint is steady-state hiss, not modulation or tonal beating (rec #5).

## 2. Regression guard: `drop_timbre="noise"` reproduces v2.2 exactly

The timbre change is additive, not destructive — `_render_one_drop_variant` (the v2.2 synthesis) is unchanged and still reachable via `drop_timbre="noise"`. Verified two ways:

- Grain-bank level: `_build_drop_bank(rng, sr, timbre="noise")` produces the exact same array sequence as calling `_render_one_drop_variant` directly at the same RNG state (`tests/test_ambient.py::test_v23_drop_timbre_noise_matches_legacy_v22_output`).
- Full-render level: `sonifier.run_render()` on `realistic-session.jsonl` with `drop_timbre="noise"` **and** the density knobs reset to their v2.2 values (`DROP_MIN_GAP_S=0.150`, `BURST_COALESCE_WINDOW_S=0.250`, `DROP_RATE_SCALE=1.0`) produces an MD5-identical WAV to the pre-v2.3 baseline render (`c004affc76d3a85ed9367353967a3471`, same seed=7).

## 3. Measured before/after (realistic-session.jsonl, steady window 60-120s)

| metric             | v2.2 (shipped) | v2.3 (shipped)    | threshold (analyze_render.py) |
| ------------------ | -------------- | ----------------- | ----------------------------- |
| spectral flatness  | 0.001095       | 0.000840 (-23.3%) | ≤ 0.000876 (20% below v2.2)   |
| brightness >3kHz   | 0.512%         | 0.39% (-22.9%)    | ≤ 0.41% (20% below v2.2)      |
| section-7 centroid | 401 Hz         | 366 Hz            | 350-1200 Hz floor+ceiling     |

Both new criteria: v2.2 render **fails**, v2.3 render **passes**, by construction of the threshold (measured on the actual v2.2 control, not guessed) — same calibration pattern `tools/complaint_checks.py` already uses throughout (measure the positive/negative control first, set the threshold relative to it).

`tools/complaint_checks.py` is still all-PASS on the v2.3 default render. Its `birds/grain is noise` and `birds/in-mix noise-not-tone` checks — which assert HIGH spectral flatness, the correct invariant for v2.2's noise-tick solution — now report N/A (not silently skipped) for any tonal `drop_timbre`, since a damped modal click is supposed to fail that specific check by design; they still run and PASS when `drop_timbre="noise"` is configured, so a future regression back to the noise timbre is still caught. The `birds/no-downward-chirp` check (the actual "reads as a bird call" invariant, independent of which timbre solves it) runs and passes for every timbre unconditionally.

## 4. Round 4: unresolved question carried forward

Round 3 explicitly did not pick a winner among woodblock/marimba/plink — see §1.2. `eval/blind/make_round4_clips.py` renders all three v2.3 candidates plus the v2.2 control as full-mix variants of the same ~50s calm→busy→hot-burst→calm session, shuffled to neutral labels, for a forced-ranking (no Likert scales) blind pass — `eval/blind/round4.html`, results template `research/blind-round4-TEMPLATE.md`. This has not been run with a listener yet; the default (`woodblock`) may change once it is.

## 5. Not addressed by v2.3

`research/eval-v22-baseline-2026-08-04.md` (a separate N=1 scored-sheet listener session, distinct from the round 2-4 protocol) surfaces what its own author calls "the single clearest actionable finding in this dataset": the `Stop` resolving cadence was not read as conclusive across three separate clips (b01, b02, a07 — all marked "still going/unresolved" when ground truth was a clean finish). That is a different axis from density/timbre/air-bed and is **not** touched by any v2.3 change — flagged here so it isn't lost, and recommended as the next research pass ahead of further pacing/timbre tuning.

## 6. Sources

- `research/blind-round2-2026-08-04.md` — half-density blind ranking result, decision to pursue a third timbre and shrink the air bed.
- `research/blind-round3-2026-08-04.md` — isolated-clip timbre candidate test, control confirmed worst, floor-effect methodology lesson, decision to redo in-context with forced ranking.
- `research/lit-review-annoyance-2026-08-04.md` — measurable annoyance metrics (flatness/sharpness), calm-tech/earcon literature on damped vs. noise timbres, commercial practice (Endel/Brain.fm/Eno) survey, and the five numbered recommendations this document implements.
- `research/eval-v22-baseline-2026-08-04.md` — scored-sheet baseline (a separate, longer-form evaluation-kit protocol), source of the Stop-cadence finding in §5.
