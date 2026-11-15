"""Receipt growth and explicit recovery of delivered transition batches."""
import hashlib
import json
import pathlib
import sqlite3
from unittest.mock import patch

from coms.handover import MAX_PACKET
from coms.inbox import Inbox
from coms.store import Store
from tests.test_switchover import Sandbox


class RelaySidecarTest(Sandbox):
    def test_near_limit_packet_relay_does_not_send_then_fail(self):
        self.send("original")
        note = self.note()
        packet = json.loads(note.read_text())
        packet["padding"] = ""
        packet["padding"] = "x" * (MAX_PACKET - len(json.dumps(packet, indent=2).encode()) - 512)
        note.write_text(json.dumps(packet, indent=2))
        self.as_holder("holding")
        code, out, err = self.run_cli("handover", "relay", str(note))
        self.assertEqual(code, 0, (out, err))
        self.assertIn("handover relayed:", out)
        self.assertEqual(self.run_cli("handover", "relay", str(note))[1].strip(), "handover relayed: 0")
        self.assertLess(note.stat().st_size, MAX_PACKET)

    def test_sidecar_migrates_legacy_receipts_and_position(self):
        self.send("original")
        note = self.note()
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "relay", str(note))[0], 0)
        packet = json.loads(note.read_text())
        self.assertNotIn("relays", packet)
        self.assertNotIn("relay_positions", packet)
        database = note.with_suffix(".relays.sqlite3")
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(database) as db:
            self.assertGreater(db.execute("select count(*) from receipts").fetchone()[0], 0)
        Store(self.state, "sender").send_message("leaving", "late original")
        self.assertIn("handover relayed: 1", self.run_cli("handover", "relay", str(note))[1])
        self.assertIn("handover relayed: 0", self.run_cli("handover", "relay", str(note))[1])

    def test_receipts_survive_failure_after_first_successful_send(self):
        self.send("one")
        self.send("two")
        note = self.note()
        self.as_holder("holding")
        send = Store.send_message
        attempts = []
        def interrupted(store, target, text):
            attempts.append(text)
            if len(attempts) == 2:
                raise OSError("synthetic transport failure before second append")
            return send(store, target, text)
        with patch.object(Store, "send_message", interrupted):
            code, _, error = self.run_cli("handover", "relay", "--replay", str(note))
            self.assertEqual(code, 2)
            self.assertIn("synthetic transport failure", error)
        self.assertEqual(self.run_cli("handover", "relay", "--replay", str(note))[0], 0)
        received = Store(self.state, "holding").read_inbox()
        originals = [json.loads(r['text'][18:])['text'] for r in received if r['text'].startswith('HANDOVER ORIGINAL ')]
        self.assertEqual(originals.count('one'), 1)
        self.assertEqual(originals.count('two'), 1)

    def delivered_receipt(self, note):
        packet = json.loads(note.read_text())
        self.enroll("successor")
        self.as_holder("holding")
        old = Inbox(Store(self.state, "leaving"))
        rows, checkpoint = old.snapshot(reset=True)
        messages = rows + [{"from": packet["leaving"], "ts": packet["created_at"],
                            "text": "HANDOVER OPEN LOOPS " + json.dumps(packet["open_loops"])}]
        for msg in messages:
            Store(self.state, "holding").send_message("successor", "HANDOVER ORIGINAL " + json.dumps(msg))
        keys = [hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest() for m in messages[len(rows):]]
        receipt = self.home / "delivered.json"
        receipt.write_text(json.dumps({"note_id": packet["id"], "destination": "successor",
            "source_end": checkpoint["offset"], "source_rows": len(rows),
            "received_total": len(messages), "metadata_keys": keys}))
        return receipt

    def test_record_delivered_advances_only_attested_prefix_without_sending(self):
        self.send("already delivered")
        note = self.note()
        receipt = self.delivered_receipt(note)
        Store(self.state, "sender").send_message("leaving", "later")
        target = pathlib.Path(Store(self.state, "successor")._inbox_path("successor"))
        original = target.read_bytes()
        code, _, err = self.run_cli("handover", "record-delivered", str(note), "--to", "successor", "--receipt", str(receipt))
        self.assertEqual(code, 0, err)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.run_cli("handover", "record-delivered", str(note), "--to", "successor", "--receipt", str(receipt))[0], 0)
        self.assertIn("handover relayed: 1", self.run_cli("handover", "relay", "--replay", str(note), "--to", "successor")[1])
        self.assertIn("handover relayed: 0", self.run_cli("handover", "relay", "--replay", str(note), "--to", "successor")[1])
        self.as_holder("successor")
        self.assertEqual(self.run_cli("handover", "accept", str(note))[0], 0)
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "retire", str(note))[0], 0)

    def test_record_delivered_rejects_wrong_destination_or_incomplete_prefix(self):
        self.send("original")
        note = self.note()
        receipt = self.delivered_receipt(note)
        data = json.loads(receipt.read_text())
        for change in ({"destination": "other"}, {"source_end": data["source_end"] - 1},
                       {"source_rows": 100}, {"metadata_keys": ["invalid"]}, {"received_total": 0}):
            receipt.write_text(json.dumps(dict(data, **change)))
            code, _, _ = self.run_cli("handover", "record-delivered", str(note), "--to", "successor", "--receipt", str(receipt))
            self.assertEqual(code, 2, change)

    def test_sidecar_symlink_refuses_before_new_delivery(self):
        self.send("original")
        note = self.note()
        target = self.home / "foreign"
        target.write_text("keep")
        note.with_suffix(".relays.sqlite3").symlink_to(target)
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "relay", str(note))[0], 2)
        self.assertEqual(target.read_text(), "keep")

    def test_existing_packet_receipts_migrate_without_losing_source_position(self):
        self.send("already handled")
        note = self.note()
        packet = json.loads(note.read_text())
        packet['relays'] = {'holding': ['a' * 64]}
        packet['relay_positions'] = {'holding': packet['inbox_checkpoint']}
        note.write_text(json.dumps(packet, indent=2))
        self.as_holder('holding')
        self.assertIn('handover relayed: 1', self.run_cli('handover', 'relay', '--replay', str(note))[1])
        received = Store(self.state, 'holding').read_inbox()
        originals = [json.loads(r['text'][18:])['text'] for r in received if r['text'].startswith('HANDOVER ORIGINAL ')]
        self.assertNotIn('already handled', originals)
        with sqlite3.connect(note.with_suffix('.relays.sqlite3')) as db:
            self.assertIsNotNone(db.execute('select 1 from receipts where receipt=?', ('a' * 64,)).fetchone())
        self.assertNotIn('relay_positions', json.loads(note.read_text()))

    def test_identical_physical_originals_remain_distinct(self):
        self.send('identical', stamp=10)
        self.send('identical', stamp=10)
        note = self.note()
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', '--replay', str(note))[0], 0)
        received = Store(self.state, 'holding').read_inbox()
        originals = [json.loads(r['text'][18:])['text'] for r in received if r['text'].startswith('HANDOVER ORIGINAL ')]
        self.assertEqual(originals.count('identical'), 2)
        self.assertIn('handover relayed: 0', self.run_cli('handover', 'relay', '--replay', str(note))[1])


