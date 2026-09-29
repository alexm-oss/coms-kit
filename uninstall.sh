#!/usr/bin/env bash
# Remove what install.sh added: the launcher, the skill and the optional Claude
# Code hooks. Anything coms-kit did not install is left alone. The coms state
# (leases, room, inboxes) stays unless you pass --purge, which removes it only
# if install.sh created it.
#
# Usage: ./uninstall.sh [--purge]
set -euo pipefail

BIN="${COMS_BIN_DIR:-$HOME/.local/bin}"
SKILLS="${COMS_SKILLS_DIR:-$HOME/.claude/skills}"
HOOKS="${COMS_HOOKS_DIR:-$HOME/.claude/hooks}"
STATE="${COMS_STATE_DIR:-$HOME/.claude/coms}"

purge=0
for arg in "$@"; do
  case "$arg" in
    --purge) purge=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "uninstall.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

# remove_file <path> <marker>
remove_file() {
  if [ -f "$1" ] && [ ! -L "$1" ] && grep -q "$2" "$1" 2>/dev/null; then
    rm -f "$1"
    echo "  removed $1"
  elif [ -e "$1" ] || [ -L "$1" ]; then
    echo "  left alone (not from coms-kit): $1"
  fi
}

echo "coms uninstall:"
remove_file "$BIN/coms" "# coms-kit launcher"

if [ -d "$SKILLS/coms" ] && [ ! -L "$SKILLS/coms" ] && [ -f "$SKILLS/coms/.coms-kit" ]; then
  rm -rf "$SKILLS/coms"
  echo "  removed $SKILLS/coms"
elif [ -e "$SKILLS/coms" ] || [ -L "$SKILLS/coms" ]; then
  echo "  left alone (not from coms-kit): $SKILLS/coms"
fi

hooks_removed=0
for h in coms-context coms-inbox-stop-gate.sh; do
  if [ -f "$HOOKS/$h" ] && grep -q "# coms-kit hook" "$HOOKS/$h" 2>/dev/null; then
    hooks_removed=1
  fi
  remove_file "$HOOKS/$h" "# coms-kit hook"
done

if [ "$purge" = 1 ]; then
  if [ -d "$STATE" ] && [ ! -L "$STATE" ] && [ -f "$STATE/.coms-kit" ]; then
    rm -rf "$STATE"
    echo "  removed $STATE"
  elif [ -e "$STATE" ] || [ -L "$STATE" ]; then
    echo "  left alone (not created by coms-kit): $STATE"
  fi
elif [ -d "$STATE" ]; then
  echo "  kept $STATE (pass --purge to remove it)"
fi

if [ "$hooks_removed" = 1 ]; then
  echo "Take the coms-context and coms-inbox-stop-gate.sh entries out of ~/.claude/settings.json too."
fi
echo "Repos you guarded keep their .git/coms-enabled marker and any backstop hooks;"
echo "'coms unguard' in each repo removes them. The launcher is gone now, so run"
echo "./install.sh again, unguard each repo, then run ./uninstall.sh."
