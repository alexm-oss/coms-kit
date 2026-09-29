#!/usr/bin/env bash
# Run the coms test suite: standard library unittest only.
# PYTHON picks the interpreter (default python3). Tests never write bytecode,
# print in order (so the summary comes last), and git in the tests ignores
# your global and system git config.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
"${PYTHON:-python3}" -m unittest discover -s tests -p 'test_*.py' -v "$@"
