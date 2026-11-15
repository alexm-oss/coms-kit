import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from coms import cli
from coms.store import Store

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-cli-")
        self._env = dict(os.environ)
        os.environ["COMS_DIR"] = self.dir
        os.environ["COMS_HOLDER"] = "sess-test"
        os.environ["COMS_SESSION_ID"] = "fixture-session"
        Store(self.dir, "sess-test").register()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def run_cli(self, *argv):
        return cli.main(list(argv))

    def run_out(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, buf.getvalue()

    def test_do_returns_the_commands_exit_code(self):
        self.assertEqual(self.run_cli("do", "sample:main", "--", "true"), 0)
        self.assertEqual(self.run_cli("do", "sample:main", "--", "false"), 1)

    def test_do_releases_the_lease_after_the_command(self):
        self.run_cli("do", "sample:main", "--", "true")
        st = Store(self.dir, "observer").status()
        self.assertNotIn("sample:main", st["resources"])

    def test_do_waits_then_fails_fast_when_held_by_other(self):
        Store(self.dir, "someone-else").acquire("sample:main")  # lease held, live
        code = self.run_cli("do", "sample:main", "--wait", "0", "--", "true")
        self.assertNotEqual(code, 0)  # could not get the lease, did not run

    def test_check_passes_free_fails_when_held_by_other(self):
        self.assertEqual(self.run_cli("check", "sample:main"), 0)
        Store(self.dir, "someone-else").acquire("sample:main")
        self.assertNotEqual(self.run_cli("check", "sample:main"), 0)

    def test_unlock_force_removes_a_lease(self):
        Store(self.dir, "someone-else").acquire("sample:main")
        self.assertEqual(self.run_cli("unlock", "sample:main"), 0)
        st = Store(self.dir, "observer").status()
        self.assertNotIn("sample:main", st["resources"])

    def test_status_json_reports_holder(self):
        Store(self.dir, "sess-a").acquire("sample:main", intent="merge HLS")
        code, out = self.run_out("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["resources"]["sample:main"]["holder"], "sess-a")
        self.assertEqual(data["resources"]["sample:main"]["intent"], "merge HLS")

    # -- reverse channel: msg / inbox / who --

    def test_msg_delivers_to_the_target_inbox(self):
        self.run_cli("msg", "orch-1", "landed", "the", "fix")
        got = Store(self.dir, "orch-1").read_inbox()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["text"], "landed the fix")
        self.assertEqual(got[0]["from"], "sess-test")

    def test_inbox_reads_messages_sent_to_us(self):
        Store(self.dir, "peer-a").send_message("sess-test", "blocked on the merge")
        code, out = self.run_out("inbox")
        self.assertEqual(code, 0)
        self.assertIn("blocked on the merge", out)
        self.assertIn("peer-a", out)

    def run_err(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_msg_without_a_holder_warns_and_delivers(self):
        del os.environ["COMS_HOLDER"]
        code, out, err = self.run_err("msg", "orch-1", "landed")
        self.assertEqual(code, 0)
        self.assertIn("COMS_HOLDER", err)
        self.assertIn("delivered", out)
        self.assertEqual(Store(self.dir, "orch-1").read_inbox()[-1]["text"], "landed")

    def test_inbox_without_a_holder_refuses_instead_of_reading_an_empty_inbox(self):
        Store(self.dir, "peer-a").send_message("sess-test", "a question for you")
        del os.environ["COMS_HOLDER"]
        for argv in (("inbox",), ("inbox", "--json"), ("inbox", "--follow")):
            code, out, err = self.run_err(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("COMS_HOLDER", err)
            self.assertNotIn("empty", out)

    def test_a_blank_holder_counts_as_no_holder(self):
        os.environ["COMS_HOLDER"] = "  "
        code, _, err = self.run_err("inbox")
        self.assertEqual(code, 2)
        self.assertIn("COMS_HOLDER", err)

    def test_inbox_is_isolated_per_recipient(self):
        Store(self.dir, "peer-a").send_message("someone-else", "not for us")
        _, out = self.run_out("inbox")
        self.assertIn("empty", out)

    def test_inbox_since_is_history_without_delivery_advance(self):
        for stamp, text in ((10.25, "old"), (10.5, "equal"), (10.75, "new")):
            Store(self.dir, "peer-a", clock=lambda stamp=stamp: stamp).send_message("sess-test", text)
        code, out = self.run_out("inbox", "--since", "10.5", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([row["text"] for row in json.loads(out)], ["new"])
        code, out = self.run_out("inbox", "--follow", "--since", "10.5")
        self.assertEqual(code, 2)
        self.assertIn("old", self.run_out("inbox")[1])

    def test_inbox_follow_without_since_still_prints_history(self):
        Store(self.dir, "peer-a", clock=lambda: 10.25).send_message("sess-test", "old")
        with mock.patch.object(cli.time, "sleep", side_effect=KeyboardInterrupt):
            code, out = self.run_out("inbox", "--follow")
        self.assertEqual(code, 0)
        self.assertIn("old", out)

    def closed_pipe_cli(self, argv):
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        env = dict(os.environ, PYTHONPATH=KIT_ROOT)
        env.pop("PYTHONUNBUFFERED", None)
        try:
            return subprocess.run([sys.executable, "-B", *argv], stdout=write_fd,
                                  stderr=subprocess.PIPE, timeout=5, env=env)
        finally:
            os.close(write_fd)

    def test_inbox_follow_exits_zero_without_stderr_when_initial_pipe_is_closed(self):
        Store(self.dir, "peer-a").send_message("sess-test", "new")
        for flags in ((), ("--json",)):
            with self.subTest(flags=flags):
                result = self.closed_pipe_cli(["-m", "coms.cli", "inbox", "--all", "--follow", *flags])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")

    def test_inbox_follow_exits_zero_without_stderr_when_later_pipe_is_closed(self):
        script = (
            "import os, sys\nfrom coms import cli\n"
            "def tick(_):\n"
            "    cli.Store(os.environ['COMS_DIR'], 'peer-a').send_message('sess-test', 'new')\n"
            "cli.time.sleep = tick\n"
            "sys.exit(cli.main(['inbox', '--follow']))\n"
        )
        result = self.closed_pipe_cli(["-c", script])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")

    def test_who_reports_the_orchestrator(self):
        Store(self.dir, "orch-1").register(role="orchestrator")
        code, out = self.run_out("who")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "orch-1")

    def test_who_fails_when_no_orchestrator(self):
        self.assertEqual(self.run_cli("who"), 1)

    def test_role_is_sticky_across_reenroll(self):
        store = Store(self.dir, "sess-test")
        store.register(intent="coordinating", role="orchestrator")
        store.register(intent="a plain status update")  # role omitted -> preserved
        self.assertEqual(store.orchestrator(), "sess-test")

    def test_bare_key_is_prefixed_with_repo(self):
        orig = cli._current_repo
        cli._current_repo = lambda: "sample"
        try:
            self.assertEqual(cli._resolve_key("main"), "sample:main")
            self.assertEqual(cli._resolve_key("sample:ship"), "sample:ship")  # explicit stays
        finally:
            cli._current_repo = orig

    def test_unlock_prefixes_a_bare_name_with_the_repo(self):
        Store(self.dir, "someone-else").acquire("demo:main")
        orig = cli._current_repo
        cli._current_repo = lambda: "demo"
        try:
            code, out = self.run_out("unlock", "main")
        finally:
            cli._current_repo = orig
        self.assertNotIn("demo:main", Store(self.dir, "observer").status()["resources"])
        self.assertEqual(code, 0)
        self.assertIn("coms: dropped the lease on demo:main", out)

    def test_unlock_of_a_free_key_says_so_and_exits_1(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.run_out("unlock", "demo:free")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("coms: no lease on demo:free; nothing dropped", err.getvalue())

    def test_plain_status_lists_every_enrolled_agent(self):
        orig = cli._current_repo
        orig_wt = cli._current_worktree
        cli._current_repo = lambda: "demo"
        cli._current_worktree = lambda: "demo-login"
        try:
            os.environ["COMS_HOLDER"] = "agent-a"
            self.run_out("enroll", "--intent", "merging the fix")
            os.environ["COMS_HOLDER"] = "agent-b"
            _, enroll_out = self.run_out("enroll")
        finally:
            cli._current_repo = orig
            cli._current_worktree = orig_wt
        line_a = "  agent-a on demo in demo-login (merging the fix)"
        line_b = "  agent-b on demo in demo-login (idle)"
        os.environ["COMS_HOLDER"] = "observer"
        code, out = self.run_out("status")
        self.assertEqual(code, 0)
        self.assertIn("Enrolled:", out)
        self.assertIn(line_a, out)
        self.assertIn(line_b, out)
        self.assertLess(out.index("coms: no resources held"), out.index("Enrolled:"))
        self.assertIn(line_a, enroll_out)
        Store(self.dir, "agent-a").acquire("demo:main", intent="merging the fix")
        _, out = self.run_out("status")
        self.assertLess(out.index("demo:main: held by agent-a"), out.index("Enrolled:"))
        self.assertIn(line_b, out)

    def test_status_enroll_and_who_skip_thread_files_they_cannot_show(self):
        # Another tool, or a hand edit, can leave any shape of file in threads/.
        threads = os.path.join(self.dir, "threads")
        os.makedirs(threads, exist_ok=True)
        now = time.time()
        files = {
            "good-orch.json": {"holder": "good-orch", "repo": "demo", "intent": "coordinating",
                               "role": "orchestrator", "updated_at": now - 5, "pid": 1},
            "list.json": [],
            "no-holder.json": {"repo": "demo", "intent": "no holder", "role": "orchestrator",
                               "updated_at": now, "pid": 2},
            "null-holder.json": {"holder": None, "repo": "demo", "intent": "null holder",
                                 "role": "orchestrator", "updated_at": now, "pid": 3},
            "string-time.json": {"holder": "string-time", "repo": "demo", "intent": "string time",
                                 "role": "orchestrator", "updated_at": "yesterday", "pid": 4},
        }
        for name, record in files.items():
            with open(os.path.join(threads, name), "w") as fh:
                json.dump(record, fh)
        env = dict(os.environ, PYTHONPATH=KIT_ROOT, COMS_HOLDER="newcomer")

        def real_cli(*argv):
            return subprocess.run([sys.executable, "-B", "-m", "coms.cli", *argv],
                                  env=env, capture_output=True, text=True)

        status = real_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("good-orch", status.stdout)
        enroll = real_cli("enroll", "--intent", "x")
        self.assertEqual(enroll.returncode, 0, enroll.stderr)
        self.assertIn("good-orch", enroll.stdout)
        who = real_cli("who")
        self.assertEqual(who.returncode, 0, who.stderr)
        self.assertEqual(who.stdout.strip(), "good-orch")

    def test_status_shows_a_six_field_thread_from_another_writer(self):
        threads = os.path.join(self.dir, "threads")
        os.makedirs(threads, exist_ok=True)
        with open(os.path.join(threads, "forge-1.json"), "w") as fh:
            json.dump({"holder": "forge-1", "repo": "forge", "intent": "bus", "role": "",
                       "updated_at": time.time(), "pid": 1}, fh)
        code, out = self.run_out("status")
        self.assertEqual(code, 0)
        self.assertIn("  forge-1 on forge (bus)", out)
