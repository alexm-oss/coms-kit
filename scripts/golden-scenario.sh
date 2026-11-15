#!/bin/bash
# golden-scenario.sh <coms-cmd> <claude-hooks-dir> <coms-peer-path>
#
# Runs one fixed 20-step scenario against a build of coms and prints a
# normalized transcript on stdout, so two builds (the live one and a port, say)
# can each be run through it, diffed, and every difference accounted for.
#
#   <coms-cmd>          run through eval, so it may hold spaces:
#                         "python3 -B -m coms.cli"  (the caller exports PYTHONPATH)
#                         "coms"                    (the installed launcher)
#   <claude-hooks-dir>  folder holding coms-context and coms-inbox-stop-gate.sh
#   <coms-peer-path>    the coms-peer script
#
# Everything happens under one fresh temp folder T: HOME, COMS_DIR and the four
# repos (demo, demo-wt, own, husky). Nothing outside T is written. T is never
# removed, so a run can be inspected afterwards; its raw transcript is T/raw.txt.
# COMS_BIN is exported so the git hook coms guard installs calls the same CLI
# as the run does, never a `coms` found on PATH.
set -u

if [ "$#" -ne 3 ]; then
  echo "usage: golden-scenario.sh <coms-cmd> <claude-hooks-dir> <coms-peer-path>" >&2
  exit 2
fi
COMS_CMD="$1"
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
export COMS_BIN="$COMS_CMD"
unset COMS_HOLDER GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_COMMON_DIR
mkdir -p "$HOME" || exit 2
# Never sit in a folder that holds a `coms` package: python -m would import it.
cd "$HOME" || exit 2

# Unless the build under test IS the installed launcher, make a stray `coms` on
# PATH fail loudly (it shows in the transcript) instead of running the live one.
case "$COMS_CMD" in
  coms|"coms "*) ;;
  *)
    mkdir -p "$T/tripwire" || exit 2
    printf '#!/bin/sh\necho "golden-scenario: a step reached the coms on PATH" >&2\nexit 97\n' > "$T/tripwire/coms" || exit 2
    chmod 755 "$T/tripwire/coms" || exit 2
    PATH="$T/tripwire:$PATH"
    ;;
esac

# ---------------------------------------------------------------- helpers

c() { eval "$COMS_CMD" '"$@"'; }

step() { printf '\n== %s\n' "$*"; }

# _exec <holder|-> <dir> <label> <command...>
# Runs the command in <dir> as that COMS_HOLDER ('-' leaves it unset), prints
# what it wrote to stdout and stderr, then exit=<code>. The label is what the
# transcript shows, so paths that differ between builds never reach it.
_exec() {
  local who="$1" dir="$2" label="$3"
  shift 3
  printf '$ [%s @ %s] %s\n' "$who" "${dir##*/}" "$label"
  (
    cd "$dir" || exit 97
    if [ "$who" = "-" ]; then unset COMS_HOLDER; else export COMS_HOLDER="$who"; fi
    "$@"
  ) </dev/null 2>&1
  printf 'exit=%s\n' "$?"
}

run() { local who="$1" dir="$2"; shift 2; _exec "$who" "$dir" "$*" "$@"; }
co() { local who="$1" dir="$2"; shift 2; _exec "$who" "$dir" "coms $*" c "$@"; }

# Take a lease as holder c through the Store API and leave it held (30s TTL).
# It uses the CLI's PYTHONPATH, or the live checkout when the caller has none.
hold() {
  _exec c "$HOME" "hold $1 as c (Store.acquire)" \
    env PYTHONPATH="${PYTHONPATH:?set PYTHONPATH to the reviewed build}" \
    python3 -B -c 'import os, sys
from coms.store import Store
Store(os.environ["COMS_DIR"], "c").acquire(sys.argv[1], intent="held")' "$1"
}

# The thread file of one holder: holder, repo, worktree ('-' if the key is
# missing) and intent, as sorted-key json.
thread_file() {
  _exec - "$HOME" "thread file of $1: holder, repo, worktree, intent" \
    python3 -B -c 'import json, os, sys
path = os.path.join(os.environ["COMS_DIR"], "threads", sys.argv[1] + ".json")
with open(path) as fh:
    data = json.load(fh)
keys = ("holder", "repo", "worktree", "intent")
print(json.dumps({k: data.get(k, "-") for k in keys}, sort_keys=True))' "$1"
}

# Feed the demo window's payload to one of the Claude hooks, as Claude Code does.
claude_hook() {
  printf '$ [claude hook] %s < {"cwd": "<demo>", "session_id": "abcdef1234567890"}\n' "$1"
  printf '{"cwd": "%s", "session_id": "abcdef1234567890"}' "$T/demo" | "$HOOKS/$1" 2>&1
  printf 'exit=%s\n' "${PIPESTATUS[1]}"
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
  if [ -e "$2" ]; then printf '> %s: yes\n' "$1"; else printf '> %s: no\n' "$1"; fi
}

