import json
from pathlib import Path
import subprocess
import unittest

HOOK = Path(__file__).resolve().parents[1] / "hooks/claude/helper-brief"


class HelperBriefTest(unittest.TestCase):
    def check_hook(self, tool, fields, *args):
        return subprocess.run([str(HOOK), *args], text=True, input=json.dumps(
            {"tool_name": tool, "tool_input": fields}), capture_output=True)

    def test_accepts_a_complete_brief_and_declines_each_missing_field(self):
        fields = ["Task: verify fixture", "Model: chosen", "Effort: medium", "Time budget: 10 minutes"]
        self.assertEqual(self.check_hook("Agent", {"prompt": "\n".join(fields)}).returncode, 0)
        for i in range(4):
            for tool in ("Agent", "NewSession"):
                with self.subTest(i=i, tool=tool):
                    run = self.check_hook(tool, {"prompt": "\n".join(fields[:i] + fields[i + 1:])})
                    self.assertEqual(run.returncode, 2)
                    self.assertIn(fields[i].split(":")[0], run.stderr)

    def test_unrelated_tool_and_configured_labels(self):
        self.assertEqual(self.check_hook("Read", {}).returncode, 0)
        fields = {"model": "chosen", "prompt": "Work: review\nDepth: high\nTime: 10 minutes"}
        run = self.check_hook("Start", fields, "--tool", "Start", "--labels", "Work", "Engine", "Depth", "Time")
        self.assertEqual(run.returncode, 0, run.stderr)
