"""Explicit adoption of unhashed legacy delivery marks, never handled state."""
import hashlib
import json
import os
from pathlib import Path
import time
import math
import sys

from coms.identity import check


def legacy_marks(store):
    root = Path(os.environ.get('COMS_WATCH_DIR', os.environ.get('XDG_STATE_HOME',
                str(Path.home() / '.local/state')) + '/coms-orchestrator'))
    paths = [('digest', Path(os.environ.get('INBOX_DIGEST_STATE', str(root / 'inbox-digest.state')))),
             ('batch', Path(os.environ.get('READ_BATCH_MARK', str(root / 'batch-read-at')))),
             ('seen', Path(store.dir) / 'inbox-seen' / store.holder_id)]
    found = []
    for kind, path in paths:
        if not path.exists():
            continue
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError('legacy marks must be regular files without symlinks')
        raw = path.read_bytes()
        if len(raw) > 4096:
            raise ValueError('legacy mark exceeds bounded metadata size')
        try:
            value = raw.decode('ascii').strip()
            parts = value.split()
            if kind == 'seen':
                valid = len(parts) == 1 and parts[0].isdigit()
            elif kind == 'digest':
                valid = len(parts) == 2 and parts[0].isdigit() and math.isfinite(float(parts[1])) and float(parts[1]) >= 0
            else:
                valid = len(parts) == 1 and math.isfinite(float(parts[0])) and float(parts[0]) >= 0
            if not valid:
                raise ValueError
        except (UnicodeError, ValueError, OverflowError):
            raise ValueError('malformed legacy mark; preserve private original and reconcile before migration') from None
        found.append({'kind': kind, 'path': str(path.absolute()),
                      'sha256': hashlib.sha256(raw).hexdigest(), 'value': value})
    return found


def require_migration(store, legacy=True):
    try:
        if legacy and legacy_marks(store):
            print('coms: warning: legacy delivery marks found; migration preview is available; continuing', file=sys.stderr)
    except ValueError as exc:
        print(f'coms: warning: {exc}; continuing', file=sys.stderr)


def preview(store, context=False):
    from coms.inbox import Inbox
    rows, state = Inbox(store).snapshot(reset=True)
    marks = legacy_marks(store)
    return {'version': 1, 'holder': store.holder_id, 'session': check(store, strict=True),
            'source': state['source'], 'end_offset': state['offset'],
            'end_lines': state['lines'], 'prefix_hash': state['prefix_hash'],
            'legacy_marks': marks, 'checkpoint': state,
            'uncertainty': ['Legacy positions lack historical prefix hashes; replacement cannot be ruled out.',
                            'Timestamp filters and tail-bounded hooks may have omitted originals.',
                            'Adopt only an operator-reviewed delivery boundary; no handled state is inferred.'],
            'suggested_offsets': suggestions(marks, state, rows, context)}


def suggestions(marks, state, rows, context=False):
    from coms.handover import message_payload
    data = Path(state['source']).read_bytes() if Path(state['source']).exists() else b''
    data = data[:state['offset']]
    if hashlib.sha256(data).hexdigest() != state['prefix_hash']:
        raise ValueError('inbox changed during migration context capture; preview again')
    lines = data.splitlines(keepends=True)
    boundaries = [0]
    for line in lines:
        boundaries.append(boundaries[-1] + len(line))
    candidates = [{'kind': 'replay', 'offset': 0, 'unverified': True},
                  {'kind': 'snapshot-end', 'offset': state['offset'], 'unverified': True}]
    for mark in marks:
        parts = mark['value'].split()
        item = {'kind': mark['kind'], 'unverified': True}
        if mark['kind'] == 'digest':
            item['offset'] = int(parts[0])
        elif mark['kind'] == 'seen':
            item['lines'] = int(parts[0])
            if item['lines'] < len(boundaries):
                item['offset'] = boundaries[item['lines']]
        else:
            item['timestamp'] = parts[0]
            count = 0
            for row in rows:
                if row['ts'] > float(parts[0]):
                    break
                count += 1
            item['offset'] = boundaries[count]
            item['warning'] = 'Only the contiguous timestamp prefix is represented; later backward stamps remain uncertain.'
        candidates.append(item)
    for item in candidates:
        offset = item.get('offset')
        item['complete_boundary'] = offset in boundaries
        if not item['complete_boundary']:
            item['warning'] = 'Position is outside this source or not a complete-line boundary; do not apply it.'
            continue
        count = boundaries.index(offset)
        item['lines'] = count
        item['prefix_hash'] = hashlib.sha256(data[:offset]).hexdigest()
        if context:
            snippets = []
            for index in range(max(0, count - 3), count):
                raw = lines[index]
                source = {'byte_start': boundaries[index], 'byte_end': boundaries[index + 1],
                          'sha256': hashlib.sha256(raw).hexdigest(), 'source': state['source']}
                safe = message_payload(rows[index], source)
                author = message_payload({'text': safe['from']}, source)['text']
                snippets.append({'ts': safe['ts'], 'from': author[:100], 'text': safe['text'][:400],
                                 'truncated': len(safe['text']) > 400, 'original_source': source,
                                 'omitted_original_lines': safe.get('omitted_original_lines', [])})
            item['context'] = snippets
    return candidates


