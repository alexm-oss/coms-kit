import json
import os
import subprocess
import tempfile
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEER = os.path.join(KIT_ROOT, "skills", "coms", "bin", "coms-peer")

SESSIONS = [
    {"sessionId": "local_main", "title": "main", "cwd": "/x/myapp", "lastActivityAt": "2026-09-29T10:04:00Z"},
    {"sessionId": "local_a", "title": "login", "cwd": "/x/wt-login", "lastActivityAt": "2026-09-29T10:02:00Z"},
    {"sessionId": "local_b", "title": "other", "cwd": "/x/wt-other", "lastActivityAt": "2026-09-29T10:03:00Z"},
]


class PeerTest(unittest.TestCase):
    def run_peer(self, entry, target):
        state = tempfile.mkdtemp(prefix="coms-peer-state-")
        os.makedirs(os.path.join(state, "threads"))
        with open(os.path.join(state, "threads", entry["holder"] + ".json"), "w") as fh:
            json.dump(entry, fh)
        return subprocess.run(["python3", "-B", PEER, target], input=json.dumps(SESSIONS),
                              capture_output=True, text=True,
                              env=dict(os.environ, COMS_DIR=state))

    def test_peer_finds_the_worktree_window_not_the_main_checkout(self):
        r = self.run_peer({"holder": "claude-1234abcd", "repo": "myapp", "worktree": "wt-login"},
                          "claude-1234abcd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["local_a\tlogin\t/x/wt-login"])

    def test_peer_falls_back_to_repo_for_a_thread_without_worktree(self):
        r = self.run_peer({"holder": "claude-5678efgh", "repo": "myapp"}, "claude-5678efgh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["local_main\tmain\t/x/myapp"])

    def test_peer_accepts_a_worktree_name_directly(self):
        r = self.run_peer({"holder": "unused", "repo": "x"}, "wt-other")
        self.assertEqual(r.stdout.splitlines(), ["local_b\tother\t/x/wt-other"])
