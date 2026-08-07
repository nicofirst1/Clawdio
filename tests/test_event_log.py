"""event_log.event_record / EventLog tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from event_log import EventLog, event_record  # noqa: E402
from classify import SESSION_EXPIRY_S, SUBAGENT_PRESENCE_DECAY_S  # noqa: E402


def test_pretooluse_read_detail_is_basename():
    rec = event_record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "tool_input": {"file_path": "/repo/src/config.py"}})
    assert rec == {"kind": "read", "label": "Read", "detail": "config.py", "session": ""}


def test_pretooluse_bash_exec_detail_is_command():
    rec = event_record({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "tool_input": {"command": "pytest -q"}})
    assert rec["kind"] == "exec"
    assert rec["detail"] == "pytest -q"


def test_pretooluse_task_is_spawn():
    rec = event_record({"hook_event_name": "PreToolUse", "tool_name": "Task",
                         "tool_input": {}})
    assert rec["kind"] == "spawn"


def test_skipped_events_return_none():
    assert event_record({"hook_event_name": "PostToolUse"}) is None
    assert event_record({"hook_event_name": "ContextPressure"}) is None
    assert event_record("not a dict") is None
    assert event_record({"tool_name": "Read"}) is None  # missing hook_event_name


def test_posttooluse_failure():
    rec = event_record({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"})
    assert rec == {"kind": "fail", "label": "Bash", "detail": "failed", "session": ""}


def test_event_record_session_is_first_six_chars():
    rec = event_record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "tool_input": {}, "session_id": "sessionABCDEF"})
    assert rec["session"] == "sessio"


def test_event_record_no_session_id_is_empty_string():
    rec = event_record({"hook_event_name": "Stop"})
    assert rec["session"] == ""


def test_static_kind_mappings():
    assert event_record({"hook_event_name": "Stop"})["kind"] == "done"
    assert event_record({"hook_event_name": "Notification"})["kind"] == "attention"
    rec = event_record({"hook_event_name": "SessionEnd"})
    assert rec["kind"] == "session"
    assert rec["label"] == "session end"


def test_eventlog_record_ignores_skipped_events():
    log = EventLog()
    log.record({"hook_event_name": "PostToolUse"})
    assert log.seq == 0
    assert log.since(0) == []


def test_eventlog_record_stamps_ids_and_time():
    log = EventLog()
    for _ in range(4):
        log.record({"hook_event_name": "Stop"})
    assert log.seq == 4
    recs = log.since(0)
    assert [r["id"] for r in recs] == [1, 2, 3, 4]
    assert all(isinstance(r["t"], float) for r in recs)


def test_eventlog_capacity_drops_oldest():
    log = EventLog(capacity=3)
    for _ in range(5):
        log.record({"hook_event_name": "Stop"})
    assert [r["id"] for r in log.since(0)] == [3, 4, 5]
    assert [r["id"] for r in log.since(4)] == [5]


def test_counts_tally_all_hooks_including_row_less_ones():
    log = EventLog()
    log.record({"hook_event_name": "PostToolUse"})  # no curated row
    log.record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {"file_path": "/a"}})  # curated row
    snap = log.snapshot()
    assert snap["counts"]["PostToolUse"] == 1
    assert snap["counts"]["PreToolUse"] == 1
    assert log.seq == 1  # only the PreToolUse produced a row


def test_skipped_event_still_bumps_counts_not_seq():
    log = EventLog()
    log.record({"hook_event_name": "ContextPressure"})
    assert log.snapshot()["counts"]["ContextPressure"] == 1
    assert log.seq == 0


def test_snapshot_shape():
    log = EventLog()
    snap = log.snapshot()
    assert set(snap.keys()) == {"events", "seq", "counts", "sessions", "agents"}
    assert isinstance(snap["events"], list)
    assert isinstance(snap["seq"], int)
    assert isinstance(snap["counts"], dict)
    assert isinstance(snap["sessions"], list)
    assert isinstance(snap["agents"], list)


def test_session_presence_appears_and_retires():
    log = EventLog()
    log.record({"hook_event_name": "SessionStart", "session_id": "s1"})
    sessions = log.snapshot()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"[:6]
    assert sessions[0]["n"] >= 1
    log.record({"hook_event_name": "SessionEnd", "session_id": "s1"})
    assert log.snapshot()["sessions"] == []


def test_session_counts_track_per_hook_for_filtering():
    log = EventLog()
    log.record({"hook_event_name": "PreToolUse", "session_id": "s1"})
    log.record({"hook_event_name": "PostToolUse", "session_id": "s1"})
    log.record({"hook_event_name": "PostToolUse", "session_id": "s1"})
    session = log.snapshot()["sessions"][0]
    assert session["counts"] == {"PreToolUse": 1, "PostToolUse": 2}


def test_agent_entries_have_no_counts_key():
    log = EventLog()
    log.record({"hook_event_name": "PreToolUse", "agent_id": "a1"})
    agent = log.snapshot()["agents"][0]
    assert "counts" not in agent


def test_two_sessions_get_distinct_idx():
    log = EventLog()
    log.record({"hook_event_name": "SessionStart", "session_id": "s1"})
    log.record({"hook_event_name": "SessionStart", "session_id": "s2"})
    sessions = {s["id"]: s["idx"] for s in log.snapshot()["sessions"]}
    assert sessions["s1"[:6]] != sessions["s2"[:6]]


def test_agent_presence_appears_and_retires():
    log = EventLog()
    log.record({"hook_event_name": "PreToolUse", "agent_id": "a1"})
    agents = log.snapshot()["agents"]
    assert len(agents) == 1
    assert agents[0]["id"] == "a1"[:6]
    log.record({"hook_event_name": "SubagentStop", "agent_id": "a1"})
    assert log.snapshot()["agents"] == []


def test_agent_event_with_session_id_keeps_session_alive():
    log = EventLog()
    log.record({"hook_event_name": "PreToolUse", "session_id": "s1", "agent_id": "a1"})
    snap = log.snapshot()
    assert any(s["id"] == "s1"[:6] for s in snap["sessions"])
    assert any(a["id"] == "a1"[:6] for a in snap["agents"])


def test_session_decays_via_injectable_now():
    log = EventLog()
    log.record({"hook_event_name": "SessionStart", "session_id": "s1"}, now=0.0)
    assert any(s["id"] == "s1"[:6] for s in log.snapshot(now=0.0)["sessions"])
    assert not any(
        s["id"] == "s1"[:6]
        for s in log.snapshot(now=SESSION_EXPIRY_S + 1)["sessions"]
    )


def test_agent_decays_via_injectable_now():
    log = EventLog()
    log.record({"hook_event_name": "PreToolUse", "agent_id": "a1"}, now=0.0)
    assert any(a["id"] == "a1"[:6] for a in log.snapshot(now=0.0)["agents"])
    assert not any(
        a["id"] == "a1"[:6]
        for a in log.snapshot(now=SUBAGENT_PRESENCE_DECAY_S + 1)["agents"]
    )
