"""Private relay receipts and account-transition commands."""
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from coms import handover as h
from coms.inbox import parse_rows


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class RelayState:
    """Growing receipts live beside the bounded note, under its existing mutex."""
    def __init__(self, path, packet):
        self.packet, self.path = packet, Path(path).with_suffix('.relays.sqlite3')
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            h.regular_file(self.path)
        else:
            os.close(fd)
        info = self.path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('relay receipt sidecar must be owner-only')
        self.db = sqlite3.connect(self.path)
        try:
            self.db.executescript("""
                create table if not exists binding (note_id text primary key);
                create table if not exists receipts (destination text, receipt text,
                    primary key(destination, receipt));
                create table if not exists positions (destination text primary key, pointer text);
                create table if not exists reconciliations (digest text primary key, destination text,
                    source_end integer, received_total integer);
            """)
            bound = self.db.execute('select note_id from binding').fetchall()
            if bound and bound != [(packet['id'],)]:
                raise ValueError('relay sidecar belongs to another transition')
            self.db.execute('insert or ignore into binding values (?)', (packet['id'],))
            for target, keys in packet.get('relays', {}).items():
                self.db.executemany('insert or ignore into receipts values (?, ?)',
                                    ((target, key) for key in keys))
            for target, pointer in packet.get('relay_positions', {}).items():
                self.db.execute('insert or ignore into positions values (?, ?)',
                                (target, json.dumps(pointer)))
            self.db.commit()
            # Save this small representation before any new outbound message.
            migrated = dict(packet)
            for key in ('relays', 'relay_positions', 'relay_checkpoint'):
                migrated.pop(key, None)
            migrated['relay_receipts'] = {'version': 1, 'file': self.path.name}
            if migrated != packet:
                h.save_packet(path, migrated)
                packet.clear()
                packet.update(migrated)
        except BaseException:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.db.close()

    def seen(self, target, key):
        return self.db.execute('select 1 from receipts where destination=? and receipt=?',
                               (target, key)).fetchone() is not None

    def any_seen(self, target):
        return self.db.execute('select 1 from receipts where destination=? limit 1', (target,)).fetchone() is not None

    def remember(self, target, key):
        self.db.execute('insert or ignore into receipts values (?, ?)', (target, key))
        self.db.commit()

    def position(self, target):
        row = self.db.execute('select pointer from positions where destination=?', (target,)).fetchone()
        if not row:
            return {}
        pointer = json.loads(row[0])
        source = h.Store(self.packet['store'], self.packet['leaving'])._inbox_path(self.packet['leaving'])
        h.validate_pointer(pointer, self.packet['leaving'], source)
        if pointer['offset'] < self.packet['inbox_checkpoint']['start_offset']:
            raise ValueError('relay position precedes original unhandled start')
        return pointer

    def set_position(self, target, pointer):
        self.db.execute('insert or replace into positions values (?, ?)', (target, json.dumps(pointer)))
        self.db.commit()


def relay_seen(path, packet, target):
    with RelayState(path, packet) as state:
        return state.any_seen(target)


def relay_position(path, packet, target):
    with RelayState(path, packet) as state:
        return state.position(target)


