import os
import subprocess
import tempfile
import time
import unittest

from coms.store import Store

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-conc-")

    def test_two_processes_never_overlap_in_the_critical_section(self):
        """The whole point of coms: real cross-process mutual exclusion.

        Each worker, while holding the lease, writes 'S', sleeps, writes 'E'. If
        the lease serializes them the log is 'SESE'. Any overlap would show 'SS'.
        """
        log = os.path.join(self.dir, "log.txt")
        body = (
            "import time\n"
            f"open({log!r}, 'a').write('S')\n"
            "time.sleep(0.25)\n"
            f"open({log!r}, 'a').write('E')\n"
        )

        def worker(holder):
            env = dict(
                os.environ, COMS_DIR=self.dir, COMS_HOLDER=holder, PYTHONPATH=REPO_ROOT
            )
            return subprocess.Popen(
                ["python3", "-m", "coms.cli", "do", "shared:main",
                 "--wait", "30", "--", "python3", "-c", body],
                env=env,
            )

        a = worker("worker-a")
        b = worker("worker-b")
        a.wait()
        b.wait()
        self.assertEqual(a.returncode, 0)
        self.assertEqual(b.returncode, 0)

        with open(log) as fh:
            content = fh.read()
        self.assertEqual(
            content, "SESE", f"critical sections overlapped: {content!r}"
        )

    def test_crashed_holder_lease_reclaimed_in_real_time(self):
        """A holder that dies without releasing must not wedge the resource."""
        crashed = Store(self.dir, "crashed", ttl=0.4)
        crashed.acquire("x:main")  # held, never released (holder is gone)

        # still live: cannot reclaim yet
        with self.assertRaises(Exception):
            Store(self.dir, "survivor", ttl=0.4).acquire("x:main", wait=0)

        time.sleep(0.55)  # let the lease expire
        claim = Store(self.dir, "survivor", ttl=0.4).acquire("x:main", wait=0)
        self.assertEqual(claim.holder, "survivor")
