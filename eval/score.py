#!/usr/bin/env python3
"""score.py -- scores listener responses collected via scoring-sheet.html,
per BRIEF-v2.2.md section 8.

Computes, per BRIEF-v2.2.md section 8's spec:
  - information-transfer score / clip  (comprehension probes vs answer-key.md)
  - affect score / clip                (semantic differential + ICBEN + control items)
  - ISOPleasant, ISOEventful           (ISO 12913 circumplex projection, exact formulas)
  - medians across listeners
  - per-listener trajectories          (score across the clip sequence, order as heard)
  - sign-test helper for Block C's A/B(/C) pacing comparison

Input format: a JSON file (or an in-memory dict, for programmatic use) of the
shape produced by transcribing eval/scoring-sheet.html sheets:

{
  "listener": "L1",
  "order": ["a01_sparse_rain", "b03_failure_recovery", ...],   # actual playback order (optional, used for trajectories)
  "clips": {
    "<clip_id>": {
      "contour": "flat" | "rising" | "spike" | "dip_recover",
      "wrong": "yes" | "no",
      "wrong_time_s": <float or null>,
      "outcome": "succeeded" | "failed" | "unresolved",
      "onevsseveral": "one" | "several",
      "sd": {"calm_frantic": 1-7, "control_overwhelm": 1-7, "pleasant_annoying": 1-7,
             "natural_mechanical": 1-7, "informative_meaningless": 1-7,
             "relaxing_stressful": 1-7, "predictable_erratic": 1-7, "hours_minutes": 1-7},
      "iso": {"pleasant":1-5, "vibrant":1-5, "eventful":1-5, "chaotic":1-5,
              "annoying":1-5, "monotonous":1-5, "uneventful":1-5, "calm":1-5},
      "icben": 0-10,
      "control": {"predict": 1-5, "outofcontrol": 1-5}
    },
    ...
  },
  "block_c": {
    "letter_to_clip": {"X": "c2_v22_mapping", "Y": "c1_v2_mapping", "Z": "c3_v22_half_density"},
    "most_frantic": "X" | "Y" | "Z",
    "most_calm": "X" | "Y" | "Z",
    "most_informative": "X" | "Y" | "Z",
    "pairs": {"XY": "left"|"right"|"notie", "YZ": ..., "XZ": ...}
  }
}

Every "sd" (semantic differential) value is on a 1-7 scale where 1 = the LEFT
pole (e.g. "Calm") and 7 = the RIGHT pole (e.g. "Frantic"), matching the
column order printed left-to-right on scoring-sheet.html.

Usage:
    python3 eval/score.py responses_listener1.json responses_listener2.json ...
    python3 eval/score.py               # no args -> runs the built-in
                                         # fabricated example and prints a report
                                         # (also serves as a self-test / round-trip check)
"""

from __future__ import annotations

import json
import os
import math
import sys
from statistics import median
from itertools import combinations


# ---------------------------------------------------------------------------
# Ground truth (mirrors eval/answer-key.md). Only fields that are objectively
# checkable from the comprehension probes are encoded here; free-text
# vocabulary identification (Block A "expected read" column) is intentionally
# NOT scored numerically -- score that qualitatively from the sheets' free
# text if needed. contour "any" means no single objectively-correct card.
# ---------------------------------------------------------------------------

GROUND_TRUTH = {
    # Block A -- vocabulary clips: outcome/onevsseveral are not meaningful
    # probes for these (too short / no real narrative), so they are marked
    # None and excluded from the information-transfer denominator.
    "a01_sparse_rain":            dict(contour="flat",   wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral=None),
    "a02_dense_rain":             dict(contour="rising",  wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral=None),
    "a03_write_notes":            dict(contour="flat",   wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral=None),
    "a04_knock":                  dict(contour="dip_recover", wrong="yes", wrong_time_s=8.0, outcome=None,    onevsseveral=None),
    "a05_subagent_choir":         dict(contour="rising",  wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral="several"),
    "a06_pressure_weather":       dict(contour="rising",  wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral=None),
    "a07_done_cadence":           dict(contour="flat",   wrong="no",  wrong_time_s=None, outcome="succeeded", onevsseveral=None),
    "a08_needs_you_chime":        dict(contour="flat",   wrong="no",  wrong_time_s=None, outcome=None,        onevsseveral=None),
    # Block B -- scenarios: fully specified, see answer-key.md.
    "b01_calm_success":           dict(contour="flat",        wrong="no",  wrong_time_s=None, outcome="succeeded",  onevsseveral="several"),
    "b02_busy_success":           dict(contour="rising",       wrong="no",  wrong_time_s=None, outcome="succeeded",  onevsseveral="several"),
    "b03_failure_recovery":       dict(contour="dip_recover",  wrong="yes", wrong_time_s=6.5,  outcome="succeeded",  onevsseveral="several"),
    "b04_busy_subagents_unresolved": dict(contour="rising",    wrong="no",  wrong_time_s=None, outcome="unresolved", onevsseveral="several"),
}

