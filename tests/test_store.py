import os
import tempfile
import unittest

from coms.store import Store, Busy


class FakeClock:
    """Injectable wall-clock so lease-expiry tests need no real sleeps."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-store-")
        self.clock = FakeClock()

    def store(self, holder):
        return Store(self.dir, holder_id=holder, ttl=30.0, clock=self.clock)

    def test_acquire_free_resource_issues_monotonic_fencing_tokens(self):
        s = self.store("sess-a")
        first = s.acquire("sample:main")
        second = s.acquire("sample:ship")
        self.assertEqual(first.token, 1)
        self.assertEqual(second.token, 2)
        self.assertEqual(first.holder, "sess-a")

    def test_acquire_held_by_other_raises_busy(self):
        self.store("sess-a").acquire("sample:main")
        with self.assertRaises(Busy) as ctx:
            self.store("sess-b").acquire("sample:main", wait=0)
        self.assertEqual(ctx.exception.holder, "sess-a")

    def test_expired_lease_is_reclaimed_with_higher_token(self):
        held = self.store("sess-a").acquire("sample:main")
        self.clock.advance(31.0)  # past the 30s TTL
        reclaimed = self.store("sess-b").acquire("sample:main", wait=0)
        self.assertEqual(reclaimed.holder, "sess-b")
        self.assertGreater(reclaimed.token, held.token)

    def test_heartbeat_extends_expiry_and_blocks_reclaim(self):
        claim = self.store("sess-a").acquire("sample:main")
        self.clock.advance(20.0)
        claim.heartbeat()  # renew: expiry now clock+30 = 1050
        self.clock.advance(15.0)  # total 35s since acquire, but only 15s since heartbeat
        with self.assertRaises(Busy):
            self.store("sess-b").acquire("sample:main", wait=0)

    def test_release_frees_resource_for_next_holder(self):
        claim = self.store("sess-a").acquire("sample:main")
        claim.release()
        nxt = self.store("sess-b").acquire("sample:main", wait=0)
        self.assertEqual(nxt.holder, "sess-b")

    def test_state_persists_across_store_instances(self):
        self.store("sess-a").acquire("sample:main")
        # a fresh Store reading the same dir sees the live holder
        with self.assertRaises(Busy):
            self.store("sess-b").acquire("sample:main", wait=0)

    def test_status_reports_live_holder(self):
        s = self.store("sess-a")
        s.acquire("sample:main", intent="merge HLS")
        st = s.status()
        self.assertIn("sample:main", st["resources"])
        self.assertEqual(st["resources"]["sample:main"]["holder"], "sess-a")
        self.assertEqual(st["resources"]["sample:main"]["intent"], "merge HLS")
