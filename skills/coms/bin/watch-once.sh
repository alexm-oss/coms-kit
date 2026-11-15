#!/usr/bin/env bash
# One bounded event with task-owned process-tree cleanup.
set -euo pipefail
case "${1:-}" in
  -h|--help)
    cat <<'EOF'
Usage: watch-once.sh [--help]
Requires explicit COMS_HOLDER and a native session marker or COMS_SESSION_ID.
Starts watch-all, emits its first event, then stops and waits for owned watchers.
Rearm after handling the event. WATCH_MAX_S defaults to 7200, WATCH_SETTLE_S to 8.
COMS_WATCH_DIR selects state; default ~/.local/state/coms-orchestrator, per holder.
WATCH_OUT overrides the output file. INBOX_MODE defaults to night; FYI needs a manual pull.
WATCH_EXTRA passes to watch-all.
EOF
    exit 0 ;;
esac
: "${COMS_HOLDER:?set COMS_HOLDER explicitly}"
exec python3 "$(dirname "$0")/watch-run.py" once
