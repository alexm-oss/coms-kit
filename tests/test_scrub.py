import os
import re
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__"}

# Generic leak patterns, so nothing personal has to be named here. They run over
# every file in the kit: code, docs, scripts and test fixtures alike.
HOME_PATH = re.compile(r"/(Users|home)/[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Reserved names (RFC 2606 and RFC 6761) can never reach a real mailbox.
RESERVED_EMAIL = re.compile(r"@(?:[A-Za-z0-9-]+\.)*(?:example\.(?:com|net|org)|example|test|invalid|localhost)$", re.I)
PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")
# Ids that end in an eight-digit date, and numbered rule ids (two or three capital
# letters, a dash and digits), belong in a private decision log, not in a shipped file.
DATED_ID = re.compile(r"\b[a-z][a-z0-9-]+-20\d{6}\b")
RULE_ID = re.compile(r"\b[A-Z]{2,3}-\d{2,4}\b")
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=]{40,}(?![A-Za-z0-9+/=_-])")
HEX_DIGEST = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# The licence names its author; nothing else does.
NAME_ALLOWED = {"LICENSE"}


def kit_files():
    for dirpath, dirnames, filenames in os.walk(KIT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


def texts():
    for path in kit_files():
        try:
            with open(path, encoding="utf-8") as fh:
                yield os.path.relpath(path, KIT_ROOT), fh.read()
        except UnicodeDecodeError:
            continue


class ScrubTest(unittest.TestCase):
    def test_no_home_paths_or_email_addresses(self):
        hits = []
        for rel, text in texts():
            for match in HOME_PATH.finditer(text):
                hits.append(f"{rel}: {match.group(0)}")
            for match in EMAIL.finditer(text):
                if not RESERVED_EMAIL.search(match.group(0)):
                    hits.append(f"{rel}: {match.group(0)}")
        self.assertEqual(hits, [], "possible personal data in the kit:\n" + "\n".join(hits))

    def test_no_phone_numbers_private_ids_or_long_tokens(self):
        hits = []
        for rel, text in texts():
            for pattern in (PHONE, DATED_ID, RULE_ID, LONG_TOKEN):
                for match in pattern.finditer(text):
                    if pattern is LONG_TOKEN and HEX_DIGEST.fullmatch(match.group(0)):
                        continue  # a git or sha256 digest in a fixture, not a secret
                    hits.append(f"{rel}: {match.group(0)}")
        self.assertEqual(hits, [], "possible private data in the kit:\n" + "\n".join(hits))

    def test_docs_speak_of_the_human_and_name_no_private_roles(self):
        pronoun = re.compile(r"\b(he|his|him)\b", re.I)
        roles = re.compile(r"\b(Command|Controller|Governor|Codex lead|seats?)\b")
        hits = []
        for rel, text in texts():
            if not rel.endswith(".md"):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pronoun.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()}")
                if rel != os.path.join("skills", "coms", "references", "operating-model.md") and roles.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual(hits, [])

    def test_no_real_looking_uuids(self):
        uuid = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
        hits = [f"{rel}: {m.group(0)}" for rel, text in texts() for m in uuid.finditer(text)
                if len(set(m.group(0).replace("-", ""))) > 4]
        self.assertEqual(hits, [])

    def test_the_author_is_named_only_in_the_licence_and_its_readme_line(self):
        with open(os.path.join(KIT_ROOT, "LICENSE"), encoding="utf-8") as fh:
            words = re.findall(r"Copyright \(c\) \d{4} (.+)", fh.read())
        self.assertEqual(len(words), 1)
        name = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(part) for part in words[0].split()), re.I)
        hits = []
        for rel, text in texts():
            if rel in NAME_ALLOWED:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if name.search(line) and not (rel == "README.md" and line.startswith("MIT licence, by ")):
                    hits.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual(hits, [])

    def test_every_file_is_plain_ascii(self):
        bad = []
        for path in kit_files():
            with open(path, "rb") as fh:
                if any(byte > 127 for byte in fh.read()):
                    bad.append(os.path.relpath(path, KIT_ROOT))
        self.assertEqual(bad, [])

    def test_no_bytecode_inside_skills(self):
        found = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(KIT_ROOT, "skills")):
            found += [os.path.join(dirpath, d) for d in dirnames if d == "__pycache__"]
            found += [os.path.join(dirpath, f) for f in filenames if f.endswith(".pyc")]
        self.assertEqual(found, [])
