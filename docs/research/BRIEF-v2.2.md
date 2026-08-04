# claude-geiger v2.2 — "Warm Room" revision brief

Coordinator synthesis. Inputs: (a) first blind-listener report on v2 renders, (b) pacing/urgency psychophysics research, (c) sonification evaluation-methodology research. This brief AMENDS BRIEF-v2.md; where they conflict, v2.2 wins. Tracing + install layers unchanged. GeigerTheme legacy unchanged.

## 0. Listener evidence (blind, n=1, earphones)

Demo render: "too fast, losing control." Realistic render: "confusing; a little anxiety; like a dark cave; can't tell if birds or drops; a far-away bing puts me under pressure; white noise + birds confusing; left/right difference; not regular; maybe under the sea; feels isolated, confused, lost."

Every item maps to a cause. v2.2 exists to fix these six causes:

| Listener phrase | Root cause in v2 engine | Fix section |
|---|---|---|
| too fast / losing control | activity→rate exponent 1.3 (urgency scales at ~1.35 — we built max-urgency mapping); discrete drops up to 40/s | §1 |
| dark cave / sea / isolated | spectral centroid ~157 Hz (far too dark), long very-wet Freeverb tail, bed nearly silent at idle → big empty space | §2 |
| birds or drops? confusing | drops are downward SINE CHIRPS = bird-call signature; tonal ambiguity with melodic notes | §3 |
| far-away bing = pressure | melodic notes too salient over a too-quiet bed, big reverb send → "distant notification" grammar | §4 |
| left/right difference | full-width random per-drop pan + decorrelation | §5 |
| not regular / lost | no audible steady anchor; irregular events over near-silence (Stallen: predictability+control) | §2, §6 |

## 1. Pacing overhaul (research-mandated)

- **Rate map becomes compressive**: discrete-drop rate = R_min + (R_cap − R_min) · log(1 + 9a)/log(10), a∈[0,1]. R_min = 0 (event-driven only at idle), **R_cap = 6 discrete drops/s** (auditory counting breaks past ~4–6/s; IEC alarm profiles live above that — stay below).
- **Discrete→wash crossfade**: activity beyond the 6/s cap does NOT add drops. From ~5/s upward, marginal intensity goes into the continuous rain-wash bed (pink-noise rain bed gain + slight brightening). Heavy work = *thicker wash*, not faster taps — how real rain encodes intensity.
- **Burst coalescing**: events with inter-onset < 250 ms merge into ONE drop with +weight (slightly louder/lower). Never stack 2–3 drops per event; **1 event = 1 drop** (weighted). This makes the mapping honest and calm at real cadence (0.05–0.5 events/s is inherently calm — keep strict 1:1 there, no statistical padding below a=0.15).
- **Pacing floors**: min inter-drop gap 150 ms; melodic notes min gap 2.5 s (idle self-play stays 1/45 s); slew the rate parameter (tau ≥ 2 s) with hysteresis so bursts can't lurch the texture.
- Whoosh/swells unchanged but attack ≥ 2 s.

## 2. From cave to warm room

