import subprocess
import tempfile
import unittest

from coms.gitcas import current_tip, cas_update_ref, CasRejected


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class GitCasTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="coms-git-")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "one")

    def _child_commit(self, parent, msg):
        """Create a new commit object off parent without moving any ref."""
        tree = git(self.repo, "rev-parse", parent + "^{tree}")
        return git(self.repo, "commit-tree", tree, "-p", parent, "-m", msg)

    def test_current_tip_returns_40_hex_sha(self):
        self.assertRegex(current_tip(self.repo, "refs/heads/main"), r"^[0-9a-f]{40}$")

    def test_current_tip_none_for_missing_ref(self):
        self.assertIsNone(current_tip(self.repo, "refs/heads/nope"))

    def test_cas_update_succeeds_when_old_matches(self):
        old = current_tip(self.repo, "refs/heads/main")
        new = self._child_commit(old, "two")
        cas_update_ref(self.repo, "refs/heads/main", new, old, message="land")
        self.assertEqual(current_tip(self.repo, "refs/heads/main"), new)

    def test_cas_update_rejected_when_tip_moved(self):
        stale = current_tip(self.repo, "refs/heads/main")
        # someone else lands first, advancing main past `stale`
        moved = self._child_commit(stale, "sneaked-in")
        cas_update_ref(self.repo, "refs/heads/main", moved, stale)
        # our write, computed off the stale tip, must be rejected
        ours = self._child_commit(moved, "ours")
        with self.assertRaises(CasRejected):
            cas_update_ref(self.repo, "refs/heads/main", ours, stale)
