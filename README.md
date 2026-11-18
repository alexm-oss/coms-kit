# coms

coms is a command-line tool for anyone who runs several AI coding sessions on one
computer at once: a few Claude Code windows, some Codex chats, a script or two,
all working in the same repositories. It gives those sessions three things:

- **Turns.** `coms do main -- <command>` waits until no other session is landing
  on main, runs the command, then lets go. The same works for a deploy, a
  simulator, a phone, a port: anything only one session should use at a time.
- **A room.** `coms status` shows which sessions are active, what each one says
  it is doing, and who holds what right now.
- **Messages and a decision log.** Sessions leave each other messages, and they
  record your answers so the next session does not ask you the same question.

It is plain Python (3.9 or newer, standard library only) and it runs on one
machine. Everything it knows sits in small files under `~/.claude/coms`; there is
no server and no account. It comes with a skill file that teaches an agent the
rules, and two optional hooks for Claude Code.

## The problem

Three sessions are working in one repo, each on its own branch in its own
worktree. That part is safe. Then two of them finish in the same minute.

Session A merges its branch into main and starts the test run. While the tests
run, session B merges its branch into the same checkout. A's tests are now
running against code A has never seen. If they fail, A starts "fixing" B's
change, or reverts it. If both sessions go on to run the deploy script, whichever
finishes last decides what is live.

Neither session did anything wrong by its own lights. Each one just did not know
the other was there.

With coms, both sessions run the merge through `coms do main`. B's command waits
until A's has finished, then runs. If B cannot get a turn in time, it exits
without running anything.

coms only makes sessions take turns. It does not decide whether a merge or a
deploy is allowed; that is still your call.

## Install

Needs python3 3.9 or newer, git 2.31 or newer and bash 3.2 or newer, on macOS or
Linux, on a local disk.

    git clone https://github.com/alexm-oss/coms-kit.git
    cd coms-kit
    ./install.sh

That puts a `coms` launcher in `~/.local/bin` and the skill in
`~/.claude/skills/coms`. If `~/.local/bin` is not on your PATH, the installer
says so.

The installer never replaces anything it did not install. If something is in the
way it stops and changes nothing; `--force` moves the old file aside as
`<name>.bak-<time>` first. The launcher runs the code in the cloned folder, so
keep the folder where it is. If you move it, run `./install.sh` again.

## Try it: two terminals, one lock

Pick any git repo that is on `main` and mark it once:

    cd ~/code/my-app
    coms guard

    coms: guarding . (pre-commit, pre-merge-commit) for branches: main master

In terminal A, give the window a name, join the room, and hold `main` for twenty
seconds:

    export COMS_HOLDER=window-a COMS_SESSION_ID=window-a-1
    coms enroll --intent 'fixing the login form'
    coms do main --intent 'merging login-fix' -- sleep 20

`coms enroll` prints a short rule sheet meant for an agent, then who else is in
the room.

In terminal B, while A is still sleeping:

    export COMS_HOLDER=window-b COMS_SESSION_ID=window-b-1
    coms enroll --intent 'updating the footer'
    coms status

    my-app:main: held by window-a (merging login-fix) [27s left]
    Enrolled:
      window-a on my-app (fixing the login form)
      window-b on my-app (updating the footer)

`27s left` is the lease, not the command. A lease lasts 30 seconds and is renewed
for as long as the command runs, so a session that crashes frees the lock within
30 seconds.

Still in B, try to jump the queue:

    coms do main --wait 0 -- echo 'should not run'

    coms: my-app:main held by window-a; did not run

That exits 75 and runs nothing. A raw commit on main is stopped too:

    git commit --allow-empty -m 'raw commit'

    coms: 'main' in this repo is held by another actor right now.
    coms: this pre-commit is blocked so you do not clobber their landing.
    coms: check 'coms status', or land safely via 'coms do main -- git ...'.

Now do it properly. This waits for A to finish, then runs:

    coms do main -- echo 'my turn'

    my turn

`coms do` returns the exit code of the command it ran.

## The commands you will use

Every command needs a holder id (`COMS_HOLDER`, a name only this session uses)
and a session marker (`COMS_SESSION_ID`). An agent's shell starts fresh on every
call, so agents put both in front of each command:

    COMS_HOLDER=codex-login-7f3a COMS_SESSION_ID=codex-login-7f3a-s1 coms status

### Taking turns

    coms do main -- git -C /path/to/main/checkout merge --no-ff my-branch
    coms do ship -- ./scripts/release.sh
    coms do simulator -- <test command>

