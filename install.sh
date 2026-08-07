#!/usr/bin/env bash
# install.sh — idempotent installer for Claudio.
#
# What it does:
#   1. Checks for python3 and numpy (required), notes sounddevice as
#      optional (needed only for live audio playback, not --render).
#   2. Uses the project files in place (this repo IS the install; nothing
#      is copied elsewhere) and makes the hook scripts executable.
#   3. Prints merge instructions for hooks/settings-snippet.json into the
#      target settings.json (global or project-scoped, see below), or --
#      if `jq` is present -- offers to merge it automatically, ALWAYS
#      backing up the existing file to settings.json.bak.<timestamp> first,
#      never overwriting blind.
#
# Scope (where the hooks get installed):
#   --global   Merge into "$CLAUDE_DIR/settings.json" (applies to every
#              Claude Code project on this machine).
#   --project  Merge into "<repo>/.claude/settings.json" (this project only).
#   Neither given + interactive (a tty): prompts. Neither given +
#   non-interactive: defaults to --global (and says so).
#
# Flags:
#   --dry-run        Print what would happen; make no changes anywhere.
#   --yes / -y        Non-interactive: assume "yes" to the merge prompt.
#   --no-merge         Never touch settings.json, just print instructions.
#   --uninstall        Surgically remove this repo's hook entries from
#                      settings.json (same scope/backup rules as install).
#   --global          Install hooks globally (see Scope above).
#   --project          Install hooks for this project only (see Scope above).
#   --claude-dir DIR  Override the Claude config dir used for --global
#                      (default: $CLAUDE_CONFIG_DIR or ~/.claude). Mainly
#                      for testing: install.sh --global --claude-dir
#                      /tmp/fakehome/.claude
#
# Safe to re-run: merging is idempotent (keyed by our hook commands; running
# twice does not duplicate entries) and file copies are skip-if-unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

DRY_RUN=0
ASSUME_YES=0
NO_MERGE=0
UNINSTALL=0
SCOPE=""
CLAUDE_DIR="${CLAUDE_DIR:-${CLAUDE_CONFIG_DIR:-${HOME:-.}/.claude}}"

usage() {
    cat <<EOF
Usage: install.sh [--dry-run] [--yes|-y] [--no-merge] [--global|--project] [--claude-dir DIR]
       install.sh --uninstall [--dry-run] [--global|--project] [--claude-dir DIR]

  --dry-run          Show what would happen; make no changes.
  --yes, -y          Non-interactive: assume yes for the settings merge.
  --no-merge         Never touch settings.json; print instructions only.
  --uninstall        Remove this repo's hook entries from settings.json (see Scope).
  --global           Install/uninstall hooks in \$CLAUDE_DIR/settings.json (every project).
  --project          Install/uninstall hooks in <repo>/.claude/settings.json (this project only).
                      Neither flag + interactive: prompts. Neither + non-interactive: --global.
  --claude-dir DIR   Claude config directory for --global (default: \$CLAUDE_CONFIG_DIR or \$HOME/.claude).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --no-merge) NO_MERGE=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --global) SCOPE="global" ;;
        --project) SCOPE="project" ;;
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

SCOPE_PROMPTED=0
if [ -z "$SCOPE" ]; then
    if [ -t 0 ]; then
        printf 'Install hooks globally (every Claude Code project on this machine) or just for this project? [g/P] '
        read -r reply || reply=""
        case "$reply" in
            g|G|global) SCOPE="global" ;;
            *) SCOPE="project" ;;
        esac
        SCOPE_PROMPTED=1
    else
        SCOPE="global"
    fi
fi

if [ "$SCOPE" = "project" ]; then
    SETTINGS_DIR="$SCRIPT_DIR/.claude"
else
    SETTINGS_DIR="$CLAUDE_DIR"
fi

say "== Claudio installer =="
say "project dir: $SCRIPT_DIR"
say "scope:       $SCOPE"
if [ "$SCOPE" = "global" ]; then
    say "claude dir:  $CLAUDE_DIR"
    if [ "$SCOPE_PROMPTED" -eq 0 ]; then
        say "  (no --global/--project given and not running interactively: defaulting to --global)"
    fi
fi
[ "$DRY_RUN" -eq 1 ] && say "(--dry-run: no changes will be made)"
say ""