def relay(args, store, packet, path):
    h.holding_check(store, packet)
    if packet['phase'] not in ('holding', 'accepted'):
        raise ValueError('handover is not ready for holding')
    target = args.to or packet.get('successor') or store.holder_id
    h.valid_holder(target)
    if target == packet['leaving']:
        raise ValueError('cannot relay back into the leaving inbox')
    updates = h.loops_file(args.updates) if args.updates else None
    with RelayState(path, packet) as state:
        inbox = h.Inbox(h.Store(store.dir, packet['leaving']))
        inbox.mutex.acquire(timeout=None)
        try:
            pinned = packet['inbox_checkpoint']
            inbox.snapshot_from(pinned)
            start = state.position(target) or dict(pinned, offset=pinned['start_offset'],
                lines=pinned['start_lines'], prefix_hash=pinned['start_prefix_hash'])
            messages, checkpoint = inbox.snapshot_from(start)
            raw = Path(inbox.path).read_bytes()[start['offset']:checkpoint['offset']].splitlines(keepends=True) if Path(inbox.path).exists() else []
        finally:
            inbox.mutex.release()
        messages += [{'from': packet['leaving'], 'ts': packet['created_at'],
                      'text': 'HANDOVER OPEN LOOPS ' + json.dumps(packet['open_loops'])}]
        if updates is not None:
            messages.append({'from': store.holder_id, 'ts': packet['created_at'],
                             'text': 'HELD DELTA ' + json.dumps(updates)})
        messages += [{'from': e['by'], 'ts': e['ts'], 'text': 'DECISION ' + json.dumps(e)}
                     for e, _ in h.select(h.DecisionLog(store.dir).entries())]
        count, offset = 0, start['offset']
        if not getattr(args, 'replay', False):
            # No-replay rule: one card per original would flood a large inbox. Send one pointer to the originals' byte range, the
            # open loops and the held delta once each, and name the decision log; never the history as mail.
            originals = b''.join(raw)
            cards = []
            if raw:
                pointer = {'note': packet['id'], 'leaving': packet['leaving'], 'path': inbox.path,
                           'byte_start': start['offset'], 'byte_end': start['offset'] + len(originals),
                           'messages': len(raw), 'sha256': hashlib.sha256(originals).hexdigest(),
                           'read': 'open the path at this byte range; do not re-send its lines as mail'}
                cards.append((digest({'pointer': pointer}), 'HANDOVER POINTER ' + json.dumps(pointer, sort_keys=True)))
            loops = {'from': packet['leaving'], 'ts': packet['created_at'],
                     'text': 'HANDOVER OPEN LOOPS ' + json.dumps(packet['open_loops'])}
            cards.append((digest(loops), loops['text']))
            if updates is not None:
                cards.append((digest({'held': updates}), 'HELD DELTA ' + json.dumps(updates)))
            cards.append((digest({'decisions': packet['id']}), 'HANDOVER DECISIONS read with: coms decisions --current'))
            for key, text in cards:
                if state.seen(target, key):
                    continue
                store.send_message(target, text)
                state.remember(target, key)
                count += 1
            state.set_position(target, checkpoint)
            print('handover relayed:', count)
            return 0
        for number, message in enumerate(messages):
            source = None
            if number < len(raw):
                source = {'path': inbox.path, 'byte_start': offset, 'byte_end': offset + len(raw[number]),
                          'sha256': hashlib.sha256(raw[number]).hexdigest()}
                offset = source['byte_end']
            safe = h.message_payload(message, source)
            key = digest({'source': source, 'message': safe}) if source else digest(safe)
            if state.seen(target, key) or (not source and state.seen(target, digest(message))):
                continue
            store.send_message(target, 'HANDOVER ORIGINAL ' + json.dumps(safe, sort_keys=True))
            state.remember(target, key)
            count += 1
        state.set_position(target, checkpoint)
    print('handover relayed:', count)
    return 0


