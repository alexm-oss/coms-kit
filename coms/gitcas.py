"""Git-native compare-and-swap: the fence for shared-branch writes.

`git update-ref <ref> <new> <old>` moves the ref only if it currently equals
<old>. That is exactly a fencing check: an actor that computed its work off a
stale tip is rejected atomically, so a lease that expired and was reclaimed can
never let a resumed holder clobber the branch. This is a real production fence
(Chubby's sequencer, git's own force-with-lease) rather than a bespoke token
service.
"""

import subprocess


class GitError(Exception):
    pass


class CasRejected(Exception):
    """The ref did not hold the expected old value, so the update was refused."""


def _git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def current_tip(repo, ref="HEAD"):
    """Return the 40-hex sha the ref points at, or None if it does not exist."""
    proc = _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    return proc.stdout.strip() or None


def cas_update_ref(repo, ref, new_sha, expected_old, message=""):
    """Atomically set ref to new_sha only if it currently equals expected_old.

    Raises CasRejected if the ref has moved (the fence catching a stale writer).
    """
    args = ["update-ref"]
    if message:
        args += ["-m", message]
    args += [ref, new_sha, expected_old]
    proc = _git(repo, *args, check=False)
    if proc.returncode != 0:
        raise CasRejected(
            f"{ref} is not at {expected_old} (moved under us): {proc.stderr.strip()}"
        )
    return True
