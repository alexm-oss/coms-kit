import contextlib
import io
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from coms import cli
from coms.store import Store


KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-reg-")
        self.clock = FakeClock()

    def store(self, holder):
        return Store(self.dir, holder_id=holder, clock=self.clock)

    def test_register_makes_a_thread_visible(self):
        self.store("sess-a").register(repo="sample", intent="merge HLS")
        holders = [t["holder"] for t in self.store("observer").threads()]
        self.assertIn("sess-a", holders)

    def test_threads_hides_stale_registrations(self):
        self.store("old-window").register(repo="sample")
        self.clock.advance(Store.STALE_THREAD_SECONDS + 1)  # past the staleness window
        holders = [t["holder"] for t in self.store("observer").threads()]
        self.assertNotIn("old-window", holders)


class PrimerCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-primer-")
        self._env = dict(os.environ)
        os.environ["COMS_DIR"] = self.dir
        os.environ["COMS_HOLDER"] = "sess-test"
        os.environ["COMS_SESSION_ID"] = "registry-fixture"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def out(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def run_monitor(self, command, expected, after_start=None):
        command = command.replace(" 1800 env", " 4 env", 1)
        proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        output = b""
        try:
            if after_start:
                time.sleep(0.3)
                after_start()
            deadline = time.monotonic() + 2.5
            while output.count(b"\n") < len(expected) and time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [],
                                            max(0.0, deadline - time.monotonic()))
                if not ready:
                    break
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                output += chunk
            self.assertEqual(output.count(b"\n"), len(expected), output)
            remaining, err = proc.communicate(timeout=5)
            output += remaining
            # Some /bin/sh implementations exec the last command and expose
            # SIGALRM directly; others encode it as 128 + signal number.
            self.assertIn(proc.returncode, (-signal.SIGALRM, 128 + signal.SIGALRM), err.decode())
            if proc.returncode > 0:
                self.assertIn("Alarm clock", err.decode())
            self.assertEqual([json.loads(line)["text"] for line in output.splitlines()], expected)
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                proc.communicate(timeout=3)

    def test_primer_prints_the_protocol_without_registering(self):
        code, out = self.out("primer")
        self.assertEqual(code, 0)
        self.assertIn("coms do", out)
        self.assertEqual(Store(self.dir, "x").threads(), [])

    def test_enroll_registers_this_window_and_prints_the_protocol(self):
        code, out = self.out("enroll", "--intent", "merge HLS")
        self.assertEqual(code, 0)
        self.assertIn("coms do", out)
        self.assertNotIn("Inbox Monitor command", out)
        holders = [t["holder"] for t in Store(self.dir, "x").threads()]
        self.assertIn("sess-test", holders)

    def test_claude_enroll_monitor_resumes_checkpoint_without_losing_quiet_messages(self):
        os.environ["COMS_HOLDER"] = "claude-test"
        Store(self.dir, "orch-test").register(role="orchestrator")
        Store(self.dir, "orch-test").send_message("claude-test", "routine update")
        Store(self.dir, "peer-test").send_message("claude-test", "a question for you")
        Store(self.dir, "BLOCKED-peer").send_message("claude-test", "routine update")
        code, out = self.out("enroll")
        self.assertEqual(code, 0)
        self.assertIn("Read earlier messages first: coms inbox", out)
        self.assertIn("rearm every 30 minutes", out)
        self.assertIn("perl -e 'alarm shift; exec @ARGV' 1800 env", out)
        self.assertIn("-u -m coms.cli inbox --consume --follow --json", out)
        self.assertIn("COMS_HOLDER=claude-test", out)
        self.assertIn("COMS_DIR=" + self.dir, out)
        _, earlier = self.out("inbox")
        self.assertIn("orch-test: routine update", earlier)
        self.assertIn("peer-test: a question for you", earlier)
        command = out.split("Inbox Monitor command (persistent, 30-minute bound; rearm every 30 minutes):\n", 1)[1].strip()
        Store(self.dir, "peer-test").send_message("claude-test", "QUESTION between inbox and Monitor")

        def send_after_start():
            Store(self.dir, "peer-test").send_message("claude-test", "quiet new line")
            Store(self.dir, "orch-test").send_message("claude-test", "new orchestrator line")
            Store(self.dir, "peer-test").send_message("claude-test", "new BLOCKED line")

        self.run_monitor(command,
                         ["QUESTION between inbox and Monitor", "quiet new line", "new orchestrator line",
                          "new BLOCKED line"], after_start=send_after_start)
        with mock.patch.dict(os.environ, {"COMS_HOLDER": "peer-test"}):
            code, _ = self.out("decide", "release-date", "ship", "friday",
                               "--quote", "friday works", "--notify-holder", "claude-test")
        self.assertEqual(code, 0)
        last = Store(self.dir, "claude-test").read_inbox()[-1]
        self.assertEqual((last["from"], last["text"]),
                         ("peer-test", "ANSWERED release-date: ship friday"))
        self.run_monitor(command, ["ANSWERED release-date: ship friday"])


class EnrollWorktreeTest(unittest.TestCase):
    def git(self, repo, *args):
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)

    def test_enroll_records_the_worktree_folder_beside_the_repo(self):
        state = tempfile.mkdtemp(prefix="coms-wt-state-")
        repo = tempfile.mkdtemp(prefix="coms-wt-repo-")
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "--allow-empty", "-m", "init")
        wt = repo + "-login"
        self.git(repo, "worktree", "add", "-q", "-b", "login", wt)
        env = dict(os.environ, COMS_DIR=state, COMS_HOLDER="wt-window", COMS_SESSION_ID="worktree-fixture", PYTHONPATH=KIT_ROOT)
        r = subprocess.run([sys.executable, "-m", "coms.cli", "enroll", "--intent", "login work"],
                           cwd=wt, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        (entry,) = [t for t in Store(state, "observer").threads() if t["holder"] == "wt-window"]
        self.assertEqual(entry["repo"], os.path.basename(repo))
        self.assertEqual(entry["worktree"], os.path.basename(wt))

    def test_worktree_lookup_is_empty_when_git_is_missing(self):
        with mock.patch.object(cli.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertEqual(cli._current_worktree(), "")
