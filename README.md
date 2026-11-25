# coms

[Start here: a day with two COMS windows](docs/GOLDEN-SETUP.md)

If you have downloaded the kit, run `./install.sh --profile pair` from the checkout first. Then open the start page above and tell your agent "start COMS" or "set up COMS".

If you have a few Claude Code windows and some Codex chats working in the same repos, coms gives them three simple things:

- **Taking turns.** Only one agent merges to `main`, deploys, or uses the simulator at a time. The others wait their turn instead of colliding.
- **A shared room.** You can see which agents are running and what each one is working on.
- **Messages and a memory of your answers.** Agents can message each other, and when you answer a question once, every agent can look it up instead of asking you again.

It is a small Python tool with no server and no account. Everything lives in a few files on your machine.

## Why you would want it

Two agents finish at the same moment. Agent A merges its branch to `main` and starts the tests. Meanwhile agent B merges its own branch into the same place. A's tests now run against code A has never seen, and A starts "fixing" B's work. Neither agent did anything wrong; they just didn't know about each other.

With coms, both run their merge through `coms do main`. B simply waits until A is done.

coms only makes agents take turns. Whether a merge or deploy should happen at all is still your call.

## Install

You need Python 3.9+, git 2.31+ and bash, on macOS or Linux.

    git clone https://github.com/alexm-oss/coms-kit.git
    cd coms-kit
    ./install.sh --profile pair

The [recommended setup](docs/recommended-setup.md) explains the optional profiles.
For the minimal core, use `./install.sh`. Inspect your current installation with `./install.sh --status`.

This adds a `coms` command and installs the skill that teaches agents the rules. It never overwrites anything it didn't install.

## See it work in a minute

In one terminal, take the `main` lock for 20 seconds:

    export COMS_HOLDER=window-a COMS_SESSION_ID=window-a-1
    coms do main -- sleep 20

In a second terminal, try to take it too:

    export COMS_HOLDER=window-b COMS_SESSION_ID=window-b-1
    coms do main -- echo "my turn"

The second one waits, then prints "my turn" the moment the first finishes. `coms status` shows who holds what.

## The few commands you'll actually use

| Run | What it does |
|---|---|
| `coms do main -- <command>` | Wait your turn, run the command, let go |
| `coms status` | Who is running, and who holds what |
| `coms msg <agent> '<text>'` | Leave another agent a message |
| `coms inbox --consume` | Read new mail and mark it delivered |
| `coms decide <topic> '<answer>' --quote '<exact words>'` | Record your answer so no agent asks again |
| `coms decisions <keyword>` | Look up a past answer |

Put the owner's words in single quotes. An apostrophe inside a quote is written as `'\''`. Credential values stay out of the decision log.

Day to day, you mostly just watch `coms status`. The agents use the rest themselves once the skill is installed.

## For agents and the curious

- [`skills/coms/SKILL.md`](skills/coms/SKILL.md) is what agents read: the rules they follow.
- [`docs/REFERENCE.md`](docs/REFERENCE.md) has every command, the Claude Code and Codex setup, how it works inside, its limits, upgrading and tests.
- [Operating model](skills/coms/references/operating-model.md) introduces the six roles and their handoffs.
- [Operator tools](skills/coms/bin/README.md) covers usage pacing, host load and scheduled job health.

## Remove

    ./uninstall.sh

## License

MIT
