import json
import os
import re
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("COMS_TEST_BASH", "bash")
SOLO = {"command", "notify"}
PAIR = SOLO | {"codex-lead", "codex-view", "handover-guide", "load-watch", "pacing"}
FULL = PAIR | {"governor", "helper-brief", "job-health", "keeper", "known-fixes", "current-rules", "security"}
GUIDES = {"command.md", "controller.md", "codex-lead.md", "governor.md", "keeper.md", "security-reviewer.md"}


def named_files(cell):
    """Recognize inline filenames with or without a relative directory."""
    names = set()
    for span in re.findall(r"`([^`]+)`", cell):
        names.update(Path(word).name for word in span.split())
    return names


def meaningful_context(cell):
    text = re.sub(r"`[^`]+`", "", cell)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    return len(re.findall(r"[a-zA-Z]+", text)) >= 4


def contextual_references(source):
    """Read when-to-use tables and standalone contextual instructions."""
    mentioned = set()
    when_column = None
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            cells = [part.strip() for part in line.strip("|").split("|")]
            header = next((i for i, part in enumerate(cells) if re.match(r"when\b", part, re.IGNORECASE)), None)
            if header is not None and not named_files(line):
                when_column = header
            elif when_column is not None and len(cells) > when_column and meaningful_context(cells[when_column]):
                mentioned.update(named_files(line))
            continue
        when_column = None
        if (re.search(r"\b(?:when|use|run|copy|read|open|check|schedule|imports?|depends on)\b", line, re.IGNORECASE)
                and meaningful_context(line)):
            mentioned.update(named_files(line))
    return mentioned


class OptionalDiscoverabilityTest(unittest.TestCase):
    def test_every_bin_file_and_role_guide_has_contextual_documentation(self):
        skill = ROOT / "skills" / "coms"
        scripts = {p.name for p in (skill / "bin").rglob("*")
                   if p.is_file() and p.name != "README.md" and p.suffix != ".pyc"}
        guides = {p.name for p in (skill / "references").glob("*.md")}
        self.assertLessEqual(GUIDES, guides)
        referenced = set()
        for path in [skill / "SKILL.md", *(skill / "references").glob("*.md")]:
            referenced.update(contextual_references(path.read_text(encoding="utf-8")))
        self.assertEqual((scripts | guides) - referenced, set(),
                         "Add a use case to SKILL.md or a reference guide for each unreferenced file.")


