import json
import os
import subprocess
import tempfile
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(KIT_ROOT, "skills", "coms")
PEER = os.path.join(SKILL_DIR, "bin", "coms-peer")


def frontmatter(text):
    _, head, body = text.split("---\n", 2)
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields, body


class SkillTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SKILL_DIR, "SKILL.md")) as fh:
            self.text = fh.read()
        self.fields, self.body = frontmatter(self.text)

    def test_frontmatter_follows_the_shared_skill_rules(self):
        self.assertEqual(self.fields["name"], "coms")
        desc = self.fields["description"]
        self.assertLessEqual(len(desc), 250)
        self.assertNotIn(". ", desc)  # one sentence
        self.assertTrue(desc.endswith("."))

    def test_skill_stays_short_and_harness_neutral(self):
        self.assertLess(len(self.text.splitlines()), 500)
        self.assertNotIn("mcp__", self.text)
        self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, "references", "claude-desktop.md")))

    def test_skill_folder_has_no_symlinks(self):
        for dirpath, dirnames, filenames in os.walk(SKILL_DIR):
            for name in dirnames + filenames:
                self.assertFalse(os.path.islink(os.path.join(dirpath, name)), name)

    def test_peer_helper_reads_threads_from_coms_dir(self):
        self.assertTrue(os.access(PEER, os.X_OK))
        state = tempfile.mkdtemp(prefix="coms-peer-state-")
        os.makedirs(os.path.join(state, "threads"))
        with open(os.path.join(state, "threads", "claude-1234abcd.json"), "w") as fh:
            json.dump({"holder": "claude-1234abcd", "repo": "myapp", "worktree": "wt-login"}, fh)
        sessions = [
            {"sessionId": "local_main", "title": "main", "cwd": "/x/myapp", "lastActivityAt": "4"},
            {"sessionId": "local_a", "title": "login", "cwd": "/x/wt-login", "lastActivityAt": "2"},
            {"sessionId": "local_b", "title": "other", "cwd": "/x/wt-other", "lastActivityAt": "3"},
        ]
        r = subprocess.run(
            ["python3", "-B", PEER, "claude-1234abcd"],
            input=json.dumps(sessions), capture_output=True, text=True,
            env=dict(os.environ, COMS_DIR=state),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["local_a\tlogin\t/x/wt-login"])

    def test_skill_primer_readme_and_desktop_notes_teach_the_decision_log(self):
        from coms.cli import PRIMER
        texts = {"SKILL.md": self.body, "PRIMER": PRIMER}
        for name in ("README.md", os.path.join("skills", "coms", "references", "claude-desktop.md")):
            with open(os.path.join(KIT_ROOT, name)) as fh:
                texts[name] = fh.read()
        for name, text in texts.items():
            self.assertIn("coms decisions", text, name)
        for name in ("SKILL.md", "PRIMER", "README.md"):
            self.assertIn("coms decide ", texts[name], name)
            self.assertIn("--quote", texts[name], name)
        for name in ("SKILL.md", "PRIMER", "README.md"):
            self.assertIn("--quote '", texts[name], name)
            self.assertIn("'\\''", texts[name], name)
        for name, text in texts.items():
            self.assertNotIn('--quote "', text, name)
