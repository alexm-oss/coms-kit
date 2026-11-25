import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("COMS_TEST_BASH", "bash")
PIECE_FILES = {
    "command": ("references/command.md",),
    "controller": ("references/controller.md",),
    "codex-lead": ("references/codex-lead.md",),
    "governor": ("references/governor.md",),
    "security": ("references/security-reviewer.md",),
    "keeper": ("references/keeper.md",),
    "pacing": ("bin/pace.py", "bin/codex-pace-watch.sh", "bin/codex_pace_watch.py"),
    "load-watch": ("bin/load-watch.sh",),
    "codex-view": ("bin/codex-now.py", "bin/codex-fleet.py", "bin/codex_local.py"),
    "notify": ("bin/notify",),
    "job-health": ("bin/job-health.py", "bin/job-watch.txt"),
    "known-fixes": ("bin/known-fixes", "bin/known-fixes.txt"),
    "current-rules": ("bin/current-rules", "bin/rule-pins.txt"),
}
SOLO = {"command", "notify"}
PAIR = SOLO | {"codex-lead", "pacing", "load-watch", "codex-view", "handover-guide"}
FULL = PAIR | {"governor", "security", "keeper", "helper-brief", "job-health", "known-fixes", "current-rules"}


class InstallProfilesTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="coms-profiles-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.skill = self.home / ".claude" / "skills" / "coms"
        self.hooks = self.home / ".claude" / "hooks"
        self.settings = self.home / ".claude" / "settings.json"
        self.launcher = self.home / ".local" / "bin" / "coms"
        self.scheduler_log = self.scratch / "scheduler.log"
        self.mocks = self.scratch / "bin"
        self.mocks.mkdir()
        self.env = {
            "PATH": str(self.mocks) + os.pathsep + os.environ["PATH"],
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "SCHEDULER_LOG": str(self.scheduler_log),
        }
        for command in ("launchctl", "systemctl", "crontab"):
            path = self.mocks / command
            path.write_text(
                '#!/bin/sh\nprintf "%s\\n" "scheduler invoked" >> "$SCHEDULER_LOG"\nexit 1\n',
                encoding="utf-8",
            )
            path.chmod(0o755)

    def execute(self, program, *args, check=True):
        result = subprocess.run([*program, *args], env=self.env, cwd=self.home,
                                capture_output=True, text=True, timeout=20, check=False)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def install(self, *args, check=True):
        return self.execute([BASH, str(REPO_ROOT / "install.sh")], *args, check=check)

    def uninstall(self):
        return self.execute([BASH, str(REPO_ROOT / "uninstall.sh")])

    def snapshot(self):
        entries = {}
        for path in [self.home, *self.home.rglob("*")]:
            stat = path.lstat()
            if path.is_symlink():
                value = os.readlink(path)
            elif path.is_file():
                value = path.read_bytes()
            else:
                value = None
            entries[path.relative_to(self.home)] = (stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns, value)
        return entries

    def assert_no_background_setup(self):
        self.assertFalse(self.settings.exists())
        self.assertFalse((self.home / "Library" / "LaunchAgents").exists())
        self.assertFalse((self.home / ".config" / "systemd").exists())
        self.assertFalse(self.scheduler_log.exists())

    def assert_pieces(self, enabled):
        self.assertTrue(self.launcher.is_file())
        self.assertTrue(os.access(self.launcher, os.X_OK))
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertTrue((self.skill / "bin" / "handover-note").is_file())
        for feature, paths in PIECE_FILES.items():
            for path in paths:
                with self.subTest(feature=feature, path=path):
                    self.assertEqual((self.skill / path).is_file(), feature in enabled)
        self.assertEqual((self.hooks / "helper-brief").is_file(), "helper-brief" in enabled)
        shared_code = (self.skill / "bin" / "operator_state.py").is_file()
        self.assertEqual(shared_code, bool({"pacing", "codex-view"} & enabled))
        handover = self.skill / "references" / "handover.md"
        guide = handover.read_text(encoding="utf-8") if handover.is_file() else ""
        for heading in ("## No replay, rotate instead", "## Durable state and identity",
                        "## New Claude window checklist", "## New Codex lead checklist"):
            self.assertEqual(heading in guide, "handover-guide" in enabled, heading)
        self.assert_no_background_setup()

    def test_default_profile_is_core(self):
        self.install()
        self.assert_pieces(set())
        self.execute([str(self.launcher)], "handover", "--help")
        self.execute([str(self.skill / "bin" / "handover-note")], "--help")

    def test_each_profile_status_and_plain_uninstall(self):
        for profile, pieces in (("core", set()), ("solo", SOLO), ("pair", PAIR), ("full-room", FULL)):
            with self.subTest(profile=profile):
                self.install("--profile", profile)
                self.execute([str(self.launcher)], "status")
                help_text = self.execute([str(self.launcher)], "--help").stdout
                for cmd in ("known-fixes", "current-rules"):
                    self.assertEqual(cmd in help_text, cmd in pieces)
                self.uninstall()
                self.assertEqual(list(self.home.iterdir()), [])

    def test_solo_profile_adds_command_and_notification(self):
        self.install("--profile", "solo")
        self.assert_pieces(SOLO)

    def test_pair_profile_adds_codex_tools_pacing_and_the_full_handover(self):
        self.install("--profile", "pair")
        self.assert_pieces(PAIR)

    def test_full_room_profile_has_no_controller_without_opt_in(self):
        self.install("--profile", "full-room")
        self.assert_pieces(FULL)
        self.assertTrue(os.access(self.hooks / "helper-brief", os.X_OK))

    def test_repeatable_with_and_without_override_a_profile(self):
        self.install("--profile", "full-room", "--with", "controller",
                     "--without", "pacing", "--without", "codex-view")
        self.assert_pieces((FULL | {"controller"}) - {"pacing", "codex-view"})
        self.install("--profile", "core", "--with", "pacing", "--with", "known-fixes")
        self.assert_pieces({"pacing", "known-fixes"})

    def test_shared_code_is_available_for_one_component_without_the_other(self):
        self.install("--profile", "core", "--with", "codex-view")
        self.assert_pieces({"codex-view"})
        for tool in ("codex-now.py", "codex-fleet.py"):
            self.execute([str(self.skill / "bin" / tool)], "--help")
        self.install("--profile", "core", "--with", "pacing")
        self.assert_pieces({"pacing"})
        for tool in ("pace.py", "codex-pace-watch.sh"):
            self.execute([str(self.skill / "bin" / tool)], "--help")

    def test_status_is_read_only_on_empty_home(self):
        before = self.snapshot()
        result = self.install("--status")
        status = result.stdout.lower()
        for label in ("enabled", "disabled", "next"):
            self.assertIn(label, status)
        self.assertEqual(before, self.snapshot())
        self.assert_no_background_setup()

    def test_status_reports_a_selected_profile_without_changing_it(self):
        self.install("--profile", "pair", "--without", "notify")
        before = self.snapshot()
        result = self.install("--status")
        status = result.stdout.lower()
        for label in ("enabled", "disabled", "next", "pair", "pacing", "notify"):
            self.assertIn(label, status)
        self.assertEqual(before, self.snapshot())
        self.assert_pieces(PAIR - {"notify"})

    def test_upgrade_removes_deselected_pieces_and_hooks(self):
        self.install("--profile", "full-room", "--with", "controller")
        self.assert_pieces(FULL | {"controller"})
        self.install("--profile", "solo", "--without", "notify")
        self.assert_pieces({"command"})

    def test_full_room_uninstalls_to_empty_home_after_a_cli_status(self):
        self.install("--profile", "full-room")
        self.execute([str(self.launcher)], "status")
        self.uninstall()
        self.assertEqual(list(self.home.iterdir()), [])
        self.assert_no_background_setup()

    def test_cli_only_keeps_launcher_and_skips_skill_and_hooks(self):
        self.install("--cli-only")
        self.assertTrue(self.launcher.is_file())
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.hooks.exists())
        self.execute([str(self.launcher)], "handover", "--help")
        self.uninstall()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_profile_inputs_leave_home_intact(self):
        before = self.snapshot()
        for options in (("--profile", "invalid-profile"), ("--with", "invalid-piece"),
                        ("--without", "invalid-piece"), ("--with",)):
            with self.subTest(options=options):
                self.assertNotEqual(self.install(*options, check=False).returncode, 0)
                self.assertEqual(before, self.snapshot())
