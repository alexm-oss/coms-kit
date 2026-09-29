import os
import re
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__"}

# Generic leak patterns, so nothing personal has to be named here: an absolute
# home path (macOS or Linux) or an email address in any shipped file.
HOME_PATH = re.compile(r"/(Users|home)/[A-Za-z0-9._-]+")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def kit_files():
    for dirpath, dirnames, filenames in os.walk(KIT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield os.path.join(dirpath, name)


class ScrubTest(unittest.TestCase):
    def test_no_home_paths_or_email_addresses(self):
        hits = []
        for path in kit_files():
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except UnicodeDecodeError:
                continue
            rel = os.path.relpath(path, KIT_ROOT)
            for pattern in (HOME_PATH, EMAIL):
                for match in pattern.finditer(text):
                    hits.append(f"{rel}: {match.group(0)}")
        self.assertEqual(hits, [], "possible personal data in the kit:\n" + "\n".join(hits))

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