def record_delivered(args, store, packet, path):
    """Record the successor's explicit reconciliation, without sending anything."""
    h.holding_check(store, packet)
    if packet['phase'] not in ('holding', 'accepted'):
        raise ValueError('handover is not ready for holding')
    h.valid_holder(args.to)
    receipt = h.regular_json(args.receipt)
    if (receipt.get('note_id') != packet['id'] or receipt.get('destination') != args.to
            or args.to == packet['leaving']):
        raise ValueError('delivered receipt belongs to another transition or destination')
    for field in ('source_end', 'source_rows', 'received_total'):
        value = receipt.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('invalid delivered receipt counts')
    keys = receipt.get('metadata_keys')
    if (not isinstance(keys, list) or any(not isinstance(k, str) or not re.fullmatch('[a-f0-9]{64}', k) for k in keys)
            or len(set(keys)) != len(keys)
            or receipt['received_total'] != receipt['source_rows'] + len(keys)):
        raise ValueError('delivered receipt metadata/count mismatch')
    inbox = h.Inbox(h.Store(store.dir, packet['leaving']))
    inbox.mutex.acquire(timeout=None)
    try:
        pinned = packet['inbox_checkpoint']
        inbox.snapshot_from(pinned)
        raw = Path(inbox.path).read_bytes() if Path(inbox.path).exists() else b''
        end = receipt['source_end']
        start = pinned['start_offset']
        prefix = raw[:end]
        if not start <= end <= len(raw) or (prefix and not prefix.endswith(b'\n')):
            raise ValueError('delivered receipt ends outside a complete original prefix')
        rows = parse_rows(prefix[start:])
        if len(rows) != receipt['source_rows']:
            raise ValueError('delivered receipt source count mismatch')
        actual_hash = hashlib.sha256(prefix).hexdigest()
        if receipt.get('source_prefix_sha256', actual_hash) != actual_hash:
            raise ValueError('delivered receipt source prefix changed')
        checkpoint = dict(pinned, offset=end, lines=prefix.count(b'\n'), prefix_hash=actual_hash,
                          last_read_at=time.time(), last_batch_at=time.time())
        with RelayState(path, packet) as state:
            previous = state.position(args.to)
            if previous.get('offset', start) > end:
                raise ValueError('delivered receipt would rewind relay position')
            state.db.executemany('insert or ignore into receipts values (?, ?)', ((args.to, key) for key in keys))
            state.db.execute('insert or ignore into reconciliations values (?, ?, ?, ?)',
                             (digest(receipt), args.to, end, receipt['received_total']))
            state.db.execute('insert or replace into positions values (?, ?)', (args.to, json.dumps(checkpoint)))
            state.db.commit()
    finally:
        inbox.mutex.release()
    print('delivered relay recorded:', receipt['received_total'], 'without sending')
    return 0


def accept(args, store, packet, path):
    h.check(store, strict=True, require_bound=True)
    if store.holder_id in (packet['leaving'], packet['holding']):
        raise ValueError('successor must enroll a NEW holder')
    if packet['phase'] != 'holding':
        raise ValueError('handover must be holding before acceptance')
    entry = next((e for e in store.threads() if e['holder'] == store.holder_id), None)
    if not entry or entry.get('role') != packet['role']:
        raise ValueError("enroll the successor with the packet's role first")
    if not relay_seen(path, packet, store.holder_id):
        raise ValueError('holding side must relay the accumulated delta to this NEW holder first')
    packet.update(phase='accepted', successor=store.holder_id, accepted_at=h.time.time(), acceptance_session=h.check(store, strict=True, require_bound=True))
    h.save_packet(path, packet)
    store.send_message(packet['holding'], 'HANDOVER ACCEPTED ' + packet['id'] + ' by ' + store.holder_id)
    print('handover accepted:', packet['id'])
    return 0

