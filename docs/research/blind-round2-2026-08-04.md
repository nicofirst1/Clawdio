# Blind test round 2 — density variants (2026-08-04)

Listener: N. (round-1 listener, non-developer). Single-blind, shuffled labels, same session rendered under three mappings (`eval/blind/`, key in `answer-key.txt`).

## Results

| Rank | Label | Mapping           | Busy (1–7) | Pleasant (1–7) |
| ---- | ----- | ----------------- | ---------- | -------------- |
| 1    | Y     | v2.2 half-density | 6          | 2              |
| 2    | Z     | v2.2              | 7          | 2              |
| 3    | X     | v2                | 7          | 1              |

Free comment: **"the white noise and chaos"**

## Conclusions

1. Blind ranking contradicts the sighted "v2.2 is the worst yet" reaction — v2 ranked worst. The sighted comparison was confounded (different demo sessions, not same-session A/B). Lesson: never evaluate on sighted, different-session listens.
2. Density direction confirmed but insufficient: halving density won the ranking yet only moved busy 7→6. Density is not the dominant axis.
3. Dominant complaint is **timbre**: broadband noise content (noise-tick rain drops, air layer) reads as "white noise". Note the tension: v2.2 moved to noise ticks specifically to fix round-1's "confusing bird calls" complaint about sine chirps. v2.3 must find a third timbre — damped/pitched percussive (woodblock-ish), not chirp, not hiss.

## Decision → v2.3

- Adopt c3 half-density knobs as defaults (`DROP_MIN_GAP_S` 0.30, doubled coalesce window, 0.5× rate map).
- Rework drop timbre away from broadband noise; cut or hard-lowpass the air layer.
- Re-run this same 3-clip protocol with v2.3 in the pool.

Caveat: N=1 listener; primary user's own blind ratings still missing.
