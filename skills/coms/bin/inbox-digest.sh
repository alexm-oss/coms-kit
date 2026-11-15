#!/usr/bin/env bash
# Shared checkpoint: fresh marked stores read backlog; legacy marks need review.
set -euo pipefail
usage() {
  cat <<'EOF'
Usage: inbox-digest.sh [--once] [--from-start] [--help]
Requires explicit COMS_HOLDER and a native session marker or COMS_SESSION_ID.
COMS_DIR selects the store. Checkpoints are per holder in COMS_DIR/checkpoints.
Fresh marked enrollment with no legacy marks reads initial backlog.
Legacy marks require coms migrate preview and confirmed apply before delivery.
--from-start is a compatibility no-op.
INBOX_MODE: normal, night, lowpower, full (same type rule in every mode).
INBOX_BATCH_S and urgency knobs are deprecated; one warning, then ignored.
INBOX_POLL_S defaults to 60. INBOX_PRINT_CHARS defaults to 600 for legacy mail.
COMS_INBOX_FILE is refused: select your own holder, never an arbitrary inbox.
Printed means delivered, not handled. Use coms inbox --peek for manual handling.
EOF
}
once=0
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --once) once=1 ;;
    --from-start) ;;
    *) echo "inbox-digest.sh: unknown argument" >&2; exit 2 ;;
  esac
done
: "${COMS_HOLDER:?set COMS_HOLDER explicitly}"
if [ -n "${COMS_INBOX_FILE:-}" ]; then
  echo "inbox-digest.sh: COMS_INBOX_FILE is unsupported; use COMS_DIR and your holder" >&2
  exit 2
fi
coms identity-check
mode="${INBOX_MODE:-normal}"
[ "${INBOX_NIGHT:-0}" != 1 ] || mode=night
deprecated=()
for knob in INBOX_BATCH_S INBOX_URGENT_FROM INBOX_EXTRA_URG INBOX_ROUTINE_RE; do
  if declare -p "$knob" >/dev/null 2>&1; then deprecated+=("$knob"); fi
done
if [ "${#deprecated[@]}" -gt 0 ]; then
  echo "coms: deprecated inbox knobs ignored: ${deprecated[*]}; use message TYPE for wakes and pull the inbox for FYI" >&2
fi
unset INBOX_BATCH_S INBOX_URGENT_FROM INBOX_EXTRA_URG INBOX_ROUTINE_RE
set -- --consume
if [ "${INBOX_PEEK:-0}" = 1 ]; then set -- --peek; fi
if [ -n "${INBOX_DELIVERY_RECEIPT:-}" ]; then set -- "$@" --delivery-receipt "$INBOX_DELIVERY_RECEIPT"; fi
while true; do
  coms inbox --digest-mode "$mode" --print-chars "${INBOX_PRINT_CHARS:-600}" "$@"
  [ "$once" != 1 ] || break
  sleep "${INBOX_POLL_S:-60}"
done
