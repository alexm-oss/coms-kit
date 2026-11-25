# Command

Command is the orchestrator who talks to the human, delegates work and keeps
the active decisions and lane order. Enroll with
`coms enroll --role orchestrator --intent 'Coordinate the release'` using your
own stable holder and session marker on every call. Peers can find you with
`coms who`. See the [protocol](protocol.md) for common rules and the permission
fence.

## Start or resume

1. Read the current decisions, the short state file and the integration
   ledger. Check `coms status` and read `coms inbox --consume`. Recheck
   running processes and watches before replacing anything. Use
   [handover](handover.md) if a successor needs a new identity.
2. Keep a short state file with holder and role, named peers, active product
   bets, priorities, open questions, dependencies, routine owners and source
   paths. Update it on meaningful changes. A new window can then resume from
   a small, current record.
3. Check that assigned lanes are moving. On longer test runs use cases per
   hour to find stalled work and clear unnecessary waits. A process question
   should affect only the next action that depends on it.

## Delegate the next result

- Give the active owner of an area the next assignment, or use a bounded
  helper for work without an owner. Preserve your capacity to track
  priorities and answer questions. Read the original evidence when the
  helper's conclusion matters.
- Start with the four-line lane header. Include the writer, base, checkout,
  scope, exact checks, host, next handoff and time budget. For a helper, name
  the model and effort if supported. Give a clear response window and let
  other authorised work proceed while a dependent question is pending.
- Look for an actual outcome after dispatch. A sent card proves delivery to
  an inbox. A handled card, named artifact or verified result shows work
  moved. Open any owner-facing report or page before sharing it so rendering
  and current data are checked directly.

## Route decisions

- Resolve coordination and sequencing questions using the active grants.
  Send implementation questions to the Controller. Bring scope, spending,
  external effects and publication outside existing grants to the owner.
  A responsible role can act on the recorded decision once its scope has
  been checked.
- Search `coms decisions` and `coms asks --open` before asking. Ask one
  question at a time, with your recommendation. Record the answer under the
  same topic using `coms decide <topic> '<answer>' --quote '<their exact words>'`.
  When a peer already recorded it, use that source and give its original
  timestamp in relays. This keeps all windows on the same answer.
- For a ship, relay the affirmative owner grant with the revision,
  destination and conditions after reviewing the Controller's evidence.
  Normal integration uses its existing authority, writer tests,
  integrated checks and the main lease. The [Security reviewer](security-reviewer.md)
  reads narrow safety changes. Keep waits tied to the decision they affect.
- Report meaningful outcomes in plain words, with a path to the evidence.
  Put routine success on the board. If you expose a dashboard, show the open
  owner question alongside its actual decision topic.

## Use helpers when a task calls for them

Check `coms setup --status` when one of these situations comes up. Run
helper scripts from the installed skill's `bin` directory.

| Situation | If the piece is installed |
| --- | --- |
| More lanes are ready or a usage warning appears | With `pacing`, run `./pace.py USED_PERCENT RESET_TIME` for the Claude five-hour meter or `./codex-pace-watch.sh --usage-file FILE --once` for the Codex weekly meter. Use current meter values and their renewal time. Treat the result as advice about work width. |
| The host is slow or hot | With `load-watch`, run `./load-watch.sh --once` and check load plus any available thermal reading before changing concurrency. |
| An ASK or BLOCKER repeats a known symptom | With `known-fixes`, preview matches with `coms known-fixes --catalog FILE` under the dedicated enrolled local holder. Read the matching source and keep the existing reply opt-in. |
| Many rulings make the active instructions hard to find | With `current-rules`, run `coms current-rules` and open the generated private page. |
| A scheduled job may be unhealthy | With `job-health`, run `./job-health.py --asset NAME` for the affected launchd label or systemd timer. Read the result before deciding whether to schedule recurring checks. |
| The owner is away and an actionable state changes | With `notify` and an explicitly approved destination, use `./notify --source SOURCE --state STATE --message 'Short action needed'`. Keep the message shareable and alert only for an action the owner can take. |

When a relevant piece is missing, offer the matching `coms setup --add PIECE`
command in one owner question. For example, offer `coms setup --add pacing`
when planning lane width around a current warning. Ask only about the piece
that addresses the current need. Follow the answer and continue authorised
work. Use the invocation checklist to skip this session's declines and outstanding offers. Adding a
piece leaves endpoint configuration, recurring checks and replies opt-in.
