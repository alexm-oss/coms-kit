"""Store: the authoritative coordination state in .coms/state.json.

Every read-modify-write of the state runs under a brief FileMutex (flock), so
concurrent actors serialize cleanly. The state holds, per guarded resource, the
current holder, a monotonically increasing fencing token, and a wall-clock lease
expiry. A holder renews its lease with heartbeats; an expired lease is reclaimed
by the next acquirer, which is handed a strictly higher fencing token.

Wall-clock (not monotonic) time is used for expiry because it must be comparable
across processes on this one machine. Short leases bound the clock-skew risk.
"""

import json
import math
import os
import shlex
import tempfile
import time

from coms.mutex import FileMutex
from coms.identity import valid_holder, check as check_identity, session_id, enrollment, valid_marker, IdentityError


class Busy(Exception):
    """Raised when a resource is held by a live lease belonging to another actor."""

    def __init__(self, key, holder):
        super().__init__(f"{key} is held by {holder}")
        self.key = key
        self.holder = holder


class Claim:
    """A held lease on one resource. Renew with heartbeat(), give up with release()."""

    def __init__(self, store, key, token, holder, expires_at):
        self._store = store
        self.key = key
        self.token = token
        self.holder = holder
        self.expires_at = expires_at

    def heartbeat(self):
        self.expires_at = self._store._heartbeat(self.key, self.token, self.holder)
        return self

    def release(self):
        self._store._release(self.key, self.token, self.holder)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class Store:
    def __init__(self, coms_dir, holder_id, ttl=30.0, clock=time.time):
        self.dir = os.path.abspath(coms_dir)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        if os.path.islink(self.dir):
            raise ValueError("COMS store must not be a symlink")
        os.chmod(self.dir, os.stat(self.dir).st_mode & 0o700)
        self.holder_id = valid_holder(holder_id)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("lease TTL must be finite and positive")
        self.ttl = ttl
        self.clock = clock
        self.state_path = os.path.join(coms_dir, "state.json")
        self._mutex = FileMutex(os.path.join(coms_dir, "locks", "coord.lock"))

    # -- state io: call only while holding the mutex --

    def _read(self):
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("coordination state must be an object")
        for field in ("resources", "queue"):
            if field in data and not isinstance(data[field], dict):
                raise ValueError("invalid coordination state maps")
        fence = data.get("fence", 0)
        if isinstance(fence, bool) or not isinstance(fence, int) or fence < 0:
            raise ValueError("invalid fencing counter")
        for resource in data.get("resources", {}).values():
            if not isinstance(resource, dict):
                raise ValueError("invalid lease record")
            valid_holder(resource.get("holder"))
            for field in ("expires_at", "acquired_at"):
                value = resource.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("invalid lease timestamp")
            token = resource.get("token")
            if isinstance(token, bool) or not isinstance(token, int) or not 0 < token <= fence:
                raise ValueError("invalid lease fencing token")
        data.setdefault("version", 1)
        data.setdefault("fence", 0)
        data.setdefault("resources", {})
        data.setdefault("queue", {})
        return data

    def _write(self, state):
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
            from coms.inbox import sync_directory
            sync_directory(self.dir)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- lease protocol --

    def _live_other(self, res, now):
        return res is not None and res["expires_at"] > now

    def acquire(self, key, intent="", wait=0.0, pid=None, owner_nonce=None):
        if pid is None:
            pid = os.getpid()
        if not math.isfinite(wait) or wait < 0:
            raise ValueError("wait must be finite and nonnegative")
        deadline = self.clock() + wait
        while True:
            self._mutex.acquire(timeout=None)
            try:
                st = self._read()
                now = self.clock()
                res = st["resources"].get(key)
                if self._live_other(res, now):
                    if self.clock() >= deadline:
                        raise Busy(key, res["holder"])
                    # otherwise fall through and wait below
                else:
                    st["fence"] += 1
                    token = st["fence"]
                    expires_at = now + self.ttl
                    st["resources"][key] = {
                        "holder": self.holder_id,
                        "token": token,
                        "intent": intent,
                        "acquired_at": now,
                        "expires_at": expires_at,
                        "pid": pid,
                    }
                    if owner_nonce is not None:
                        st['resources'][key]['owner_nonce'] = owner_nonce
                    self._write(st)
                    self._touch_thread()
                    return Claim(self, key, token, self.holder_id, expires_at)
            finally:
                self._mutex.release()
            time.sleep(0.05)

    def _heartbeat(self, key, token, holder):
        self._mutex.acquire(timeout=None)
        try:
            st = self._read()
            res = st["resources"].get(key)
            if not res or res["holder"] != holder or res["token"] != token or res["expires_at"] <= self.clock():
                raise Busy(key, res["holder"] if res else None)
            expires_at = self.clock() + self.ttl
            res["expires_at"] = expires_at
            self._write(st)
            return expires_at
        finally:
            self._mutex.release()

    def _release(self, key, token, holder):
        self._mutex.acquire(timeout=None)
        try:
            st = self._read()
            res = st["resources"].get(key)
            if res and res["holder"] == holder and res["token"] == token:
                del st["resources"][key]
                self._write(st)
        finally:
            self._mutex.release()

    def release_request(self, key, nonce):
        """Recover a wrapper interrupted after persistence but before Claim return."""
        self._mutex.acquire(timeout=None)
        try:
            state = self._read()
            resource = state['resources'].get(key)
            if (resource and resource.get('owner_nonce') == nonce
                    and resource['holder'] == self.holder_id and resource.get('pid') == os.getpid()):
                del state['resources'][key]
                self._write(state)
        finally:
            self._mutex.release()

    def force_release(self, key):
        """Admin override: drop a lease regardless of holder. Returns True if one existed."""
        self._mutex.acquire(timeout=None)
        try:
            st = self._read()
            existed = key in st["resources"]
            if existed:
                del st["resources"][key]
                self._write(st)
            return existed
        finally:
            self._mutex.release()

    def reclaim_stale(self):
        self._mutex.acquire(timeout=None)
        try:
            st = self._read()
            now = self.clock()
            stale = [k for k, r in st["resources"].items() if r["expires_at"] <= now]
            for k in stale:
                del st["resources"][k]
            if stale:
                self._write(st)
            return stale
        finally:
            self._mutex.release()

    def status(self):
        self._mutex.acquire(timeout=None)
        try:
            return self._read()
        finally:
            self._mutex.release()

    # -- thread registry: a lightweight presence board, one file per actor --
    # Registry writers serialize register, touch and retirement under one mutex.

    STALE_THREAD_SECONDS = 14400

    def _thread_path(self):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.holder_id)
        return os.path.join(self.dir, "threads", safe + ".json")

    def _touch_thread(self):
        """Refresh only an existing active record, serialized with retire/register."""
        from coms.inbox import atomic_json
        try:
            mutex = FileMutex(os.path.join(self.dir, "locks", "registry.lock"))
            mutex.acquire(timeout=None)
            try:
                with open(self._thread_path()) as source:
                    entry = json.load(source)
                if not isinstance(entry, dict) or entry.get("retired"):
                    return
                check_identity(self)
                entry["updated_at"] = self.clock()
                atomic_json(self._thread_path(), entry)
            finally:
                mutex.release()
        except (OSError, ValueError):
            pass

    def register(self, repo="", intent="", role=None, worktree="", adopt=False, repair=False):
        from coms.inbox import atomic_json
        mutex = FileMutex(os.path.join(self.dir, "locks", "registry.lock"))
        mutex.acquire(timeout=None)
        try:
            from pathlib import Path
            path = Path(self._thread_path())
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise IdentityError('enrollment path must not contain symlinks')
            repaired = False
            try:
                check_identity(self, strict=True, require_bound=not adopt and path.exists())
            except ValueError:
                if not repair or not session_id():
                    raise
                # Repair only unreadable own records; never override a valid owner.
                try:
                    with open(self._thread_path()) as source:
                        damaged = json.load(source)
                    if (isinstance(damaged, dict) and isinstance(damaged.get('holder'), str)
                            and damaged['holder'] and damaged['holder'] != self.holder_id):
                        raise IdentityError('own enrollment file names another holder; manual reconciliation required')
                    if isinstance(damaged, dict) and (damaged.get('retired') or valid_marker(damaged.get('session'))
                                                       and damaged.get('session') != session_id()):
                        raise IdentityError('bound or retired enrollment cannot be repaired by another session')
                except json.JSONDecodeError:
                    pass
                try:
                    enrollment(self)
                except ValueError:
                    pass
                else:
                    raise ValueError('valid enrollment cannot be repaired; preserve its owner')
                raw = path.read_bytes()
                backup = str(path) + '.corrupt-' + str(time.time_ns())
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as target:
                    target.write(raw)
                    target.flush()
                    os.fsync(target.fileno())
                from coms.inbox import sync_directory
                sync_directory(os.path.dirname(self._thread_path()))
                repaired = True
            old = {} if repaired else enrollment(self) or {}
            payload = {**old, "holder": self.holder_id, "repo": repo, "worktree": worktree,
                       "intent": intent, "role": old.get("role", "") if role is None else role,
                       "updated_at": self.clock(), "pid": os.getpid(), "session": session_id()}
            payload.pop('migration_pending', None)
            atomic_json(self._thread_path(), payload)
        finally:
            mutex.release()

    def threads(self):
        d = os.path.join(self.dir, "threads")
        now = self.clock()
        out = []
        try:
            names = os.listdir(d)
        except FileNotFoundError:
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name)) as fh:
                    entry = json.load(fh)
            except (ValueError, OSError):
                continue
            # Skip records another tool wrote, or a hand edit, that coms cannot show.
            if not isinstance(entry, dict):
                continue
            holder, stamp = entry.get("holder"), entry.get("updated_at")
            if not isinstance(holder, str) or not holder:
                continue
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                continue
            if not entry.get("retired") and now - entry.get("updated_at", 0) <= self.STALE_THREAD_SECONDS:
                out.append(entry)
        return sorted(out, key=lambda e: e.get("holder", ""))

    def orchestrator(self):
        """Holder id of the actor marked role=orchestrator (most recent), or None.

        Lets a peer discover who is coordinating without knowing it up front, so it
        can route a message: coms msg $(coms who) "...".
        """
        orchs = [t for t in self.threads() if t.get("role") == "orchestrator"]
        if not orchs:
            return None
        orchs.sort(key=lambda t: t.get("updated_at", 0), reverse=True)
        return orchs[0]["holder"]

    # -- inbox: an append-only per-actor mailbox for peer-to-peer messages --
    # Recipient mailbox mutexes serialize appends and retirement routing checks.

    def _inbox_path(self, holder):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in holder)
        return os.path.join(self.dir, "inbox", safe + ".jsonl")

    def monitor_command(self, orchestrator):
        """A bounded native CLI read resumes the same durable checkpoint."""
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        marker = session_id()
        key, value = marker.split(":", 1) if marker else ("COMS_SESSION_ID", "")
        return ("perl -e 'alarm shift; exec @ARGV' 1800 env -u COMS_SESSION_ID "
                f"COMS_HOLDER={shlex.quote(self.holder_id)} "
                f"{key}={shlex.quote(value)} "
                f"COMS_DIR={shlex.quote(self.dir)} PYTHONPATH={shlex.quote(root)} "
                f"{shlex.quote(sys.executable)} -u -m coms.cli inbox --consume --follow --json")

    def invocation_prefix(self):
        """Copyable own-window routing for separate shell invocations."""
        marker = session_id()
        key, value = marker.split(':', 1) if marker else ('COMS_SESSION_ID', '')
        return (f'env -u COMS_SESSION_ID COMS_HOLDER={shlex.quote(self.holder_id)} {key}={shlex.quote(value)} '
                f'COMS_DIR={shlex.quote(self.dir)} coms')

    def send_message(self, to_holder, text):
        valid_holder(to_holder)
        check_identity(self)
        d = os.path.join(self.dir, "inbox")
        os.makedirs(d, mode=0o700, exist_ok=True)
        line = json.dumps({"from": self.holder_id, "ts": self.clock(), "text": text})
        mutex = FileMutex(os.path.join(self.dir, "locks", "mail-" + to_holder + ".lock"))
        mutex.acquire(timeout=None)
        try:
            recipient = Store(self.dir, to_holder, clock=self.clock)
            try:
                with open(recipient._thread_path()) as source:
                    record = json.load(source)
                if not isinstance(record, dict):
                    raise ValueError("invalid recipient enrollment")
                if isinstance(record, dict) and record.get("retired"):
                    successor = record.get("successor")
                    if not isinstance(successor, str):
                        raise ValueError("recipient retired; replacement enrollment needs reconciliation")
                    valid_holder(successor)
                    raise ValueError("recipient retired; send to replacement holder " + successor)
            except FileNotFoundError:
                pass
            fd = os.open(self._inbox_path(to_holder), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(line + "\n")
                fh.flush()
        finally:
            mutex.release()
        self._touch_thread()

    def read_inbox(self, holder=None, since=None):
        """Messages sent to holder (default: us). since filters to ts strictly greater."""
        holder = holder or self.holder_id
        out = []
        try:
            with open(self._inbox_path(holder)) as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        msg = json.loads(ln)
                    except ValueError:
                        continue
                    if since is not None and msg.get("ts", 0) <= since:
                        continue
                    out.append(msg)
        except FileNotFoundError:
            pass
        return out