# ---------------------------------------------------------------------
# 0. --uninstall: surgically remove this repo's hook entries, then exit.
# ---------------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
    if ! command -v jq >/dev/null 2>&1; then
        say "ERROR: jq not found; can't uninstall automatically." >&2
        say "  Remove any hooks pointing at $SCRIPT_DIR/hooks/{send-event,autostart-daemon}.sh" >&2
        say "  by hand from: $SETTINGS_DIR/settings.json" >&2
        exit 1
    fi

    SETTINGS="$SETTINGS_DIR/settings.json"
    say "-- uninstalling Claudio hooks ($SCOPE) --"

    if [ ! -f "$SETTINGS" ]; then
        say "  $SETTINGS does not exist; nothing to do."
        exit 0
    fi
    if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
        say "ERROR: $SETTINGS is not valid JSON; refusing to touch it." >&2
        exit 1
    fi

    # Drop any hook entry whose command references our two scripts by path
    # (send-event.sh / autostart-daemon.sh), then prune whatever that leaves
    # empty: a matcher-group with no hooks left, an event key with no
    # matcher-groups left, and the whole "hooks" object if nothing remains.
    uninstalled="$(jq '
        def prune_hooks: .hooks |= map(select(.command | test("hooks/(send-event|autostart-daemon)\\.sh") | not));
        (.hooks // {}) as $h
        | ($h | with_entries(.value |= (map(prune_hooks | select((.hooks // []) | length > 0))))) as $pruned
        | ($pruned | with_entries(select((.value | length) > 0))) as $final
        | if ($final | length) == 0 then del(.hooks) else . + {hooks: $final} end
    ' "$SETTINGS")"

    if diff -q <(jq -S . "$SETTINGS") <(printf '%s' "$uninstalled" | jq -S .) >/dev/null 2>&1; then
        say "  no Claudio hook entries found in $SETTINGS; nothing to do."
        exit 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        say "  [dry-run] would remove Claudio hook entries from $SETTINGS (backed up first)"
        exit 0
    fi

    ts="$(date +%Y%m%d%H%M%S)"
    backup="${SETTINGS}.bak.${ts}"
    cp "$SETTINGS" "$backup"
    say "  backed up existing settings to $backup"
    printf '%s\n' "$uninstalled" >"$SETTINGS"
    say "  removed Claudio hook entries from $SETTINGS"
    exit 0
fi

# ---------------------------------------------------------------------
# 1. Dependency checks
# ---------------------------------------------------------------------

say "-- checking dependencies --"

if ! command -v python3 >/dev/null 2>&1; then
    say "ERROR: python3 not found on PATH. Install python3 >= 3.10 and re-run." >&2
    exit 1
fi

# Prefer the repo's venv python (has numpy/scipy/sounddevice) over plain
# python3, same resolution hooks/autostart-daemon.sh uses -- so what we
# check here is what the daemon actually runs.
REPO_VENV_PY="$SCRIPT_DIR/.venv/bin/python"
PYTHON="$([ -x "$REPO_VENV_PY" ] && printf '%s' "$REPO_VENV_PY" || printf '%s' python3)"

PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "  interpreter: $PYTHON ($PY_VER)"

if "$PYTHON" -c 'import numpy' >/dev/null 2>&1; then
    say "  numpy: OK"
else
    say "ERROR: numpy MISSING (required by src/main.py, including offline --render)." >&2
    say "       Install with: python3 -m pip install -r requirements.txt" >&2
    say "       Or skip setup entirely: uv run src/main.py --check" >&2
    say "       (src/main.py has a PEP 723 header uv reads to build a venv on the fly)." >&2
    exit 1
fi

if "$PYTHON" -c 'import scipy' >/dev/null 2>&1; then
    say "  scipy: OK"
else
    say "  scipy: MISSING (required by the default AmbientTheme sound -- v2's"
    say "         Freeverb/bed/rain filtering. GeigerTheme, CLAUDIO_THEME=geiger,"
    say "         still runs without it, but ambient will sound degraded/unfiltered.)"
    say "         Install with: python3 -m pip install -r requirements.txt"
fi

if "$PYTHON" -c 'import sounddevice' >/dev/null 2>&1; then
    say "  sounddevice: OK (live audio playback available)"
else
    say "  sounddevice: not installed (OPTIONAL -- only needed for live audio"
    say "               playback; offline --render works without it)."
    say "               Install with: python3 -m pip install -r requirements.txt"
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
SETTINGS="$SETTINGS_DIR/settings.json"

say "-- Claude Code settings ($SCOPE) --"

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
    if [ "$SCOPE" = "global" ]; then
        say "  Command paths in the snippet are written as \${CLAUDE_PROJECT_DIR}/hooks/...,"
        say "  which only resolves when this project ($SCRIPT_DIR)"
        say "  IS the Claude Code project root. Since $SETTINGS is"
        say "  read for EVERY project, replace \${CLAUDE_PROJECT_DIR} with the absolute"
        say "  path $SCRIPT_DIR so the hooks resolve everywhere"
        say "  (this is exactly what the auto-merge below does)."
    else
        say "  Command paths in the snippet are written as \${CLAUDE_PROJECT_DIR}/hooks/...;"
        say "  leave them as-is -- Claude Code sets \${CLAUDE_PROJECT_DIR} to this project's"
        say "  path at hook runtime for project-scoped settings, so no rewriting is needed"
        say "  (and the snippet stays portable if this repo is ever moved or cloned elsewhere)."
    fi
}

# Emit the snippet, rewriting ${CLAUDE_PROJECT_DIR} to this project's
# absolute path ONLY for global scope: settings.json there applies to every
# project, so a ${CLAUDE_PROJECT_DIR}-relative command would point at a
# nonexistent script in any other project and make every hook invocation
# fail there. Project scope leaves it unexpanded -- Claude Code resolves
# ${CLAUDE_PROJECT_DIR} to this project's own path at hook runtime for
# project-scoped settings, so the snippet is merged verbatim.
resolved_snippet() {
    if [ "$SCOPE" = "global" ]; then
        jq --arg dir "$SCRIPT_DIR" '
            def fix:
                if   type == "string" then gsub("\\$\\{CLAUDE_PROJECT_DIR\\}"; $dir)
                elif type == "object" then with_entries(.value |= fix)
                elif type == "array"  then map(fix)
                else . end;
            {hooks: (.hooks | fix)}
        ' "$SNIPPET"
    else
        jq '{hooks: .hooks}' "$SNIPPET"
    fi
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
        run mkdir -p "$SETTINGS_DIR"

        if [ "$DRY_RUN" -eq 1 ]; then
            if [ "$SCOPE" = "global" ]; then
                say "  [dry-run] would rewrite \${CLAUDE_PROJECT_DIR} -> $SCRIPT_DIR in the snippet's hook commands"
            else
                say "  [dry-run] would keep \${CLAUDE_PROJECT_DIR} unexpanded in the snippet's hook commands"
            fi
            if [ -f "$SETTINGS" ]; then
                if ! jq empty "$SETTINGS" >/dev/null 2>&1; then
                    say "  [dry-run] WARNING: $SETTINGS is not valid JSON; a real run would fail here" >&2
                else
                    say "  [dry-run] would back up $SETTINGS -> ${SETTINGS}.bak.<timestamp> (skipped if merge is a no-op)"
                    say "  [dry-run] would merge hooks (per-event union with your existing hooks, deduped) into $SETTINGS"
                fi
            else
                say "  [dry-run] would create $SETTINGS from the snippet's hooks block"
            fi
        else
            snippet_tmp="$(mktemp)"
            trap 'rm -f "$snippet_tmp"' EXIT
            resolved_snippet > "$snippet_tmp"

            if [ -f "$SETTINGS" ]; then
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
                    if diff -q <(jq -S . "$SETTINGS") <(jq -S . "$tmp") >/dev/null 2>&1; then
                        rm -f "$tmp"
                        say "  $SETTINGS already up to date; no changes"
                    else
                        ts="$(date +%Y%m%d%H%M%S)"
                        backup="${SETTINGS}.bak.${ts}"
                        cp "$SETTINGS" "$backup"
                        say "  backed up existing settings to $backup"
                        mv "$tmp" "$SETTINGS"
                        say "  merged hooks into $SETTINGS"
                    fi
                else
                    rm -f "$tmp"
                    say "  ERROR: jq merge failed; left $SETTINGS untouched" >&2
                    manual_instructions
                    exit 1
                fi
            else
                cp "$snippet_tmp" "$SETTINGS"
                say "  created $SETTINGS with the hooks block"
            fi
            if [ "$SCOPE" = "global" ]; then
                say "  hook commands point at $SCRIPT_DIR/hooks/ (absolute, so they work from any project)"
            else
                say "  hook commands use \${CLAUDE_PROJECT_DIR}/hooks/ (resolved by Claude Code at runtime for this project)"
            fi
        fi
    else
        manual_instructions
    fi
fi

say ""
say "-- done --"
say "Test the daemon manually with:"
say "  python3 $SCRIPT_DIR/src/main.py --check"
say "Simulate a session without needing Claude Code at all:"
say "  python3 $SCRIPT_DIR/src/simulate_session.py --speed 4"
say "  python3 $SCRIPT_DIR/src/main.py --render $SCRIPT_DIR/demos/demo-session.jsonl /tmp/demo.wav"
