---
name: coms
description: Use when several AI agents share this machine, or the user says /coms or "start coms", to take turns on risky local operations like a merge, a ship or a shared simulator, and to message each other.
---

# coms: take your turn, don't clobber

Use coms to take turns on shared operations, see who is working, and exchange messages on this machine.

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

4. Check `coms setup --status` (or `./install.sh --status` from the kit). For two model families, run `coms setup --status --profile pair`.
   Read `coms decisions --current --json "setup-$COMS_HOLDER-"` first. Skip exact topics with answer `declined`.
   Offer the remaining missing pieces once this session, one line each with the printed benefit and exact add command.
   Ask one question about the piece relevant now. Wait for a yes before adding it. Leave unanswered offers pending.
   For a no, set `piece` to its name and run `coms decide "setup-$COMS_HOLDER-$piece" declined --quote '<exact words>'`.
   Record each piece if all are declined. Skip declined pieces for the rest of this session, including in role guides.
   An empty list needs no prompt. Continue other authorised work. Endpoints, schedules and replies require their own opt-in.
   See [when each piece helps](references/operating-model.md#when-a-piece-helps) for the triggers and tools.

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

Target the main checkout with the `git -C` merge shown above. Then verify the landing:

    git -C /path/to/main/checkout merge-base --is-ancestor <your-sha> main \
      && echo LANDED || echo "NOT LANDED"

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
