import os
import tempfile
import unittest

from coms.mutex import FileMutex, LockTimeout


class FileMutexTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-mutex-")
        self.path = os.path.join(self.dir, "coord.lock")

    def test_second_acquire_times_out_while_first_holds(self):
        a = FileMutex(self.path)
        b = FileMutex(self.path)
        a.acquire(timeout=1)
        try:
            with self.assertRaises(LockTimeout):
                b.acquire(timeout=0.2)
        finally:
            a.release()
