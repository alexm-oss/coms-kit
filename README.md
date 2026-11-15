# coms

coms keeps several AI agents on one machine from tripping over each other. They
take turns on anything only one of them should do at a time, see who is doing
what, and message each other.

It is a small Python tool (3.9 or newer, standard library only) plus a skill that
teaches your agent to use it. Any agent that can run shell commands can use it.
Two optional hooks make it smoother in Claude Code.

## When it helps

- **One room for your parallel agents.** `coms enroll` and `coms status` show who
  holds what and what each agent is working on. Agents message each other with
  `coms msg`, and one agent can coordinate the rest.
- **Taking turns on local things.** `coms do simulator -- <command>` makes agents
  queue for a simulator, a port, a release script or a shared checkout.
- **Guarding a local main.** In repos where you merge by hand (side projects, not
  PR-based repos), `coms do main -- git merge ...` stops two agents landing at once.
- **Never asking the human twice.** `coms decide` records the human's answer with
  their exact words; every agent checks `coms decisions` before asking.

What it won't do: coordinate between people or machines. If your team lands code
through pull requests, keep using your PR flow and deploy holds for that.

## Install

Needs python3 3.9 or newer, git 2.31 or newer (the Claude Code hooks use
`git rev-parse --path-format`), and bash 3.2 or newer, on a local filesystem with
POSIX flock (macOS or Linux). From this folder:

    ./install.sh                  # CLI + skill: ~/.local/bin/coms, ~/.claude/skills/coms
    ./install.sh --claude-hooks   # also copies the two optional Claude Code hooks

The installer never replaces anything it did not install. If something is in the
way it stops and changes nothing; `--force` moves the old file aside as
`<name>.bak-<time>` first. It never edits `~/.claude/settings.json`: with
`--claude-hooks` it prints the lines to add (see below).

The launcher runs the code in this folder, so keep the folder where it is. If you
move it, run `./install.sh` again from the new place.

**Other agents (Codex, Cursor and others):** the installer puts the skill only
where Claude Code finds it. Point your tool at `skills/coms/SKILL.md` in this
folder, or copy the `skills/coms` folder to wherever your tool loads skills from.
If your agent setup builds its skills from a list of skill folders, install the
CLI only and add this kit's `skills` folder to that list:

    ./install.sh --cli-only

Already using the earlier kit? See "Upgrading from the earlier kit" below.

## Turn on the hooks in Claude Code

`./install.sh --claude-hooks` copies two hooks to `~/.claude/hooks`:

- `coms-context` (UserPromptSubmit): in a repo marked by `coms guard`, adds a
  short reminder with this window's holder id, plus any unread coms mail, to each
  prompt. It prints nothing anywhere else.
- `coms-inbox-stop-gate.sh` (Stop): in a marked repo, stops the window from going
  idle while its inbox holds unread mail, and shows that mail once.

