"""EventLog: a bounded, wall-clock-stamped feed of the events the daemon
handles, for the /viz readable log page. Pure sidecar to the audio -- it taps
the same raw hook dict the theme's handle_event sees (at the ingress boundary
in io_modes) and derives a display record with the SAME classify() the theme
uses, so the log reads like what you hear without touching the DSP.

Thread contract differs from the themes': here multiple ingress threads (UDP +
HTTP) append AND the GET /events handler thread reads, so unlike the lock-free
ingress-only theme state this needs a lock -- iterating a deque while another
thread appends can raise. The lock is uncontended in practice (events are rare)
and only guards the tiny record append / snapshot.
"""

from __future__ import annotations

import collections
import os
import threading
import time

from classify import classify, SESSION_EXPIRY_S, SUBAGENT_PRESENCE_DECAY_S

# hook_event_name -> the record we log for it. PostToolUse (success) and
# ContextPressure are intentionally absent: they make no distinct sound, so
# logging them would clutter a feed that's meant to mirror the soundscape.
# PreToolUse and PostToolUseFailure are handled specially below.
_STATIC_KIND = {
    "Stop": ("done", "done"),
    "Notification": ("attention", "needs you"),
    "PermissionRequest": ("attention", "needs you"),
    "SubagentStart": ("subagent", "subagent"),
    "SubagentStop": ("subagent", "subagent"),
    "UserPromptSubmit": ("prompt", "prompt"),
    "PreCompact": ("compact", "compact"),
    "SessionStart": ("session", "session start"),
    "SessionEnd": ("session", "session end"),
}

# Counted for the bar graph but deliberately NO log row -- too frequent to
# be readable. Every OTHER unrecognized hook falls through to a generic
# "other" row so the log stays forward-open to future Claude Code hooks.
_NO_ROW = {"PostToolUse", "ContextPressure"}


def _truncate(s: str, n: int = 64) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _detail(tool_input) -> str:
    """A short human string for the event's target: filename, command, pattern,
    subagent description -- whichever the tool_input carries."""
    if not isinstance(tool_input, dict):
        return ""
    fp = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    if fp:
        return os.path.basename(str(fp))
    for key in ("command", "pattern", "query", "url", "description", "subagent_type", "prompt"):
        val = tool_input.get(key)
        if val:
            return _truncate(str(val).strip().replace("\n", " "))
    return ""


def event_record(ev) -> dict | None:
    """Map a raw hook event to a display record, or None to skip it.

    Returned dict has {kind, label, detail, session}; id and t are stamped by
    EventLog.record. kind drives the frontend's colour/icon. session is the
    same first-6-chars shortening _live() uses for session ids, so the
    frontend can match a row to its session chip.
    """
    if not isinstance(ev, dict):
        return None
    name = ev.get("hook_event_name")
    if not isinstance(name, str):
        return None
    tool_name = ev.get("tool_name")
    tool_input = ev.get("tool_input")
    sid = ev.get("session_id")
    session = str(sid)[:6] if sid else ""

    if name == "PreToolUse":
        cls = classify(tool_name, tool_input)  # read/write/exec, or None for spawns
        kind = cls if cls is not None else "spawn"
        label = tool_name if isinstance(tool_name, str) and tool_name else kind
        return {"kind": kind, "label": label, "detail": _detail(tool_input), "session": session}
    if name == "PostToolUseFailure":
        label = tool_name if isinstance(tool_name, str) and tool_name else "fail"
        return {"kind": "fail", "label": label, "detail": "failed", "session": session}
    if name in _STATIC_KIND:
        kind, label = _STATIC_KIND[name]
        return {"kind": kind, "label": label, "detail": "", "session": session}
    if name in _NO_ROW:
        return None
    # Forward-open fallback: any hook Claude Code adds later still gets a row.
    return {"kind": "other", "label": name, "detail": _detail(tool_input), "session": session}


