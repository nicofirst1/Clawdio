"""Tool classification: classify(), _classify_bash_command(). Split out
of sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

from config import CLASS_READ, CLASS_EXEC, READ_BASH_CMDS, GIT_READ_SUBCOMMANDS, TOOL_CLASS

# A session whose terminal died without ever sending SessionEnd stops pinning
# live_count() after this many idle seconds; worst case after a wrong expiry
# (too short) is just the old single-session behavior for that session.
SESSION_EXPIRY_S = 1800.0


# BRIEF-v2.5 voice-slot palette: per-session (pan, pitch_offset_semitones)
# for the discrete gestures only. Allocated first-free (not by hashing the
# id) so the first N concurrent sessions land on N spread-out positions.
# Slot 0 is (0.0, 0) BY DESIGN: the first session reproduces the v2.4
# center-panned, un-transposed gesture path exactly, so a single-session
# render stays byte-identical (BRIEF-v2.5 section 2.2). 6th+ sessions share
# the last slot (section 5, slot exhaustion).
SLOT_PALETTE = (
    (0.00, 0),   # slot 0: 1st live session (center; the legacy path)
    (-0.40, -2),  # slot 1: 2nd (hard left)
    (0.40, 2),   # slot 2: 3rd (hard right)
    (-0.20, 3),  # slot 3: 4th (mid-left)
    (0.20, -3),  # slot 4: 5th and beyond (mid-right)
)
CENTER_SLOT = SLOT_PALETTE[0]


class SessionTracker:
    """Tracks which session_ids are "live" so multi-session daemons don't let
    one session's SessionEnd silence a soundscape another session is still
    using, and hands each live session a stable voice slot (BRIEF-v2.5) for
    placing its discrete gestures in the stereo field / pitch register.
    Ingress-thread-only, like the rest of a theme's event state (see
    ambient.py's threading notes) -- no locks.
    """

    def __init__(self):
        self._last_seen = {}
        self._slots = {}  # session_id -> slot index into SLOT_PALETTE

    def _assign_slot(self, session_id):
        """First-free allocation: claim the lowest palette index not in use;
        6th+ concurrent sessions share the last slot (SLOT_PALETTE[-1])."""
        used = set(self._slots.values())
        for i in range(len(SLOT_PALETTE)):
            if i not in used:
                self._slots[session_id] = i
                return
        self._slots[session_id] = len(SLOT_PALETTE) - 1

    def note(self, session_id, t):
        if not session_id:
            return
        if session_id not in self._slots:
            self._assign_slot(session_id)
        self._last_seen[session_id] = t

    def end(self, session_id):
        if not session_id:
            return
        self._last_seen.pop(session_id, None)
        self._slots.pop(session_id, None)

    def slot_of(self, session_id):
        """(pan, pitch_offset_semitones) for this session's gestures.

        A falsy or never-noted id gets the center slot (pan 0.0, 0 st) -- the
        legacy path (BRIEF-v2.5 section 5, "session_id missing on an event").
        Read once, at gesture-queue time, per the brief; the returned pan/
        offset are then baked into the queued voice.
        """
        idx = self._slots.get(session_id)
        if idx is None:
            return CENTER_SLOT
        return SLOT_PALETTE[idx]

    def live_count(self, t):
        expired = [sid for sid, last in self._last_seen.items() if t - last >= SESSION_EXPIRY_S]
        for sid in expired:
            del self._last_seen[sid]
            self._slots.pop(sid, None)
        return len(self._last_seen)


def _classify_bash_command(cmd):
    """Classify a Bash tool's shell command string into 'read' | 'exec'."""
    if not isinstance(cmd, str):
        cmd = str(cmd)
    first = cmd.strip().split()[0] if cmd.strip() else ""
    # strip a leading path component, e.g. /usr/bin/grep -> grep
    first = first.rsplit("/", 1)[-1]
    if first in READ_BASH_CMDS:
        return CLASS_READ
    if first == "git":
        parts = cmd.strip().split()
        sub = parts[1] if len(parts) > 1 else ""
        if sub in GIT_READ_SUBCOMMANDS:
            return CLASS_READ
        return CLASS_EXEC
    return CLASS_EXEC


def classify(tool_name, tool_input=None):
    """Classify a tool invocation into 'read' | 'write' | 'exec' | None.

    Returns None for tools that shouldn't produce a click at all (e.g.
    Task/Agent subagent-spawning tools -- those get the subagent chime
    instead, not a click-train timbre).
    """
    if not tool_name or not isinstance(tool_name, str):
        return None
    if tool_name in TOOL_CLASS:
        return TOOL_CLASS[tool_name]
    if tool_name == "Bash":
        cmd = tool_input.get("command") or "" if isinstance(tool_input, dict) else ""
        return _classify_bash_command(cmd)
    # Unknown tool name: fall back to exec-ish default class so it's audible.
    return CLASS_EXEC
