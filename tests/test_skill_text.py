import os
import unittest

from coms.cli import PRIMER

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SkillTextTest(unittest.TestCase):
    def test_skill_and_primer_teach_the_decision_log(self):
        with open(os.path.join(REPO_ROOT, "skills", "coms", "SKILL.md")) as fh:
            skill = fh.read()
        for text in (skill, PRIMER):
            self.assertIn("coms decisions", text)
            self.assertIn("coms decide ", text)
            self.assertIn("--quote '", text)
            self.assertNotIn('--quote "', text)
        self.assertIn("search: `coms decisions <keyword>`", skill)

    def test_operating_model_teaches_the_orchestrator_to_delegate(self):
        with open(os.path.join(REPO_ROOT, "skills", "coms", "references", "operating-model.md")) as fh:
            model = " ".join(fh.read().split())
        self.assertIn("hands tasks to other windows or to short-lived helpers", model)

    def test_skill_is_a_short_starter(self):
        with open(os.path.join(REPO_ROOT, "skills", "coms", "SKILL.md")) as fh:
            skill = fh.read()
        self.assertLessEqual(len(skill.splitlines()), 150)
        for needed in ("coms enroll", "coms status", "coms guard", "coms do main", "coms do ship",
                       "merge-base --is-ancestor", "coms msg", "coms inbox --consume", "coms inbox --peek",
                       "coms check", "coms unlock", "references/operating-model.md", "references/handover.md"):
            self.assertIn(needed, skill)


class PermissionAndRolesTest(unittest.TestCase):
    def test_skill_primer_and_hook_say_coms_do_grants_no_permission(self):
        paths = {"SKILL.md": os.path.join(REPO_ROOT, "skills", "coms", "SKILL.md"),
                 "hook": os.path.join(REPO_ROOT, "hooks", "claude", "coms-context")}
        texts = {"PRIMER": PRIMER}
        for name, path in paths.items():
            with open(path) as fh:
                texts[name] = fh.read()
        for name, text in texts.items():
            words = " ".join(text.split())
            self.assertIn("needs the human's own yes first", words, name)
            self.assertIn("it never grants permission", words, name)

    def test_documented_roles_are_the_roles_handover_accepts(self):
        import re
        from coms import handover
        accepted = {"orchestrator", "controller", "codex-lead", handover.REVIEW_ROLE}
        with open(os.path.join(REPO_ROOT, "skills", "coms", "references", "operating-model.md")) as fh:
            documented = set(re.findall(r"--role ([a-z-]+)", fh.read()))
        self.assertEqual(documented, accepted)
