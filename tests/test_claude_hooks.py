import json
import os
import subprocess
import tempfile
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT = os.path.join(KIT_ROOT, "hooks", "claude", "coms-context")
STOP = os.path.join(KIT_ROOT, "hooks", "claude", "coms-inbox-stop-gate.sh")
BASH = os.environ.get("COMS_TEST_BASH", "bash")
SID = "abcdef1234567890"
HOLDER = "claude-abcdef12"


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        # State lives at $HOME/.claude/coms with COMS_DIR pointing at the same
        # place, so the hooks as they ran before this change (HOME only) and
        # after it (COMS_DIR) read one inbox, and a RED run means something.
        home = tempfile.mkdtemp(prefix="coms-home-")
        self.state = os.path.join(home, ".claude", "coms")
        os.makedirs(self.state)
        self.repo = tempfile.mkdtemp(prefix="coms-hookrepo-")
        _git(self.repo, "init", "-q", "-b", "main")
        launcher_dir = os.path.join(home, "bin")
        os.makedirs(launcher_dir)
        launcher = os.path.join(launcher_dir, "coms")
        with open(launcher, "w") as target:
            target.write('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        os.chmod(launcher, 0o700)
        self.env = dict(os.environ, COMS_DIR=self.state, HOME=home,
                        PATH=launcher_dir + os.pathsep + os.environ["PATH"], PYTHONPATH=KIT_ROOT)

    def run_hook(self, script, cwd=None):
        env = dict(self.env, COMS_HOLDER=HOLDER, COMS_SESSION_ID=SID)
        subprocess.run(["python3", "-m", "coms.cli", "enroll"], env=env, check=True,
                       capture_output=True, text=True, timeout=3)
        payload = json.dumps({"cwd": cwd or self.repo, "session_id": SID})
        return subprocess.run([BASH, script], input=payload, env=self.env,
                              capture_output=True, text=True)

    def mark(self):
        with open(os.path.join(self.repo, ".git", "coms-enabled"), "w") as fh:
            fh.write("coms guard marker v1\n")

    def sentinel(self):
        with open(os.path.join(self.repo, ".git", "hooks", "pre-commit"), "w") as fh:
            fh.write("#!/usr/bin/env bash\n# coms guard hook. legacy install\n")

    def send(self, text):
        inbox = os.path.join(self.state, "inbox")
        os.makedirs(inbox, exist_ok=True)
        with open(os.path.join(inbox, HOLDER + ".jsonl"), "a") as fh:
            fh.write(json.dumps({"from": "peer-1", "ts": 1.0, "text": text}) + "\n")

    def test_context_is_silent_in_an_unmarked_repo(self):
        r = self.run_hook(CONTEXT)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_context_is_silent_outside_git(self):
        plain = tempfile.mkdtemp(prefix="coms-plain-")
        if subprocess.run(["git", "-C", plain, "rev-parse", "--git-dir"],
                          capture_output=True).returncode == 0:
            self.skipTest("the temp dir sits inside a git repo")
        r = self.run_hook(CONTEXT, cwd=plain)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_context_speaks_in_a_marker_only_husky_repo(self):
        _git(self.repo, "config", "core.hooksPath", ".husky")
        self.mark()
        r = self.run_hook(CONTEXT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"COMS_HOLDER={HOLDER}", r.stdout)
        self.assertIn(f"[coms] This repo ({os.path.basename(self.repo)}) is coordinated", r.stdout)

    def test_context_speaks_in_a_legacy_sentinel_repo(self):
        self.sentinel()
        r = self.run_hook(CONTEXT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"COMS_HOLDER={HOLDER}", r.stdout)

    def test_context_speaks_from_a_linked_worktree_of_a_marked_repo(self):
        _git(self.repo, "config", "core.hooksPath", ".husky")  # marker only: no sentinel hook
        self.mark()
        _git(self.repo, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "init")
        worktree = self.repo + "-wt"
        _git(self.repo, "worktree", "add", "-q", "-b", "feat", worktree)
        r = self.run_hook(CONTEXT, cwd=worktree)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"COMS_HOLDER={HOLDER}", r.stdout)
        self.assertIn(f"[coms] This repo ({os.path.basename(self.repo)}) is coordinated", r.stdout)

    def test_context_injects_unread_messages_once(self):
        self.mark()
        self.send("hold the merge")
        first = self.run_hook(CONTEXT).stdout
        second = self.run_hook(CONTEXT).stdout
        self.assertIn("UNREAD INBOX", first)
        self.assertIn("hold the merge", first)
        self.assertNotIn("hold the merge", second)

    def test_stop_gate_refuses_once_with_unread_messages(self):
        self.mark()
        self.send("hold the merge")
        first = self.run_hook(STOP)
        second = self.run_hook(STOP)
        self.assertEqual(first.returncode, 2)
        self.assertIn("STOP REFUSED ONCE", first.stderr)
        self.assertIn("hold the merge", first.stderr)
        self.assertEqual(second.returncode, 0)

    def test_stop_gate_refuses_once_in_a_legacy_sentinel_repo(self):
        self.sentinel()  # an older guard: hooks only, no coms-enabled mark
        self.send("hold the merge")
        first = self.run_hook(STOP)
        second = self.run_hook(STOP)
        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertIn("STOP REFUSED ONCE", first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

    def test_stop_gate_is_silent_in_an_unmarked_repo(self):
        self.send("hold the merge")
        r = self.run_hook(STOP)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_stop_gate_lets_the_stop_through_when_it_cannot_record_seen(self):
        self.sentinel()  # a repo guarded the old way, which the old hook also sees
        self.send("hold the merge")
        with open(os.path.join(self.state, "notification-seen"), "w") as fh:
            fh.write("a file where the directory should be\n")
        for _ in range(2):
            r = self.run_hook(STOP)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("hold the merge", r.stderr)
            self.assertIn("notification state parent", r.stderr)

    def test_hooks_carry_the_kit_marker(self):
        for path in (CONTEXT, STOP):
            with open(path) as fh:
                self.assertIn("# coms-kit hook", fh.read())

    def test_hooks_read_coms_dir_not_home(self):
        self.mark()
        self.send("decoy in the home folder")  # what a hook that ignored COMS_DIR would find
        self.state = tempfile.mkdtemp(prefix="coms-elsewhere-")  # not $HOME/.claude/coms
        self.env["COMS_DIR"] = self.state
        self.send("first from COMS_DIR")
        shown = self.run_hook(CONTEXT)
        self.assertIn("first from COMS_DIR", shown.stdout)
        self.assertNotIn("decoy in the home folder", shown.stdout)
        self.send("second from COMS_DIR")
        first = self.run_hook(STOP)
        second = self.run_hook(STOP)
        self.assertEqual(first.returncode, 2, first.stderr)
        self.assertIn("second from COMS_DIR", first.stderr)
        self.assertNotIn("decoy in the home folder", first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

    def test_fifteen_hook_messages_are_shown_in_order_without_skipping(self):
        self.mark()
        for n in range(15):
            self.send("backlog-%02d" % n)
        output = "".join(self.run_hook(CONTEXT).stdout for _ in range(3))
        for n in range(15):
            self.assertEqual(output.count("backlog-%02d" % n), 1)
        self.assertNotIn("backlog-", self.run_hook(CONTEXT).stdout)
        # Hook notification marks do not acknowledge the canonical CLI inbox.
        env = dict(self.env, COMS_HOLDER=HOLDER, COMS_SESSION_ID=SID)
        result = subprocess.run(["python3", "-m", "coms.cli", "inbox", "--json"], env=env,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 15)
