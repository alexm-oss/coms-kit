"""Durable, cooperative account-transition packets and explicit acceptance."""
import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from coms.decisions import DecisionLog, select
from coms.identity import check, valid_holder
from coms.inbox import (Inbox, atomic_json, handled_checkpoint, regular_file, sync_directory, valid_time,
                        validate_pointer)
from coms.mutex import FileMutex
from coms.store import Store

REVIEW_ROLE = "governor"
DEFAULT_HANDOVER = "~/.local/share/coms/handovers"
LOOPS = ("open_work", "owner_questions", "routines", "pending_handoffs", "evidence")
MAX_PACKET = 1024 * 1024
# A handled receipt embeds the caller's open-loops object, so it is sized apart from a handover
# input. loops_file caps open loops at MAX_PACKET as a file, but as ASCII JSON they grow by up to
# about 4x (a 2-byte character becomes a 6-byte escape, 1e15 becomes 1000000000000000.0).
# Writers refuse above HANDLED_WRITE_MAX before replacing the old receipt. Readers do not parse the
# open loops: they find the checkpoint (inbox.handled_checkpoint), so no receipt, however large, old,
# deeply nested or differently ordered, can lock a holder out.
HANDLED_WRITE_MAX = 8 * MAX_PACKET
# A packet copies a handled receipt only while its form inside the packet (pretty-printed) stays this
# small. A larger one (a receipt over 1 MiB, or a small file of nested lists that prints at
# megabytes) would push the packet past MAX_PACKET on its own, so the packet points at a private copy
# of it by byte count and hash instead, as it does for large inbox originals.
HANDLED_EMBED_MAX = 64 * 1024
# Handover input is walked and printed by code that stops near 1,000 levels, while the decoder reads far
# deeper, so nesting is capped where no real list reaches. A receipt small enough to be copied into a
# packet cannot reach it: a list nested d levels pretty-prints to about 2 * d ** 2 bytes, so
# HANDLED_EMBED_MAX allows about 180 levels.
MAX_NESTING = 200
# The fields of an inbox pointer (see inbox.validate_pointer). A receipt's checkpoint may carry more; a
# note keeps only these if the extras would fail its credential check, and the private copy keeps all.
POINTER_FIELDS = ("version", "holder", "source", "offset", "lines", "start_offset", "start_lines",
                  "prefix_hash", "start_prefix_hash", "last_read_at", "last_batch_at")
SECRET = re.compile(r"(?i)(password|secret|api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]+|cookie\s*[:=]\s*(?:[^\s;=]+=[^\s;]+|[A-Za-z0-9+/=_-]{24,})")


def private_dir(path):
    path = Path(path).expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("handover paths must not contain symlinks")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("handover directory must be private (mode 700)")
    return path