To switch them on, add these entries to `~/.claude/settings.json`. If `"hooks"`
or either list is missing, create it, and keep any entries already in those lists:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/coms-context", "timeout": 10}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "~/.claude/hooks/coms-inbox-stop-gate.sh", "timeout": 10}]}
    ]
  }
}
```

Both hooks are silent outside marked repos and let a stop through if anything
goes wrong, so a broken setup never traps a window.

## Use

Tell your agent to use the coms skill (in Claude Code, type `/coms` or say
"start coms"). By hand, give each terminal its own holder id and session marker
and repeat them on every command:

    export COMS_HOLDER=my-window COMS_SESSION_ID=my-window-session-1
    coms enroll --intent 'what I am doing'     # join the room
    coms guard                                 # mark this repo (once)
    coms do main -- git -C <main checkout> merge --no-ff <branch>
    coms do ship -- ./scripts/release.sh
    coms status                                # who holds what
    coms msg <holder-id> 'text'                # message another agent
    coms inbox --consume                       # read yours and mark it read
    coms inbox --peek                          # look without marking
    coms decisions <keyword>                   # before you ask the human anything
    coms decide <topic> '<answer>' --quote '<their exact words>'

`coms do` waits for its turn (up to ten minutes; `--wait 0` refuses at once),
runs the command with a heartbeat keeping the lease alive, releases, and returns
the command's exit code. If it cannot get the lease it exits 75 and runs nothing.
A crashed holder's lease expires after 30 seconds. `coms unlock <name>` is a
manual override for a lease you are sure is stuck.

`coms do` runs in the caller's folder. Name the target checkout explicitly (`git
-C <main checkout> ...`), then prove the commit landed with `git merge-base
--is-ancestor <sha> main`: from a feature worktree, a plain merge can print
"Already up to date", exit 0 and land nothing.

When the human answers a question, the agent that heard it records the answer
with `coms decide`, and every agent checks `coms decisions` before asking.
`--quote` takes the human's exact words. Keep the answer and the words in single
quotes, writing each `'` as `'\''`, so the shell passes them on unchanged.

Agent shells pipe their output, and piped inbox output defaults to peek, so an
agent names `--consume` to mark mail read or `--peek` to only look. A message is
appended to a file: it reaches a busy agent and never wakes an idle one, so wake a
peer the way your tool allows for anything it must act on.

`coms guard` marks the repo inside `.git`. Where git owns the hooks folder and no
pre-commit or pre-merge-commit hook exists yet, it also installs a backstop hook
that blocks a raw commit to main/master while someone holds the lease. Repos with
their own hooks, husky or otherwise, keep them untouched and get the mark only.

## Running many agents

`skills/coms/references/operating-model.md` is an optional way to organise one
person's several agents: an orchestrator the human talks to, a controller that
owns integration, a lead for a second model family, and a governor that reviews
plans and stuck work, with
short briefs, short reports and safe defaults (nothing is pushed, merged,
deployed, deleted, paid for or sent to a person without the human's own yes).
`references/handover.md` covers moving a window to another account or chat
without losing mail, and `skills/coms/bin/` has small inbox readers and watches.
None of it is needed for the basics above.

## How it works

A short `flock(2)` mutex guards a JSON state file in `~/.claude/coms` (or
`$COMS_DIR`). Each lease has a holder, a fencing token and a 30 second expiry,
renewed while the command runs. The room, the inboxes, per-holder read
checkpoints and the decision log are small files next to it. Inbox reads keep a
byte offset, line count and prefix hash per holder, so a restart resumes exactly
where it left off and a changed or truncated inbox refuses instead of skipping mail.

Holder ids and session markers are cooperative routing between your own agents,
not authentication. Corrupt coordination state refuses operations rather than
resetting leases; repair it from backups, never by deleting it.

## Upgrading from the earlier kit

Unpack or pull the new version into the same folder and run `./install.sh`
again (add `--claude-hooks` if you used it). It replaces the launcher, skill and
hooks the kit installed and keeps your coms state: the room, the inboxes and the
decision log. What changes for your agents:

- **Every command needs a session marker as well as a holder id.** Set
  `COMS_SESSION_ID` to a value unique to that window and repeat it on every call,
  as you already do with `COMS_HOLDER`. Claude Code windows have one already
  (`CLAUDE_CODE_SESSION_ID`). Without one, reads refuse with exit 75 and say
  which variable to set.
- **Bind an old holder once.** A holder that enrolled under the earlier kit
  reads its unread mail again after one enroll with `--adopt-legacy`:

      COMS_HOLDER=<old id> COMS_SESSION_ID=<new marker> coms enroll --adopt-legacy --intent '...'

  Until then its reads refuse with "holder is missing or unbound". A new holder
  id just enrolls.
- **A piped inbox read only peeks.** Agent shells pipe their output, so say
  `coms inbox --consume` to mark mail read, or `--peek` to only look.
- **The state folder is private.** coms now keeps `~/.claude/coms` at mode 700.

## Remove

    coms unguard                  # in each repo you guarded, before you uninstall
    ./uninstall.sh                # the launcher, skill and hooks it installed
    ./uninstall.sh --purge        # also deletes the coms state folder, if install.sh created it

It leaves alone anything it did not install. If you added the hook lines to
`~/.claude/settings.json`, take them out too.

## Limits

- One machine only.
- `git commit --no-verify` skips the backstop hook; `coms do` is the protocol.
- Leases use wall-clock time, so a large clock jump can shift an expiry.
- `coms do` does not make arbitrary commands transactional; SIGKILL, a paused
  machine or a command that escapes its process group remain limits.

## Tests

    ./run-tests.sh

It runs in an isolated home and state folder, checks syntax, runs shellcheck,
ruff and gitleaks when they are installed (and says UNSUPPORTED when they are
not), then the standard library unittest suite.

MIT licence, by Alex Maxey.
