"""Adversarial account transition, delivery recovery and routing regressions."""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from coms import cli
from coms.identity import IdentityError
from coms.inbox import Inbox
from coms.store import Busy, Store

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name).resolve()
        self.state = str(self.home / "state")
        self.notes = self.home / "handovers"
        self.env = {"HOME": str(self.home), "COMS_DIR": self.state,
                    "COMS_HANDOVER_DIR": str(self.notes), "COMS_HOLDER": "leaving",
                    "COMS_SESSION_ID": "session-leaving", "PATH": os.environ["PATH"],
                    "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        Store(self.state, "leaving").register(role="codex-lead")
        self.loops = self.home / "loops.json"
        self.loops.write_text(json.dumps({"open_work": ["finish approved lane"],
                                        "owner_questions": ["pending scope ruling"],
                                        "routines": ["watch from saved checkpoint"],
                                        "pending_handoffs": [], "evidence": []}))

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def as_holder(self, holder):
        os.environ.update(COMS_HOLDER=holder, COMS_SESSION_ID="session-" + holder)

    def enroll(self, holder, role="codex-lead"):
        self.as_holder(holder)
        code, _, err = self.run_cli("enroll", "--role", role)
        self.assertEqual(code, 0, err)

    def send(self, text, stamp=10):
        Store(self.state, "sender", clock=lambda: stamp).send_message(os.environ["COMS_HOLDER"], text)

    def note(self, role="codex-lead"):
        self.enroll("holding", "orchestrator" if role == "codex-lead" else "codex-lead")
        self.enroll("leaving", role)
        code, out, err = self.run_cli("handover", "note", "--to", "holding", "--role", role,
                                    "--open-loops", str(self.loops), "--id", "transition-test")
        self.assertEqual(code, 0, err)
        return pathlib.Path(out.strip())


class SwitchoverTest(Sandbox):
    def test_fifteen_messages_before_first_watch_are_not_skipped(self):
        for n in range(15):
            self.send("QUESTION " + str(n))
        code, out, _ = self.run_cli("inbox")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("QUESTION"), 15)
        self.assertIn("inbox empty", self.run_cli("inbox")[1])
        self.send("arrived between restarts", stamp=1)
        self.assertIn("arrived between restarts", self.run_cli("inbox")[1])

    def test_same_timestamp_and_clock_regression_mail_is_read(self):
        self.send("first", 20)
        self.run_cli("inbox")
        self.send("equal", 20)
        self.send("older", 19)
        out = self.run_cli("inbox")[1]
        self.assertIn("equal", out)
        self.assertIn("older", out)

    def test_partial_trailing_json_is_preserved_until_newline(self):
        path = pathlib.Path(Store(self.state, "leaving")._inbox_path("leaving"))
        path.parent.mkdir(parents=True)
        path.write_text('{"from":"sender","ts":1,"text":"partial"}')
        self.assertIn("empty", self.run_cli("inbox")[1])
        with path.open("a") as target:
            target.write("\n")
        self.assertIn("partial", self.run_cli("inbox")[1])

    def test_malformed_complete_line_warns_and_advances_checkpoint(self):
        self.send("keep")
        inbox = Inbox(Store(self.state, "leaving"))
        with open(inbox.path, "a") as target:
            target.write("[]\n")
        code, _, err = self.run_cli("inbox")
        self.assertEqual(code, 0)
        self.assertIn("malformed", err)
        self.assertEqual(json.loads(pathlib.Path(inbox.checkpoint).read_text())["offset"], pathlib.Path(inbox.path).stat().st_size)

    def test_same_size_replacement_warns_and_resyncs(self):
        self.send("first")
        self.run_cli("inbox")
        inbox = Inbox(Store(self.state, "leaving"))
        path = pathlib.Path(inbox.path)
        path.write_text(path.read_text().replace("first", "other"))
        code, _, err = self.run_cli("inbox")
        self.assertEqual(code, 0)
        self.assertIn("resyncing", err)
        self.assertIn("other", self.run_cli("inbox", "--reset-checkpoint")[1])

    def test_corrupt_checkpoint_warns_and_replays_complete_lines(self):
        self.send("first")
        self.run_cli("inbox")
        inbox = Inbox(Store(self.state, "leaving"))
        pathlib.Path(inbox.checkpoint).write_text("{broken")
        self.assertEqual(self.run_cli("inbox")[0], 0)

    def test_peek_does_not_advance_and_all_keeps_history(self):
        self.send("first")
        self.assertIn("first", self.run_cli("inbox", "--peek")[1])
        self.assertIn("first", self.run_cli("inbox")[1])
        self.assertIn("first", self.run_cli("inbox", "--all")[1])
        self.assertIn("empty", self.run_cli("inbox")[1])

    def test_checkpoint_permissions_and_prefix(self):
        self.send("first")
        self.run_cli("inbox")
        path = pathlib.Path(Inbox(Store(self.state, "leaving")).checkpoint)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["lines"], 1)
        self.assertEqual(len(saved["prefix_hash"]), 64)

    def test_wrong_live_session_cannot_read_or_rebind_but_other_commands_warn(self):
        self.enroll("leaving")
        self.send("private inbox")
        os.environ["COMS_SESSION_ID"] = "foreign-session"
        for args in (("inbox",), ("enroll",)):
            code, out, err = self.run_cli(*args)
            self.assertEqual(code, 75, (args, err))
            self.assertNotIn("private inbox", out)
            self.assertIn("different live session", err)
        for args in (("msg", "holding", "warning transport"),
                     ("do", "repo:main", "--wait", "0", "--", "true")):
            code, _, err = self.run_cli(*args)
            self.assertEqual(code, 0, err)
            self.assertIn("warning", err)

    def test_missing_marker_cannot_read_bound_session(self):
        self.enroll("leaving")
        del os.environ["COMS_SESSION_ID"]
        self.assertEqual(self.run_cli("inbox")[0], 75)

    def test_native_marker_is_checked_without_explicit_override(self):
        os.environ.pop("COMS_SESSION_ID")
        os.environ["COMS_HOLDER"] = "native-window"
        os.environ["CODEX_THREAD_ID"] = "native-owner"
        self.assertEqual(self.run_cli("enroll")[0], 0)
        os.environ["CODEX_THREAD_ID"] = "native-foreign"
        self.assertEqual(self.run_cli("inbox")[0], 75)

    def test_matching_user_controlled_marker_is_not_authentication(self):
        self.enroll("leaving")
        # A deliberate same-user process can copy this marker. Record the boundary.
        self.send("cooperative message")
        self.assertEqual(self.run_cli("inbox")[0], 0)

    def test_holder_boundary_rejects_aliases_and_path_injection(self):
        for holder in ("../escape", "a/b", "a b", "", ".hidden"):
            with self.assertRaises(IdentityError):
                Store(self.state, holder)

    def test_same_holder_cannot_replace_live_lease_token(self):
        store = Store(self.state, "leaving")
        first = store.acquire("repo:main")
        with self.assertRaises(Busy):
            Store(self.state, "leaving").acquire("repo:main")
        self.assertEqual(store.status()["resources"]["repo:main"]["token"], first.token)

    def test_corrupt_state_preserves_bytes_and_never_resets_fence(self):
        store = Store(self.state, "leaving")
        store.acquire("repo:main")
        path = pathlib.Path(store.state_path)
        path.write_text("{")
        with self.assertRaises(ValueError):
            store.acquire("repo:ship")
        self.assertEqual(path.read_text(), "{")

    def test_invalid_wait_and_ttl_are_refused(self):
        for bad in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                Store(self.state, "leaving").acquire("repo:main", wait=bad)
            with self.assertRaises(ValueError):
                Store(self.state, "leaving", ttl=bad)

    def test_both_directions_hold_relay_accept_then_retire(self):
        for role in ("codex-lead", "orchestrator", "controller"):
            with self.subTest(role=role):
                # Each transition has a separate store and notes, never a live runtime.
                os.environ["COMS_DIR"] = self.state + "-" + role
                os.environ["COMS_HANDOVER_DIR"] = str(self.notes) + "-" + role
                self.state = os.environ["COMS_DIR"]
                note = self.note(role)
                self.assertEqual(note.stat().st_mode & 0o777, 0o600)
                self.assertEqual(note.parent.stat().st_mode & 0o777, 0o700)
                self.enroll("successor", role)
                self.as_holder("holding")
                self.assertEqual(self.run_cli("handover", "relay", str(note), "--to", "successor")[0], 0)
                self.as_holder("successor")
                self.assertIn("HANDOVER OPEN LOOPS", self.run_cli("inbox")[1])
                self.assertEqual(self.run_cli("handover", "accept", str(note), "--confirm-ready")[0], 0)
                self.as_holder("holding")
                self.assertEqual(self.run_cli("handover", "retire", str(note), "--routes-confirmed")[0], 0)
                self.as_holder("leaving")
                self.assertEqual(self.run_cli("enroll")[0], 75)

    def test_note_contains_newest_decisions_lanes_questions_and_unread(self):
        self.send("pending original")
        self.run_cli("decide", "scope", "old", "--quote", "old ruling")
        self.run_cli("decide", "scope", "new", "--quote", "new ruling")
        note = self.note()
        packet = json.loads(note.read_text())
        self.assertEqual(packet["decisions"][0]["answer"], "new")
        self.assertEqual(len(packet["decisions"]), 1)
        self.assertIn("pending scope ruling", packet["open_loops"]["owner_questions"])
        self.assertEqual(packet["unread"][0]["text"], "pending original")
        self.assertTrue(packet["lanes"])

    def test_live_operation_is_preserved_and_prepared_note_survives(self):
        self.enroll("holding")
        self.enroll("leaving")
        lease = Store(self.state, "leaving").acquire("repo:main")
        code, _, err = self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                  "--open-loops", str(self.loops), "--id", "prepared")
        self.assertEqual(code, 2)
        self.assertIn("still running", err)
        self.assertEqual(Store(self.state, "leaving").status()["resources"]["repo:main"]["token"], lease.token)
        self.assertEqual(json.loads((self.notes / "prepared.json").read_text())["phase"], "prepared")

    def test_holding_relay_keeps_late_mail_and_deduplicates_per_destination(self):
        note = self.note()
        self.as_holder("leaving")
        self.send("late original")
        self.as_holder("holding")
        for destination in ("holding", "new-holder"):
            self.assertEqual(self.run_cli("handover", "relay", "--replay", str(note), "--to", destination)[0], 0)
            self.assertIn("relayed: 0", self.run_cli("handover", "relay", "--replay", str(note), "--to", destination)[1])
            rows = Store(self.state, destination).read_inbox()
            self.assertEqual(sum("late original" in m["text"] for m in rows), 1)

    def test_early_retirement_and_wrong_holding_session_are_refused(self):
        note = self.note()
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "retire", str(note), "--routes-confirmed")[0], 2)
        self.as_holder("outsider")
        self.assertEqual(self.run_cli("handover", "relay", str(note))[0], 2)

    def test_duplicate_note_and_symlink_input_are_refused(self):
        self.note()
        self.as_holder("leaving")
        self.assertEqual(self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                     "--open-loops", str(self.loops), "--id", "transition-test")[0], 2)
        link = self.home / "linked.json"
        link.symlink_to(self.loops)
        self.assertEqual(self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                     "--open-loops", str(link))[0], 2)

    def test_credential_like_prose_is_redacted_without_printing_values(self):
        self.enroll("holding")
        self.enroll("leaving")
        loops = json.loads(self.loops.read_text())
        loops["open_work"] = ["password=synthetic-private-value"]
        self.loops.write_text(json.dumps(loops))
        code, out, err = self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                    "--open-loops", str(self.loops))
        self.assertEqual(code, 0, err)
        self.assertNotIn("synthetic-private-value", out + err)
        note = pathlib.Path(out.strip())
        self.assertNotIn("synthetic-private-value", note.read_text())
        self.assertIn("REDACTED", note.read_text())

    def test_inbox_scripts_require_holder_before_creating_state(self):
        import shutil
        shutil.rmtree(self.state)
        env = dict(self.env)
        env.pop("COMS_HOLDER")
        for name in ("watch-once.sh", "watch-all.sh", "read-batch.sh", "inbox-digest.sh", "handover-note"):
            result = subprocess.run([str(ROOT / "skills/coms/bin" / name)], env=env,
                                    capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("COMS_HOLDER", result.stderr)
        self.assertFalse(pathlib.Path(self.state).exists())

    def test_broken_stdout_never_acknowledges_inbox(self):
        self.send("keep after failed output")
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        try:
            result = subprocess.run([sys.executable, "-u", "-m", "coms.cli", "inbox", "--consume", "--follow"],
                                    env=self.env, stdout=write_fd, stderr=subprocess.PIPE, timeout=3)
        finally:
            os.close(write_fd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(pathlib.Path(Inbox(Store(self.state, "leaving")).checkpoint).read_text())["offset"], 0)


if __name__ == "__main__":
    unittest.main()
