#!/bin/bash
# golden-scenario.sh <coms-cmd> <claude-hooks-dir> <coms-peer-path>
#
# Runs one fixed 20-step scenario against a build of coms and prints a
# normalized transcript on stdout, so two builds (the live one and a port, say)
# can each be run through it, diffed, and every difference accounted for.
#
#   <coms-cmd>          trusted launcher command, optionally with arguments:
#                         "python3 -B -m coms.cli"  (the caller exports PYTHONPATH)
#                         "coms"                    (the installed launcher)
#   <claude-hooks-dir>  folder holding coms-context and coms-inbox-stop-gate.sh
#   <coms-peer-path>    the coms-peer script
#
# Everything happens under one fresh temp folder T: HOME, COMS_DIR and the four
# repos (demo, demo-wt, own, husky). Nothing outside T is written. T is never
# removed, so a run can be inspected afterwards; its raw transcript is T/raw.txt.
# A fixture launcher on PATH routes Git and Claude hooks to the build under test.
set -u

if [ "${1:-}" = "--help" ]; then
  echo "usage: golden-scenario.sh <coms-cmd> <claude-hooks-dir> <coms-peer-path>"
  exit 0
fi
if [ "$#" -ne 3 ]; then
  echo "usage: golden-scenario.sh <coms-cmd> <claude-hooks-dir> <coms-peer-path>" >&2
  exit 2
fi
COMS_CMD="$1"
case "$COMS_CMD" in
  coms|"coms "*)
    launcher=$(command -v coms) || { echo 'golden-scenario: coms missing from PATH' >&2; exit 2; }
    printf -v launcher '%q' "$launcher"
    COMS_CMD="$launcher${COMS_CMD#coms}"
    ;;
esac
HOOKS="$(cd "$2" && pwd)" || { echo "golden-scenario: no such folder: $2" >&2; exit 2; }
PEER="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
for f in "$HOOKS/coms-context" "$HOOKS/coms-inbox-stop-gate.sh" "$PEER"; do
  [ -f "$f" ] || { echo "golden-scenario: missing file: $f" >&2; exit 2; }
done

# T is canonical (no symlinks), so the paths git prints match the ones we use.
# macOS mktemp -d ignores TMPDIR unless it is given a template, so pass one:
# a caller can then choose where T lands.
T="$(mktemp -d "${TMPDIR:-/tmp}/golden-scenario.XXXXXXXX")" || exit 2
[ -d "$T" ] || exit 2
T="$(cd "$T" && pwd -P)"

export HOME="$T/home"
export COMS_DIR="$HOME/.claude/coms"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export LC_ALL=C
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export COMS_HANDOVER_DIR="$HOME/handovers"
export COMS_BIN=coms
unset COMS_HOLDER COMS_SESSION_ID CLAUDE_CODE_SESSION_ID CLAUDE_SESSION_ID CODEX_THREAD_ID
unset COMS_LEASE_KEY COMS_LEASE_TOKEN COMS_INBOX_FILE COMS_MESSAGE_CARDS
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS
mkdir -p "$HOME" || exit 2
# Never sit in a folder that holds a `coms` package: python -m would import it.
cd "$HOME" || exit 2

mkdir -p "$T/bin" || exit 2
{
  printf '%s\n' '#!/bin/bash'
  printf 'exec %s "$@"\n' "$COMS_CMD"
} > "$T/bin/coms" || exit 2
chmod 755 "$T/bin/coms" || exit 2
export PATH="$T/bin:$PATH"

# ---------------------------------------------------------------- helpers

HOOK_SESSION=fixture-session
HOOK_HOLDER="claude-${HOOK_SESSION:0:8}"
LAST="$T/last-output.txt"
failures=0

step() { printf '\n== %s\n' "$*"; }

failed() {
  printf 'ASSERTION FAILED: %s\n' "$*"
  failures=$((failures + 1))
}

expect_line() {
  grep -Eq -- "$1" "$LAST" || failed "expected output matching $1"
}

reject_line() {
  if grep -Eq -- "$1" "$LAST"; then failed "unexpected output matching $1"; fi
}

report() {
  cat "$LAST"
  printf 'exit=%s\n' "$2"
  [ "$1" -eq "$2" ] || failed "expected exit=$1 but got exit=$2"
}

