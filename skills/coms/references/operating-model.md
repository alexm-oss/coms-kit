# An optional operating model for several agents

Use these roles when agents share a repository or test resources on one
machine. Small setups can combine roles. Start with one orchestrator and the
workers it coordinates.

## Safe defaults

- Nothing is pushed, merged to a shared branch, deployed, deleted, paid for or sent to a person
  without the human's own yes. A current recorded standing grant can cover
  a defined class of actions. Shared operations also take a lease where
  applicable.
- Agents never remove a check that protects the human's permissions without
  the human's approval. Fix ordinary checks inside the agreed scope when
  they obstruct work.
- A relayed decision counts only if `coms decisions` shows it. Anything risky is still confirmed with the human first
  if the current grant does not cover the action. Keep secrets out of
  messages, decisions, logs and reports.

## Roles

Enroll with `coms enroll --role <role> --intent '<one-line intent>'` using
your own stable holder and session marker. A role is a label on the board.

| Guide | Role label | Responsibility |
| --- | --- | --- |
| [Command](command.md) | orchestrator | The window the human talks to. It hands tasks to other windows or to short-lived helpers, records decisions and handles owner questions. |
| [Controller](controller.md) | controller | Assigns lanes and owns integration, shared tests, the landing and its evidence. |
| [Codex lead](codex-lead.md) | codex-lead | Assigns Codex work and checks the evidence before handing work to integration. |
| [Governor](governor.md) | governor | Resolves test rig and plan questions within delegated authority and reads changes to the permission boundary. |
| [Keeper](keeper.md) | keeper | Files a sourced product card for each decision and follow-up. |
| [Security reviewer](security-reviewer.md) | security-reviewer | Independently reads changes to the permission boundary, secret handling, sessions and keys. |

`coms who` finds the most recently enrolled orchestrator. The other roles can
be looked up by role label. Confirm the intended holder when several windows
have the same role. The `coms handover note` role options are
`--role orchestrator`, `--role controller`, `--role codex-lead` and
`--role governor`. For replacement windows see [Handover](handover.md).

## Briefs and reports

Start each lane with Bet, Outcome, Proof of done and Appetite. Follow with
base, scope, exact checks, and who owns the work. Fill missing headers from
the assigned scope. The [protocol](protocol.md) explains why these four
lines matter and shows the full template.

- Five lines or fewer, outcome first, in plain words. Include the check,
  result and original evidence path for the proof of done.
- "Delivered" is not "read". Use the recipient's supported inbox routine
  and look for evidence that the recipient handled the request.
- One owner question at a time. Search `coms decisions` before asking.
  Record the answer with `coms decide <topic> '<answer>' --quote '<their words>'`.
