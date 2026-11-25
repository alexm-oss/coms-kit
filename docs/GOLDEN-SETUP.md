# Start here: a two-window day with COMS

Use one Claude Code window to coordinate and one Codex window to do the work.
Both run on the same machine and share one local COMS store. Start from the
kit checkout on macOS or Linux with Python 3.9+, Git 2.31+ and Bash. The
installer selects files. You start windows and watches and configure alerts
separately.

```sh
./install.sh --profile pair
export PATH="$HOME/.local/bin:$PATH"
coms setup --status
```

Open both windows in the same local project. The examples use the default
install paths. Generate each identity once and keep its exact values on
every later shell call in that window.

## Paste into the Claude Code window

```text
Coordinate this pair. Run these commands, keeping this identity on every COMS call.

export PATH="$HOME/.local/bin:$PATH"
export COMS_DIR="$HOME/.claude/coms"
export COMS_HOLDER=pair-command-$(python3 -c 'import secrets; print(secrets.token_hex(4))')
export COMS_SESSION_ID="$COMS_HOLDER-session"
coms enroll --role orchestrator --intent 'Coordinate the pair'
coms status
coms inbox --consume

Read ~/.claude/skills/coms/references/command.md. Take the owner's goal and
approved scope, search existing decisions, then use coms who to find the builder and give it one lane
with Bet, Outcome, Proof of done, and Appetite. Name the checkout, permitted
edits and test. Ask one owner question at a time and record the exact answer
with coms decide. Verify the landing evidence before reporting the outcome.
Wait for the owner's task after enrollment.
```

## Paste into the Codex window

```text
Run the build lane. Run these commands, keeping this identity on every COMS call.

export PATH="$HOME/.local/bin:$PATH"
export COMS_DIR="$HOME/.claude/coms"
export COMS_HOLDER=pair-codex-$(python3 -c 'import secrets; print(secrets.token_hex(4))')
export COMS_SESSION_ID="$COMS_HOLDER-session"
coms enroll --role codex-lead --intent 'Run the build lane'
coms status
coms inbox --consume

Read ~/.claude/skills/coms/references/codex-lead.md. Find the coordinator with
coms who and wait for the first lane. Confirm the base, checkout and test from
the brief. Own one candidate at a time and send the coordinator its full SHA,
test output and any gaps. Use the owner's recorded grant for shared changes
and take the main lease when landing. Keep open work and evidence paths current.
```

## Run a practice lane

For a first lane, use a local practice repository with a main branch and a
tracked file named `label.txt`. The owner asks for a clearer label. The
coordinator sends the builder the repository path and this brief:

```text
Bet: A clearer label makes the sample easier to read.
Outcome: label.txt says Shipping label.
Proof of done: The exact text check passes and the candidate SHA is recorded.
Appetite: 10 minutes.
```

The builder creates a branch, edits the file, runs
`test "$(cat label.txt)" = "Shipping label"` and commits. The coordinator
checks the evidence and asks whether the owner wants that full candidate SHA
landed in the practice repository. Record the answer with `coms decide`.
After approval, the builder switches to main, runs
`coms do main -- git merge --ff-only "$candidate"` with that exact SHA,
repeats the text check and reports `git rev-parse main`.

## Add a watch when wanted

Start from an enrolled shell. Give the Codex weekly watcher a JSON file with
`used_percent` and a timezone-aware `resets_at`. A manual sample or a trusted
adapter can update the file. For example, the shape is
`{"used_percent":33.5,"resets_at":"2044-01-01T12:30:00Z"}`. Use the actual
weekly usage and reset when monitoring a real window.

```sh
skill="$HOME/.claude/skills/coms"
export CODEX_WEEKLY_USAGE_FILE="$COMS_DIR/weekly-meter.json"
WATCH_EXTRA="$skill/bin/codex-pace-watch.sh:$skill/bin/load-watch.sh" \
  bash "$skill/bin/watch-all.sh"
```

Run this in a foreground terminal or a runner that streams output. The inbox
watch polls every minute by default and surfaces actionable cards. Read valid
FYI-only mail with `coms inbox --consume`. The weekly watcher polls every
five minutes by default. Keep extras running in watch mode. A child exit ends the whole watch,
including the load watch's normal exit after sustained strain, ten minutes by
default. Respond to the event, then start the watch again. Ctrl-C stops the
owned watcher processes. The watch prints locally. Leave notification backends
unconfigured unless the owner asks to use one.

If Codex reports `OVER PACE`, keep the practice check and defer unrelated
work. For Claude, use a separate five-hour reading and its reset time.
For example, `"$skill/bin/pace.py" 98 '2044-01-01T12:30:00Z'` prints
`HANDOVER` for an open window. Use actual meter values day to day. Let the
current leased operation reach a safe boundary, record open work and evidence,
and let the other window hold the lane. The replacement uses a new holder and
recovers the recorded work. Follow the [handover guide](../skills/coms/references/handover.md).

## Try a local known fix

This exercise runs one bot pass explicitly from the enrolled Codex shell.
It adds a fictional match in a separate catalog. Use your own verified fixes
and consenting local holders if you later opt into routine replies.

```sh
coms setup --add known-fixes
skill="$HOME/.claude/skills/coms"
bot=pair-fixes-$(python3 -c 'import secrets; print(secrets.token_hex(4))')
catalog=$(mktemp "$COMS_DIR/known-fixes-demo.XXXXXX")
sed -n 's/^# \(fictional display blank |.*\)/\1/p' "$skill/bin/known-fixes.txt" > "$catalog"
COMS_HOLDER="$bot" COMS_SESSION_ID="$bot-session" \
  coms enroll --intent 'Suggest catalogued fixes'
coms msg "$bot" 'TYPE: BLOCKER
TOPIC: Fictional display blank
NEED: Find the known fix
PROOF: label.txt'
COMS_HOLDER="$bot" COMS_SESSION_ID="$bot-session" \
  coms known-fixes --catalog "$catalog"
COMS_HOLDER="$bot" COMS_SESSION_ID="$bot-session" \
  coms known-fixes --catalog "$catalog" --reply --reply-to "$COMS_HOLDER"
coms inbox --consume
```

The preview counts matches. The opt-in pass sends an allowlisted local RULING
quoting the catalogued fix. Review the suggestion before acting.

## Grow when the work needs it

Add the integration lead with `coms setup --add controller` when several
writers regularly need their changes combined and tested. For occasional
permissions, secret handling, session or key changes, bring in an independent
reviewer for that revision. Use the full room profile when ongoing integration,
review, record keeping or host monitoring justifies dedicated roles and tools.
Choose the pieces and open the windows explicitly. See [recommended setup](recommended-setup.md).
