# An optional operating model for several agents

You do not need any of this to use coms. It is one way to organise things when one person runs
several agents at once, for example a few Claude Code windows and a few Codex chats on one machine.
Take what helps and ignore the rest.

## Safe defaults

These hold whatever roles you use, and no role or relayed message overrides them:

- Nothing is pushed, merged to a shared branch, deployed, deleted, paid for or sent to a person
  without the human's own yes, given in that window.
- Agents never remove a check, a test, a hook or an approval step on their own. If one seems to be in
  the way, they say so and ask.
- A relayed decision counts only if `coms decisions` shows it, recorded by the window the human talked
  to. Anything risky is still confirmed with the human first.
- Secrets never go into messages, decisions, logs or reports.
- When unsure, stop and ask. A wrong guess costs more than a question.

## Roles

Each role is a window that enrolls with `coms enroll --role <role> --intent '...'`. Roles are labels on
the board; coms does not give a role any extra power.

- **Orchestrator** (`--role orchestrator`): the window the human talks to. It hands tasks to other
  windows or to short-lived helpers rather than doing long work itself, so its context stays free to
  remember the room: who is doing what, what the human decided, what is waiting on them. Other windows
  find it with `coms who`.
- **Controller** (`--role controller`): owns integration. It writes the briefs, reads what comes back,
  merges work under `coms do main`, runs the full tests and proves each landing with
  `git merge-base --is-ancestor`.
- **Lead for a second model family** (`--role codex-lead`): for example a Codex chat that starts and
  checks Codex workers. It hands finished work, with its evidence, to the controller.
- **Plan reviewer** (`--role governor`): reviews test plans, risky changes and stuck work before they
  go on, and says plainly what it would change. It advises; the human decides.

`coms handover note --role` accepts exactly these four: orchestrator, controller, codex-lead and
governor. `coms enroll --role` takes any label, but only these four can be handed over.

Small setups can merge roles. One orchestrator plus workers is a perfectly good start.

## Briefs

Every task handed to another window states, in a few lines:

- the outcome: what the human or a user can see when it is done;
- the proof of done: the command and output that show it, never a say-so;
- the scope: the branch or worktree, the files, and what is out of bounds;
- who to report to, and how long to wait for an answer before carrying on with other work.

## Reports

- Five lines or fewer, outcome first, in plain words. Put the detail in a file and name the file.
- Evidence is a quoted command, its exit code and its counts. "Exit 0" alone proves only that a
  command ran.
- "Delivered" is not "read". `coms msg` appends to a file; a window acts on it only when it reads its
  inbox. Wake the window the way your tool allows, and report done only after its own reply.

## Talking to the human

- One question at a time, short, with your recommendation. Check `coms decisions` first.
- Record each answer with their exact words: `coms decide <topic> '<answer>' --quote '<their words>'`.
- When you pass a decision on, say who decided, when, and what still needs checking.