WRONG_TIME_TOLERANCE_S = 10.0


# ---------------------------------------------------------------------------
# Information-transfer score
# ---------------------------------------------------------------------------

def information_transfer_score(clip_id, response):
    """Returns (score in [0,1] or None if clip has no ground truth, detail dict)."""
    gt = GROUND_TRUTH.get(clip_id)
    if gt is None:
        return None, {}

    detail = {}
    hits = 0
    total = 0

    # 1. contour
    if gt["contour"] is not None:
        total += 1
        ok = response.get("contour") == gt["contour"]
        detail["contour"] = ok
        hits += int(ok)

    # 2. "anything wrong" (yes/no) + time window if applicable
    if gt["wrong"] is not None:
        total += 1
        wrong_ok = response.get("wrong") == gt["wrong"]
        if wrong_ok and gt["wrong"] == "yes":
            t = response.get("wrong_time_s")
            if t is not None and gt["wrong_time_s"] is not None:
                wrong_ok = abs(t - gt["wrong_time_s"]) <= WRONG_TIME_TOLERANCE_S
            else:
                wrong_ok = False
        detail["wrong"] = wrong_ok
        hits += int(wrong_ok)

    # 3. outcome
    if gt["outcome"] is not None:
        total += 1
        ok = response.get("outcome") == gt["outcome"]
        detail["outcome"] = ok
        hits += int(ok)

    # 4. one-vs-several
    if gt["onevsseveral"] is not None:
        total += 1
        ok = response.get("onevsseveral") == gt["onevsseveral"]
        detail["onevsseveral"] = ok
        hits += int(ok)

    if total == 0:
        return None, detail
    return hits / total, detail


# ---------------------------------------------------------------------------
# Affect score (semantic differential + ICBEN + control items)
# ---------------------------------------------------------------------------

# Every SD pair is stored LEFT-pole=1 .. RIGHT-pole=7. Positive-affect pole
# is on the LEFT for all 8 pairs as printed on the sheet (Calm / In control /
# Pleasant / Natural / Informative / Relaxing / Predictable / Hours-tolerable),
# so affect = mean(8 - value) rescaled to 0..1, where 1 = maximally positive.
SD_KEYS = [
    "calm_frantic", "control_overwhelm", "pleasant_annoying", "natural_mechanical",
    "informative_meaningless", "relaxing_stressful", "predictable_erratic", "hours_minutes",
]


def affect_score(response):
    """Returns a 0..1 composite affect score (1 = most positive/pleasant/tolerable)
    blending the 8 semantic-differential pairs, the (inverted) ICBEN annoyance
    item, and the (inverted) "felt out of control" control item. Returns None
    if no relevant fields are present."""
    parts = []

    sd = response.get("sd") or {}
    sd_vals = [sd[k] for k in SD_KEYS if k in sd]
    if sd_vals:
        # 1..7, left pole (positive) = 1 -> normalize so 1.0 = best
        norm = [(7 - v) / 6.0 for v in sd_vals]
        parts.append(sum(norm) / len(norm))

    icben = response.get("icben")
    if icben is not None:
        parts.append(1.0 - (icben / 10.0))

    ctrl = response.get("control") or {}
    if "outofcontrol" in ctrl:
        parts.append((5 - ctrl["outofcontrol"]) / 4.0)
    if "predict" in ctrl:
        parts.append((ctrl["predict"] - 1) / 4.0)

    if not parts:
        return None
    return sum(parts) / len(parts)


# ---------------------------------------------------------------------------
# ISO 12913 circumplex projection (exact formulas from BRIEF-v2.2.md sec 8)
# ---------------------------------------------------------------------------

ISO_NORM = 4.0 + math.sqrt(32.0)  # per brief, same normalizer for both axes


