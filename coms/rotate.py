"""Archive-and-rotate one holder's inbox at its delivery boundary.

The whole original file is copied to COMS_DIR/inbox-archive first and never deleted. The live file restarts with
only the bytes past the holder's delivery checkpoint, and the checkpoint is rebased to zero, so no undelivered card
is skipped. Appends (mail-<holder>.lock) and readers (inbox-<holder>.lock) are both held for the whole swap.

Crash safety: a pending marker (inbox-archive/pending-<holder>.json) is written and synced before the live file is
touched. While it exists, readers refuse to resync over a changed prefix (coms/inbox.py) and say to run
`coms inbox-rotate <holder> --recover`, which finishes or rolls back the rotation from the marker.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from coms.identity import valid_holder
from coms.inbox import atomic_json, sync_directory
from coms.mutex import FileMutex

EMPTY = hashlib.sha256(b"").hexdigest()
LOCK_WAIT = 120
BLOCKED = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}


def pending_path(cdir, holder):
    return os.path.join(cdir, "inbox-archive", "pending-" + holder + ".json")


def _stamp(now):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))


def _write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def boundary(data, saved, holder, source, what="delivery checkpoint"):
    """Validated offset of a checkpoint-shaped pointer, or ValueError."""
    if not isinstance(saved, dict) or saved.get("version") != 1 or saved.get("holder") != holder \
            or saved.get("source") != source:
        raise ValueError(what + " does not belong to this holder and inbox")
    offset = saved.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= len(data):
        raise ValueError(what + " offset is outside the inbox")
    if offset and data[offset - 1:offset] != b"\n":
        raise ValueError(what + " is not on a line boundary")
    if saved.get("prefix_hash") != _sha(data[:offset]) or saved.get("lines") != data[:offset].count(b"\n"):
        raise ValueError(what + " prefix does not match the inbox; reconcile before rotating")
    return offset


def _rebased(saved, source, now, archive):
    return dict(saved, start_offset=0, start_lines=0, start_prefix_hash=EMPTY, version=1, source=source,
                offset=0, lines=0, prefix_hash=EMPTY, last_read_at=saved.get("last_read_at", now),
                last_batch_at=saved.get("last_batch_at", now), rotated_from=archive)


def _paths(cdir, holder):
    return dict(
        inbox=os.path.join(cdir, "inbox", holder + ".jsonl"),
        checkpoint=os.path.join(cdir, "checkpoints", holder + ".json"),
        handled=os.path.join(cdir, "handled", holder + ".json"),
        thread=os.path.join(cdir, "threads", holder + ".json"),
        seen=os.path.join(cdir, "notification-seen", holder),
        suppressed=os.path.join(cdir, "stop-suppressed", holder),
        archive_dir=os.path.join(cdir, "inbox-archive"))


def _finish(cdir, holder, record, p, now):
    """Steps after the live swap. Each is safe to repeat (used by both rotate and --recover)."""
    with open(p["checkpoint"]) as source:
        current = json.load(source)
    with open(p["inbox"], "rb") as source:
        live = source.read()
    try:
        # Only called after the swap: a checkpoint that validates against the live file is post-rebase (the old
        # offset lies past the shorter file), even after a reader advanced it and dropped rotated_from.
        rebased = current != record["old_checkpoint"] and boundary(live, current, holder, record["live"]) >= 0
    except ValueError:
        rebased = False
    if not rebased:
        if current != record["old_checkpoint"]:
            raise ValueError("delivery checkpoint moved during an incomplete rotation; reconcile by hand")
        atomic_json(p["checkpoint"], _rebased(record["old_checkpoint"], record["live"], now, record["archive"]))
    if record.get("handled_mode") == "rebase" and os.path.exists(p["handled"]):
        with open(p["handled"]) as source:
            receipt = json.load(source)
        if receipt.get("checkpoint", {}).get("rotated_from") != record["archive"]:
            receipt["checkpoint"] = _rebased(receipt["checkpoint"], record["live"], now, record["archive"])
            from coms.handover import HANDLED_WRITE_MAX
            atomic_json(p["handled"], receipt, compact=True, limit=HANDLED_WRITE_MAX)
    elif record.get("handled_mode") == "archive" and os.path.exists(p["handled"]):
        os.replace(p["handled"], record["handled_archive"])
    for key in ("seen", "suppressed"):
        if os.path.exists(p[key]):
            os.replace(p[key], record["pointer_archive"][key])
    record.update(status="rotated", finished_at=now)
    pending = pending_path(cdir, holder)
    atomic_json(pending, record)
    os.replace(pending, record["record"])
    sync_directory(p["archive_dir"])
    return record


def recover(cdir, holder, now=None):
    """Finish or roll back an interrupted rotation from its pending marker."""
    valid_holder(holder)
    now = time.time() if now is None else now
    p, pending = _paths(cdir, holder), pending_path(cdir, holder)
    if not os.path.exists(pending):
        return dict(status="nothing_pending", holder=holder)
    mail = FileMutex(os.path.join(cdir, "locks", "mail-" + holder + ".lock"))
    reader = FileMutex(os.path.join(cdir, "locks", "inbox-" + holder + ".lock"))
    mail.acquire(timeout=LOCK_WAIT)
    try:
        reader.acquire(timeout=LOCK_WAIT)
        try:
            return _recover(cdir, holder, p, pending, now)
        finally:
            reader.release()
    finally:
        mail.release()


def _recover(cdir, holder, p, pending, now):
    if not os.path.exists(pending):
        return dict(status="nothing_pending", holder=holder)
    with open(pending) as source:
        record = json.load(source)
    with open(p["inbox"], "rb") as source:
        live = source.read()
    if len(live) >= record["archive_bytes"] and _sha(live[:record["archive_bytes"]]) == record["archive_sha256"]:
        base = record["record"][:-len(".rotation.json")]
        for n, stray in enumerate(sorted(Path(p["inbox"]).parent.glob(Path(p["inbox"]).name + ".rotate-*"))):
            os.replace(stray, "%s.aborted-carried-%d.jsonl" % (base, n))
        record.update(status="rolled_back", finished_at=now)
        atomic_json(pending, record)
        os.replace(pending, record["record"].replace(".rotation.json", ".rolled-back.json"))
        return record
    carried = record["carried_bytes"]
    if len(live) >= carried and _sha(live[:carried]) == record["carried_sha256"]:
        return _finish(cdir, holder, record, p, now)
    raise ValueError("live inbox matches neither side of the interrupted rotation; reconcile by hand from "
                     + record["archive"])


def rotate(cdir, holder, min_bytes=0, note=None, reason="", now=None, dry_run=False,
           accept_delivered_boundary=False, max_carried=None):
    valid_holder(holder)
    now = time.time() if now is None else now
    p = _paths(cdir, holder)
    mail = FileMutex(os.path.join(cdir, "locks", "mail-" + holder + ".lock"))
    reader = FileMutex(os.path.join(cdir, "locks", "inbox-" + holder + ".lock"))
    mail.acquire(timeout=LOCK_WAIT)
    try:
        reader.acquire(timeout=LOCK_WAIT)
        try:
            if os.path.exists(pending_path(cdir, holder)):
                raise ValueError("an earlier rotation is incomplete; run: coms inbox-rotate %s --recover" % holder)
            if os.path.islink(p["inbox"]):
                raise ValueError("inbox must not be a symlink")
            try:
                with open(p["thread"]) as source:
                    if json.load(source).get("handover_pending"):
                        raise ValueError("holder has an active handover; finish or cancel it before rotating")
            except FileNotFoundError:
                pass
            with open(p["inbox"], "rb") as source:
                data = source.read()
            if len(data) < min_bytes:
                return dict(status="skipped", holder=holder, bytes=len(data), min_bytes=min_bytes)
            live = os.path.abspath(p["inbox"])
            with open(p["checkpoint"]) as source:
                saved = json.load(source)
            offset = boundary(data, saved, holder, live)
            carried = data[offset:]
            if max_carried is not None and len(carried) > max_carried:
                return dict(status="skipped", holder=holder, reason="unread part alone is %d bytes" % len(carried))
            handled_mode = None
            if os.path.exists(p["handled"]):
                with open(p["handled"]) as source:
                    receipt = json.load(source)
                handled_offset = boundary(data, receipt.get("checkpoint"), holder, live, "handled receipt")
                if handled_offset == offset:
                    handled_mode = "rebase"
                elif accept_delivered_boundary:
                    handled_mode = "archive"
                else:
                    raise ValueError("handled receipt is at byte %d, behind delivery at %d; acknowledge or record the "
                                     "pending work first, or pass --accept-delivered-boundary with --note naming the "
                                     "state of record" % (handled_offset, offset))
            name = "%s-%s-to-line%d" % (holder, _stamp(now), data[:offset].count(b"\n"))
            base = os.path.join(p["archive_dir"], name)
            record = dict(
                version=1, status="pending", holder=holder, rotated_at=now, reason=reason, state_of_record=note,
                live=live, archive=base + ".jsonl", record=base + ".rotation.json",
                archive_bytes=len(data), archive_lines=data.count(b"\n"), archive_sha256=_sha(data),
                boundary_offset=offset, boundary_lines=data[:offset].count(b"\n"),
                boundary_prefix_hash=saved["prefix_hash"],
                carried_bytes=len(carried), carried_lines=carried.count(b"\n"), carried_sha256=_sha(carried),
                old_checkpoint=saved, handled_mode=handled_mode,
                handled_archive=base + ".handled.json" if handled_mode == "archive" else None,
                pointer_archive=dict(seen=base + ".notification-seen.json", suppressed=base + ".stop-suppressed.json"),
                rule="Old cards stay in the archive. Read them by path and byte range; never replay them as mail.")
            if dry_run:
                return dict(record, status="dry_run")
            os.makedirs(p["archive_dir"], mode=0o700, exist_ok=True)
            _write_new(record["archive"], data)
            with open(record["archive"], "rb") as source:
                if _sha(source.read()) != record["archive_sha256"]:
                    raise ValueError("archive copy does not match the original; live inbox left unchanged")
            previous = signal.pthread_sigmask(signal.SIG_BLOCK, BLOCKED)
            try:
                atomic_json(pending_path(cdir, holder), record)
                sync_directory(p["archive_dir"])
                temporary = ""
                try:
                    temporary = "%s.rotate-%s-%d-%s" % (p["inbox"], _stamp(now), os.getpid(), os.urandom(4).hex())
                    _write_new(temporary, carried)
                    os.replace(temporary, p["inbox"])
                except BaseException:
                    # Nothing live changed: close the marker as an abort record (kept, never deleted).
                    if os.path.exists(temporary):
                        os.replace(temporary, base + ".aborted-carried.jsonl")
                    record.update(status="aborted", finished_at=time.time())
                    atomic_json(pending_path(cdir, holder), record)
                    os.replace(pending_path(cdir, holder), base + ".aborted.json")
                    raise
                sync_directory(os.path.dirname(p["inbox"]))
                return _finish(cdir, holder, record, p, now)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        finally:
            reader.release()
    finally:
        mail.release()


def command(args, cdir):
    from coms.cli import coms_dir
    if args.recover:
        result = recover(coms_dir(cdir), args.holder)
    else:
        result = rotate(coms_dir(cdir), args.holder, min_bytes=args.min_bytes, note=args.note,
                        reason=args.reason or "", dry_run=args.dry_run,
                        accept_delivered_boundary=args.accept_delivered_boundary, max_carried=args.max_carried)
    result.pop("old_checkpoint", None)
    print(json.dumps(result, indent=2))
    return 0


def add_parser(sub):
    parser = sub.add_parser("inbox-rotate", help="archive a holder's whole inbox and restart it small at its delivery checkpoint")
    parser.add_argument("holder")
    parser.add_argument("--min-bytes", type=int, default=0, help="skip unless the live inbox is at least this big")
    parser.add_argument("--max-carried", type=int, help="skip when the unread part alone is bigger than this")
    parser.add_argument("--note", help="state-of-record path to record with the rotation")
    parser.add_argument("--reason", help="why, for example a threshold that tripped")
    parser.add_argument("--accept-delivered-boundary", action="store_true",
                        help="rotate although the handled receipt is behind delivery; the receipt moves to the archive")
    parser.add_argument("--recover", action="store_true", help="finish or roll back an interrupted rotation")
    parser.add_argument("--dry-run", action="store_true")


def pending_note(cdir, holder):
    """Reader-side guard text when a rotation is incomplete, else None."""
    return ("inbox rotation for %s is incomplete; run: coms inbox-rotate %s --recover" % (holder, holder)
            if Path(pending_path(cdir, holder)).exists() else None)