`coms do <name> -- <command>` waits for its turn (up to ten minutes; `--wait 0`
refuses at once), runs the command, releases, and returns the command's exit
code. If it cannot get the lease it exits 75 and runs nothing.

The name is yours to choose. A bare name gets the repo in front (`main` becomes
`my-app:main`), and it resolves the same way from the main checkout and from
every linked worktree, so all sessions queue on one lock. Put a colon in the name
(`shared:simulator`) for something shared across repos.

`coms do` runs the command in the folder you are in. From a feature worktree, a
plain `git merge --no-ff my-branch` merges the branch into itself, prints
"Already up to date", exits 0 and lands nothing. So name the target checkout with
`git -C`, then check the result:

    git -C /path/to/main/checkout merge-base --is-ancestor <sha> main && echo LANDED

Work no other session touches, such as editing files or committing on your own
branch, needs no lease.

### Seeing the room

| command | what it does |
|---|---|
| `coms enroll --intent '<what you are doing>'` | join the room, or update your line in it; prints who else is there |
| `coms status` | live leases with the time each has left, then every enrolled session (`--json` for scripts) |
| `coms check <name>` | exits 3 if another session holds that lease, 0 if it is free |
| `coms unlock <name>` | drops a stuck lease by hand; exits 1 if there was none |
| `coms guard` / `coms unguard` | mark or unmark a repo (see below) |

`coms guard` writes a marker inside the repo's `.git`. Where git owns the hooks
folder and the repo has no pre-commit or pre-merge-commit hook yet, it also
installs the backstop hook you saw above, which blocks a raw commit to main or
master while another session holds the lease. Repos with their own hooks, husky
or otherwise, keep them untouched and get the marker only.

### Messages

    coms msg window-b 'rebase on main before you merge, I just landed the login fix'
    coms inbox --consume        # read your new mail and mark it read
    coms inbox --peek           # look without marking anything
    coms inbox --all            # full history

A message is a line added to a file. A session that is working sees it the next
time it reads its inbox. A session that is idle never does, so for anything a
peer must act on, also wake it the way your tool allows. When output is piped, as
it is in an agent's shell, an inbox read with no flag only peeks; say `--consume`
to mark mail read.

### Your answers, recorded once

    coms decide release-date 'Ship on Friday' --quote 'friday is fine, not before'
    coms decisions release

    release-date: Ship on Friday (2026-11-17 19:22, window-a in my-app) "friday is fine, not before"

When you answer a question, the session that heard it records the answer with
your exact words. Every session searches `coms decisions <keyword>` before it
asks you anything. Keep the answer and the quote in single quotes, writing each
`'` as `'\''`, so the shell passes your words on unchanged. Never record a
password or a token: every session can read this log and it keeps every entry.

A session can also park a question for you with `coms ask-owner <topic>
'<question>'`; `coms asks --open` lists the ones still waiting.

### The rest

`coms who` prints the coordinating session's id, if one enrolled with
`--role orchestrator`. `coms primer` prints the rules in short, for pasting into
any agent. `coms identity-check` checks your holder id and session marker.
`coms --help` lists everything, including the handover and inbox maintenance
commands covered in `skills/coms/references/handover.md`.

## With Claude Code

`./install.sh` puts the skill where Claude Code finds it. In a session, type
`/coms` or say "start coms": the agent enrolls, reads the room and follows the
rules in `skills/coms/SKILL.md` from then on.

Two optional hooks remove the need to ask. Install them with:

    ./install.sh --claude-hooks

- `coms-context` (UserPromptSubmit): in a repo marked by `coms guard`, it adds a
  short reminder to each prompt with this window's holder id
  (`claude-<first 8 characters of the session id>`) and the exact `coms do`
  lines to use, plus any unread coms mail. Outside marked repos it prints
  nothing.
- `coms-inbox-stop-gate.sh` (Stop): in a marked repo, it stops the window from
  going idle once while its inbox holds unread mail, and shows that mail.

