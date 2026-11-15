"""Exercise actual card parsing and the actual CLI before its Store boundary."""
import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

from coms import cli, message_cards


def card(kind, need):
    return (f"TYPE: {kind}\nTOPIC: need-lint\nNEED: {need}\n"
            "PROOF: /tmp/proof.txt\nPlease read the proof.")


class MessageCardNeedTest(unittest.TestCase):
    def run_cli(self, text, cards="1", *, holder="unit-card-need-holder", identity=None):
        store = Mock()
        out, err = io.StringIO(), io.StringIO()
        environment = {
            "COMS_MESSAGE_CARDS": cards,
            "COMS_SESSION_ID": "unit-card-need-session",
        }
        if holder is not None:
            environment["COMS_HOLDER"] = holder
        if identity is None:
            identity = Mock()
        # Drop inherited routing, credentials and state settings for this call.
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(cli, "_store", return_value=store) as factory:
                with patch.object(cli, "check_identity", identity):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = cli.main(["msg", "unit-recipient", text])
        return code, out.getvalue(), err.getvalue(), factory, store

    def test_missing_or_blank_holder_warns_and_delivers(self):
        for holder in (None, "", " \t "):
            text = card("ASK", "read patch")
            code, _, err, factory, store = self.run_cli(text, holder=holder)
            self.assertEqual(code, 0)
            self.assertIn("COMS_HOLDER", err)
            store.send_message.assert_called_once_with("unit-recipient", text)

    def test_valid_card_runs_actual_validation_and_identity_before_send(self):
        text = card("ASK", "read patch")

        def check_before_send(store, **kwargs):
            validate.assert_any_call(text)
            store.send_message.assert_not_called()

        identity = Mock(side_effect=check_before_send)
        with patch.object(message_cards, "validate_send",
                          wraps=message_cards.validate_send) as validate:
            code, _, err, factory, store = self.run_cli(text, identity=identity)
        self.assertEqual((code, err), (0, ""))
        validate.assert_any_call(text)
        for call in validate.call_args_list:
            self.assertEqual(call.args, (text,))
        identity.assert_called_once_with(store, strict=False)
        factory.assert_called()
        store.send_message.assert_called_once_with("unit-recipient", text)

    def test_invalid_card_warns_and_sends_unchanged(self):
        text = card("FYI", "read patch")
        code, _, err, factory, store = self.run_cli(text)
        self.assertEqual(code, 0)
        self.assertIn("ASK or BLOCKER", err)
        store.send_message.assert_called_once_with("unit-recipient", text)

    def test_identity_rejection_still_prevents_send(self):
        identity = Mock(side_effect=cli.IdentityError("unit identity refusal"))
        code, out, err, factory, store = self.run_cli(
            card("ASK", "read patch"), identity=identity)
        self.assertEqual(code, 75)
        self.assertEqual(out, "")
        self.assertIn("unit identity refusal", err)
        identity.assert_called_once_with(store, strict=False)
        factory.assert_called_once_with(None)
        store.send_message.assert_not_called()

    def test_fixture_isolates_and_restores_inherited_environment(self):
        inherited = {
            "COMS_HOLDER": "unit-inherited-holder",
            "COMS_SESSION_ID": "unit-inherited-session",
            "CODEX_THREAD_ID": "unit-inherited-thread",
            "COMS_DIR": "/unit-fixture-unused-state",
            "COMS_NOTIFY_SECRET": "unit-fixture-unused-secret",
        }
        seen = {}
        identity = Mock(side_effect=lambda store, **kwargs: seen.update(os.environ))
        with patch.dict(os.environ, inherited, clear=True):
            code, _, err, _, store = self.run_cli(
                card("ASK", "read patch"), identity=identity)
            self.assertEqual(dict(os.environ), inherited)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(seen, {
            "COMS_MESSAGE_CARDS": "1",
            "COMS_HOLDER": "unit-card-need-holder",
            "COMS_SESSION_ID": "unit-card-need-session",
        })
        identity.assert_called_once_with(store, strict=False)

    def test_actionable_fyi_done_ruling_refused_before_store_or_append(self):
        for kind in ("FYI", "DONE", "RULING"):
            for need in ("read patch", "none; read patch", "none;realneed"):
                with self.subTest(kind=kind, need=need):
                    code, out, err, factory, store = self.run_cli(card(kind, need))
                    self.assertEqual(code, 0)
                    self.assertIn("delivered", out)
                    self.assertIn("NEED", err)
                    self.assertIn("ASK or BLOCKER", err)
                    factory.assert_called()
                    store.send_message.assert_called()

    def test_actionable_ask_and_blocker_reach_send_unchanged(self):
        for kind in ("ASK", "BLOCKER"):
            text = card(kind, "none;realneed")
            with self.subTest(kind=kind):
                code, _, err, factory, store = self.run_cli(text)
                self.assertEqual((code, err), (0, ""))
                factory.assert_called()
                store.send_message.assert_called_once_with("unit-recipient", text)

    def test_literal_none_is_case_insensitive_and_all_types_still_valid(self):
        for kind in message_cards.TYPES:
            for need in ("none", "NONE", "NoNe"):
                with self.subTest(kind=kind, need=need):
                    text = card(kind, need)
                    self.assertEqual(message_cards.parse(text)["NEED"], need)
                    code, _, err, _, store = self.run_cli(text)
                    self.assertEqual((code, err), (0, ""))
                    store.send_message.assert_called_once_with("unit-recipient", text)

    def test_trimmed_none_warns_and_preserves_original(self):
        for need in (" none", "none ", " NONE "):
            with self.subTest(need=need):
                code, _, err, factory, store = self.run_cli(card("FYI", need))
                self.assertEqual(code, 0)
                self.assertIn("spacing", err)
                self.assertEqual(err.count("coms: warning:"), 1)
                self.assertNotIn("ASK or BLOCKER", err)
                store.send_message.assert_called_once_with("unit-recipient", card("FYI", need))

    def test_untyped_legacy_keeps_opt_in_behavior(self):
        text = "Old mail\nNEED: read patch\nTYPE: FYI quoted context"
        code, _, err, _, store = self.run_cli(text, cards="0")
        self.assertEqual((code, err), (0, ""))
        store.send_message.assert_called_once_with("unit-recipient", text)
        code, _, err, factory, store = self.run_cli(text)
        self.assertEqual(code, 0)
        self.assertIn("TYPE", err)
        factory.assert_called()
        store.send_message.assert_called()
        code, _, err, factory, store = self.run_cli(card("DONE", "read patch"), cards="0")
        self.assertEqual(code, 0)
        self.assertIn("ASK or BLOCKER", err)
        factory.assert_called()
        store.send_message.assert_called()

    def test_format_errors_cannot_hide_actionable_need(self):
        for kind in ("FYI", "DONE", "RULING"):
            text = card(kind, "read patch").replace("TOPIC: need-lint\n", "")
            with self.subTest(kind=kind):
                code, _, err, factory, store = self.run_cli(text)
                self.assertEqual(code, 0)
                self.assertIn("ASK or BLOCKER", err)
                factory.assert_called()
                store.send_message.assert_called()

    def test_need_header_variants_cannot_hide_actionable_need(self):
        for kind in ("FYI", "DONE", "RULING"):
            for header in ("Need:", "NEED :", "nEeD\t:"):
                text = card(kind, "read patch").replace("NEED:", header)
                with self.subTest(kind=kind, header=header):
                    code, out, err, factory, store = self.run_cli(text)
                    self.assertEqual(code, 0)
                    self.assertIn("delivered", out)
                    self.assertIn("ASK or BLOCKER", err)
                    factory.assert_called()
                    store.send_message.assert_called()

    def test_type_header_variants_keep_uppercase_action_values(self):
        for kind in ("ASK", "BLOCKER"):
            for header in ("Type:", "TYPE :", "tYpE\t:"):
                text = card(kind, "read patch").replace("TYPE:", header)
                with self.subTest(kind=kind, header=header):
                    code, _, err, _, store = self.run_cli(text)
                    self.assertEqual(code, 0)
                    self.assertEqual(err.count("coms: warning:"), 1)
                    store.send_message.assert_called_once_with("unit-recipient", text)

    def test_proof_header_variants_keep_body_header_words_in_body(self):
        for header in ("Proof:", "PROOF :", "pRoOf\t:"):
            text = (card("FYI", "none").replace("PROOF:", header)
                    + "\nTYPE: quoted context\nNeed : quoted body")
            with self.subTest(header=header):
                code, _, err, _, store = self.run_cli(text)
                self.assertEqual(code, 0)
                self.assertEqual(err.count("coms: warning:"), 1)
                store.send_message.assert_called_once_with("unit-recipient", text)
                self.assertTrue(message_cards.wakes({"text": text}))

    def test_misordered_proof_and_header_spacing_cannot_hide_need(self):
        texts = ["PROOF: /tmp/proof.txt\nTYPE: FYI\nNEED: read patch",
                 card("FYI", "read patch").replace("NEED:", " NEED:")]
        for text in texts:
            with self.subTest(text=text):
                code, _, err, factory, store = self.run_cli(text)
                self.assertEqual(code, 0)
                self.assertIn("ASK or BLOCKER", err)
                factory.assert_called()
                store.send_message.assert_called()

    def test_header_words_in_body_do_not_change_card_semantics(self):
        text = card("FYI", "none") + "\nTYPE: quoted context\nNEED: quoted body"
        code, _, err, _, store = self.run_cli(text)
        self.assertEqual((code, err), (0, ""))
        store.send_message.assert_called_once_with("unit-recipient", text)
        self.assertFalse(message_cards.wakes({"text": text}))

    def test_warning_does_not_bypass_session_identity_refusal(self):
        identity = Mock(side_effect=cli.IdentityError("unit session refusal"))
        code, out, err, factory, store = self.run_cli("legacy format", identity=identity)
        self.assertEqual(code, 75)
        self.assertEqual(out, "")
        self.assertEqual(err.count("coms: warning:"), 1)
        self.assertIn("unit session refusal", err)
        identity.assert_called_once_with(store, strict=False)
        store.send_message.assert_not_called()

    def test_wake_behavior_remains_fail_safe(self):
        for kind in message_cards.TYPES:
            need = "read patch" if kind in ("ASK", "BLOCKER") else "none"
            self.assertEqual(message_cards.wakes({"text": card(kind, need)}), kind != "FYI")
        malformed = ["legacy mail", card("FYI", "read patch"),
                     card("FYI", "none").replace("TOPIC: need-lint\n", ""),
                     card("FYI", "none").replace("/tmp/proof.txt", "/tmp/a /tmp/b"),
                     card("FYI", "none").replace("/tmp/proof.txt", "https://example.test/proof"),
                     card("FYI", "none") + "\nline" * 5,
                     card("FYI", "none") + "  squeezed",
                     card("FYI", "none") + "\n" + "z" * 41]
        for text in malformed:
            self.assertIsNone(message_cards.message_type({"text": text}))
            self.assertTrue(message_cards.wakes({"text": text}))


if __name__ == "__main__":
    unittest.main()
