# BRIEF-v2.5 — "Who Is That" (2026-08-06)

Status: implemented, pending round 6. Depends on the SessionTracker landing (v2.4.x). This brief assumes that live-session tracking already exists: a `SessionTracker` in `src/classify.py` maps `session_id` to last-seen time with a 30-minute expiry, and the SessionEnd fade-out and activity-zeroing are already gated on last-session-out (a SessionEnd from one of several live sessions no longer fades the whole room). v2.5 builds on that tracker and touches nothing it does not need to. It changes only how discrete session-scoped gestures are placed in the stereo field and pitch register; it does not touch the continuous textures, the pacing law, the timbre, or the Stop cadence melody itself.

## 1. The problem: two windows, one voice

The daemon runs one shared room. Before the SessionTracker, that was the whole design: everything blended, and there was no way to tell whose event you were hearing because the daemon discarded `session_id` outright. The tracker fixes the lifecycle (the room does not go silent while a second session is still live) but not the ambiguity. With two Claude Code windows open at once, both feeding the same port, every gesture still comes from the same place in the stereo field and the same register.

Two concrete confusions this leaves on the table:

- **Whose cadence?** Window A finishes and plays the v2.4 Stop cadence. Window B is still working. You hear "the resolved, over" gesture v2.4 built specifically to be read as conclusive, and you have no way to know which of your two sessions it belongs to. You alt-tab to the wrong one.
- **Whose permission chime?** Window B hits a `PermissionRequest` and plays the needs-you chime (`Notification`/`PermissionRequest`, the A4/E4 bell pair). It is an inviting "come look at me" gesture by design. With two sessions it invites you toward an unspecified one. The whole value of the chime, that it is a targeted "this session needs you", collapses when the target is unknowable.

The textures already answer "how busy is it, overall" well. What is missing is a cheap, always-available answer to "who". That is a per-actor problem, and the engine already has one per-actor pattern to imitate: `StemLayer` keys its subagent stem voices by `agent_id` in the `_presence` dict (`src/ambient_layers.py` ~line 1070), so two concurrent subagents fade in and out as distinct presences rather than one summed blob. v2.5 does the same thing one level up, keyed by `session_id`, and only for the discrete gestures.

## 2. Design: a stable voice slot per session, for gestures only

Each live `session_id` gets a **voice slot**: a stereo pan position plus a small pitch register offset. The slot is applied only to that session's **discrete gestures**: the failure knock (`PostToolUseFailure`), the Stop cadence (`Stop`), the needs-you chime (`Notification`/`PermissionRequest`), and the acknowledgment / prompt-submit note (`UserPromptSubmit`). Those are the four gestures a listener uses to answer "who just did something", so those are the four that carry the slot.

Everything continuous stays global and shared:

- the bed pad, the rain wash, the melodic bloom self-play, the context-pressure weather, and the one shared Freeverb room are untouched;
- they answer "how busy is the machine overall", summed across every live session, exactly as they do today;
- there is one room, and the gestures are placed **within** it.

This split is the whole design. Textures = the aggregate state. Gestures = the identity of the actor. A listener learns "my left session finished, my right session needs me" without the ambient bed ever fragmenting into per-session pads (which would be both muddy and a much larger change).

### 2.1 The slot palette

Slots come from a fixed ordered palette, allocated **first-free**, not by hashing the `session_id`. A raw hash of the id onto a pan position has a real failure mode: two sessions can hash to nearly the same pan and become indistinguishable, which is the exact thing this brief exists to prevent. First-free allocation guarantees the first N concurrent sessions land on N spread-out, deliberately-chosen positions.

`_mono_to_stereo(sig, pan)` (`src/dsp.py`) already defines `pan` in `[-1, 1]` with `0` = center, negative = left, positive = right (equal-power law). The palette is expressed directly in those units.