- **Reverb**: shrink the space. Freeverb roomsize 0.90→**0.78** (feedback ≈ 0.918), damp → 0.45, wet level −6 dB vs v2, dry fraction up. Target RT60 ≈ **1.3–1.8 s** (was ~3–4). A small warm room, not a cathedral/cave.
- **Brightness**: raise mix spectral centroid into **400–900 Hz** (v2 measured 157 Hz). Means: bed gets a mid-register warm layer (add C3+G3 soft content), master lowpass opens to ~6 kHz default, rain bed slightly brighter. The §7 slope criterion is RELAXED: slope target now −4.5 ± 1.5 dB/oct measured 125 Hz–8 kHz, and conformity ±6 dB — do not chase slope at the cost of darkness. NEW criterion: centroid FLOOR 350 Hz and ceiling 1.2 kHz.
- **Bed presence = the control anchor**: active-session bed level raised so it is clearly audible under everything (target: bed alone ≈ −30 dBFS RMS; events peak ≤ 10 dB above the bed's RMS — embedding rule, §4). Idle bed −36 dBFS, still clearly present on earphones. The bed must be STEADY: OU drift stays but excursions halved. A listener should feel a continuously humming warm engine, never a void with occasional sounds.

## 3. Unambiguous sound identities

- **Drops become noise-based**: kill the downward sine chirps. New drop = 4–10 ms filtered NOISE tick (RBJ bandpass 1.8–3.5 kHz, Q 2–4, sharp exp decay) — reads as rain-patter/typewriter-adjacent tick, cannot read as a bird. Small register split by tool class stays (read brighter/quieter, exec lower via center-freq ×0.7 + slightly longer).
- **Melodic notes stay clearly instrumental** (FM e-piano r=1.0, NO r=3.5 bell voice — the bell is the "bing" offender; delete it from routine flow, keep a softened version ONLY for Notification/PermissionRequest where "needs you" is the intended message).
- No other tonal chirpy elements. If it could be mistaken for wildlife, it's wrong.

## 4. Embedding rule (no more lonely bings)

- Any pitched one-shot must sit ON the bed: note peak level ≤ bed RMS + 10 dB (knock exempt: ≤ +14 dB). Note reverb send −6 dB vs v2; notes shortened (decay tau ≤ 1.0 s).
- Idle self-play notes: quieter (≤ bed + 6 dB), mid register only (C3–A4), never the top octave alone.
- Stop cadence and acknowledgment notes follow the same embedding rule.

## 5. Stereo discipline

- Per-drop pan constrained to ±0.35; melodic notes ±0.2; knock/cadence center. Wash/bed decorrelation kept but mid/side ratio limited (S ≤ 0.5·M).
- NEW criterion: long-window (5 s) L−R RMS difference ≤ 1.0 dB; interchannel correlation 0.5–0.9 (was 0.3–0.9).

## 6. Predictability

- The soundscape's slow layers change only on slow, monotonic ramps (unchanged). Melodic pool selection biased to stepwise motion (next note within ±2 pool steps of previous) — melodies feel intentional, not random (predictability → control).
- Keep silence semantics (SessionEnd fade) unchanged.

## 7. Amended acceptance battery (verifier)

Replace v2 §7 items 1,2,7 and ADD:
1'. Slope −4.5 ± 1.5 dB/oct (125 Hz–8 kHz), band conformity ±6 dB.
2'. Centroid (Welch, full mix, active steady window) in **[350, 1200] Hz**. HF>5 kHz ≤ 10% unchanged.
7'. Stereo: correlation 0.5–0.9; 5 s-window |L−R| RMS ≤ 1 dB.
N1. Discrete-drop onset rate never exceeds 7/s in any 2 s window at any activity (render an a=1.0 stress scene to check).
N2. Embedding: pitched one-shot peaks ≤ bed RMS + 10 dB (knock +14) measured on the render.
N3. RT60 estimate of the tail after final event in [1.0, 2.2] s.
N4. Urgency ordering sanity: RMS-envelope "eventfulness proxy" (onset density × mean onset salience) must rank: failure moment > busy phase > calm phase > idle. 
All other v2 criteria (AM limits, tonal prominence, crest, stability, info checks, determinism) unchanged and must still pass after re-tuning.

## 8. Evaluation kit (new deliverable, eval/ directory)

Purpose: turn household listeners into scored, unbiased data points. Contents:
- `eval/README.md` — protocol: within-subject, order rotation, verbatim anti-priming script: "You'll hear short recordings of sounds a computer program makes while it works. There are no right or wrong answers — we're testing the sounds, not you."
- `eval/make_clips.py` — generates from the engine (fixed seeds): Block A vocabulary clips (8 × 10–15 s: sparse rain, dense rain, write-notes, knock, subagent choir entry, pressure weather, done cadence, needs-you chime); Block B scenario clips (4 × 60–90 s: calm+success, busy+success, failure+recovery, busy+subagents+unresolved) with ground-truth timestamp files; Block C: same session at 3 pacing variants (v2 mapping, v2.2 mapping, v2.2 at half density) — the flip-point probe.
- `eval/scoring-sheet.html` — printable 1-pager per scenario clip: 4 comprehension probes (activity contour choice of 4 cards; anything wrong? when ±10 s; final outcome 3-choice; one-thing-or-several), 8 semantic differential pairs (calm–frantic, in control–overwhelming, pleasant–annoying, natural–mechanical, informative–meaningless, relaxing–stressful, predictable–erratic, hours–minutes tolerance), ISO 12913 8-item circumplex block (agree 1–5: pleasant, vibrant, eventful, chaotic, annoying, monotonous, uneventful, calm), ICBEN 0–10 item ("Imagine this sound playing near you for an hour while you did your own things…"), 2 control items ("I could tell what would happen next", "The sound felt out of control").
- `eval/score.py` — enter responses, computes: information-transfer score/clip, affect score, ISOPleasant = [(p−a)+.707(ca−ch)+.707(v−m)]/(4+√32), ISOEventful = [(e−u)+.707(ch−ca)+.707(v−m)]/(4+√32), medians across listeners, per-listener trajectories, sign-test helper for A/B.
- Ground truth answer key kept separate (eval/answer-key.md).
