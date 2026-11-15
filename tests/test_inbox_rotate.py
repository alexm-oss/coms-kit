"""coms inbox-rotate: archive whole, restart small at the delivery checkpoint, never skip undelivered mail."""
import hashlib
import json
import pathlib

from coms.store import Store
from tests.test_switchover import Sandbox


class InboxRotateTest(Sandbox):
    def setUp(self):
        super().setUp()
        self.enroll("leaving", "controller")
        for n in range(30):
            self.send("TYPE: FYI\nTOPIC: old %d\nNEED: none\nPROOF: /tmp/x" % n)
        code, out, err = self.run_cli("inbox", "--consume")
        self.assertEqual(code, 0, err)
        self.inbox = pathlib.Path(self.state) / "inbox" / "leaving.jsonl"
        self.before = self.inbox.read_bytes()

    def rotate(self, *extra):
        code, out, err = self.run_cli("inbox-rotate", "leaving", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_archive_is_the_whole_original_and_live_restarts_empty(self):
        result = self.rotate("--reason", "test")
        self.assertEqual(result["status"], "rotated")
        archive = pathlib.Path(result["archive"])
        self.assertEqual(archive.read_bytes(), self.before)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(self.before).hexdigest())
        self.assertEqual(self.inbox.read_bytes(), b"")
        self.assertTrue(pathlib.Path(result["archive"].replace(".jsonl", ".rotation.json")).exists())

    def test_undelivered_mail_is_carried_and_delivered_once(self):
        self.send("TYPE: ASK\nTOPIC: undelivered before rotate\nNEED: answer\nPROOF: /tmp/y")
        result = self.rotate()
        self.assertEqual(result["carried_lines"], 1)
        self.send("TYPE: ASK\nTOPIC: after rotate\nNEED: answer\nPROOF: /tmp/z")
        code, out, err = self.run_cli("inbox", "--consume")
        self.assertEqual(code, 0, err)
        self.assertIn("undelivered before rotate", out)
        self.assertIn("after rotate", out)
        self.assertNotIn("old 3", out)
        self.assertNotIn("prefix changed", err)
        self.assertIn("inbox empty", self.run_cli("inbox", "--consume")[1])

    def test_min_bytes_skips_small_inbox(self):
        result = self.rotate("--min-bytes", str(len(self.before) + 1))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.inbox.read_bytes(), self.before)

    def test_bad_checkpoint_refuses_and_leaves_inbox_alone(self):
        checkpoint = pathlib.Path(self.state) / "checkpoints" / "leaving.json"
        saved = json.loads(checkpoint.read_text())
        saved["prefix_hash"] = "0" * 64
        checkpoint.write_text(json.dumps(saved))
        code, _, err = self.run_cli("inbox-rotate", "leaving")
        self.assertEqual(code, 2)
        self.assertIn("reconcile", err)
        self.assertEqual(self.inbox.read_bytes(), self.before)
        self.assertFalse((pathlib.Path(self.state) / "inbox-archive").exists())

    def handled_at(self, offset):
        handled = pathlib.Path(self.state) / "handled" / "leaving.json"
        handled.parent.mkdir(parents=True, exist_ok=True)
        cp = json.loads((pathlib.Path(self.state) / "checkpoints" / "leaving.json").read_text())
        data = self.inbox.read_bytes()
        cp.update(offset=offset, lines=data[:offset].count(b"\n"), prefix_hash=hashlib.sha256(data[:offset]).hexdigest())
        handled.write_text(json.dumps({"checkpoint": cp, "pending": {"open_work": ["keep me"]}}))
        return handled

    def test_handled_receipt_behind_delivery_refuses_without_flag(self):
        self.handled_at(0)
        code, _, err = self.run_cli("inbox-rotate", "leaving")
        self.assertEqual(code, 2)
        self.assertIn("--accept-delivered-boundary", err)
        self.assertEqual(self.inbox.read_bytes(), self.before)

    def test_handled_receipt_behind_delivery_moves_to_archive_with_flag(self):
        handled = self.handled_at(0)
        original = handled.read_text()
        result = self.rotate("--accept-delivered-boundary", "--note", "/tmp/state-of-record.md")
        self.assertFalse(handled.exists())
        self.assertEqual(pathlib.Path(result["handled_archive"]).read_text(), original)

    def test_handled_receipt_at_delivery_is_rebased_and_keeps_pending(self):
        handled = self.handled_at(len(self.before))
        self.rotate()
        receipt = json.loads(handled.read_text())
        self.assertEqual(receipt["checkpoint"]["offset"], 0)
        self.assertEqual(receipt["checkpoint"]["prefix_hash"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(receipt["pending"], {"open_work": ["keep me"]})

    def test_active_handover_refuses(self):
        thread = pathlib.Path(self.state) / "threads" / "leaving.json"
        record = json.loads(thread.read_text())
        record["handover_pending"] = "transition-x"
        thread.write_text(json.dumps(record))
        code, _, err = self.run_cli("inbox-rotate", "leaving")
        self.assertEqual(code, 2)
        self.assertIn("handover", err)

    def test_notification_pointers_move_to_the_archive(self):
        seen = pathlib.Path(self.state) / "notification-seen" / "leaving"
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text("{}")
        result = self.rotate()
        self.assertFalse(seen.exists())
        self.assertTrue(pathlib.Path(result["pointer_archive"]["seen"]).exists())

    def test_crash_after_swap_refuses_reads_then_recover_delivers_once(self):
        from unittest.mock import patch
        from coms import rotate as r
        self.send("TYPE: ASK\nTOPIC: carried across crash\nNEED: answer\nPROOF: /tmp/c")
        with patch.object(r, "_finish", side_effect=KeyboardInterrupt("crash")):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("inbox-rotate", "leaving")
        code, out, err = self.run_cli("inbox", "--consume")
        self.assertEqual(code, 2)
        self.assertIn("--recover", err)
        result = self.rotate("--recover")
        self.assertEqual(result["status"], "rotated")
        out = self.run_cli("inbox", "--consume")[1]
        self.assertEqual(out.count("carried across crash"), 1)
        self.assertIn("inbox empty", self.run_cli("inbox", "--consume")[1])

    def test_crash_before_swap_rolls_back(self):
        from unittest.mock import patch
        from coms import rotate as r
        real = r.os.replace
        def failing(src, dst):
            if dst.endswith("leaving.jsonl"):
                raise KeyboardInterrupt("crash before swap")
            return real(src, dst)
        with patch.object(r.os, "replace", side_effect=failing):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("inbox-rotate", "leaving")
        archive = pathlib.Path(self.state) / "inbox-archive"
        self.assertFalse((archive / "pending-leaving.json").exists())
        self.assertEqual(len(list(archive.glob("*.aborted.json"))), 1)
        self.assertEqual(self.inbox.read_bytes(), self.before)
        self.send("TYPE: ASK\nTOPIC: after abort\nNEED: answer\nPROOF: /tmp/d")
        self.assertIn("after abort", self.run_cli("inbox", "--consume")[1])
        self.assertEqual(self.rotate()["status"], "rotated")

    def killed_with_marker(self):
        """State after a SIGKILL between the pending marker and the swap (no handler ran)."""
        from coms import rotate as r
        from coms.inbox import atomic_json
        record = r.rotate(self.state, "leaving", dry_run=True)
        record["status"] = "pending"
        atomic_json(r.pending_path(self.state, "leaving"), record)
        return record

    def test_killed_before_swap_with_new_mail_rolls_back(self):
        self.killed_with_marker()
        self.send("TYPE: ASK\nTOPIC: arrived during the gap\nNEED: answer\nPROOF: /tmp/e")
        self.assertEqual(self.rotate("--recover")["status"], "rolled_back")
        out = self.run_cli("inbox", "--consume")[1]
        self.assertEqual(out.count("arrived during the gap"), 1)

    def test_killed_after_rebase_then_a_read_still_finishes(self):
        from unittest.mock import patch
        from coms import rotate as r
        seen = pathlib.Path(self.state) / "notification-seen" / "leaving"
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text("{}")
        real = r.os.replace
        def failing(src, dst):
            if dst.endswith(".notification-seen.json"):
                raise KeyboardInterrupt("killed after rebase")
            return real(src, dst)
        with patch.object(r.os, "replace", side_effect=failing):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli("inbox-rotate", "leaving")
        self.send("TYPE: ASK\nTOPIC: read between\nNEED: answer\nPROOF: /tmp/f")
        self.assertIn("read between", self.run_cli("inbox", "--consume")[1])
        self.assertEqual(self.rotate("--recover")["status"], "rotated")
        self.assertFalse(seen.exists())
        self.assertIn("inbox empty", self.run_cli("inbox", "--consume")[1])

    def test_dry_run_changes_nothing(self):
        result = self.rotate("--dry-run")
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(self.inbox.read_bytes(), self.before)

    def test_senders_still_reach_the_holder_after_rotation(self):
        self.rotate()
        Store(self.state, "sender").send_message("leaving", "late card")
        self.assertIn("late card", self.inbox.read_text())
