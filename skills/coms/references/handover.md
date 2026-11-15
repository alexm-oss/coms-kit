# Handing a window over to another account or window

Use this when a window that others message (an orchestrator, say) has to move: to another account,
another app, or a fresh chat. The commands keep its mail and open work, and make sure nothing is lost
or delivered twice. coms does not sign in, authenticate accounts, merge, push or deploy; signing in is
the human's job.

Three parties take part: the **leaving** window, a **holding** window that covers while the switch
happens, and the **new** window that takes over.

## Before you start

- Every window pins its own `COMS_HOLDER` and `COMS_SESSION_ID` on every command. The new window uses a
  NEW holder id and session marker; it never takes over the old one's id.
- Notes go to `COMS_HANDOVER_DIR` (default `~/.local/share/coms/handovers`) as private files (mode 600
  in a mode 700 folder). The coms state stays in `COMS_DIR` (default `~/.claude/coms`).
- Open loops are a JSON file with these lists: `open_work`, `owner_questions`, `routines`,
  `pending_handoffs`, `evidence`. Leave credentials, cookies, tokens and private values out; obvious
  credentials are refused, but no check recognises every secret.
- Notes and receipts are capped at 1 MiB. Large inboxes stay in their files; notes carry byte ranges
  and hashes that point at the originals.

## Leaving side checklist

1. The holding window enrolls first with its own holder and marker.
2. Finish or stop only your own leased operations at a safe point. Never force-unlock another holder.
3. Write the open-loops file.
4. Save a receipt of what you have handled, handle each message in it or record what it is waiting on
   in the open-loops file, then acknowledge it:

       COMS_HOLDER=leaving-holder COMS_SESSION_ID=leaving-session coms inbox --peek --receipt > /private/path/receipt.json
       COMS_HOLDER=leaving-holder COMS_SESSION_ID=leaving-session coms checkpoint-ack /private/path/receipt.json --pending /private/path/loops.json

   Use `umask 077` and a private folder. Never acknowledge messages you have not read just to shrink a note.
5. Write the note. It records status, leases, recent messages, current decisions and unread mail, then
   releases only your idle leases. A running lease refuses and leaves a prepared note; retry with a new
   `--id` after the operation ends.

       bin/handover-note --to <holding-holder> --role <role> --open-loops /private/path/loops.json

   `<role>` is one of orchestrator, controller, codex-lead or governor (see `operating-model.md`).
6. Send the note's path to the holding window. Keep the old inbox and checkpoints for late mail.

Until the note is accepted, the leaving window can call it off: `coms handover cancel <note>`.

## Holding side checklist

1. Keep approved work moving and queue anything that needs the human. Holding grants no extra powers.
2. Pull the old window's unread mail into your own inbox: `coms handover relay <note>`.
   The relay sends one pointer card (inbox path, byte range, sha256, count), the open loops and your
   delta once each; it never re-sends history as mail. `--replay` (one card per original) is only for
   a small, deliberate recovery.
3. Keep a delta open-loops file of what changed while you held.
4. When the new window has enrolled, relay to it:
   `coms handover relay <note> --to <new-holder> --updates /private/path/delta.json`.
5. Keep relaying late mail for the old holder until every sender uses the new one.

## New window checklist

1. Enroll with a NEW holder and session marker and the same `--role`. Read the note, the current
   decisions (`coms decisions --current`) and the relayed delta.
2. A new inbox starts at byte zero. Never copy the old inbox's offset. Read with `coms inbox --consume`.
3. Restart watches and routines from saved checkpoints, after checking what is already running, so
   nothing is duplicated.
4. Send yourself one real message at the new holder, see it delivered and handled, then accept:
   `coms handover accept <note>` (`--confirm-ready` optional). This is your own statement; coms cannot
   check a sign-in or an app wake.

## Retiring the old holder

Only the holding window retires it, after acceptance, with no live old lease and no unreconciled late
mail: `coms handover retire <note> --routes-confirmed`. The old id stays retired; later sends to it
refuse and name the replacement. Files are kept.

## Inbox checkpoints in short

- `coms inbox --consume` prints complete unread lines and then advances this holder's checkpoint (byte
  offset, line count, prefix hash in `COMS_DIR/checkpoints`). `--peek` leaves it alone, and piped
  output peeks unless you say `--consume`. `coms inbox --all` reads the whole history.
- A changed, truncated or corrupt inbox refuses instead of skipping mail. After reconciling the
  originals, `coms inbox --reset-checkpoint` only when replay is the intended recovery.
- Delivery is not handling. `checkpoint-ack` records what was handled and what is still pending.
- `coms inbox-rotate <holder>` archives a large inbox to `COMS_DIR/inbox-archive` (never deleted) and
  restarts the live file at the delivery checkpoint without skipping undelivered mail. Run it under the
  main lease.
