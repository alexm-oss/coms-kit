import contextlib
import io
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/coms/bin/notify"
notify = runpy.run_path(str(SCRIPT))


class NotifyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enter = patch.dict(os.environ, {"COMS_NOTIFY_STATE_DIR": str(self.root / "state")}, clear=True)
        self.enter.start()
        self.addCleanup(self.enter.stop)

    def event(self, state):
        return notify["main"](["--source", "sample", "--state", state, "--message", "sample finding"])

    def test_disabled_and_command_transitions(self):
        self.assertEqual(self.event("warn"), 0)
        self.assertFalse((self.root / "state").exists())
        dest = self.root / "sink"
        dest.write_text('#!/bin/sh\ncat >> "$HOME/received"\n')
        dest.chmod(0o700)
        os.environ.update(HOME=str(self.root), COMS_NOTIFY_COMMAND=str(dest), COMS_NOTIFY_BACKEND="command")
        for event in ("ok", "warn", "warn", "ok", "warn"):
            self.assertEqual(self.event(event), 0)
        self.assertEqual((self.root / "received").read_text(), "sample finding\nsample finding\n")

    @patch("http.client.HTTPSConnection")
    def test_mock_http_destinations(self, connect):
        client = connect.return_value
        client.getresponse.return_value.status = 200
        client.getresponse.return_value.read.return_value = b'{"ok":true}'
        os.environ.update(COMS_NOTIFY_BACKEND="ntfy", COMS_NTFY_URL="https://notify.invalid/topic")
        self.assertEqual(self.event("warn"), 0)
        connect.assert_called_once_with("notify.invalid", timeout=10)
        client.request.assert_called_once_with("POST", "/topic", b"sample finding", {"Content-Type": "text/plain"})
        os.environ.update(COMS_NOTIFY_BACKEND="telegram", TELEGRAM_BOT_TOKEN="sample", TELEGRAM_CHAT_ID="fixture")
        self.assertEqual(self.event("other"), 0)
        self.assertEqual(client.request.call_args.args[:3],
                         ("POST", "/botsample/sendMessage", "chat_id=fixture&text=sample+finding"))

    @patch("http.client.HTTPSConnection")
    def test_unknown_delivery_is_quiet_on_repeat(self, connect):
        client = connect.return_value
        client.getresponse.side_effect = OSError("private fixture detail")
        os.environ.update(COMS_NOTIFY_BACKEND="ntfy", COMS_NTFY_URL="https://notify.invalid/topic")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(self.event("warn"), 1)
            self.assertEqual(self.event("warn"), 0)
        self.assertEqual(client.request.call_count, 1)
        self.assertNotIn("private fixture detail", errors.getvalue())
