"""Day-one migration, notification and platform boundaries."""
import json
import os
import pathlib
import subprocess
import sys

from coms.inbox import Inbox
from coms.store import Store
from tests.test_switchover import ROOT, Sandbox


class DayOneTest(Sandbox):
    def launcher(self):
        binary = self.home / 'bin/coms'
        binary.parent.mkdir(exist_ok=True)
        binary.write_text('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        binary.chmod(0o700)
        self.env['PATH'] = str(binary.parent) + os.pathsep + self.env['PATH']

    def process(self, *args, timeout=3):
        return subprocess.run([sys.executable, '-m', 'coms.cli', *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def hooks(self, bound=True):
        self.launcher()
        sid = 'abcd1234-test-session'
        holder = 'claude-abcd1234'
        self.env.update(COMS_HOLDER=holder, COMS_SESSION_ID=sid)
        os.environ.update(COMS_HOLDER=holder, COMS_SESSION_ID=sid)
        if bound:
            Store(self.state, holder).register()
        repo = self.home / 'guarded'
        subprocess.run(['git', 'init', '-q', str(repo)], env=self.env, check=True, capture_output=True)
        (repo / '.git/coms-enabled').write_text('coms guard marker v1\n')
        return json.dumps({'cwd': str(repo), 'session_id': sid})

    def hook(self, name, payload):
        return subprocess.run(['/bin/bash', str(ROOT / 'hooks' / 'claude' / name)], env=self.env,
                              input=payload, capture_output=True, text=True, timeout=3)

    def test_bash32_digest_empty_array(self):
        self.launcher()
        self.send('QUESTION bash32-original')
        run = subprocess.run(['/bin/bash', str(ROOT / 'skills/coms/bin/inbox-digest.sh'), '--once'],
                             env=self.env, capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('bash32-original', run.stdout)
        self.assertIn('empty', self.run_cli('inbox')[1])

    def test_unenrolled_guarded_window_still_gets_reminder(self):
        payload = self.hooks(bound=False)
        self.send('PRIVATE unbound-original')
        run = self.hook('coms-context', payload)
        self.assertIn('[coms] This repo', run.stdout)
        self.assertNotIn('unbound-original', run.stdout + run.stderr)
        self.assertFalse((pathlib.Path(self.state) / 'notification-seen').exists())

    def test_notification_starts_after_validated_delivery(self):
        payload = self.hooks()
        self.send('already-delivered')
        self.assertEqual(self.run_cli('inbox')[0], 0)
        self.send('QUESTION new-original')
        run = self.hook('coms-context', payload)
        self.assertNotIn('already-delivered', run.stdout)
        self.assertIn('new-original', run.stdout)

    def test_stop_is_bounded_without_consuming_unshown_mail(self):
        payload = self.hooks()
        for i in range(15):
            self.send('QUESTION original-' + str(i))
        first = self.hook('coms-inbox-stop-gate.sh', payload)
        second = self.hook('coms-inbox-stop-gate.sh', payload)
        self.assertEqual(first.returncode, 2)
        self.assertEqual(second.returncode, 0)
        self.assertIn('original-5', self.hook('coms-context', payload).stdout)
        self.send('QUESTION later-arrival')
        self.assertEqual(self.hook('coms-inbox-stop-gate.sh', payload).returncode, 2)
        self.assertEqual(len(json.loads(self.run_cli('inbox', '--peek', '--json')[1])), 15 + 1)

    def adopt(self):
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        entry = json.loads(path.read_text())
        entry['session'] = None
        path.write_text(json.dumps(entry))
        self.assertEqual(self.run_cli('enroll', '--adopt-legacy')[0], 0)

    def test_adoption_without_marks_allows_delivery_after_reenroll(self):
        self.send('old-original')
        self.adopt()
        self.assertEqual(self.run_cli('enroll')[0], 0)
        self.assertIn('old-original', self.run_cli('inbox')[1])
        entry = json.loads(pathlib.Path(Store(self.state, 'leaving')._thread_path()).read_text())
        self.assertNotIn('migration_pending', entry)

    def test_adoption_keeps_existing_checkpoint_and_ack_available(self):
        self.send('old-original')
        receipt = self.home / 'receipt.json'
        receipt.write_text(self.run_cli('inbox', '--peek', '--receipt')[1])
        self.run_cli('inbox')
        self.adopt()
        self.assertEqual(self.run_cli('inbox')[0], 0)
        self.assertEqual(self.run_cli('checkpoint-ack', str(receipt), '--pending', str(self.loops))[0], 0)

    def test_seen_suggestion_contains_complete_byte_boundary(self):
        self.send('first-original')
        self.send('second-original')
        mark = pathlib.Path(self.state) / 'inbox-seen/leaving'
        mark.parent.mkdir()
        mark.write_text('1')
        saved = json.loads(self.run_cli('migrate', 'preview')[1])
        choice = next(row for row in saved['suggested_offsets'] if row['kind'] == 'seen')
        source = pathlib.Path(Inbox(Store(self.state, 'leaving')).path).read_bytes()
        self.assertEqual(choice['offset'], source.index(b'\n') + 1)
        self.assertNotIn('first-original', json.dumps(saved))

    def test_piped_default_preserves_originals_and_consume_is_explicit(self):
        for i in range(5):
            self.send('t' + str(i))
        first = self.process('inbox')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('t4', first.stdout)
        self.assertIn('t0', self.run_cli('inbox')[1])
        self.send('explicit-original')
        consumed = self.process('inbox', '--consume')
        self.assertEqual(consumed.returncode, 0, consumed.stderr)
        self.assertIn('empty', self.run_cli('inbox')[1])

    def test_follow_exits_after_first_complete_batch(self):
        self.send('QUESTION first-original')
        run = self.process('inbox', '--peek', '--follow', '--exit-after-first-batch', timeout=2)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('first-original', run.stdout)

    def test_mixed_marker_refusal_explains_namespace(self):
        os.environ.pop('COMS_SESSION_ID')
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'session-leaving'
        code, _, err = self.run_cli('inbox')
        self.assertEqual(code, 75)
        self.assertIn('marker namespace', err)

    def test_preview_context_is_opt_in_bounded_and_redacted(self):
        self.send('password=synthetic-hidden')
        self.send('QUESTION context-original ' + 'x' * 4000)
        code, out, err = self.run_cli('migrate', 'preview', '--context')
        self.assertEqual(code, 0, err)
        self.assertNotIn('synthetic-hidden', out)
        saved = json.loads(out)
        self.assertLess(len(out), 20000)
        self.assertTrue(saved['suggested_offsets'])
        self.assertIn('original_source', out)

    def test_legacy_migration_pending_field_no_longer_blocks(self):
        self.adopt()
        preview = self.home / 'preview.json'
        preview.write_text(self.run_cli('migrate', 'preview')[1])
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        entry = json.loads(path.read_text())
        entry['migration_pending'] = {'generation': []}
        path.write_text(json.dumps(entry))
        self.assertEqual(self.run_cli('migrate', 'apply', '--preview', str(preview), '--replay')[0], 0)
        self.assertEqual(self.run_cli('inbox')[0], 0)

    def test_corrupt_notification_prefix_never_replays_or_changes_state(self):
        payload = self.hooks()
        self.send('QUESTION original-notification')
        self.hook('coms-context', payload)
        path = pathlib.Path(self.state) / 'notification-seen' / self.env['COMS_HOLDER']
        raw = path.read_bytes()
        inbox = pathlib.Path(Inbox(Store(self.state, self.env['COMS_HOLDER'])).path)
        inbox.write_bytes(inbox.read_bytes().replace(b'original-notification', b'changed-notification!'))
        run = self.hook('coms-context', payload)
        self.assertNotIn('changed-notification', run.stdout)
        self.assertEqual(path.read_bytes(), raw)

    def test_actual_read_batch_consumes_with_redirected_output(self):
        self.launcher()
        self.send('QUESTION batch-original')
        run = subprocess.run(['/bin/bash', str(ROOT / 'skills/coms/bin/read-batch.sh')],
                             env=self.env, capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('batch-original', run.stdout)
        self.assertIn('empty', self.run_cli('inbox')[1])

    def test_runner_exports_resolved_selected_tmpdir_before_python(self):
        selected = self.home / 'selected-temp'
        selected.mkdir()
        alias = self.home / 'temp-alias'
        alias.symlink_to(selected, target_is_directory=True)
        fake = self.home / 'fake-python'
        fake.write_text('#!/bin/sh\nprintf \'%s\\n\' "$TMPDIR"\nexit 44\n')
        fake.chmod(0o700)
        env = dict(self.env, TMPDIR=str(alias), PYTHON=str(fake))
        run = subprocess.run(['/bin/bash', str(ROOT / 'run-tests.sh')], env=env,
                             capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 44, run.stderr)
        self.assertEqual(run.stdout.strip(), str(selected))

    def test_short_quiet_watch_uses_seconds(self):
        self.launcher()
        env = dict(self.env, WATCH_MAX_S='.2', WATCH_SETTLE_S='0', INBOX_POLL_S='.1')
        run = subprocess.run(['/bin/bash', str(ROOT / 'skills/coms/bin/watch-once.sh')], env=env,
                             capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('seconds', run.stdout)
        self.assertNotIn('0 minutes', run.stdout)

    def test_leaving_checklist_acks_before_note(self):
        document = (ROOT / 'skills/coms/references/handover.md').read_text()
        leaving = document.split('## Leaving side checklist')[1].split('## Holding side checklist')[0]
        self.assertLess(leaving.index('checkpoint-ack'), leaving.index('handover-note'))

    def test_printed_monitor_explicitly_consumes_in_fresh_environment(self):
        self.enroll('claude-owner')
        self.send('QUESTION monitor-original')
        command = Store(self.state, 'claude-owner').monitor_command(None)
        command += ' --exit-after-first-batch'
        env = dict(self.env)
        env.pop('COMS_HOLDER')
        env.pop('COMS_SESSION_ID')
        run = subprocess.run(['/bin/bash', '-c', command], env=env,
                             capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('monitor-original', run.stdout)
        self.assertIn('empty', self.run_cli('inbox')[1])

    def test_mismatched_native_payload_still_has_neutral_reminder(self):
        payload = self.hooks()
        self.send('PRIVATE wrong-native-original')
        self.env['CLAUDE_CODE_SESSION_ID'] = 'foreign-native'
        run = self.hook('coms-context', payload)
        self.assertIn('[coms] This repo', run.stdout)
        self.assertNotIn('wrong-native-original', run.stdout + run.stderr)
        self.assertFalse((pathlib.Path(self.state) / 'notification-seen').exists())

    def test_apply_uses_registry_before_inbox_lock_order(self):
        from unittest.mock import patch
        from coms.mutex import FileMutex
        self.adopt()
        preview = self.home / 'preview.json'
        preview.write_text(self.run_cli('migrate', 'preview')[1])
        order = []
        original = FileMutex.acquire
        def observe(lock, *args, **kwargs):
            order.append(str(lock.path))
            return original(lock, *args, **kwargs)
        with patch.object(FileMutex, 'acquire', observe):
            self.assertEqual(self.run_cli('migrate', 'apply', '--preview', str(preview),
                                         '--replay')[0], 0)
        registry = next(i for i, name in enumerate(order) if name.endswith('registry.lock'))
        inbox = next(i for i, name in enumerate(order) if '/inbox-' in name)
        self.assertLess(registry, inbox)

    def test_notification_append_uses_one_captured_prefix(self):
        import hashlib
        from unittest.mock import patch
        self.hooks()
        for i in range(6):
            self.send('QUESTION frozen-' + str(i))
        original = Inbox.snapshot_from
        appended = []
        def append_before_snapshot(inbox, saved, *args, **kwargs):
            if not appended:
                self.send('QUESTION appended-race')
                appended.append(True)
            return original(inbox, saved, *args, **kwargs)
        with patch.object(Inbox, 'snapshot_from', append_before_snapshot):
            self.assertEqual(self.run_cli('hook-notifications', 'stop')[0], 10)
        path = pathlib.Path(self.state) / 'stop-suppressed' / self.env['COMS_HOLDER']
        saved = json.loads(path.read_text())
        source = pathlib.Path(Inbox(Store(self.state, self.env['COMS_HOLDER'])).path).read_bytes()
        self.assertEqual(saved['prefix_hash'], hashlib.sha256(source[:saved['offset']]).hexdigest())
        self.assertEqual(self.run_cli('hook-notifications', 'stop')[0], 10)
        self.assertEqual(self.run_cli('hook-notifications', 'stop')[0], 0)

    def test_repair_does_not_add_a_migration_gate(self):
        self.send('QUESTION unreviewed-original')
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        path.write_text('{broken')
        self.assertEqual(self.run_cli('enroll', '--repair-enrollment')[0], 0)
        self.assertIn('unreviewed-original', self.run_cli('inbox', '--consume')[1])
        self.assertNotIn('migration_pending', json.loads(path.read_text()))

    def test_context_capture_refuses_changed_source_prefix(self):
        from unittest.mock import patch
        from coms import migration
        self.send('QUESTION original-context')
        source = pathlib.Path(Inbox(Store(self.state, 'leaving')).path)
        original = migration.suggestions
        def mutate_before_context(*args, **kwargs):
            source.write_bytes(source.read_bytes().replace(b'original-context', b'replaced-context'))
            return original(*args, **kwargs)
        with patch.object(migration, 'suggestions', mutate_before_context):
            code, out, _ = self.run_cli('migrate', 'preview', '--context')
        self.assertEqual(code, 2)
        self.assertEqual(out, '')

    def test_adoption_allows_staged_watch_delivery_and_output(self):
        self.send('QUESTION pending-staged-original')
        self.adopt()
        receipt = self.home / 'watch-delivery.json'
        code, out, err = self.run_cli('inbox', '--peek', '--delivery-receipt', str(receipt))
        self.assertEqual(code, 0, err)
        self.assertIn('pending-staged-original', out)
        self.assertTrue(receipt.exists())
        self.assertEqual(self.run_cli('checkpoint-deliver', str(receipt))[0], 0)
