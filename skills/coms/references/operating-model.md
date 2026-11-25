# An optional operating model for several agents

Start with one coordinator and its workers. Combine roles when sharing a local repository or test rig.

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

Enroll with `coms enroll --role <role> --intent '<one-line intent>'` using your stable holder and session marker. Roles label the board.

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

Start each lane with Bet, Outcome, Proof of done and Appetite, then name the base, scope, checks and writer.
Use the assigned scope to fill missing headers. The [protocol](protocol.md) gives the full template and reasons.

- Five lines or fewer, outcome first, in plain words. Include the check,
  result and original evidence path for the proof of done.
- "Delivered" is not "read". Use the recipient's supported inbox routine
  and look for evidence that the recipient handled the request.
- One owner question at a time. Search `coms decisions` before asking.
  Record the answer with `coms decide <topic> '<answer>' --quote '<their words>'`.

## When a piece helps

Use the installed skill's `bin` and `references` directories. Invoke internal modules through their public commands.
If a piece is missing, explain the benefit and offer `coms setup --add PIECE`. Wait for a yes and respect this session's declines.

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
