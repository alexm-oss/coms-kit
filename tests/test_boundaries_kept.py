"""Lease, path and delivery boundaries hold in the edge cases."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from coms import owner_asks
from coms.inbox import Inbox
from coms.store import Store
from tests.test_switchover import Sandbox


class BoundariesKeptTest(Sandbox):
    def test_malformed_row_between_originals_does_not_hide_later_mail(self):
        self.send('before')
        inbox = Inbox(Store(self.state, 'leaving'))
        with open(inbox.path, 'a') as target:
            target.write('{broken\n')
        self.send('after')
        code, out, err = self.run_cli('inbox', '--consume', '--json')
        self.assertEqual(code, 0, err)
        self.assertEqual([row['text'] for row in json.loads(out)], ['before', 'after'])
        self.assertIn('skipping malformed inbox line', err)
        saved = json.loads(Path(inbox.checkpoint).read_text())
        self.assertEqual(saved['offset'], Path(inbox.path).stat().st_size)
        self.assertEqual(saved['lines'], 3)
        self.assertIn('{broken', Path(inbox.path).read_text())

    def test_malformed_only_batch_advances_and_new_mail_still_delivers(self):
        inbox = Inbox(Store(self.state, 'leaving'))
        Path(inbox.path).parent.mkdir(parents=True, exist_ok=True)
        Path(inbox.path).write_text('[]\n')
        self.assertEqual(self.run_cli('inbox', '--consume')[0], 0)
        self.assertEqual(json.loads(Path(inbox.checkpoint).read_text())['offset'], 3)
        self.send('new original')
        self.assertIn('new original', self.run_cli('inbox', '--consume')[1])

    def test_changed_prefix_resyncs_to_next_complete_line(self):
        self.send('first')
        inbox = Inbox(Store(self.state, 'leaving'))
        self.run_cli('inbox', '--consume')
        old = Path(inbox.path).read_bytes()
        Path(inbox.path).write_bytes(old.replace(b'first', b'other'))
        self.send('complete boundary original')
        self.send('later original')
        code, out, err = self.run_cli('inbox', '--consume')
        self.assertEqual(code, 0, err)
        self.assertIn('resyncing', err)
        self.assertIn('later original', out)
        self.assertIn('complete boundary original', out)
        saved = json.loads(Path(inbox.checkpoint).read_text())
        self.assertEqual(saved['prefix_hash'], hashlib.sha256(Path(inbox.path).read_bytes()).hexdigest())

    def test_stale_matching_key_token_does_not_take_over_a_live_claim(self):
        claim = Store(self.state, 'leaving').acquire('repo:outer')
        os.environ.update(COMS_LEASE_KEY='repo:outer', COMS_LEASE_TOKEN=str(claim.token + 1))
        code, _, err = self.run_cli('do', 'repo:inner', '--wait', '0', '--', 'true')
        self.assertEqual(code, 0, err)
        self.assertIn('cleared stale', err)
        self.assertNotIn('COMS_LEASE_KEY', os.environ)
        self.assertEqual(Store(self.state, 'observer').status()['resources']['repo:outer']['token'], claim.token)
        claim.release()

    def test_owner_ask_expiry_and_newer_ask_keep_reasking_available(self):
        base = self.home / 'asks'
        queue, decisions = base / 'owner-asks.jsonl', base / 'decisions.jsonl'
        def ask(at):
            return owner_asks.append_owner_ask(queue, decisions, holder_id='unit-window',
                    repo='unit', topic='unit-question', question='New conditions?', clock=lambda: at)
        self.assertTrue(ask(100)['created'])
        decision = dict(ts=110, by='unit-command', repo='unit', topic='unit-question',
                        answer='yes', quote='yes', expires_at=120)
        decisions.write_text(json.dumps(decision) + '\n')
        self.assertFalse(ask(115)['created'])
        self.assertTrue(ask(130)['created'])
        self.assertTrue(ask(140)['created'])
        with patch('coms.owner_asks.time.time', return_value=140):
            self.assertEqual(len(owner_asks.open_owner_asks(queue, decisions)), 3)
        for at in (150, 160):
            result = owner_asks.show_owner_ask(queue, decisions, topic='unit-question',
                        holder_id='unit-command', clock=lambda: at)
            self.assertEqual(result['status'], 'shown')
        raw = queue.read_bytes()
        owner_asks.init_owner_asks(queue, decisions)
        self.assertEqual(queue.read_bytes(), raw)

    def test_handover_optional_flags_do_not_pause_main_lease(self):
        note = self.note()
        self.as_holder('leaving')
        self.assertEqual(self.run_cli('do', 'repo:main', '--wait', '0', '--', 'true')[0], 0)
        self.as_holder('holding')
        self.enroll('successor')
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', str(note), '--to', 'successor')[0], 0)
        self.as_holder('successor')
        self.assertEqual(self.run_cli('handover', 'accept', str(note))[0], 0)
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'retire', str(note))[0], 0)

    def test_long_owner_question_warns_and_is_recorded_without_truncation(self):
        import contextlib
        import io
        queue, decisions = self.home / 'owner-asks.jsonl', self.home / 'decisions.jsonl'
        question = 'unit question ' * 80
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = owner_asks.append_owner_ask(queue, decisions, holder_id='unit-window',
                repo='unit', topic='unit-long', question=question, clock=lambda: 100)
        self.assertTrue(result['created'])
        self.assertIn('warning', errors.getvalue())
        self.assertEqual(result['row']['question'], question.strip())

    def test_handover_redacts_lane_decision_and_status_prose_without_leaking(self):
        from coms import handover
        payload = dict(lanes=[dict(enrollment=dict(intent='password=synthetic-lane'))],
                       decisions=[dict(answer='authorization: synthetic-decision')],
                       status=dict(intent='secret=synthetic-status'))
        sanitized = handover.safe_payload(payload)
        self.assertNotIn('synthetic-', json.dumps(sanitized))
        self.assertIn('REDACTED', json.dumps(sanitized))
        self.assertIn('synthetic-lane', json.dumps(payload))
        for unsafe in ({'password': 'synthetic-key'}, {'text': '-----BEGIN synthetic'}):
            with self.assertRaisesRegex(ValueError, 'credential'):
                handover.safe_payload(unsafe)

    def test_actual_note_redacts_lane_decision_and_lease_status_prose(self):
        from coms.decisions import DecisionLog
        self.enroll('holding', 'orchestrator')
        self.enroll('leaving')
        entry_path = Path(Store(self.state, 'leaving')._thread_path())
        entry = json.loads(entry_path.read_text())
        entry['intent'] = 'password=synthetic-lane-value'
        entry_path.write_text(json.dumps(entry))
        DecisionLog(self.state, 'leaving').record('unit-scope',
            'authorization: synthetic-decision-value', 'owner quote')
        Store(self.state, 'leaving').acquire('repo:main',
            intent='secret=synthetic-status-value', pid=987654321)
        code, out, err = self.run_cli('handover', 'note', '--to', 'holding',
            '--role', 'codex-lead', '--open-loops', str(self.loops), '--id', 'unit-redaction')
        self.assertEqual(code, 0, err)
        packet = Path(out.strip()).read_text()
        for value in ('synthetic-lane-value', 'synthetic-decision-value', 'synthetic-status-value'):
            self.assertNotIn(value, packet)
        self.assertGreaterEqual(packet.count('REDACTED'), 3)
        self.assertIn('synthetic-lane-value', entry_path.read_text())
