import contextlib
import io
import os
import subprocess
import tempfile
import unittest

from coms import cli
from coms.store import Store

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-guard-state-")
        self.repo = tempfile.mkdtemp(prefix="coms-repo-")
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")
        self.key = f"{os.path.basename(self.repo)}:main"
        self.env = dict(os.environ)
        self.env.update(
            COMS_DIR=self.dir,
            COMS_HOLDER="committer",
            COMS_BIN="python3 -m coms.cli",
            PYTHONPATH=REPO_ROOT,
        )

    def commit(self):
        return subprocess.run(
            ["git", "-C", self.repo, "commit", "-q", "--allow-empty", "-m", "x"],
            env=self.env, capture_output=True, text=True,
        )

    def test_guard_installs_the_hook(self):
        self.assertEqual(cli.main(["guard", self.repo]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git", "hooks", "pre-commit")))

    def test_commit_allowed_when_branch_is_free(self):
        cli.main(["guard", self.repo])
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_commit_blocked_when_branch_held_by_another(self):
        cli.main(["guard", self.repo])
        Store(self.dir, "another-actor").acquire(self.key)
        result = self.commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("held by another", result.stderr)

    def test_unguard_removes_the_hook(self):
        cli.main(["guard", self.repo])
        cli.main(["unguard", self.repo])
        Store(self.dir, "another-actor").acquire(self.key)
        result = self.commit()  # hook gone, so not blocked
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_coms_do_lands_through_its_own_backstop_without_a_holder_id(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["guard", self.repo])
        env = dict(self.env)
        del env["COMS_HOLDER"]
        result = subprocess.run(
            ["python3", "-m", "coms.cli", "do", self.key, "--wait", "0", "--",
             "git", "-C", self.repo, "commit", "-q", "--allow-empty", "-m", "landed"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def marker(self):
        return os.path.join(self.repo, ".git", "coms-enabled")

    def test_guard_writes_the_marker(self):
        cli.main(["guard", self.repo])
        self.assertTrue(os.path.exists(self.marker()))

    def test_unguard_removes_the_marker(self):
        cli.main(["guard", self.repo])
        cli.main(["unguard", self.repo])
        self.assertFalse(os.path.exists(self.marker()))

    def test_guard_leaves_a_husky_repo_untouched(self):
        husky = os.path.join(self.repo, ".husky")
        os.makedirs(husky)
        hook = os.path.join(husky, "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/usr/bin/env sh\necho husky-ran\n")
        os.chmod(hook, 0o755)
        _git(self.repo, "add", ".husky/pre-commit")
        _git(self.repo, "commit", "-q", "-m", "add husky hook")
        _git(self.repo, "config", "core.hooksPath", ".husky")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["guard", self.repo]), 0)
        porcelain = subprocess.run(
            ["git", "-C", self.repo, "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(porcelain, "")
        self.assertEqual(sorted(os.listdir(husky)), ["pre-commit"])
        self.assertTrue(os.path.exists(self.marker()))
        self.assertIn("core.hooksPath", out.getvalue())
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("husky-ran", result.stdout + result.stderr)

    def test_guard_leaves_an_existing_hook_alone(self):
        hooks = os.path.join(self.repo, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        hook = os.path.join(hooks, "pre-commit")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho own-hook-ran\n")
        os.chmod(hook, 0o755)
        with open(hook, "rb") as fh:
            before = fh.read()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["guard", self.repo]), 0)
        with open(hook, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertFalse(os.path.exists(hook + ".coms-orig"))
        self.assertFalse(os.path.exists(os.path.join(hooks, "pre-merge-commit")))
        self.assertTrue(os.path.exists(self.marker()))
        self.assertIn("pre-commit", out.getvalue())
        self.assertIn("left the repo's hooks alone", out.getvalue())
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("own-hook-ran", result.stdout + result.stderr)

    def test_hook_blocks_a_linked_worktree_on_the_held_branch(self):
        cli.main(["guard", self.repo])
        _git(self.repo, "switch", "-q", "-c", "other")
        wt = self.repo + "-wt"
        _git(self.repo, "worktree", "add", "-q", wt, "main")
        Store(self.dir, "another-actor").acquire(self.key)
        result = subprocess.run(
            ["git", "-C", wt, "commit", "-q", "--allow-empty", "-m", "x"],
            env=self.env, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("held by another", result.stderr)

    def test_guard_outside_a_repo_fails_cleanly(self):
        plain = tempfile.mkdtemp(prefix="coms-plain-")
        inside = subprocess.run(["git", "-C", plain, "rev-parse", "--git-dir"],
                                capture_output=True)
        if inside.returncode == 0:
            self.skipTest("the temp dir sits inside a git repo")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["guard", plain]), 2)
        self.assertEqual(os.listdir(plain), [])
        self.assertIn("not inside a git repository", err.getvalue())

    def test_legacy_installed_hook_works_with_the_new_cli(self):
        # An older coms-hook, as `coms guard` installed it before the current version.
        legacy = rb'''#!/usr/bin/env bash
# coms guard hook. Installed by `coms guard` as pre-commit and pre-merge-commit.
#
# It blocks a write to a guarded branch (default: main / master) while another
# actor holds the coms lease on that branch, so a direct commit can never clobber
# a landing already in progress. It chains to whatever hook coms replaced.
#
# Failure policy: block ONLY on a confirmed conflict (coms check exit 3). If coms
# is missing or errors, allow the commit (fail open) so a misconfigured coms can
# never wedge every commit on the machine.

hook_name="$(basename "$0")"
repo_top="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
repo="$(basename "$repo_top")"
branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo DETACHED)"
guarded="$(git config --get coms.branches 2>/dev/null || echo 'main master')"
coms_bin="${COMS_BIN:-$(git config --get coms.bin 2>/dev/null || echo coms)}"

is_guarded=0
for b in $guarded; do
  if [ "$b" = "$branch" ]; then is_guarded=1; fi
done

if [ "$is_guarded" = "1" ]; then
  key="${repo}:${branch}"
  $coms_bin check "$key" >/dev/null 2>&1
  rc=$?
  if [ "$rc" = "3" ]; then
    echo "coms: '$key' is held by another actor right now." >&2
    echo "coms: this $hook_name is blocked so you do not clobber their landing." >&2
    echo "coms: check 'coms status', or land safely via 'coms do $key -- git ...'." >&2
    exit 1
  fi
fi

# chain to the hook coms replaced, if any
orig="$(dirname "$0")/${hook_name}.coms-orig"
if [ -x "$orig" ]; then exec "$orig" "$@"; fi
exit 0
'''
        hooks = os.path.join(self.repo, ".git", "hooks")
        for name in ("pre-commit", "pre-merge-commit"):
            path = os.path.join(hooks, name)
            with open(path, "wb") as fh:
                fh.write(legacy)
            os.chmod(path, 0o755)
        self.assertEqual(self.commit().returncode, 0)
        Store(self.dir, "another-actor").acquire(self.key)
        blocked = self.commit()
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("held by another", blocked.stderr)
        Store(self.dir, "x").force_release(self.key)
        env = dict(self.env)
        del env["COMS_HOLDER"]
        landed = subprocess.run(
            ["python3", "-m", "coms.cli", "do", self.key, "--wait", "0", "--",
             "git", "-C", self.repo, "commit", "-q", "--allow-empty", "-m", "landed"],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(landed.returncode, 0, landed.stderr)
