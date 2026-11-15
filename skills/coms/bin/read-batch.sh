#!/usr/bin/env bash
# The CLI and watch use one per-holder physical checkpoint.
set -euo pipefail
usage() {
  cat <<'EOF'
Usage: read-batch.sh [MAX_CHARS | --max-chars N] [--help]
Requires explicit COMS_HOLDER and a native session marker or COMS_SESSION_ID.
COMS_DIR selects the store; COMS_DIR/checkpoints holds the durable last-read point.
READ_BATCH_CHARS defaults to 1800. Full history: coms inbox --all.
Delivery does not prove an action was handled. COMS_INBOX_FILE is unsupported.
EOF
}
cap="${READ_BATCH_CHARS:-1800}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --max-chars) [ "$#" -ge 2 ] || exit 2; cap="$2"; shift 2 ;;
    ''|*[!0-9]*) echo "read-batch.sh: unknown argument" >&2; exit 2 ;;
    *) cap="$1"; shift ;;
  esac
done
case "$cap" in ''|*[!0-9]*) echo "read-batch.sh: MAX_CHARS must be a number" >&2; exit 2 ;; esac
: "${COMS_HOLDER:?set COMS_HOLDER explicitly}"
if [ -n "${COMS_INBOX_FILE:-}" ]; then
  echo "read-batch.sh: COMS_INBOX_FILE is unsupported; use your own holder" >&2
  exit 2
fi
coms identity-check
exec coms inbox --consume --batch-read --print-chars "$cap"
