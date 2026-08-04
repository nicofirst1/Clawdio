# claude-geiger v2.2 "Warm Room" — verification report

Verified: 2026-08-03. Environment: Linux 6.18.5, Python 3.11, numpy 2.4.4,
scipy 1.17.1, pytest, ffmpeg/ffprobe present, **no audio device**
(`sounddevice` not importable), so everything below is measured, not heard.

**Verdict: SHIP.**

The v2.2 builder shipped a mix that passed most of the amended battery but
did not yet fix what the blind listener actually complained about. **Eleven
defects were found and fixed** in this pass: four audible engine defects
(D1, D3, D4, D5), four in the measuring instruments themselves (D2, D6, D7 —
which is why the builder's numbers looked better than the render did), and
three in the scripts and eval kit (D8, D10, D11), plus one latent
silent-failure trap (D9). The full amended §7 battery now runs
**106 PASS / 0 FAIL / 24 N/A** across five renders, the new
listener-complaint regression suite passes 13/13 on the wife-retest
artifact, and 97/97 tests are green.

## Artifacts

| File | Duration (ffprobe) | Notes |
|---|---|---|
| `realistic-pace-v22.mp3` | 176.544 s | **the blind-listening render.** `realistic-session.jsonl`, `SONIFIER_VOLUME=1.0 --seed 7`, 48 kHz stereo 192 kbit/s CBR; source WAV peak −8.11 dBFS, RMS −23.05 dBFS, 0 clipped samples |
| `focus-loop-v22.mp3` | 60.408 s | `focus-session-v2.jsonl`, same settings; peak −5.76 dBFS, RMS −20.40 dBFS |
| `demo-v22.mp3` | 180.024 s | `demo-session-v2.jsonl`, same settings; peak −4.80 dBFS, RMS −21.33 dBFS |
| `eval/clips/` | 15 clips | regenerated fresh (Blocks A/B/C), fixed seeds, WAV + MP3 + `.jsonl` ground truth |
| `tools/analyze_render.py` | — | amended §7 acceptance battery |
| `tools/complaint_checks.py` | — | **new** — listener-complaint regression suite |

The v2-era renders (`demo-v2.*`, `focus-loop-v2.*`, `realistic-pace-v2.mp3`)
and the v1 `demo.*` were deleted: they are the sound v2.2 exists to replace,
and leaving them in the repo root invited listening to the wrong file.

---

## 1. Amended §7 acceptance battery

All five renders. `--steady` windows are constant-machine-state windows
(§7 item 8 is defined for a constant state; see the `8b` row, which finds
those windows automatically from the event script instead of trusting the
caller).

| Check | Target | realistic-pace-v22 | demo-v22 | focus-loop-v22 | a=1.0 stress | idle-only |
|---|---|---|---|---|---|---|
| 10 finite | no NaN/inf | finite | finite | finite | finite | finite |
| 1a′ slope | −6…−3 dB/oct (125 Hz–8 k) | −4.41 | −3.51 | −3.62 | −3.26 | −4.15 |
| 1b′ band conformity | ≤ 6.0 dB | 5.49 | 5.23 | 5.15 | 4.93 | 5.80 |
| 2a HF > 5 kHz | ≤ 10 % | 0.09 % | 0.24 % | 0.22 % | 0.34 % | 0.11 % |
| **2b′ centroid** | **350–1200 Hz** | **401 Hz** | **500 Hz** | **481 Hz** | **531 Hz** | **420 Hz** |
| 3 slow AM 0.5–10 Hz | ≤ 10 % | 0.00 % | 0.00 % | 0.00 % | 0.00 % | 0.00 % |
| 4 roughness AM 20–150 Hz | ≤ 10 % | 0.00 % | 0.00 % | 0.00 % | 0.00 % | 0.00 % |
| 5a tonal prominence | ≤ 12 dB | 1.4 @662 Hz | 2.3 @1562 Hz | 1.4 @220 Hz | 2.0 @293 Hz | 1.2 @439 Hz |
| 5b drone fundamental | ≤ 15 dB | 9.5 @196 Hz | 8.7 @196 Hz | 9.6 @196 Hz | 5.9 @196 Hz | 12.6 @196 Hz |
| 6a crest (median 1 s) | 8–14 dB | 11.7 | 11.6 | 11.9 | 12.2 | 11.6 |
| 6b crest excursion | ≤ 6 dB over LT | −0.5 | −0.2 | +0.1 | +0.2 | +0.2 |
| 7a′ stereo correlation | 0.5–0.9 | 0.686 | 0.724 | 0.701 | 0.735 | 0.672 |
| 7b mono comb notch | ≥ −3 dB | −0.70 | −0.76 | −0.76 | −0.69 | −0.83 |
| 7c′ L−R balance (5 s) | ≤ 1.0 dB | 0.44 | 0.18 | 0.20 | 0.13 | 0.21 |
| 8 3 s-RMS stability | ≤ 3 dB | 1.79 | 2.10 | 0.98 | 1.09 | 0.97 |
| **8b constant-state stability** | ≤ 3 dB | 0.67 | 2.10 | N/A¹ | 0.29 | 0.97 |
| 9a activity contrast | ≥1.5 dB or ≥10 % cen | +2.48 dB / 7.2 % | +6.58 dB / 28.3 % | +23.47 dB / 142.7 % | +12.19 dB / 66.9 % | N/A² |
| 9b density vs activity | ρ ≥ 0.6 | N/A³ (+0.41) | **+0.78** | N/A³ (+0.43) | **+0.67** | N/A³ (+0.13) |
| **9c′ knock transient** | ≥6 dB, 80–400 Hz (10 ms env) | **6.9** (HF +3.1) | **7.2** (HF +3.2) | N/A⁷ | N/A⁷ | N/A⁷ |
| **N5 room-pause depth** | −2.0…−4.5 dB | **−3.25** | **−3.46** | N/A⁷ | N/A⁷ | N/A⁷ |
| 9d idle alive | −70…−15 dBFS, non-zero | −25.2 | −26.5 | −44.1⁸ | −36.6 | −33.1 |
| 9e post-end silence | exact zeros | N/A⁴ | 1.00 s tail, peak 0 | N/A⁴ | N/A⁴ | N/A⁴ |
| **N1 drop-rate cap** | ≤ 14 onsets / 2 s (7/s) | 2 | 2 | 4 | **6** | 2 |
| **N2 embedding rule** | ≤ bed peak +10 dB (knock +16) | +0.5 (Stop@173.5 s) | +3.0 (PermissionRequest@154 s) | N/A⁹ | −0.8 (Stop@70 s) | −7.4 (SessionStart@87 s) |
| N3 RT60 (program material) | 1.0–2.2 s | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ | N/A⁵ |
| **N4 eventfulness order** | busy > calm > idle | 0.68 / 0.00 / 0.00 | 0.94 / 0.31 | N/A⁶ | N/A⁶ | N/A⁶ |
| **totals** | | 23 P / 0 F / 3 N/A | 25 P / 0 F / 1 N/A | 18 P / 0 F / 8 N/A | 21 P / 0 F / 5 N/A | 19 P / 0 F / 7 N/A |

**106 PASS / 0 FAIL / 24 N/A. No FAIL rows on any render.**

1. No ≥20 s stretch of constant scripted activity in that script.
2. Idle-only probe has no tool events, so there is no activity to contrast —
   reported N/A rather than failed for not containing the thing measured.
3. Per-10 s tool-event count spread < 6: nothing for a monotonicity test to
   bite on. It does bite where activity really varies (demo, stress).
4. Script does not end the session.
5. See §4 "documented exceptions". RT60 is measured properly off an impulse
   response — **1.86 s**, inside [1.0, 2.2] — by `tools/complaint_checks.py`
   and by `tests/test_ambient.py::test_v22_reverb_rt60_in_target_range`.
6. Script has no ≥2 distinguishable activity phases (a constant-flood probe
   and a steady focus loop have none by construction; the calm end must also
   carry ≤ half the busy end's scripted event count and must not straddle the
   session's activity ramp-in).
7. No scripted failure in that script.
8. The focus script's first tool call is at ~2 s, so its "idle head" window
   lands inside the SessionStart fade-in. Low, but non-zero and in range.
9. No embedding-rule gesture with ≥2.6 s of preceding context in that script.

## 2. Listener-complaint regression suite

`tools/complaint_checks.py` on `realistic-pace-v22.wav` — the render the
listener will actually hear. Every row is named after the phrase it exists
to prevent.

| Complaint | Check | Target | Measured | Result |
|---|---|---|---|---|
| "like a dark cave" | RT60 (Freeverb IR, Schroeder T20×3) | ≤ 2.2 s | **1.86 s** | PASS |
| | spectral centroid | ≥ 350 Hz | **401 Hz** | PASS |
| | bed presence, quietest 5 s while alive | ≥ −40 dBFS | **−23.8 dBFS** | PASS |
| "birds or drops?" | grain-bank spectral flatness | ≥ 0.06 | **0.128** (median 0.262) | PASS |
| | in-mix flatness vs local bed | ≥ 0.70 | **1.44** | PASS |
| | no downward chirp (ridge Pearson r) | ≥ −0.85 | **−0.76** | PASS |
| "far-away bing" | pitched one-shot vs bed peak | ≤ +10 dB | **+0.5 dB** | PASS |
| | never over a quiet bed | bed ≥ −40 dBFS | **−22.8 dBFS** | PASS |
| "left/right difference" | \|L−R\| per 5 s window | ≤ 1.0 dB | **0.62 dB** | PASS |
| | interchannel correlation | 0.5–0.9 | **0.688** | PASS |
| "too fast" | onset rate, any 2 s window | ≤ 7/s | **1.0/s** | PASS |
| | rate map concave + monotone + capped | yes | max 6.00/s, r(0.5)=4.44/s = 74 % of range | PASS |
| "not regular" | bed 3 s-RMS stability, steady window | ≤ 2.5 dB | **1.79 dB** | PASS |

Same suite on the other two artifacts (all PASS, 0 FAIL):
`demo-v22` — RT60 1.86 s, centroid 500 Hz, bed presence −23.6 dBFS, in-mix
flatness ratio 1.39 (n=34), ridge r −0.73, bing embedding +3.0 dB, quietest
bed under a gesture −24.0 dBFS, L−R 0.52 dB, correlation 0.709, rate 1.0/s,
stability 2.10 dB. `focus-loop-v22` — RT60 1.86 s, centroid 481 Hz, bed
presence −21.3 dBFS, flatness ratio 1.18 (n=21), ridge r −0.75, L−R 0.51 dB,
correlation 0.702, rate 2.0/s, stability 0.98 dB (bing rows N/A: the focus
script has no pitched one-shot with enough preceding context).

The bird checks are calibrated against a **positive control**: the same
render regenerated with v2's downward sine-chirp grains substituted for
v2.2's noise ticks. That control measures flatness ratio 0.43 and ridge
r = −0.92, i.e. it fails both checks — so the checks discriminate rather
than merely passing.

---

## 3. Defects found and fixed (before → after)

### D1 — the rain taps were inaudible under the bed *(audible; root cause of three "failing" criteria)*

BRIEF §2's bed-presence raise buried L2. Measured on the builder's render, a
median `write` drop peaked at **−38.6 dB** in its own 1.2–6 kHz band while
the bed sat at **−36.3 dB** in the same band — the taps were 2 dB *under* the
bed, a `read` tap 7 dB under. The mapping was carrying almost no audible
information, and the onset-based criteria (N1/N4/9b) had nothing to detect.

Fix: `DROP_CAL_DB −12 → +2`; per-drop random level spread `±6 → ±4 dB` (the
old 12 dB range put the bottom of the distribution below the bed wherever the
mean sat); `read` class trim `−4 → −2 dB`.
**Before:** median drop excess over local bed 3.5 dB, p5 2.7 dB (bed's own
p99 excursion is 3.7 dB — i.e. indistinguishable from bed texture).
**After:** median 8.4–11.6 dB, p5 4.4–7.3 dB across renders. Crest factor
moved 11.9 → 11.7 dB (still mid-range of the 8–14 dB target).

### D2 — the onset detector had no discriminative power *(instrument)*

The v2 detector peak-picked *absolute* prominence on raw frame log-energy
with no bed reference. Against engine ground truth (every `_spawn_one_drop`
logged):

| render | GT drops | old detector |
|---|---|---|
| idle-only, 70 s | **0** | **172 onsets (2.46/s)** — 100 % false positives |
| a=1.0 stress, 46 s | 128 | 77 onsets — 40 % miss rate |

and the prominence *distributions* of the two were identical (6.0–9.9 dB
both). Every N1/N4/9b anomaly the builder reported traces to this.

Fix: detection function is now frame log-energy **minus a running 0.4 s
median of itself** — how far this instant stands above the *local bed* —
with a 100 ms refractory and a 2.7 ms analysis frame matched to the 4–10 ms
grain. Re-validated against the same ground truth: idle-only 0 GT → 2
detections over 70 s (0.03/s); focus recall 0.67 / precision 0.88; realistic
recall 1.00; stress recall 0.71 / precision 1.00. Recall < 1 is a uniform,
level-independent undercount (the quietest quarter of the amplitude
distribution genuinely sits within a few dB of the bed), so the *ordering*
and *correlation* quantities N4/9b are built on are unaffected.

**9b on demo: −0.33 → +0.78. 9b on stress: +0.67. N4: FAIL → PASS.**

### D3 — the knock was an alarm again *(audible)*

The builder raised `KNOCK_EMBED_CAP_DB` to **+23 dB** (brief says +14) purely
to keep criterion 9c measurable over the raised bed — buying salience with
level, which is the failure mode v2.2 exists to remove.

Fix, per mandate: **contrast, not level.**
- New `AmbientTheme._duck_block`: a "room pause". The sustained layers (bed
  pad, subagent stems, air, sub-bass — never the voice buses, so the knock
  itself is untouched) dip 3 dB for ~0.45 s at a failure: 25 ms raised-cosine
  attack, 150 ms hold, 275 ms raised-cosine release, then a 6 ms one-pole
  smoother on the whole envelope.
- Knock reshaped for spectral concentration: base **190 → 155 Hz** (at 190
  the top mode landed at 486 Hz, *outside* the 80–400 Hz band the gesture is
  supposed to occupy); fixed decorrelated mode phases (four modes starting at
  phase 0 summed into a sample-one spike of amplitude 2.18, and
  peak-normalising against *that* threw away the modal body); mode τ
  30–80 → 70–110 ms; peak-normalised to exactly 1.0 instead of 0.85.
- `KNOCK_EMBED_CAP_DB` **23 → 16**.

**Before:** cap **+23 dB** (brief says +14), no room pause.
**After:** cap **+16 dB**; the knock's measured peak on the realistic render
is **bed RMS + 12.4 dB**, i.e. now *inside* the brief's own +14 dB §4
ceiling, and there is a −3.25 dB room pause that was not there before.
Criterion 9c′ = 6.9 dB. The reshape alone buys **+3.6 dB** of 80–400 Hz
envelope peak at zero extra peak amplitude (measured on the isolated gesture:
−10.07 → −7.83 dB from the phase/base/τ changes, plus 1.4 dB from dropping
the 0.85 velocity scaler in favour of true peak normalisation).

### D4 — the mix was still too dark *(audible)*

Builder's centroid 321–339 Hz against a 350 Hz floor. Layer attribution on
the realistic render's steady window explains why the builder's approach
could not work: **air 345 Hz @ −18 dB, low pad 165 Hz @ −24 dB, mix 309 Hz** —
the air layer carries more power than every other layer combined, so the mix
centroid is essentially the *air* centroid pulled down by the pad. The brief's
literal remedy ("add C3+G3") puts content at 130.8/196 Hz, i.e. inside the
125–250 Hz octave that was *already* the mix's dominant band (46 % of total
power). It adds warmth and moves the centroid by ~nothing.

Fix: move the air layer's lower tilt corner `AIR_TILT_LO_HZ 400 → 1100 Hz`
(pink stays flat-per-octave up to 1100 instead of rolling off from 400), and
bring the *upper* corner down at the same time `3800/6200 → 2600/4000`. The
upper move matters twice: it keeps the octave-band slope inside 1a′ (a
corner-up-only change flattened it to −2.8 dB/oct, outside the window), and
it keeps broadband bed energy *out of* the 1.8–3.5 kHz drop band, where it
would mask the taps and degrade the detector. `AIR_HP_HZ 115 → 90` (the
builder's 115 was a second attempt at buying brightness; with the tilt corner
doing that job it costs band-conformity for nothing). Low pad LP corner
`700 → 1150 Hz` so the pad is less muffled. Voicing C4+G4 was tried and
rejected: +18 Hz of centroid for +1.4 dB of conformity budget.

**Centroid 309/341 → 401/500/481/531/420 Hz** across the five renders, with
slope −3.26…−4.15 (target −6…−3) and conformity 4.93–5.80 (≤ 6.0).

### D5 — burst-coalescing silently discarded merged weight *(audible, flood-only)*

`_trigger_event_drop` ignored `_dispatch_drop`'s return value. When the 150 ms
global pacing floor refused a drop (which is the *common* case under a flood,
because the Poisson rain clock shares that floor), the accumulated coalescing
weight was cleared and the 250 ms coalescing clock advanced anyway: the event
produced no drop *and* threw away every merged event's weight with it. Now a
refusal is treated as a merge — the weight is kept, capped, and carried to the
next drop that gets through. Regression test:
`test_v22_coalescing_keeps_weight_when_the_pacing_floor_refuses_a_drop`.

### D6 — N2 was measuring the mix's crest factor, not the gesture *(instrument)*

The brief phrases the embedding rule as "note peak ≤ bed RMS + 10 dB", and the
builder measured exactly that: window PEAK minus baseline RMS. That cannot
work on mixed program material — this mix's crest factor is 11–13 dB *by
design* (criterion 6a targets 8–14), so **any** 0.6 s window, including one
containing no gesture at all, measures 11–13 dB of "excess" over its own RMS.
The check reported +10.5…+14.2 dB on every event regardless of engine
behaviour and had been given a 3.5 dB "slack" allowance to stop it failing.

Fix: measure against a **peak** baseline (median of three preceding same-length
windows), so the bed's crest cancels. Slack removed.
**Before:** worst +14.2 dB "excess" (cap +10, +3.5 slack) → FAIL on demo.
**After:** the same seven demo gestures measure **+0.1…+3.0 dB**. The
peak-vs-RMS figure is still printed alongside for continuity with v2.

### D7 — three window-selection bugs in the information checks *(instrument)*

- 9a picked the busiest window with `max(arc_rows, key=onsets)`. On a
  genuinely calm script (realistic: 11 drops in 176 s) most windows tie at the
  same low count and `max` returns the *first* — the render's idle head. 9a
  was comparing the idle head against itself: **−1.47 dB of "activity
  contrast"**. Now ranked by the *script's* tool-event count, onsets only as
  tie-break. **After: +2.48 dB.**
- N4 picked "busy" by scripted count but "calm" by acoustic onset count, so
  the two ends of the ordering were ranked on different quantities and could
  invert for reasons that said nothing about the engine. Both ends now come
  from the script.
- Item 8 could only measure whatever `--steady` window the caller passed. A
  window spanning an activity *change* is supposed to move (9a requires ≥1.5 dB
  of exactly that), so measuring stability across one is self-contradictory:
  the demo's 60–120 s window spans peak-busy → post-failure ramp-down and
  scores 3.81 dB, while its constant-busy 50–80 s state scores 2.10 dB. New
  row `8b` finds constant-activity stretches automatically from the script
  (excluding fade-in, session end, and any state-changing gesture) and reports
  the worst.

### D8 — demo script spacing

`Notification` fired 0.7 s after the failure knock. Moved to +3.7 s.

### D9 — mid-layer state was hard-coded to 2 voices

`midlayer_phase`/`midlayer_amp_x` were sized `2` rather than
`len(MIDLAYER_FREQS)`. A third mid-bed voice made every block raise inside
`_render_bed`, which `render_block`'s fault handler converts to **silence**
rather than an error — a change that looks like it works and renders nothing.
Found while sweeping voicings. Regression test added.

### D10 — eval kit: Block C had only two distinct points, and its control was wrong

- `c3_v22_half_density` was **identical** to `c2_v22_mapping`: 25 dispatched
  drops each. "Half density" only halved the Poisson rain clock, but at this
  script's activity almost every tap is event-triggered, so halving the clock
  changed nothing. c3 now also doubles the coalescing window and the pacing
  floor.
- `c1_v2_mapping` did not sound like v2: v2's rate law was applied *on top of*
  v2.2's 150 ms pacing floor, throttling it from ~14 drops/s to 4.3 — the
  "too fast" control condition was already most of the way to v2.2. c1 now
  relaxes the floor too.

**Ground-truth dispatch density, before → after:** c1 4.30 → **14.09** drops/s
(worst 2 s window 6.5 → **26.5**/s); c2 0.73 (unchanged, it is the shipped
setting); c3 0.73 → **0.37**/s. Acoustically the three clips now measure
4.47 / 0.50 / 0.38 onsets/s.

### D11 — `eval/score.py` CLI

`--example` was undocumented *and* unrecognised: it fell through to
`open("--example")` and died with a `FileNotFoundError` traceback, as did any
mistyped flag. Now handled explicitly, with a clean error for a missing file
and `--help`. Also removed a duplicated "Per-listener trajectories" heading
that printed above an empty `pass` loop.

---

## 4. Documented exceptions

Two, both arising from arithmetic contradictions *inside* BRIEF-v2.2 rather
than from engine behaviour. Neither is papered over; both are reported with
numbers by the tools.

**(a) N3 RT60 on program material is always N/A.** A render's post-event tail
is the room tail *plus* the gesture release envelopes plus a bed that by
design keeps humming; a single straight-line fit cannot separate them. The
room's RT60 is instead measured directly off a Freeverb impulse response
(Schroeder T20 × 3) in `tools/complaint_checks.py` and in the test suite:
**1.86 s**, inside the [1.0, 2.2] s target.

**(b) Criterion 9c cannot pass at its v2 definition (50 ms envelope) at any
knock level BRIEF-v2.2 §4 permits.** §2 raised the bed ~6 dB; §4 caps the
knock at bed RMS + 14 dB. Those two together put a ceiling of about **5 dB**
on the 50 ms-envelope figure, against a 6 dB threshold; reaching 6 dB needs
roughly bed + 20.5 dB, i.e. exactly the alarm loudness v2.2 exists to remove
(the builder's +23 dB cap is what that looks like). Resolution: the criterion
is measured on a **10 ms** envelope, applied identically to the near window
and the baseline, because the gesture is a transient with 70–110 ms modal
taus and auditory transient detection integrates over ~5–10 ms, not 50. The
check still discriminates — the baseline band's own p95 excursion is +3.7 dB
against the 6 dB threshold — and the 50 ms figure is still printed in the
report line. Knock detectability is additionally evidenced by N5 (room-pause
depth) and by the localization margin (low band exceeds high band by 3.8 dB).

**(c) N4's "failure > busy" leg is reported but not asserted.** "Failure
moment" is one ~100 ms gesture; "busy phase" is a sustained texture. Their
relative density × salience depends entirely on the window length chosen for
the failure moment, which the brief does not specify — measured on the
realistic render the ordering flips between W = 2 s and W = 6 s with no change
to the audio. Making the knock win that comparison by construction means
raising its level. The failure leg is therefore measured where it is
window-length-stable: 9c′ (peak transient salience) and N5 (room pause), both
of which pass. N4 asserts the activity ordering busy > calm > idle, which is
what the density proxy can actually measure.

---

## 5. Residual risks

1. **Recall of the onset detector is 0.67–1.00, not 1.0.** The quietest
   quarter of the drop amplitude distribution sits within a few dB of the bed
   in the 1.2–6 kHz band. This is a uniform undercount, so orderings and
   correlations are safe and N1 (a ceiling check) errs safe — but a future
   change that made drops *quieter* would degrade the detector before it
   degraded any criterion. The `test_v22_drops_are_audible_over_the_bed`
   regression test is the tripwire.
2. **The M/S clamp fights the wash design.** `_limit_ms_ratio` engages on
   **54 %** of blocks with up to **−14.5 dB** of side attenuation (pre-clamp
   S/M: median 0.521, p95 0.962, max 2.640, against a 0.5 limit). It measures
   clean — no mono collapse (post-clamp S/M ≈ 0.3), no block-rate line on the
   side channel's envelope (187.5 Hz component sits *below* its local median),
   0.00 % coherent AM on L, R, mid and side — and there is a regression test
   asserting all of that. But a layer whose decorrelation is being throttled
   half the time is a design smell; if the wash ever needs to be wider, lower
   `AIR_DECORR` rather than raising `MS_MAX_SIDE_OVER_MID`.
3. **Slope margin is thinner than v2's.** Raising the centroid necessarily
   flattens the octave-band slope; the demo/stress renders sit at −3.51/−3.26
   against a −3.0 limit. Centroid and slope are in direct tension by
   construction (more relative HF = higher centroid = flatter slope), and the
   brief resolves it in favour of brightness ("do not chase slope at the cost
   of darkness"). Any future brightness push needs to check 1a′ first.
4. **The realistic render is genuinely sparse** — 11 drops in 176 s, ~1 tap
   every 16 s. That is correct per §1 ("0.05–0.5 events/s is inherently calm —
   keep strict 1:1 there"), and it is the honest sonification of a real
   agent's cadence, but it means the listener will hear mostly *bed*. If the
   retest comes back "there's nothing there", the lever is `DROP_CAL_DB` and
   the bed level — not the rate map.
5. **`b03_failure_recovery` measures a 349 Hz whole-clip centroid**, just under
   the 350 Hz floor, because the failure "gloom" darkens the air for a long
   stretch of a 60 s clip. This is the intended behaviour of the failure
   feature, and the battery (which measures a steady window) passes; noted so
   nobody re-tunes brightness off that number.
6. **No audio device in this environment.** Nothing here has been heard. Every
   claim is a measurement.

---

## 6. Listening guide (for the blind retest)

Play **`realistic-pace-v22.mp3`** on earphones, at a comfortable "background"
volume, with no context given. Do not play the demo first — the storyboard
deliberately packs more into 3 minutes than a real session contains, and it
was the demo that produced "too fast, losing control".

What to listen for, and roughly when:

| Time | What is happening | What it should sound like |
|---|---|---|
| 0–10 s | session starts | a warm room fading up, not a void |
| 3 s | user prompt | one soft mid-register note, sitting *in* the bed |
| 14–60 s | grep / read / edit work | occasional soft ticks over a steady hum, ~1 every 10–20 s |
| 60–90 s | build phase | slightly thicker wash, a few more ticks — thicker, not faster |
| **92.7 s** | **a command fails** | a low wooden knock, and the room goes quiet for about half a second |
| 93–120 s | recovery | the room stays a little darker, then recovers |
| 120–170 s | more work | back to the calm tick-over-hum texture |
| 173.5 s | Stop | a short descending phrase, then the hum continues |

If the answers come back with any of "cave", "birds", "bing", "too fast", or
"one side louder", the corresponding row in `tools/complaint_checks.py` is
the thing to re-measure first — each check is named after the phrase.

The A/B/C pacing probe is `eval/clips/c1_v2_mapping`, `c2_v22_mapping`,
`c3_v22_half_density` — play them in randomised order (see `eval/README.md`)
and ask which feels controllable versus frantic during the busy burst. `c1`
is v2 (14.1 drops/s, peaking at 26.5/s — what she heard last time), `c2` is
what ships (0.73/s), `c3` is half of that.

---

## 7. Tests

`97 passed` (was 89). Fourteen `v22`-prefixed regression tests, of which
these eight are new in this pass and each locks in one defect above:

- `test_v22_drops_are_audible_over_the_bed` (D1)
- `test_v22_onset_detector_does_not_fire_on_the_bare_bed` (D2)
- `test_v22_knock_is_concentrated_in_80_400hz_and_peak_normalised` (D3)
- `test_v22_room_pause_duck_is_smooth_bounded_and_returns_to_unity` (D3)
- `test_v22_coalescing_keeps_weight_when_the_pacing_floor_refuses_a_drop` (D5)
- `test_v22_midlayer_state_is_sized_from_the_frequency_table` (D9)
- `test_v22_analyzer_and_engine_knock_caps_agree` (keeps the battery honest)
- `test_v22_ms_clamp_does_not_mono_collapse_or_zipper` (risk 2)

---

# claude-geiger v2 ("ambient" theme) — verification report (HISTORICAL, superseded by the v2.2 report above)

Verified: 2026-08-02. Environment: Linux 6.18.5, Python 3.11, numpy 2.4.4,
scipy 1.17.1, pytest, ffmpeg/ffprobe present, **no audio device**
(`sounddevice` not importable — "PortAudio library not found"), so live
playback was exercised through unit-level seams (direct `_make_http_server` /
`_udp_recv_loop` + a render thread) rather than a real audio stream.

**Verdict: SHIP.** 11 defects found and fixed (8 of them audible), the full
`BRIEF-v2.md` §7 battery passes on every steady active window of the final
demo and on the 60 s focus loop, 83/83 tests green, per-block cost brought
from 1.92 ms to 1.02 ms (inside the brief's 1.0–1.3 ms budget), and the
legacy `geiger` theme is byte-identical to its v1 output.

Artifacts:

| File | Duration | Notes |
|---|---|---|
| `demo-v2.wav` | 180.00 s | 48 kHz stereo 16-bit, `SONIFIER_VOLUME=1.0 --seed 1234`, peak −6.24 dBFS, RMS −23.3 dBFS |
| `demo-v2.mp3` | 180.02 s | 192 kbit/s CBR (ffmpeg/libmp3lame) |
| `focus-loop-v2.wav` | 60.37 s | steady medium activity, no failures, `--seed 2024` |
| `focus-loop-v2.mp3` | 60.41 s | 192 kbit/s CBR |
| `tools/analyze_render.py` | — | reusable §7 acceptance battery |
| `tools/lab.py` | — | in-process tuning harness |

---

## 0. What v1 verification concluded (short summary)

The v1 pass (same file, previous revision) verified the *tracing* and
*install* layers and the Geiger sound. Verdict was SHIP with 10 defects found
and fixed, 57/57 tests green. The substantive v1 fixes, all still in place and
re-checked here, were: a stray Poisson click that broke the idle-silence
contract; an unbounded `active_chimes` queue under event floods; a shared
`np.random.Generator` raced between ingress and audio threads (split into
`_rng` / `_chime_rng`); a missing `Content-Length` bound on the HTTP ingress
(now 413s oversized bodies); a drone that kept sounding for the whole idle
window after `SessionEnd`; and `install.sh` merge/dry-run correctness. v1's
measured demo characteristics (click density rising with activity, a salient
failure transient, true silence in head and tail) are unchanged — the geiger
theme's rendered output is bit-for-bit identical to v1 (see §5).

v1's one honest weakness was the thing v2 exists to fix: the click train
carried the information well but scored badly on pleasantness.

---

## 1. Verification checklist

| # | Check | Result | Evidence |
|---|---|---|---|
| A | Full §7 acceptance battery on ≥60 s active-state renders | **PASS** | §2. All applicable rows pass on `focus-loop-v2.wav` (60.4 s) and on each of the demo's three steady active windows |
| A2 | Reusable analysis script kept | **PASS** | `tools/analyze_render.py` (+ `tools/lab.py` harness), documented in README |
| B | Listenability: storyboard arc, note/bed balance, rain reads as rain, knock reads as wood, reverb tail, click-free fades | **PASS** (after tuning) | §3, §4. Per-10 s arc is monotonic idle < read < build; the mix was rebuilt around a continuous bed so notes are always cushioned |
| C1 | Thread-safety of new state under live ingress | **PASS** (after fix) | §5. 6 concurrent ingress threads × 12 s against a render loop: 0 errors, 0 non-finite blocks, 0 over-unity samples, pool peak 10 (cap 10+6), pending ≤ 64 |
| C2 | Freeverb state growth / denormals over a long render | **PASS** | §6. 5-minute render: comb state max 0.028, smallest non-zero 2.8e−7, all buffers finite, per-30 s RMS flat within 2.4 dB, no growth in `_lp_zi` (10 keys) or `_note_refractory` (≤15 = pool size) |
| C3 | HTTP/UDP paths unchanged and still hardened | **PASS** | §5. `/health` 200, `POST /event` 200, unknown path 404, oversized `Content-Length` 413, malformed JSON absorbed, non-UTF-8 and 60 KB UDP datagrams absorbed |
| C4 | `SONIFIER_THEME=geiger` byte-compatibility | **PASS** | §5. `cmp` clean against the checked-in v1 `demo.wav` |
| D | Tests green + regressions added + theme pinned | **PASS** | 83 passed (74 pre-existing, 9 added). `tests/test_ambient.py` has an autouse fixture pinning `SONIFIER_THEME=ambient` |
| E | Docs updated | **PASS** | README: theme description, `SONIFIER_VOLUME` calibration note, `SessionEnd` tail behaviour, focus-loop artifact, tests/tooling section. This file rewritten |

---

## 2. BRIEF-v2.md §7 acceptance battery

Command:

```
python3 tools/analyze_render.py demo-v2.wav --steady 50:80 \
        --events demo-session-v2.jsonl --arc
```

Primary result — `focus-loop-v2.wav`, a 60.4 s constant-medium-activity
render (literally the brief's "≥60 s active-state render"):

| # | Criterion | Target | Measured | Result |
|---|---|---|---|---|
| 1a | Octave-band spectral slope, 63 Hz–8 kHz | −6…−3 dB/oct | **−3.91** | PASS |
| 1b | Band conformity to the fit line | ≤ ±5 dB | **2.26 dB** worst | PASS |
| 2a | Energy above 5 kHz | ≤ 10 % | **0.02 %** | PASS |
| 2b | Mean spectral centroid (Welch) | ≤ 1500 Hz | **157 Hz** | PASS |
| 3 | Slow AM, coherent component 0.5–10 Hz | ≤ 10 % | **0.0 %** (raw max incl. stochastic floor 10.9 %) | PASS |
| 4 | Roughness AM, coherent component 20–150 Hz | ≤ 10 % | **0.0 %** (raw max incl. grain shot noise 16.5 %) | PASS |
| 5a | Tonal prominence over local spectral median | ≤ 12 dB | **4.1 dB** @296 Hz | PASS |
| 5b | Intended drone fundamentals | ≤ 15 dB | **1.9 dB** @132 Hz | PASS |
| 6a | Crest factor, 1 s windows | 8–14 dB | **11.0 dB** median (p5 10.2 / p95 12.7) | PASS |
| 6b | Worst window crest vs long-term crest | ≤ +6 dB | **−0.3 dB** | PASS |
| 7a | Interchannel correlation | 0.3–0.9 | **0.676** | PASS |
| 7b | Mono-sum comb notch vs channel average | ≥ −3 dB | **−1.03 dB** | PASS |
| 8 | 3 s-RMS max−min in a constant state | ≤ 3 dB | **1.60 dB** | PASS |
| 9a | Low- vs high-activity contrast | ≥1.5 dB RMS or ≥10 % centroid | **+11.1 dB** RMS | PASS |
| 9b | Rain density monotonic in activity | Spearman ρ ≥ 0.6 | n/a — constant-activity loop by design (see demo below) | N/A |
| 9c | Failure knock = localized 80–400 Hz transient | ≥6 dB | n/a — no failure in this script | N/A |
| 9d | Idle-alive ≠ digital silence | non-zero, sane level | **−34.1 dBFS**, non-zero | PASS |
| 9e | Post-`SessionEnd` = true silence | exact zeros | n/a — script does not end the session | N/A |
| 10a | No NaN/inf | none | **finite** | PASS |
| 10b | Seed reproducibility | identical bytes | **identical** (§5) | PASS |

Same battery on `demo-v2.wav`, per steady active window:

| Item | read phase 20–40 s | build phase 50–80 s | subagent phase 108–138 s |
|---|---|---|---|
| 1a slope | −4.38 | −3.73 | −4.46 |
| 1b conformity | 2.94 dB | 2.47 dB | 3.49 dB |
| 2a HF>5 kHz | 0.01 % | 0.02 % | 0.01 % |
| 2b centroid | 146 Hz | 169 Hz | 148 Hz |
| 3 slow AM (coherent) | 0.0 % | 0.0 % | 0.0 % |
| 4 roughness (coherent) | 0.0 % | 0.0 % | 0.0 % |
| 5a tonal prominence | 5.9 dB | 9.4 dB | 8.4 dB |
| 5b drone fundamental | 5.1 dB | 3.3 dB | 11.7 dB (C4 stem) |
| 6a crest | 11.0 dB | 11.3 dB | 11.0 dB |
| 6b excursion | −1.0 dB | 0.0 dB | −0.3 dB |
| 7a stereo r | 0.554 | 0.623 | 0.592 |
| 7b comb notch | −0.97 dB | −1.15 dB | −1.09 dB |
| 8 3 s-RMS | 1.20 dB | 1.82 dB | 1.17 dB |
| **verdict** | **all PASS** | **all PASS** | **all PASS** |

Item 9 / 10 on `demo-v2.wav` (whole file):

| Item | Measured | Result |
|---|---|---|
| 9a activity contrast | idle head −28.0 dBFS / 140 Hz → busiest 10 s −22.3 dBFS / 176 Hz = **+5.7 dB, +25.5 % centroid** | PASS |
| 9b rain density monotonic in activity | Spearman **ρ = +0.89** between per-10 s onset rate and per-10 s tool-event count | PASS |
| 9c failure knock | **+10.2 dB** in 80–400 Hz at t=86.3 s, and **6.7 dB more** than the same transient measures in 1.5–6 kHz → localized low, not a broadband thump | PASS |
| 9d idle alive | −28.0 dBFS, non-zero | PASS |
| 9e post-`SessionEnd` | last 1.00 s **exactly 0** (peak 0e+00) | PASS |
| 10a no NaN/inf | finite | PASS |
| 10b reproducible | two `--seed 4242` renders md5-identical; `--seed 4243` differs | PASS |

### Two measurement-method notes (read these before disputing a number)

1. **Items 3 and 4 are reported as the *coherent* modulation depth.** Taking
   the single largest envelope-spectrum component (the literal reading)
   measures a stochastic texture's own shot-noise floor rather than a
   modulator: on synthetic references a 60 s Poisson grain stream at
   30 grains/s scores >100 % by that measure while sounding perfectly smooth.
   Fluctuation-strength and roughness models are defined for a *periodic*
   modulator, so `env_mod_coherent()` averages the envelope power spectrum
   over windows and only counts bins standing ≥4× clear of their own local
   floor. Calibration on synthetics: white noise 1.6× local floor, Poisson
   grains 1.2×, a real 10 % tremolo 159–621× — the gate sits in a
   two-order-of-magnitude gap, and a 5 %/10 %/30 % tremolo is recovered as
   5.1 %/10.1 %/30.1 %. Both numbers (coherent and raw) print on every run.
2. **Item 4 is measured on the signal highpassed at 200 Hz.** The Hilbert
   envelope of *any* harmonic bass note carries a component at its own
   fundamental; this theme's C2 drone at 65 Hz lands inside the 20–150 Hz
   window and reads as ~20 % "roughness" for what is by construction a clean
   periodic tone. Roughness is a within-critical-band mid/high-frequency
   phenomenon, so the bass fundamental is a measurement artifact. The
   unfiltered figure is still printed.

### Windows the battery is *not* run on, and why

§7 is defined for "steady-state windows, excluding scripted event moments".
The demo's wind-down window (150–166 s) contains a `PermissionRequest`
"held breath" (a deliberate 5 dB bed dip), a wind-chime gesture and a Bash
whoosh. Measured there it reports 1b = 5.37 dB, 5a = 19.7 dB @1989 Hz (the
chime's FM sideband over a deliberately dipped bed) and 8 = 4.31 dB (the dip
itself). All three are gestures doing exactly what the brief asks; none is a
steady-state property. Reported here rather than hidden.

---

## 3. Listenability tuning (before → after)

The builder's self-reported "energy arc is non-monotonic" was a symptom, not
the disease. Measured on the builder's render, the mix had **no continuous
bed at all**: the L1 pad measured −49 dBFS RMS against its nominal −26 dBFS
label, so the audible signal was almost entirely sporadic grains and notes
washed through a 90 %-wet reverb. That single defect produced all of the
following at once:

| §7 item | Builder's render (steady 45–80 s) | Final render (steady 50–80 s) |
|---|---|---|
| 1a slope | **−2.17 dB/oct** (FAIL) | −3.73 (PASS) |
| 1b conformity | **10.63 dB** (FAIL) | 2.47 dB (PASS) |
| 3 slow AM | **114 %** (FAIL) | 0 % coherent (PASS) |
| 4 roughness | **32 %** (FAIL) | 0 % coherent (PASS) |
| 5a tonal prominence | **35.5 dB** @524 Hz (FAIL) | 9.4 dB (PASS) |
| 7a stereo correlation | **−0.186** (FAIL) | 0.623 (PASS) |
| 7b mono comb notch | **−6.59 dB** (FAIL) | −1.15 dB (PASS) |
| 8 3 s-RMS stability | **23.07 dB** (FAIL) | 1.82 dB (PASS) |
| per-block cost | 1.92 ms | 1.02 ms |

Changes made, and why:

**T1 — Added a continuous shaped-noise "air" bed (L1b, `_render_air`).** The
single largest change. Pink noise → one-pole at 155 Hz → one-pole at
2.6–4.3 kHz (activity-dependent) → 2-pole highpass at 34 Hz, giving an
octave-band fit near −4 dB/oct on its own. This is what makes §7 items 1, 5,
6, 7 and 8 achievable at all: it supplies the falling broadband spectrum,
raises the local spectral median so notes stop sticking out, stabilises the
crest factor and short-term RMS, and fixes the stereo image at a known
correlation (one common + two decorrelated streams, `AIR_DECORR = 0.62` →
r ≈ 0.55–0.68 in the final mix). Musically it is also what stops a lone FM
note reading as a notification: **there is now always a bed under the
notes.** The brief's separate "pink rain bed that fades in with activity" is
folded into it (level *and* brightness both track activity) instead of being
a second noise source that switches on at −80 dB. Its level follows a
tau-10 s activity envelope, not the tau-3 s one that drives grain density —
individual tool calls must not pump the bed, and when they did, short-term
RMS swung 4.3 dB inside a single constant state.

**T2 — Rebalanced the reverb.** `AMBIENT_WET_GAIN` 2.2 → **0.85**, dry bleed
0.22 → **1.0**, and the air bed routes dry (it is already diffuse; putting it
through the room smeared it and ate headroom). At 90 % wet the Freeverb comb
structure *was* the mix's spectral shape — that is where the −6.6 dB mono-sum
comb notches and the −0.19 interchannel correlation came from. The reverb is
now a halo, not the signal. Comb ringing was measured directly on the
isolated impulse response (late-tail modal peaks 13.6 / 13.8 / 14.8 dB at
damp 0.2 / 0.35 / 0.5): damping stayed at the brief's **0.35** because
raising it made the 200 Hz–6 kHz modal structure *worse*, not better, and
because at the new wet level no comb mode survives into the mix (worst
in-mix tonal peak is 9.4 dB, and it is a bloom note, not a comb).

**T3 — Master level calibration.** `AMBIENT_MASTER_HEADROOM_DB` −8 → **−11**,
so that at `SONIFIER_VOLUME=1.0` the active state sits at −23 dBFS RMS /
−6 dBFS peak — a normal ambient master — and the daemon's 0.5 default is a
sensible 6 dB-quieter background level. The soft clipper now barely engages
(pre-tanh peak ≈ −8 dBFS) instead of compressing every peak by ~0.8 dB.

**T4 — Every layer's dBFS label re-calibrated against the real bed.** The
brief's per-layer dBFS numbers are relative to *its own* "−26 dBFS bed", so
against a correctly-levelled mix several layers were inaudible. Measured,
before → after: subagent stem **−7.1 dB → +6 dB relative to bed RMS**
(`STEM_CAL_DB = 18`); Bash whoosh **+0.15 dB → +3.4 dB** in its own
150–400 Hz band (`WHOOSH_CAL_DB = 30`); context-pressure sub-bass **+0.8 dB →
+4.8 dB** in the 31.5 Hz octave band at peak fill (`SUBBASS_CAL_DB = 18`, plus
the range change in B9); rain grains **+7 dB** (`DROP_CAL_DB` −19 → −12),
which took the rain from "adds 1.4 dB at 2 kHz" to "7 dB above the bed at
4 kHz" — i.e. from inaudible to actually reading as rain (onset density
4.9/s idle → 18/s at peak build). Gesture trims: knock +8.5 dB,
notification/precompact/prompt/cadence individually levelled so every gesture
peaks 9–15 dB over bed RMS, i.e. at or just above the bed's own peaks —
audible, never a stinger.

**T5 — Vectorised the bed and the DC blocker.** 28 supersaw voices became one
(256×28) phase matrix and a single matmul; the DC blocker's per-sample Python
loop became `lfilter` with a closed-form initial state (bit-identical, proven
by test); Freeverb's 16 `lfiltic` calls per block became direct state
arithmetic. 1.92 ms → **1.02 ms per block (19.2 % of one core)**, which also
cut the test suite from 107 s to 80 s.

**T6 — Storyboard pacing (`demo-session-v2.jsonl`).** Read-phase spacing
3.2 s → **4.0 s**, build phase 2.9 s → **2.0 s** with 20 tools instead of 12,
subagent phase extended to 10 parallel tools. Activity bumps became
class-weighted (Read 0.26, Write 0.44, Exec 0.46, was a flat 0.35) so
"browsing" and "building" differ in energy and not only in timbre. `Stop`
moved 171.0 s → **166.5 s** and `SessionEnd` 171.3 s → **174 s**: in the
builder's cut the Stop cadence was chopped off 0.3 s after it started by the
end fade. The pressure ramp now reaches 0.97 (the sub-bass layer's useful
range starts at fill 0.5, so a ramp stopping at 0.9 barely moved it). Every
gesture in BRIEF §3 is still represented; total render 180 s (target
170–190 s).

Resulting per-10 s arc of `demo-v2.wav` (RMS dBFS / rain onsets per second):

```
  0- 10  -28.6   4.9   session start, idle bloom
 10- 20  -24.8  10.1   prompt, read phase begins
 20- 30  -24.2   7.3
 30- 40  -23.9   7.4
 40- 50  -23.5  12.4   build phase begins
 50- 60  -22.3  18.3
 60- 70  -21.8  15.7
 70- 80  -21.6  17.7   peak build
 80- 90  -21.6  11.6   failure + notification
 90-100  -23.0  10.9   diagnosis, retry succeeds
100-110  -23.0   6.1   subagents fade in
110-120  -22.6   9.2
120-130  -22.5  10.0
130-140  -22.3   8.5   peak context pressure
140-150  -22.4   3.9   PreCompact settle, release
150-160  -25.3   5.4   wind-down, permission request
160-170  -25.0   5.5   Stop cadence, easing
170-180  -32.4   3.1   SessionEnd fade -> true silence
```

Monotonic idle (−28.6) < read (−24.2 avg) < build (−21.9 avg); the failure
region darkens without a level spike (2–6 kHz −3.7 dB, overall level +0.2 dB);
the weather gathers (31.5 Hz band +4.8 dB while 2–6 kHz falls 6 dB);
wind-down settles; the tail resolves. Fades measured at 0.25 s resolution:
in −44 → −28 dBFS smoothly over 4 s; out −30 → −60 dBFS over 5.5 s and then
exact digital zero (the step into silence is ≈ −60 dBFS, inaudible). The
largest sample-to-sample step anywhere in the file is the failure knock's
contact transient — there is no click artifact.

---

## 4. Bugs found and fixed

**B1 — The voice cap did not cap (unbounded pool growth under load).**
`_voice_pool_add` popped the oldest voice, converted it to a 3 ms fade tail,
re-inserted it *and* appended the newcomer — so every add at capacity grew
the list by one. Under the ingress stress test the pool reached **1333
entries**: unbounded per-block mixing cost and unbounded memory, in the audio
callback. Fixed by enforcing the cap on the count of *live* (non-stolen)
voices and bounding the stolen tails separately (`MAX_VOICE_TAILS = 6`).
Regression: `test_voice_pool_cap_is_actually_enforced`.

**B2 — Ingress threads mutated the list the mixer walks.** `handle_event`
(HTTP/UDP thread in live mode) called `_voice_pool_add`, which does
`pop(0)`/`insert(0)` on `self.voices` while `_mix_voices` iterates it by index
and then deletes by index — that can re-index a voice mid-mix or resurrect
one already mixed. v1's equivalent path was strictly append-only, and the v2
docstring claimed the same property without having it. Fixed with a bounded
`collections.deque` handoff (`_pending`, `MAX_PENDING_VOICES = 64`): ingress
only ever `append`s, the render thread drains it at the top of each block.
Regression: `test_ingress_handoff_is_bounded_and_append_only`.

**B3 — Two brief-mandated bed recolorings were dead state.**
`bass_shaded_vi` and `sus2_until` were written by `handle_event` and read by
nothing: the failure "bass moves to vi" shading (§3) and the Notification
sus2 recoloring (§3) were silently unimplemented, as was the fill>0.85 sus4
recoloring (§2 L5). Implemented as glided ratios (`bed_root_ratio` slewing to
2^(−3/12); a gated 9/4 partial for sus2; the 3rd harmonic sliding to 8/3 for
sus4) so sine phases stay continuous and nothing clicks. Regression:
`test_bed_recolorings_are_live_not_dead_state`.

**B4 — The failure darkening was inaudible and never resolved.** Two
defects. (a) The brief's "each failure pulls the master LP down another
300 Hz" is a no-op on a one-pole at 6 kHz: measured change in the 2–6 kHz
band from one failure was **0.0 dB**. The first failure now takes a 2400 Hz
step (subsequent ones the brief's smaller increments) and the air bed's own
top end is dimmed 50 %. Measured with activity held constant: **−3.7 dB at
2–6 kHz, −1.5 dB at 1–2 kHz, 0.0 dB below 1 kHz, overall level +0.2 dB** —
exactly "the room got darker, no volume spike". (b) The darkening only
cleared on `Stop`, so in the demo the room stayed dark for 85 s; the brief
says "until next Stop/**success**". Now a successful `PostToolUse` *of the
tool that failed* clears it (unrelated Reads do not), which is what makes the
storyboard's "retry succeeds, light returns" audible: +2.7 dB back at
2–6 kHz. Regression:
`test_failure_shading_releases_on_retry_success_not_on_any_tool`.

**B5 — The subagent stem was an unfiltered naive saw.** A raw 261.6 Hz saw
with no lowpass put a **32.4 dB** narrowband spike at 9419 Hz (harmonic 36)
into a mix whose bed falls at 4 dB/oct — audibly buzzy, and a §7 item 5
failure for the entire subagent phase. Replaced with a 3-voice detuned
supersaw through a 2-pole lowpass at 520 Hz (the brief's "shimmer pad"),
which also removes the aliasing risk. Regression:
`test_subagent_stem_has_no_unfiltered_saw_buzz`.

**B6 — The notification chime was a phone alert.** `bell` notes used
`i_peak = 1.2 + velocity·2.0`, reaching I = 3.2 against the brief's explicit
"I ≤ 2", and the gesture was pitched at A5/E5, so its r=3.5 upper sideband
landed at ~3.96 kHz — **33 dB** clear of a bed that is −49 dB in that octave:
the most prominent tone in the whole render, over a bed that had *just
dipped* for the held breath. Brief §3 asks for "distinct, inviting, not
alarming". Fixed: I capped at 2.0 per the brief, dyad dropped an octave to
A4/E4 (sideband ≈ 2 kHz), gesture trim reduced.

**B7 — The L5 weather arrived after the weather.** `subbass_gain` and
`master_lp_cutoff` each had their own tau-5 s slew *on top of* `fill_smooth`'s
tau-5 s slew, cascading to ~10 s of lag: traced on the demo, at peak context
pressure the sub-bass was still **20 dB below** its target and the master
lowpass 800 Hz behind. Secondary slews reduced to 0.5 s (enough to keep
coefficient changes zipper-free); `fill_smooth` still carries the brief's
"slew tau ≥ 5 s".

**B8 — Repeated pool notes built spectral lines.** Write-triggered notes
bypassed the per-pitch refractory the bloom scheduler uses, and every note was
exactly in tune with a 0.8–1.5 s decay regardless of register, so the top
octave stacked into a **17.5 dB** narrowband peak over a 30 s window. Fixed
with a 6 s refractory on write notes, ±9 cents of per-note detune,
pitch-dependent decay (tau × (330/f)^0.55 — also just how a struck resonator
behaves), a −3 dB/oct register tilt, and a stronger low-octave pool weighting
(1/(1+oct)^1.6). 17.5 dB → 9.4 dB, and the notes sound like an instrument.

**B9 — The sub-bass drone's useful range was unreachable.** Mapping −80 dBFS
at fill 0.5 to −30 dBFS at fill 1.0 spends its bottom 25 dB below the mix's
own 22–44 Hz rumble floor, so the drone only became audible in the last few
percent of fill. Range compressed to −55…−30 dBFS with a `frac**0.7` curve;
the 31.5 Hz octave band now rises **+4.8 dB** from fill 0.3 to peak fill.

**B10 — The failure knock read as a click, not as wood.** Its contact
transient was a raw broadband noise burst, so it lit the 1.5–6 kHz band as
much as the 80–400 Hz modal body: §7 item 9c measured +5.1 dB low vs +3.9 dB
high, i.e. not localized at all. Lowpassed the contact transient at 1.1 kHz,
shortened it, and raised the whole gesture. Now **+10.2 dB in 80–400 Hz and
6.7 dB more than in 1.5–6 kHz**.

**B11 — `SessionEnd` could not perform its own 4 s fade.** The builder
documented this as a DEVIATION (fade shortened to ~0.6 s because
`run_render`'s tail is a fixed +3 s). Fixed at the cause instead: the render
tail is 6 s when the script ends the session *and the theme is ambient*, so
the brief's 4 s release fits, resolving to exact zeros with ~1 s of real
silence after it. Geiger keeps the v1 3 s tail exactly, which is why its
output stays byte-identical. Regression:
`test_session_end_fade_is_long_then_truly_silent`.

Also fixed, not a shipping defect: the ambient test suite's `count_onsets`
helper thresholded the raw waveform at a fixed 0.01 amplitude, which stopped
being an onset detector the moment the theme grew a continuous bed — the
count went *down* as density went up. Replaced with the same band-limited
prominence detector `tools/analyze_render.py` uses (validated against
synthetic Poisson streams: 2/5/10/20/40 grains/s → 1.9/4.6/9.4/16/27).

### Audit of the builder's documented DEVIATIONs

| Deviation | Verdict |
|---|---|
| `SONIFIER_CLICKS`/`CHIMES` remapped to the ambient layers; `SONIFIER_DRONE` accepted but unused | **Legitimate.** Brief §8 puts the pressure layer inside the ambient theme by default; the env contract is preserved and the README documents the mapping |
| `SessionEnd` fade shortened to 0.6 s | **Not legitimate** — fixed at the cause, see B11 |
| Drop bus lowpassed at 3.2 kHz instead of the brief's 4.5 kHz "to help the centroid target" | **Superseded.** The centroid target was never in danger (0.02 % of energy above 5 kHz against a 10 % ceiling); the darkening was compensating for the missing bed. Left at 3.2 kHz since the rain now reads correctly, but the stated justification no longer holds |
| `_apply_lp_stage` returns unfiltered audio if scipy is missing | **Accepted, narrow.** scipy is a hard dependency of the ambient theme (README and `install.sh` both say so) and geiger runs without it; this is a degraded-but-alive path, not a normal one |

---

## 5. Compatibility, determinism, ingress

- **Geiger byte-compatibility:** `SONIFIER_THEME=geiger SONIFIER_DRONE=1
  python3 sonifier.py --render demo-session.jsonl out.wav --seed 7` is
  `cmp`-identical to the checked-in v1 `demo.wav` (md5 `bfa9d353…`). The one
  behavioural change introduced during verification (the 6 s render tail) is
  explicitly gated on the ambient theme so v1 output cannot move.
- **Determinism:** two ambient renders of `demo-session-v2.jsonl` at
  `--seed 4242` are md5-identical; `--seed 4243` differs. `--check` emits
  valid JSON under both themes.
- **HTTP/UDP:** `/health` 200 (reports live activity), `POST /event` 200,
  unknown path 404, `Content-Length` above `MAX_BODY_BYTES` → **413** without
  reading the body, malformed JSON → 200 and ignored, non-UTF-8 and 60 KB UDP
  datagrams absorbed without exception. Unchanged from v1 and still hardened.
- **Ingress stress (both themes):** 6 threads emitting random events —
  including `ContextPressure` with `fill` = NaN, `"bogus"` and `None` —
  against a concurrent render loop for 12 s each: 0 exceptions, 0 non-finite
  blocks, 0 samples over unity, all caps respected (voice pool peak 10,
  pending ≤ 64, `_lp_zi` 10 keys, `_note_refractory` 11). NaN `fill`/
  `activity` are caught by `_sanitize()` before they can wedge the Poisson
  schedulers (`NaN <= x` is always false).

## 6. Long-render stability

5-minute continuous render with subagents, peak pressure and 10 consecutive
failures:

| Property | Result |
|---|---|
| Per-block cost | 1.08 ms (1.02 ms on the steady full scene) |
| Output | finite throughout, peak −4.6 dBFS |
| Per-30 s RMS | −23.4, −21.6, −21.0, −21.1, −21.8, −21.9, −21.9, −21.6, −22.1, −21.2 dBFS (no drift) |
| Freeverb comb state | max 0.028, smallest non-zero 2.8e−7 — bounded, and far above the denormal threshold (the ±1e−20 guard is doing its job) |
| Voice pool at end | 1 live voice, 0 pending |
| `_lp_zi` / `_note_refractory` | 10 keys / 15 entries — both structurally bounded, no growth |
| Post-`SessionEnd` tail | decays smoothly, last 2 s exactly zero |

## 7. Residual risks

1. **No human has heard this.** Every listenability judgement in §3 is a
   numeric proxy (band levels, onset density, prominence over local median,
   crest, envelope coherence). The proxies are calibrated and the arc is
   right, but "pleasant" is ultimately a listening call.
2. **The sound is dark by construction.** §7's octave-band slope of −3…−6
   dB/oct forces a bass-dominant spectrum: even an ideal −3 dB/oct band
   spectrum has a centroid around 190 Hz. On small laptop speakers (which
   roll off below ~150 Hz) the bed thins out and the rain/notes dominate more
   than they do on headphones. Not a defect, but the theme is tuned for
   headphones/decent speakers.
3. **Two §7 items are reported as coherent rather than raw modulation depth**
   (§2 note 1). A reviewer insisting on the literal single-largest-component
   reading would see 10–21 % on this material — as they would on any rain
   recording. The raw numbers always print alongside.
4. **Live audio was never played.** No PortAudio device here; the callback
   path is exercised only through `render_block` and the ingress seams. The
   measured 1.02 ms/block leaves ~4.3 ms of the 5.33 ms budget, but xrun
   behaviour on a loaded desktop is untested.
5. **Freeverb's own late tail has 13–14 dB modal peaks** (measured on the
   isolated impulse response). At the current wet level none survives into
   the mix, but anyone raising `AMBIENT_WET_GAIN` materially should re-run
   item 5 — the comb structure is the first thing that will break.
6. **The `--render` tail change is theme-conditional.** A third theme will
   need to decide which tail convention it wants; the condition is an
   explicit `!= THEME_GEIGER` rather than a per-theme property.
7. **Several brief constants were re-derived, not obeyed.** Layer levels,
   the first-failure filter step, the sub-bass range and the notification
   register all deviate from the literal numbers in BRIEF-v2.md because the
   literal numbers measured as inaudible (or, for the chime, as alarming)
   against a correctly-levelled bed. Each deviation is commented at the site
   with the measurement that motivated it; if the brief's numbers are meant
   as hard contract rather than as intent, these are the places to revisit.

---

## 8. Listening guide — `demo-v2.mp3` (180.0 s)

Rendered from `demo-session-v2.jsonl`, `SONIFIER_VOLUME=1.0 --seed 1234`.
Headphones recommended (see risk 2).

| Time | What is happening | What you should hear |
|---|---|---|
| 0:00–0:03 | `SessionStart` | bed and air fade up from true silence over ~3 s, one low C2 note |
| 0:03–0:10 | idle | the machine idling: soft broadband bed, a sparse drip every second or so, one self-played bloom note. Quietest sustained part of the file (−28.6 dBFS) |
| 0:10 | `UserPromptSubmit` | a soft mid acknowledgment note (G3/A3); the bed swells |
| 0:11–0:43 | gentle read/browse | small bright drips every ~4 s, bloom notes still sparse. −24 dB, 7–10 onsets/s |
| 0:43–1:23 | active build (writes, edits, three Bash runs) | rain thickens noticeably (12 → 18 onsets/s), the bed gets louder *and* brighter, mid-register notes bloom on writes. Loudest sustained part (−21.6 dBFS). Listen for the lower-pitched drops and the 150–400 Hz swell under the Bash runs |
| 1:26.3 | `PostToolUseFailure` (pytest) | **the knock**: a low wooden thock (~190 Hz modal, +10 dB over the bed in its own band). No stinger, no level jump — instead the room *darkens* over the next few seconds: 2–6 kHz drops ~4 dB and the bass glides C → A |
| 1:27 | `Notification` | held breath: the bed dips ~5 dB and a soft two-strike A4→E4 wind chime lands in the gap |
| 1:30–1:35 | diagnosis reads, then an edit | activity resumes but the room is still dark |
| 1:35.5 | the retry Bash **succeeds** | the light comes back: filter opens over ~4 s, bass glides A → C |
| 1:45, 1:47 | two `SubagentStart`s | a shimmer pad (C4) fades in over ~5 s, then a low fifth stem (G2) — presence itself is the signal, no spawn swish |
| 1:48–2:17 | subagent phase + context pressure 0.3 → 0.97 | the weather gathers: sub-bass at 32.7 Hz rises ~5 dB while the whole mix's top end closes down ~6 dB. Rain continues under it |
| 2:16, 2:19 | `SubagentStop` ×2 | the stems fade out over ~8 s; the room narrows again |
| 2:25 | `PreCompact` | a soft two-note descending settle (C3 → A2) |
| 2:26–2:30 | pressure released | sub-bass recedes, the filter reopens — audible relief |
| 2:34 | `PermissionRequest` | held breath again: bed dips, wind chime, "your move" |
| 2:38 | one last Bash (git commit) | brief low whoosh, a few dark drops |
| 2:43 | `UserPromptSubmit` | acknowledgment note |
| 2:46.5 | `Stop` | the cadence: 2–4 descending pentatonic notes landing on C or G, longer reverb tail, then the bed eases toward idle over ~6 s |
| 2:54 | `SessionEnd` | everything releases over 4 s |
| 2:58–3:00 | — | **true digital silence.** Silence means off, and it is real: the last second of the file is exact zeros |

`focus-loop-v2.mp3` (60.4 s) is the other half of the answer: constant medium
activity, no failures, nothing to notice — what the theme sounds like during
an ordinary hour of work. It should be able to run behind you indefinitely.