# _exec <expected exit> <holder|-> <dir> <label> <command...>
_exec() {
  local expected="$1" who="$2" dir="$3" label="$4" status
  shift 4
  printf '$ [%s @ %s] %s\n' "$who" "${dir##*/}" "$label"
  (
    cd "$dir" || exit 97
    if [ "$who" = "-" ]; then
      unset COMS_HOLDER COMS_SESSION_ID
    else
      export COMS_HOLDER="$who" COMS_SESSION_ID="golden-$who"
      if [ "$who" = "$HOOK_HOLDER" ]; then export COMS_SESSION_ID="$HOOK_SESSION"; fi
    fi
    "$@"
  ) </dev/null > "$LAST" 2>&1
  status=$?
  report "$expected" "$status"
}

run_expect() {
  local expected="$1" who="$2" dir="$3"
  shift 3
  _exec "$expected" "$who" "$dir" "$*" "$@"
}
run() { run_expect 0 "$@"; }

co_expect() {
  local expected="$1" who="$2" dir="$3"
  shift 3
  _exec "$expected" "$who" "$dir" "coms $*" coms "$@"
}
co() { co_expect 0 "$@"; }

# Take a lease as holder c through the Store API and leave it held (30s TTL).
# It uses the CLI's PYTHONPATH, or the live checkout when the caller has none.
hold() {
  _exec 0 c "$HOME" "hold $1 as c (Store.acquire)" \
    env PYTHONPATH="${PYTHONPATH:?set PYTHONPATH to the reviewed build}" \
    python3 -B -c 'import os, sys
from coms.store import Store
Store(os.environ["COMS_DIR"], "c").acquire(sys.argv[1], intent="held")' "$1"
}

# The thread file of one holder: holder, repo, worktree ('-' if the key is
# missing) and intent, as sorted-key json.
thread_file() {
  _exec 0 - "$HOME" "thread file of $1: holder, repo, worktree, intent" \
    python3 -B -c 'import json, os, sys
path = os.path.join(os.environ["COMS_DIR"], "threads", sys.argv[1] + ".json")
with open(path) as fh:
    data = json.load(fh)
keys = ("holder", "repo", "worktree", "intent")
print(json.dumps({k: data.get(k, "-") for k in keys}, sort_keys=True))' "$1"
}

# Feed the demo window's payload to one of the Claude hooks, as Claude Code does.
claude_hook() {
  local expected="$1" hook="$2" status
  printf '$ [claude hook] %s < fixture payload for %s\n' "$hook" "$HOOK_HOLDER"
  python3 -B -c 'import json, sys
print(json.dumps({"cwd": sys.argv[1], "session_id": sys.argv[2]}))' "$T/demo" "$HOOK_SESSION" \
    | "$HOOKS/$hook" > "$LAST" 2>&1
  status=${PIPESTATUS[1]}
  report "$expected" "$status"
}

# Names in a folder, sample files left out, one line: "> <label>: a b c".
names() {
  local label="$1" dir="$2" path f out=""
  for path in "$dir"/*; do
    [ -e "$path" ] || [ -L "$path" ] || continue  # no match leaves the pattern itself
    f="${path##*/}"
    case "$f" in *.sample) ;; *) out="$out $f" ;; esac
  done
  printf '> %s:%s\n' "$label" "${out:- (none)}"
}

flag() {
  if [ -e "$2" ]; then
    printf '> %s: yes\n' "$1"
  else
    printf '> %s: no\n' "$1"
    failed "$1"
  fi
}