| slot | assigned to      | pan                 | pitch offset   |
| ---- | ---------------- | ------------------- | -------------- |
| 0    | 1st live session | `0.00` (center)     | `0` semitones  |
| 1    | 2nd live session | `-0.40` (left)      | `-2` semitones |
| 2    | 3rd live session | `+0.40` (right)     | `+2` semitones |
| 3    | 4th live session | `-0.20` (mid-left)  | `+3` semitones |
| 4    | 5th live session | `+0.20` (mid-right) | `-3` semitones |
| 5+   | 6th and beyond   | shares slot 4       | shares slot 4  |

The order is chosen so that the common cases are the maximally-distinct ones: one session is dead center (see §2.2), the second and third sit at the hard left/right positions the ear separates most easily, and only the fourth and fifth fall back to the harder-to-place mid positions. Released slots return to the pool (§2.3), so a long-lived session mix churns through the low, well-separated slots rather than climbing into the crowded ones.

### 2.2 Slot 0 is center: hard backward-compat

**The first session assigned always takes slot 0, which is `pan = 0.0` and `0` semitones.** This is not an aesthetic choice, it is a compatibility requirement. The gestures today are already center-panned: the v2.2 stereo-discipline rule pins "knock/cadence center" at `pan=0.0` (`src/ambient.py`, the knock and cadence handlers), and the needs-you chime and ack note are unpanned. Slot 0 reproduces that exactly. Therefore, with exactly one live session, every v2.5 gesture is placed and pitched identically to v2.4, and a single-session render is **byte-identical** to the v2.4 render (§4, acceptance criterion 1). v2.5 only becomes audible the moment a second session is live.

**First session stays center; it does not migrate when a second arrives.** The recommendation, and the shipped default, is that slot assignments are stable for the life of a session: session A holds slot 0 (center) even after session B claims slot 1 (left). Justification:

- **Stability of reference.** The point of a slot is that a listener learns "the center one is the session I started this morning". If the center session slid to the left the instant a second window opened, that learned association would break every time the session count changed. A voice slot is only useful if it is fixed for the session's life.
- **Compat is preserved for the common transition.** The single-session case stays byte-identical, and the moment you open the second window the change you hear is the new session appearing off to one side, not your existing session moving. The existing session's sound does not change under you.

The alternative (re-center to a symmetric spread, e.g. both sessions move to `-0.35` / `+0.35` so neither is privileged) reads better on paper as "fairness" but loses both properties above: it breaks the learned position of every running session each time the count changes, and it makes the single-to-double transition a double-move instead of a single-appearance. Rejected. If a blind round later shows listeners actively prefer the symmetric spread, it is a one-line change to the palette (make slot 0 migrate), so this is a reversible default, not a lock-in.

### 2.3 Lifecycle

