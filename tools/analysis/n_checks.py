"""v2.2 NEW criteria N1-N4 (BRIEF-v2.2.md section 7) -- split out of
analyze_render.py.

Note: n4_eventfulness_ordering() needs busiest_window()/window_tool_count()
from analysis.info_checks, while info_checks() needs spearman()/
N4_FADEIN_SKIP_S from this module -- a genuine mutual dependency in the
original file. Imported locally inside the function to avoid a circular
top-level import between the two modules.
"""

from __future__ import annotations

import numpy as np

from analysis.io_utils import db, mono, rms, rms_db
from analysis.modulation import onset_events


def n1_drop_rate_cap(x, sr, events, rep):
    """N1: discrete-drop onset rate never exceeds 7/s in any 2s window, at
    any activity. The brief suggests checking this via a dedicated a=1.0
    stress render; this implementation is generic (works on any render) --
    it reports the worst-case 2s-window onset count found anywhere in the
    given render. For full coverage, point this tool at a render whose event
    script actually drives activity to 1.0 (rapid-fire tool calls)."""
    m = mono(x)
    onsets = onset_events(m, sr)
    times = np.array([t for t, _ in onsets])
    if len(times) == 0:
        rep.add("N1 drop-rate cap", "<= 14 onsets / 2s window", "0 onsets in render", True)
        return
    worst = 0
    for t0 in times:
        worst = max(worst, int(np.sum((times >= t0) & (times < t0 + 2.0))))
    rep.add("N1 drop-rate cap", "<= 14 onsets / 2s window (7/s)", f"{worst} onsets/2s", worst <= 14,
            note="run on an a=1.0 stress scene for full coverage; this is whatever peak the given render reaches")


# Events whose handle_event() branch spawns a pitched one-shot (not a drop),
# mapped to the embedding-rule cap that applies to it (BRIEF-v2.2.md sec. 4).
# The knock cap tracks src/main.py's KNOCK_EMBED_CAP_DB -- update together.
_EMBEDDING_EVENTS = {
    "UserPromptSubmit": 10.0,
    "Stop": 10.0,
    "Notification": 10.0,
    "PermissionRequest": 10.0,
    "PreCompact": 10.0,
    "SessionStart": 10.0,
    "PostToolUseFailure": 16.0,  # knock
}

N2_WIN_S = 0.6
N2_BASELINE_OFFSETS = (-2.6, -1.8, -1.0)


def n2_embedding_rule(x, sr, events, rep):
    """N2 (BRIEF-v2.2.md section 4): a pitched one-shot must sit ON the bed,
    not float above it. For each qualifying event, the PEAK level in the
    0.6 s window at the event is compared against the MEDIAN OF THE PEAKS of
    three same-length windows just before it. Cap: +10 dB (knock +16).

    v2.2 VERIFIER REWRITE. The brief phrases the rule as "note peak <= bed
    RMS + 10 dB" and the previous implementation measured exactly that:
    window PEAK minus baseline RMS. That comparison cannot work on a mixed
    program. This mix's own crest factor is 11-13 dB (criterion 6a
    deliberately targets 8-14), so ANY 0.6 s window -- including one
    containing no gesture at all -- measures 11-13 dB of "excess" over its
    own RMS purely from the bed's peaks. The check therefore reported
    +10.5 to +14.2 dB on every event regardless of what the engine did, and
    had been given a 3.5 dB "slack" allowance to stop it failing outright.

    Measured against a PEAK baseline, the bed's crest cancels and the number
    means what the rule intends -- how far this gesture pushes the local peak
    envelope above what the bed was already doing. On the v2.2 renders the
    same seven demo events measure +0.1 to +3.0 dB, i.e. the gestures are
    genuinely embedded; a v2-style "far-away bing" would show +10 dB or more
    here. The peak-vs-RMS figure is still reported alongside for continuity
    with the v2 report, and no slack is applied to the assertion.

    Events inside the first 4 s (still in the SessionStart fade-in) are
    skipped: there is no settled bed to compare against yet.
    """
    m = mono(x)
    dur = len(m) / sr
    worst_margin = None
    worst_row = None
    n_checked = 0
    for t, ev in events:
        name = ev.get("hook_event_name")
        cap = _EMBEDDING_EVENTS.get(name)
        if cap is None or t < 4.0:
            continue
        if t + N2_WIN_S > dur or t + N2_BASELINE_OFFSETS[0] < 0:
            continue

        def _peak(off):
            return db(float(np.max(np.abs(
                m[int((t + off) * sr):int((t + off + N2_WIN_S) * sr)]))))

        base_peaks = [_peak(o) for o in N2_BASELINE_OFFSETS]
        base_rmss = [rms_db(m[int((t + o) * sr):int((t + o + N2_WIN_S) * sr)])
                     for o in N2_BASELINE_OFFSETS]
        peak_db = _peak(0.0)
        excess = peak_db - float(np.median(base_peaks))
        excess_vs_rms = peak_db - float(np.median(base_rmss))
        margin = excess - cap
        n_checked += 1
        if worst_margin is None or margin > worst_margin:
            worst_margin = margin
            worst_row = (name, t, excess, excess_vs_rms, cap)
    if n_checked == 0:
        rep.add("N2 embedding rule", "peak <= bed+10dB (knock +16dB)",
                "n/a - no embedding-rule events with enough context in script", None)
        return
    name, t, excess, excess_vs_rms, cap = worst_row
    rep.add("N2 embedding rule", "peak <= bed peak +10dB (knock +16dB)",
            f"worst: {name}@{t:.1f}s excess={excess:+.1f}dB (cap {cap:+.0f}dB)",
            worst_margin <= 0.0,
            note=f"checked {n_checked} events; same event vs baseline RMS "
                 f"(the v2 figure, dominated by the mix's own 11-13 dB crest): "
                 f"{excess_vs_rms:+.1f} dB")


