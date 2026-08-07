"""event_log.event_record / EventLog tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from event_log import EventLog, event_record  # noqa: E402


def test_pretooluse_read_detail_is_basename():
    rec = event_record({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "tool_input": {"file_path": "/repo/src/config.py"}})
    assert rec == {"kind": "read", "label": "Read", "detail": "config.py"}


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
    assert rec == {"kind": "fail", "label": "Bash", "detail": "failed"}


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
