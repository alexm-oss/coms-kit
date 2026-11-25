"""Hook visibility pointers and bounded Stop suppression, never delivery state."""
import hashlib
import json
from pathlib import Path
import sys

from coms.identity import check
from coms.inbox import Inbox, atomic_json
from coms.message_cards import any_wakes


def pointer(state, data, offset):
    return dict(state, offset=offset, lines=data[:offset].count(b'\n'),
                prefix_hash=hashlib.sha256(data[:offset]).hexdigest(),
                start_offset=offset, start_lines=data[:offset].count(b'\n'),
                start_prefix_hash=hashlib.sha256(data[:offset]).hexdigest())


def load(path, inbox, data):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('notification state must not contain symlinks')
    if any(parent.exists() and not parent.is_dir() for parent in path.parents):
        raise ValueError('notification state parent must be a directory; preserve conflicting state')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 8192:
        raise ValueError('notification state must be bounded private metadata')
    try:
        saved = json.loads(path.read_text())
    except (ValueError, RecursionError):
        raise ValueError('invalid notification state; preserve it for private reconciliation') from None
    inbox.snapshot_from(saved, data=data)  # Includes schema, anchor, complete prefix and hash.
    return saved


def command(args, cdir):
    from coms.cli import _store, _fmt_msg
    store = _store(cdir)
    check(store, strict=True, require_bound=True)
    inbox = Inbox(store)
    inbox.mutex.acquire(timeout=None)
    try:
        _, delivery = inbox.snapshot(initialize=False)
        data = Path(inbox.path).read_bytes() if Path(inbox.path).exists() else b''
        data = data[:delivery['offset']]
        if hashlib.sha256(data).hexdigest() != delivery['prefix_hash']:
            raise ValueError('inbox changed during notification capture; state unchanged')
        notified = Path(store.dir) / 'notification-seen' / store.holder_id
        stopped = Path(store.dir) / 'stop-suppressed' / store.holder_id
        seen = load(notified, inbox, data)
        suppressed = load(stopped, inbox, data) if args.phase == 'stop' else None
        if seen is None:
            seen = pointer(delivery, data, delivery['start_offset'])
        # Normal delivery may have advanced after an earlier hook notification.
        if seen['offset'] < delivery['start_offset']:
            seen = pointer(delivery, data, delivery['start_offset'])
        rows, end = inbox.snapshot_from(seen, data=data)
        if not any_wakes(rows) or args.phase == 'stop' and suppressed and end['offset'] <= suppressed['offset']:
            return 0
        shown = rows[:5]
        end_show = seen['offset']
        for _ in shown:
            end_show = data.index(b'\n', end_show) + 1
        stream = sys.stderr if args.phase == 'stop' else sys.stdout
        label = 'STOP REFUSED ONCE' if args.phase == 'stop' else 'UNREAD INBOX'
        print('[coms] ' + label + ': ' + str(len(rows)) + ' message(s) for ' + store.holder_id + '.', file=stream)
        print('Full history: ' + store.invocation_prefix() + ' inbox --all', file=stream)
        if len(rows) > len(shown):
            print('[coms] ' + str(len(rows) - len(shown)) + ' later unread not shown; review your inbox.', file=stream)
        for row in shown:
            print('  ' + _fmt_msg(row)[:1000], file=stream)
        stream.flush()
        atomic_json(str(notified), pointer(end, data, end_show))
        if args.phase == 'stop':
            # Suppression merely bounds re-prompts; undisplayed mail remains unread.
            atomic_json(str(stopped), pointer(end, data, end['offset']))
            return 10
        return 0
    finally:
        inbox.mutex.release()


def add_parser(sub):
    parser = sub.add_parser('hook-notifications', help='private guarded-hook notification state')
    parser.add_argument('phase', choices=('context', 'stop'))
