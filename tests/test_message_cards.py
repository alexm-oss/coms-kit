"""Focused message card validation, wake selection and physical inbox compatibility."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from coms import cli
from coms.inbox import Inbox, urgent
from coms.store import Store


def card(kind="ASK", body="Please read the patch."):
    return f"TYPE: {kind}\nTOPIC: message-card\nNEED: {'read patch' if kind in ('ASK', 'BLOCKER') else 'none'}\nPROOF: /tmp/proof.txt\n{body}"


class MessageCardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.environment = patch.dict(os.environ, {
            "COMS_DIR": str(self.state), "COMS_HOLDER": "reader",
            "COMS_SESSION_ID": "card-fixture", "COMS_MESSAGE_CARDS": "1",
            "COMS_HANDOVER_DIR": str(Path(self.tmp.name) / "handovers"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = Store(str(self.state), "reader")
        self.store.register()
        self.inbox = Inbox(self.store)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def send(self, text):
        # Store represents legacy writers as well as the canonical CLI.
        self.store.send_message("reader", text)

    def test_accept_all_types_and_preserve_five_body_lines(self):
        for kind in ("ASK", "RULING", "BLOCKER", "DONE", "FYI"):
            text = card(kind, "\n".join("Plain line " + str(i) for i in range(5)))
            self.assertEqual(self.run_cli("msg", "reader", text)[0], 0)
            self.assertEqual(self.store.read_inbox()[-1]["text"], text)

    def test_format_warnings_append_original_text_once_per_call(self):
        cases = [("plain legacy message", "TYPE"),
                 (card(body="\n".join(["line"] * 6)), "5 body lines"),
                 (card().replace("TOPIC: message-card\n", ""), "TOPIC"),
                 (card().replace("NEED: read patch", "NEED:"), "NEED"),
                 (card().replace("PROOF: /tmp/proof.txt\n", ""), "PROOF"),
                 (card("FYI").replace("TYPE: FYI\n", ""), "TYPE"),
                 (card().replace("/tmp/proof.txt", "/tmp/a /tmp/b"), "one path"),
                 (card().replace("/tmp/proof.txt", "https://example.test/proof"), "one path"),
                 (card(body="Words  squeezed together"), "spacing"),
                 (card(body="Words\ttogether"), "spacing"),
                 (card(body="z" * 41), "spaces")]
        for text, fix in cases:
            with self.subTest(fix=fix, text=text):
                before = len(self.store.read_inbox())
                code, _, err = self.run_cli("msg", "reader", text)
                self.assertEqual(code, 0, err)
                self.assertIn(fix, err)
                self.assertEqual(err.count("coms: warning:"), 1)
                self.assertIn("sending original text", err)
                self.assertEqual(len(self.store.read_inbox()), before + 1)
                self.assertEqual(self.store.read_inbox()[-1]["text"], text)

    def test_invalid_type_warns_and_appends_original(self):
        code, _, err = self.run_cli("msg", "reader", card("UNKNOWN"))
        self.assertEqual(code, 0)
        self.assertIn("TYPE must be", err)
        self.assertEqual(self.store.read_inbox()[-1]["text"], card("UNKNOWN"))

    def test_word_boundary_is_accepted_without_warning(self):
        code, _, err = self.run_cli("msg", "reader", card(body="z" * 40))
        self.assertEqual((code, err), (0, ""))

    def test_exempt_paths_urls_hashes_and_quoted_owner_words(self):
        bodies = ["/tmp/" + "z" * 90, "src/" + "z" * 90 + ".py",
                  "https://example.test/" + "z" * 90, "a9" * 32,
                  'Owner said "' + "z" * 90 + '"',
                  "Owner said '" + "z" * 90 + "'"]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(self.run_cli("msg", "reader", card(body=body))[0], 0)

    def test_transition_allows_untyped_but_still_validates_typed_cards(self):
        os.environ.pop("COMS_MESSAGE_CARDS")
        self.assertEqual(self.run_cli("msg", "reader", "old", "message")[0], 0)
        self.assertEqual(self.store.read_inbox()[0]["text"], "old message")
        text = card("FYI", "\n".join(["line"] * 6))
        code, _, err = self.run_cli("msg", "reader", text)
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(self.store.read_inbox()[-1]["text"], text)

    def test_all_action_types_wake_in_every_mode_from_any_writer(self):
        for kind in ("ASK", "RULING", "BLOCKER", "DONE"):
            for mode in ("normal", "night", "lowpower", "full"):
                self.assertTrue(urgent([{"from": "script", "text": card(kind)}], mode))

    def test_untyped_legacy_from_any_writer_always_wakes(self):
        for sender in ("watch-script", "codex-worker", "claude-worker"):
            for mode in ("normal", "night", "lowpower", "full"):
                self.assertTrue(urgent([{"from": sender, "text": "ACK routine update"}], mode))
        self.send("ACK routine update")
        code, out, _ = self.run_cli("inbox", "--consume", "--digest-mode", "night")
        self.assertEqual(code, 0)
        self.assertIn("ACK routine update", out)

    def test_fyi_only_watch_never_emits_or_advances_even_after_timer(self):
        self.send(card("FYI", "BLOCKED request slot is just quoted context."))
        receipt = Path(self.tmp.name) / "staged.json"
        for flags in (("--digest-mode", "full", "--batch-seconds", "0", "--consume"),
                      ("--follow", "--peek")):
            args = cli.build_parser().parse_args(["inbox", *flags, "--delivery-receipt", str(receipt)])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                count = self.inbox.deliver(args, cli._fmt_msg)
            self.assertEqual(count, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertFalse(receipt.exists())
            if Path(self.inbox.checkpoint).exists():
                self.assertEqual(json.loads(Path(self.inbox.checkpoint).read_text())["offset"], 0)
        self.assertIn("TYPE: FYI", self.run_cli("inbox", "--peek")[1])

    def test_mixed_watch_prints_all_originals_before_staged_boundary(self):
        for text in (card("FYI", "First update."), card("ASK"), "legacy original", card("FYI")):
            self.send(text)
        receipt = Path(self.tmp.name) / "staged.json"
        args = cli.build_parser().parse_args(["inbox", "--follow", "--peek", "--json",
                                             "--delivery-receipt", str(receipt)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.inbox.deliver(args, cli._fmt_msg), 4)
        originals = self.store.read_inbox()
        self.assertEqual([json.loads(line) for line in out.getvalue().splitlines()], originals)
        self.assertEqual(json.loads(receipt.read_text())["checkpoint"]["offset"], Path(self.inbox.path).stat().st_size)
        self.assertEqual(self.run_cli("checkpoint-deliver", str(receipt))[0], 0)
        self.assertIn("inbox empty", self.run_cli("inbox", "--peek")[1])
        self.assertEqual(json.loads(self.run_cli("inbox", "--all", "--json")[1]), originals)

    def test_digest_prints_complete_cards_but_preserves_legacy_print_cap(self):
        for text in (card("FYI", "/tmp/" + "z" * 700), "legacy " + "long text " * 100):
            self.send(text)
        code, out, err = self.run_cli("inbox", "--consume", "--digest-mode", "night", "--print-chars", "20")
        self.assertEqual(code, 0, err)
        self.assertIn(self.store.read_inbox()[0]["text"], out)
        self.assertNotIn(self.store.read_inbox()[1]["text"], out)
        self.assertIn("inbox empty", self.run_cli("inbox", "--peek")[1])

    def test_fyi_hooks_are_quiet_and_legacy_hooks_wake_without_delivery_ack(self):
        self.send(card("FYI"))
        for phase in ("context", "stop"):
            code, out, err = self.run_cli("hook-notifications", phase)
            self.assertEqual((code, out, err), (0, "", ""))
        self.assertFalse((self.state / "notification-seen" / "reader").exists())
        self.send("ACK helper legacy original")
        code, out, err = self.run_cli("hook-notifications", "stop")
        self.assertEqual(code, 10)
        self.assertIn("ACK helper legacy original", err)
        self.assertEqual(json.loads(self.run_cli("inbox", "--peek", "--json")[1]), self.store.read_inbox())

    def test_hook_action_after_five_fyi_rows_does_not_advance_delivery(self):
        for index in range(6):
            self.send(card("FYI", "Update " + str(index)))
        self.send(card("RULING", "Stop this lane."))
        code, out, err = self.run_cli("hook-notifications", "stop")
        self.assertEqual(code, 10)
        self.assertIn("2 later unread not shown", err)
        notified = json.loads((self.state / "notification-seen" / "reader").read_text())
        self.assertEqual(notified["lines"], 5)
        self.assertEqual(json.loads(self.run_cli("inbox", "--peek", "--json")[1]), self.store.read_inbox())

    def test_malformed_fyi_is_fail_safe_and_old_reads_remain_complete(self):
        self.send("TYPE: FYI\ninvalid old card")
        self.assertIn("invalid old card", self.run_cli("inbox", "--consume", "--digest-mode", "night")[1])
        self.send(card("FYI"))
        self.send("old plain message")
        code, out, _ = self.run_cli("inbox", "--consume", "--batch-read", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([row["text"] for row in json.loads(out)], [card("FYI"), "old plain message"])


if __name__ == "__main__":
    unittest.main()
