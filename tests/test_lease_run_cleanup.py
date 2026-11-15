"""coms do must stop its child without failing because the child's process group has nothing left to signal.

On macOS os.killpg raises PermissionError (EPERM), not ProcessLookupError, when the group has members but
none it may signal: zombies (a child that died on the first SIGTERM and is not reaped yet), a leader part
way through exiting that waitpid cannot reap yet, or a live member this user may not signal. The lease-lost
path stops the child with no grace period, so under load stop_group sent SIGKILL at a dying child, the error
escaped and `coms do` printed "coms: [Errno 1] Operation not permitted" and exited 2 instead of 75. Only the
last kind of refusal is a real failure, and it must still be reported.
"""
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from coms import lease_run


class Leader:
    """A group leader the test drives: waitpid can reap it after `polls` more polls."""
    pid = 4242

    def __init__(self, polls=0):
        self.polls, self.returncode = polls, None

    def poll(self):
        if self.returncode is None:
            if self.polls:
                self.polls -= 1
            else:
                self.returncode = 0
        return self.returncode

    def wait(self, timeout=None):
        while self.poll() is None:
            pass
        return self.returncode


def macos_killpg(leader, live_member_refused=False):
    """killpg as macOS answers it for the leader's group: EPERM until the leader is reaped, then ESRCH,
    unless a live member this user may not signal remains, which keeps it at EPERM."""
    def killpg(pgid, sig):
        if leader.returncode is None or live_member_refused:
            raise PermissionError(1, "Operation not permitted")
        raise ProcessLookupError(3, "No such process")
    return killpg


class StopGroupTest(unittest.TestCase):
    def test_a_dead_leader_alone_in_its_group_is_not_an_error(self):
        leader = Leader()  # a zombie: reapable at once
        with patch.object(lease_run.os, "killpg", macos_killpg(leader)):
            lease_run.stop_group(leader, grace=0)
        self.assertEqual(leader.returncode, 0)  # still reaped, so nothing is left behind

    def test_a_leader_still_exiting_when_the_os_will_not_signal_it_is_not_an_error(self):
        leader = Leader(polls=3)  # refused while waitpid cannot reap it yet
        with patch.object(lease_run.os, "killpg", macos_killpg(leader)):
            lease_run.stop_group(leader, grace=0)
        self.assertEqual(leader.returncode, 0)

    def test_a_member_the_os_will_not_signal_after_the_leader_exits_is_still_an_error(self):
        leader = Leader()
        with patch.object(lease_run, "EXIT_SETTLE", .2), \
                patch.object(lease_run.os, "killpg", macos_killpg(leader, live_member_refused=True)):
            with self.assertRaises(PermissionError):
                lease_run.stop_group(leader, grace=0)

    def test_a_zombie_only_group_is_stopped_and_reaped(self):
        process = subprocess.Popen(["sh", "-c", "exit 0"], start_new_session=True)
        state = ""
        for _ in range(250):  # wait for the exit without reaping it: now a zombie leading its own group
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(process.pid)],
                                   capture_output=True, text=True).stdout.strip()
            if state.startswith("Z"):
                break
            time.sleep(0.02)
        self.assertTrue(state.startswith("Z"), "setup: expected an unreaped zombie, saw %r" % state)
        lease_run.stop_group(process, grace=0)
        self.assertEqual(process.returncode, 0)

    def test_a_live_leader_the_os_will_not_signal_is_still_an_error(self):
        process = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)  # runs first (cleanups are LIFO), so the cleanup wait never sleeps out the minute
        outcome = []

        def stop():
            try:
                lease_run.stop_group(process, grace=0)
                outcome.append(None)
            except BaseException as exc:
                outcome.append(exc)

        refused = PermissionError(1, "Operation not permitted")
        with patch.object(lease_run, "EXIT_SETTLE", .2), patch.object(lease_run.os, "killpg", side_effect=refused):
            worker = threading.Thread(target=stop, daemon=True)
            worker.start()
            worker.join(8)
            self.assertFalse(worker.is_alive(), "stop_group must not wait forever on a live leader it cannot signal")
        self.assertIsInstance(outcome[0], PermissionError)
        self.assertIsNone(process.poll())  # it was reported, not mistaken for a dying leader


if __name__ == "__main__":
    unittest.main()
