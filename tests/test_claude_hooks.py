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


def code_lines(path, start, end):
    """Code lines from the first line starting with `start` through the next
    line starting with `end`, stripped, with blank lines and comments dropped."""
    out, inside = [], False
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not inside and line.startswith(start):
                inside = True
            if inside and line and not line.startswith("#"):
                out.append(line)
            if inside and len(out) > 1 and line.startswith(end):
                return out
    raise AssertionError(f"{start!r} ... {end!r} not found in {path}")


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


class ClaudeHookTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="coms-hookstate-")
        self.repo = tempfile.mkdtemp(prefix="coms-hookrepo-")
        _git(self.repo, "init", "-q", "-b", "main")
        self.env = dict(os.environ, COMS_DIR=self.state,
                        HOME=tempfile.mkdtemp(prefix="coms-home-"))

    def run_hook(self, script, cwd=None):
        payload = json.dumps({"cwd": cwd or self.repo, "session_id": SID})
        return subprocess.run([BASH, script], input=payload, env=self.env,
                              capture_output=True, text=True)

    def mark(self):
        with open(os.path.join(self.repo, ".git", "coms-enabled"), "w") as fh:
            fh.write("coms guard marker v1\n")

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
        inside = subprocess.run(["git", "-C", plain, "rev-parse", "--git-dir"],
                                capture_output=True)
        if inside.returncode == 0:
            self.skipTest("the temp dir sits inside a git repo")
        r = self.run_hook(CONTEXT, cwd=plain)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_context_speaks_in_a_marked_husky_repo(self):
        _git(self.repo, "config", "core.hooksPath", ".husky")
        self.mark()
        r = self.run_hook(CONTEXT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"COMS_HOLDER={HOLDER}", r.stdout)
        self.assertIn(os.path.basename(self.repo), r.stdout)

    def test_context_injects_unread_messages_once(self):
        self.mark()
        self.send("hold the merge")
        first = self.run_hook(CONTEXT).stdout
        second = self.run_hook(CONTEXT).stdout
        self.assertIn("hold the merge", first)
        self.assertNotIn("hold the merge", second)

    def test_stop_gate_refuses_once_with_unread_messages(self):
        self.mark()
        self.send("hold the merge")
        first = self.run_hook(STOP)
        second = self.run_hook(STOP)
        self.assertEqual(first.returncode, 2)
        self.assertIn("hold the merge", first.stderr)
        self.assertEqual(second.returncode, 0)

    def test_stop_gate_is_silent_in_an_unmarked_repo(self):
        self.send("hold the merge")
        r = self.run_hook(STOP)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_stop_gate_lets_the_stop_through_when_it_cannot_record_seen(self):
        self.mark()
        self.send("hold the merge")
        with open(os.path.join(self.state, "inbox-seen"), "w") as fh:
            fh.write("a file where the directory should be\n")
        for _ in range(2):
            r = self.run_hook(STOP)
            self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_hooks_carry_the_kit_marker(self):
        for path in (CONTEXT, STOP):
            with open(path) as fh:
                self.assertIn("# coms-kit hook", fh.read())

    def test_the_two_hooks_keep_their_shared_blocks_identical(self):
        # Each hook is installed as a standalone file, so the blocks they share
        # are duplicated on purpose; this catches a change made to only one.
        blocks = (("input=$(cat", "fi"),
                  ("holder=", "state="),
                  ("seen_dir=", 'case "$seen" in'),
                  ("import sys, json, time", "' 2>/dev/null"))
        for start, end in blocks:
            self.assertEqual(code_lines(CONTEXT, start, end),
                             code_lines(STOP, start, end), start)
