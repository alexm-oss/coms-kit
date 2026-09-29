import contextlib
import io
import json
import os
import tempfile
import unittest

from coms import cli
from coms.store import Store


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-cli-")
        self._env = dict(os.environ)
        os.environ["COMS_DIR"] = self.dir
        os.environ["COMS_HOLDER"] = "sess-test"

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
        self.assertEqual(self.run_cli("do", "demo:main", "--", "true"), 0)
        self.assertEqual(self.run_cli("do", "demo:main", "--", "false"), 1)

    def test_do_releases_the_lease_after_the_command(self):
        self.run_cli("do", "demo:main", "--", "true")
        st = Store(self.dir, "observer").status()
        self.assertNotIn("demo:main", st["resources"])

    def test_do_waits_then_fails_fast_when_held_by_other(self):
        Store(self.dir, "someone-else").acquire("demo:main")  # lease held, live
        code = self.run_cli("do", "demo:main", "--wait", "0", "--", "true")
        self.assertNotEqual(code, 0)  # could not get the lease, did not run

    def test_check_passes_free_fails_when_held_by_other(self):
        self.assertEqual(self.run_cli("check", "demo:main"), 0)
        Store(self.dir, "someone-else").acquire("demo:main")
        self.assertNotEqual(self.run_cli("check", "demo:main"), 0)

    def test_unlock_force_removes_a_lease(self):
        Store(self.dir, "someone-else").acquire("demo:main")
        self.assertEqual(self.run_cli("unlock", "demo:main"), 0)
        st = Store(self.dir, "observer").status()
        self.assertNotIn("demo:main", st["resources"])

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

    def test_status_json_reports_holder(self):
        Store(self.dir, "sess-a").acquire("demo:main", intent="merge feature")
        code, out = self.run_out("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["resources"]["demo:main"]["holder"], "sess-a")
        self.assertEqual(data["resources"]["demo:main"]["intent"], "merge feature")

    def test_plain_status_lists_every_enrolled_agent(self):
        orig_repo, orig_wt = cli._current_repo, cli._current_worktree
        cli._current_repo = lambda: "demo"
        cli._current_worktree = lambda: "demo-login"
        try:
            os.environ["COMS_HOLDER"] = "agent-a"
            self.run_out("enroll", "--intent", "merging the fix")
            os.environ["COMS_HOLDER"] = "agent-b"
            _, enroll_out = self.run_out("enroll")
        finally:
            cli._current_repo, cli._current_worktree = orig_repo, orig_wt
        line_a = "  agent-a on demo in demo-login (merging the fix)"
        line_b = "  agent-b on demo in demo-login (idle)"
        os.environ["COMS_HOLDER"] = "observer"
        code, out = self.run_out("status")
        self.assertEqual(code, 0)
        self.assertIn("Enrolled:", out)
        self.assertIn(line_a, out)
        self.assertIn(line_b, out)
        self.assertLess(out.index("coms: no resources held"), out.index("Enrolled:"))
        self.assertIn(line_a, enroll_out)  # enroll prints the same line for a peer
        Store(self.dir, "agent-a").acquire("demo:main", intent="merging the fix")
        _, out = self.run_out("status")
        self.assertLess(out.index("demo:main: held by agent-a"), out.index("Enrolled:"))
        self.assertIn(line_b, out)

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

    def test_inbox_is_isolated_per_recipient(self):
        Store(self.dir, "peer-a").send_message("someone-else", "not for us")
        _, out = self.run_out("inbox")
        self.assertIn("empty", out)

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
        cli._current_repo = lambda: "demo"
        try:
            self.assertEqual(cli._resolve_key("main"), "demo:main")
            self.assertEqual(cli._resolve_key("demo:ship"), "demo:ship")  # explicit stays
        finally:
            cli._current_repo = orig
