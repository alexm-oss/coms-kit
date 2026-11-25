---
name: coms
description: Use when several AI agents share this machine, or the user says /coms or "start coms", to take turns on risky local operations like a merge, a ship or a shared simulator, and to message each other.
---

# coms: take your turn, don't clobber

You may be one of several agents on this machine at once: other windows of your tool, other tools,
and scripts. coms makes everyone take turns on operations only one agent should run at a time, gives
you a shared room to see who is doing what, and lets agents message each other, on this machine only.

## First: your holder id and session marker

Every coms command runs as a holder id plus a session marker. coms treats two agents with the same id
as one agent, so your id must be one no other agent on this machine uses.

- If a coms hook gave your session an id (Claude Code: `claude-<hex>`), use the exact prefix it shows.
- Otherwise make one from your tool, your folder and four random characters, like `codex-login-7f3a`
  (`python3 -c 'import secrets; print(secrets.token_hex(2))'`; never copy them from an example).
  Make a session marker the same way, and keep both for the whole session.
- Never reuse a `COMS_HOLDER` you merely inherited from your shell, and never use another agent's id.
  If coms says your holder is "missing or unbound" (it enrolled under an older coms), enroll once with `--adopt-legacy`.

Put both in front of EVERY coms command, reads included, because each shell call starts fresh:

    COMS_HOLDER=codex-login-7f3a COMS_SESSION_ID=codex-login-7f3a-s1 coms status

The examples below show the bare command for short; add your two values when you run them.

## On invocation ("start coms")

1. Enroll with one line on what you are about to do. It prints the room: who holds what, who is enrolled.

       coms enroll --intent '<one line: what you are about to do>'

   A Claude Code holder also gets an inbox Monitor command printed. Run `coms inbox --consume` first
   for earlier mail, then run that command in a background Monitor (it stops after 30 minutes; re-arm it).

2. Read the room any time with `coms status`. If someone holds `<repo>:main`, expect to wait your turn.

3. If the repo is not marked yet, mark it once with `coms guard` (inside the repo, or pass its path).
   Where git owns the hooks folder and no pre-commit or pre-merge-commit hook exists yet, this also
   installs a backstop hook that blocks raw commits to main/master while someone holds the lease. Repos
   with their own hooks, husky or otherwise, get the mark only: coms leaves their hooks alone.

4. Check the installed setup from any directory with `coms setup --status`.
   For a Claude and Codex pair, run `coms setup --status --profile pair` to
   compare directly with the recommended pair. From the kit checkout,
   `./install.sh --status --profile pair` gives the same comparison.

   Before offering anything, check this window's previous answers:

       coms decisions --current --json "setup-$COMS_HOLDER-"

   Use your unique holder for this session. Skip any piece whose exact topic
   `setup-$COMS_HOLDER-$piece` has answer `declined`. Show the remaining
   missing pieces once per session, one line each, using the benefit and the
   exact add command printed by status. If the list is empty, continue quietly.
   Ask one question about the piece that fits the work in front of you. Wait
   for the human's answer before running its add command. An unanswered offer
   stays pending. Keep normal work moving.

   If the human declines, set `piece` to the suggested piece name and record
   the actual answer once. Use the quoting rules below:

       coms decide "setup-$COMS_HOLDER-$piece" 'declined' --quote '<exact words>'

   Never suggest that piece again in this session after a decline. If the human
   declines all the listed pieces, record the decline for each. Check the
   same decisions before offering a piece later from a role guide. Choosing
   files leaves destinations, schedules and permission to act up to the human.

## When a piece helps

Use the installed skill's `bin` and `references` directories. For an optional
piece that is missing, offer `coms setup --add PIECE` with the named piece
and the reason it fits. Add one accepted piece without dropping prior choices.
Internal modules run through their public commands.

| When | Installed tool or guide | Optional piece |
| --- | --- | --- |
| Mail should interrupt the current work once | Run `watch-once.sh`; use `watch-run.py` through the watcher to manage its child processes. | Core |
| Several watchers should stream together | Run `watch-all.sh`; `inbox-digest.sh` supplies actionable mail as it arrives. | Core |
| A watch signals new mail | Use `read-batch.sh` to read it and advance delivery. | Core |
| A local desktop window needs identifying | Use `coms-peer` with the session list described in `claude-desktop.md`. | Core |
| A replacement window is taking over | Use `handover-note` and `handover.md`; add the full guide for role-specific checklists. | `handover-guide` |
| Team members need to agree responsibilities | Read `operating-model.md` and, with a role selected, `protocol.md` for reasons and grant boundaries. | Core index |
| The owner needs one coordinator | Use `command.md` to route work and owner questions. | `command` |
| Codex work needs a named lead | Use `codex-lead.md` to assign work and check evidence. | `codex-lead` |
| Several lanes land in one repository | Use `controller.md` to coordinate integration and shared tests. | `controller` |
| Test scope or a stuck rig needs a ruling | Use `governor.md` to brief the test reviewer. | `governor` |
| A change touches secrets, sessions or keys | Use `security-reviewer.md` to brief the independent reviewer. | `security` |
| Decisions and follow-ups span several products | Use `keeper.md` to file one sourced card per affected product. | `keeper` |
| A helper is about to receive work | Use `helper-brief` to check its task, model, effort and time budget after hook configuration. | `helper-brief` |
| A usage warning arrives before widening work | Run `pace.py` for a five-hour reading or `codex-pace-watch.sh` for a weekly reading; the latter calls `codex_pace_watch.py`. Both use `operator_state.py` for local state. | `pacing` |
| A Codex window or its helpers seem busy | Run `codex-now.py` or `codex-fleet.py`; both use `codex_local.py` to summarize local activity. | `codex-view` |
| The machine feels slow or hot | Run `load-watch.sh --once` before changing lane width. | `load-watch` |
| An actionable change needs the human away from the desk | Use `notify` with an approved destination and shareable text. | `notify` |
| A scheduled job appears to be failing | Run `job-health.py` for that asset, or configure the selected jobs in `job-watch.txt`. | `job-health` |
| An ASK or BLOCKER repeats a known symptom | Use `known-fixes` to preview matches against a populated copy of `known-fixes.txt`. | `known-fixes` |
| Many decisions obscure the current answer | Use `current-rules` to build the private page and `rule-pins.txt` to prioritize topics. | `current-rules` |

