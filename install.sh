#!/usr/bin/env bash
# install.sh — idempotent installer for claude-geiger (agent-sonifier).
#
# What it does:
#   1. Checks for python3 and numpy (required), notes sounddevice as
#      optional (needed only for live audio playback, not --render).
#   2. Uses the project files in place (this repo IS the install; nothing
#      is copied elsewhere) and makes the hook scripts executable.
#   3. Prints merge instructions for hooks/settings-snippet.json into
#      ~/.claude/settings.json, or -- if `jq` is present -- offers to merge
#      it automatically, ALWAYS backing up the existing file to
#      settings.json.bak.<timestamp> first, never overwriting blind.
#
# Flags:
#   --dry-run        Print what would happen; make no changes anywhere.
#   --yes / -y        Non-interactive: assume "yes" to the merge prompt.
#   --no-merge         Never touch ~/.claude/settings.json, just print
#                      instructions.
#   --claude-dir DIR  Override the Claude config dir (default: ~/.claude).
#                      Mainly for testing: install.sh --claude-dir
#                      /tmp/fakehome/.claude
#
# Safe to re-run: merging is idempotent (keyed by our hook commands; running
# twice does not duplicate entries) and file copies are skip-if-unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

DRY_RUN=0
ASSUME_YES=0
NO_MERGE=0
CLAUDE_DIR="${CLAUDE_DIR:-${HOME:-.}/.claude}"

usage() {
    cat <<EOF
Usage: install.sh [--dry-run] [--yes|-y] [--no-merge] [--claude-dir DIR]

  --dry-run          Show what would happen; make no changes.
  --yes, -y          Non-interactive: assume yes for the settings merge.
  --no-merge         Never touch ~/.claude/settings.json; print instructions only.
  --claude-dir DIR   Claude config directory (default: \$HOME/.claude).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --no-merge) NO_MERGE=1 ;;
        --claude-dir) CLAUDE_DIR="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

say() { printf '%s\n' "$*"; }
run() {
    # Execute a state-changing command unless --dry-run.
    if [ "$DRY_RUN" -eq 1 ]; then
        say "  [dry-run] $*"
    else
        "$@"
    fi
}

say "== claude-geiger installer =="
say "project dir: $SCRIPT_DIR"
say "claude dir:  $CLAUDE_DIR"
[ "$DRY_RUN" -eq 1 ] && say "(--dry-run: no changes will be made)"
say ""

# ---------------------------------------------------------------------
# 1. Dependency checks
# ---------------------------------------------------------------------

say "-- checking dependencies --"

if ! command -v python3 >/dev/null 2>&1; then
    say "ERROR: python3 not found on PATH. Install python3 >= 3.10 and re-run." >&2
    exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "  python3: OK ($PY_VER)"

if python3 -c 'import numpy' >/dev/null 2>&1; then
    say "  numpy: OK"
else
    say "  numpy: MISSING (required by sonifier.py, including offline --render)."
    say "         Install with: pip install numpy --break-system-packages"
    say "         (or: pip install numpy   inside a venv)"
fi

if python3 -c 'import scipy' >/dev/null 2>&1; then
    say "  scipy: OK"
else
    say "  scipy: MISSING (required by the default AmbientTheme sound -- v2's"
    say "         Freeverb/bed/rain filtering. GeigerTheme, SONIFIER_THEME=geiger,"
    say "         still runs without it, but ambient will sound degraded/unfiltered.)"
    say "         Install with: pip install scipy --break-system-packages"
    say "         (or: pip install scipy   inside a venv)"
fi

if python3 -c 'import sounddevice' >/dev/null 2>&1; then
    say "  sounddevice: OK (live audio playback available)"
else
    say "  sounddevice: not installed (OPTIONAL -- only needed for live audio"
    say "               playback; offline --render works without it)."
    say "               Install with: pip install sounddevice --break-system-packages"
fi

if command -v jq >/dev/null 2>&1; then
    HAVE_JQ=1
    say "  jq: OK (can auto-merge hooks into settings.json)"
else
    HAVE_JQ=0
    say "  jq: not found (will print manual merge instructions instead of auto-merging)"
fi

say ""

# ---------------------------------------------------------------------
# 2. Make hook scripts executable (in place; nothing is copied)
# ---------------------------------------------------------------------

say "-- preparing hook scripts --"
for f in "hooks/send-event.sh" "hooks/autostart-daemon.sh"; do
    path="$SCRIPT_DIR/$f"
    if [ -f "$path" ]; then
        if [ -x "$path" ]; then
            say "  $f: already executable"
        else
            run chmod +x "$path"
            say "  $f: chmod +x"
        fi
    else
        say "  WARNING: $f not found under $SCRIPT_DIR" >&2
    fi
done
say ""

# ---------------------------------------------------------------------
# 3. Merge (or print instructions for) hooks/settings-snippet.json
# ---------------------------------------------------------------------

SNIPPET="$SCRIPT_DIR/hooks/settings-snippet.json"
SETTINGS="$CLAUDE_DIR/settings.json"

say "-- Claude Code settings --"

if [ ! -f "$SNIPPET" ]; then
    say "ERROR: $SNIPPET not found." >&2
    exit 1
fi

