"""Physical inbox delivery checkpoints. Output is delivered, not necessarily handled."""
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from coms.identity import check, valid_holder
from coms.mutex import FileMutex
from coms.message_cards import any_wakes, message_type


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, data, compact=False, limit=None):
    """Replace path with data as JSON. compact drops the indentation. limit refuses a larger
    file before the old one is touched, so a caller never writes what its readers would refuse."""
    text = json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=2)
    # ensure_ascii is on, so characters are bytes and len() is the size on disk.
    if limit is not None and len(text) > limit:
        name = os.path.join(os.path.basename(os.path.dirname(path)), os.path.basename(path))
        raise ValueError("%s would be %d bytes, over its %d-byte limit; nothing was written"
                         % (name, len(text), limit))
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".checkpoint-")
    try:
        with os.fdopen(fd, "w") as target:
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        sync_directory(os.path.dirname(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def regular_file(path):
    """A path that exists, is a regular file and has no symlink in it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("handover input not found")
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("expected a regular JSON file, not a symlink")
    return path


# A handled receipt's checkpoint is found without parsing the rest of the file. It is the first key of
# every receipt this tool has written, so one HANDLED_HEAD read holds it. A receipt another tool wrote
# may lead with the open loops instead (tens of megabytes in an older pretty-printed one), and those are
# skipped by their strings and brackets, a chunk at a time. No size, key order or nesting can lock a
# holder out, and memory stays flat except for one byte per level of nesting.
HANDLED_HEAD = 64 * 1024
# An inbox pointer is about half a KiB, and checkpoint-ack refuses to save a receipt checkpoint over 4 KiB.
HANDLED_CHECKPOINT_MAX = 1024 * 1024
BAD_RECEIPT = ("invalid handled checkpoint; keep the file, move it aside and acknowledge again "
               "(that replays the whole inbox)")
_STRING_EDGE = re.compile(rb'["\\]')
_STRUCTURE = re.compile(rb'["{}\[\]:,]')


def is_checkpoint_key(raw):
    """Whether the raw bytes between a key's quotes spell checkpoint, escapes included."""
    if raw == b"checkpoint":
        return True
    if b"\\" not in raw or len(raw) > 128:  # only an escaped spelling differs and still means it
        return False
    try:
        return json.loads(b'"' + raw + b'"') == "checkpoint"
    except ValueError:
        return False


def find_checkpoint(source):
    """The value of the first top-level "checkpoint" key of the JSON object read from source.

    Everything else is skipped by its strings and brackets, HANDLED_HEAD bytes at a time. Brackets are
    matched by kind, so a damaged file is refused rather than guessed at; the values themselves are not
    validated, because only the checkpoint is used."""
    brackets = bytearray()  # the brackets open right now, innermost last
    in_string = escaped = reading_key = key_is_checkpoint = expect_key = wanted = False
    key = bytearray()  # the top-level key being read, up to one byte past what could spell checkpoint
    value_depth = piece_from = None
    pieces, captured = [], 0
    while True:
        chunk = source.read(HANDLED_HEAD)
        if not chunk:
            raise ValueError(BAD_RECEIPT)  # the file ended before the checkpoint did
        pos, size = 0, len(chunk)
        if piece_from is not None:
            piece_from = 0  # the checkpoint's value goes on into this chunk
        while pos < size:
            if in_string:
                if escaped:  # the byte after a backslash, whatever it is
                    if reading_key and len(key) <= 128:
                        key += chunk[pos:pos + 1]
                    escaped = False
                    pos += 1
                    continue
                edge = _STRING_EDGE.search(chunk, pos)
                stop = edge.start() if edge else size
                if reading_key and len(key) <= 128:
                    key += chunk[pos:stop][:129 - len(key)]
                if edge is None:
                    break
                pos = stop + 1
                if chunk[stop] == 0x5C:  # a backslash
                    if reading_key and len(key) <= 128:
                        key += b"\\"
                    escaped = True
                else:  # the closing quote
                    in_string = False
                    if reading_key:
                        key_is_checkpoint, reading_key = is_checkpoint_key(bytes(key)), False
                continue
            edge = _STRUCTURE.search(chunk, pos)
            stop = edge.start() if edge else size
            if (wanted or not brackets) and chunk[pos:stop].strip():
                raise ValueError(BAD_RECEIPT)  # a number or a word where an object, or the top level, must begin
            if edge is None:
                break
            pos, c = stop + 1, chunk[stop]
            if c == 0x22:  # an opening quote
                if wanted or not brackets:
                    raise ValueError(BAD_RECEIPT)
                in_string = True
                reading_key = expect_key  # only a top-level key can be expected: see the brackets below
                if reading_key:
                    key.clear()
                    expect_key = False
            elif c == 0x7B or c == 0x5B:  # { or [
                if (wanted or not brackets) and c != 0x7B:
                    raise ValueError(BAD_RECEIPT)  # the top level and the checkpoint must be objects
                if wanted:
                    wanted, value_depth, piece_from, captured = False, len(brackets), stop, 0
                brackets.append(c)
                expect_key = len(brackets) == 1
            elif c == 0x7D or c == 0x5D:  # } or ]
                if not brackets or brackets[-1] != (0x7B if c == 0x7D else 0x5B):
                    raise ValueError(BAD_RECEIPT)
                brackets.pop()
                if piece_from is not None and len(brackets) == value_depth:
                    pieces.append(chunk[piece_from:stop + 1])
                    return parse_checkpoint(b"".join(pieces))
                if not brackets:
                    raise ValueError(BAD_RECEIPT)  # the object ended without a checkpoint
            elif c == 0x2C:  # a comma
                expect_key = expect_key or len(brackets) == 1
            elif len(brackets) == 1:  # a colon after a top-level key
                wanted, key_is_checkpoint = key_is_checkpoint, False
        if piece_from is not None:
            pieces.append(chunk[piece_from:])
            captured += size - piece_from
            if captured > HANDLED_CHECKPOINT_MAX:
                raise ValueError("handled receipt checkpoint is larger than an inbox pointer; keep the file, "
                                 "move it aside and acknowledge again (that replays the whole inbox)")


def parse_checkpoint(raw):
    if len(raw) > HANDLED_CHECKPOINT_MAX:
        raise ValueError("handled receipt checkpoint is larger than an inbox pointer; keep the file, "
                         "move it aside and acknowledge again (that replays the whole inbox)")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError(BAD_RECEIPT) from None
    if not isinstance(value, dict):
        raise ValueError(BAD_RECEIPT)
    return value


def handled_checkpoint(path):
    """The checkpoint of a handled receipt (see find_checkpoint)."""
    path = regular_file(path)
    with path.open("rb") as source:
        return find_checkpoint(source)


def finite(value):
    """math.isfinite, except that an integer too large for a float is not finite instead of an error."""
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def valid_time(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and finite(value) and 0 <= value < 253402300800)


def validate_pointer(saved, holder, path):
    if (not isinstance(saved, dict) or isinstance(saved.get('version'), bool) or saved.get('version') != 1
            or saved.get('holder') != holder or saved.get('source') != os.path.abspath(path)
            or not valid_time(saved.get('last_read_at')) or not valid_time(saved.get('last_batch_at'))):
        raise ValueError('invalid inbox pointer identity, clock or version')
    for name in ('offset', 'lines', 'start_offset', 'start_lines'):
        value = saved.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('invalid inbox pointer position')
    if saved['start_offset'] > saved['offset'] or saved['start_lines'] > saved['lines']:
        raise ValueError('invalid inbox pointer start')
    for name in ('prefix_hash', 'start_prefix_hash'):
        if not isinstance(saved.get(name), str) or not re.fullmatch('[a-f0-9]{64}', saved[name]):
            raise ValueError('invalid inbox pointer hash')


def parse_rows(data):
    rows = []
    for line in data.splitlines():
        try:
            msg = json.loads(line)
            stamp = msg.get("ts") if isinstance(msg, dict) else None
            if (not isinstance(msg, dict) or not isinstance(msg.get("text"), str)
                    or not isinstance(msg.get("from"), str) or not valid_time(stamp)):
                raise ValueError("invalid message fields")
        except (ValueError, RecursionError) as exc:
            print(f"coms: warning: skipping malformed inbox line ({type(exc).__name__}); original retained", file=sys.stderr)
            continue
        rows.append(msg)
    return rows


class Inbox:
    def __init__(self, store):
        self.store = store
        self.path = store._inbox_path(valid_holder(store.holder_id))
        self.checkpoint = os.path.join(store.dir, "checkpoints", store.holder_id + ".json")
        self.mutex = FileMutex(os.path.join(store.dir, "locks", "inbox-" + store.holder_id + ".lock"))

    def snapshot(self, reset=False, from_handled=False, initialize=True, review=False, data=None):
        if any(part.is_symlink() for part in (Path(self.path), *Path(self.path).parents)):
            raise ValueError("inbox source must not contain symlinks")
        if not reset and not review:
            from coms.migration import require_migration
            require_migration(self.store, legacy=not os.path.exists(self.checkpoint))
        if data is None:
            try:
                with open(self.path, "rb") as source:
                    data = source.read()
            except FileNotFoundError:
                data = b""
        # Never acknowledge an incomplete append, even if it happens to be valid JSON.
        end = data.rfind(b"\n") + 1
        data = data[:end]
        saved = {}
        if not reset:
            try:
                if from_handled:
                    saved = handled_checkpoint(os.path.join(self.store.dir, "handled", self.store.holder_id + ".json"))
                else:
                    with open(self.checkpoint) as source:
                        saved = json.load(source)
            except FileNotFoundError:
                pass
            except (ValueError, RecursionError) as exc:
                if from_handled and isinstance(exc, ValueError):
                    raise  # the receipt's own message says what to do
                print(f"coms: warning: invalid inbox checkpoint ({type(exc).__name__}); replaying complete lines", file=sys.stderr)
                saved = {}
        if not isinstance(saved, dict):
            raise ValueError("invalid inbox checkpoint")
        offset = saved.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("invalid inbox checkpoint offset")
        if saved and (saved.get("holder") != self.store.holder_id or saved.get("source") != os.path.abspath(self.path)):
            raise ValueError("inbox checkpoint belongs to a different holder or store")
        if saved and (not valid_time(saved.get("last_batch_at"))
                      or not valid_time(saved.get("last_read_at"))
                      or saved.get("version") != 1
                      or isinstance(saved.get("lines"), bool)
                      or not isinstance(saved.get("lines"), int)):
            raise ValueError("invalid inbox checkpoint fields")
        if offset > end or (saved and (hashlib.sha256(data[:offset]).hexdigest() != saved.get("prefix_hash")
                or saved.get("lines") != data[:offset].count(b"\n")
                or offset and not data[:offset].endswith(b"\n"))):
            from coms.rotate import pending_note
            note = pending_note(self.store.dir, self.store.holder_id)
            if note:
                raise ValueError(note)
            print("coms: warning: inbox prefix changed; resyncing to the next complete line", file=sys.stderr)
            offset = min(offset, end)
            if offset and not data[:offset].endswith(b"\n"):
                offset = data.find(b"\n", offset) + 1
        rows = parse_rows(data[offset:])
        next_state = {"start_offset": offset, "start_lines": data[:offset].count(b"\n"),
                      "start_prefix_hash": hashlib.sha256(data[:offset]).hexdigest(), "version": 1, "holder": self.store.holder_id, "source": os.path.abspath(self.path),
                      "offset": end, "lines": data.count(b"\n"),
                      "prefix_hash": hashlib.sha256(data).hexdigest(), "last_read_at": time.time(),
                      "last_batch_at": saved.get("last_batch_at", time.time())}
        if initialize and not saved and not reset and not from_handled:
            # Persist only the initial timer; unread bytes remain at offset zero.
            initial = dict(next_state, offset=0, lines=0,
                           prefix_hash=hashlib.sha256(b"").hexdigest())
            atomic_json(self.checkpoint, initial)
        return rows, next_state

    def deliver(self, args, formatter):
        check(self.store, require_bound=not (args.peek or args.all or args.since is not None))
        self.mutex.acquire(timeout=None)
        try:
            if getattr(args, 'delivery_receipt', None) or not (args.peek or args.all or args.since is not None):
                from coms.migration import require_migration
                require_migration(self.store, legacy=not os.path.exists(self.checkpoint))
            historical = args.all or args.since is not None
            rows, state = self.snapshot(reset=args.reset_checkpoint or historical, from_handled=args.receipt,
                                        initialize=not args.peek, review=args.peek)
            if args.follow and getattr(args, '_follow_position', None):
                rows, state = self.snapshot_from(args._follow_position)
            if args.since is not None:
                rows = [m for m in rows if m["ts"] > args.since]
            if args.receipt and (not args.peek or args.follow):
                raise ValueError("--receipt requires --peek and does not support --follow")
            # Automatic readers never wake for FYI alone, even after a digest timer.
            # On a wake, print the whole physical batch so no original is skipped.
            if (args.digest_mode or args.follow) and not (historical or args.reset_checkpoint) and not any_wakes(rows):
                return 0
            if args.receipt:
                receipt = {"segment": state, "checkpoint": state}
                if len(json.dumps(rows).encode()) <= 65536:
                    receipt["messages"] = rows
                print(json.dumps(receipt, indent=2), flush=True)
            elif args.json:
                if args.follow:
                    for msg in rows:
                        print(json.dumps(msg), flush=True)
                else:
                    print(json.dumps(rows, indent=2), flush=True)
            else:
                for msg in rows:
                    text = formatter(msg)
                    # Cards stay complete on automatic delivery; legacy previews keep their cap.
                    if args.print_chars is not None and (not (args.digest_mode or args.follow) or message_type(msg) is None):
                        text = text[:args.print_chars]
                    print(text, flush=True)
                if args.batch_read:
                    print("unread:", len(rows), flush=True)
                elif not rows and not args.follow and not args.digest_mode:
                    print("coms: inbox empty", flush=True)
            sys.stdout.flush()
            if getattr(args, 'delivery_receipt', None) and rows and not historical:
                atomic_json(args.delivery_receipt, {'checkpoint': state})
            if not historical and not args.peek and (state["offset"] > state["start_offset"] or args.reset_checkpoint):
                state["last_batch_at"] = time.time()
                atomic_json(self.checkpoint, state)
            if args.follow and args.peek:
                args._follow_position = state
            return len(rows)
        finally:
            self.mutex.release()

    def snapshot_from(self, saved, data=None):
        """Validate a pinned range start against current originals without writing."""
        validate_pointer(saved, self.store.holder_id, self.path)
        rows, end = self.snapshot(reset=True, data=data)
        if data is None:
            try:
                with open(self.path, 'rb') as source:
                    data = source.read()
            except FileNotFoundError:
                data = b''
        offset = saved.get('offset')
        if (isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= end['offset']
                or saved.get('version') != 1 or not valid_time(saved.get('last_read_at'))
                or not valid_time(saved.get('last_batch_at')) or saved.get('lines') != data[:offset].count(b'\n')
                or saved.get('holder') != self.store.holder_id or saved.get('source') != os.path.abspath(self.path)
                or offset and not data[:offset].endswith(b'\n')
                or hashlib.sha256(data[:offset]).hexdigest() != saved.get('prefix_hash')):
            raise ValueError('pinned inbox prefix changed; reconcile originals')
        end.update(start_offset=offset, start_lines=data[:offset].count(b'\n'),
                   start_prefix_hash=hashlib.sha256(data[:offset]).hexdigest())
        return parse_rows(data[offset:end['offset']]), end


def urgent(rows, mode):
    # All existing modes use the same type rule; every untyped writer is fail-safe.
    return any_wakes(rows)


def deprecated_knobs(args):
    names = ["--batch-seconds"] if args.batch_seconds is not None else []
    names += [key for key in ("INBOX_URGENT_FROM", "INBOX_EXTRA_URG", "INBOX_ROUTINE_RE") if key in os.environ]
    if names:
        print("coms: deprecated inbox knobs ignored: " + ", ".join(names)
              + "; use message TYPE for wakes and pull the inbox for FYI", file=sys.stderr)


def cmd_inbox(args, cdir):
    from coms.cli import _fmt_msg, _named_holder, _store
    if not _named_holder("inbox"):
        return 2
    store = _store(cdir)
    inbox = Inbox(store)
    if args.batch_seconds is not None and (not math.isfinite(args.batch_seconds) or args.batch_seconds < 0):
        raise ValueError("batch seconds must be finite and nonnegative")
    if args.print_chars is not None and args.print_chars <= 0:
        raise ValueError("print chars must be positive")
    if args.since is not None and not math.isfinite(args.since):
        raise ValueError("since must be finite")
    if args.follow and args.since is not None:
        raise ValueError("--follow with --since would skip physical unread; use a one-shot history request or --follow alone")
    if args.exit_after_first_batch and not args.follow:
        raise ValueError("--exit-after-first-batch requires --follow")
    if args.consume and args.peek:
        raise ValueError("choose --consume or --peek")
    if not args.consume and not args.peek:
        try:
            args.peek = not os.isatty(sys.stdout.fileno())
        except (AttributeError, OSError, ValueError):
            pass  # In-memory callers have no redirected OS descriptor.
    deprecated_knobs(args)
    try:
        count = inbox.deliver(args, _fmt_msg)
        if args.exit_after_first_batch and count:
            return 0
        # Historical filters are one-shot; subsequent polls use physical positions.
        if args.follow:
            args.all = False
            args.since = None
            args.reset_checkpoint = False
            while True:
                time.sleep(1.0)
                count = inbox.deliver(args, _fmt_msg)
                if args.exit_after_first_batch and count:
                    return 0
        return 0
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, sys.stdout.fileno())
        os.close(fd)
        return 0
