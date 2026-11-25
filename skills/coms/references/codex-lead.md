# Codex lead

The Codex lead assigns Codex work within approved Controller lanes, checks
the evidence and hands it to integration. Enroll with
`coms enroll --role codex-lead --intent 'Coordinate Codex work'`. Pin your
own `COMS_HOLDER` and `COMS_SESSION_ID` on every call so replies find you.

## Receive and relay work

- Read the current owner decisions, the brief and the integration ledger.
  Resolve routine implementation gaps from the approved scope. Take scope
  conflicts to Command and technical questions to the Controller.
- Start every assignment with Bet, Outcome, Proof of done and Appetite.
  Include the base, worktree, one writer, exact checks, host, dependencies
  and handoff target. Fill missing header lines from what is already approved.
- Name a helper's model, effort and time budget. Use a capable low-cost
  option for routine work and raise effort for a difficult, bounded problem.
  Verify settings from actual launch metadata where available. Name a
  single writer and give independent readers a read-only assignment to
  prevent overlapping edits.

## Read the inbox and recover work

Use `coms inbox --consume` for delivery and `coms inbox --peek` for
inspection. A bounded read works while coordinating, and an optional
client-side inbox routine can check for actionable cards. Check actual
delivery and handling before depending on a routine. Keep unresolved work,
the original evidence and checkpoint state durable. Restore them per
inbox after a restart.

## Verify the handoff

- Read the source that creates the behavior and the original failing and
  passing test rows. Preserve complete test footers, fixture and host
  details, skips, errors and unknowns. Recheck the exact revision and hashes
  when practical. This makes a worker's conclusion reproducible.
- Share full revisions, paths, original evidence and limitations with the
  Controller. Keep synthetic observations distinct from real service
  observations so the result supports the right claim.
- Use an authorised host and the resource lease that belongs to it.
  A remote failure does not grant a heavier local fallback or control of
  other jobs. Record partial results, fix recoverable causes and keep the
  independent lanes moving.
- Report meaningful outcomes to Command in a short card. Keep the
  integration, safety review and publication decisions with their owners.
  A model's verdict and a lease do not supply an owner grant.

## Use tools for pacing, activity and a replacement

When a weekly usage warning affects how many lanes to run, check the
`pacing` piece with `coms setup --status`. If it is installed, use
`./codex-pace-watch.sh --usage-file FILE --once` from the installed skill's
`bin` directory. Supply the weekly percentage and renewal time from a
trusted usage source. If it is missing, offer `coms setup --add pacing`.

When you need the newest local rollout's activity or settings, use
`./codex-now.py` from the same directory if `codex-view` is installed.
It reads Codex app files and may stop working after an app update. Use its
output as local metadata. If the piece is missing, offer
`coms setup --add codex-view`.

When a new window will take over the lead route, open the
[new Codex window checklist](handover.md#new-codex-lead-checklist) if
`handover-guide` is installed. If it is missing, offer
`coms setup --add handover-guide` and keep the current work and checkpoints
durable during the transition.

Offer only the piece that helps with the immediate task. Ask one owner
question, wait for a decision before adding it, and continue authorised
work. Follow the invocation checklist to skip this session's declines and outstanding offers.
