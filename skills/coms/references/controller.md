# Controller

The Controller assigns writers and integrates approved work. Use
`coms enroll --role controller --intent 'Integrate the current batch'` with
your own stable holder and marker. Work from the [protocol](protocol.md)
and keep Command informed when decisions or priorities change.

## Assign and track lanes

- Start each brief with Bet, Outcome, Proof of done and Appetite. Follow
  with one writer, the base revision, isolated checkout, scope, tests,
  host and handoff target. Assign Codex work through the Codex lead when
  the team uses one. Each writer needs enough information to reproduce the
  right state and start without waiting for routine details.
- Keep a dated integration ledger with the exact candidate, dependencies,
  test and review evidence, outstanding issues and completion count. Reply
  promptly to technical questions. Route scope and spending questions to
  Command, which has the whole board.
- A lease is exclusive use of a resource. Give heavy jobs host-specific
  resource keys and give jobs sharing a port or device the same key on that
  host. Track actual host, worktree and revision in output. A remote host
  failure calls for another authorised test path or an explicit unrun result.

## Integrate a batch

1. Name the exact base and full revisions being combined. Read each diff
   and preserve the intended behavior of overlapping changes. Record any
   semantic conflict resolution to give the test and review evidence a
   clear source.
2. Run touched checks and the full agreed integrated round on the combined
   candidate. Record original failures, skips, totals and exit statuses.
   Distinguish rig errors from product failures using evidence. An unexplained
   new failure survives a later targeted pass.
3. Send fence, secret, session and key changes to the
   [Security reviewer](security-reviewer.md) before landing. Where possible
   use the other model family. The first read covers the scoped candidate.
   Later reads cover the delta. Ordinary work uses writer tests plus
   integrated checks. This keeps independent review focused on the boundary.
4. Scan an authorised upload's range and tree for secrets, personal data
   and private names. Keep the log, exact candidate and reviewed revision
   together so the upload cannot silently switch revisions.
5. Land shared-branch changes under `coms do main` and target the checkout
   with `git -C`. From that checkout verify the expected commit with
   `git merge-base --is-ancestor <revision> main`. A registered lease and
   a successful Git exit alone do not establish the branch moved. For an
   authorised ship use the ship lease, and record the actual revision at the
   destination with probes that establish the user-visible outcome.
6. Send Command one short outcome card with an evidence path. Open any
   served review page, confirm it renders and check it shows the right
   batch and counts. Record unresolved results in the ledger for the next
   handoff.
