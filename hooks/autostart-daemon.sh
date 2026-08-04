#!/usr/bin/env bash
# autostart-daemon.sh — SessionStart hook. Checks whether the sonifier
# daemon is already answering on its HTTP health endpoint; if not, launches
# it detached (nohup + disown, stdout/stderr to a log file, no controlling
# terminal) so it outlives this short-lived hook process.
#
# Never blocks SessionStart: all work here is either near-instant (curl
# health check with a short timeout) or backgrounded (the launch itself).
set -u

PORT="${SONIFIER_PORT:-9753}"
HOST="${SONIFIER_HOST:-127.0.0.1}"

# Resolve the directory this script lives in, so it works regardless of cwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd -P)"
SONIFIER_PY="${SONIFIER_PY:-${SCRIPT_DIR}/../src/sonifier.py}"

health_url="http://${HOST}:${PORT}/health"

is_up() {
    if command -v curl >/dev/null 2>&1; then
        curl -s -m 1 -o /dev/null -w '%{http_code}' "$health_url" 2>/dev/null | grep -q '^200$'
        return $?
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$health_url" <<'PYEOF' 2>/dev/null
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=1) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PYEOF
        return $?
    fi
    # No way to check; assume not up so we attempt a (harmless, idempotent
    # from the daemon's perspective if it binds and exits on port-in-use) start.
    return 1
}

if is_up; then
    exit 0
fi

if [ ! -f "$SONIFIER_PY" ]; then
    # Daemon not installed alongside these hooks; nothing to launch.
    exit 0
fi

LOG_DIR="${SONIFIER_LOG_DIR:-${TMPDIR:-/tmp}}"
LOG_FILE="${LOG_DIR}/sonifier.log"

nohup python3 "$SONIFIER_PY" >>"$LOG_FILE" 2>&1 &
disown 2>/dev/null || true

exit 0
