# v2.2 evaluation kit — listener N baseline (2026-08-04)

Source: `claude-geiger v2.2 -- listener scoring sheets.pdf` (25 pages), filled by listener N, single sitting, date 2026-08-04. Transcribed to `eval/responses/listener-N-2026-08-04.json` and scored with `.venv/bin/python eval/score.py eval/responses/listener-N-2026-08-04.json`.

This is **N=1**. Treat every number here as a baseline point, not a verdict — the purpose of this file is to give v2.3 something concrete to beat, not to declare v2.2 good or bad.

## Headline scores

| clip                          | IT   | affect | ISOpleasant | ISOeventful |
| ----------------------------- | ---- | ------ | ----------- | ----------- |
| a01_sparse_rain               | 0.50 | 0.53   | -0.03       | -0.38       |
| a02_dense_rain                | 1.00 | 0.65   | -0.43       | 0.43        |
| a03_write_notes               | 0.50 | 0.24   | -0.68       | -0.13       |
| a04_knock                     | 0.00 | 0.48   | 0.00        | 0.41        |
| a05_subagent_choir            | 0.67 | 0.47   | -0.25       | -0.50       |
| a06_pressure_weather          | 0.50 | 0.54   | 0.21        | -0.04       |
| a07_done_cadence              | 0.33 | 0.44   | N/A         | N/A         |
| a08_needs_you_chime           | 0.00 | 0.61   | 0.18        | 0.34        |
| b01_calm_success              | 0.75 | 0.45   | 0.53        | -0.53       |
| b02_busy_success              | 0.50 | 0.42   | -0.43       | 0.68        |
| b03_failure_recovery          | 0.50 | 0.63   | 0.18        | 0.57        |
| b04_busy_subagents_unresolved | 0.25 | 0.34   | -0.50       | 0.31        |

IT = information-transfer score (0-1, fraction of comprehension-probe items matching ground truth). affect = 0-1 composite (1 = most positive/pleasant). ISOpleasant/ISOeventful are the ISO 12913 circumplex projections (roughly -1..+1). a07's ISOpleasant/eventful are N/A because the "Calm" row of its ISO block is genuinely unmarked on the sheet (see Ambiguities below) — score.py has no partial-ISO fallback, so that clip's `iso` block was omitted entirely rather than guessed, and the ISO formula was skipped just for that clip (IT and affect for a07 are unaffected and still computed from the SD/ICBEN/control items).

### Mean IT by block

- Block A (vocabulary, 8 clips): mean IT ≈ 0.38 (a02 hit at 1.00, everything else partial or zero)
- Block B (scenario, 4 clips): mean IT ≈ 0.50

### Block C (pacing flip-point, sign test, n=1 so p is uninformative)

- most_frantic → c1_v2_mapping (X), most_calm → c2_v22_mapping (Z), most_informative → c3_v22_half_density (Y)
- Pairwise "rather listen all day": c2 preferred over c1; c3 preferred over c2; c3 preferred over c1 (score.py sign-test output: c1 vs c2 -1, c1 vs c3 -1, c2 vs c3 +1 — all n=1, p=1.0, not statistically meaningful on its own)

## Findings: comprehension vs. ground truth (`eval/answer-key.md`)

**The knock (a04_knock).** Listener correctly picked the "spike" contour card and correctly said something went wrong (matches ground truth: dip_recover- ish single failure event). But she called the outcome **"failed"** rather than picking up that a04 has no true resolution info (Block A clips have no outcome ground truth — this scored as a miss in the strict IT computation because ground truth outcome is `None`/not applicable, but note her instinct to read a bare failure knock as "ended badly" rather than "can't tell" is itself informative: the knock timbre reads as _terminal_, not as a mid- session blip). She marked "one thing happening," consistent with the clip being a single event. Net: she clearly heard _that_ something failed: the core "knock = bad thing happened" signal transferred. Score is 0.0 only because the contour card she picked (spike) didn't match the "dip_recover" label the answer key expects — a labeling/vocabulary mismatch between "spike" and "dip, then recovers" more than a comprehension failure; worth revisiting whether those two contour cards are visually distinct enough.

