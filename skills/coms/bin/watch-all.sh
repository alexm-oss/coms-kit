#!/usr/bin/env bash
# Each child owns a separate process group, cleaned on signal, error or exit.
set -euo pipefail
case "${1:-}" in
  -h|--help)
    cat <<'EOF'
Usage: watch-all.sh [--help]
Requires explicit COMS_HOLDER and a native session marker or COMS_SESSION_ID.
Runs inbox-digest plus any watchers you name in WATCH_EXTRA.
WATCH_EXTRA is colon-separated paths to shell or Python watchers.
Stops and waits for its owned watcher process groups on signal or child exit.
EOF
    exit 0 ;;
esac
: "${COMS_HOLDER:?set COMS_HOLDER explicitly}"
exec python3 "$(dirname "$0")/watch-run.py" all
