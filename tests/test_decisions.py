import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from coms.decisions import FIELDS, DecisionLog, format_line, select
from coms.mutex import FileMutex
from coms import cli

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Clock:
    """Each call is one second later, so records get distinct times."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


class DecisionLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-dec-")
        self.log = DecisionLog(self.dir, "agent-a", clock=Clock())

    def lines(self):
        with open(self.log.path) as fh:
            return fh.read().splitlines()

    def test_record_appends_one_line_with_the_six_fields(self):
        self.log.record("release-date", "ship friday", "friday works", repo="myapp")
        [line] = self.lines()
        entry = json.loads(line)
        self.assertEqual(sorted(entry), sorted(FIELDS))
        self.assertEqual(
            (entry["by"], entry["repo"], entry["topic"], entry["answer"], entry["quote"]),
            ("agent-a", "myapp", "release-date", "ship friday", "friday works"),
        )

    def test_record_refuses_a_blank_field_and_writes_nothing(self):
        for topic, answer, quote in (("", "a", "q"), ("t", "  ", "q"), ("t", "a", "")):
            with self.assertRaises(ValueError):
                self.log.record(topic, answer, quote)
        self.assertFalse(os.path.exists(self.log.path))

    def test_record_creates_the_coms_folder(self):
        log = DecisionLog(os.path.join(self.dir, "new", "coms"), "agent-a")
        log.record("t", "a", "q")
        self.assertEqual(len(log.entries()), 1)

    def test_newest_answer_per_topic_wins_and_lists_newest_first(self):
        self.log.record("release-date", "ship friday", "friday")
        self.log.record("db", "postgres", "use postgres")
        self.log.record("release-date", "ship monday", "monday instead")
        rows = select(self.log.entries())
        self.assertEqual(
            [(e["topic"], e["answer"], current) for e, current in rows],
            [("release-date", "ship monday", True), ("db", "postgres", True)],
        )

    def test_topics_match_ignoring_case_and_spaces(self):
        self.log.record("Release-Date", "ship friday", "friday")
        self.log.record("  release-date ", "ship monday", "monday")
        rows = select(self.log.entries())
        self.assertEqual([(e["answer"], current) for e, current in rows], [("ship monday", True)])

    def test_text_finds_old_wording_but_shows_the_current_answer(self):
        self.log.record("screen-access-prompt", "keep it", "keep the monthly prompt")
        self.log.record("screen-access-prompt", "remove it", "get rid of it")
        entries = self.log.entries()
        self.assertEqual(
            [(e["answer"], current) for e, current in select(entries, "monthly")],
            [("remove it", True)],
        )
        self.assertEqual(
            [(e["answer"], current) for e, current in select(entries, "monthly", include_replaced=True)],
            [("remove it", True), ("keep it", False)],
        )
        self.assertEqual(len(select(entries, "remove")), 1)

    def test_every_word_of_text_must_appear(self):
        self.log.record("screen-access-prompt", "remove it", "get rid of it")
        self.assertEqual(len(select(self.log.entries(), "SCREEN prompt")), 1)
        self.assertEqual(select(self.log.entries(), "screen banana"), [])

    def test_line_breaks_quotes_and_non_ascii_round_trip(self):
        answer = "line one\nline two"
        quote = 'he said "yes" - na\u00efve \u2713'
        self.log.record("t", answer, quote)
        [entry] = self.log.entries()
        self.assertEqual((entry["answer"], entry["quote"]), (answer, quote))
        with open(self.log.path, "rb") as fh:
            self.assertTrue(all(b < 128 for b in fh.read()))
        self.assertNotIn("\n", format_line(entry))

    def test_undecodable_text_is_stored_printably(self):
        self.log.record("t", "a", "caf" + chr(0xDCE9) + " is fine")
        [entry] = self.log.entries()
        format_line(entry).encode("utf-8")
        self.assertTrue(entry["quote"].startswith("caf"))
        self.assertTrue(entry["quote"].endswith(" is fine"))

    def test_unreadable_lines_are_skipped(self):
        self.log.record("good", "yes", "yes please")
        with open(self.log.path, "a") as fh:
            fh.write("not json\n[]\n")
            fh.write(json.dumps({"topic": 1, "answer": "a", "quote": "q", "ts": 1}) + "\n")
            fh.write(json.dumps({"topic": "t", "answer": "a", "quote": "q", "ts": "later"}) + "\n")
            fh.write('{"topic": "cut off mid-wri')
        self.assertEqual([e["topic"] for e in self.log.entries()], ["good"])

    def test_a_deeply_nested_line_is_skipped(self):
        self.log.record("good", "yes", "yes please")
        with open(self.log.path, "a") as fh:
            fh.write("[" * 100000 + "\n")
        self.assertEqual([e["topic"] for e in self.log.entries()], ["good"])

    def test_a_record_with_an_impossible_time_is_skipped(self):
        self.log.record("good", "yes", "yes please")
        impossible = (
            ("nan", float("nan")),
            ("infinity", float("inf")),
            ("minus-infinity", float("-inf")),
            ("far-future", 1e20),
            ("before-1970", -1),
            ("year-10000", 253402300800),
            ("too-big-for-a-float", 10 ** 400),
        )
        with open(self.log.path, "a") as fh:
            for topic, ts in impossible:
                fh.write(json.dumps({"topic": topic, "answer": "a", "quote": "q", "ts": ts}) + "\n")
        entries = self.log.entries()
        self.assertEqual([e["topic"] for e in entries], ["good"])
        for e in entries:
            self.assertTrue(format_line(e).startswith("good: yes ("))

    def test_a_torn_last_line_does_not_swallow_the_next_record(self):
        self.log.record("first", "yes", "yes please")
        with open(self.log.path, "a") as fh:
            fh.write('{"topic": "cut off mid-wri')
        self.log.record("second", "no", "no thanks")
        self.assertEqual([e["topic"] for e in self.log.entries()], ["first", "second"])

    def test_format_line_names_the_repo_only_when_there_is_one(self):
        self.log.record("t", "a", "q", repo="myapp")
        self.log.record("u", "b", "r")
        lines = {e["topic"]: format_line(e) for e in self.log.entries()}
        self.assertIn("agent-a in myapp)", lines["t"])
        self.assertIn(", agent-a)", lines["u"])
        self.assertNotIn(" in ", lines["u"])
        self.assertTrue(format_line(self.log.entries()[0], current=False).startswith("(replaced) t: a ("))

    def test_concurrent_records_stay_whole_lines(self):
        quote = "x" * 3000
        script = (
            "import sys\n"
            "from coms.decisions import DecisionLog\n"
            "log = DecisionLog(sys.argv[1], sys.argv[2])\n"
            "for i in range(25):\n"
            "    log.record('topic-%d' % i, 'answer', sys.argv[3])\n"
        )
        env = dict(os.environ, PYTHONPATH=REPO_ROOT)
        procs = [
            subprocess.Popen([sys.executable, "-B", "-c", script, self.dir, f"w{n}", quote], env=env)
            for n in range(4)
        ]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        lines = self.lines()
        self.assertEqual(len(lines), 100)
        self.assertTrue(all(json.loads(ln)["quote"] == quote for ln in lines))

    def test_record_waits_for_the_coord_lock(self):
        holder = FileMutex(os.path.join(self.dir, "locks", "coord.lock"))
        holder.acquire()
        worker = threading.Thread(target=self.log.record, args=("t", "a", "q"))
        worker.start()
        worker.join(0.5)
        self.assertTrue(worker.is_alive())
        self.assertFalse(os.path.exists(self.log.path))
        holder.release()
        worker.join(5)
        self.assertEqual(len(self.log.entries()), 1)


class DecisionsCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="coms-dec-cli-")
        self.outside = tempfile.mkdtemp(prefix="coms-dec-cwd-")  # not inside any git repo
        self._env = dict(os.environ)
        self._cwd = os.getcwd()
        os.environ["COMS_DIR"] = self.dir
        os.environ["COMS_HOLDER"] = "sess-test"
        os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(self.outside)
        os.chdir(self.outside)

    def tearDown(self):
        os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._env)

    def run_out(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def log_exists(self):
        return os.path.exists(os.path.join(self.dir, "decisions.jsonl"))

    def test_decide_then_decisions_finds_it(self):
        code, out, _ = self.run_out("decide", "release-date", "ship", "friday", "--quote", "friday works")
        self.assertEqual((code, out), (0, "coms: recorded decision on release-date\n"))
        code, out, _ = self.run_out("decisions", "release")
        self.assertEqual(code, 0)
        self.assertRegex(
            out, r'^release-date: ship friday \(\d{4}-\d\d-\d\d \d\d:\d\d, sess-test\) "friday works"\n$'
        )

    def test_decide_without_quote_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["decide", "t", "a"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--quote", err.getvalue())
        self.assertFalse(self.log_exists())

    def test_decide_with_a_blank_quote_exits_2_and_writes_nothing(self):
        code, out, err = self.run_out("decide", "t", "a", "--quote", "  ")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("coms: a decision needs", err)
        self.assertFalse(self.log_exists())

    def test_decisions_says_when_there_is_nothing_to_show(self):
        self.assertEqual(self.run_out("decisions")[:2], (0, "coms: no decisions recorded\n"))
        self.run_out("decide", "t", "a", "--quote", "q")
        self.assertEqual(self.run_out("decisions", "banana")[:2], (0, "coms: no decisions match 'banana'\n"))

    def test_all_shows_replaced_answers_and_json_lists_records(self):
        self.run_out("decide", "t", "keep", "--quote", "keep it")
        self.run_out("decide", "t", "drop", "--quote", "drop it")
        lines = self.run_out("decisions", "--all")[1].splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("t: drop ("))
        self.assertTrue(lines[1].startswith("(replaced) t: keep ("))
        records = json.loads(self.run_out("decisions", "--json")[1])
        self.assertEqual([r["answer"] for r in records], ["drop"])
        self.assertEqual(sorted(records[0]), sorted(FIELDS))

    def test_decide_outside_a_repo_records_no_repo(self):
        self.run_out("decide", "t", "a", "--quote", "q")
        [record] = json.loads(self.run_out("decisions", "--json")[1])
        self.assertEqual(record["repo"], "")

    def test_decide_records_the_repo_and_decisions_shows_it(self):
        with mock.patch.object(cli, "_current_repo", return_value="kit"):
            self.run_out("decide", "release-date", "ship", "friday", "--quote", "friday works")
        out = self.run_out("decisions", "release", "friday")[1]
        self.assertIn("sess-test in kit)", out)
        [record] = json.loads(self.run_out("decisions", "--json")[1])
        self.assertEqual(record["repo"], "kit")