**b04_busy_subagents_unresolved — the critical unresolved-ending test.** This is the item the clip exists to test (per answer-key.md: "a listener who confidently reports 'it finished' or 'it succeeded' is a miss"). She did **not** report success — she marked outcome = "failed." That's still a miss against ground truth ("unresolved"), but it is the _safer_ kind of miss: she correctly avoided the false-confidence failure mode the test is designed to catch. She also left Q1 (activity contour) completely blank for this clip — genuinely unmarked, not a transcription gap (confirmed by pixel inspection of the PDF). That's suggestive: this is the one clip in the whole set where she didn't feel confident enough to commit to a shape, which lines up with "unresolved / can't tell" being the intended read. Onevsseveral was correct ("several") — she did hear the two subagents/concurrent threads.

**Subagent-choir "several voices" (a05_subagent_choir).** She marked onevsseveral = "one," missing the intended "second harmonic voice joining" read (ground truth = "several"). This is the one Block A item explicitly testing whether the choir layering reads as multiple voices, and it did not land for her. Contour (rising) and wrong=no were both correct.

**b01_calm_success / b02_busy_success — outcome misread as "unresolved."** Both of these clean-success clips (ends with a Stop cadence, no failures) were marked outcome = "still going / can't tell / not finished" rather than "succeeded." This is a real hole: the "wrap-up" cadence either isn't landing as conclusive, or 60-70s clips are simply running out before she's sure it's over. b01's contour (flat) was correctly identified; b02's contour (flat) was _not_ — ground truth wants "sustained medium-high throughout," she heard it as flat/steady. Combined with a07_done_cadence (the dedicated Block A "wrapping up" vocabulary clip) also being marked outcome=unresolved, there's a consistent pattern: **the resolving/Stop cadence is not read as conclusive across three separate clips.** This is the single clearest actionable finding in this dataset — worth prioritizing over pacing/timbre work for v2.3 if it holds up with more listeners.