def command(args, cdir):
    from coms.cli import _store
    from coms.handover import regular_json
    from coms.inbox import Inbox, atomic_json, validate_pointer
    store = _store(cdir)
    check(store, strict=True, require_bound=True)
    inbox = Inbox(store)
    from coms.mutex import FileMutex
    registry = FileMutex(os.path.join(store.dir, 'locks', 'registry.lock'))
    if args.action == 'apply':
        registry.acquire(timeout=None)
    try:
        inbox.mutex.acquire(timeout=None)
        if args.action == 'preview':
            print(json.dumps(preview(store, context=args.context), indent=2))
            return 0
        saved = regular_json(args.preview)
        current = preview(store)
        if (saved.get('uncertainty') != current['uncertainty']
                or not isinstance(saved.get('checkpoint'), dict)
                or not isinstance(saved.get('suggested_offsets'), list)
                or not isinstance(saved.get('end_lines'), int) or isinstance(saved['end_lines'], bool)
                or not isinstance(saved.get('prefix_hash'), str)):
            raise ValueError('invalid migration preview schema; generate a fresh own-window preview')
        validate_pointer(saved['checkpoint'], store.holder_id, inbox.path)
        if any(saved.get(key) != current.get(key) for key in ('version', 'holder', 'session', 'source', 'legacy_marks')):
            raise ValueError('migration preview belongs to a changed owner, source or legacy mark; preview again')
        data = Path(inbox.path).read_bytes() if Path(inbox.path).exists() else b''
        end = saved.get('end_offset')
        if (isinstance(end, bool) or not isinstance(end, int) or not 0 <= end <= len(data)
                or hashlib.sha256(data[:end]).hexdigest() != saved.get('prefix_hash')
                or saved['end_lines'] != data[:end].count(b'\n')
                or saved['checkpoint']['offset'] != end
                or saved['checkpoint']['prefix_hash'] != saved['prefix_hash']):
            raise ValueError('migration preview source prefix changed; preserve originals and preview again')
        offset = 0 if args.replay else args.offset
        if offset is None:
            raise ValueError('apply requires --offset N or --replay')
        if not 0 <= offset <= end or offset and not data[:offset].endswith(b'\n'):
            raise ValueError('migration boundary must be a complete prefix within the reviewed snapshot')
        state = dict(current['checkpoint'], offset=offset, lines=data[:offset].count(b'\n'),
                     prefix_hash=hashlib.sha256(data[:offset]).hexdigest(),
                     start_offset=offset, start_lines=data[:offset].count(b'\n'),
                     start_prefix_hash=hashlib.sha256(data[:offset]).hexdigest(),
                     migration={'reviewed': True, 'session': saved['session'], 'legacy_marks': saved['legacy_marks'],
                                'preview_prefix_hash': saved['prefix_hash'], 'uncertainty': saved['uncertainty']})
        if Path(inbox.checkpoint).exists():
            atomic_json(inbox.checkpoint + '.backup-' + str(time.time_ns()), regular_json(inbox.checkpoint))
        atomic_json(inbox.checkpoint, state)
        print('migration delivery boundary:', offset, '(handled state unchanged)')
        return 0
    finally:
        inbox.mutex.release()
        registry.release()


def add_parser(sub):
    parent = sub.add_parser('migrate', help='review and acknowledge legacy delivery positions')
    actions = parent.add_subparsers(dest='action', required=True)
    preview_parser = actions.add_parser('preview', help='read-only JSON preview with historical uncertainty')
    preview_parser.add_argument('--context', action='store_true', help='include bounded redacted original context in private preview')
    apply = actions.add_parser('apply', help='own-window attestation to a reviewed delivery boundary')
    apply.add_argument('--preview', required=True)
    choice = apply.add_mutually_exclusive_group(required=True)
    choice.add_argument('--offset', type=int)
    choice.add_argument('--replay', action='store_true')