manual_instructions() {
    say "  To wire up the hooks manually, merge the 'hooks' object from:"
    say "    $SNIPPET"
    say "  into the 'hooks' key of:"
    say "    $SETTINGS"
    say "  (creating $SETTINGS with just that content if it doesn't exist yet)."
    say ""
    say "  Command paths in the snippet are written as \${CLAUDE_PROJECT_DIR}/hooks/...,"
    say "  which only resolves when this project ($SCRIPT_DIR)"
    say "  IS the Claude Code project root. Since $SETTINGS is"
    say "  read for EVERY project, replace \${CLAUDE_PROJECT_DIR} with the absolute"
    say "  path $SCRIPT_DIR so the hooks resolve everywhere"
    say "  (this is exactly what the auto-merge below does)."
}

# Emit the snippet with ${CLAUDE_PROJECT_DIR} rewritten to this project's
# absolute path. settings.json is global (applies to every project), so a
# ${CLAUDE_PROJECT_DIR}-relative command would point at a nonexistent script
# in any other project and make every hook invocation fail there.
resolved_snippet() {
    jq --arg dir "$SCRIPT_DIR" '
        def fix:
            if   type == "string" then gsub("\\$\\{CLAUDE_PROJECT_DIR\\}"; $dir)
            elif type == "object" then with_entries(.value |= fix)
            elif type == "array"  then map(fix)
            else . end;
        {hooks: (.hooks | fix)}
    ' "$SNIPPET"
}

if [ "$NO_MERGE" -eq 1 ]; then
    manual_instructions
elif [ "$HAVE_JQ" -eq 0 ]; then
    manual_instructions
else
    do_merge=0
    if [ "$ASSUME_YES" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then
        do_merge=1
    elif [ -t 0 ]; then
        printf '  Merge hooks into %s now? [y/N] ' "$SETTINGS"
        read -r reply || reply=""
        case "$reply" in
            y|Y|yes|YES) do_merge=1 ;;
            *) do_merge=0 ;;
        esac
    else
        say "  (non-interactive shell and no --yes given: skipping auto-merge)"
    fi

    if [ "$do_merge" -eq 1 ]; then
        run mkdir -p "$CLAUDE_DIR"

        if [ "$DRY_RUN" -eq 1 ]; then
            say "  [dry-run] would rewrite \${CLAUDE_PROJECT_DIR} -> $SCRIPT_DIR in the snippet's hook commands"
            if [ -f "$SETTINGS" ]; then
                say "  [dry-run] would back up $SETTINGS -> ${SETTINGS}.bak.<timestamp>"
                say "  [dry-run] would merge hooks (per-event union with your existing hooks, deduped) into $SETTINGS"
            else
                say "  [dry-run] would create $SETTINGS from the snippet's hooks block"
            fi
        else
            snippet_tmp="$(mktemp)"
            trap 'rm -f "$snippet_tmp"' EXIT
            resolved_snippet > "$snippet_tmp"

            if [ -f "$SETTINGS" ]; then
                ts="$(date +%Y%m%d%H%M%S)"
                backup="${SETTINGS}.bak.${ts}"
                cp "$SETTINGS" "$backup"
                say "  backed up existing settings to $backup"

                tmp="$(mktemp)"
                # Merge: existing settings as base, snippet's "hooks" overlaid
                # on top event-by-event. For each event name present in
                # either side, the resulting hooks array is the UNION
                # (base entries ++ snippet entries, deduped by exact value)
                # so any pre-existing hooks the user configured for that
                # same event (e.g. their own PreToolUse matcher) are kept
                # side-by-side with ours, never dropped. `unique` on the
                # concatenated array also makes re-running this installer
                # idempotent: identical entries from a prior run collapse
                # instead of duplicating.
                if jq -s '
                    .[0] as $base | .[1] as $new |
                    ($base.hooks // {}) as $bh |
                    ($new.hooks // {}) as $nh |
                    (($bh | keys) + ($nh | keys) | unique) as $allkeys |
                    ($allkeys | reduce .[] as $k ({};
                        . + { ($k): ((($bh[$k] // []) + ($nh[$k] // [])) | unique) }
                    )) as $merged_hooks |
                    $base + { hooks: $merged_hooks }
                ' "$SETTINGS" "$snippet_tmp" > "$tmp"; then
                    mv "$tmp" "$SETTINGS"
                    say "  merged hooks into $SETTINGS"
                else
                    rm -f "$tmp"
                    say "  ERROR: jq merge failed; left $SETTINGS untouched (backup at $backup)" >&2
                    manual_instructions
                    exit 1
                fi
            else
                cp "$snippet_tmp" "$SETTINGS"
                say "  created $SETTINGS with the hooks block"
            fi
            say "  hook commands point at $SCRIPT_DIR/hooks/ (absolute, so they work from any project)"
        fi
    else
        manual_instructions
    fi
fi

say ""
say "-- done --"
say "Test the daemon manually with:"
say "  python3 $SCRIPT_DIR/sonifier.py --check"
say "Simulate a session without needing Claude Code at all:"
say "  python3 $SCRIPT_DIR/simulate_session.py --speed 4"
say "  python3 $SCRIPT_DIR/sonifier.py --render $SCRIPT_DIR/demo-session.jsonl /tmp/demo.wav"
