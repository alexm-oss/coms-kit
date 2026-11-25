import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

BIN = Path(__file__).resolve().parents[1] / "skills" / "coms" / "bin"
SCRIPTS = ("pace.py", "codex-pace-watch.sh", "load-watch.sh", "codex-now.py", "codex-fleet.py")


class OperatorToolsTest(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory(prefix="coms-operator-")
        self.addCleanup(sandbox.cleanup)
        self.home = Path(sandbox.name)
        self.watch = self.home / "watch"
        self.codex = self.home / "codex"
        self.env = {
            "PATH": os.environ["PATH"], "HOME": str(self.home), "COMS_WATCH_DIR": str(self.watch),
            "CODEX_HOME": str(self.codex), "LOAD_LIMIT": "10", "THERMAL_LIMIT": "2",
            "STRAIN_MINUTES": "1", "INTERVAL": "30",
        }

    def run_script(self, script, *args, input=None, check=True, env=None):
        return subprocess.run(
            [str(BIN / script), *map(str, args)], env=dict(self.env, **(env or {})),
            check=check, capture_output=True, text=True, input=input, timeout=10,
        )

    def pace_state(self, seconds_ago, used):
        self.watch.mkdir(exist_ok=True)
        now = time.time()
        self.reset_stamp = int(now + 3600)
        data = {"end": self.reset_stamp, "level": 3, "samples": [{"t": now - seconds_ago, "u": used}]}
        (self.watch / "pace-state.json").write_text(json.dumps(data), encoding="utf-8")

    def meter(self, used, reset, *args, check=True):
        sample = json.dumps({"used_percent": used, "resets_at": reset})
        return self.run_script("codex-pace-watch.sh", "--once", "--usage-file", "-", *args, input=sample, check=check)

    def samples(self, text, *args, check=True):
        path = self.home / "samples.txt"
        path.write_text(text, encoding="utf-8")
        return self.run_script("load-watch.sh", "--samples", path, *args, check=check)

    @staticmethod
    def fixture_id(digit):
        return "-".join(digit * width for width in (8, 4, 4, 4, 12))

    def rollout(self, digit, records, age=0, archive=False, partial=""):
        folder = self.codex / ("archived_sessions" if archive else "sessions" / Path("2044/01/01"))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-2044-01-01T00-00-00-{self.fixture_id(digit)}.jsonl"
        path.write_text("".join(json.dumps(x) + "\n" for x in records) + partial, encoding="utf-8")
        os.utime(path, (time.time() - age, time.time() - age))
        return path

    @staticmethod
    def event(kind, **fields):
        return {"type": "event_msg", "payload": {"type": kind, **fields}}

    def metadata(self, digit, source):
        return {"type": "session_meta", "payload": {"source": source, "id": self.fixture_id(digit)}}

    def test_every_entry_point_has_help(self):
        for script in SCRIPTS:
            with self.subTest(script=script):
                self.assertIn("usage:", self.run_script(script, "--help").stdout.lower())
        self.assertFalse(self.watch.exists())
        self.assertFalse(self.codex.exists())

    def test_claude_meter_handover_and_after_window(self):
        reset = "2044-01-01T12:30:00+00:00"
        still_open = self.run_script("pace.py", "97.99", reset).stdout
        self.assertNotIn("HANDOVER", still_open)
        handover = self.run_script("pace.py", "98").stdout
        self.assertIn("HANDOVER", handover)
        self.assertIn("references/handover.md", handover)
        self.assertIn("AFTER", self.run_script("pace.py", "100", "2000-01-01T00:00:00Z").stdout)

    def test_claude_meter_recommends_width_changes_from_samples(self):
        for previous, used, expected, level in (
                (40, 50, "LOWER", "level 2"), (40, 41, "RAISE", "level 4"),
                (42, 50, "HOLD", "level 3")):
            with self.subTest(verdict=expected):
                self.pace_state(600, previous)
                result = self.run_script("pace.py", used)
                self.assertIn(expected, result.stdout)
                self.assertIn(level, result.stdout)

    def test_claude_meter_drops_old_window_and_recovers_damaged_state(self):
        self.pace_state(600, 0)
        result = self.run_script("pace.py", "13", "2044-01-01T12:30:00Z")
        self.assertIn("measured unknown", result.stdout)
        state = self.watch / "pace-state.json"
        state.write_text('{"level":"bad","end":null,"samples":{}}', encoding="utf-8")
        result = self.run_script("pace.py", "20", "2044-01-01T12:30:00Z")
        self.assertIn("starting fresh", result.stderr)
        self.assertIn("HOLD", result.stdout)
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_claude_meter_validates_inputs(self):
        for value, reset in (("nan", "2044-01-01T00:00:00Z"), ("101", "2044-01-01T00:00:00Z"),
                             ("40", "2044-01-01T00:00:00"), ("40", "not a time")):
            with self.subTest(value=value, reset=reset):
                self.assertEqual(2, self.run_script("pace.py", value, reset, check=False).returncode)
        self.assertFalse(self.watch.exists())

    def test_weekly_meter_compares_to_elapsed_pace_line_and_rebases(self):
        reset = time.time() + 604800
        result = self.meter(15, reset)
        self.assertIn("ON PACE | used 15% | pace line 15.0%", result.stdout)
        state = self.watch / "codex-pace-state.json"
        values = json.loads(state.read_text(encoding="utf-8"))
        values["started_at"] -= 7200
        state.write_text(json.dumps(values), encoding="utf-8")
        self.assertIn("OVER PACE | used 22% | pace line 17.0%", self.meter(22, reset).stdout)
        self.assertIn("PLAN USED", self.meter(100, reset).stdout)
        self.assertIn("NEW WINDOW | ON PACE", self.meter(1, reset + 604800).stdout)

    def test_weekly_meter_rejects_invalid_data_and_validates_expiry(self):
        reset = time.time() + 604800
        for value, stamp in ((True, reset), (-1, reset), (101, reset), (1, "local time")):
            with self.subTest(value=value, stamp=stamp):
                self.assertEqual(self.meter(value, stamp, check=False).returncode, 2)
        self.assertIn("READING EXPIRED", self.meter(99, time.time() - 60).stdout)

    def test_weekly_meter_reads_named_file_and_leaves_it_alone(self):
        path = self.home / "manual-meter.json"
        sample = '{"used_percent": 32.5, "resets_at": "2044-01-01T12:30:00Z"}'
        path.write_text(sample, encoding="utf-8")
        result = self.run_script("codex-pace-watch.sh", "--once", env={"CODEX_WEEKLY_USAGE_FILE": str(path)})
        self.assertIn("ON PACE | used 32.5%", result.stdout)
        self.assertEqual(sample, path.read_text(encoding="utf-8"))

    def test_host_load_and_thermal_thresholds(self):
        self.assertIn("CLEAR", self.samples("10 -\n", "--once").stdout)
        self.assertIn("STRAIN", self.samples("10.01 -\n", "--once").stdout)
        self.assertIn("STRAIN", self.samples("0 2\n", "--once").stdout)
        self.assertIn("UNKNOWN", self.samples("- -\n", "--once").stdout)
        self.assertEqual(2, self.samples("bad sample\n", "--once", check=False).returncode)

    def test_host_load_requires_sustained_strain_and_resets_on_clear(self):
        self.assertEqual("", self.samples("11 -\n11 -\n").stdout)
        self.assertIn("HOST UNDER STRAIN for 1 min", self.samples("11 -\n0 2\n11 -").stdout)
        self.assertEqual("", self.samples("11 -\n11 -\n0 -\n11 -\n11 -\n").stdout)
        self.assertEqual("", self.samples("11 -\n11 -\n- -\n11 -\n11 -\n").stdout)

    def test_host_load_works_when_macos_thermal_is_unavailable(self):
        mocks = self.home / "bin"
        mocks.mkdir()
        for name, body in {
                "uname": "printf '%s\\n' Darwin", "sysctl": "printf '%s\\n' '{ 18.75 10.00 10.00 }'",
                "notifyutil": "exit 1"}.items():
            path = mocks / name
            path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            path.chmod(0o755)
        result = self.run_script("load-watch.sh", "--once", env={"PATH": str(mocks) + os.pathsep + self.env["PATH"]})
        self.assertEqual("STRAIN | load_1m=18.75 | thermal=-\n", result.stdout)

    def test_now_summarizes_only_metadata(self):
        self.rollout("1", [
            self.metadata("1", "desktop"),
            {"type": "turn_context", "payload": {"model": "gpt-5", "effort": "low"}},
            self.event("thread_settings_applied", thread_id=self.fixture_id("1"), thread_settings={
                "model": "gpt-6.2-astra", "reasoning_effort": "high", "service_tier": "default",
            }),
            self.event("task_started"),
            self.event("agent_message", message="synthetic private transcript"),
            {"type": "response_item", "payload": {"type": "function_call", "arguments": "synthetic tool input"}},
        ], partial=json.dumps(self.event("task_complete", last_agent_message="still writing")))
        result = self.run_script("codex-now.py", "1111")
        self.assertIn("turn open | model gpt-6.2-astra | effort high | service tier default", result.stdout)
        self.assertNotIn("1111", result.stdout)
        self.assertNotIn("synthetic", result.stdout)
        self.assertNotIn(str(self.home), result.stdout)
        self.rollout("2", [self.metadata("2", "desktop"), self.event("task_complete")], archive=True)
        self.assertIn("turn closed", self.run_script("codex-now.py", "2222").stdout)

    def test_malformed_or_linked_rollouts_do_not_disclose_values(self):
        path = self.rollout("2", [
            self.metadata("2", "desktop"),
            {"type": "turn_context", "payload": {"model": "fixture-private-model", "effort": ["oops"]}},
            self.event("thread_settings_applied", service_tier={"field": "fixture-private-tier"}),
        ])
        link = path.with_name(path.name.replace(self.fixture_id("2"), self.fixture_id("3")))
        link.symlink_to(path)
        result = self.run_script("codex-now.py", "2222")
        self.assertIn("turn unknown | model unknown | effort unknown | service tier unknown", result.stdout)
        self.assertNotIn("fixture-private", result.stdout)
        result = self.run_script("codex-now.py", "3333", check=False)
        self.assertEqual(1, result.returncode)
        self.assertIn("no matching rollout", result.stderr)

    def test_large_or_unrecognized_headers_preserve_unknown_classification(self):
        for digit, size, source in (
                ("1", 70 * 1024, "vscode"), ("2", 1050 * 1024, "vscode"), ("3", 0, {"other": "fixture"})):
            header = self.metadata(digit, source)
            header["payload"]["instructions"] = "x " * (size // 2)
            self.rollout(digit, [header, self.event("task_started")])
        fleet = self.run_script("codex-fleet.py")
        self.assertIn("roots 1 | helpers 0 | unknown 2", fleet.stdout)
        self.assertIn("Selection 1 (root)", self.run_script("codex-now.py", "1111").stdout)
        result = self.run_script("codex-now.py", "2222")
        self.assertIn("Selection 1 (unknown)", result.stdout)
        self.assertIn("turn open", result.stdout)

    def test_fleet_groups_recent_helpers_and_ignores_old_rollouts(self):
        self.rollout("1", [self.metadata("1", "desktop"), self.event("task_started")])
        linked = {"subagent": {"thread_spawn": {"parent_thread_id": self.fixture_id("1")}}}
        self.rollout("2", [self.metadata("2", linked), self.event("turn_aborted")])
        unlinked = {"subagent": {"thread_spawn": {"parent_thread_id": self.fixture_id("3")}}}
        self.rollout("3", [self.metadata("3", "desktop"), self.event("task_complete")], age=3600)
        self.rollout("4", [self.metadata("4", unlinked)])
        result = self.run_script("codex-fleet.py", "10")
        self.assertIn("roots 1 | helpers 2 | unknown 0", result.stdout)
        self.assertIn("recent helpers 1", result.stdout)
        self.assertIn("Helpers without a recent root | 1", result.stdout)
        self.assertNotIn(self.fixture_id("1"), result.stdout)


if __name__ == "__main__":
    unittest.main()
