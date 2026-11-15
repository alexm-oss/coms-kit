"""Documented agent inbox reads must say whether they consume or only look.

Agent shells pipe their output, and piped or redirected output defaults to peek
(cmd_inbox in coms/inbox.py), so a bare `coms inbox` never marks mail read for
an agent: the mail stays unread, the Stop gate keeps refusing, and the agent
believes it consumed what it only looked at. Every documented agent loop must
therefore name --consume (mark delivered) or --peek (look only).
"""
import glob
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(REPO_ROOT, "skills", "coms")

EXPLICIT = {"--consume", "--peek"}
# History and recovery forms never depend on the output type, so they cannot
# hide a consume behind a pipe. Every other form must name one of EXPLICIT.
SELF_DESCRIBING = {"--all", "--since", "--reset-checkpoint"}

# "coms inbox" plus its arguments, up to the end of a code span, a shell
# operator, a comment, a comma or a bracket.
COMMAND = re.compile(r"coms inbox(?![\w-])([^`#>|;&,()\n]*)")
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")

# Bare reads the scanner must catch: none names --consume or --peek.
OLD_AGENT_LOOPS = (
    "Plain `coms inbox` advances delivery after successful output;",
    "until that window runs `coms inbox` again, which an idle window never does.",
    "When a line prints, run `coms inbox`, act, then wait again.",
    "1. `coms inbox` FIRST - you may have unread messages.",
    "COMS_HOLDER=my-window COMS_SESSION_ID=my-session coms inbox",
    "COMS_HOLDER=my-window COMS_SESSION_ID=my-session coms inbox --follow",
    "wait with coms inbox --follow (bounded, repeat)",
    "Redirected output defaults to peek, so `coms inbox | tail` cannot silently",
)

EXPLICIT_FORMS = (
    "run `coms inbox --consume`, act",
    "COMS_HOLDER=x COMS_SESSION_ID=y coms inbox --consume   # read (--follow to tail)",
    "coms inbox --consume --follow",
    "coms inbox --json --peek",
    "perl -e 'alarm shift; exec @ARGV' 900 env COMS_HOLDER=<id> COMS_SESSION_ID=<s> "
    "coms inbox --peek --follow --exit-after-first-batch",
    "`coms inbox --peek --receipt > /private/path/receipt.json`",
    "Use `coms inbox --all` for older history.",
    "`coms inbox --reset-checkpoint` only when replay is the intended recovery.",
)


def bare_inbox_reads(text):
    """Return (line number, line) for every `coms inbox` that names neither
    --consume nor --peek and is not a history or recovery form."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in COMMAND.finditer(line):
            flags = set(FLAG.findall(match.group(1)))
            if not flags & (EXPLICIT | SELF_DESCRIBING):
                found.append((number, line.strip()))
    return found


def scanned_files():
    """The skill, the tool README and runbook, and the text the tool prints."""
    paths = [
        os.path.join(REPO_ROOT, "README.md"),
        os.path.join(SKILL, "SKILL.md"),
    ]
    for pattern in (
        os.path.join(SKILL, "references", "*.md"),
        os.path.join(SKILL, "bin", "*.md"),
        os.path.join(REPO_ROOT, "hooks", "claude", "*"),
        os.path.join(REPO_ROOT, "hooks", "coms-hook"),
        os.path.join(REPO_ROOT, "coms", "*.py"),
    ):
        paths.extend(sorted(glob.glob(pattern)))
    return paths


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class SkillInboxFlagsTest(unittest.TestCase):
    def test_documented_agent_loops_name_consume_or_peek(self):
        problems = []
        for path in scanned_files():
            for number, line in bare_inbox_reads(read(path)):
                problems.append(f"{os.path.relpath(path, REPO_ROOT)}:{number}: {line}")
        if problems:
            self.fail(
                "bare `coms inbox` in a documented agent loop; piped output defaults to "
                "peek, so name --consume to consume or --peek to look:\n" + "\n".join(problems)
            )

    def test_scanner_rejects_the_old_bare_reads(self):
        for line in OLD_AGENT_LOOPS:
            with self.subTest(line=line):
                self.assertTrue(bare_inbox_reads(line), line)

    def test_scanner_accepts_the_explicit_forms(self):
        for line in EXPLICIT_FORMS:
            with self.subTest(line=line):
                self.assertEqual(bare_inbox_reads(line), [], line)

    def test_skill_teaches_both_explicit_forms(self):
        skill = read(os.path.join(SKILL, "SKILL.md"))
        self.assertIn("coms inbox --consume", skill)
        self.assertIn("coms inbox --peek", skill)
        self.assertIn("read-batch.sh", skill)

    def test_the_scan_covers_the_files_that_teach_agents(self):
        mentions = {
            os.path.relpath(path, REPO_ROOT): len(COMMAND.findall(read(path)))
            for path in scanned_files()
        }
        for needed in ("README.md", "skills/coms/SKILL.md",
                       "skills/coms/references/handover.md", "coms/cli.py"):
            self.assertGreater(mentions.get(needed, 0), 0, needed)


if __name__ == "__main__":
    unittest.main()
