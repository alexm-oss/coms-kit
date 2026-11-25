# Handing over a COMS window

Use a handover when a replacement window takes over work other agents send to a
leaving window. The holding window keeps approved work moving while the
successor gets ready. Use the same local COMS store throughout. The human
handles any sign-in. Existing grants and scope still apply.

The examples use shell variables for paths and holders you supply. Pin your
own `COMS_HOLDER` and `COMS_SESSION_ID` on every COMS call, including reads.

## No replay, rotate instead

Use `coms handover relay "$note"` from the holding window. It sends a pointer
to each new range of originals, the open loops, any supplied held delta and a
reference to `coms decisions --current`. Read the pinned bytes from the source
path, check the `sha256` digest and use the original sender and timestamp. An
inbox full of copied history obscures live requests and discards the useful
distinction between a new message and a recovered original. Relay progress is
tracked separately for each destination.

To shrink a large live inbox while keeping the same holder, rotate it outside
any active handover. Use the following from the coordinated repository, with
the appropriate holder and session set for the operator.

    coms do main -- coms inbox-rotate "$holder" --dry-run
    coms do main -- coms inbox-rotate "$holder"

Rotation archives the full inbox under `COMS_DIR/inbox-archive`, carries the
undelivered bytes forward and rebases the delivery checkpoint. It keeps the
originals available by path and byte range. Settle or record pending
dependencies before rotation when handled state is behind delivery. Keep the
archive and its rotation record together. For an interruption, use
`coms do main -- coms inbox-rotate "$holder" --recover` and inspect its result.
Rotation preserves a holder's route. A handover creates a new holder's route.

## Durable state and identity

- Use a private, persistent `COMS_HANDOVER_DIR` on the local machine. Its default
  is `~/.local/share/coms/handovers`. Notes are atomically written mode 600
  files in a mode 700 directory. Use regular files and non-symlinked paths. The
  separate `COMS_DIR` defaults to `~/.claude/coms`.
- Use a unique, stable holder and session marker for each window.
  `COMS_SESSION_ID` overrides an inherited native marker. A foreign or retired
  holder cannot be taken over. The binding survives the four-hour board
  listing timeout. Markers prevent ordinary routing errors among cooperative
  processes. They are copyable and supply no account or sender authentication.
- A window with its own old unmarked record can enroll with `--adopt-legacy`.
  For its own malformed record, `--repair-enrollment` preserves a private
  backup. Resolve old routes and review any legacy delivery positions before
  assuming earlier work was handled.
- Keep open loops and the note within their 1 MiB limits. Larger originals
  stay in place with byte and hash pointers. If preparation reports a size
  problem, reduce summaries or move evidence into named files while preserving
  unresolved originals and dependencies.

## Leaving side checklist

1. Identify a holding window by literal holder and scope. It enrolls with its
   own role and marker before preparation.
2. Finish your leased operation at its safe boundary. Another holder's lease
   belongs to that holder. Save a private JSON file with five lists named
   `open_work`, `owner_questions`, `routines`, `pending_handoffs` and
   `evidence`. Record the bounds of grants, full revisions, paths, original
   timestamps and evidence. For routines, include their owners, schedules,
   saved checkpoints, and the work their processes are doing. Omit credentials,
   cookies, tokens and sensitive values.
3. Optionally record handled work before taking the note. In a private
   directory with `umask 077`, save a receipt. Read each original and handle it
   or record its exact pending dependency in the loops file before acknowledging.

       coms inbox --peek --receipt > "$receipt"
       coms checkpoint-ack "$receipt" --pending "$loops"

   Without that acknowledgement, recovery conservatively includes unhandled
   originals and may include work you already completed.
4. Prepare the note. Supported roles are orchestrator, controller, codex-lead
   and governor. Use the role enrolled by the leaving window.

       coms handover note --to "$holding_holder" --role "$role" --open-loops "$loops"

   The [handover-note helper](../bin/README.md) wraps the same command. The note
   saves lanes, leases, decisions and both delivery and handled state. A live
   owned lease leaves preparation incomplete. After the operation ends, use
   a fresh transition ID and retain the first note for diagnosis.
5. Share the note path with the holding window and preserve the old inbox
   through retirement. The leaving window can call
   `coms handover cancel "$note"` for its own prepared or holding transition
   before acceptance.

## Holding side checklist

1. Continue approved lanes. Queue questions that actually need the human and
   use the decision log for settled questions. Record a private delta file
   with the same five lists as the original loops file.
2. Recover the old window's mail to your own route with
   `coms handover relay "$note"`. Read the original range and act or record the
   dependency.
3. Once the replacement has enrolled, relay to its exact new holder.

       coms handover relay "$note" --to "$new_holder" --updates "$delta"

   Keep later messages distinct. If delivery was interrupted, reconcile using
   the exact path, byte range and digest. Two messages about the same lane can
   carry different instructions.
4. Check sender routes and relay late mail after acceptance. Keep holding
   until the new route is in use and all late originals are recovered.

## New Claude window checklist

1. Enroll a new holder and marker with the leaving window's role. Review the
   handover note, current decisions, held delta and integration ledger.
2. Read pointer ranges with their original sender and time. Restore routines
   from saved checkpoints and check existing processes before starting a
   replacement watch. The new inbox starts at byte zero. An old offset is
   valid only for its original inbox.
3. Have a peer send an actionable test card to the replacement route. Observe
   it in the correct inbox and handle it. If using a client-provided watch or
   monitor, prove that path actually surfaced the card. Confirm the routes
   with peers and accept with `coms handover accept "$note"`.

## New Codex lead checklist

1. Enroll a new holder and marker with role `codex-lead`. Read current
   instructions, the note, the decision log, the held delta and the integration
   ledger. Recover lane grants, current worktrees, routine ownership, results
   and pending handoffs from the named evidence.
2. Restore each inbox from its own checkpoint. Keep pending work durable and
   use `coms inbox --consume` for the replacement inbox. Set up an inbox read
   routine where the client supports one, or use bounded reads and checkpoints
   during work. Keep quiet while the state is unchanged.
3. Verify any configured watcher with an actionable card sent to the new
   holder. Handle the card and preserve its evidence. Confirm the replacement
   route with the holding window and affected peers, then accept with
   `coms handover accept "$note"`.

The accept command checks enrollment and the relay. An observed card verifies
the route you tested. A sign-in, app wake, or ongoing delivery needs its own
observation if it matters to the handover.

## Checkpoints and retirement

- `coms inbox --consume` advances the physical delivery checkpoint after
  successful output. It records the offset, line count and prefix hash in
  `COMS_DIR/checkpoints`. `coms inbox --peek` and `coms inbox --all` leave
  delivery unchanged. Redirected output defaults to peek. A partial trailing
  line remains unread until complete.
- `coms inbox --peek --receipt` starts from handled state, even if a watch
  already delivered those messages. `coms checkpoint-ack` validates the exact
  range and records the handled boundary and pending state in
  `COMS_DIR/handled`. An earlier receipt cannot acknowledge later mail.
- Investigate malformed rows or a changed prefix using the preserved original
  bytes. Ordinary delivery can warn and continue. Exact-range handover and
  handled operations need a valid source range. Never infer handling from a
  timestamp, a delivery mark or a resync warning.
- The holding window retires the old holder after acceptance, route
  confirmation, release of old live leases and reconciliation of late mail.
  Run `coms handover retire "$note" --routes-confirmed`. If late bytes or a
  live lease prevent retirement, reconcile them and retry. COMS keeps the
  old files and directs later senders to the replacement. Stop the redundant
  task-owned routines after the replacement proves it can receive mail.
