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

What it won't do: coordinate between people or machines. If your team lands code
through pull requests, keep using your PR flow and deploy holds for that.

## Install

Needs python3 3.9 or newer and git 2.31 or newer. From this folder:

    ./install.sh                  # CLI + skill: ~/.local/bin/coms, ~/.claude/skills/coms
    ./install.sh --claude-hooks   # also copies the two optional Claude Code hooks

The installer never replaces anything it did not install. If something is in the
way it stops and changes nothing; `--force` moves the old file aside as
`<name>.bak-<time>` first. It never edits `~/.claude/settings.json`: with
`--claude-hooks` it prints the lines to add.

The launcher runs the code in this folder, so keep the folder where it is. If you
move it, run `./install.sh` again from the new place.

**Other agents (Codex, Cursor and others):** the installer puts the skill only
where Claude Code finds it. Point your tool at `skills/coms/SKILL.md` in this
folder, or copy the `skills/coms` folder to wherever your tool loads skills from.
If your agent setup builds its skills from a list of skill folders, install the
CLI only and add this kit's `skills` folder to that list:

    ./install.sh --cli-only

## Use

Tell your agent to use the coms skill (in Claude Code, type `/coms`). By hand:

    export COMS_HOLDER=my-window               # a name only this terminal uses, so messages find you
    coms enroll --intent "what I'm doing"      # join the room
    coms guard                                 # mark this repo (once)
    coms do main -- git -C <main checkout> merge --no-ff <branch>
    coms do ship -- ./scripts/release.sh
    coms status                                # who holds what
    coms msg <holder-id> "text"                # message another agent
    coms inbox                                 # read yours

`coms do` waits for its turn, runs the command with a heartbeat keeping the lease
alive, releases, and returns the command's exit code. If it cannot get the lease
it exits 75 and runs nothing. A crashed holder's lease expires after 30 seconds.

`coms guard` marks the repo inside `.git`. Where git owns the hooks folder and no
pre-commit or pre-merge-commit hook exists yet, it also installs a backstop hook
that blocks a raw commit to main/master while someone holds the lease. Repos with
their own hooks, husky or otherwise, keep them untouched and get the mark only.

## How it works

A short `flock(2)` mutex guards a JSON state file in `~/.claude/coms` (or
`$COMS_DIR`). Each lease has a holder, a fencing token and a 30 second expiry,
renewed every 15 seconds while the command runs. The room and the inboxes are
small files next to it.

## Remove

    coms unguard                  # in each repo you guarded
    ./uninstall.sh                # the launcher, skill and hooks it installed
    ./uninstall.sh --purge        # also deletes the coms state folder, if install.sh created it

It leaves alone anything it did not install. If you added the hook lines to
`~/.claude/settings.json`, take them out too.

## Limits

- One machine only.
- `git commit --no-verify` skips the backstop hook.
- Leases use wall-clock time, so a large clock jump can shift an expiry.

## Tests

    ./run-tests.sh

MIT licence, by Alex Maxey.