- **Assign** on the first event seen from a `session_id` that has no slot. This is the same trigger point where the SessionTracker first records the id, so slot assignment hangs off tracker admission with no new bookkeeping loop.
- **Free** on `SessionEnd` for that id, or on tracker expiry (the id's last-seen crosses the 30-minute window). A freed slot returns to the pool and is the next one handed out. Freeing on expiry, not only on SessionEnd, means a window that is killed without a clean SessionEnd still eventually releases its slot instead of leaking it.

The slot table lives next to the tracker (it is keyed by the same `session_id` and shares the same admit/expire lifecycle), so there is one owner of session identity, not two.

## 3. Pitch offset, and the fallback

Each slot carries a small per-slot pitch offset (the semitone column in §2.1), applied to the **gesture voicings only**, transposing them within the existing scale the gestures already draw from. The offsets are small (`+/- 2` to `+/- 3` semitones) and symmetric so no session sounds "higher = more important"; they are a second lateralization cue stacked on top of pan, for the case where a listener's playback is mono-ish (a single speaker, one earbud) and pan alone gives them nothing.

**Explicit risk, and the fallback.** A pitch offset on a gesture is ambiguous in a way a pan offset is not. Pan is semantically empty: "the same gesture, over there" reads as "same meaning, different speaker". Pitch is not empty in this engine, the cadence lands on the tonic _on purpose_ (v2.4) precisely because listeners parse pitch relationships as meaning. A cadence transposed up two semitones might read as "a different, less resolved ending" rather than "the same ending, different session". If the blind round (round 6, §5) shows the pitch offset reading as **different meaning** instead of **different speaker**, the fallback is **pan-only**: keep the pan column of §2.1, set every pitch offset to `0`. Pan-only still distinguishes sessions in stereo, which is the primary case, and it removes the meaning-collision risk entirely. The pitch offset is the speculative half of this design and is the first thing cut if it does not earn its place.

## 4. What does NOT change, and the acceptance criteria

### What does not change

- The bed, rain, bloom, weather, and Freeverb room: untouched. No per-session textures.
- The Stop cadence melody, the knock synthesis, the chime voicing, the ack note: unchanged synthesis. v2.5 only chooses their `pan` and transposes their pitch by the slot offset; it does not rewrite any gesture.
- The pacing law, the drop timbre, the air-bed tuning: untouched (v2.3/v2.4 own those).
- The SessionTracker itself: v2.5 reads it, and shares its lifecycle, but does not change its expiry, its gating of SessionEnd, or its admission rule.

### Acceptance criteria

1. **Single-session renders are byte-identical.** A `--render` of any session where only one `session_id` ever appears produces an **MD5-identical** WAV to the v2.4 render at the same seed. This is the same regression-guard pattern `done_cadence="v22"` (v2.4) and `drop_timbre="noise"` (v2.3) use: the legacy behavior is reachable by construction (one session = slot 0 = `pan 0.0`, `0` semitones), not approximated. A test pins it (assert the MD5 of a one-session render equals the recorded v2.4 baseline hash at seed=7), matching `tests/test_ambient.py`'s existing MD5-baseline tests.
2. **A listener can lateralize which side asked.** In a two-session render where one session plays a needs-you chime and the other does not, a listener asked "which side needs you, left or right" answers correctly at **better than chance** across the round-6 clips. Same for "which side finished" (Stop cadence on one session only). This is a comprehension criterion, not a preference one, in the same spirit as v2.4's round-5 "how did it end" probe.
3. **The full battery still passes.** `tools/analyze_render.py` and `tools/complaint_checks.py` remain all-PASS on a two-session v2.5 render. In particular the v2.2 §7 stereo criteria (interchannel correlation `0.5–0.9`, 5-second-window `|L-R|` RMS `<= 1.0 dB`) must still hold: the slot pans are within the `+/- 0.40` bound, which sits inside the per-drop `+/- 0.35`-and-wider envelope the room already tolerates, but this must be measured on a real two-session render, not assumed. If a two-session gesture-heavy passage pushes the 5-second `|L-R|` RMS past `1.0 dB`, that is a finding to resolve (narrow the palette or widen the criterion with justification), not something to paper over.

## 5. Edge cases

- **Slot exhaustion (6+ sessions).** The palette holds five distinct slots. The sixth and any further concurrent session share slot 4 (`+0.20`, `-3` semitones). Six-plus simultaneous live Claude Code sessions feeding one daemon is far outside the design target (the two-window case is the whole motivation), so overflow sharing the last slot is acceptable: those sessions become mutually indistinguishable but stay distinct from slots 0-3. No error, no crash, graceful collision at the tail.
- **`session_id` missing on an event.** An event with no `session_id` (older hook payload, malformed event) gets the **center slot** (`pan 0.0`, `0` semitones), i.e. it behaves exactly as v2.4 did, with no slot lookup. This is the same defensive default the whole engine uses (an untagged event is the legacy path) and it means a missing id never allocates or leaks a slot.
- **Expiry mid-gesture.** A gesture is a queued, pre-rendered voice (the knock, the cadence sequence, the chime pair are all built at event time and queued into the bus). Its pan and pitch are baked in when it is queued. If the session expires while a multi-note cadence is still playing out, the notes already queued keep the slot's pan and pitch (they were fixed at spawn), and only a _subsequent_ event from a re-admitted id would draw a fresh slot. There is no mid-gesture pan jump, because the slot is read once, at queue time, per gesture.

## 6. Round 6: can a listener tell the sides apart?

`eval/blind/round6/` tests the two acceptance-criterion-2 comprehension questions directly, reusing the round-4/round-5 tooling and the `listening-feedback.yml` issue-form vocabulary.

- **Clips.** Paired ~40-60s clips, each rendering **two sessions interleaved** into one shared room (a two-`session_id` demo JSONL, the interleave built the same way `eval/make_clips.py` builds its scenario clips). In each clip, exactly one of the two sessions plays the gesture under test, from a known slot, and the other session works quietly without it. Slot assignment is fixed by construction so the answer key knows which side each session is on.
- **Two tasks, forced-choice.**
  - "Which side **finished**? Left / Right." (Stop cadence on one session only.)
  - "Which side **needs you**? Left / Right." (needs-you chime on one session only.) Forced-choice against a known ground truth, so the result is a straight better-than-chance test (binomial sign test, the same helper `eval/score.py` already has for A/B).
- **A pan-only control arm.** Include, for at least one pair, a hidden **pan-only** variant (every pitch offset zeroed, §3) alongside the pan+pitch variant. This is the direct data for the §3 fallback decision: if listeners do no worse (or do better, or report less "different meaning" confusion) on pan-only, the pitch offset is cut.
- **Vocabulary.** Reuse the `listening-feedback.yml` fields for the free-text and annoyance capture: "What you heard" / "What you expected" / the 1-5 annoyance scale, so round-6 free comments are directly comparable to live-session feedback issues. Not yet run with a listener.

## 7. Open questions

- **Max distinguishable sessions.** How many slots can a listener actually keep apart by ear before the palette should stop growing? The palette assumes five is already generous and overflow-shares beyond that, but the real ceiling (three? four?) is a listening question round 6 can start to answer, and it sets whether the `+/- 0.20` mid slots are worth keeping at all.
- **Should subagent stems inherit their session's pan?** `StemLayer` already pans its two stem voices (`stem_pan` = `0.3` / `-0.3`, `src/ambient_layers.py`). A subagent belongs to a session; today its stem pan is independent of that session's gesture slot. Should a subagent's stem drift toward its parent session's slot pan, so the whole session (gestures and its subagents' pads) reads from one side? It is tempting for coherence but it collides with the stem layer's own left/right split, and it pushes per-session panning into the continuous textures, which §2 deliberately keeps global. Flagged, not decided.
- **Does pan on the cadence fight its existing stereo image?** The Stop cadence is a multi-note sequence played into the Freeverb room, which already gives it a stereo image (the wet return is decorrelated). Panning the dry cadence hard to `-0.40` while its reverb tail stays room-centered may read as smeared or as two events rather than as one lateralized gesture. Needs a listen on a real two-session render; if it smears, the reverb send for a slotted gesture may need to follow the dry pan rather than staying centered.

## 8. Sources

- `docs/research/BRIEF-v2.4.md` — "State Legibility", the Stop cadence and SessionEnd behavior v2.5 places into slots; the SessionTracker (v2.4.x) v2.5 depends on.
- `docs/research/BRIEF-v2.2.md` — §5 stereo discipline (`pan=0.0` for knock/cadence, the correlation and `|L-R|` RMS criteria v2.5 must still pass).
- `src/ambient_layers.py` (`StemLayer._presence`, ~line 1070) — the per-`agent_id` actor pattern v2.5 imitates one level up, per-`session_id`.
- `src/dsp.py` (`_mono_to_stereo`) — the `pan in [-1, 1]` convention the §2.1 palette is expressed in.
- `.github/ISSUE_TEMPLATE/listening-feedback.yml` — the round-6 free-text and annoyance vocabulary.
