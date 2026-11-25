"""Exercise public helper entry points with synthetic data and local mocks."""

import json
import os
import runpy
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from coms.decisions import DecisionLog
from coms.known_fixes import match_card, read_fixes
from coms.message_cards import parse

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "skills" / "coms" / "bin"
JOB = runpy.run_path(str(BIN / "job-health.py"))


def request(kind="ASK", need="Check the display", body="The sample panel is blank."):
    return f"TYPE: {kind}\nTOPIC: Display help\nNEED: {need}\nPROOF: fixture.txt\n{body}"


class MemoryEntryPoints(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name).resolve()
        self.state = self.home / "coms-state"
        self.env = {"HOME": str(self.home), "COMS_DIR": str(self.state),
                    "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}

    def coms(self, *args, holder="fixture-reader", check=True):
        env = dict(self.env, COMS_HOLDER=holder, COMS_SESSION_ID="fixture-" + holder)
        return subprocess.run([sys.executable, "-m", "coms.cli", *args], cwd=ROOT, env=env,
                              text=True, capture_output=True, check=check)

    def test_empty_starters_contain_no_rules(self):
        self.assertEqual(read_fixes(BIN / "known-fixes.txt"), [])
        self.assertEqual(sum("fictional" in line for line in (BIN / "known-fixes.txt").read_text().splitlines()), 2)
        self.coms("current-rules")
        page = (self.state / "current-rules.html").read_text()
        self.assertEqual(page.count("<p>None</p>"), 3)

    def test_catalog_match_uses_complete_cards_and_specific_whole_words(self):
        catalog = self.home / "fixes.txt"
        catalog.write_text("display | generic fix | display-check | basic guidance\n"
                           "blank panel | Refit the display. | display-tool | display fit\n"
                           "invalid entry\n")
        fixes = read_fixes(catalog)
        card = match_card(request("BLOCKER"), fixes)
        self.assertEqual(parse(card)["TYPE"], "RULING")
        self.assertIn('Fix: "Refit the display."', card)
        self.assertEqual(parse(card)["NEED"], "none")
        self.assertIsNone(match_card(request("FYI", "none"), fixes))
        self.assertIsNone(match_card("TYPE: ASK\n", fixes))
        fallback = match_card(request(body="The panel has a blanket."), fixes)
        self.assertIn("generic fix", fallback)

    def test_known_fixes_requires_opt_in_and_only_replies_once_to_the_allowed_sender(self):
        self.coms("enroll")
        self.state.joinpath("known-fixes.txt").write_text(
            "blank panel | Refit the display. | display-tool | display fit\n")
        for sender in ("fixture-writer", "fixture-other"):
            self.coms("enroll", holder=sender)
            self.coms("msg", "fixture-reader", request(), holder=sender)
        inbox = self.state / "inbox" / "fixture-reader.jsonl"
        rows = inbox.read_bytes().splitlines(keepends=True)
        with inbox.open("ab") as target:
            target.write(rows[0])

        rejected = self.coms("known-fixes", "--reply", check=False)
        self.assertEqual(rejected.returncode, 2)
        opts = ["--reply-to", "fixture-writer"]
        result = self.coms("known-fixes", *opts)
        self.assertIn("matches=1 replies=0 errors=0", result.stdout)
        inbox_args = ["inbox", "--all", "--json"]
        self.assertEqual(json.loads(self.coms(*inbox_args, holder="fixture-writer").stdout), [])

        result = self.coms("known-fixes", "--reply", *opts)
        self.assertIn("matches=1 replies=1 errors=0", result.stdout)
        self.assertIn("matches=0 replies=0 errors=0", self.coms("known-fixes", "--reply", *opts).stdout)
        self.assertFalse(self.state.joinpath("checkpoints", "fixture-reader.json").exists())
        outbox = json.loads(self.coms(*inbox_args, holder="fixture-writer").stdout)
        self.assertEqual(len(outbox), 1)
        response = outbox[0]
        self.assertEqual(response["from"], "fixture-reader")
        self.assertEqual(parse(response["text"])["TYPE"], "RULING")
        self.assertIn('Fix: "Refit the display."', response["text"])
        self.assertEqual(json.loads(self.coms(*inbox_args, holder="fixture-other").stdout), [])

        self.coms("msg", "fixture-reader", request(), holder="fixture-writer")
        self.assertIn("matches=1 replies=1 errors=0", self.coms("known-fixes", "--reply", *opts).stdout)
        self.assertEqual(len(json.loads(self.coms(*inbox_args, holder="fixture-writer").stdout)), 2)

    def test_known_fixes_reconciles_after_an_uncertain_append_and_rotation(self):
        self.coms("enroll")
        self.coms("enroll", holder="fixture-writer")
        self.coms("msg", "fixture-reader", request(), holder="fixture-writer")
        custom = self.state / "custom fixes.txt"
        custom.write_text("panel | Refit the display. | display-tool | display fit\n")
        args = ("known-fixes", "--catalog", str(custom), "--reply", "--reply-to", "fixture-writer")
        destination = self.state / "inbox" / "fixture-writer.jsonl"
        destination.write_text('{"incomplete":')
        rejected = self.coms(*args, check=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(destination.read_text(), '{"incomplete":')
        destination.unlink()

        # Force the actual Store to report an error after its recipient append.
        code = ("import sys; from coms.cli import main; from coms.store import Store; "
                "Store._touch_thread = lambda self: (_ for _ in ()).throw(OSError()); "
                "sys.exit(main())")
        env = dict(self.env, COMS_HOLDER="fixture-reader", COMS_SESSION_ID="fixture-fixture-reader")
        uncertain = subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT, env=env,
                                   text=True, capture_output=True, check=False)
        self.assertEqual(uncertain.returncode, 1)
        self.assertIn("errors=1", uncertain.stdout)
        original = json.loads(destination.read_text())
        self.assertEqual(parse(original["text"])["PROOF"], "./custom%20fixes.txt")
        self.coms("inbox", "--consume", holder="fixture-writer")
        self.coms("inbox-rotate", "fixture-writer", "--accept-delivered-boundary",
                  "--note", "fixture proof", holder="fixture-writer")
        self.assertEqual(destination.read_text(), "")
        self.assertIn("matches=0 replies=0 errors=0", self.coms(*args).stdout)
        self.assertEqual(destination.read_text(), "")

    def test_current_rules_follow_coms_current_and_render_pins_and_history(self):
        dates = iter([400, 300, 500, 600])
        decisions = DecisionLog(self.state, "fixture-decider", clock=dates.__next__)
        decisions.record("Gallery", "Old note", "Quote not shown")
        decisions.record(" GALLERY ", "Current <script>note</script>", "Quote not shown")
        decisions.record("Predecessor", "An earlier procedure", "Quote not shown")
        decisions.record("Successor", "Updated procedure", "Quote not shown", supersedes="Predecessor")
        self.state.joinpath("rule-pins.txt").write_text("gallery\npredecessor\n")
        self.coms("current-rules")
        page = self.state / "current-rules.html"
        text = page.read_text()
        pinned, current = text.split("<h2>Current</h2>", 1)
        live, past = current.split("<h2>Superseded or expired</h2>", 1)
        self.assertIn("Current &lt;script&gt;note&lt;/script&gt;", pinned)
        self.assertIn("Updated procedure", live)
        self.assertIn("Old note", past)
        self.assertIn("An earlier procedure", past)
        self.assertNotIn("An earlier procedure", pinned + live)
        self.assertNotIn("<script>", text)
        self.assertNotIn("Quote not shown", text)
        self.assertEqual(stat.S_IMODE(page.stat().st_mode), 0o600)

    def test_public_help(self):
        for name in ("known-fixes", "current-rules"):
            with self.subTest(name=name):
                self.assertIn(name, self.coms(name, "--help").stdout)
        command = [sys.executable, str(BIN / "job-health.py"), "--help"]
        help_text = subprocess.run(command, env=self.env, check=True, text=True, capture_output=True).stdout
        self.assertIn("broken-assets", help_text)


def result(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class JobHealth(unittest.TestCase):
    def test_launchd_parses_failures_never_run_missing_and_unknown(self):
        reason = JOB["launchd_reason"]
        sample = 'fixture {\n\tstate = not running\n\tlast exit code = 78: EX_CONFIG\n}'
        self.assertEqual(reason(result(sample)), "exit code 78")
        self.assertEqual(reason(result(sample.replace("78: EX_CONFIG", "0"))), "")
        self.assertEqual(reason(result(sample.replace("78: EX_CONFIG", "(never exited)"))), "")
        self.assertEqual(reason(result(sample.replace("last exit code = 78: EX_CONFIG",
                                                     "last terminating signal = Terminated: 15"))),
                         "terminated by signal")
        nested = "\tstate = running\n\targs = {\n\t\tlast exit code = 42\n\t}\n"
        self.assertIsNone(reason(result(nested)))
        self.assertEqual(reason(result(stderr="Could not find service", code=113)), "service missing")
        self.assertIsNone(reason(result(stderr="Permission denied", code=1)))

    def test_systemd_reads_timer_configured_unit_and_oneshot_outcome(self):
        reason = JOB["systemd_reason"]
        timer = result("LoadState=loaded\nActiveState=active\nResult=success\nUnit=custom.service\n")
        job = result("LoadState=loaded\nActiveState=inactive\nResult=success\n")
        invoke = Mock(side_effect=[timer, job])
        self.assertEqual(reason("fixture.timer", invoke=invoke), "")
        self.assertEqual([call.args[0][-1] for call in invoke.call_args_list], ["fixture.timer", "custom.service"])
        self.assertIn("--user", invoke.call_args.args[0])
        failing = result("LoadState=loaded\nActiveState=failed\nResult=exit-code\n")
        invoke = Mock(side_effect=[timer, failing])
        self.assertEqual(reason("fixture.timer", system=True, invoke=invoke), "failed (exit-code)")
        self.assertNotIn("--user", invoke.call_args.args[0])
        missing = result("LoadState=not-found\nActiveState=inactive\n", code=4)
        self.assertEqual(reason("fixture.timer", invoke=Mock(return_value=missing)), "timer unavailable")
        self.assertIsNone(reason("fixture.timer", invoke=Mock(return_value=result(code=1))))

    def test_notification_transition_only_and_probe_unknown_preserves_last_result(self):
        scan = JOB["scan"]
        source = scan.__globals__
        with TemporaryDirectory() as folder, patch.dict(source, {"call": Mock(return_value=result())}):
            path = Path(folder) / "broken-assets.json"
            notifier = Path(folder) / "mock-notify"
            assets = ["fixture-private-job.timer"]
            hook = source["call"]
            scan(path, assets, Mock(return_value=""), notifier)
            hook.assert_not_called()
            failing = Mock(return_value="timer inactive")
            scan(path, assets, failing, notifier)
            scan(path, assets, failing, notifier)
            scan(path, assets, Mock(return_value=None), notifier)
            hook.assert_called_once()
            payload = json.loads(path.read_text())
            self.assertEqual(payload["broken"], [{"asset": assets[0], "reason": "timer inactive"}])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn(assets[0], " ".join(hook.call_args.args[0]))
            self.assertIn("Monitored jobs needing attention: 1.", " ".join(hook.call_args.args[0]))
            scan(path, assets, Mock(return_value=""), notifier)
            scan(path, assets, Mock(return_value=""), notifier)
            self.assertEqual(hook.call_count, 2)
            self.assertEqual(hook.call_args.args[0][4], "ok")
            self.assertEqual(json.loads(path.read_text())["broken"], [])

    def test_failed_notification_retried_without_refreshing_unchanged_success(self):
        scan = JOB["scan"]
        notifier_results = Mock(side_effect=[result(code=1), result()])
        with TemporaryDirectory() as folder, patch.dict(scan.__globals__, {"call": notifier_results}):
            path = Path(folder) / "broken-assets.json"
            assets = ["fixture.timer"]
            probe = Mock(return_value="timer inactive")
            for _ in range(3):
                scan(path, assets, probe, Path(folder) / "mock-notify")
            self.assertEqual(notifier_results.call_count, 2)


    def test_job_health_calls_the_public_hook_through_a_local_mock_command(self):
        with TemporaryDirectory() as folder:
            home = Path(folder).resolve()
            env = {"HOME": str(home), "COMS_DIR": str(home / "state"),
                   "COMS_NOTIFY_BACKEND": "command", "PATH": str(home) + os.pathsep + os.defpath}
            python = home / "python3"
            python.symlink_to(sys.executable)
            tool = home / "launchctl"
            tool.write_text('#!/bin/sh\nprintf "\tstate = not running\n\tlast exit code = %s\n" '
                            '"$(cat "$HOME/test-status")"\n')
            sink = home / "mock-delivery"
            sink.write_text('#!/bin/sh\ncat >> "$HOME/test-outbox"\n')
            for fixture in (tool, sink):
                fixture.chmod(0o700)
            env["COMS_NOTIFY_COMMAND"] = str(sink)
            command = [sys.executable, str(BIN / "job-health.py"), "--backend", "launchd",
                       "--asset", "sample.task"]

            def check(*args):
                return subprocess.run(command + list(args), env=env, check=True, text=True,
                                      capture_output=True)

            status = home / "test-status"
            outbox = home / "test-outbox"
            status.write_text("42")
            self.assertIn("checked=1 broken=1 unknown=0", check().stdout)
            self.assertFalse(outbox.exists())
            check("--notify")
            check("--notify")
            self.assertEqual(len(outbox.read_text().splitlines()), 1)
            status.write_text("0")
            check("--notify")
            self.assertEqual(len(outbox.read_text().splitlines()), 1)
            status.write_text("42")
            check("--notify")
            self.assertEqual(len(outbox.read_text().splitlines()), 2)

            # A failing sink may have accepted the request. Repeats stay
            # unconfirmed without a second delivery, and recovery rearms it.
            status.write_text("0")
            check("--notify")
            sink.write_text('#!/bin/sh\ncat >> "$HOME/test-outbox"\nexit 1\n')
            status.write_text("42")
            check("--notify")
            check("--notify")
            saved = home / "state" / "broken-assets.json"
            self.assertEqual(json.loads(saved.read_text())["notified"], "unconfirmed")
            self.assertEqual(len(outbox.read_text().splitlines()), 3)
            status.write_text("0")
            check("--notify")
            self.assertEqual(json.loads(saved.read_text())["notified"], "ok")
            sink.write_text('#!/bin/sh\ncat >> "$HOME/test-outbox"\n')
            status.write_text("42")
            check("--notify")
            self.assertNotEqual(json.loads(saved.read_text())["notified"], "unconfirmed")
            self.assertEqual(len(outbox.read_text().splitlines()), 4)
