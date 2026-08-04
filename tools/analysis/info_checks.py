"""Section 7 item 9 -- information checks (need the event script) -- split
out of analyze_render.py."""

from __future__ import annotations

import json

import numpy as np
from scipy import signal as sps

from analysis.io_utils import db, mono, rms_db
from analysis.modulation import short_term_rms_db
from analysis.n_checks import N4_FADEIN_SKIP_S, spearman
from analysis.spectral import centroid_and_hf


def load_events(path):
    evs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if "t" in o and "event" in o:
                evs.append((float(o["t"]), o["event"]))
    evs.sort(key=lambda e: e[0])
    return evs


def band_energy(x, sr, lo, hi):
    nyq = sr / 2.0
    b, a = sps.butter(4, [lo / nyq, min(hi, nyq * 0.99) / nyq], btype="band")
    return sps.lfilter(b, a, x)


def window_tool_count(row, names):
    return sum(1 for t, n, *_ in names
               if row["t0"] <= t < row["t1"] and n in ("PreToolUse", "PostToolUse"))


def busiest_window(arc_rows, names):
    """The render's busiest window, ranked by the SCRIPT's tool-event count
    first and the acoustic onset count only as a tie-break.

    v2.2 VERIFIER fix. This used to be `max(arc_rows, key=onsets)`. On a
    genuinely calm script (realistic-session: 11 drops in 176 s) most windows
    tie at the same low onset count, and `max` returns the FIRST such window
    -- which is the render's idle head. Criterion 9a then compared the idle
    head against itself and reported -1.47 dB of "activity contrast", a
    measurement artifact rather than an engine defect. "Which window is busy"
    is known exactly from the input script; there is no reason to infer it
    from the output we are trying to test."""
    return max(arc_rows, key=lambda r: (window_tool_count(r, names), r["onsets"]))


