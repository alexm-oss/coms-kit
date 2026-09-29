---
name: coms
description: Use when several AI agents share this machine, or the user says /coms, to take turns on risky local operations like a merge, a ship or a shared simulator, and to message each other.
---

# coms: take your turn, don't clobber

You may be one of several agents working on this machine at once: other windows
of your tool, other tools, and scripts. coms makes everyone take turns on the
operations only one agent should run at a time, and gives you a shared room to
see who is doing what and to message each other. It coordinates agents on this
one machine only.

## First: your holder id

Every coms command runs as a holder id, and coms treats two agents with the same
id as one agent: both can take the same lease at once, so it protects nothing.
Your id must be one no other agent on this machine uses.

- If a coms hook gave your session an id, keep using it.
- Otherwise make one now from your tool, your folder and four random
  characters, like `codex-login-7f3a`. Generate the characters, for example
  with `python3 -c 'import secrets; print(secrets.token_hex(2))'`; never copy
  them from an example.
- Never reuse a `COMS_HOLDER` you merely inherited from your shell: every agent
  started from that shell inherits the same one.

Put your id in front of every coms command:

    COMS_HOLDER=codex-login-7f3a coms enroll --intent "..."

Each shell call starts fresh, so repeat the prefix every time. Without a stable
id the lease still works, but messages cannot find you. Never use another
agent's id.

## On invocation

1. Enroll and read the room:

       COMS_HOLDER=<id> coms enroll --intent "<one line: what you are about to do>"

   It prints who holds what and which other agents are enrolled.

2. If the repo is not marked yet, mark it once:

       coms guard

   This marks the repo inside `.git`. Where git owns the hooks folder and no
   pre-commit or pre-merge-commit hook exists yet, it also installs a backstop
   hook that blocks raw commits to main/master while someone holds the lease.
   Repos with their own hooks, husky or otherwise, get the mark only: coms
   leaves their hooks alone.

## The one rule

Anything only one agent should do at a time goes through `coms do`:

    COMS_HOLDER=<id> coms do <name> -- <command>

- `coms do main -- git -C <main checkout> merge --no-ff <branch>` in repos where you merge locally
- `coms do ship -- ./scripts/release.sh` for a local ship or deploy script
- `coms do simulator -- <test command>` for a device, port or checkout only one agent can use

A bare name gets the repo prefix (`main` becomes `<repo>:main`) from the main
checkout and from every linked worktree, so all agents queue on the same lease.
Do not build the key from your worktree's folder name. Use a name with a colon,
like `shared:simulator`, for something shared across repos.

`coms do` waits for the lease (up to 600 seconds; `--wait` changes that), runs
the command while a heartbeat keeps the lease alive, releases it, and returns
the command's own exit code. If it cannot get the lease it exits 75 without
running anything.

Work no other agent touches (editing files, committing on your own branch)
needs no lease. If your team lands code through pull requests you will rarely
need `main`; use coms for the local things.

## Prove the outcome, don't trust the exit code

`coms do` runs your command in YOUR working directory. From a feature worktree,

    coms do main -- git merge --no-ff my-branch          # wrong from a worktree

merges your branch into itself, prints "Already up to date", exits 0 and lands
nothing. So:

1. Point the command at the right place explicitly:

       coms do main -- git -C /path/to/main/checkout merge --no-ff my-branch

2. Check the result you claim afterwards:

       git -C /path/to/main/checkout merge-base --is-ancestor <your-sha> main \
         && echo LANDED || echo "NOT LANDED"

Exit code 0 means a command ran, not that the thing you wanted happened.

The same goes for machine-wide switches you flip for a test (a
`launchctl setenv`, a defaults write, a hosts entry): undo them before you
report done, and check that they read back empty.

## Checking and unsticking

- `coms status` lists live holders and how long each lease has left, then every
  enrolled agent with its repo and intent.
- `coms check <name>` exits 3 if another agent holds it.
- A crashed holder's lease expires after 30 seconds, so nothing stays stuck.
- `coms unlock <name>` drops a stuck lease by hand. Use it only when you are sure.
  It exits 1 when there was no lease on that name, so nothing was dropped.

