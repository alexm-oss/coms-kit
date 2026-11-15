#!/usr/bin/env bash
# Install coms from this kit: a `coms` launcher on PATH, the coms skill, and
# (only with --claude-hooks) two optional Claude Code hooks. It never replaces
# anything it did not install; --force moves such files aside first.
#
# Usage: ./install.sh [--cli-only] [--claude-hooks] [--force]
# Locations (override for testing): COMS_BIN_DIR, COMS_SKILLS_DIR,
# COMS_HOOKS_DIR, COMS_STATE_DIR.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="${COMS_BIN_DIR:-$HOME/.local/bin}"
SKILLS="${COMS_SKILLS_DIR:-$HOME/.claude/skills}"
HOOKS="${COMS_HOOKS_DIR:-$HOME/.claude/hooks}"
STATE="${COMS_STATE_DIR:-$HOME/.claude/coms}"

cli_only=0 claude_hooks=0 force=0
for arg in "$@"; do
  case "$arg" in
    --cli-only) cli_only=1 ;;
    --claude-hooks) claude_hooks=1 ;;
    --force) force=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || { echo "install.sh: coms needs python3 3.9 or newer on PATH" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "install.sh: coms needs git" >&2; exit 1; }

# ours <path> <marker>: true when this kit installed the file or folder.
ours() {
  if [ -d "$1" ] && [ ! -L "$1" ]; then
    [ -f "$1/.coms-kit" ]
  elif [ -f "$1" ] && [ ! -L "$1" ]; then
    [ -n "$2" ] && grep -q "$2" "$1" 2>/dev/null
  else
    return 1
  fi
}

blocked=""
check() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    ours "$1" "$2" || blocked="$blocked$1
"
  fi
}
check "$BIN/coms" "# coms-kit launcher"
[ "$cli_only" = 1 ] || check "$SKILLS/coms" ""
if [ "$claude_hooks" = 1 ]; then
  check "$HOOKS/coms-context" "# coms-kit hook"
  check "$HOOKS/coms-inbox-stop-gate.sh" "# coms-kit hook"
fi

stamp="$(date +%Y%m%d-%H%M%S)"
if [ -n "$blocked" ]; then
  if [ "$force" = 0 ]; then
    echo "install.sh: these already exist and were not installed by coms-kit:" >&2
    printf '%s' "$blocked" | sed 's/^/  /' >&2
    echo "Nothing was changed. Rerun with --force to move them aside (to <name>.bak-$stamp) first." >&2
    exit 1
  fi
  printf '%s' "$blocked" | while IFS= read -r path; do
    [ -n "$path" ] || continue
    mv "$path" "$path.bak-$stamp"
    echo "  moved aside: $path -> $path.bak-$stamp"
  done
fi

state_is_new=0
[ -e "$STATE" ] || state_is_new=1
mkdir -p "$BIN" "$STATE/locks" "$STATE/threads"
# Mark the state folder only when this install created it: --purge deletes
# only a marked folder, never one that was already there.
if [ "$state_is_new" = 1 ]; then
  : > "$STATE/.coms-kit"
fi
# The kit path is written with printf %q, so a folder name holding $( ), backticks
# or quotes stays a literal path and never runs as a command.
{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' "# coms-kit launcher: runs this kit's coms with the kit first on the import path"
  printf '%s\n' '# and the current folder off it, so a coms/ package where you stand cannot stand in.'
  printf 'kit=%q\n' "$ROOT"
  # shellcheck disable=SC2016  # literal on purpose: the launcher expands $kit and $@ when it runs
  printf '%s\n' 'exec python3 -c '"'"'import sys; kit = sys.argv.pop(1); sys.path[:] = [kit] + [p for p in sys.path if p != ""]; from coms.cli import main; sys.exit(main())'"'"' "$kit" "$@"'
} > "$BIN/coms"
chmod +x "$BIN/coms"

if [ "$cli_only" = 0 ]; then
  mkdir -p "$SKILLS"
  if [ -d "$SKILLS/coms" ] && [ ! -L "$SKILLS/coms" ] && [ -f "$SKILLS/coms/.coms-kit" ]; then
    rm -rf "$SKILLS/coms"
  fi
  cp -R "$ROOT/skills/coms" "$SKILLS/coms"
  : > "$SKILLS/coms/.coms-kit"
fi

if [ "$claude_hooks" = 1 ]; then
  mkdir -p "$HOOKS"
  cp "$ROOT/hooks/claude/coms-context" "$ROOT/hooks/claude/coms-inbox-stop-gate.sh" "$HOOKS/"
  chmod +x "$HOOKS/coms-context" "$HOOKS/coms-inbox-stop-gate.sh"
fi

echo "coms installed:"
echo "  launcher: $BIN/coms"
if [ "$cli_only" = 1 ]; then
  echo "  skill:    not installed (--cli-only)"
else
  echo "  skill:    $SKILLS/coms"
fi
echo "  state:    $STATE"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "  NOTE: add $BIN to your PATH to use 'coms' directly" ;;
esac
if [ "$claude_hooks" = 1 ]; then
  cat <<EOF

Claude Code hooks copied to $HOOKS.
To switch them on, open ~/.claude/settings.json and, under "hooks", append this
group to the "UserPromptSubmit" list:

  {"hooks": [{"type": "command", "command": "$HOOKS/coms-context", "timeout": 10}]}

and append this group to the "Stop" list:

  {"hooks": [{"type": "command", "command": "$HOOKS/coms-inbox-stop-gate.sh", "timeout": 10}]}

If "hooks" or either list is missing, create it. Leave the groups already in
those lists as they are.
EOF
fi
echo
echo "Next: run 'coms guard' inside a repo you want coordinated."