class SetupSuggestionTest(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory(prefix="coms-suggestions-")
        self.addCleanup(sandbox.cleanup)
        self.scratch = Path(sandbox.name).resolve()
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.launcher = self.home / ".local" / "bin" / "coms"
        self.skill = self.home / ".claude" / "skills" / "coms"
        self.repo = self.scratch / "repo"
        self.repo.mkdir()
        self.env = {
            "HOME": str(self.home), "PATH": str(self.launcher.parent) + os.pathsep + os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "COMS_HOLDER": "suggestion-fixture", "COMS_SESSION_ID": "fixture-session-one",
        }

    def execute(self, program, *args, env=None, check=True):
        result = subprocess.run([*program, *args], cwd=self.repo,
                                env=dict(self.env, **(env or {})), capture_output=True,
                                text=True, timeout=20, check=False)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def install(self, *args, **kwargs):
        return self.execute([BASH, str(ROOT / "install.sh")], *args, **kwargs)

    def coms(self, *args, **kwargs):
        return self.execute([str(self.launcher)], *args, **kwargs)

    def snapshot(self):
        entries = {}
        for path in [self.home, *self.home.rglob("*")]:
            info = path.lstat()
            if path.is_symlink():
                value = os.readlink(path)
            elif path.is_file():
                value = path.read_bytes()
            else:
                value = None
            entries[path.relative_to(self.home)] = (info.st_mode, info.st_mtime_ns, info.st_ctime_ns, value)
        return entries

    @staticmethod
    def status_fields(result):
        fields = {}
        for line in result.stdout.splitlines():
            label, sep, value = line.partition(":")
            if sep:
                fields[label.casefold().strip()] = value.strip()
        return fields

    def status(self, *opts):
        return self.status_fields(self.install("--status", *opts))

    def assert_pieces(self, field, expected):
        self.assertEqual(set() if field.casefold() in ("none", "(none)", "") else {
            name.strip() for name in field.split(",")
        }, expected)

    def suggestions(self, result, add_command):
        pieces = []
        for line in result.stdout.splitlines():
            if not line.startswith("- "):
                continue
            match = re.fullmatch(r"- ([a-z][a-z-]*): (\S.*)\s+Add: (.+)", line)
            self.assertIsNotNone(match, line)
            piece, benefit, command = match.groups()
            self.assertTrue(meaningful_context(benefit), line)
            self.assertEqual(command.strip(), f"{add_command} --add {piece}")
            self.assertNotIn(piece, pieces)
            pieces.append(piece)
        return pieces

    def test_installer_recommends_only_the_next_profiles_missing_components(self):
        matrix = (
            ("core", set(), "solo", SOLO),
            ("solo", SOLO, "pair", PAIR - SOLO),
            ("pair", PAIR, "full-room", FULL - PAIR),
            ("full-room", FULL, "none", set()),
        )
        for profile, enabled, next_profile, suggested in matrix:
            with self.subTest(profile=profile):
                self.install("--profile", profile)
                before = self.snapshot()
                result = self.install("--status")
                fields = self.status_fields(result)
                self.assertEqual(fields["installed profile"], profile)
                self.assert_pieces(fields["enabled"], enabled)
                self.assertEqual(fields["recommended next"].split()[0].rstrip(":").lower(), next_profile)
                self.assertEqual(set(self.suggestions(result, "./install.sh")), suggested)
                self.assertEqual(before, self.snapshot())

    def test_full_room_recommends_any_explicitly_omitted_default_without_controller(self):
        self.install("--profile", "full-room", "--without", "known-fixes", "--without", "notify")
        result = self.install("--status")
        self.assertEqual(set(self.suggestions(result, "./install.sh")), {"known-fixes", "notify"})
        self.assert_pieces(self.status_fields(result)["enabled"], FULL - {"known-fixes", "notify"})

    def test_explicit_target_queries_are_read_only_and_change_only_suggestions(self):
        self.install("--profile", "core", "--with", "load-watch", "--with", "keeper")
        before = self.snapshot()
        for command, add_command in (
            (self.install, "./install.sh"),
            (lambda *args: self.coms("setup", *args), "coms setup"),
        ):
            with self.subTest(add_command=add_command):
                result = command("--status", "--profile", "pair")
                fields = self.status_fields(result)
                self.assertEqual(fields["installed profile"], "core")
                self.assert_pieces(fields["enabled"], {"load-watch", "keeper"})
                self.assertEqual(set(self.suggestions(result, add_command)), PAIR - {"load-watch"})
                self.assertEqual(before, self.snapshot())

    def test_status_before_any_install_is_read_only(self):
        before = self.snapshot()
        result = self.install("--status", "--profile", "pair")
        self.assertEqual(set(self.suggestions(result, "./install.sh")), PAIR)
        self.assertEqual(before, self.snapshot())

    def test_setup_add_is_additive_across_a_custom_profile_and_preserves_declared_omissions(self):
        self.install("--profile", "solo", "--without", "notify", "--with", "keeper")
        expected = {"command", "keeper"}
        self.assert_pieces(self.status()["enabled"], expected)
        for piece, path in (
            ("pacing", "bin/pace.py"),
            ("codex-view", "bin/codex-fleet.py"),
            ("handover-guide", "references/handover.md"),
        ):
            with self.subTest(piece=piece):
                self.coms("setup", "--add", piece)
                expected.add(piece)
                fields = self.status_fields(self.coms("setup", "--status"))
                self.assertEqual(fields["installed profile"], "solo")
                self.assert_pieces(fields["enabled"], expected)
                self.assertTrue((self.skill / path).is_file())
                self.assertFalse((self.skill / "bin" / "notify").exists())
        handover = self.skill / "references" / "handover.md"
        self.assertIn("New Claude window checklist", handover.read_text(encoding="utf-8"))

    def test_direct_add_and_invalid_add_share_installed_selections(self):
        self.install("--profile", "core", "--with", "keeper")
        self.install("--add", "command")
        self.assert_pieces(self.status_fields(self.coms("setup", "--status"))["enabled"], {"command", "keeper"})
        before = self.snapshot()
        for result in (
            self.coms("setup", "--add", "unknown-selection", check=False),
            self.install("--add", "unknown-selection", check=False),
        ):
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.snapshot())

    def test_parallel_additions_to_the_same_install_both_survive(self):
        self.install("--profile", "solo", "--without", "notify")
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(self.coms, "setup", "--add", "pacing"),
                pool.submit(self.install, "--add", "codex-view"),
            ]
            for job in jobs:
                job.result()
        fields = self.status_fields(self.coms("setup", "--status"))
        self.assertEqual(fields["installed profile"], "solo")
        self.assert_pieces(fields["enabled"], {"command", "pacing", "codex-view"})
        self.assertTrue((self.skill / "bin" / "pace.py").is_file())
        self.assertTrue((self.skill / "bin" / "codex-fleet.py").is_file())
        self.assertFalse((self.skill / "bin" / "notify").exists())

    def test_installed_setup_remembers_alternate_paths_without_overrides(self):
        root = self.home / "kit paths"
        paths = {
            "COMS_BIN_DIR": str(root / "bin"),
            "COMS_SKILLS_DIR": str(root / "skills"),
            "COMS_HOOKS_DIR": str(root / "hooks"),
            "COMS_STATE_DIR": str(root / "state"),
        }
        self.install("--profile", "solo", "--without", "notify", env=paths)
        alternate = str(root / "bin" / "coms")
        before = self.snapshot()
        result = self.execute([alternate], "setup", "--status", "--profile", "pair")
        self.assertEqual(set(self.suggestions(result, "coms setup")), PAIR - {"command"})
        self.assertEqual(before, self.snapshot())
        self.execute([alternate], "setup", "--add", "pacing")
        fields = self.status_fields(self.execute([alternate], "setup", "--status"))
        self.assert_pieces(fields["enabled"], {"command", "pacing"})
        self.assertTrue((root / "skills" / "coms" / "bin" / "pace.py").is_file())
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.skill.exists())

    def presented_pieces(self, holder, session):
        identity = {"COMS_HOLDER": holder, "COMS_SESSION_ID": session}
        result = self.coms("setup", "--status", "--profile", "pair", env=identity)
        candidates = self.suggestions(result, "coms setup")
        prefix = f"setup-{holder}-"
        rulings = json.loads(self.coms("decisions", "--current", "--json", prefix, env=identity).stdout)
        declined = {row["topic"] for row in rulings if row["answer"] == "declined"}
        return [piece for piece in candidates if prefix + piece not in declined]

    def test_fresh_invocation_decline_suppresses_one_piece_for_one_window(self):
        self.install()
        self.execute(["git", "init", "-q", "-b", "main"])
        self.coms("enroll", "--intent", "synthetic onboarding")
        self.coms("status")
        self.coms("guard")
        holder, session = self.env["COMS_HOLDER"], self.env["COMS_SESSION_ID"]
        self.assertEqual(set(self.presented_pieces(holder, session)), PAIR)
        self.coms("setup", "--add", "command")
        self.assertEqual(set(self.presented_pieces(holder, session)), PAIR - {"command"})

        topic = f"setup-{holder}-notify"
        self.coms("decide", topic, "declined", "--quote", "Skip this notification option for this window.")
        decisions = self.home / ".claude" / "coms" / "decisions.jsonl"
        before = decisions.read_bytes()
        self.assertEqual(set(self.presented_pieces(holder, session)), PAIR - {"command", "notify"})
        self.assertEqual(set(self.presented_pieces(holder, session)), PAIR - {"command", "notify"})
        self.assertEqual(before, decisions.read_bytes())
        self.assertFalse((self.skill / "bin" / "notify").exists())

        next_identity = {"COMS_HOLDER": "suggestion-other", "COMS_SESSION_ID": "fixture-session-two"}
        self.coms("enroll", "--intent", "another synthetic window", env=next_identity)
        later = self.presented_pieces(next_identity["COMS_HOLDER"], next_identity["COMS_SESSION_ID"])
        self.assertEqual(set(later), PAIR - {"command"})

        skill = (self.skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("coms setup --status --profile pair", skill)
        self.assertIn("setup-$COMS_HOLDER-", skill)