def retire(args, store, packet, path):
    h.holding_check(store, packet)
    if packet['phase'] != 'accepted':
        raise ValueError('accepted successor is required before retirement')
    old = h.Store(store.dir, packet['leaving'])
    old._mutex.acquire(timeout=None)
    registry = h.FileMutex(h.os.path.join(store.dir, 'locks', 'registry.lock'))
    try:
        registry.acquire(timeout=None)
        if any((r['holder'] == old.holder_id and r['expires_at'] > h.time.time() for r in old._read()['resources'].values())):
            raise ValueError('leaving holder still owns a live lease')
        inbox = h.Inbox(old)
        mailbox = h.FileMutex(h.os.path.join(store.dir, 'locks', 'mail-' + old.holder_id + '.lock'))
        mailbox.acquire(timeout=None)
        inbox.mutex.acquire(timeout=None)
        try:
            messages, checkpoint = inbox.snapshot(from_handled=True)
            if h.Path(inbox.path).exists() and h.Path(inbox.path).stat().st_size != checkpoint['offset']:
                raise ValueError('incomplete late old-inbox record remains; reconcile before retirement')
            relayed = relay_position(path, packet, packet['successor'])
            if relayed.get('offset') != checkpoint['offset'] or relayed.get('prefix_hash') != checkpoint['prefix_hash']:
                raise ValueError('late old-inbox mail remains; relay before retirement')
        finally:
            inbox.mutex.release()
        try:
            entry = h.regular_json(old._thread_path())
        except (FileNotFoundError, ValueError):
            entry = {'holder': old.holder_id}
        packet.update(phase='retired', retired_at=h.time.time())
        h.packet_size_check(packet)
        entry.update(retired=True, updated_at=h.time.time(), role='', successor=packet['successor'])
        h.atomic_json(old._thread_path(), entry)
        h.save_packet(path, packet)
    finally:
        if 'mailbox' in locals():
            mailbox.release()
        registry.release()
        old._mutex.release()
    print('handover retired:', packet['leaving'])
    return 0

def cancel(args, store, packet, path):
    if store.holder_id != packet['leaving'] or h.check(store, strict=True, require_bound=True) != packet['leaving_session']:
        raise ValueError('only the named leaving session may cancel')
    if packet['phase'] not in ('prepared', 'holding', 'cancelled'):
        raise ValueError('accepted or retired handover cannot be cancelled')
    registry = h.FileMutex(h.os.path.join(store.dir, 'locks', 'registry.lock'))
    registry.acquire(timeout=None)
    try:
        entry = h.regular_json(store._thread_path())
        active = entry.get('handover_pending')
        if active and active != packet['id']:
            raise ValueError('cancellation belongs to a different active transition; acquisitions remain paused')
        packet.update(phase='cancelled', cancelled_at=h.time.time())
        h.save_packet(path, packet)
        entry['handover_pending'] = False
        h.atomic_json(store._thread_path(), entry)
    finally:
        registry.release()
    print('handover cancelled:', packet['id'])
    return 0

def command(args, cdir):
    from coms.cli import _store
    store = _store(cdir)
    if args.action == 'note':
        return h.create(args, store)
    path = h.note_path(args.note)
    mutex = h.note_mutex(path)
    mutex.acquire(timeout=None)
    try:
        packet = h.load_for(store, path)
        return {'relay': relay, 'accept': accept, 'retire': retire, 'cancel': cancel, 'record-delivered': record_delivered}[args.action](args, store, packet, path)
    finally:
        mutex.release()

def add_parser(sub):
    parser = sub.add_parser('handover', help='prepare, relay, accept and retire an account transition')
    actions = parser.add_subparsers(dest='action', required=True)
    note = actions.add_parser('note', help='write a durable packet, then release idle owned leases')
    note.add_argument('--open-loops', required=True)
    note.add_argument('--to', required=True, help='holding holder')
    note.add_argument('--role', choices=('orchestrator', 'controller', 'codex-lead', h.REVIEW_ROLE), required=True)
    note.add_argument('--id')
    for action in ('relay', 'accept', 'retire', 'cancel', 'record-delivered'):
        child = actions.add_parser(action)
        child.add_argument('note')
        if action == 'record-delivered':
            child.add_argument('--to', required=True)
            child.add_argument('--receipt', required=True)
        elif action == 'relay':
            child.add_argument('--to', help='new successor holder, or holding side while waiting')
            child.add_argument('--updates', help='named open-loops JSON containing held delta')
            child.add_argument('--replay', action='store_true',
                               help='re-send every original as its own card (off by default: the no-replay rule sends one pointer)')
        elif action == 'accept':
            child.add_argument('--confirm-ready', action='store_true')
        elif action == 'retire':
            child.add_argument('--routes-confirmed', action='store_true')
