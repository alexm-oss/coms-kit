#!/usr/bin/env bash
# One isolated command: tests, syntax, optional lint and secret scan.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
# Resolve only the selected temporary root, retaining guards on child paths.
TMPDIR="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
export TMPDIR
# Preserve source selection; strip inherited runtime identity and credentials.
TEST_HOME=$(mktemp -d "${TMPDIR:-/tmp}/coms-checks.XXXXXX")
TEST_HOME=$(cd "$TEST_HOME" && pwd -P)
trap 'rm -rf "$TEST_HOME"' EXIT
export HOME="$TEST_HOME" COMS_DIR="$TEST_HOME/state" COMS_HANDOVER_DIR="$TEST_HOME/handovers"
unset COMS_HOLDER COMS_SESSION_ID CLAUDE_SESSION_ID CLAUDE_CODE_SESSION_ID CODEX_THREAD_ID COMS_INBOX_FILE
unset COMS_LEASE_KEY COMS_LEASE_TOKEN COMS_WATCH_DIR XDG_STATE_HOME READ_BATCH_MARK INBOX_DIGEST_STATE
unset INBOX_DELIVERY_RECEIPT INBOX_PEEK WATCH_OUT WATCH_EXTRA
unset INBOX_MODE INBOX_FROM_START INBOX_POLL_S
unset COMS_BIN_DIR COMS_SKILLS_DIR COMS_STATE_DIR COMS_HOOKS_DIR
# Deterministic synthetic marker for older direct-Store test fixtures only.
export COMS_SESSION_ID=isolated-test-session
"${PYTHON:-python3}" scripts/check-source.py
if command -v shellcheck >/dev/null; then
  "${PYTHON:-python3}" scripts/check-source.py --shellcheck
else
  echo 'UNSUPPORTED: shellcheck is not installed'
fi
if command -v ruff >/dev/null; then
  ruff check --no-cache . hooks/claude/helper-brief skills/coms/bin/notify
else
  echo 'UNSUPPORTED: ruff is not installed'
fi
if command -v gitleaks >/dev/null; then
  gitleaks dir . --redact --no-banner
  gitleaks dir skills/coms --redact --no-banner
else
  echo 'UNSUPPORTED: gitleaks is not installed'
fi
"${PYTHON:-python3}" -m unittest discover -s tests -p 'test_*.py' -v "$@"
