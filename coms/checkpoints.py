"""Explicit handled receipts, distinct from automatic delivery checkpoints."""
import hashlib
import json
import os
from pathlib import Path

from coms.identity import check
from coms.inbox import Inbox, atomic_json, handled_checkpoint, valid_time, validate_pointer, parse_rows

# An inbox pointer is about half a KiB. A receipt's checkpoint is copied verbatim into the
# handled receipt and the delivery checkpoint, so it must not carry a payload into either file.
MAX_POINTER = 4096


def require_plain_pointer(checkpoint):
    try:
        size = len(json.dumps(checkpoint, indent=2))
    except RecursionError:  # nested past the encoder, so certainly not an inbox pointer
        size = MAX_POINTER + 1
    if size > MAX_POINTER:
        raise ValueError("receipt checkpoint is larger than an inbox pointer")


def acknowledge(args, cdir):
    from coms.cli import _store
    from coms.handover import HANDLED_WRITE_MAX, loops_file, regular_json
    store = _store(cdir)
    check(store, strict=True, require_bound=True)
    receipt = regular_json(args.receipt)
    pending = loops_file(args.pending)
    checkpoint = receipt.get("checkpoint")
    if not isinstance(checkpoint, dict) or not (isinstance(receipt.get("messages"), list)
                                               or receipt.get("segment") == checkpoint):
        raise ValueError("receipt needs exact segment and checkpoint")
    inbox = Inbox(store)
    validate_pointer(checkpoint, store.holder_id, inbox.path)
    require_plain_pointer(checkpoint)
    if checkpoint.get("holder") != store.holder_id or checkpoint.get("source") != os.path.abspath(inbox.path):
        raise ValueError("handled receipt belongs to another holder or inbox")
    if (checkpoint.get("version") != 1 or not valid_time(checkpoint.get("last_batch_at"))
            or not valid_time(checkpoint.get("last_read_at"))):
        raise ValueError("invalid handled receipt clock or version")
    offset = checkpoint.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("invalid handled offset")
    inbox.mutex.acquire(timeout=None)
    try:
        inbox.snapshot(reset=True)  # Validate source shape and reject symlink paths.
        try:
            data = Path(inbox.path).read_bytes()
        except FileNotFoundError:
            data = b""
        prefix = data[:offset]
        if offset > len(data) or prefix and not prefix.endswith(b"\n"):
            raise ValueError("handled offset is not a complete inbox prefix")
        if hashlib.sha256(prefix).hexdigest() != checkpoint.get("prefix_hash"):
            raise ValueError("handled inbox prefix changed; reconcile originals")
        if checkpoint.get("lines") != prefix.count(b"\n"):
            raise ValueError("handled receipt line count mismatch")
        start = checkpoint.get("start_offset")
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= offset:
            raise ValueError("invalid handled receipt start")
        before = prefix[:start]
        if (before and not before.endswith(b"\n")
                or hashlib.sha256(before).hexdigest() != checkpoint.get("start_prefix_hash")
                or before.count(b"\n") != checkpoint.get("start_lines")):
            raise ValueError("handled receipt start prefix mismatch")
        rows = parse_rows(prefix[start:])
        if "messages" in receipt and rows != receipt["messages"]:
            raise ValueError("handled receipt messages do not match exact original segment")
        path = os.path.join(store.dir, "handled", store.holder_id + ".json")
        try:
            previous = handled_checkpoint(path)
        except FileNotFoundError:
            previous = {}
        if previous.get("offset", 0) != start:
            raise ValueError("handled receipt does not start at the last handled point")
        # The delivery checkpoint can be ahead (a watch already printed newer
        # mail); never move it backwards or consume bytes newer than the receipt.
        # Read it before anything is written, so a malformed one changes nothing.
        try:
            delivery = regular_json(inbox.checkpoint)
        except FileNotFoundError:
            delivery = {}
        ahead = delivery.get("offset", 0)  # regular_json refuses anything but an object
        if isinstance(ahead, bool) or not isinstance(ahead, int):
            raise ValueError("invalid inbox checkpoint; inspect it before --reset-checkpoint")
        hashes = [hashlib.sha256(line).hexdigest() for line in prefix.splitlines()] if prefix.count(b'\n') <= 256 else []
        atomic_json(path, {"checkpoint": checkpoint, "pending": pending, "message_hashes": hashes,
                           "original_count": prefix.count(b'\n'),
                           "segment_hash": hashlib.sha256(prefix[start:]).hexdigest()},
                    compact=True, limit=HANDLED_WRITE_MAX)
        if ahead <= offset:
            atomic_json(inbox.checkpoint, checkpoint)
    finally:
        inbox.mutex.release()
    print("handled checkpoint:", offset)
    return 0


def add_parser(sub):
    parser = sub.add_parser("checkpoint-ack", help="record exact handled receipt and pending dependencies")
    parser.add_argument("receipt", help="saved coms inbox --peek --receipt output")
    parser.add_argument("--pending", required=True, help="named open-loops JSON after handling this receipt")
    delivered = sub.add_parser('checkpoint-deliver', help='acknowledge a privately staged watch receipt after visible output')
    delivered.add_argument('receipt')


def delivered(args, cdir):
    from coms.cli import _store
    from coms.handover import regular_json
    store = _store(cdir)
    check(store, strict=True, require_bound=True)
    inbox = Inbox(store)
    receipt = regular_json(args.receipt)
    saved = receipt.get('checkpoint')
    if not isinstance(saved, dict):
        raise ValueError('delivery receipt needs checkpoint')
    require_plain_pointer(saved)
    inbox.mutex.acquire(timeout=None)
    try:
        inbox.snapshot_from(saved)
        _, current = inbox.snapshot()
        if saved.get('start_offset') != current['start_offset']:
            if current['start_offset'] >= saved.get('offset', 0):
                return 0
            raise ValueError('staged delivery does not begin at current delivery point')
        prefix = Path(inbox.path).read_bytes()[:saved['start_offset']] if Path(inbox.path).exists() else b''
        if (saved.get('start_prefix_hash') != hashlib.sha256(prefix).hexdigest()
                or saved.get('start_lines') != prefix.count(b'\n')):
            raise ValueError('staged delivery start changed')
        atomic_json(inbox.checkpoint, saved)
        return 0
    finally:
        inbox.mutex.release()
