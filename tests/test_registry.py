import contextlib
import io
import os
import subprocess
import sys
import tempfile
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
        self.store("sess-a").register(repo="demo", intent="merge feature")
        holders = [t["holder"] for t in self.store("observer").threads()]
        self.assertIn("sess-a", holders)

    def test_threads_hides_stale_registrations(self):
        self.store("old-window").register(repo="demo")
        self.clock.advance(Store.STALE_THREAD_SECONDS + 1)  # past the staleness window
        holders = [t["holder"] for t in self.store("observer").threads()]
        self.assertNotIn("old-window", holders)


class PrimerCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-primer-")
        self._env = dict(os.environ)
        os.environ["COMS_DIR"] = self.dir
        os.environ["COMS_HOLDER"] = "sess-test"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def out(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_primer_prints_the_protocol_without_registering(self):
        code, out = self.out("primer")
        self.assertEqual(code, 0)
        self.assertIn("coms do", out)
        self.assertEqual(Store(self.dir, "x").threads(), [])

    def test_enroll_registers_this_window_and_prints_the_protocol(self):
        code, out = self.out("enroll", "--intent", "merge feature")
        self.assertEqual(code, 0)
        self.assertIn("coms do", out)
        holders = [t["holder"] for t in Store(self.dir, "x").threads()]
        self.assertIn("sess-test", holders)


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
        env = dict(os.environ, COMS_DIR=state, COMS_HOLDER="wt-window", PYTHONPATH=KIT_ROOT)
        r = subprocess.run([sys.executable, "-m", "coms.cli", "enroll", "--intent", "login work"],
                           cwd=wt, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        (entry,) = [t for t in Store(state, "observer").threads() if t["holder"] == "wt-window"]
        self.assertEqual(entry["repo"], os.path.basename(repo))
        self.assertEqual(entry["worktree"], os.path.basename(wt))

    def test_worktree_lookup_is_empty_when_git_is_missing(self):
        with mock.patch.object(cli.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertEqual(cli._current_worktree(), "")