class NoReplayRuleTest(Sandbox):
    """No-replay rule: a relay sends one pointer, never the history as mail."""

    def originals(self, holder):
        return [r for r in Store(self.state, holder).read_inbox() if r['text'].startswith('HANDOVER ORIGINAL ')]

    def test_default_relay_sends_one_pointer_not_one_card_per_original(self):
        for n in range(50):
            self.send('history %d' % n)
        note = self.note()
        self.as_holder('holding')
        code, out, err = self.run_cli('handover', 'relay', str(note))
        self.assertEqual(code, 0, err)
        received = Store(self.state, 'holding').read_inbox()
        self.assertEqual(self.originals('holding'), [])
        self.assertLessEqual(len(received), 4)
        pointer = [json.loads(r['text'][len('HANDOVER POINTER '):]) for r in received
                   if r['text'].startswith('HANDOVER POINTER ')]
        self.assertEqual(len(pointer), 1)
        self.assertEqual(pointer[0]['messages'], 50)
        raw = pathlib.Path(pointer[0]['path']).read_bytes()[pointer[0]['byte_start']:pointer[0]['byte_end']]
        self.assertEqual(hashlib.sha256(raw).hexdigest(), pointer[0]['sha256'])
        self.assertEqual(raw.count(b'\n'), 50)
        self.assertIn('handover relayed: 0', self.run_cli('handover', 'relay', str(note))[1])

    def test_late_mail_gets_one_new_pointer(self):
        self.send('before')
        note = self.note()
        self.as_holder('holding')
        self.run_cli('handover', 'relay', str(note))
        Store(self.state, 'sender').send_message('leaving', 'late one')
        Store(self.state, 'sender').send_message('leaving', 'late two')
        self.assertIn('handover relayed: 1', self.run_cli('handover', 'relay', str(note))[1])
        pointers = [json.loads(r['text'][len('HANDOVER POINTER '):]) for r in Store(self.state, 'holding').read_inbox()
                    if r['text'].startswith('HANDOVER POINTER ')]
        self.assertEqual([p['messages'] for p in pointers], [1, 2])
        self.assertEqual(pointers[1]['byte_start'], pointers[0]['byte_end'])

    def test_decisions_are_named_not_replayed(self):
        self.send('x')
        code, _, err = self.run_cli('decide', 'rotation-test', 'yes', '--quote', 'go ahead')
        self.assertEqual(code, 0, err)
        note = self.note()
        self.as_holder('holding')
        self.run_cli('handover', 'relay', str(note))
        texts = [r['text'] for r in Store(self.state, 'holding').read_inbox()]
        self.assertFalse([t for t in texts if t.startswith('DECISION ')])
        self.assertTrue([t for t in texts if 'coms decisions --current' in t])
