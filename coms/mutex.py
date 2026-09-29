"""FileMutex: a cross-process advisory lock built on POSIX flock(2).

flock is the right primitive for coarse-grained, same-machine mutual exclusion:
the kernel releases the lock automatically when the holding process exits or the
file descriptor is closed, so a crashed holder never leaks this mutex. It is the
brief mutex that guards every read-modify-write of the coordination state.
"""

import errno
import fcntl
import os
import time

_RETRY_SLEEP = 0.02


class LockTimeout(Exception):
    """Raised when the mutex could not be acquired within the timeout."""


class FileMutex:
    def __init__(self, path):
        self.path = path
        self._fd = None

    def acquire(self, timeout=None):
        """Acquire the lock.

        timeout=None blocks until acquired, timeout=0 is a single non-blocking
        attempt, timeout>0 polls until that many seconds elapse then raises
        LockTimeout.
        """
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return self
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    os.close(fd)
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    os.close(fd)
                    raise LockTimeout(
                        f"could not acquire {self.path} within {timeout}s"
                    )
                time.sleep(_RETRY_SLEEP)

    def release(self):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