class EventLog:
    """Bounded ring of recent display records with a monotonic id per record,
    so the frontend can poll GET /events?since=<id> and only draw new ones."""

    def __init__(self, capacity: int = 400):
        self._buf: collections.deque = collections.deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._counts = collections.Counter()  # raw hook_event_name -> cumulative total
        self._sessions = {}  # session_id -> {"idx": int, "n": int, "last": float}
        self._agents = {}    # agent_id   -> {"idx": int, "n": int, "last": float}
        self._next_session_idx = 0
        self._next_agent_idx = 0

    def _touch(self, table, key, now, idx_attr, name):
        entry = table.get(key)
        if entry is None:
            idx = getattr(self, idx_attr)
            setattr(self, idx_attr, idx + 1)
            entry = {"idx": idx, "n": 0, "last": now, "counts": collections.Counter()}
            table[key] = entry
        entry["n"] += 1
        entry["last"] = now
        entry["counts"][name] += 1

    def _live(self, table, decay_s, now, with_counts=False):
        expired = [k for k, e in table.items() if now - e["last"] > decay_s]
        for k in expired:
            del table[k]
        survivors = sorted(table.items(), key=lambda kv: kv[1]["idx"])
        out = []
        for k, e in survivors:
            entry = {"id": str(k)[:6], "idx": e["idx"], "n": e["n"]}
            if with_counts:
                entry["counts"] = dict(e["counts"])
            out.append(entry)
        return out

    def record(self, ev, now=None) -> None:
        """Derive and store a record for ev (no-op if ev isn't worth logging).

        Counting and session/agent presence tracking happen for EVERY valid
        event (including PostToolUse/ContextPressure, which never get a
        curated row) -- that's the raw data the /viz bar graph and live
        session/agent view need.
        """
        if not isinstance(ev, dict):
            return
        name = ev.get("hook_event_name")
        if not isinstance(name, str) or not name:
            return
        now = time.time() if now is None else now

        with self._lock:
            self._counts[name] += 1

            sid = ev.get("session_id")
            if name == "SessionEnd":
                if sid:
                    self._sessions.pop(sid, None)
            elif sid:
                self._touch(self._sessions, sid, now, "_next_session_idx", name)

            aid = ev.get("agent_id")
            if name == "SubagentStop":
                if aid:
                    self._agents.pop(aid, None)
            elif aid:
                self._touch(self._agents, aid, now, "_next_agent_idx", name)

            rec = event_record(ev)
            if rec is not None:
                self._seq += 1
                rec["id"] = self._seq
                rec["t"] = now
                self._buf.append(rec)

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def since(self, since_id: int) -> list:
        """Records with id > since_id, oldest first."""
        with self._lock:
            return [r for r in self._buf if r["id"] > since_id]

    def snapshot(self, since_id: int = 0, now=None) -> dict:
        """Everything the /viz page needs in one call: new curated rows since
        since_id, the monotonic seq, cumulative raw counts per hook name, and
        the currently-live sessions/subagents (per the same presence windows
        the audio uses, SESSION_EXPIRY_S / SUBAGENT_PRESENCE_DECAY_S).

        Side effect: _live() evicts sessions/agents whose window has lapsed, so
        a snapshot prunes as it reads. Correct because `now` is a monotonic wall
        clock -- an expired id can't reappear -- but don't call with an out-of-
        order past `now` expecting evicted entries back."""
        now = time.time() if now is None else now
        with self._lock:
            return {
                "events": [r for r in self._buf if r["id"] > since_id],
                "seq": self._seq,
                "counts": dict(self._counts),
                "sessions": self._live(self._sessions, SESSION_EXPIRY_S, now, with_counts=True),
                "agents": self._live(self._agents, SUBAGENT_PRESENCE_DECAY_S, now),
            }


if __name__ == "__main__":
    # ponytail: one runnable self-check for the mapping + ring + since filter.
    log = EventLog(capacity=3)
    assert event_record({"hook_event_name": "PostToolUse"}) is None  # no-row hook, skipped
    assert event_record({"hook_event_name": "ContextPressure"}) is None  # no-row hook, skipped
    # unrecognized hooks fall through to a generic "other" row (forward-open).
    assert event_record({"hook_event_name": "TaskCreated", "session_id": "sXYZ12345"}) == {
        "kind": "other", "label": "TaskCreated", "detail": "", "session": "sXYZ12"}
    assert event_record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "tool_input": {"file_path": "/a/b/config.py"}}) == {
        "kind": "read", "label": "Read", "detail": "config.py", "session": ""}
    assert event_record({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": "pytest -q"}})["kind"] == "exec"
    assert event_record({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}) == {
        "kind": "fail", "label": "Bash", "detail": "failed", "session": ""}
    # session is the first 6 chars of session_id, matching _live()'s shortening.
    assert event_record({"hook_event_name": "PreToolUse", "session_id": "sessionABCDEF"}
                         )["session"] == "sessio"
    for i in range(5):
        log.record({"hook_event_name": "Stop"})
    assert log.seq == 5
    assert [r["id"] for r in log.since(0)] == [3, 4, 5]  # capacity 3 dropped 1,2
    assert log.since(4) == log.since(4) and [r["id"] for r in log.since(4)] == [5]

    # raw counts include PostToolUse/ContextPressure (no row) as well as
    # curated events (row); seq only moves for curated rows.
    seq_before = log.seq
    log.record({"hook_event_name": "PostToolUse"})
    assert log.seq == seq_before  # no row
    assert log.snapshot()["counts"]["PostToolUse"] == 1

    # session presence: appears on SessionStart, gone after SessionEnd.
    log.record({"hook_event_name": "SessionStart", "session_id": "s1"})
    snap = log.snapshot()
    assert any(s["id"] == "s1"[:6] for s in snap["sessions"])
    log.record({"hook_event_name": "SessionEnd", "session_id": "s1"})
    assert not any(s["id"] == "s1"[:6] for s in log.snapshot()["sessions"])

    # per-session hook counts, for the /viz bar graph session filter.
    log.record({"hook_event_name": "PreToolUse", "session_id": "s3"})
    log.record({"hook_event_name": "PostToolUse", "session_id": "s3"})
    s3 = next(s for s in log.snapshot()["sessions"] if s["id"] == "s3"[:6])
    assert s3["counts"] == {"PreToolUse": 1, "PostToolUse": 1}

    # agent presence: appears on any event carrying agent_id, gone after
    # SubagentStop. Agent entries stay lean -- no "counts" key exposed.
    log.record({"hook_event_name": "PreToolUse", "agent_id": "a1"})
    agents = log.snapshot()["agents"]
    assert any(a["id"] == "a1"[:6] for a in agents)
    assert "counts" not in agents[0]
    log.record({"hook_event_name": "SubagentStop", "agent_id": "a1"})
    assert not any(a["id"] == "a1"[:6] for a in log.snapshot()["agents"])

    # decay via injectable now: present at t0, gone after SESSION_EXPIRY_S.
    log.record({"hook_event_name": "SessionStart", "session_id": "s2"}, now=0.0)
    assert any(s["id"] == "s2"[:6] for s in log.snapshot(now=0.0)["sessions"])
    assert not any(
        s["id"] == "s2"[:6]
        for s in log.snapshot(now=SESSION_EXPIRY_S + 1)["sessions"]
    )
    print("event_log self-check ok")
