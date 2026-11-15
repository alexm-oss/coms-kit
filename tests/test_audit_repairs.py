"""Original senior audit regressions and independent review failure cases."""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from coms import cli
from coms.handover import safe_payload
from coms.inbox import Inbox
from coms.store import Store
from tests.test_switchover import Sandbox, ROOT


class AuditRepairTest(Sandbox):
    def test_same_holder_parallel_commands_serialize_real_children(self):
        sequence = self.home / "sequence"
        script = "import pathlib,sys,time;p=pathlib.Path(sys.argv[1]);f=p.open('a');f.write('S');f.flush();time.sleep(.2);f.write('E');f.close()"
        argv = [sys.executable, "-m", "coms.cli", "do", "repo:main", "--wait", "2", "--",
                sys.executable, "-c", script, str(sequence)]
        workers = [subprocess.Popen(argv, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for worker in workers:
            _, err = worker.communicate(timeout=5)
            self.assertEqual(worker.returncode, 0, err)
        self.assertEqual(sequence.read_text(), "SESE")

    def test_explicit_store_is_inherited_and_check_requires_current_token(self):
        explicit = self.home / "selected"
        Store(self.state, "other").acquire("repo:main")
        args = [sys.executable, "-m", "coms.cli", "--dir", str(explicit), "do", "repo:main", "--",
                sys.executable, "-m", "coms.cli", "check", "repo:main"]
        result = subprocess.run(args, env=self.env, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        lease = Store(self.state, "leaving").acquire("repo:ship")
        self.assertEqual(self.run_cli("check", "repo:ship")[0], 3)
        with patch.dict(os.environ, COMS_LEASE_KEY="repo:ship", COMS_LEASE_TOKEN=str(lease.token)):
            self.assertEqual(self.run_cli("check", "repo:ship")[0], 0)

    def test_parallel_registry_updates_keep_sticky_role(self):
        self.enroll("leaving", "orchestrator")
        failures = []
        def update():
            try:
                for _ in range(20):
                    Store(self.state, "leaving").register(intent="new status")
                    Store(self.state, "leaving")._touch_thread()
            except Exception as exc:
                failures.append(type(exc).__name__)
        workers = [threading.Thread(target=update) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(Store(self.state, "leaving").threads()[0]["role"], "orchestrator")

    def test_launcher_paths_are_literal_and_existing_skill_symlink_survives(self):
        for name in ('repo$(touch shell-marker)', 'repo`touch backtick-marker`', 'repo "quoted" spaces'):
            build = self.home / name
            build.mkdir()
            for folder in ("coms", "hooks", "skills"):
                shutil.copytree(ROOT / folder, build / folder)
            shutil.copyfile(ROOT / "install.sh", build / "install.sh")
            runtime = self.home / ("runtime" + str(len(name)))
            runtime.mkdir(exist_ok=True)
            skills = runtime / "skills"
            skills.mkdir(exist_ok=True)
            external = runtime / "canonical"
            external.mkdir(exist_ok=True)
            (external / "sentinel").write_text("preserve")
            (skills / "coms").symlink_to(external)
            env = dict(self.env, COMS_BIN_DIR=str(runtime / "bin"), COMS_SKILLS_DIR=str(skills),
                       COMS_STATE_DIR=str(runtime / "state"), COMS_HOOKS_DIR=str(runtime / "hooks"))
            refused = subprocess.run(["bash", str(build / "install.sh")], env=env,
                                     capture_output=True, text=True, cwd=self.home, timeout=3)
            self.assertEqual(refused.returncode, 1, refused.stderr)
            self.assertIn("Nothing was changed", refused.stderr)
            install = subprocess.run(["bash", str(build / "install.sh"), "--cli-only"], env=env,
                                     capture_output=True, text=True, cwd=self.home, timeout=3)
            self.assertEqual(install.returncode, 0, install.stderr)
            run = subprocess.run([str(runtime / "bin/coms"), "status", "--json"], env=env,
                                 capture_output=True, text=True, cwd=self.home, timeout=3)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertFalse((self.home / "shell-marker").exists())
            self.assertFalse((self.home / "backtick-marker").exists())
            self.assertTrue((skills / "coms").is_symlink())
            self.assertEqual((external / "sentinel").read_text(), "preserve")

    def test_first_quiet_backlog_stays_unread_after_ignored_timer(self):
        text = "TYPE: FYI\nTOPIC: quiet backlog\nNEED: none\nPROOF: tests/backlog.txt\nroutine quiet backlog"
        self.send(text)
        inbox = Inbox(Store(self.state, "leaving"))
        checkpoint = pathlib.Path(inbox.checkpoint)
        original = pathlib.Path(inbox.path).read_bytes()
        for stamp in (100, 120):
            with patch("coms.inbox.time.time", return_value=stamp):
                code, out, err = self.run_cli("inbox", "--consume", "--digest-mode", "night", "--batch-seconds", "10")
            self.assertEqual((code, out), (0, ""), err)
            self.assertIn("deprecated inbox knobs ignored: --batch-seconds", err)
            saved = json.loads(checkpoint.read_text())
            self.assertEqual((saved["offset"], saved["lines"], saved["last_batch_at"]), (0, 0, 100))
            self.assertEqual(pathlib.Path(inbox.path).read_bytes(), original)
        code, out, err = self.run_cli("inbox", "--peek", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([row["text"] for row in json.loads(out)], [text])
        self.assertEqual(json.loads(checkpoint.read_text())["offset"], 0)
        code, out, err = self.run_cli("inbox", "--consume", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([row["text"] for row in json.loads(out)], [text])
        self.assertEqual(json.loads(checkpoint.read_text())["offset"], len(original))
        self.assertFalse((pathlib.Path(self.state) / "handled/leaving.json").exists())
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 0, err)
        self.assertEqual([row["text"] for row in json.loads(out)["messages"]], [text])

    def test_legacy_and_malformed_cards_wake_despite_ignored_night_timer(self):
        for text in ("routine legacy backlog", "TYPE: FYI\ninvalid old card"):
            with self.subTest(text=text):
                self.send(text)
                code, out, err = self.run_cli("inbox", "--consume", "--digest-mode", "night", "--batch-seconds", "10")
                self.assertEqual(code, 0, err)
                self.assertIn(text, out)
                self.assertIn("deprecated inbox knobs ignored: --batch-seconds", err)
                self.assertIn("inbox empty", self.run_cli("inbox", "--peek")[1])

    def test_structured_credentials_are_refused_without_value_output(self):
        for key in ("password", "authorization", "access_token", "api_key", "cookie"):
            with self.assertRaisesRegex(ValueError, "credential"):
                safe_payload({"open_work": [{key: "synthetic-private"}]})

    def test_frozen_unread_survives_departing_read_then_relay(self):
        self.send("frozen original")
        note = self.note()
        self.as_holder("leaving")
        self.assertIn("frozen original", self.run_cli("inbox")[1])
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "relay", "--replay", str(note), "--to", "replacement")[0], 0)
        self.assertTrue(any("frozen original" in m["text"] for m in Store(self.state, "replacement").read_inbox()))

    def test_retired_recipient_refuses_new_mail_and_retired_touch_cannot_revive(self):
        note = self.note()
        self.enroll("successor")
        self.as_holder("holding")
        self.run_cli("handover", "relay", str(note), "--to", "successor")
        self.as_holder("successor")
        self.run_cli("handover", "accept", str(note), "--confirm-ready")
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "retire", str(note), "--routes-confirmed")[0], 0)
        before = len(Store(self.state, "leaving").read_inbox())
        code, _, err = self.run_cli("msg", "leaving", "late after retirement")
        self.assertEqual(code, 2)
        self.assertIn("successor", err)
        self.assertEqual(len(Store(self.state, "leaving").read_inbox()), before)
        Store(self.state, "leaving")._touch_thread()
        self.as_holder("leaving")
        self.assertEqual(self.run_cli("enroll")[0], 75)

    def test_handled_receipt_does_not_acknowledge_later_arrival(self):
        self.send("handled first")
        code, out, _ = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 0)
        receipt = self.home / "receipt.json"
        receipt.write_text(out)
        self.send("later pending")
        self.assertEqual(self.run_cli("checkpoint-ack", str(receipt), "--pending", str(self.loops))[0], 0)
        self.assertIn("later pending", self.run_cli("inbox")[1])
        saved = json.loads((pathlib.Path(self.state) / "handled/leaving.json").read_text())
        self.assertEqual(len(saved["message_hashes"]), 1)
        self.assertIn("pending scope ruling", saved["pending"]["owner_questions"])

    def test_delivered_but_unhandled_originals_survive_note_and_relay(self):
        for number in range(3):
            self.send("unhandled-%d" % number)
        self.assertIn("unhandled-2", self.run_cli("inbox")[1])
        note = self.note()
        self.assertEqual(len(json.loads(note.read_text())["unread"]), 3)
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "relay", "--replay", str(note), "--to", "replacement")[0], 0)
        originals = Store(self.state, "replacement").read_inbox()
        self.assertEqual(sum("unhandled-" in m["text"] for m in originals), 3)

    def test_receipt_rejects_removed_messages_and_invalid_clock(self):
        self.send("exact original")
        receipt = json.loads(self.run_cli("inbox", "--peek", "--receipt")[1])
        path = self.home / "receipt.json"
        for mutation in ("messages", "clock"):
            changed = json.loads(json.dumps(receipt))
            if mutation == "messages":
                changed["messages"] = []
            else:
                changed["checkpoint"]["last_batch_at"] = {}
            path.write_text(json.dumps(changed))
            self.assertEqual(self.run_cli("checkpoint-ack", str(path), "--pending", str(self.loops))[0], 2)
            self.assertFalse((pathlib.Path(self.state) / "handled/leaving.json").exists())
        path.write_text(json.dumps(receipt))
        self.assertEqual(self.run_cli("checkpoint-ack", str(path), "--pending", str(self.loops))[0], 0)

    def test_malformed_checkpoint_packet_and_retired_route_fail_safely(self):
        self.send("preserved original")
        self.run_cli("inbox")
        checkpoint = pathlib.Path(Inbox(Store(self.state, "leaving")).checkpoint)
        data = json.loads(checkpoint.read_text())
        data["last_batch_at"] = []
        checkpoint.write_text(json.dumps(data))
        before = checkpoint.read_bytes()
        self.assertEqual(self.run_cli("inbox", "--digest-mode", "night")[0], 2)
        self.assertEqual(checkpoint.read_bytes(), before)
        note = self.notes / "malformed.json"
        self.notes.mkdir(mode=0o700)
        note.write_text(json.dumps({"version": 1, "store": self.state}))
        self.assertEqual(self.run_cli("handover", "relay", str(note))[0], 2)
        route = pathlib.Path(Store(self.state, "retired")._thread_path())
        route.parent.mkdir(exist_ok=True)
        route.write_text(json.dumps({"retired": True, "successor": {}}))
        self.assertEqual(self.run_cli("msg", "retired", "preserved")[0], 2)
        self.assertFalse(pathlib.Path(Store(self.state, "retired")._inbox_path("retired")).exists())

    def test_oversize_metadata_packet_refuses_before_pausing_or_releasing(self):
        self.enroll("holding")
        Store(self.state, "holding").register(intent="X" * 600000)
        self.enroll("leaving")
        Store(self.state, "leaving").register(intent="X" * 600000)
        owned = Store(self.state, "leaving").acquire("repo:main", pid=987654)
        code, _, err = self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                  "--open-loops", str(self.loops), "--id", "oversize")
        self.assertEqual(code, 2)
        self.assertIn("packet exceeds 1 MiB", err)
        self.assertFalse((self.notes / "oversize.json").exists())
        self.assertFalse(json.loads(pathlib.Path(Store(self.state, "leaving")._thread_path()).read_text()).get("handover_pending"))
        self.assertEqual(Store(self.state, "leaving").status()["resources"]["repo:main"]["token"], owned.token)

    def test_retirement_refuses_incomplete_late_record_until_reconciled(self):
        note = self.note()
        self.enroll("successor")
        self.as_holder("holding")
        self.run_cli("handover", "relay", str(note), "--to", "successor")
        self.as_holder("successor")
        self.run_cli("handover", "accept", str(note), "--confirm-ready")
        mailbox = pathlib.Path(Store(self.state, "leaving")._inbox_path("leaving"))
        mailbox.parent.mkdir(exist_ok=True)
        mailbox.write_text(json.dumps({"from": "sender", "ts": 10, "text": "unknown trailing original"}))
        self.as_holder("holding")
        code, _, err = self.run_cli("handover", "retire", str(note), "--routes-confirmed")
        self.assertEqual(code, 2)
        self.assertIn("incomplete late", err)
        self.assertEqual(json.loads(note.read_text())["phase"], "accepted")
        with mailbox.open("a") as stream:
            stream.write("\n")
        self.assertEqual(self.run_cli("handover", "relay", str(note), "--to", "successor")[0], 0)
        self.assertEqual(self.run_cli("handover", "retire", str(note), "--routes-confirmed")[0], 0)

    def test_private_new_and_existing_mail_files_under_open_umask(self):
        previous = os.umask(0o022)
        try:
            self.enroll("leaving")
            self.send("private words")
            store = Store(self.state, "leaving")
            mailbox = pathlib.Path(store._inbox_path("leaving"))
            mailbox.chmod(0o644)
            self.send("new private words")
            for path in (pathlib.Path(self.state), pathlib.Path(self.state) / "threads", mailbox.parent):
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            for path in (pathlib.Path(store._thread_path()), mailbox):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(previous)

    def test_cancel_watch_stops_owned_extra_descendant(self):
        launcher = self.home / "bin/coms"
        launcher.parent.mkdir()
        launcher.write_text('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        launcher.chmod(0o700)
        watcher = self.home / "extra.py"
        tick = self.home / "tick"
        watcher.write_text("import os,time,pathlib,signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\np=pathlib.Path(os.environ['TEST_TICK'])\nwhile True:\n p.write_text(str(time.monotonic()))\n time.sleep(.02)\n")
        env = dict(self.env, PATH=str(launcher.parent) + os.pathsep + self.env["PATH"],
                   TEST_TICK=str(tick), WATCH_EXTRA=str(watcher), WATCH_MAX_S="5",
                   COMS_WATCH_DIR=str(self.home / "watch"), INBOX_POLL_S="0.1")
        process = subprocess.Popen([str(ROOT / "skills/coms/bin/watch-once.sh")], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not tick.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(tick.exists())
            process.terminate()
            process.communicate(timeout=4)
            stopped = tick.read_text()
            time.sleep(.1)
            self.assertEqual(tick.read_text(), stopped)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=3)

    def test_handovers_release_only_idle_owned_tokens_and_allow_new_acquisitions(self):
        self.enroll("holding")
        self.enroll("leaving")
        own = Store(self.state, "leaving").acquire("repo:main", pid=987654)
        other = Store(self.state, "other").acquire("repo:ship", pid=987653)
        with patch("coms.handover.os.kill", side_effect=ProcessLookupError):
            note = self.note()
        state = Store(self.state, "observer").status()
        self.assertNotIn("repo:main", state["resources"])
        self.assertEqual(state["resources"]["repo:ship"]["token"], other.token)
        self.assertEqual(state["fence"], other.token)
        self.as_holder("leaving")
        code, _, err = self.run_cli("do", "repo:main", "--wait", "0", "--", "true")
        self.assertEqual(code, 0, err)
        self.assertNotIn("acquisitions are paused", err)
        self.assertEqual(json.loads(note.read_text())["leases"]["repo:main"]["token"], own.token)

    def test_concurrent_readers_deliver_each_original_once(self):
        for n in range(15):
            self.send("original-%d" % n)
        args = [sys.executable, "-m", "coms.cli", "inbox", "--consume", "--json"]
        workers = [subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        texts = []
        for worker in workers:
            out, err = worker.communicate(timeout=3)
            self.assertEqual(worker.returncode, 0, err)
            texts.extend(m["text"] for m in json.loads(out))
        self.assertEqual(len(texts), 15)
        self.assertEqual(len(set(texts)), 15)

    def test_lease_loss_stops_only_the_owned_child(self):
        ready = self.home / "child-ready"
        after = self.home / "child-finished"
        child = "import pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(2);pathlib.Path(sys.argv[2]).write_text('finished')"
        def revoke():
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            Store(self.state, "observer").force_release("repo:main")
        thread = threading.Thread(target=revoke)
        thread.start()
        store = Store(self.state, "leaving", ttl=.3)
        with patch.object(cli, "_store", return_value=store):
            code, _, err = self.run_cli("do", "repo:main", "--wait", "0", "--",
                                        sys.executable, "-c", child, str(ready), str(after))
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(code, 75)
        self.assertIn("lease lost", err)
        self.assertFalse(after.exists())