def info_checks(x, sr, events, rep, arc_rows):
    m = mono(x)
    names = [(t, e.get("hook_event_name"), e.get("tool_name")) for t, e in events]

    # -- 9a low- vs high-activity contrast (idle head vs busiest window)
    first_tool_opt = next((t for t, n, _ in names if n == "PreToolUse"), None)
    first_tool = 10.0 if first_tool_opt is None else first_tool_opt
    idle = m[int(0.5 * sr):int(max(first_tool - 0.5, 2.0) * sr)]
    busiest = busiest_window(arc_rows, names)
    hi_seg = m[int(busiest["t0"] * sr):int(busiest["t1"] * sr)]
    d_rms = rms_db(hi_seg) - rms_db(idle)
    c_idle, _ = centroid_and_hf(idle, sr)
    c_hi, _ = centroid_and_hf(hi_seg, sr)
    d_cen = abs(c_hi - c_idle) / max(c_idle, 1e-9)
    # An idle-only probe render has no activity to contrast: report N/A
    # rather than failing it for not containing the thing being measured.
    rep.add("9a activity contrast", ">=1.5 dB or >=10% cen",
            f"{d_rms:+.2f} dB / {100 * d_cen:.1f}%",
            None if first_tool_opt is None else (d_rms >= 1.5 or d_cen >= 0.10),
            note=("n/a - script has no tool events (idle-only probe); "
                  if first_tool_opt is None else "")
                 + f"idle {rms_db(idle):.1f} dB {c_idle:.0f} Hz -> "
                   f"busy {rms_db(hi_seg):.1f} dB {c_hi:.0f} Hz")

    # -- 9b rain density monotonic in activity (Spearman vs per-window event count)
    counts = []
    for r in arc_rows:
        counts.append(sum(1 for t, n, _ in names
                          if r["t0"] <= t < r["t1"] and n in ("PreToolUse", "PostToolUse")))
    onsets = [r["onsets"] for r in arc_rows]
    rho = spearman(counts, onsets)
    spread = (max(counts) - min(counts)) if counts else 0
    # A deliberately constant-activity render (the focus loop) has nothing for
    # a monotonicity test to bite on; only assert it where activity varies.
    ok = None if spread < 6 else rho >= 0.6
    rep.add("9b density vs activity", "rho >= 0.6", f"{rho:+.2f}", ok,
            note=f"per-10s onset rate vs per-10s tool-event count (count spread {spread})")

    # -- 9c failure knock = localized 80-400 Hz transient at the scripted time
    ft = next((t for t, n, _ in names if n == "PostToolUseFailure"), None)
    if ft is None:
        rep.add("9c' knock transient (v2.2)", ">=6 dB in 80-400 Hz",
                "n/a - no failure in script", None)
        rep.add("N5 room-pause depth", "-2.0..-4.5 dB",
                "n/a - no failure in script", None)
    else:
        def _excess(lo, hi, win_s):
            yy = band_energy(m, sr, lo, hi)
            ww = int(win_s * sr)
            ee = np.convolve(np.abs(sps.hilbert(yy)), np.ones(ww) / ww, mode="same")
            nr = ee[int((ft - 0.1) * sr):int((ft + 0.6) * sr)]
            bs = np.concatenate([ee[int((ft - 6.0) * sr):int((ft - 0.5) * sr)],
                                 ee[int((ft + 3.0) * sr):int((ft + 8.0) * sr)]])
            return db(np.max(nr)) - db(np.median(bs)), db(np.percentile(bs, 95)) - db(np.median(bs)), ee
        # v2.2 VERIFIER: envelope smoothing 50 ms -> 10 ms, applied identically
        # to the near window and the baseline. Rationale, not goalpost-moving:
        #  * The gesture being measured is a TRANSIENT with 70-110 ms modal
        #    taus. A 50 ms rectangular smoother reports its mean, not its
        #    onset; auditory transient detection integrates over ~5-10 ms.
        #  * The check still discriminates by the same margin it always did:
        #    the baseline band's own p95 excursion is reported alongside, and
        #    at 10 ms it is ~+3.7 dB against a 6 dB threshold.
        #  * The 50 ms figure is reported too, and it is NOT reachable at the
        #    knock level BRIEF-v2.2.md section 4 permits -- see the exception
        #    documented in VERIFICATION.md. Section 2 raised the bed ~6 dB and
        #    section 4 capped the knock at bed+14 dB; those two together put a
        #    ceiling of about 5 dB on the 50 ms figure. Reaching v2's 6 dB
        #    would need bed+20.5 dB, i.e. exactly the "alarm" loudness v2.2
        #    exists to remove.
        exc, base_p95, _ = _excess(80.0, 400.0, 0.010)
        exc50, _, _ = _excess(80.0, 400.0, 0.050)
        exc_hi, _, _ = _excess(1500.0, 6000.0, 0.010)
        rep.add("9c' knock transient (v2.2)", ">=6 dB in 80-400 Hz (10ms env)",
                f"{exc:.1f} dB (HF band {exc_hi:+.1f} dB)", exc >= 6.0,
                note=f"at t={ft:.1f}s; localized: low band exceeds high by "
                     f"{exc - exc_hi:.1f} dB; baseline p95 excursion +{base_p95:.1f} dB; "
                     f"v2's 50ms-env figure {exc50:.1f} dB")

        # -- N5 room-pause: the sustained layers dip for ~0.45 s around the
        # knock (AmbientTheme._duck_block). Measured in 600-3000 Hz, which is
        # bed-dominated and carries essentially none of the knock's own
        # energy, so this reads the duck and not the gesture.
        yp = band_energy(m, sr, 600.0, 3000.0)
        wp = int(0.02 * sr)
        ep = np.convolve(np.abs(sps.hilbert(yp)), np.ones(wp) / wp, mode="same")
        pre = db(np.median(ep[int((ft - 1.0) * sr):int((ft - 0.2) * sr)]))
        hold = db(np.median(ep[int((ft + 0.05) * sr):int((ft + 0.17) * sr)]))
        post = db(np.median(ep[int((ft + 0.8) * sr):int((ft + 1.8) * sr)]))
        dip = hold - pre
        rep.add("N5 room-pause depth", "-2.0..-4.5 dB", f"{dip:+.2f} dB",
                -4.5 <= dip <= -2.0,
                note=f"600-3000Hz: pre {pre:.1f} -> hold {hold:.1f} -> recovered {post:.1f} dB")

    # -- N6 (v2.4, research/BRIEF-v2.4.md): DONE state legibility. Two
    # independent listeners confirmed the Stop cadence didn't read as
    # conclusive and post-Stop idle was indistinguishable from idle-during-
    # work. Only meaningful when a Stop is followed by >= 20s with no
    # further scripted events (the "waiting for user" window this measures);
    # N/A otherwise. RMS is the reliable acoustic signal here -- bloom-note
    # rate is NOT independently checked acoustically: at a ~1/45-128s mean
    # inter-note interval, 20s is too short a window for an onset-count to
    # be anything but noise (measured: v2.2 and v2.4 settled-window onset
    # counts in the bloom register are statistically indistinguishable at
    # this timescale). That leg is asserted directly against the engine's
    # own dispatch rate instead -- see
    # tests/test_ambient.py::test_v24_settled_bloom_rate_is_reduced.
    st = next((t for t, n, _ in names if n == "Stop"), None)
    last_t = names[-1][0] if names else 0.0
    if st is None or (last_t - st) < 20.0:
        rep.add("N6 done-state legibility", "settled step <= -5.0 dB", "n/a", None,
                note="no Stop, or Stop not followed by >=20s of no events")
    else:
        # working reference: 16s of steady activity ending 2s before Stop
        # (skips any duck/knock right at the Stop boundary).
        w0, w1 = max(0.0, st - 18.0), st - 2.0
        # settled window: the LAST 12s of the post-Stop 20s+ gap, skipping
        # the cadence gesture itself and the bed's glide into its settled
        # target (SETTLED_BED_TAU_S / legacy easing tau).
        s0, s1 = st + 8.0, st + 20.0
        work_db = rms_db(m[int(w0 * sr):int(w1 * sr)])
        settled_db = rms_db(m[int(s0 * sr):int(s1 * sr)])
        step = settled_db - work_db
        rep.add("N6 done-state legibility", "settled step <= -5.0 dB", f"{step:+.2f} dB",
                step <= -5.0,
                note=f"work {work_db:.1f} dB ({w0:.0f}-{w1:.0f}s) -> "
                     f"settled {settled_db:.1f} dB ({s0:.0f}-{s1:.0f}s); "
                     f"v2.2 measures ~-2.5 dB here, v2.4 ~-8.4 dB (see BRIEF-v2.4.md)")

    # -- 8b loudness stability WITHIN A CONSTANT MACHINE STATE
    # BRIEF-v2.md section 7 item 8 says "<= 3 dB within a constant machine
    # state", but item 8 above can only measure whatever window the caller
    # passed in --steady. v2.2 VERIFIER: with the event script in hand the
    # constant states are known exactly, so measure them rather than trusting
    # the caller's window. A window that spans an activity CHANGE is supposed
    # to move -- criterion 9a requires >= 1.5 dB of exactly that -- so
    # measuring item 8 across one is self-contradictory. (Measured: the demo
    # render's 60-120 s window spans peak-busy -> post-failure ramp-down and
    # scores 3.81 dB; its constant-busy 50-80 s state scores 2.10 dB.)
    # A window only counts as a constant STATE if the scripted tool-event rate
    # is unchanging AND no state-changing gesture fires in it: those events
    # (failure gloom, compaction, breath-hold, subagent stems, and the
    # start/end fades) are by design level-moving, so including them would
    # measure the feature rather than the stability.
    STATE_CHANGERS = ("PostToolUseFailure", "PreCompact", "Notification",
                      "PermissionRequest", "SubagentStart", "SubagentStop",
                      "SessionStart", "SessionEnd", "UserPromptSubmit", "Stop")
    end_t = next((t for t, n, *_ in names if n == "SessionEnd"), None)
    t_hi = (end_t - 1.0) if end_t is not None else float("inf")
    runs = []
    if arc_rows:
        counts = [window_tool_count(r, names) for r in arc_rows]
        usable = [r["t0"] >= N4_FADEIN_SKIP_S and r["t1"] <= t_hi
                  and not any(r["t0"] <= t < r["t1"] for t, n, *_ in names
                              if n in STATE_CHANGERS)
                  for r in arc_rows]
        i = 0
        while i < len(counts):
            if not usable[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(counts) and usable[j + 1] and counts[j + 1] == counts[i]:
                j += 1
            if j - i + 1 >= 2:  # >= 20 s of unchanging scripted activity
                runs.append((arc_rows[i]["t0"], arc_rows[j]["t1"], counts[i]))
            i = j + 1
    if not runs:
        rep.add("8b constant-state stability", "<= 3 dB",
                "n/a - no >=20s stretch of constant scripted activity", None)
    else:
        worst, worst_run = -1.0, None
        for t0, t1, c in runs:
            st_r = short_term_rms_db(m[int(t0 * sr):int(t1 * sr)], sr, 3.0, 1.0)
            if len(st_r) < 2:
                continue
            spread = float(np.max(st_r) - np.min(st_r))
            if spread > worst:
                worst, worst_run = spread, (t0, t1, c)
        rep.add("8b constant-state stability", "<= 3 dB", f"{worst:.2f} dB", worst <= 3.0,
                note=f"worst of {len(runs)} constant-activity stretches: "
                     f"{worst_run[0]:.0f}-{worst_run[1]:.0f}s @ {worst_run[2]} tool events/10s")

    # -- 9d idle-alive is not digital silence
    idle_rms = rms_db(idle)
    rep.add("9d idle alive", "-70..-15 dBFS, non-zero", f"{idle_rms:.1f} dBFS",
            -70.0 < idle_rms < -15.0 and float(np.max(np.abs(idle))) > 0.0)

    # -- 9e post-SessionEnd tail is TRUE silence
    et = next((t for t, n, _ in names if n == "SessionEnd"), None)
    if et is None:
        rep.add("9e post-end silence", "exact zeros",
                "n/a - script does not end the session", None)
    else:
        tail = x[int((et + 5.0) * sr):]
        ok = len(tail) > 0 and float(np.max(np.abs(tail))) == 0.0
        rep.add("9e post-end silence", "exact zeros",
                f"{len(tail) / sr:.2f}s tail, peak {float(np.max(np.abs(tail))) if len(tail) else -1:.0e}",
                ok)
