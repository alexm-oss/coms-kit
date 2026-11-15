import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from coms.cli import PRIMER

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(KIT_ROOT, "skills", "coms")
BIN = os.path.join(SKILL, "bin")
REFS = os.path.join(SKILL, "references")
SCRIPTS = ["inbox-digest.sh", "read-batch.sh", "watch-all.sh", "watch-once.sh", "handover-note",
           "watch-run.py", "coms-peer"]


def read(path):
    with open(path) as fh:
        return fh.read()


class Sandbox(unittest.TestCase):
    """A scratch HOME and watch folder; no real coms, Codex or notifier state is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="coms-skill-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.watch = os.path.join(self.tmp, "watch")
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(("COMS_", "INBOX_", "CODEX_", "WATCH_", "PACE_", "READ_BATCH")):
                del self.env[key]
        self.env.update({"HOME": self.tmp, "COMS_WATCH_DIR": self.watch,
                         "COMS_DIR": os.path.join(self.tmp, "state"), "COMS_HOLDER": "test-reader",
                         "COMS_SESSION_ID": "test-session"})
        self.env.pop("CLAUDE_SESSION_ID", None)
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)
        launcher_dir = os.path.join(self.tmp, "bin")
        os.makedirs(launcher_dir)
        launcher = os.path.join(launcher_dir, "coms")
        with open(launcher, "w") as target:
            target.write('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        os.chmod(launcher, 0o755)
        self.env["PATH"] = launcher_dir + os.pathsep + self.env["PATH"]
        self.env["PYTHONPATH"] = KIT_ROOT

    def run_script(self, name, *args, env=None, timeout=60):
        merged = dict(self.env)
        merged.update(env or {})
        subprocess.run(["python3", "-m", "coms.cli", "enroll"], env=merged,
                       check=True, capture_output=True, text=True, timeout=3)
        proc = subprocess.run([os.path.join(BIN, name)] + list(args), env=merged,
                              capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr

    def inbox(self, *msgs, ts_offset=-5):
        path = os.path.join(self.tmp, "state", "inbox", "test-reader.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            for sender, text in msgs:
                fh.write(json.dumps({"ts": time.time() + ts_offset, "from": sender, "text": text}) + "\n")
        return path


class SkillTextTest(unittest.TestCase):
    def test_skill_links_the_optional_operating_model(self):
        skill = read(os.path.join(SKILL, "SKILL.md"))
        self.assertIn("## Operating model", skill)
        for name in ("operating-model.md", "handover.md", "claude-desktop.md"):
            self.assertIn("references/" + name, skill)
            self.assertTrue(os.path.isfile(os.path.join(REFS, name)), name)
        self.assertEqual(sorted(os.listdir(REFS)), ["claude-desktop.md", "handover.md", "operating-model.md"])
        self.assertIn("bin/README.md", skill)
        self.assertTrue(os.path.isfile(os.path.join(BIN, "README.md")))

    def test_operating_model_states_safe_defaults(self):
        model = " ".join(read(os.path.join(REFS, "operating-model.md")).split())
        self.assertLessEqual(len(read(os.path.join(REFS, "operating-model.md")).splitlines()), 75)
        for phrase in ("Nothing is pushed, merged to a shared branch, deployed, deleted, paid for or sent to a person "
                       "without the human's own yes",
                       "Agents never remove a check",
                       "A relayed decision counts only if `coms decisions` shows it",
                       "Anything risky is still confirmed with the human first",
                       "Five lines or fewer", "proof of done", '"Delivered" is not "read"',
                       "--quote '"):
            self.assertIn(phrase, model)
        for banned in ("fence", "Rule ", "never idle", "never end a turn idle"):
            self.assertNotIn(banned, model)

    def test_primer_covers_identity_messages_and_decisions(self):
        for phrase in ("COMS_HOLDER", "COMS_SESSION_ID", "coms inbox --consume", "--peek", "coms decisions",
                       "coms decide", "merge-base --is-ancestor", "SKILL.md"):
            self.assertIn(phrase, PRIMER)
        self.assertNotIn('--quote "', PRIMER)

    def test_the_package_holds_no_personal_data_and_no_secrets(self):
        bad = {
            "a home path": r"/Users/",
            "a working-folder name": r"scratchpad",
            "a holder id": r"claude-[0-9a-f]{8}\b",
            "an email address": r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.(com|ca|org|net|io)\b",
            "a phone number": r"\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b",
            "a long token": r"[A-Za-z0-9+=]{40,}",
            "a bearer token": r"(?i)bearer\s+[A-Za-z0-9._-]{16,}",
            "a quoted credential": r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*['\"][^'\"\s]{8,}['\"]",
            "credentials in a url": r"://[^/\s:@]+:[^@\s]+@",
        }
        private = os.environ.get("COMS_PRIVATE_TERMS")
        if private:  # names you do not want shipped (the owner's name, a project name) stay out of the repo itself
            bad["one of your private terms"] = private
        found = []
        for root, dirs, files in os.walk(SKILL):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                path = os.path.join(root, name)
                text = read(path)
                for what, pattern in bad.items():
                    if re.search(pattern, text):
                        found.append("%s in %s" % (what, os.path.relpath(path, SKILL)))
        self.assertEqual(found, [])



class HelpTest(Sandbox):
    def test_every_script_is_executable_and_answers_help(self):
        for name in SCRIPTS:
            path = os.path.join(BIN, name)
            self.assertTrue(os.access(path, os.X_OK), name + " is not executable")
            code, out, err = self.run_script(name, "--help")
            self.assertEqual(code, 0, name + ": " + err)
            self.assertIn("usage", out.lower(), name)


class InboxScriptsTest(Sandbox):
    def assert_delivered_but_unhandled(self, path, env):
        checkpoint = os.path.join(env["COMS_DIR"], "checkpoints", "test-reader.json")
        self.assertEqual(json.loads(read(checkpoint))["offset"], os.path.getsize(path))
        self.assertFalse(os.path.exists(os.path.join(env["COMS_DIR"], "handled", "test-reader.json")))
        originals = [json.loads(line) for line in read(path).splitlines()]
        proc = subprocess.run(["python3", "-m", "coms.cli", "inbox", "--peek", "--receipt"],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=3)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["messages"], originals)

    def test_normal_mode_passes_a_worker_line_and_night_mode_waits_for_an_escalation(self):
        path = self.inbox(("claude-aaaa1111", "status update: lane green"))
        code, out, err = self.run_script("inbox-digest.sh", "--once", "--from-start",
                                         env={"COMS_DIR": os.path.dirname(os.path.dirname(path))})
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"^\[\d\d:\d\d:\d\d\] claude-aaaa1111: status update: lane green")
        fyi = "TYPE: FYI\nTOPIC: lane status\nNEED: none\nPROOF: tests/lane.txt\nstatus update: lane green"
        with open(path, "w") as target:
            target.write(json.dumps({"ts": time.time(), "from": "claude-aaaa1111", "text": fyi}) + "\n")
        night = {"INBOX_MODE": "night", "COMS_DIR": os.path.join(self.tmp, "night")}
        os.makedirs(os.path.join(night["COMS_DIR"], "inbox"), exist_ok=True)
        shutil.copyfile(path, os.path.join(night["COMS_DIR"], "inbox", "test-reader.jsonl"))
        code, out, err = self.run_script("inbox-digest.sh", "--once", "--from-start", env=night)
        self.assertEqual((code, out), (0, ""))
        self.inbox(("codex-lead-fixture", "TYPE: ASK\nTOPIC: fixture ruling\nNEED: rule on fixture\nPROOF: tests/ruling.txt\nESCALATION: need a ruling on the fixture"))
        os.makedirs(os.path.join(night["COMS_DIR"], "inbox"), exist_ok=True)
        shutil.copyfile(path, os.path.join(night["COMS_DIR"], "inbox", "test-reader.jsonl"))
        code, out, err = self.run_script("inbox-digest.sh", "--once", "--from-start", env=night)
        self.assertEqual(code, 0, err)
        self.assertIn("ESCALATION: need a ruling", out)
        self.assertIn(fyi, out)
        self.assert_delivered_but_unhandled(os.path.join(night["COMS_DIR"], "inbox", "test-reader.jsonl"), night)
        code, out, err = self.run_script("inbox-digest.sh", "--once", env=night)
        self.assertEqual((code, out), (0, ""), err)

    def test_read_batch_prints_what_is_new_and_the_digest_does_not_repeat_it(self):
        path = self.inbox(("claude-aaaa1111", "first report"))
        env = {"COMS_DIR": os.path.dirname(os.path.dirname(path))}
        code, out, err = self.run_script("read-batch.sh", "300", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("first report", out)
        self.assertIn("unread: 1", out)
        code, out, err = self.run_script("read-batch.sh", env=env)
        self.assertIn("unread: 0", out)
        code, out, err = self.run_script("inbox-digest.sh", "--once", "--from-start", env=env)
        self.assertEqual(out, "")
        self.inbox(("claude-aaaa1111", "second report"), ts_offset=2)
        code, out, err = self.run_script("inbox-digest.sh", "--once", env=env)
        self.assertIn("second report", out)
        self.assertNotIn("first report", out)

    def test_watch_once_prints_the_first_event_and_exits(self):
        path = self.inbox(("codex-lead-fixture", "ESCALATION: the gate needs a ruling"))
        env = {"COMS_DIR": os.path.dirname(os.path.dirname(path)), "INBOX_FROM_START": "1", "INBOX_POLL_S": "1", "WATCH_POLL_S": "1",
               "WATCH_SETTLE_S": "1", "WATCH_MAX_S": "30"}
        started = time.time()
        code, out, err = self.run_script("watch-once.sh", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("ESCALATION: the gate needs a ruling", out)
        self.assertLess(time.time() - started, 20)

    def test_watch_once_says_quiet_when_nothing_urgent_happens(self):
        fyi = "TYPE: FYI\nTOPIC: routine update\nNEED: none\nPROOF: tests/routine.txt\nroutine update"
        path = self.inbox(("claude-aaaa1111", fyi))
        env = {"COMS_DIR": os.path.dirname(os.path.dirname(path)), "INBOX_FROM_START": "1", "INBOX_POLL_S": "1", "WATCH_POLL_S": "1",
               "WATCH_SETTLE_S": "1", "WATCH_MAX_S": "3"}
        code, out, err = self.run_script("watch-once.sh", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("quiet: no event in 3.0 seconds", out)
        self.assertNotIn("routine update", out)
        checkpoint = os.path.join(self.tmp, "state", "checkpoints", "test-reader.json")
        if os.path.exists(checkpoint):
            self.assertEqual(json.loads(read(checkpoint))["offset"], 0)
        code, out, err = self.run_script("read-batch.sh", "1000", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn(fyi, out)
        self.assert_delivered_but_unhandled(path, env)

    def test_night_wrappers_wake_for_legacy_and_malformed_cards(self):
        for index, text in enumerate(("routine legacy update", "TYPE: FYI\ninvalid old card")):
            with self.subTest(text=text):
                env = {"COMS_DIR": os.path.join(self.tmp, "night-" + str(index)),
                       "INBOX_MODE": "night", "INBOX_POLL_S": "1",
                       "WATCH_SETTLE_S": "1", "WATCH_MAX_S": "10"}
                os.makedirs(os.path.join(env["COMS_DIR"], "inbox"))
                destination = os.path.join(env["COMS_DIR"], "inbox", "test-reader.jsonl")
                with open(destination, "w") as target:
                    target.write(json.dumps({"ts": time.time(), "from": "legacy-writer", "text": text}) + "\n")
                code, out, err = self.run_script("watch-once.sh", env=env)
                self.assertEqual(code, 0, err)
                self.assertIn(text, out)
                self.assertNotIn("quiet: no event", out)
                self.assert_delivered_but_unhandled(destination, env)
                code, out, err = self.run_script("inbox-digest.sh", "--once", env=env)
                self.assertEqual((code, out), (0, ""), err)
                self.assertFalse(os.path.exists(os.path.join(env["COMS_DIR"], "handled", "test-reader.json")))