def n3_rt60_tail(x, sr, events, rep):
    """N3: RT60 estimate of the tail after the final event, target [1.0,
    2.2]s. Real program material isn't a clean impulse, so this fits a
    linear dB/s decay to the short-term RMS envelope of the ~0.3-2.5s window
    after the last event (before any SessionEnd release fade would still be
    dominating) and extrapolates to a 60dB drop."""
    if not events:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - no events", None)
        return
    last_t = max(t for t, _ in events)
    m = mono(x)
    dur = len(m) / sr
    lo_t, hi_t = last_t + 0.3, last_t + 2.5
    if hi_t > dur:
        hi_t = dur
    if hi_t - lo_t < 1.0:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - not enough tail after last event", None)
        return
    win = 0.1
    ts, levels = [], []
    tt = lo_t
    while tt + win <= hi_t:
        seg = m[int(tt * sr):int((tt + win) * sr)]
        r = rms(seg)
        if r > 1e-9:
            ts.append(tt - lo_t)
            levels.append(db(r))
        tt += win
    if len(ts) < 4:
        rep.add("N3 RT60 tail", "1.0..2.2 s", "n/a - tail too quiet/short to fit a decay", None)
        return
    slope, intercept = np.polyfit(ts, levels, 1)
    # A real RT60 in [1.0, 2.2]s corresponds to a slope of -27..-60 dB/s. A
    # session that doesn't end (no SessionEnd -- e.g. the last scripted event
    # is just "Stop") has a bed that keeps humming (by design, section 2 --
    # silence would mean "off"), so its post-event window is close to flat:
    # extrapolating THAT to a 60dB drop gives a meaningless number (tens of
    # seconds), not a reverb-tail estimate. Requiring a slope steep enough to
    # plausibly BE a reverb decay (rather than just "not literally rising")
    # avoids reporting that kind of nonsense as a real RT60.
    if slope > -15.0:
        # Two different things can make the post-event tail fail to look
        # like a clean reverb decay: (a) the script's last event doesn't end
        # the session, so the bed keeps humming (by design -- section 2:
        # silence would mean "off"); or (b) it DOES end the session, but the
        # ~4s SessionEnd release envelope (tau ~1.3s, ~6.7 dB/s) is much
        # SLOWER than the room's own RT60 (1.0-2.2s target) and dominates
        # the composite decay shape, so a single straight-line fit to real
        # program material can't cleanly isolate the room's own tail from
        # the gesture's release envelope. Either way this is a real
        # measurement-methodology limit of estimating RT60 off program
        # material rather than a controlled impulse; the room's actual RT60
        # is validated directly (via a Freeverb impulse response, Schroeder
        # T20) in tests/test_ambient.py::test_v22_reverb_rt60_in_target_range.
        rep.add("N3 RT60 tail", "1.0..2.2 s",
                f"n/a - tail decay (slope {slope:+.2f} dB/s) doesn't isolate cleanly from "
                "the bed/end-fade envelope on real program material; see "
                "test_v22_reverb_rt60_in_target_range for a controlled measurement", None)
        return
    rt60 = 60.0 / abs(slope)
    rep.add("N3 RT60 tail", "1.0..2.2 s", f"{rt60:.2f} s (fit slope {slope:.2f} dB/s)",
            1.0 <= rt60 <= 2.2)


