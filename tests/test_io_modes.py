"""io_modes.run_live idle-exit predicate tests. No audio device / real
server required -- the predicate is a pure function of theme state, tested
directly rather than by driving the full run_live loop (which would need to
mock sd.OutputStream, httpd, threading, etc. -- disproportionate ceremony for
a two-line boolean)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import io_modes  # noqa: E402
from ambient import AmbientTheme  # noqa: E402
from classify import SESSION_EXPIRY_S  # noqa: E402

IDLE_EXIT_S = 30.0


def test_live_session_pins_daemon_through_idle_stretch():
    # A tracked-live session must pin the daemon open through a long quiet
    # stretch inside it, even past idle_exit_s -- multi-session design
    # intent (SessionTracker docstring). Regression: the old two-branch
    # idle check only looked at last_event_t, so a session that registered
    # once and then went quiet for idle_exit_s got exited even though
    # SessionTracker still considered it live.
    state = AmbientTheme(seed=0)
    state.t = 1.0
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "session_id": "s1"})
    state.t = 1.0 + IDLE_EXIT_S * 2  # well past idle_exit_s, no further event
    assert state.sessions.live_count(state.t) == 1
    assert io_modes._should_idle_exit(state, IDLE_EXIT_S) is False


def test_all_idle_still_exits_never_started():
    # Genuinely all-idle (no event ever seen, live_count is 0 from the
    # start -- the last_event_t == 0.0 branch) must still exit on schedule.
    state = AmbientTheme(seed=0)
    state.t = IDLE_EXIT_S * 2
    assert state.sessions.live_count(state.t) == 0
    assert io_modes._should_idle_exit(state, IDLE_EXIT_S) is True


def test_all_idle_still_exits_after_session_expiry():
    # A session that was live but whose SessionTracker entry has since
    # expired (SESSION_EXPIRY_S) must not pin the daemon open forever --
    # live_count() is 0 once its own expiry window lapses, so the idle
    # exit still fires.
    state = AmbientTheme(seed=0)
    state.t = 1.0
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Read",
                         "session_id": "s1"})
    state.t = 1.0 + SESSION_EXPIRY_S + IDLE_EXIT_S * 2
    assert state.sessions.live_count(state.t) == 0
    assert io_modes._should_idle_exit(state, IDLE_EXIT_S) is True


def test_should_idle_exit_false_before_idle_window():
    # Sanity: recent activity and live_count == 0 (no session_id on the
    # event) must not exit early -- guards against a sloppy and/or
    # inversion in the predicate.
    state = AmbientTheme(seed=0)
    state.t = 1.0
    state.handle_event({"hook_event_name": "PreToolUse", "tool_name": "Read"})
    state.t = 1.0 + IDLE_EXIT_S / 2
    assert state.sessions.live_count(state.t) == 0
    assert io_modes._should_idle_exit(state, IDLE_EXIT_S) is False
