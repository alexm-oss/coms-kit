#!/usr/bin/env bash
# coms-kit hook: coms Stop hook for Claude Code. A window in a coms-guarded
# repo may not go idle while its inbox holds unread messages. On Stop with
# unread mail, at most five originals are shown. A separate hashed prefix
# suppresses another refusal for the same batch without marking unshown mail
# notified. Later appended arrivals can refuse once again. Silent elsewhere.

umask 077
input=$(cat 2>/dev/null)
[ -z "$input" ] && exit 0

cwd=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
[ -z "$cwd" ] || [ -z "$sid" ] && exit 0

# Same guard detection as coms-context: only act inside coms-guarded repos.
marker_dir=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
if [ ! -f "${marker_dir%/}/coms-enabled" ]; then
  hooks_dir=$(git -C "$cwd" rev-parse --path-format=absolute --git-path hooks 2>/dev/null) || exit 0
  grep -q "coms guard hook" "$hooks_dir/pre-commit" 2>/dev/null || exit 0
fi

holder="claude-${sid:0:8}"
# Choose caller evidence, never copy a marker from an enrollment record.
# Automatic hooks route only the holder derived from the trusted harness payload.
session_var=COMS_SESSION_ID
marker="$sid"
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  [ "$CLAUDE_CODE_SESSION_ID" = "$sid" ] || exit 0
  session_var=CLAUDE_CODE_SESSION_ID
fi
if [ "${COMS_HOLDER:-}" = "$holder" ] && [ -n "${COMS_SESSION_ID:-}" ]; then
  session_var=COMS_SESSION_ID
  marker="$COMS_SESSION_ID"
fi
env -u COMS_SESSION_ID COMS_HOLDER="$holder" "$session_var=$marker" coms identity-check >/dev/null 2>&1 || exit 0
# The helper separately tracks shown mail and exact-prefix Stop suppression.
env -u COMS_SESSION_ID COMS_HOLDER="$holder" "$session_var=$marker" coms hook-notifications stop
result=$?
[ "$result" != 10 ] || exit 2
exit 0