**b03_failure_recovery — "did she catch the knock=failure, then miss the recovery?"** Contour = dip_recover: correct, she heard the darken-then-lift shape. onevsseveral = several: correct. But outcome was marked "failed," not "succeeded" (ground truth: succeeded after one failure+recovery, credited as partial per answer-key.md's own scoring note — "credit partial information transfer if the listener catches the dip but reports the ending as ambiguous"). She didn't report it as ambiguous, she reported it as a flat failure — she caught the dip but not the recovery signal. Combined with the b01/b02/a07 pattern above, this reinforces: whatever v2.2 uses to signal "it's over and it went fine" is not cutting through reliably, especially after a mid-clip failure dip.

**b04's "invent an ending" question.** She did not invent a success ending (the failure mode the brief specifically worries about) — she said "failed," and left the contour question blank rather than guess. That is a better failure mode than false-confidence, but it's still a miss on the actual ground truth (unresolved, not failed) and it cost her the contour probe too (unanswered).

## ICBEN / annoyance and affect notes

Highest annoyance-if-played-for-an-hour ratings: a06_pressure_weather (8/10) and b01_calm_success (9/10, surprising — the calmest/most-successful clip in the set scored as most annoying-if-sustained by ICBEN, worth a second look: possibly the "informative/meaningless" and "predictable/erratic" SD items pulled this one toward "boring/meaningless" territory rather than "unpleasant," and ICBEN doesn't distinguish those). Lowest annoyance: b02_busy_success (2/10). No obvious order/fatigue trend across the raw per-clip affect trajectory (0.53 → 0.65 → 0.24 → 0.48 → 0.47 → 0.54 → 0.44 → 0.61 → 0.45 → 0.42 → 0.63 → 0.34 in playback-as-listed order) beyond mild noise; a03_write_notes (0.24) and b04 (0.34) are the two lowest-affect clips.

## Block C — pacing mapping assumption

**The sheet's "Playback order used (X/Y/Z mapped to actual clip IDs)" line was left blank** (confirmed by direct inspection of page 25 — no text on the fill-in line). Per the task instructions, this report **assumes** listener N heard the round-2 blind files under the same X/Y/Z → clip mapping already established in `eval/blind/answer-key.txt` and used in `research/blind-round2-2026-08-04.md`:

- X = c1_v2_mapping (v2 original pacing law)
- Y = c3_v22_half_density (v2.2 map at half density)
- Z = c2_v22_mapping (v2.2 shipped mapping)

**This is an assumption, not a verified fact** — nothing in the PDF itself confirms which physical files X/Y/Z pointed to for this listener/session; it could have been re-randomized. If it was NOT this mapping, every Block C result in this file (most_frantic/calm/informative, all three pairwise preferences) is mislabeled and should be disregarded until the actual playback order is confirmed from the experimenter's records.

Taking the assumption at face value, her answers were **not** what answer-key.md predicts as the "expected flip-point result" (c1 most frantic, c2 ≳ c1 > c3 most informative, c1 > c2 > c3 frantic-to-calm ranking, c2 as the intended sweet spot):

- most_frantic = c1 (matches expectation)
- most_calm = **c2_v22_mapping**, not c3 (expected c3 to be calmest, since it's half-density)
- most_informative = **c3_v22_half_density**, not c2 (expected c2 to be the informative sweet spot; she found the _half-density_ variant most informative, which is the "did we overcorrect toward under-informative" probe reading the opposite direction than predicted)
- Pairwise: c3 beat both c1 and c2 in "rather listen all day," and c2 beat c1.

At face value (single listener, letter-mapping assumed) this reads as a mild vote for c3 (half-density) over the shipped c2 mapping — the opposite of what v2.2 intended to prove. Given this is exactly the axis `research/blind-round2-2026-08-04.md` already flagged as unresolved ("density direction confirmed but insufficient... timbre is the dominant complaint, not density"), this is consistent with prior signal rather than a new finding, but it's one more data point in the same direction and reinforces that v2.3 should not assume c2's density/pacing is settled.

## Ambiguities / judgment calls made while transcribing

1. **a07_done_cadence, ISO "Calm" row**: confirmed genuinely unmarked (no filled radio in the 1-5 row) via direct inspection — not a scan artifact. `iso` block omitted for this clip in the JSON (see Headline scores note).
2. **b04_busy_subagents_unresolved, Q1 (activity contour)**: confirmed genuinely unmarked — all four contour cards blank, verified via a high-resolution crop of the PDF page. Recorded as `contour: null`.
3. **Four "if yes, roughly when" free-text time fields** (a04_knock, b03_failure_recovery, b04_busy_subagents_unresolved, a08_needs_you_chime): in every case Q2 was marked "Yes" but the seconds fill-in line was left blank. Verified via pixel-level crop of each line — genuinely empty, not faint handwriting lost to scan resolution. Recorded as `wrong: "yes", wrong_time_s: null` in all four cases; score.py treats a "yes" answer with no timestamp as `wrong_ok = False` (can't verify the ±10s window), so these count against the wrong-time-window sub-item where applicable (only b03 has a ground-truth wrong_time_s to check against; a04/a08 are Block A clips with no wrong-time ground truth, and b04's ground truth `wrong` is "no" so the miss there is on the yes/no axis, not the timing).
4. **ICBEN 0-10 scale readings**: the 11-point scale has no per-circle numeric label directly adjacent to each radio button in a way that's easy to eyeball at a glance (numbers sit below, evenly spaced) — an initial pass miscounted several of these by one position. Re-verified all 12 ICBEN values via pixel-position measurement (locating the filled blue dot's x-coordinate against the 11 evenly-spaced circle centers) rather than by eye, and corrected the JSON accordingly before scoring. Final values used: a01=6, a02=5, a03=6, a04=7, a05=7, a06=8, a07=7, a08=6, b01=9, b02=2, b03=6, b04=5.
5. **Block C playback-order mapping**: see dedicated section above — this is an assumption per the task brief, not read off the sheet.
6. No other items were ambiguous; all semantic-differential (7-point) and ISO 12913 (5-point) rows had exactly one clearly-marked radio button per row, read directly under their printed column headers.

## score.py note

No bug found that blocks scoring valid, fully-specified input. The one rough edge encountered: `iso_pleasant_eventful()` indexes all 8 ISO keys directly (`iso["calm"]`, etc.) with no `.get()`/missing-key handling, so a partially-filled ISO block (a real possibility any time a listener skips one row) raises `KeyError` instead of degrading gracefully (e.g. returning `(None, None)` like the function already does for a wholly-absent `iso` block). Worked around by omitting the `iso` key entirely for the one affected clip (a07) rather than editing score.py, per task instructions. Flagging for whoever owns score.py next: consider `iso.get(k)` with a None-if-any-missing guard so single-row omissions don't crash a whole listener's scoring run.
