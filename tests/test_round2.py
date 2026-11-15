"""Original Claude review failures reproduced in isolated synthetic stores."""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from coms.identity import session_id
from coms.inbox import Inbox, atomic_json
from coms.store import Store
from tests.test_switchover import ROOT, Sandbox


class RoundTwoTest(Sandbox):
    def test_actual_claude_marker_and_explicit_override(self):
        os.environ.pop('COMS_SESSION_ID')
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'native-parent'
        self.assertEqual(session_id(), 'CLAUDE_CODE_SESSION_ID:native-parent')
        os.environ['COMS_SESSION_ID'] = 'own-window'
        self.assertEqual(session_id(), 'COMS_SESSION_ID:own-window')

    def test_printed_monitor_carries_marker(self):
        self.enroll('claude-synthetic')
        text = Store(self.state, 'claude-synthetic').monitor_command(None)
        self.assertIn('COMS_SESSION_ID=session-claude-synthetic', text)

    def test_unmarked_enroll_refuses(self):
        os.environ.pop('COMS_SESSION_ID')
        self.assertEqual(self.run_cli('enroll')[0], 75)

    def test_legacy_unbound_inbox_cannot_be_consumed_by_foreign_window(self):
        store = Store(self.state, 'leaving')
        atomic_json(store._thread_path(), {'holder': 'leaving', 'updated_at': time.time()})
        self.send('owner-original')
        os.environ['COMS_SESSION_ID'] = 'foreign'
        self.assertEqual(self.run_cli('inbox')[0], 75)
        self.assertFalse(pathlib.Path(Inbox(store).checkpoint).exists())
        self.assertIn('owner-original', self.run_cli('inbox', '--peek')[1])

    def test_malformed_enrollments_recover_only_explicit_enroll(self):
        self.enroll('leaving')
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        for original in ('{', '[]'):
            path.write_text(original)
            self.assertEqual(self.run_cli('inbox')[0], 75)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(self.run_cli('enroll', '--repair-enrollment')[0], 0)
            self.assertTrue(list(path.parent.glob('leaving.json.corrupt-*')))

    def test_large_backlog_note_and_receipt_are_bounded(self):
        path = pathlib.Path(Store(self.state, 'leaving')._inbox_path('leaving'))
        path.parent.mkdir(parents=True)
        with path.open('w') as output:
            for number in range(2500):
                output.write(json.dumps({'from': 'lane', 'ts': 10,
                                         'text': 'original-%d ' % number + 'x' * 1000}) + '\n')
        original = path.read_bytes()
        code, receipt, err = self.run_cli('inbox', '--peek', '--receipt')
        self.assertEqual(code, 0, err)
        self.assertLess(len(receipt.encode()), 100000)
        file = self.home / 'receipt.json'
        file.write_text(receipt)
        self.assertEqual(self.run_cli('checkpoint-ack', str(file), '--pending', str(self.loops))[0], 0)
        self.assertEqual(path.read_bytes(), original)
        with path.open('a') as output:
            output.write(json.dumps({'from': 'lane', 'ts': 10, 'text': 'late pending'}) + '\n')
        note = self.note()
        self.assertLess(note.stat().st_size, 100000)
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', '--replay', str(note), '--to', 'replacement')[0], 0)
        rows = Store(self.state, 'replacement').read_inbox()
        self.assertEqual(sum('late pending' in row['text'] for row in rows), 1)
        self.assertEqual(sum('original-' in row['text'] for row in rows), 0)

    def test_large_unhandled_history_note_uses_preserved_pointer(self):
        path = pathlib.Path(Store(self.state, 'leaving')._inbox_path('leaving'))
        path.parent.mkdir(parents=True)
        with path.open('w') as output:
            for number in range(2000):
                output.write(json.dumps({'from': 'lane', 'ts': 10, 'text': 'pending ' + 'x' * 1000}) + '\n')
        note = self.note()
        self.assertLess(note.stat().st_size, 100000)
        self.assertNotIn('x' * 1000, note.read_text())

    def test_cookie_prose_survives_and_credential_line_is_redacted(self):
        self.send('privacy rule: cookie: decline non-essential')
        self.send('password=synthetic-private-value')
        note = self.note()
        self.assertNotIn('synthetic-private-value', note.read_text())
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', '--replay', str(note), '--to', 'replacement')[0], 0)
        rows = json.dumps(Store(self.state, 'replacement').read_inbox())
        self.assertIn('cookie: decline non-essential', rows)
        self.assertNotIn('synthetic-private-value', rows)
        self.assertIn('REDACTED', rows)

    def test_base_digest_position_migrates_on_first_read(self):
        self.send('already delivered', 10)
        path = pathlib.Path(Store(self.state, 'leaving')._inbox_path('leaving'))
        offset = path.stat().st_size
        legacy = self.home / '.local/state/coms-orchestrator'
        legacy.mkdir(parents=True)
        (legacy / 'inbox-digest.state').write_text('%d 20\n' % offset)
        self.send('physical late with older clock', 9)
        self.assertEqual(self.run_cli('inbox', '--peek')[0], 0)
        code, preview, err = self.run_cli('migrate', 'preview')
        self.assertEqual(code, 0, err)
        saved = self.home / 'migration-preview.json'
        saved.write_text(preview)
        self.assertEqual(self.run_cli('migrate', 'apply', '--preview', str(saved), '--offset', str(offset))[0], 0)
        out = self.run_cli('inbox')[1]
        self.assertNotIn('already delivered', out)
        self.assertIn('physical late with older clock', out)
        self.assertIn('empty', self.run_cli('inbox')[1])

    def test_base_seen_requires_explicit_reconciliation_without_handled_inference(self):
        for n in range(3):
            self.send('shown-%d' % n)
        seen = pathlib.Path(self.state) / 'inbox-seen/leaving'
        seen.parent.mkdir()
        seen.write_text('3')
        self.send('after-seen')
        self.assertEqual(self.run_cli('inbox', '--peek')[0], 0)
        code, preview, err = self.run_cli('migrate', 'preview')
        self.assertEqual(code, 0, err)
        saved = self.home / 'migration-preview.json'
        saved.write_text(preview)
        offset = sum(len(line) for line in pathlib.Path(Store(self.state, 'leaving')._inbox_path('leaving')).read_bytes().splitlines(keepends=True)[:3])
        self.assertEqual(self.run_cli('migrate', 'apply', '--preview', str(saved), '--offset', str(offset))[0], 0)
        out = self.run_cli('inbox')[1]
        self.assertNotIn('shown-', out)
        self.assertIn('after-seen', out)
        self.assertFalse((pathlib.Path(self.state) / 'handled/leaving.json').exists())

    def test_since_follow_cannot_skip_physical_unread(self):
        self.send('older unread', 1)
        with patch('coms.inbox.time.sleep', side_effect=KeyboardInterrupt):
            self.run_cli('inbox', '--follow', '--since', '100')
        self.assertIn('older unread', self.run_cli('inbox')[1])

    def test_peek_follow_does_not_repeat_same_record(self):
        self.send('single-record')
        with patch('coms.inbox.time.sleep', side_effect=[None, None, KeyboardInterrupt]):
            out = self.run_cli('inbox', '--peek', '--follow')[1]
        self.assertEqual(out.count('single-record'), 1)
        self.assertIn('single-record', self.run_cli('inbox')[1])

    def test_handover_cancel_reopens_only_leaving_holder(self):
        note = self.note()
        self.as_holder('leaving')
        self.assertEqual(self.run_cli('handover', 'cancel', str(note))[0], 0)
        self.assertEqual(self.run_cli('do', 'repo:main', '--wait', '0', '--', 'true')[0], 0)
        self.assertEqual(json.loads(note.read_text())['phase'], 'cancelled')

    def test_nested_same_key_refuses_immediately_with_clear_message(self):
        claim = Store(self.state, 'leaving').acquire('repo:main')
        os.environ.update(COMS_LEASE_KEY='repo:main', COMS_LEASE_TOKEN=str(claim.token))
        started = time.monotonic()
        code, _, err = self.run_cli('do', 'repo:main', '--wait', '.5', '--', 'true')
        self.assertEqual(code, 75)
        self.assertLess(time.monotonic() - started, .2)
        self.assertIn('nested', err)

    def test_signal_cleanup_releases_lease_and_stops_owned_child(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                ready, late = self.home / ('ready%d' % sig), self.home / ('late%d' % sig)
                script = ('import pathlib,sys,time,signal;'
                          'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                          'pathlib.Path(sys.argv[1]).write_text("ready");'
                          'time.sleep(4);pathlib.Path(sys.argv[2]).write_text("orphan")')
                process = subprocess.Popen([sys.executable, '-m', 'coms.cli', 'do', 'repo:main',
                            '--wait', '0', '--', sys.executable, '-c', script, str(ready), str(late)],
                            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                child_pid = None
                try:
                    deadline = time.monotonic() + 2
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(ready.exists())
                    # Only enumerate descendants of this exact wrapper for cleanup.
                    children = subprocess.check_output(['pgrep', '-P', str(process.pid)], text=True)
                    child_pid = int(children.splitlines()[0])
                    os.kill(process.pid, sig)
                    process.wait(timeout=5)
                    self.assertNotIn('repo:main', Store(self.state, 'observer').status()['resources'])
                    time.sleep(1.1)
                    self.assertFalse(late.exists())
                finally:
                    if child_pid:
                        try:
                            os.killpg(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_directory_fsync_after_atomic_replace(self):
        with patch('coms.inbox.os.fsync', wraps=os.fsync) as sync:
            atomic_json(str(self.home / 'private/state.json'), {'test': 1})
        self.assertGreaterEqual(sync.call_count, 2)

    def test_settle_interrupt_never_consumes_undelivered_mail(self):
        launcher = self.home / 'bin/coms'
        launcher.parent.mkdir()
        launcher.write_text('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        launcher.chmod(0o700)
        self.send('QUESTION settle-original')
        output = self.home / 'settle.out'
        env = dict(self.env, PATH=str(launcher.parent) + os.pathsep + self.env['PATH'],
                   WATCH_OUT=str(output), WATCH_SETTLE_S='60', WATCH_MAX_S='2',
                   COMS_WATCH_DIR=str(self.home / 'watch'), INBOX_POLL_S='.1')
        process = subprocess.Popen([str(ROOT / 'skills/coms/bin/watch-once.sh')], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True)
        try:
            deadline = time.monotonic() + 2
            while (not output.exists() or not output.stat().st_size) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(output.exists() and output.stat().st_size)
            time.sleep(.15)
            process.terminate()
            out, _ = process.communicate(timeout=5)
            remaining = self.run_cli('inbox')[1]
            self.assertTrue('settle-original' in out.decode() or 'settle-original' in remaining)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=3)

    def test_relay_metadata_not_rewritten_per_original(self):
        for number in range(10):
            self.send('pending-%d' % number)
        note = self.note()
        self.as_holder('holding')
        from coms.handover import save_packet
        with patch('coms.handover.save_packet', wraps=save_packet) as save:
            self.assertEqual(self.run_cli('handover', 'relay', str(note), '--to', 'replacement')[0], 0)
        self.assertLess(save.call_count, 4)

    def test_docs_use_generic_holder_and_full_history(self):
        skill = (ROOT / 'skills/coms/SKILL.md').read_text()
        self.assertNotIn('codex-lead-2026', skill)
        hook = (ROOT / 'hooks/claude/coms-context').read_text()
        self.assertIn('hook-notifications context', hook)
        helper = (ROOT / 'coms/notifications.py').read_text()
        self.assertIn('Full history: ', helper)
        self.assertIn(' inbox --all', helper)
        readme = (ROOT / 'skills/coms/bin/README.md').read_text()
        self.assertIn('COMS_SESSION_ID=', readme.split('watch-once.sh')[0])