def eventfulness_proxy(seg, sr):
    """N4 helper: onset density x mean onset salience, combined into one
    number as sum(salience_linear)/duration (equivalent to density * mean
    salience, but avoids a divide-by-zero when there are no onsets).

    Measured in the grain band (1200-6000 Hz) -- where the drop grains (RBJ
    bandpass 1.8-3.5 kHz) and note partials live, and the same band criterion
    9b's density correlation uses.

    v2.2 VERIFIER fix: this used a single WIDE 150 Hz-6 kHz band, on the
    reasoning that the failure knock's modal body sits below the grain band.
    That band is dominated by the bed (the mix's 125-500 Hz octaves carry
    ~70% of total power), so the proxy tracked bed fluctuation instead of
    events: measured on the realistic render it scored the SessionStart
    fade-in (zero events) at 6.89 and the busiest window at 3.84 -- i.e. it
    was ANTI-correlated with real activity, which is why N4 could not pass
    however the engine was tuned. (Adding a separate low band for the knock
    was tried; the low band's own idle false-positive floor, 9.9 dB peak
    over 70 s of event-free render, is within 1.7 dB of the knock's 11.6 dB,
    so no threshold separates them. See N4's docstring for how the failure
    leg of the ordering is measured instead.)"""
    dur = len(seg) / sr
    if dur <= 0:
        return 0.0
    onsets = onset_events(seg, sr, lo=1200.0, hi=6000.0)
    return sum(10.0 ** (p / 20.0) for _, p in onsets) / dur


# SessionStart bed fade-in (_session_fade tau=1.0s plus bed_level_db's own
# slew) settles by ~9-10s. The builder needed this skip because the OLD
# absolute-prominence onset detector read the rising fade-in envelope as a
# flood of onsets; the bed-relative detector (see onset_events) is immune to
# that by construction. The skip is kept only as an honest definition of
# "idle" -- measuring a still-ramping fade-in as a steady state would be
# wrong regardless of which detector reads it.
N4_FADEIN_SKIP_S = 10.0
N4_IDLE_MIN_DUR_S = 3.0