def iso_pleasant_eventful(response):
    """Returns (ISOPleasant, ISOEventful) or (None, None) if iso block absent.
    Uses the raw 1-5 ISO 12913 circumplex items (pleasant, vibrant, eventful,
    chaotic, annoying, monotonous, uneventful, calm)."""
    iso = response.get("iso")
    if not iso:
        return None, None
    p = iso["pleasant"]; v = iso["vibrant"]; e = iso["eventful"]; ch = iso["chaotic"]
    a = iso["annoying"]; m = iso["monotonous"]; u = iso["uneventful"]; ca = iso["calm"]

    iso_pleasant = ((p - a) + 0.707 * (ca - ch) + 0.707 * (v - m)) / ISO_NORM
    iso_eventful = ((e - u) + 0.707 * (ch - ca) + 0.707 * (v - m)) / ISO_NORM
    return iso_pleasant, iso_eventful


# ---------------------------------------------------------------------------
# Per-listener / cross-listener aggregation
# ---------------------------------------------------------------------------

def score_listener(payload):
    """Scores one listener's full response payload (see module docstring for
    shape). Returns a dict: {clip_id: {"it": .., "affect": .., "iso_p": ..,
    "iso_e": .., "it_detail": {...}}}."""
    out = {}
    for clip_id, resp in (payload.get("clips") or {}).items():
        it, it_detail = information_transfer_score(clip_id, resp)
        aff = affect_score(resp)
        iso_p, iso_e = iso_pleasant_eventful(resp)
        out[clip_id] = {
            "it": it, "it_detail": it_detail,
            "affect": aff,
            "iso_pleasant": iso_p, "iso_eventful": iso_e,
        }
    return out


def cross_listener_medians(scored_by_listener):
    """scored_by_listener: {listener_id: score_listener(...) output}.
    Returns {clip_id: {"it": median or None, "affect": median, "iso_pleasant": median, "iso_eventful": median, "n": count}}."""
    clip_ids = set()
    for scores in scored_by_listener.values():
        clip_ids.update(scores.keys())

    out = {}
    for clip_id in sorted(clip_ids):
        row = {}
        for field in ("it", "affect", "iso_pleasant", "iso_eventful"):
            vals = [scores[clip_id][field] for scores in scored_by_listener.values()
                     if clip_id in scores and scores[clip_id][field] is not None]
            row[field] = median(vals) if vals else None
            row[field + "_n"] = len(vals)
        out[clip_id] = row
    return out


def listener_trajectory(payload, scored):
    """Returns [(clip_id, affect_score), ...] in the listener's ACTUAL
    playback order (payload["order"]) if given, else dict insertion order.
    Useful for spotting fatigue/order effects (e.g. affect drifting down
    over a long session)."""
    order = payload.get("order") or list((payload.get("clips") or {}).keys())
    traj = []
    for clip_id in order:
        if clip_id in scored:
            traj.append((clip_id, scored[clip_id]["affect"]))
    return traj


# ---------------------------------------------------------------------------
# Sign test (Block C A/B/C pacing comparison, and general paired use)
# ---------------------------------------------------------------------------

def sign_test(pairs):
    """pairs: iterable of (a_value, b_value) numeric scores for the SAME
    listener on two conditions (e.g. affect score on c1 vs c2), OR an
    iterable of +1/-1/0 preference votes (already-signed). Ties (a==b or 0)
    are dropped per the standard sign test. Returns dict with n, n_plus,
    n_minus, and a two-sided exact binomial p-value under H0: p=0.5."""
    signs = []
    for pair in pairs:
        if isinstance(pair, tuple):
            a, b = pair
            d = a - b
        else:
            d = pair
        if d > 0:
            signs.append(1)
        elif d < 0:
            signs.append(-1)
        # d == 0 -> tie, dropped

    n = len(signs)
    n_plus = sum(1 for s in signs if s > 0)
    n_minus = n - n_plus

    if n == 0:
        return {"n": 0, "n_plus": 0, "n_minus": 0, "p_value": None}

    k = min(n_plus, n_minus)
    # two-sided exact binomial p-value, p=0.5
    def binom_pmf(k, n):
        return math.comb(n, k) / (2 ** n)

    p_one_tail = sum(binom_pmf(i, n) for i in range(0, k + 1))
    p_value = min(1.0, 2 * p_one_tail)

    return {"n": n, "n_plus": n_plus, "n_minus": n_minus, "p_value": p_value}


