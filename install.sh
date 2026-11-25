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

# Existing callers can put --profile before --status.
for arg in "$@"; do
  case "$arg" in
    --status|--add)
      exec python3 -I -B "$ROOT/coms/install_profiles.py" setup "$ROOT/install.sh" "$BIN" ./install.sh "$@" ;;
  esac
done

cli_only=0 claude_hooks=0 force=0
options=()
export PYTHONDONTWRITEBYTECODE=1
while [ "$#" -gt 0 ]; do
  arg=$1
  shift
  case "$arg" in
    --cli-only) cli_only=1 ;;
    --claude-hooks) claude_hooks=1 ;;
    --force) force=1 ;;
    --profile|--with|--without)
      if [ "$#" -eq 0 ]; then echo "install.sh: $arg needs a value" >&2; exit 2; fi
      options+=("$arg" "$1")
      shift ;;
    -h|--help)
      sed -n '2,8p' "$0"
      echo "Use --status [--profile NAME] for suggestions, or --add PIECE to keep existing choices."
      python3 "$ROOT/coms/install_profiles.py" --help
      exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || { echo "install.sh: coms needs python3 3.9 or newer on PATH" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "install.sh: coms needs git" >&2; exit 1; }

selection=$(python3 "$ROOT/coms/install_profiles.py" ${options[@]+"${options[@]}"})
if [ "$cli_only" = 1 ] && [ "$selection" != "core" ]; then
  echo "install.sh: --cli-only installs core. Use a profile for optional skill files." >&2
  exit 2
fi
helper=0
case " $selection " in *" helper-brief "*) helper=1 ;; esac

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

if [ "$helper" = 1 ]; then
  check "$HOOKS/helper-brief" "# coms-kit hook"
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
python3 "$ROOT/coms/install_files.py" mkdir "$BIN" "$STATE"
mkdir -p "$STATE/locks" "$STATE/threads"
# Mark the state folder only when this install created it: --purge deletes
# only a marked folder, never one that was already there.
if [ "$state_is_new" = 1 ]; then
  : > "$STATE/.coms-kit"
  : > "$STATE/locks/coord.lock"
fi
launcher=$(mktemp "$BIN/.coms-launcher.XXXXXX")
trap 'rm -f "$launcher"' EXIT
# The kit path is written with printf %q, so a folder name holding $( ), backticks
# or quotes stays a literal path and never runs as a command.
{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' "# coms-kit launcher: runs this kit's coms with the kit first on the import path"
  printf '%s\n' '# and the current folder off it, so a coms/ package where you stand cannot stand in.'
  printf '# coms-kit selection: %s\n' "$selection"
  printf 'export COMS_KIT_SELECTION=%q\n' "$selection"
  printf 'kit=%q\n' "$ROOT"
  # An installed agent may be outside the checkout or use nondefault destinations.
  # shellcheck disable=SC2016  # evaluated by the installed launcher
  printf '%s\n' 'if [ "${1:-}" = setup ]; then' '  shift'
  printf '  export COMS_BIN_DIR=%q COMS_SKILLS_DIR=%q COMS_HOOKS_DIR=%q COMS_STATE_DIR=%q\n' "$BIN" "$SKILLS" "$HOOKS" "$STATE"
  # shellcheck disable=SC2016  # expanded by the installed launcher
  printf '%s\n' '  exec python3 -I -B "$kit/coms/install_profiles.py" setup "$kit/install.sh" "$COMS_BIN_DIR" "coms setup" "$@"' 'fi'
  # shellcheck disable=SC2016  # literal on purpose: the launcher expands $kit and $@ when it runs
  printf '%s\n' 'exec python3 -c '"'"'import sys; kit = sys.argv.pop(1); sys.path[:] = [kit] + [p for p in sys.path if p != ""]; from coms.cli import main; sys.exit(main())'"'"' "$kit" "$@"'
} > "$launcher"
chmod 755 "$launcher"

if [ "$cli_only" = 0 ]; then
  python3 "$ROOT/coms/install_files.py" mkdir "$SKILLS"
  python3 "$ROOT/coms/install_files.py" copy-skill "$ROOT/skills/coms" "$SKILLS/coms" --selection "$selection"
fi

if [ "$claude_hooks" = 1 ]; then
  python3 "$ROOT/coms/install_files.py" mkdir "$HOOKS"
  cp "$ROOT/hooks/claude/coms-context" "$ROOT/hooks/claude/coms-inbox-stop-gate.sh" "$HOOKS/"
  chmod +x "$HOOKS/coms-context" "$HOOKS/coms-inbox-stop-gate.sh"
fi

if [ "$helper" = 1 ]; then
  python3 "$ROOT/coms/install_files.py" mkdir "$HOOKS"
  cp "$ROOT/hooks/claude/helper-brief" "$HOOKS/helper-brief"
  chmod +x "$HOOKS/helper-brief"
  echo "To activate the selected helper hook, see docs/REFERENCE.md."
elif ours "$HOOKS/helper-brief" "# coms-kit hook"; then
  rm -f "$HOOKS/helper-brief"
  python3 "$ROOT/coms/install_files.py" prune "$HOOKS"
fi

mv -f "$launcher" "$BIN/coms"
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