any_orig() {
  local f
  for f in "$2"/*.coms-orig; do
    if [ -e "$f" ]; then printf '> %s: yes\n' "$1"; failed "$1"; return; fi
  done
  printf '> %s: no\n' "$1"
}

# ---------------------------------------------------------------- setup
# Quiet on purpose: the transcript starts at step 1.

must() {
  "$@" >/dev/null 2>&1 || { echo "golden-scenario: setup failed: $*" >&2; exit 3; }
}

# demo: main with one empty commit, plus demo-wt, a linked worktree on branch feat
must git init -q -b main "$T/demo"
must git -C "$T/demo" commit -q --allow-empty -m init
must git -C "$T/demo" worktree add -q -b feat "$T/demo-wt"

# own: main with one commit and a hook of its own
must git init -q -b main "$T/own"
must git -C "$T/own" commit -q --allow-empty -m init
printf '#!/bin/sh\necho own-hook-ran\n' > "$T/own/.git/hooks/pre-commit" || exit 3
chmod 755 "$T/own/.git/hooks/pre-commit" || exit 3

# husky: main with a committed .husky/pre-commit, and core.hooksPath=.husky
must git init -q -b main "$T/husky"
mkdir -p "$T/husky/.husky" || exit 3
printf '#!/bin/sh\necho husky-ran\n' > "$T/husky/.husky/pre-commit" || exit 3
chmod 755 "$T/husky/.husky/pre-commit" || exit 3
must git -C "$T/husky" add .husky/pre-commit
must git -C "$T/husky" commit -q -m init
must git -C "$T/husky" config core.hooksPath .husky

# ---------------------------------------------------------------- the scenario

scenario() {
  step "1 primer"
  co - "$HOME" primer

  step "2 enroll a from demo, --intent landing"
  co a "$T/demo" enroll --intent landing

  step "3 enroll b from demo-wt, --role orchestrator --intent coordinating"
  co b "$T/demo-wt" enroll --role orchestrator --intent coordinating

  step "4 status"
  co a "$T/demo" status
  expect_line "a on demo .*landing"
  expect_line "b on demo in demo-wt .*orchestrator.*coordinating"

  step "5 who"
  co a "$T/demo" who
  expect_line "^b$"

  step "6 msg b hello as a, then inbox as b"
  co a "$T/demo" msg b hello
  co b "$T/demo-wt" inbox --consume
  expect_line "a: hello$"
  co b "$T/demo-wt" inbox --peek
  reject_line "a: hello$"

  step "7 check demo:main as a"
  co a "$T/demo" check demo:main

  step "8 do demo:main --wait 0 -- echo ran, as a"
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co a "$T/demo" do demo:main --wait 0 -- echo ran
  expect_line "^ran$"

  step "9 c holds demo:main; check and do as a"
  hold demo:main
  co_expect 3 a "$T/demo" check demo:main
  expect_line "^demo:main held by c$"
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co_expect 75 a "$T/demo" do demo:main --wait 0 -- echo ran
  expect_line "held by c; did not run"
  reject_line "^ran$"

  step "10 unlock main from demo as a, then status"
  co a "$T/demo" unlock main
  co a "$T/demo" status

  step "11 unlock demo:free as a"
  co_expect 1 a "$T/demo" unlock demo:free
  expect_line "no lease on demo:free"

  step "12 guard in demo; its hooks and marker"
  co a "$T/demo" guard
  names "demo .git/hooks" "$T/demo/.git/hooks"
  flag "demo .git/coms-enabled exists" "$T/demo/.git/coms-enabled"
  flag "demo pre-commit exists" "$T/demo/.git/hooks/pre-commit"
  flag "demo pre-merge-commit exists" "$T/demo/.git/hooks/pre-merge-commit"

  step "13 c holds demo:main again; commit on main as a (blocked); unlock demo:main; commit again (allowed)"
  hold demo:main
  run_expect 1 a "$T/demo" git commit -q --allow-empty -m blocked
  expect_line "pre-commit is blocked"
  co a "$T/demo" unlock demo:main
  run a "$T/demo" git commit --allow-empty -m allowed
  run a "$T/demo" git log --format='log: %h %s'
  expect_line "^log: [0-9a-f]+ allowed$"
  reject_line "^log: [0-9a-f]+ blocked$"

  step "14 do main -- git commit, from demo with COMS_HOLDER unset"
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co - "$T/demo" do main -- git commit -q --allow-empty -m landed
  run - "$T/demo" git log --format='log: %h %s'
  expect_line "^log: [0-9a-f]+ landed$"

  step "15 guard in own; its hooks, .coms-orig and marker; commit in own"
  co a "$T/own" guard
  names "own .git/hooks" "$T/own/.git/hooks"
  any_orig "own has a .coms-orig hook" "$T/own/.git/hooks"
  flag "own .git/coms-enabled exists" "$T/own/.git/coms-enabled"
  run a "$T/own" git commit -q --allow-empty -m own-commit
  expect_line "^own-hook-ran$"

  step "16 guard in husky; git status; .husky; marker"
  co a "$T/husky" guard
  run a "$T/husky" git status --porcelain
  reject_line "."
  names "husky .husky" "$T/husky/.husky"
  flag "husky .git/coms-enabled exists" "$T/husky/.git/coms-enabled"

  step "17 enroll w from demo-wt; its thread file"
  co w "$T/demo-wt" enroll
  thread_file w
  expect_line '"repo": "demo"'
  expect_line '"worktree": "demo-wt"'

  step "18 coms-peer w with sessions for demo (local_demo, newest) and demo-wt (local_wt)"
  python3 -B -c 'import json, sys
print(json.dumps([
    {"sessionId": "local_demo", "title": "demo window", "cwd": sys.argv[1],
     "lastActivityAt": "2044-01-01T10:00:00Z"},
    {"sessionId": "local_wt", "title": "wt window", "cwd": sys.argv[2],
     "lastActivityAt": "2044-01-01T09:00:00Z"},
]))' "$T/demo" "$T/demo-wt" > "$T/sessions.json"
  _exec 0 - "$HOME" "coms-peer w --sessions sessions.json" python3 -B "$PEER" w --sessions "$T/sessions.json"
  expect_line "^local_wt[[:space:]]"
  reject_line "^local_demo[[:space:]]"

  step "19 claude hooks: banner, inbox shown once, stop gate refuses once then passes"
  co "$HOOK_HOLDER" "$T/demo" enroll --intent "fixture hook"
  claude_hook 0 coms-context
  expect_line 'This repo \(demo\) is coordinated'
  reject_line "UNREAD INBOX"
  co a "$T/demo" msg "$HOOK_HOLDER" "hold on"
  claude_hook 0 coms-context
  expect_line "UNREAD INBOX"
  expect_line "a: hold on$"
  claude_hook 0 coms-context
  reject_line "UNREAD INBOX|a: hold on$"
  co a "$T/demo" msg "$HOOK_HOLDER" second
  claude_hook 2 coms-inbox-stop-gate.sh
  expect_line "STOP REFUSED ONCE"
  expect_line "a: second$"
  claude_hook 0 coms-inbox-stop-gate.sh
  reject_line "STOP REFUSED|a: second$"

  step "20 obstruct notification state; prove the stop gate fails open twice"
  # shellcheck disable=SC2016  # the inner shell expands $COMS_DIR
  _exec 0 - "$HOME" "obstruct notification-seen in the fixture store" \
    sh -c 'mv "$COMS_DIR/notification-seen" "$COMS_DIR/notification-seen.aside" &&
      : > "$COMS_DIR/notification-seen"'
  co a "$T/demo" msg "$HOOK_HOLDER" third
  local _
  for _ in 1 2; do
    claude_hook 0 coms-inbox-stop-gate.sh
    expect_line "notification state parent must be a directory"
    reject_line "STOP REFUSED|a: third$"
  done
}

# ---------------------------------------------------------------- normalize

normalize() {
  python3 -B -c 'import os, re, shlex, sys
text = open(sys.argv[1], encoding="utf-8", errors="backslashreplace").read()
text = text.replace(sys.argv[2], "<T>")
build = os.environ.get("PYTHONPATH")
if build:
    text = text.replace(shlex.quote(build), "<build>").replace(build, "<build>")
text = text.replace(shlex.quote(sys.executable), "<python>")
text = re.sub(r"\[main [0-9a-f]{7,40}\]", "[main <sha>]", text)
text = re.sub(r"(?m)^log: [0-9a-f]{7,40} ", "log: <sha> ", text)
text = re.sub(r"[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?", "<time>", text)
text = re.sub(r"[0-9]+s left", "<n>s left", text)
text = re.sub(r"pid-[0-9]+", "pid-<n>", text)
sys.stdout.write(text)' "$1" "$2"
}

{
  scenario
  printf '\nGolden assertions: %s failure(s) across 20 steps.\n' "$failures"
} > "$T/raw.txt" 2>&1
normalize "$T/raw.txt" "$T" || exit 3
if [ "$failures" -ne 0 ]; then
  printf 'golden-scenario: raw diagnostics: %s\n' "$T/raw.txt" >&2
  exit 1
fi
