"""Message card checks: NEED and TYPE rules, PROOF paths, deprecated knobs and wake rules."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from tests import test_message_cards as fixtures

ROOT = Path(__file__).resolve().parent.parent


class CardDeltaTest(unittest.TestCase):
    setUp = fixtures.MessageCardsTest.setUp
    run_cli = fixtures.MessageCardsTest.run_cli
    send = fixtures.MessageCardsTest.send

    def test_hash_punctuation_and_sha256_prefix_are_exempt(self):
        for token in ("a9" * 20 + ".", "a9" * 20 + ":", "sha256:" + "a9" * 32 + "."):
            with self.subTest(token=token):
                code, _, err = self.run_cli("msg", "reader", fixtures.card(body=token))
                self.assertEqual(code, 0, err)

    def test_proof_error_explains_required_path_marker(self):
        text = fixtures.card().replace("/tmp/proof.txt", "packet")
        code, _, err = self.run_cli("msg", "reader", text)
        self.assertEqual(code, 0)
        self.assertIn("'/' or '.'", err)
        self.assertEqual(self.store.read_inbox()[-1]["text"], text)

    def test_explicit_history_and_reset_bypass_fyi_quiet_return(self):
        self.send(fixtures.card("FYI"))
        self.run_cli("inbox", "--consume")
        for flags in (("--all",), ("--reset-checkpoint", "--consume")):
            with self.subTest(flags=flags):
                code, out, err = self.run_cli("inbox", *flags, "--digest-mode", "normal", "--json")
                self.assertEqual(code, 0, err)
                self.assertTrue(out.strip(), "explicit history or reset must print the FYI original")
                self.assertEqual(json.loads(out), self.store.read_inbox())
        saved = json.loads(Path(self.inbox.checkpoint).read_text())
        self.assertEqual(saved["offset"], Path(self.inbox.path).stat().st_size)

    def test_legacy_print_cap_is_preserved_in_a_mixed_watch(self):
        fyi, legacy = fixtures.card("FYI", "/tmp/" + "z" * 700), "old " + "long text " * 100
        self.send(fyi)
        self.send(legacy)
        code, out, err = self.run_cli("inbox", "--consume", "--digest-mode", "normal", "--print-chars", "20")
        self.assertEqual(code, 0, err)
        self.assertIn(fyi, out)
        self.assertNotIn(legacy, out)
        self.assertEqual(len(out.splitlines()[-1]), 20)
        self.assertTrue(out.splitlines()[-1].endswith("reader: o"))
        self.assertEqual(self.store.read_inbox()[-1]["text"], legacy)

    def test_deprecated_knobs_emit_one_combined_line_but_no_fyi_mail(self):
        self.send(fixtures.card("FYI"))
        with patch.dict(os.environ, INBOX_URGENT_FROM="writer", INBOX_EXTRA_URG="ASK", INBOX_ROUTINE_RE="ACK"):
            code, out, err = self.run_cli("inbox", "--digest-mode", "normal", "--peek", "--batch-seconds", "1800")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("deprecated", err)
        for name in ("--batch-seconds", "INBOX_URGENT_FROM", "INBOX_EXTRA_URG", "INBOX_ROUTINE_RE"):
            self.assertIn(name, err)

    def test_docs_describe_types_manual_fyi_and_deprecated_knobs(self):
        readme = (ROOT / "skills/coms/bin/README.md").read_text()
        self.assertNotIn("urgent lines at once, the rest batched", readme)
        self.assertIn("FYI", readme)
        self.assertIn("Untyped", readme)
        for name in ("INBOX_URGENT_FROM", "INBOX_EXTRA_URG", "INBOX_ROUTINE_RE"):
            self.assertIn(name, readme)

    def test_flag_off_preserves_legacy_need_lines_but_first_type_validates(self):
        os.environ.pop("COMS_MESSAGE_CARDS")
        legacy = "Old mail\nNEED: please read\nTYPE: this is quoted context"
        code, _, err = self.run_cli("msg", "reader", legacy)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.store.read_inbox()[0]["text"], legacy)
        for sender in ("watch-script", "codex-worker", "claude-worker"):
            self.assertTrue(fixtures.urgent([{"from": sender, "text": legacy}], "night"))
        self.assertIn(legacy, self.run_cli("inbox", "--consume", "--digest-mode", "night")[1])
        text = "TYPE: FYI\nmalformed"
        code, _, err = self.run_cli("msg", "reader", text)
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertEqual(self.store.read_inbox()[-1]["text"], text)

    def test_default_digest_wrapper_remains_quiet_after_deprecation_change(self):
        self.send(fixtures.card("FYI"))
        launch = Path(self.tmp.name) / "bin/coms"
        launch.parent.mkdir()
        launch.write_text('#!/usr/bin/env python3\nfrom coms.cli import main\nraise SystemExit(main())\n')
        launch.chmod(0o700)
        env = dict(os.environ, PATH=str(launch.parent) + os.pathsep + os.environ["PATH"], PYTHONPATH=str(ROOT))
        for key in ("INBOX_BATCH_S", "INBOX_URGENT_FROM", "INBOX_EXTRA_URG", "INBOX_ROUTINE_RE"):
            env.pop(key, None)
        result = subprocess.run(["bash", str(ROOT / "skills/coms/bin/inbox-digest.sh"), "--once"],
                                env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
