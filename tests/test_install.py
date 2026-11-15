import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="coms-home-")
        self.tmp = tempfile.mkdtemp(prefix="coms-install-")

    def env(self):
        """Every override install.sh reads, plus a throwaway HOME, so running
        this suite can never write into the real ~/.claude."""
        return dict(
            os.environ,
            HOME=self.home,
            COMS_BIN_DIR=os.path.join(self.tmp, "bin"),
            COMS_SKILLS_DIR=os.path.join(self.tmp, "skills"),
            COMS_STATE_DIR=os.path.join(self.tmp, "state"),
            COMS_HOOKS_DIR=os.path.join(self.tmp, "hooks"),
        )

    def test_installer_creates_a_launcher_that_runs_the_cli(self):
        result = subprocess.run(
            ["bash", os.path.join(REPO_ROOT, "install.sh")],
            env=self.env(), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        launcher = os.path.join(self.tmp, "bin", "coms")
        self.assertTrue(os.access(launcher, os.X_OK), "launcher not executable")

        run = subprocess.run(
            [launcher, "status", "--json"],
            env=dict(self.env(), COMS_DIR=os.path.join(self.tmp, "state")),
            capture_output=True, text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        json.loads(run.stdout)  # launcher produced valid JSON: it really ran coms
        self.assertEqual(os.listdir(self.home), [])

    def test_installer_test_keeps_hooks_out_of_home(self):
        result = subprocess.run(
            ["bash", os.path.join(REPO_ROOT, "install.sh"), "--claude-hooks"],
            env=self.env(), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "hooks", "coms-context")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "hooks", "coms-inbox-stop-gate.sh")))
        self.assertEqual(os.listdir(self.home), [])


INSTALL = os.path.join(REPO_ROOT, "install.sh")
UNINSTALL = os.path.join(REPO_ROOT, "uninstall.sh")
BASH = os.environ.get("COMS_TEST_BASH", "bash")


def files_under(root):
    out = set()
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            out.add(os.path.relpath(os.path.join(dirpath, name), root))
    return out


class KitInstallTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="coms-home-")
        self.env = dict(os.environ, HOME=self.home)
        for var in ("COMS_BIN_DIR", "COMS_SKILLS_DIR", "COMS_HOOKS_DIR",
                    "COMS_STATE_DIR", "COMS_DIR", "COMS_HOLDER"):
            self.env.pop(var, None)
        self.skill = os.path.join(self.home, ".claude", "skills", "coms")
        self.launcher = os.path.join(self.home, ".local", "bin", "coms")
        self.hooks = os.path.join(self.home, ".claude", "hooks")

    def sh(self, script, *args):
        return subprocess.run([BASH, script, *args], env=self.env,
                              capture_output=True, text=True)

    def launch(self, *args):
        # Run from the throwaway home, not the kit, so the launcher has to find
        # the kit on its own.
        return subprocess.run([self.launcher, *args], env=self.env, cwd=self.home,
                              capture_output=True, text=True)

    def test_install_gives_a_working_launcher_and_the_skill(self):
        r = self.sh(INSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.launch("status", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        json.loads(run.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.skill, "SKILL.md")))
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "coms-context")))

    def test_cli_only_skips_the_skill(self):
        r = self.sh(INSTALL, "--cli-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.skill))

    def test_claude_hooks_are_opt_in_and_settings_are_never_edited(self):
        r = self.sh(INSTALL, "--claude-hooks")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.access(os.path.join(self.hooks, "coms-context"), os.X_OK))
        self.assertTrue(os.access(os.path.join(self.hooks, "coms-inbox-stop-gate.sh"), os.X_OK))
        self.assertIn('"UserPromptSubmit"', r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "settings.json")))

    def test_refuses_to_replace_a_skill_it_did_not_install(self):
        os.makedirs(self.skill)
        with open(os.path.join(self.skill, "SKILL.md"), "w") as fh:
            fh.write("someone else's skill\n")
        before = files_under(self.home)
        r = self.sh(INSTALL)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertEqual(files_under(self.home), before)

    def test_force_moves_a_symlinked_skill_aside_without_touching_its_target(self):
        target = tempfile.mkdtemp(prefix="coms-foreign-skill-")
        with open(os.path.join(target, "SKILL.md"), "w") as fh:
            fh.write("personal skill\n")
        os.makedirs(os.path.dirname(self.skill))
        os.symlink(target, self.skill)
        r = self.sh(INSTALL, "--force")
        self.assertEqual(r.returncode, 0, r.stderr)
        parent = os.path.dirname(self.skill)
        aside = [n for n in os.listdir(parent) if n.startswith("coms.bak-")]
        self.assertEqual(len(aside), 1)
        self.assertTrue(os.path.islink(os.path.join(parent, aside[0])))
        with open(os.path.join(target, "SKILL.md")) as fh:
            self.assertEqual(fh.read(), "personal skill\n")
        self.assertFalse(os.path.islink(self.skill))

    def test_reinstall_over_its_own_install_works(self):
        self.assertEqual(self.sh(INSTALL, "--claude-hooks").returncode, 0)
        r = self.sh(INSTALL, "--claude-hooks")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_install_from_a_path_with_spaces(self):
        kit = os.path.join(tempfile.mkdtemp(prefix="coms-sp-"), "my tools", "coms-kit")
        shutil.copytree(REPO_ROOT, kit, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        r = subprocess.run([BASH, os.path.join(kit, "install.sh")], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.launch("status", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_uninstall_purge_leaves_home_as_it_was(self):
        before = files_under(self.home)
        self.assertEqual(self.sh(INSTALL, "--claude-hooks").returncode, 0)
        self.assertEqual(self.launch("enroll", "--intent", "test").returncode, 0)
        r = self.sh(UNINSTALL, "--purge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(files_under(self.home), before)

    def test_uninstall_keeps_state_and_foreign_files_by_default(self):
        self.assertEqual(self.sh(INSTALL).returncode, 0)
        os.makedirs(self.hooks, exist_ok=True)
        foreign = os.path.join(self.hooks, "coms-context")
        with open(foreign, "w") as fh:
            fh.write("#!/bin/sh\n# someone else's hook\n")
        r = self.sh(UNINSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(foreign))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".claude", "coms")))
        self.assertFalse(os.path.exists(self.launcher))
        self.assertFalse(os.path.exists(self.skill))
        self.assertNotIn("install.sh again", r.stdout)
        self.assertIn("-m coms.cli unguard <repo>", r.stdout)

    def test_unguard_command_printed_by_uninstall_works_without_the_launcher(self):
        self.assertEqual(self.sh(INSTALL).returncode, 0)
        repo = os.path.join(self.home, "repo")
        subprocess.run(["git", "init", "-q", repo], check=True)
        self.assertEqual(self.launch("guard", repo).returncode, 0)
        r = self.sh(UNINSTALL)
        line = next(l for l in r.stdout.splitlines() if "unguard <repo>" in l).strip()
        run = subprocess.run(["bash", "-c", line.replace("<repo>", "\"$1\""), "unguard", repo],
                             env=self.env, cwd=self.home, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(os.path.exists(os.path.join(repo, ".git", "hooks", "pre-commit")))

    def test_refuses_to_replace_a_launcher_it_did_not_install(self):
        os.makedirs(os.path.dirname(self.launcher))
        with open(self.launcher, "w") as fh:
            fh.write("#!/bin/sh\necho theirs\n")
        before = files_under(self.home)
        r = self.sh(INSTALL)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertEqual(files_under(self.home), before)
        with open(self.launcher) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho theirs\n")

    def test_refuses_to_replace_hooks_it_did_not_install(self):
        os.makedirs(self.hooks)
        gate = os.path.join(self.hooks, "coms-inbox-stop-gate.sh")
        with open(gate, "w") as fh:
            fh.write("#!/bin/sh\n# not from the kit\n")
        before = files_under(self.home)
        r = self.sh(INSTALL, "--claude-hooks")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Nothing was changed", r.stderr)
        self.assertEqual(files_under(self.home), before)
        with open(gate) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\n# not from the kit\n")

    def test_uninstall_leaves_a_skill_it_did_not_install(self):
        os.makedirs(self.skill)
        with open(os.path.join(self.skill, "SKILL.md"), "w") as fh:
            fh.write("someone else's skill\n")
        before = files_under(self.home)
        r = self.sh(UNINSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(files_under(self.home), before)

    def test_launcher_ignores_a_coms_package_in_the_current_folder(self):
        self.assertEqual(self.sh(INSTALL).returncode, 0)
        fake = os.path.join(self.home, "coms")
        os.makedirs(fake)
        with open(os.path.join(fake, "__init__.py"), "w"):
            pass
        with open(os.path.join(fake, "cli.py"), "w") as fh:
            fh.write('print("SHADOW")\n\n\ndef main():\n    return 0\n')
        run = self.launch("status", "--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("SHADOW", run.stdout)
        json.loads(run.stdout)

    def test_purge_leaves_a_state_folder_the_kit_did_not_create(self):
        state = os.path.join(self.home, ".claude")
        os.makedirs(state)
        with open(os.path.join(state, "settings.json"), "w") as fh:
            fh.write("{}\n")
        self.env["COMS_STATE_DIR"] = state
        self.assertEqual(self.sh(INSTALL).returncode, 0)
        r = self.sh(UNINSTALL, "--purge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(state, "settings.json")))
        self.assertIn("left alone", r.stdout)