def block_c_letter_votes_to_clip_votes(block_c):
    """Maps a listener's Block C letter-based votes (X/Y/Z) back to real clip
    ids using letter_to_clip, so results can be pooled across listeners even
    though each listener saw a different randomized letter assignment."""
    l2c = block_c.get("letter_to_clip", {})
    out = {}
    for key in ("most_frantic", "most_calm", "most_informative"):
        letter = block_c.get(key)
        out[key] = l2c.get(letter, letter)
    pairs_out = {}
    for pair_key, winner_side in (block_c.get("pairs") or {}).items():
        # pair_key like "XY": left=X, right=Y
        left_letter, right_letter = pair_key[0], pair_key[1]
        if winner_side == "left":
            winner_clip = l2c.get(left_letter, left_letter)
        elif winner_side == "right":
            winner_clip = l2c.get(right_letter, right_letter)
        else:
            winner_clip = None  # notie
        pairs_out[(l2c.get(left_letter, left_letter), l2c.get(right_letter, right_letter))] = winner_clip
    return out, pairs_out


def block_c_preference_sign(all_block_c, clip_a, clip_b):
    """Given a list of per-listener block_c dicts, builds a sign_test-ready
    list of +1 (clip_a preferred) / -1 (clip_b preferred) / 0 (tie/dropped)
    votes for the pair (clip_a, clip_b), pooling across whichever
    letter-permutation each listener actually saw."""
    signs = []
    for block_c in all_block_c:
        _, pairs_out = block_c_letter_votes_to_clip_votes(block_c)
        for (left, right), winner in pairs_out.items():
            if {left, right} != {clip_a, clip_b}:
                continue
            if winner == clip_a:
                signs.append(1)
            elif winner == clip_b:
                signs.append(-1)
            else:
                signs.append(0)
    return signs


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def fmt(x, nd=2):
    return "  N/A" if x is None else f"{x:+.{nd}f}" if isinstance(x, float) and x < 0 else f"{x:.{nd}f}"