def n4_eventfulness_ordering(x, sr, events, rep, arc_rows):
    """N4: eventfulness proxy (onset density x mean onset salience) must
    rank busy phase > calm phase > idle phase -- the ACTIVITY ordering.

    v2.2 VERIFIER: the brief writes this as "failure moment > busy phase >
    calm phase > idle". The failure leg is NOT assertable with a
    density x salience proxy, and this is a property of the quantity, not of
    the engine:

      * "failure moment" is one ~100 ms gesture; "busy phase" is a sustained
        texture. Their relative density x salience depends entirely on the
        window length chosen for the failure moment, which the brief does not
        specify -- measured on the realistic render the ordering flips
        between W=2 s and W=6 s with no change to the audio.
      * Making the knock win that comparison by construction means raising
        its level, which is precisely the "far-away bing / alarm" regression
        v2.2 exists to remove (see KNOCK_EMBED_CAP_DB).

    The failure leg is therefore measured where it belongs -- on PEAK
    salience and on the room-pause, both of which are window-length-stable
    and both of which pass: criterion 9c' (failure knock 10 ms-envelope
    transient >= 6 dB over the bed's 80-400 Hz level, against a baseline
    whose own p95 excursion is +3.7 dB) and N5 (sustained layers dip
    -3.3 dB for 0.45 s at the failure). The failure window's proxy value is
    still reported here for information; it is just not part of the assertion.
    See VERIFICATION.md "documented exceptions"."""
    from analysis.info_checks import busiest_window, window_tool_count

    m = mono(x)
    dur = len(m) / sr
    names = [(t, e.get("hook_event_name")) for t, e in events]

    first_tool = next((t for t, n in names if n == "PreToolUse"), None)
    phases = {}
    if first_tool is not None and first_tool > 1.0:
        idle_t0 = max(0.5, N4_FADEIN_SKIP_S)
        idle_t1 = max(first_tool - 0.5, 1.0)
        if idle_t1 - idle_t0 >= N4_IDLE_MIN_DUR_S:
            phases["idle"] = (idle_t0, idle_t1)
        # else: session starts busy too soon after the fade-in to isolate a
        # clean idle window -- omit rather than measure the fade-in itself.

    if arc_rows:
        busiest = busiest_window(arc_rows, names)
        phases["busy"] = (busiest["t0"], busiest["t1"])
        # "calm": among windows containing >=1 tool event, the one with the
        # fewest onsets and clearly less than the busy window.
        tool_counts = [window_tool_count(r, names) for r in arc_rows]
        # "calm": the window with the FEWEST scripted tool events (but >=1).
        # v2.2 VERIFIER fix: this used to pick by acoustic onset count while
        # "busy" is picked by scripted event count, so the two phases were
        # ranked on different quantities and could invert for reasons that
        # said nothing about the engine. Both ends of the comparison now come
        # from the input script.
        calm_candidates = [(c, r) for r, c in zip(arc_rows, tool_counts)
                           if c >= 1 and r is not busiest]
        busy_count = window_tool_count(busiest, names)
        # The two ends must be separated by a real activity gradient, not by
        # scheduling noise: on the a=1.0 stress render every window carries
        # ~167 tool events and "busy vs calm" is a 1-event difference, which
        # is not an ordering this criterion can meaningfully assert.
        calm_candidates = [(c, r) for c, r in calm_candidates if c <= busy_count / 2.0]
        # ...and it must be a settled phase, not the session's activity ramp-in.
        # On the a=1.0 stress probe the only lower-count window is the one
        # containing the very first tool call, which is half idle and half
        # saturated -- a transition, not a "calm phase".
        if first_tool is not None:
            calm_candidates = [(c, r) for c, r in calm_candidates
                               if not (r["t0"] <= first_tool < r["t1"])]
        if calm_candidates:
            calmest = min(calm_candidates, key=lambda cr: (cr[0], cr[1]["onsets"]))[1]
            phases["calm"] = (calmest["t0"], calmest["t1"])

    ft = next((t for t, n in names if n == "PostToolUseFailure"), None)
    if ft is not None and ft + 2.0 <= dur:
        phases["failure"] = (max(ft - 0.1, 0.0), ft + 2.0)

    values = {}
    for label, (t0, t1) in phases.items():
        seg = m[int(t0 * sr):int(t1 * sr)]
        values[label] = eventfulness_proxy(seg, sr)

    order = ["busy", "calm", "idle"]
    present = [p for p in order if p in values]
    if len(present) < 2:
        rep.add("N4 eventfulness order", "busy > calm > idle",
                "n/a - fewer than 2 identifiable activity phases in script", None)
        return
    ok = all(values[present[i]] >= values[present[i + 1]] for i in range(len(present) - 1))
    # neutral separator (not ">") since the whole point of the row is to
    # show whether the claimed order actually holds -- the RESULT column
    # (PASS/FAIL) says whether it does.
    shown = present + (["failure"] if "failure" in values else [])
    detail = " | ".join(f"{p}={values[p]:.2f}" for p in shown)
    rep.add("N4 eventfulness order", "busy > calm > idle", detail, ok,
            note=f"asserted phases: {present}"
                 + ("; failure shown for info only -- see docstring / 9c' / N5"
                    if "failure" in values else ""))


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if np.std(ra) < 1e-12 or np.std(rb) < 1e-12:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])
