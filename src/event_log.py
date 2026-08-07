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

from classify import classify

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

    Returned dict has {kind, label, detail}; id and t are stamped by
    EventLog.record. kind drives the frontend's colour/icon.
    """
    if not isinstance(ev, dict):
        return None
    name = ev.get("hook_event_name")
    if not isinstance(name, str):
        return None
    tool_name = ev.get("tool_name")
    tool_input = ev.get("tool_input")

    if name == "PreToolUse":
        cls = classify(tool_name, tool_input)  # read/write/exec, or None for spawns
        kind = cls if cls is not None else "spawn"
        label = tool_name if isinstance(tool_name, str) and tool_name else kind
        return {"kind": kind, "label": label, "detail": _detail(tool_input)}
    if name == "PostToolUseFailure":
        label = tool_name if isinstance(tool_name, str) and tool_name else "fail"
        return {"kind": "fail", "label": label, "detail": "failed"}
    if name in _STATIC_KIND:
        kind, label = _STATIC_KIND[name]
        return {"kind": kind, "label": label, "detail": ""}
    return None


class EventLog:
    """Bounded ring of recent display records with a monotonic id per record,
    so the frontend can poll GET /events?since=<id> and only draw new ones."""

    def __init__(self, capacity: int = 400):
        self._buf: collections.deque = collections.deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()

    def record(self, ev) -> None:
        """Derive and store a record for ev (no-op if ev isn't worth logging)."""
        rec = event_record(ev)
        if rec is None:
            return
        with self._lock:
            self._seq += 1
            rec["id"] = self._seq
            rec["t"] = time.time()
            self._buf.append(rec)

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def since(self, since_id: int) -> list:
        """Records with id > since_id, oldest first."""
        with self._lock:
            return [r for r in self._buf if r["id"] > since_id]


if __name__ == "__main__":
    # ponytail: one runnable self-check for the mapping + ring + since filter.
    log = EventLog(capacity=3)
    assert event_record({"hook_event_name": "PostToolUse"}) is None  # success skipped
    assert event_record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "tool_input": {"file_path": "/a/b/config.py"}}) == {
        "kind": "read", "label": "Read", "detail": "config.py"}
    assert event_record({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": "pytest -q"}})["kind"] == "exec"
    assert event_record({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}) == {
        "kind": "fail", "label": "Bash", "detail": "failed"}
    for i in range(5):
        log.record({"hook_event_name": "Stop"})
    assert log.seq == 5
    assert [r["id"] for r in log.since(0)] == [3, 4, 5]  # capacity 3 dropped 1,2
    assert log.since(4) == log.since(4) and [r["id"] for r in log.since(4)] == [5]
    print("event_log self-check ok")