def print_report(scored_by_listener, all_block_c=None):
    medians = cross_listener_medians(scored_by_listener)
    print(f"Listeners: {len(scored_by_listener)} ({', '.join(sorted(scored_by_listener))})")
    print()
    header = f"{'clip':32s} {'IT':>6s} {'affect':>7s} {'ISOpl':>7s} {'ISOev':>7s}  n"
    print(header)
    print("-" * len(header))
    for clip_id, row in sorted(medians.items()):
        print(f"{clip_id:32s} {fmt(row['it']):>6s} {fmt(row['affect']):>7s} "
              f"{fmt(row['iso_pleasant']):>7s} {fmt(row['iso_eventful']):>7s}  {row['affect_n']}")

    print()
    # (Per-listener trajectories need the RAW payloads, not the scored ones,
    # to recover playback order -- main() prints them. v2.2 VERIFIER: this
    # used to print the section header here and then an empty `pass` loop,
    # so every report showed the heading twice with nothing under the first.)

    if all_block_c:
        print()
        print("Block C pairwise sign tests:")
        clip_ids = sorted({c for bc in all_block_c for c in bc.get("letter_to_clip", {}).values()})
        for a, b in combinations(clip_ids, 2):
            signs = block_c_preference_sign(all_block_c, a, b)
            res = sign_test(signs)
            print(f"  {a:24s} vs {b:24s}  n={res['n']:>2d}  "
                  f"+{res['n_plus']}/-{res['n_minus']}  p={fmt(res['p_value'], 3) if res['p_value'] is not None else 'N/A'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fabricated_example():
    """A small, internally-consistent fabricated response set (3 listeners)
    used both as usage documentation and as a round-trip self-test: run this
    file with no arguments and it scores these in-memory payloads and prints
    the same report a real `responses_*.json` set would produce."""

    def listener(lid, order, b01_good=True, b03_ok=True, b04_ok=True, c_pref=("c2", "c2", "c3")):
        clips = {}

        def clip(cid, contour, wrong, wrong_time_s, outcome, onevsseveral,
                  sd_bias, iso_bias, icben, predict, outofcontrol):
            sd = {
                "calm_frantic": sd_bias, "control_overwhelm": sd_bias, "pleasant_annoying": sd_bias,
                "natural_mechanical": sd_bias + 1, "informative_meaningless": sd_bias,
                "relaxing_stressful": sd_bias, "predictable_erratic": sd_bias, "hours_minutes": sd_bias,
            }
            iso = {
                "pleasant": iso_bias, "vibrant": 3, "eventful": 3, "chaotic": 6 - iso_bias,
                "annoying": 6 - iso_bias, "monotonous": 3, "uneventful": 3, "calm": iso_bias,
            }
            clips[cid] = dict(contour=contour, wrong=wrong, wrong_time_s=wrong_time_s,
                               outcome=outcome, onevsseveral=onevsseveral, sd=sd, iso=iso,
                               icben=icben, control={"predict": predict, "outofcontrol": outofcontrol})

        clip("a01_sparse_rain", "flat", "no", None, None, None, 2, 4, 1, 4, 1)
        clip("a04_knock", "dip_recover", "yes", 7.5 if b03_ok else 30.0, None, None, 3, 3, 3, 3, 2)
        clip("b01_calm_success", "flat" if b01_good else "spike", "no", None,
             "succeeded" if b01_good else "failed", "several", 2, 4, 1, 4, 1)
        clip("b03_failure_recovery", "dip_recover" if b03_ok else "flat",
             "yes" if b03_ok else "no", 8.0 if b03_ok else None,
             "succeeded", "several", 3, 3, 3, 3, 2)
        clip("b04_busy_subagents_unresolved", "rising", "no", None,
             "unresolved" if b04_ok else "succeeded", "several", 3, 3, 4, 2, 3)

        letter_to_clip = {"X": "c2_v22_mapping", "Y": "c1_v2_mapping", "Z": "c3_v22_half_density"}
        clip_to_letter = {v: k for k, v in letter_to_clip.items()}
        frantic_clip = "c1_v2_mapping"  # the honest expectation: v2's mapping reads most frantic
        calm_clip = c_pref[1] + "_v22_half_density" if c_pref[1] == "c3" else "c2_v22_mapping"
        block_c = {
            "letter_to_clip": letter_to_clip,
            "most_frantic": clip_to_letter[frantic_clip],
            "most_calm": clip_to_letter.get(calm_clip, "Z"),
            "most_informative": clip_to_letter["c2_v22_mapping"],
            "pairs": {
                "XY": "left",   # X=c2 preferred over Y=c1
                "YZ": "right",  # Z=c3 preferred over Y=c1
                "XZ": "left" if c_pref[0] == "c2" else "right",
            },
        }
        return {"listener": lid, "order": order, "clips": clips, "block_c": block_c}

    listeners = [
        listener("L1", ["a01_sparse_rain", "a04_knock", "b01_calm_success",
                         "b03_failure_recovery", "b04_busy_subagents_unresolved"]),
        listener("L2", ["b01_calm_success", "a01_sparse_rain", "b04_busy_subagents_unresolved",
                         "a04_knock", "b03_failure_recovery"], c_pref=("c2", "c3", "c2")),
        listener("L3", ["a04_knock", "b03_failure_recovery", "a01_sparse_rain",
                         "b01_calm_success", "b04_busy_subagents_unresolved"],
                 b03_ok=False, c_pref=("c3", "c3", "c3")),
    ]
    return listeners


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        print("usage: score.py [--example | RESPONSES.json ...]")
        return 0

    # v2.2 VERIFIER: --example used to be undocumented AND unrecognised -- it
    # fell through to open("--example") and died with a FileNotFoundError
    # traceback, as did any mistyped flag. Accept it explicitly and fail
    # cleanly on a missing file.
    if not argv or argv == ["--example"]:
        print("# running built-in fabricated example (round-trip self-test)\n")
        payloads = _fabricated_example()
    else:
        payloads = []
        for path in argv:
            if path.startswith("-"):
                print(f"score.py: unknown option {path!r} "
                      f"(try --example, or a responses .json path)", file=sys.stderr)
                return 2
            if not os.path.exists(path):
                print(f"score.py: no such responses file: {path}", file=sys.stderr)
                return 2
            with open(path) as f:
                payloads.append(json.load(f))

    scored_by_listener = {}
    all_block_c = []
    for payload in payloads:
        lid = payload.get("listener", f"listener{len(scored_by_listener) + 1}")
        scored_by_listener[lid] = score_listener(payload)
        if "block_c" in payload:
            all_block_c.append(payload["block_c"])

    print_report(scored_by_listener, all_block_c=all_block_c)

    print()
    print("Per-listener trajectories (clip: affect score, in playback order):")
    for payload in payloads:
        lid = payload.get("listener", "?")
        traj = listener_trajectory(payload, scored_by_listener[lid])
        traj_str = "  ->  ".join(f"{cid}:{fmt(v)}" for cid, v in traj)
        print(f"  {lid}: {traj_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
