"""The governor role label and durable handover, in synthetic stores only."""
import json
import time

from coms.cli import PRIMER
from coms.store import Store
from tests.test_switchover import Sandbox


class GovernorTest(Sandbox):
    def test_governor_enrollment_is_visible_on_board(self):
        self.enroll("governor-window", "governor")
        entry = next(t for t in Store(self.state, "governor-window").threads()
                     if t["holder"] == "governor-window")
        self.assertEqual(entry["role"], "governor")
        code, output, error = self.run_cli("status")
        self.assertEqual(code, 0, error)
        self.assertIn("governor-window on", output)
        self.assertIn("[governor]", output)
        code, output, error = self.run_cli("status", "--json")
        self.assertEqual(code, 0, error)
        board = json.loads(output)
        self.assertEqual(next(t for t in board["threads"] if t["holder"] == "governor-window")["role"], "governor")

    def test_governor_note_loads_and_successor_accepts(self):
        note = self.note("governor")
        self.assertEqual(json.loads(note.read_text())["role"], "governor")
        self.enroll("successor", "governor")
        self.as_holder("holding")
        code, _, error = self.run_cli("handover", "relay", str(note), "--to", "successor")
        self.assertEqual(code, 0, error)
        self.as_holder("successor")
        code, _, error = self.run_cli("handover", "accept", str(note), "--confirm-ready")
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(note.read_text())["phase"], "accepted")

    def test_primer_teaches_no_role_doctrine(self):
        for word in ("Governor", "Command", "Controller", "slot grant", "Dev, staging"):
            self.assertNotIn(word, PRIMER)

    def test_who_role_finds_latest_governor_without_changing_plain_who(self):
        now = time.time()
        for holder, role, stamp in (("command", "orchestrator", now),
                                    ("governor-old", "governor", now + 1),
                                    ("governor-new", "governor", now + 2)):
            self.as_holder(holder)
            Store(self.state, holder, clock=lambda: stamp).register(role=role)
        code, output, error = self.run_cli("who", "--role", "governor")
        self.assertEqual((code, output.strip(), error), (0, "governor-new", ""))
        code, output, error = self.run_cli("who")
        self.assertEqual((code, output.strip(), error), (0, "command", ""))
        code, output, error = self.run_cli("who", "--role", "orchestrator")
        self.assertEqual((code, output.strip(), error), (0, "command", ""))

    def test_who_missing_role_preserves_plain_missing_orchestrator(self):
        code, output, error = self.run_cli("who", "--role", "governor")
        self.assertEqual((code, output, error), (1, "", "coms: no governor registered\n"))
        code, output, error = self.run_cli("who")
        self.assertEqual((code, output, error), (1, "", "coms: no orchestrator registered\n"))

    def test_who_role_ignores_stale_and_retired_records(self):
        now = time.time()
        for holder, stamp in (("governor-active", now),
                              ("governor-stale", now - Store.STALE_THREAD_SECONDS - 1),
                              ("governor-retired", now + 1)):
            self.as_holder(holder)
            store = Store(self.state, holder, clock=lambda: stamp)
            store.register(role="governor")
            if holder == "governor-retired":
                path = store._thread_path()
                with open(path) as source:
                    entry = json.load(source)
                entry["retired"] = True
                with open(path, "w") as target:
                    json.dump(entry, target)
        self.as_holder("governor-active")
        code, output, error = self.run_cli("who", "--role", "governor")
        self.assertEqual((code, output.strip(), error), (0, "governor-active", ""))
