"""Config: constants, env-var helpers, load_config(). Split out of
sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SAMPLE_RATE = 48000
BLOCKSIZE = 256
TAU_ACTIVITY = 3.0  # seconds, leaky integrator time constant
SLEW_TAU = 1.0  # seconds, generic one-pole parameter lag
MAX_CLICK_RATE = 25.0  # clicks/sec, combined cap

# Ingress hardening limits.
MAX_BODY_BYTES = 1 << 20  # 1 MiB cap on an HTTP POST /event body
MAX_ACTIVE_CHIMES = 24  # concurrent one-shot chimes; excess is dropped
HTTP_READ_TIMEOUT = 5.0  # seconds, per-connection socket timeout

# Tool timbre classes
CLASS_READ = "read"
CLASS_WRITE = "write"
CLASS_EXEC = "exec"

# classify() decision table: tool_name -> classification, or None for tools
# that shouldn't produce a click/drop at all (Task/Agent spawn subagent
# chimes instead). "Bash" isn't in here -- it's classified separately by
# _classify_bash_command below.
TOOL_CLASS = {
    "Read": CLASS_READ,
    "Glob": CLASS_READ,
    "Grep": CLASS_READ,
    "WebFetch": CLASS_READ,
    "WebSearch": CLASS_READ,
    "Write": CLASS_WRITE,
    "Edit": CLASS_WRITE,
    "NotebookEdit": CLASS_WRITE,
    "Task": None,
    "Agent": None,
}
READ_BASH_CMDS = {"ls", "cat", "grep", "rg", "find", "head", "tail", "jq"}
GIT_READ_SUBCOMMANDS = {"status", "log", "diff", "show", "branch", "blame"}

TIMBRE = {
    CLASS_READ: dict(center=2200.0, q=6.0, decay=0.0035, amp=0.55),
    CLASS_WRITE: dict(center=3200.0, q=8.0, decay=0.0028, amp=0.85),
    CLASS_EXEC: dict(center=1200.0, q=5.0, decay=0.0055, amp=0.75),
}

# v2: theme selection. "ambient" is the new default sound layer (see
# AmbientTheme / BRIEF-v2.md); "geiger" selects the v1 click-train sound
# (GeigerTheme) verbatim.
THEME_GEIGER = "geiger"
THEME_AMBIENT = "ambient"


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_bool_flag(name: str, default: bool) -> bool:
    """For SONIFIER_CLICKS/CHIMES/DRONE style '0 disables' flags."""
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in ("0", "false", "off", "no", "")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_theme(name: str, default: str) -> str:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    v = v.strip().lower()
    if v in (THEME_GEIGER, THEME_AMBIENT):
        return v
    # Unknown value: fall back to default rather than raising/crashing the
    # daemon over a typo in an env var.
    return default


def load_config() -> dict:
    return dict(
        port=_env_int("SONIFIER_PORT", 9753),
        volume=max(0.0, min(1.0, _env_float("SONIFIER_VOLUME", 0.5))),
        mute=_env_bool_flag("SONIFIER_MUTE", False),
        clicks=_env_bool_flag("SONIFIER_CLICKS", True),
        chimes=_env_bool_flag("SONIFIER_CHIMES", True),
        drone=_env_bool_flag("SONIFIER_DRONE", False),
        idle_exit_min=max(0.0, _env_float("SONIFIER_IDLE_EXIT_MIN", 30.0)),
        quiet=_env_bool_flag("SONIFIER_QUIET", False),
        theme=_env_theme("SONIFIER_THEME", THEME_AMBIENT),
    )