## The one rule

Anything only one agent should do at a time goes through `coms do`. Never commit or merge to a shared
branch, and never ship or deploy, with a raw command:

    coms do main -- git -C /path/to/main/checkout merge --no-ff my-branch
    coms do ship -- ./scripts/release.sh
    coms do simulator -- <test command>          # a device, port or checkout only one agent can use

A bare name gets the repo prefix (`main` becomes `<repo>:main`) from the main checkout and from every
linked worktree, so all agents queue on the same lease. Do not build the key from your worktree's
folder name. Use a name with a colon, like `shared:simulator`, for something shared across repos.

`coms do` waits for the lease (up to ten minutes; `--wait 0` refuses at once), keeps it alive while the
command runs, releases it and returns the command's own exit code. If it cannot get the lease it exits
75 and runs nothing. Work no other agent touches (editing files, committing on your own branch) needs
no lease.

A merge to a shared branch, a ship or a deploy needs the human's own yes first: `coms do` only makes
agents take turns, it never grants permission.

## The trap: `coms do` runs in YOUR folder

`coms do` runs your command in your working directory; it does not move you to the main checkout. From
a feature worktree, `coms do main -- git merge --no-ff my-branch` merges the branch into itself, prints
"Already up to date", exits 0 and lands nothing. So:

1. Target the main checkout explicitly, whatever your folder:

       coms do main -- git -C /path/to/main/checkout merge --no-ff my-branch

2. Prove the landing afterwards. Never trust the exit code alone:

       git -C /path/to/main/checkout merge-base --is-ancestor <your-sha> main \
         && echo LANDED || echo "NOT LANDED"

Exit code 0 means a command ran, not that the thing you wanted happened.

## Messages between agents

    coms who                                    # the coordinating agent's id, if one enrolled
    coms msg <holder-id> 'blocked on the build, need a call before I land'
    coms inbox --consume                        # read your new mail and mark it read
    coms inbox --peek                           # look without marking anything
    coms inbox --all                            # full history

Agent shells pipe their output, and piped output defaults to peek, so name `--consume` to mark mail
read or `--peek` to only look. `bin/read-batch.sh` is a short consuming read for scripts and watches.

A message is a line appended to a file. A busy agent sees it the next time it reads its inbox; an idle
agent never does. "Delivered" is not "read": for anything a peer must act on, also wake it the way your
tool allows, and wait for its own reply before you report it done. In the Claude desktop app, see
`references/claude-desktop.md`. To wait for mail without ending your turn, use a bounded read and repeat:

    perl -e 'alarm shift; exec @ARGV' 900 env COMS_HOLDER=<id> COMS_SESSION_ID=<marker> coms inbox --peek --follow --exit-after-first-batch

When a line prints, run `coms inbox --consume`, act, then wait again.

Message cards keep mail short. Start with these lines, then at most five lines of body:

    TYPE: ASK
    TOPIC: release-scope
    NEED: rule on whether the export fix ships in this release
    PROOF: notes/release-scope.md

TYPE is ASK, RULING, BLOCKER, DONE or FYI. A NEED other than `none` goes with ASK or BLOCKER. Format
problems only warn; the original is always delivered. A valid FYI card stays quiet in the watches;
every other card and all plain mail wakes them.

## The human's answers

The human should never answer the same question twice. When they answer something another agent could
need, record it straight away with their exact words, in single quotes (write each `'` as `'\''`, so
`don't` becomes `'don'\''t'`):

    coms decide <topic> '<answer>' --quote '<their exact words>'

Before you ask the human anything, search: `coms decisions <keyword>` (one word at a time works best;
`coms decisions --current` shows the latest answer per topic). Never record a password, token or other
secret: every agent reads this log and it keeps every entry.

## Checking and unsticking

- `coms status` (or `--json`) lists live leases with the time each has left, then every enrolled agent.
- `coms check <name>` exits non-zero if another agent holds it.
- A crashed holder's lease expires after 30 seconds, so nothing stays stuck.
- `coms unlock <name>` drops a stuck lease by hand, only when you are sure. It exits 1 if there was none.
- `coms identity-check` checks your holder and marker; `coms primer` prints these rules in short.

## Limits

- One machine only. Teams that land through pull requests keep their PR flow; use coms for local things.
- `git commit --no-verify` skips the backstop hook. `coms do` is the protocol; the hook is a backstop.
- Holder ids and markers are cooperative routing between your own agents, not authentication.

## Operating model (optional, for several agents)

None of this is needed to use coms. If you run many agents at once, `references/operating-model.md`
suggests optional roles (an orchestrator the human talks to, a controller that owns integration, a lead
for a second model family, a governor that reviews plans and stuck work), short briefs and reports, and safe defaults. Moving a
window to another account or chat without losing mail is in `references/handover.md`, and the helper
scripts (inbox readers and watches) are in `bin/README.md`.