def regular_json(path, limit=MAX_PACKET, what="handover input"):
    path = regular_file(path)
    if path.stat().st_size > limit:
        raise ValueError("%s exceeds %d MiB" % (what, limit // MAX_PACKET))
    try:
        with path.open() as source:
            data = json.load(source)
    except RecursionError:
        raise ValueError('handover input nesting exceeds safe JSON limits') from None
    if not isinstance(data, dict):
        raise ValueError("handover input must be an object")
    return data


class RepeatedKey(ValueError):
    pass


def refuse_repeated_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise RepeatedKey("a key is repeated")
    return dict(pairs)


def receipt_record(raw):
    """The receipt as an object, or None when a packet cannot copy it faithfully: not valid JSON, not an
    object, nested past what the decoder reads, or a key repeated (the decoder keeps only the last value)."""
    try:
        record = json.loads(raw, object_pairs_hook=refuse_repeated_keys)
    except (ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def fits_in_packet(record):
    """Whether record, pretty-printed inside a packet, stays within HANDLED_EMBED_MAX. The encoder stops
    as soon as it passes the limit, so a small file that prints at megabytes costs no more than one that
    fits, and it never recurses deep: a list nested d levels prints at about 2 * d ** 2 bytes, so the limit
    is passed near 180 levels, far short of where the encoder fails (about 1,000)."""
    size = 0
    for chunk in json.JSONEncoder(indent=2).iterencode({"handled": record}):
        size += len(chunk)
        if size > HANDLED_EMBED_MAX:
            return False
    return True


class HandledReceipt:
    """One read of a handled receipt. A single pass over the file feeds its hash, its size, its head and a
    private temporary copy of exactly those bytes, so what a packet says about the receipt cannot mix
    versions, and the next acknowledgement, which replaces the receipt file, cannot change the version
    that was described. embedded() or pointer() then give the packet's account; release() cleans up."""

    def __init__(self, path, root):
        path = regular_file(path)
        self.origin, self.root, self.copy = path.absolute(), Path(root), None
        self.digest, self.size, self.head = hashlib.sha256(), 0, b""
        fd, self.temporary = tempfile.mkstemp(dir=str(self.root), prefix=".handled-")
        try:
            with os.fdopen(fd, "wb") as target, path.open("rb") as source:
                for chunk in iter(lambda: source.read(1 << 20), b""):
                    self.digest.update(chunk)
                    self.size += len(chunk)
                    self.head += chunk[:HANDLED_EMBED_MAX - len(self.head)]
                    target.write(chunk)
        except BaseException:
            self.release(keep_copy=False)
            raise

    def embedded(self):
        """The receipt as a packet may copy it whole, else None: it must be small, a valid object, within
        HANDLED_EMBED_MAX once printed in a packet, and agree with the checkpoint every reader takes."""
        if self.size > HANDLED_EMBED_MAX:
            return None
        record = receipt_record(self.head)
        if record is None or not fits_in_packet(record):
            return None
        try:
            checkpoint = handled_checkpoint(self.temporary)
        except ValueError:
            return None
        return record if record.get("checkpoint") == checkpoint else None

    def pointer(self, transition):
        """The packet's account of the receipt: a pointer, by size and hash, at a private copy of it saved
        beside the note. The name cannot be another note's: notes end in .json."""
        checkpoint = handled_checkpoint(self.temporary)  # the reader every command uses, on the hashed bytes
        with open(self.temporary, "rb") as saved:
            os.fsync(saved.fileno())
        final = self.root / (transition + ".handled")
        created = False
        try:
            try:
                os.link(self.temporary, final)  # fails if the name is taken, a symlink included: never replace a copy
            except FileExistsError:
                raise
            except OSError:  # a filesystem without hard links still needs a private copy that cannot replace a name
                fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                created = True
                self.copy = final  # named from the moment it exists, so release() can still remove a partial copy
                with os.fdopen(fd, "wb") as target, open(self.temporary, "rb") as saved:
                    for chunk in iter(lambda: saved.read(1 << 20), b""):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
            created = True
            self.copy = final
            os.unlink(self.temporary)
            sync_directory(str(self.root))
        except BaseException as error:
            if created:
                final.unlink(missing_ok=True)  # should this fail too, self.copy still names it for release()
                self.copy = None
            if isinstance(error, FileExistsError):
                raise ValueError("a private receipt copy named %s already exists; keep it and use another note id"
                                 % final.name) from None
            raise
        return {"source": str(final), "origin": str(self.origin), "bytes": self.size,
                "sha256": self.digest.hexdigest(), "checkpoint": checkpoint,
                "omitted": "pending and message hashes preserved in the private copy named by source"}

    def release(self, keep_copy):
        """Remove the temporary copy and, unless keep_copy, the published one: no note will point at it."""
        try:
            if os.path.exists(self.temporary):
                os.unlink(self.temporary)
        finally:
            if self.copy is not None and not keep_copy:
                self.copy.unlink(missing_ok=True)


def packet_size_check(packet):
    try:
        size = len(json.dumps(packet, indent=2).encode())
    except RecursionError:
        raise ValueError("handover packet nesting exceeds safe JSON limits") from None
    if size > MAX_PACKET:
        raise ValueError("handover packet exceeds 1 MiB; preserve originals and reconcile scope before retrying")


def save_packet(path, packet):
    packet_size_check(packet)
    atomic_json(str(path), packet)


def safe_payload(data):
    result = [None]
    stack = [(data, 1, result, 0)]
    while stack:
        value, depth, parent, key = stack.pop()
        if depth > MAX_NESTING:
            raise ValueError("handover input nesting exceeds safe JSON limits")
        if isinstance(value, dict):
            parent[key] = copy = {}
            for field, child in value.items():
                if "-----BEGIN" in str(field) or re.fullmatch(r"(?i)(password|secret|api[_-]?key|access[_-]?token|authorization|cookie)", str(field)):
                    raise ValueError("possible credential field in handover input; remove it before retrying")
                stack.append((child, depth + 1, copy, field))
        elif isinstance(value, list):
            parent[key] = copy = [None] * len(value)
            stack.extend((child, depth + 1, copy, index) for index, child in enumerate(value))
        elif isinstance(value, str):
            if "-----BEGIN" in value:
                raise ValueError("possible credential in handover input; remove it before retrying")
            parent[key] = message_payload({'text': value})['text']
        else:
            parent[key] = value
    return result[0]


def safe_packet(packet, pointer=None):
    """Scan a copy with only the packet's own path values hidden; path words are not credentials.
    The handled entry's source and origin are hidden only when it is the pointer this note built
    (pointer): an embedded receipt is another writer's data and is scanned whole."""
    data = dict(packet)
    if isinstance(data.get("store"), str):
        data["store"] = "[path]"
    if pointer is not None and data.get("handled") is pointer:
        data["handled"] = dict(pointer)
        for field in ("source", "origin"):
            if isinstance(data["handled"].get(field), str):
                data["handled"][field] = "[path]"
    if isinstance(data.get("lanes"), list):
        data["lanes"] = []
        for lane in packet["lanes"]:
            message = lane.get("last_message") if isinstance(lane, dict) else None
            if isinstance(message, dict) and isinstance(message.get("source"), str):
                lane = dict(lane, last_message=dict(message, source="[path]"))
            data["lanes"].append(lane)
    result = safe_payload(data)
    if isinstance(packet.get("store"), str):
        result["store"] = packet["store"]
    if pointer is not None and packet.get("handled") is pointer:
        for field in ("source", "origin"):
            if isinstance(pointer.get(field), str):
                result["handled"][field] = pointer[field]
    for original, lane in zip(packet.get("lanes", []), result.get("lanes", [])):
        message = original.get("last_message") if isinstance(original, dict) else None
        if isinstance(message, dict) and isinstance(message.get("source"), str):
            lane["last_message"]["source"] = message["source"]
    return result


def acceptable(packet, pointer=None):
    """Whether safe_packet and the size check would pass the packet as it stands."""
    try:
        sanitized = safe_packet(packet, pointer)
        if packet.get("handled") is not pointer and sanitized.get("handled") != packet.get("handled"):
            return False  # Preserve exact receipts by private pointer rather than rewriting them.
        if sanitized.get("delivery_checkpoint") != packet.get("delivery_checkpoint"):
            return False
        packet_size_check(packet)
    except ValueError:
        return False
    return True


def message_payload(message, source=None):
    """Keep ordinary prose; mark credential-bearing source lines as omissions."""
    result = dict(message)
    lines, redacted = [], []
    for number, line in enumerate(message['text'].splitlines(), 1):
        if SECRET.search(line) or '-----BEGIN' in line or re.search(r'\b(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{16,}', line):
            redacted.append({'line': number, 'sha256': hashlib.sha256(line.encode()).hexdigest()})
            lines.append('[REDACTED credential-bearing original line; inspect private source]')
        else:
            lines.append(line)
    if redacted:
        result['text'] = '\n'.join(lines)
        result['omitted_original_lines'] = redacted
        result['original_source'] = source
    return result


def loops_file(path):
    data = regular_json(path)
    if not all(isinstance(data.get(key), list) for key in LOOPS):
        raise ValueError("open-loops needs lists: " + ", ".join(LOOPS))
    return safe_payload(data)


def handover_root():
    return private_dir(os.environ.get("COMS_HANDOVER_DIR", DEFAULT_HANDOVER))


def note_path(value):
    root = handover_root()
    path = Path(value).absolute()
    if path.parent != root or path.is_symlink():
        raise ValueError("note must be a regular file directly in COMS_HANDOVER_DIR")
    return path


def note_mutex(path):
    return FileMutex(str(path) + ".lock")


def release_idle(store, transition):
    """Freeze new acquisitions and release idle tokens under the coordination lock."""
    registry = FileMutex(os.path.join(store.dir, "locks", "registry.lock"))
    store._mutex.acquire(timeout=None)
    try:
        registry.acquire(timeout=None)
        check(store, strict=True, require_bound=True)
        state = store._read()
        owned = [(key, lease) for key, lease in state["resources"].items()
                 if lease["holder"] == store.holder_id]
        for _, lease in owned:
            pid = lease.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ValueError("cannot establish that an owned lease is idle")
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                raise ValueError("cannot establish that an owned lease is idle") from None
            else:
                raise ValueError("owned lease process is still running; stop at a safe boundary before handing over")
        entry = regular_json(store._thread_path())
        if entry.get('handover_pending'):
            raise ValueError('holder already has an active transition; cancel that exact note first')
        entry["handover_pending"] = transition
        atomic_json(store._thread_path(), entry)
        for key, _ in owned:
            del state["resources"][key]
        if owned:
            store._write(state)
    finally:
        registry.release()
        store._mutex.release()


def create(args, store):
    session = check(store, strict=True, require_bound=True)
    valid_holder(args.to)
    if args.to == store.holder_id:
        raise ValueError("holding holder must be different")
    holder = next((e for e in store.threads() if e["holder"] == args.to and not e.get("retired")), None)
    if not holder or not holder.get("session"):
        raise ValueError("holding side must enroll with a session marker first")
    leaving = next((e for e in store.threads() if e["holder"] == store.holder_id), None)
    if not leaving or leaving.get("role") != args.role:
        raise ValueError("leaving side must enroll with the requested transition role first")
    if leaving.get('handover_pending'):
        raise ValueError('holder already has an active transition; cancel that exact note first')
    loops = loops_file(args.open_loops)
    root = handover_root()
    transition = args.id or (store.holder_id[:80] + "-" + str(time.time_ns()))
    valid_holder(transition)
    path = root / (transition + ".json")
    mutex = note_mutex(path)
    mutex.acquire(timeout=None)
    receipt = None
    try:
        if path.exists():
            raise ValueError("transition id already exists; preserve the original note")
        state = store.status()
        inbox = Inbox(store)
        handled = Path(store.dir) / "handled" / (store.holder_id + ".json")
        inbox.mutex.acquire(timeout=None)
        try:
            unread, checkpoint = inbox.snapshot(from_handled=True)
            # An acknowledgement replaces the receipt under this lock, so the packet describes the receipt
            # that fixed the unhandled start above, not a later one.
            if handled.exists():
                receipt = HandledReceipt(handled, root)
        finally:
            inbox.mutex.release()
        lanes = []
        for entry in store.threads():
            recent = store.read_inbox(holder=entry["holder"])
            last = None
            if recent:
                source = Path(store._inbox_path(entry['holder']))
                raw = source.read_bytes()
                end = raw.rfind(b'\n') + 1
                line = raw[:end].splitlines(keepends=True)[-1]
                last = {'source': str(source.absolute()), 'byte_start': end - len(line), 'byte_end': end,
                        'sha256': hashlib.sha256(line).hexdigest(), 'omitted': 'original preserved in inbox'}
            lanes.append({"enrollment": entry, "last_message": last})
        summary = [message_payload(m) for m in unread]
        if len(json.dumps(summary).encode()) > 65536:
            summary = []
        packet = {"version": 1, "id": transition, "phase": "prepared", "created_at": time.time(),
                  "leaving": store.holder_id, "leaving_session": session, "holding": args.to,
                  "holding_session": holder["session"], "store": os.path.abspath(store.dir),
                  "role": args.role, "open_loops": loops, "leases": state["resources"], "lanes": lanes,
                  "decisions": [e for e, _ in select(DecisionLog(store.dir).entries())],
                  "inbox_checkpoint": checkpoint, "delivery_checkpoint": {}, "handled": {}, "unread": summary,
                  "unread_source": 'pinned original range, including delivered but unhandled', "relayed": [], "successor": None}
        delivery = Path(inbox.checkpoint)
        if delivery.exists():
            packet["delivery_checkpoint"] = regular_json(delivery)
        pointer = None  # the handled entry this note built: only its own paths skip the credential check
        if receipt is not None:
            record = receipt.embedded()
            if record is not None:
                packet["handled"] = record
                if not acceptable(packet):  # this receipt would break the cap or the credential check
                    record = None
            if record is None:
                pointer = packet["handled"] = receipt.pointer(transition)
        if not acceptable(packet, pointer):  # words in either checkpoint's extra fields can trip the credential check
            for container, field in ((packet["handled"], "checkpoint"), (packet, "delivery_checkpoint")):
                if field in container:
                    container[field] = {key: container[field][key] for key in POINTER_FIELDS if key in container[field]}
        packet = safe_packet(packet, pointer)
        save_packet(path, packet)
        # Durable note survives a refusal caused by a still-running lease.
        release_idle(store, transition)
        packet["phase"] = "holding"
        save_packet(path, packet)
        store.send_message(args.to, "HANDOVER " + str(path))
        print(str(path))
    finally:
        if receipt is not None:
            receipt.release(keep_copy=path.exists())  # a private copy is kept only for a note that points at it
        mutex.release()
    return 0


def load_for(store, path):
    packet = regular_json(path)
    if packet.get("store") != os.path.abspath(store.dir) or packet.get("version") != 1:
        raise ValueError("handover belongs to another COMS store or version")
    for field in ("id", "leaving", "holding"):
        if not isinstance(packet.get(field), str):
            raise ValueError("invalid handover holder fields")
        valid_holder(packet[field])
    if (packet.get("phase") not in ("prepared", "holding", "accepted", "retired", "cancelled")
            or packet.get("role") not in ("orchestrator", "controller", "codex-lead", REVIEW_ROLE)
            or not valid_time(packet.get("created_at"))
            or any(not isinstance(packet.get(k), str) or not packet[k]
                   for k in ("holding_session", "leaving_session"))
            or not isinstance(packet.get("open_loops"), dict)
            or any(not isinstance(packet["open_loops"].get(k), list) for k in LOOPS)
            or not isinstance(packet.get("unread"), list)):
        raise ValueError("invalid handover packet fields")
    for message in packet["unread"]:
        if (not isinstance(message, dict) or not isinstance(message.get("from"), str)
                or not isinstance(message.get("text"), str) or not valid_time(message.get("ts"))):
            raise ValueError("invalid handover original message")
    old_inbox = Store(store.dir, packet['leaving'])._inbox_path(packet['leaving'])
    validate_pointer(packet.get('inbox_checkpoint'), packet['leaving'], old_inbox)
    positions = packet.get('relay_positions', {})
    if not isinstance(positions, dict):
        raise ValueError('invalid handover relay position map')
    for target, position in positions.items():
        valid_holder(target)
        validate_pointer(position, packet['leaving'], old_inbox)
        if position['offset'] < packet['inbox_checkpoint']['start_offset']:
            raise ValueError('relay position precedes original unhandled start')
    targets = packet.get("relays", {})
    if (not isinstance(targets, dict) or any(not isinstance(v, list)
            or any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h) for h in v)
            for v in targets.values())):
        raise ValueError("invalid handover relay receipts")
    for target in targets:
        valid_holder(target)
    if packet.get("successor") is not None:
        if not isinstance(packet["successor"], str):
            raise ValueError("invalid handover successor")
        valid_holder(packet["successor"])
    if packet["phase"] in ("accepted", "retired") and packet.get("successor") is None:
        raise ValueError("accepted handover needs a successor")
    return packet


def holding_check(store, packet):
    if store.holder_id != packet["holding"] or check(store, strict=True, require_bound=True) != packet["holding_session"]:
        raise ValueError("only the named holding session may relay or retire this transition")


from coms.handover_relay import relay, accept, retire, cancel, command, add_parser
