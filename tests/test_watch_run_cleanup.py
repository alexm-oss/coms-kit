"""watch-run.py must not fail a watch because a process group has nothing left to signal.

On macOS os.killpg raises PermissionError (EPERM), not ProcessLookupError, when the group holds only
zombies: a watcher that exited after the first SIGTERM and is not reaped yet. Under load the
watch-once scenario hit it, printed "watch: setup or execution failed" and exited 2 before it
printed the event it had caught.
"""
import importlib.util
import os
import signal
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(KIT_ROOT, "skills", "coms", "bin", "watch-run.py")


def load_runner():
    spec = importlib.util.spec_from_file_location("watch_run", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StopAllTest(unittest.TestCase):
    def test_a_group_the_os_will_not_signal_is_not_an_error(self):
        runner = load_runner()

        class Finished:
            pid = 4242
            waited = False

            def poll(self):
                return 0  # the leader has exited

            def wait(self, timeout=None):
                self.waited = True
                return 0

        child = Finished()
        with patch.object(runner.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")):
            runner.stop_all([child])
        self.assertTrue(child.waited)  # still reaped, so nothing is left behind

    def test_a_zombie_only_group_is_stopped_and_reaped(self):
        runner = load_runner()
        process = subprocess.Popen(["sh", "-c", "exit 0"], start_new_session=True)
        state = ""
        for _ in range(250):  # wait for the exit without reaping it: now a zombie leading its own group
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(process.pid)],
                                   capture_output=True, text=True).stdout.strip()
            if state.startswith("Z"):
                break
            time.sleep(0.02)
        self.assertTrue(state.startswith("Z"), "setup: expected an unreaped zombie, saw %r" % state)
        runner.stop_all([process])
        self.assertEqual(process.returncode, 0)

    def test_a_live_leader_the_os_will_not_signal_is_still_an_error(self):
        runner = load_runner()
        process = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)  # runs first: a stuck stop_all ends here instead of hanging the suite
        outcome = []

        def stop():
            try:
                runner.stop_all([process])
                outcome.append(None)
            except BaseException as exc:
                outcome.append(exc)

        with patch.object(runner.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")):
            worker = threading.Thread(target=stop, daemon=True)
            worker.start()
            worker.join(8)
            self.assertFalse(worker.is_alive(), "stop_all must not wait forever on a live watcher it cannot signal")
        self.assertIsInstance(outcome[0], PermissionError)

    def test_children_that_ignore_sigterm_and_their_descendants_are_killed(self):
        runner = load_runner()
        # The leader ignores SIGTERM, and so does the descendant it starts: ignored signals survive exec.
        process = subprocess.Popen(["sh", "-c", 'trap "" TERM; sleep 60 & echo $!; wait'],
                                   start_new_session=True, stdout=subprocess.PIPE, text=True)
        self.addCleanup(process.stdout.close)
        descendant = int(process.stdout.readline())
        runner.stop_all([process])
        self.assertEqual(process.returncode, -signal.SIGKILL)
        state = None
        for _ in range(250):  # init reaps the orphaned descendant a moment after its parent died
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(descendant)],
                                   capture_output=True, text=True).stdout.strip()
            if not state or state.startswith("Z"):
                break
            time.sleep(0.02)
        self.assertTrue(not state or state.startswith("Z"), "the descendant outlived stop_all: %r" % state)

    def test_a_live_group_is_still_terminated(self):
        runner = load_runner()
        process = subprocess.Popen(["sleep", "60"], start_new_session=True)
        started = time.monotonic()
        runner.stop_all([process])
        self.assertEqual(process.returncode, -signal.SIGTERM)  # asked to stop first, not killed
        self.assertLess(time.monotonic() - started, 2.5)  # well inside the 3 second grace period


if __name__ == "__main__":
    unittest.main()