any_orig() {
  local f
  for f in "$2"/*.coms-orig; do
    if [ -e "$f" ]; then printf '> %s: yes\n' "$1"; return; fi
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

  step "5 who"
  co a "$T/demo" who

  step "6 msg b hello as a, then inbox as b"
  co a "$T/demo" msg b hello
  co b "$T/demo-wt" inbox

  step "7 check demo:main as a"
  co a "$T/demo" check demo:main

  step "8 do demo:main --wait 0 -- echo ran, as a"
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co a "$T/demo" do demo:main --wait 0 -- echo ran

  step "9 c holds demo:main; check and do as a"
  hold demo:main
  co a "$T/demo" check demo:main
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co a "$T/demo" do demo:main --wait 0 -- echo ran

  step "10 unlock main from demo as a, then status"
  co a "$T/demo" unlock main
  co a "$T/demo" status

  step "11 unlock demo:free as a"
  co a "$T/demo" unlock demo:free

  step "12 guard in demo; its hooks and marker"
  co a "$T/demo" guard
  names "demo .git/hooks" "$T/demo/.git/hooks"
  flag "demo .git/coms-enabled exists" "$T/demo/.git/coms-enabled"

  step "13 c holds demo:main again; commit on main as a (blocked); unlock demo:main; commit again (allowed)"
  hold demo:main
  run a "$T/demo" git commit -q --allow-empty -m blocked
  co a "$T/demo" unlock demo:main
  run a "$T/demo" git commit --allow-empty -m allowed
  run a "$T/demo" git log --format='log: %h %s'

  step "14 do main -- git commit, from demo with COMS_HOLDER unset"
  # shellcheck disable=SC1010  # "do" is the coms subcommand, not a shell keyword
  co - "$T/demo" do main -- git commit -q --allow-empty -m landed
  run - "$T/demo" git log --format='log: %h %s'

  step "15 guard in own; its hooks, .coms-orig and marker; commit in own"
  co a "$T/own" guard
  names "own .git/hooks" "$T/own/.git/hooks"
  any_orig "own has a .coms-orig hook" "$T/own/.git/hooks"
  flag "own .git/coms-enabled exists" "$T/own/.git/coms-enabled"
  run a "$T/own" git commit -q --allow-empty -m own-commit

  step "16 guard in husky; git status; .husky; marker"
  co a "$T/husky" guard
  run a "$T/husky" git status --porcelain
  names "husky .husky" "$T/husky/.husky"
  flag "husky .git/coms-enabled exists" "$T/husky/.git/coms-enabled"

  step "17 enroll w from demo-wt; its thread file"
  co w "$T/demo-wt" enroll
  thread_file w

  step "18 coms-peer w with sessions for demo (local_demo, newest) and demo-wt (local_wt)"
  printf '[{"sessionId": "local_demo", "title": "demo window", "cwd": "%s/demo", "lastActivityAt": "2026-09-29T10:00:00Z"}, {"sessionId": "local_wt", "title": "wt window", "cwd": "%s/demo-wt", "lastActivityAt": "2026-09-29T09:00:00Z"}]\n' "$T" "$T" > "$T/sessions.json"
  _exec - "$HOME" "coms-peer w --sessions sessions.json" python3 -B "$PEER" w --sessions "$T/sessions.json"

  step "19 claude hooks: banner, inbox shown, not shown again, stop gate refuses once, then passes"
  claude_hook coms-context
  co a "$T/demo" msg claude-abcdef12 "hold on"
  claude_hook coms-context
  claude_hook coms-context
  co a "$T/demo" msg claude-abcdef12 second
  claude_hook coms-inbox-stop-gate.sh
  claude_hook coms-inbox-stop-gate.sh

  step "20 inbox-seen becomes a regular file; a third message; stop gate twice"
  # shellcheck disable=SC2016  # the inner sh expands $COMS_DIR, not this shell
  _exec - "$HOME" "move inbox-seen aside, put a regular file in its place" \
    sh -c 'mv "$COMS_DIR/inbox-seen" "$COMS_DIR/inbox-seen.aside" && : > "$COMS_DIR/inbox-seen"'
  co a "$T/demo" msg claude-abcdef12 third
  claude_hook coms-inbox-stop-gate.sh
  claude_hook coms-inbox-stop-gate.sh
}

# ---------------------------------------------------------------- normalize

normalize() {
  python3 -B -c 'import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="backslashreplace").read()
text = text.replace(sys.argv[2], "<T>")
text = re.sub(r"\[main [0-9a-f]{7,40}\]", "[main <sha>]", text)
text = re.sub(r"(?m)^log: [0-9a-f]{7,40} ", "log: <sha> ", text)
text = re.sub(r"[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?", "<time>", text)
text = re.sub(r"[0-9]+s left", "<n>s left", text)
text = re.sub(r"pid-[0-9]+", "pid-<n>", text)
sys.stdout.write(text)' "$1" "$2"
}

scenario > "$T/raw.txt" 2>&1
normalize "$T/raw.txt" "$T"