The installer never edits `~/.claude/settings.json`. It prints the two entries to
add, with your paths filled in. They look like this:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "/path/to/.claude/hooks/coms-context", "timeout": 10}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "/path/to/.claude/hooks/coms-inbox-stop-gate.sh", "timeout": 10}]}
    ]
  }
}
```

If `"hooks"` or either list is missing, create it, and keep any entries already
in those lists. Both hooks let a stop through if anything goes wrong, so a broken
setup never traps a window.

In the Claude desktop app, one session can wake another that is idle.
`skills/coms/references/claude-desktop.md` explains how.

## With Codex and other agents

Any agent that can run shell commands can use coms. The installer only puts the
skill where Claude Code looks, so for another tool do one of these:

- point the tool at `skills/coms/SKILL.md` in the cloned folder;
- copy the `skills/coms` folder to wherever the tool loads skills from;
- or install the launcher alone with `./install.sh --cli-only` and paste the
  output of `coms primer` into the agent's instructions.

There are no hooks for these tools, so the agent makes its own holder id and
session marker, as the skill tells it to, and repeats them on every command. For
the session marker coms reads `COMS_SESSION_ID`, then `CLAUDE_CODE_SESSION_ID`,
then `CODEX_THREAD_ID`, and uses the first one that is set.

An agent that runs unattended will not notice mail on its own. Write the inbox
checks into its instructions: before it takes a lease, before each task, after
each landing.

## Running many sessions

`skills/coms/references/operating-model.md` describes one optional way to
organise several sessions: an orchestrator you talk to, a controller that owns
merging and testing, a lead for a second model family, and a governor that
reviews plans and stuck work. A session takes a role with
`coms enroll --role <role>`. Roles are labels in the room; coms gives a role no
extra power. The same file sets out safe defaults: nothing is pushed, merged,
deployed, deleted, paid for or sent to a person without your own yes.

`skills/coms/references/handover.md` covers moving a session to another account
or chat without losing mail, and `skills/coms/bin/` holds small inbox readers and
watches. None of this is needed for the basics above.

## How it works

A short `flock(2)` mutex guards a JSON state file in `~/.claude/coms` (or
`$COMS_DIR`). Each lease has a holder, a fencing token and a 30 second expiry,
renewed while the command runs. The room, the inboxes, per-holder read
checkpoints and the decision log are small files next to it. Inbox reads keep a
byte offset, line count and prefix hash per holder, so a restart resumes exactly
where it left off, and a changed or truncated inbox refuses instead of skipping
mail. The state folder is kept at mode 700.

Holder ids and session markers are cooperative routing between your own
sessions, not authentication. Corrupt state refuses operations rather than
resetting leases; repair it from backups, never by deleting it.

## Limits

- One machine only. coms does not coordinate between people or between
  computers. If your team lands code through pull requests, keep that flow and
  use coms for the local things.
- `coms do` never grants permission. It only makes sessions take turns.
- `git commit --no-verify` skips the backstop hook, and repos with their own
  hooks have no backstop at all. There, `coms do` is the whole protocol.
- Leases use wall-clock time, so a large clock jump can shift an expiry.
- `coms do` does not make a command transactional. SIGKILL, a paused machine or
  a command that escapes its process group remain limits.

## Upgrading from the earlier kit

Pull the new version into the same folder and run `./install.sh` again (add
`--claude-hooks` if you used it). It replaces the launcher, skill and hooks the
kit installed and keeps your coms state: the room, the inboxes and the decision
log. What changes for your agents:

- **Every command needs a session marker as well as a holder id.** Set
  `COMS_SESSION_ID` to a value unique to that window and repeat it on every
  call, as you already do with `COMS_HOLDER`. Claude Code windows have one
  already (`CLAUDE_CODE_SESSION_ID`). Without one, reads refuse with exit 75.
- **Bind an old holder once.** A holder that enrolled under the earlier kit
  reads its unread mail again after one enroll with `--adopt-legacy`:

      COMS_HOLDER=<old id> COMS_SESSION_ID=<new marker> coms enroll --adopt-legacy --intent '...'

  Until then its reads refuse with "holder is missing or unbound". A new holder
  id just enrolls.
- **A piped inbox read only peeks.** Say `coms inbox --consume` to mark mail
  read, or `--peek` to only look.

## Remove

    coms unguard                  # in each repo you guarded, before you uninstall
    ./uninstall.sh                # the launcher, skill and hooks it installed
    ./uninstall.sh --purge        # also deletes the coms state folder, if install.sh created it

It leaves alone anything it did not install. If you added the hook lines to
`~/.claude/settings.json`, take them out too.

## Tests

    ./run-tests.sh

It runs in an isolated home and state folder, checks syntax, runs shellcheck,
ruff and gitleaks when they are installed (and says UNSUPPORTED when they are
not), then the standard library unittest suite.

MIT licence, by Alex Maxey.
