#!/usr/bin/env bash
# send-event.sh — fire the Claude Code hook JSON (on stdin) at the sonifier
# daemon over UDP, non-blocking and fast. Used as the "command" for every
# hook wired in settings-snippet.json.
#
# Strategy: prefer bash's /dev/udp pseudo-device (zero dependency, no
# subprocess fork of nc/python needed). Fall back to `nc -4u -w0` and then to
# a python3 one-liner if /dev/udp is unavailable (e.g. non-bash shells,
# restricted environments). Always bounded by `timeout` so a hook can never
# hang Claude Code.
#
# Env:
#   CLAUDIO_PORT   UDP port the daemon listens on (default 9753)
#   CLAUDIO_HOST   host the daemon listens on (default 127.0.0.1)
#
# Usage: some-hook-json | hooks/send-event.sh
set -u

PORT="${CLAUDIO_PORT:-9753}"
HOST="${CLAUDIO_HOST:-127.0.0.1}"

# Read at most 8KiB of stdin (hook payloads are small JSON objects; this
# caps worst-case latency/memory if something huge is piped in).
payload="$(head -c 8192)"

# Nothing to send.
[ -z "$payload" ] && exit 0

# Optional bounded-time wrapper. macOS ships no `timeout` (coreutils installs
# it as `gtimeout`); when neither exists, run the send directly. Every send
# path below is already non-blocking (/dev/udp write, `nc -w0`, one sendto),
# so the wrapper is belt-and-suspenders, not load-bearing — gating on a
# missing `timeout` silently dropped every event on macOS.
if command -v timeout >/dev/null 2>&1; then
    TO="timeout 1"
elif command -v gtimeout >/dev/null 2>&1; then
    TO="gtimeout 1"
else
    TO=""
fi

# bash /dev/udp pseudo-device path (requires bash, not sh/dash; not
# available under `set -o posix` or some restricted/minimal bash builds).
printf '%s' "$payload" | $TO bash -c "cat > /dev/udp/${HOST}/${PORT}" 2>/dev/null && exit 0

if command -v nc >/dev/null 2>&1; then
    printf '%s' "$payload" | $TO nc -4u -w0 "$HOST" "$PORT" 2>/dev/null && exit 0
fi

if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$payload" | $TO python3 -c '
import socket, sys
data = sys.stdin.buffer.read()
if data:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(data, (sys.argv[1], int(sys.argv[2])))
' "$HOST" "$PORT" 2>/dev/null && exit 0
fi

# Daemon may simply not be running yet (e.g. very first SessionStart before
# autostart completes) — never fail the hook, always exit 0.
exit 0