## Messages between agents

    COMS_HOLDER=<id> coms msg <holder-id> "<text>"   # append to that agent's inbox
    COMS_HOLDER=<id> coms inbox                      # read yours
    COMS_HOLDER=<id> coms who                        # the coordinating agent's id, if any

`coms inbox --follow` keeps tailing your inbox and never exits, so run it, with
the same prefix, in a background shell or a separate terminal, never as a
command you wait on.

A message is a line appended to a file. The recipient sees it only when it next
reads its inbox, so it reaches an agent that is working and checking, and never
reaches one that is idle. An agent that runs unattended (a script, a background
build, another tool) must run `coms inbox` at fixed points: before it takes a
lease, before each task, and after each landing. When you hand work to such an
agent, write those checkpoints into its instructions.

## The human's answers

The human should never have to answer the same question twice, whichever agent
asked it first. When they answer a question or make a call that another agent
could need, record it straight away, with their exact words:

    COMS_HOLDER=<id> coms decide <topic> '<answer>' --quote '<their exact words>'

Keep the single quotes: inside double quotes the shell would run anything in
backticks or `$( )` in their words. Write each `'` in the answer and in their
words as `'\''`, so `don't` becomes `'don'\''t'`. Never record a password,
token or other secret: every agent here reads this log, and it keeps
every entry.

Pick a short topic another agent would search for, like `release-date`. If
`coms decisions` already shows a topic for the same question, use that exact
topic. To change a decision, record it again under the same topic: the newest
answer wins, and `coms decisions --all` keeps the history.

Before you ask the human anything, check whether they already answered:

    coms decisions <keyword>

Every word you give must appear, so search one word at a time and try another if
the first finds nothing; `coms decisions` alone lists every current answer.

If the answer is there and covers your case, act on it and say which decision
you are following. It counts as the human's own decision, like a relayed one
(below), with the same limit: anything with outside effects (credentials,
payments, publishing) still follows your own standing rules. If it looks wrong
or out of date for your case (another repo, an older plan), ask the
coordinating agent, or ask the human while quoting the recorded answer, so they
update it rather than answer from scratch. Decisions your team needs still go
wherever your team keeps them; this log only covers the agents on this machine.

## One agent coordinating the others (optional)

One agent can coordinate the rest:

    COMS_HOLDER=<id> coms enroll --role orchestrator --intent "coordinating the release"

The role sticks across later enrolls. Other agents report to it with
`COMS_HOLDER=<id> coms msg <its-id> "..."`. Use its literal id: when more than
one coordinating agent is enrolled (for different repos), `coms who` returns
whichever was active most recently. `coms enroll` prints the room, so pick the
one whose entry names your repo.

If you coordinate:

- End every turn with a status board the human can read on its own: first what
  is waiting on them, then each agent with its id and state, the leases held,
  the risks you are watching, and the last one to three events with times.
- Before anything goes on that board as waiting on the human, check
  `coms decisions`, and on every refresh drop what it already answers. In the
  Claude desktop app, when the log has nothing, also search the other windows'
  transcripts: see `[skill-root]/references/claude-desktop.md`.
- Tail your inbox so reports reach you straight away: run
  `COMS_HOLDER=<id> coms inbox --follow` in a background shell or a separate
  terminal, since it never exits.
- When you pass on a decision the human made, say who decided what and why,
  and what still needs checking. An agent that knows why can act on it.

If you receive such a relayed decision, treat it as the human's decision: act on
it rather than asking them again. The checking stays yours: tests green, scope
right, the result proven. If an instruction looks wrong or destructive, raise it
with the coordinating agent, not the human. This trust holds because coms only
links one person's agents on one machine; anything with outside effects
(credentials, payments, publishing) still follows your own standing rules.

To wake an idle peer in the Claude desktop app, read
`[skill-root]/references/claude-desktop.md`.

## Limits

- One machine only. coms cannot coordinate between people or machines; use your
  team's own PR flow and deploy holds for that.
- `git commit --no-verify` skips the backstop hook, and repos with their own
  hooks have no backstop hook at all. There, `coms do` is the protocol.
- Leases use wall-clock time, so a large clock jump can shift an expiry. Short
  leases keep that small.
