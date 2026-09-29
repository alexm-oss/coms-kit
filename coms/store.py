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
import os
import tempfile
import time

from coms.mutex import FileMutex


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
        self.dir = coms_dir
        self.holder_id = holder_id
        self.ttl = ttl
        self.clock = clock
        self.state_path = os.path.join(coms_dir, "state.json")
        self._mutex = FileMutex(os.path.join(coms_dir, "locks", "coord.lock"))

    # -- state io: call only while holding the mutex --

    def _read(self):
        try:
            with open(self.state_path) as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("fence", 0)
        data.setdefault("resources", {})
        data.setdefault("queue", {})
        return data

    def _write(self, state):
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.state_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- lease protocol --

    def _live_other(self, res, now):
        return res is not None and res["holder"] != self.holder_id and res["expires_at"] > now

    def acquire(self, key, intent="", wait=0.0, pid=None):
        if pid is None:
            pid = os.getpid()
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
            if not res or res["holder"] != holder or res["token"] != token:
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
    # No coord mutex needed: each actor writes only its own file (atomic rename),
    # and reads are a plain directory scan.

    STALE_THREAD_SECONDS = 14400

    def _thread_path(self):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.holder_id)
        return os.path.join(self.dir, "threads", safe + ".json")

    def _touch_thread(self):
        """Bump our thread record's liveness on any coms activity.

        Visibility used to depend solely on explicit re-enrolls, so a window
        that was actively messaging or landing still aged out of the room view
        after an hour (2026-07-24: the whole board looked 'dropped' while every
        thread was alive and mid-conversation). Any message send or lease
        acquisition now refreshes updated_at. Touch never fabricates an
        enrollment - no record, no touch - and never fails the primary op.
        """
        path = self._thread_path()
        try:
            with open(path) as fh:
                entry = json.load(fh)
            entry["updated_at"] = self.clock()
            tmp = path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(entry, fh)
            os.replace(tmp, path)
        except (OSError, ValueError):
            pass

    def register(self, repo="", intent="", role=None, worktree=""):
        # role is sticky: pass None to keep whatever was set before (so a plain
        # status re-enroll never clears an orchestrator marker), or a string to set it.
        d = os.path.join(self.dir, "threads")
        os.makedirs(d, exist_ok=True)
        path = self._thread_path()
        if role is None:
            try:
                with open(path) as fh:
                    role = json.load(fh).get("role", "")
            except (OSError, ValueError):
                role = ""
        payload = {
            "holder": self.holder_id,
            "repo": repo,
            "worktree": worktree,
            "intent": intent,
            "role": role,
            "updated_at": self.clock(),
            "pid": os.getpid(),
        }
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)

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
            if now - entry.get("updated_at", 0) <= self.STALE_THREAD_SECONDS:
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
    # One JSONL file per recipient holder. Messages are small and appended under
    # O_APPEND, which is atomic for sub-PIPE_BUF writes on a local filesystem, so
    # concurrent senders do not need the coordination mutex.

    def _inbox_path(self, holder):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in holder)
        return os.path.join(self.dir, "inbox", safe + ".jsonl")

    def send_message(self, to_holder, text):
        d = os.path.join(self.dir, "inbox")
        os.makedirs(d, exist_ok=True)
        line = json.dumps({"from": self.holder_id, "ts": self.clock(), "text": text})
        with open(self._inbox_path(to_holder), "a") as fh:
            fh.write(line + "\n")
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
