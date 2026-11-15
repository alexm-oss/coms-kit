"""Lease-owned command supervision and catchable-signal cleanup."""
import os
import signal
import subprocess
import sys
import time
import uuid

from coms.store import Busy

# Seconds a group the OS refused to signal gets to finish dying before the refusal counts.
EXIT_SETTLE = 2


class Interrupted(BaseException):
    def __init__(self, number):
        self.number = number


def signal_group(process, sig):
    """Signal the owned group; False when nothing was left to signal."""
    deadline = time.monotonic() + EXIT_SETTLE
    while True:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return False
        except PermissionError:
            # macOS answers EPERM, not ESRCH, when the group has members but none it may signal: zombies,
            # a leader part way through exiting that waitpid cannot reap yet, or a live member this user may
            # not signal. Reap the leader once it can be reaped and give the rest of a dying group a moment
            # to go (ESRCH). A group that still refuses after EXIT_SETTLE holds a live member: that is real.
            if time.monotonic() >= deadline:
                raise
            process.poll()
            time.sleep(.02)
        else:
            return True


def stop_group(process, grace=3):
    """Kill the owned group even when its immediate child already exited."""
    signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        if not signal_group(process, 0):
            break
        time.sleep(.02)
    signal_group(process, signal.SIGKILL)
    process.wait()


def command(args, cdir):
    from coms.cli import EX_TEMPFAIL, _resolve_key, _store
    if not args.cmd:
        print("coms do: nothing after '--' to run", file=sys.stderr)
        return 2
    key = _resolve_key(args.key)
    store = _store(cdir)
    inherited = os.environ.get("COMS_LEASE_KEY")
    if inherited or os.environ.get("COMS_LEASE_TOKEN"):
        live = store.status().get("resources", {}).get(inherited)
        if (live and live["expires_at"] > store.clock()
                and str(live["token"]) == os.environ.get("COMS_LEASE_TOKEN")):
            print("coms: nested coms do refused: inherited token matches a live lease", file=sys.stderr)
            return EX_TEMPFAIL
        os.environ.pop("COMS_LEASE_KEY", None)
        os.environ.pop("COMS_LEASE_TOKEN", None)
        print("coms: warning: cleared stale inherited lease context; continuing", file=sys.stderr)
    process = claim = None
    request = uuid.uuid4().hex
    previous = {}
    spawning, deferred = False, None

    def interrupted(number, _frame):
        nonlocal deferred
        if spawning:
            deferred = number
            return
        raise Interrupted(number)

    try:
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[number] = signal.signal(number, interrupted)
        try:
            claim = store.acquire(key, intent=args.intent, wait=args.wait, owner_nonce=request)
        except Busy as exc:
            print(f"coms: {key} held by {exc.holder}; did not run", file=sys.stderr)
            return EX_TEMPFAIL
        env = dict(os.environ, COMS_DIR=os.path.abspath(store.dir), COMS_HOLDER=store.holder_id,
                   COMS_LEASE_KEY=key, COMS_LEASE_TOKEN=str(claim.token))
        spawning = True
        try:
            process = subprocess.Popen(args.cmd, env=env, start_new_session=True)
        finally:
            spawning = False
        if deferred is not None:
            raise Interrupted(deferred)
        next_beat = time.monotonic() + max(.1, store.ttl / 3)
        while process.poll() is None:
            if time.monotonic() >= next_beat:
                try:
                    claim.heartbeat()
                except (Busy, OSError, ValueError):
                    print("coms: lease lost; stopping task-owned command", file=sys.stderr)
                    return EX_TEMPFAIL
                next_beat = time.monotonic() + max(.1, store.ttl / 3)
            time.sleep(.05)
        return process.returncode
    except Interrupted as exc:
        return 128 + exc.number
    finally:
        for number in previous:
            signal.signal(number, signal.SIG_IGN)
        try:
            if process is not None:
                grace = 0
                try:
                    claim.heartbeat()
                    grace = min(3, store.ttl / 3)
                except (Busy, OSError, ValueError):
                    pass
                stop_group(process, grace=grace)
            if claim is not None:
                claim.release()
            else:
                store.release_request(key, request)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
