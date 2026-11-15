"""Runtime trust boundaries strengthening the original review regressions."""
import hashlib
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import time
from unittest.mock import patch

from coms import cli
from coms.inbox import Inbox, atomic_json
from coms.store import Store
from tests.test_switchover import ROOT, Sandbox


class TrustBoundaryTest(Sandbox):
    def launcher(self):
        binary = self.home / 'bin/coms'
        binary.parent.mkdir(exist_ok=True)
        binary.write_text('#!/bin/sh\nexec python3 -m coms.cli "$@"\n')
        binary.chmod(0o700)
        self.env['PATH'] = str(binary.parent) + os.pathsep + self.env['PATH']

    def test_monitor_executes_in_clean_marker_environment(self):
        self.enroll('claude-owner')
        self.send('QUESTION original-monitor')
        command = Store(self.state, 'claude-owner').monitor_command(None).replace(' 1800 env', ' 1 env')
        env = dict(self.env)
        for name in ('COMS_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID'):
            env.pop(name, None)
        run = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=3)
        self.assertIn('original-monitor', run.stdout, run.stderr)
        self.assertNotIn('different live session', run.stderr)

    def test_native_monitor_executes_without_inherited_native_marker(self):
        os.environ.pop('COMS_SESSION_ID')
        os.environ['CLAUDE_CODE_SESSION_ID'] = 'native-owner'
        os.environ['COMS_HOLDER'] = 'claude-native'
        self.assertEqual(self.run_cli('enroll')[0], 0)
        self.send('QUESTION native-original')
        command = Store(self.state, 'claude-native').monitor_command(None).replace(' 1800 env', ' 1 env')
        env = dict(self.env)
        env.pop('COMS_SESSION_ID', None)
        run = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=3)
        self.assertIn('native-original', run.stdout, run.stderr)

    def test_actual_documented_watch_command_has_inline_identity(self):
        self.launcher()
        self.send('QUESTION inline-doc-original')
        env = dict(self.env, WATCH_MAX_S='.5', WATCH_SETTLE_S='0', INBOX_POLL_S='.1')
        env.pop('COMS_HOLDER')
        env.pop('COMS_SESSION_ID')
        document = (ROOT / 'skills/coms/bin/README.md').read_text()
        command = next(line.strip() for line in document.splitlines()
                       if 'watch-once.sh' in line and line.strip().startswith('COMS_HOLDER='))
        command = command.replace('my-holder', 'leaving').replace('my-session', 'session-leaving')
        command = command.replace('~/.claude/skills/coms/bin/watch-once.sh', str(ROOT / 'skills/coms/bin/watch-once.sh'))
        run = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('inline-doc-original', run.stdout)

    def test_fsync_uses_directory_descriptor(self):
        kinds = []
        original = os.fsync
        def inspect(fd):
            kinds.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            original(fd)
        with patch('coms.inbox.os.fsync', side_effect=inspect):
            atomic_json(str(self.home / 'private/state.json'), {'test': 1})
        self.assertEqual(kinds, [False, True])

    def test_catchable_signals_no_late_write_after_short_lease_expiry(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number):
                state = self.state + str(number)
                ready, late, child = [self.home / (name + str(number)) for name in ('ready', 'late', 'child')]
                worker = ("import os,pathlib,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                          "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
                          "pathlib.Path(sys.argv[2]).write_text('ready');"
                          "time.sleep(1);pathlib.Path(sys.argv[3]).write_text('orphan')")
                wrapper = ("import sys;from coms import cli;from coms.store import Store;"
                           "cli._store=lambda cdir:Store(cdir,'leaving',ttl=.6);"
                           "sys.exit(cli.main(['--dir',sys.argv[1],'do','repo:main','--wait','0','--']+sys.argv[2:]))")
                process = subprocess.Popen([sys.executable, '-c', wrapper, state, sys.executable, '-c',
                                            worker, str(child), str(ready), str(late)], env=self.env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 2
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(ready.exists())
                    os.kill(process.pid, number)
                    process.wait(timeout=4)
                    time.sleep(.65)
                    with Store(state, 'contender', ttl=.6).acquire('repo:main', wait=1):
                        time.sleep(.45)
                        self.assertFalse(late.exists(), 'orphan write after another holder acquired')
                    self.assertNotIn('repo:main', Store(state, 'observer').status()['resources'])
                finally:
                    if child.exists():
                        try:
                            os.killpg(int(child.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_other_key_nested_refusal_preserves_outer_token(self):
        claim = Store(self.state, 'leaving').acquire('repo:outer')
        os.environ.update(COMS_LEASE_KEY='repo:outer', COMS_LEASE_TOKEN=str(claim.token))
        child = self.home / 'should-not-run'
        code, _, err = self.run_cli('do', 'repo:inner', '--wait', '0', '--', 'touch', str(child))
        self.assertEqual(code, 75)
        self.assertIn('nested', err)
        self.assertFalse(child.exists())
        self.assertEqual(Store(self.state, 'leaving').status()['resources']['repo:outer']['token'], claim.token)

    def test_invalid_inherited_token_is_cleared_and_child_runs(self):
        os.environ.update(COMS_LEASE_KEY='repo:outer', COMS_LEASE_TOKEN='invalid')
        child = self.home / 'should-not-run'
        self.assertEqual(self.run_cli('do', 'repo:inner', '--wait', '0', '--', 'touch', str(child))[0], 0)
        self.assertTrue(child.exists())

    def test_cookie_prose_in_named_loops_does_not_refuse(self):
        loops = json.loads(self.loops.read_text())
        loops['open_work'] = ['privacy: cookie: decline non-essential']
        self.loops.write_text(json.dumps(loops))
        note = self.note()
        self.assertIn('cookie: decline non-essential', note.read_text())

    def test_two_enrollment_repairs_preserve_private_raw_backups(self):
        self.enroll('leaving')
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        originals = [b'{', b'[]']
        for raw in originals:
            path.write_bytes(raw)
            self.assertEqual(self.run_cli('enroll')[0], 75)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(self.run_cli('enroll', '--repair-enrollment')[0], 0)
        backups = list(path.parent.glob('leaving.json.corrupt-*'))
        self.assertEqual(sorted(p.read_bytes() for p in backups), sorted(originals))
        for backup in backups:
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_repair_never_overrides_foreign_owner_or_retirement(self):
        self.enroll('leaving')
        path = pathlib.Path(Store(self.state, 'leaving')._thread_path())
        os.environ['COMS_SESSION_ID'] = 'foreign'
        original = path.read_bytes()
        self.assertEqual(self.run_cli('enroll', '--repair-enrollment')[0], 75)
        self.assertEqual(path.read_bytes(), original)
        self.as_holder('leaving')
        data = json.loads(path.read_text())
        data['retired'] = True
        path.write_text(json.dumps(data))
        self.assertEqual(self.run_cli('enroll', '--repair-enrollment')[0], 75)

    def test_large_receipt_source_mutation_refuses_without_handled_write(self):
        self.send('x' * 100000)
        code, receipt, err = self.run_cli('inbox', '--peek', '--receipt')
        self.assertEqual(code, 0, err)
        file = self.home / 'receipt.json'
        file.write_text(receipt)
        inbox = pathlib.Path(Inbox(Store(self.state, 'leaving')).path)
        inbox.write_bytes(inbox.read_bytes().replace(b'xxxx', b'yyyy', 1))
        self.assertEqual(self.run_cli('checkpoint-ack', str(file), '--pending', str(self.loops))[0], 2)
        self.assertFalse((pathlib.Path(self.state) / 'handled/leaving.json').exists())

    def test_physical_identical_originals_relay_once_each(self):
        self.send('same physical original', 10)
        self.send('same physical original', 10)
        note = self.note()
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', '--replay', str(note), '--to', 'replacement')[0], 0)
        rows = Store(self.state, 'replacement').read_inbox()
        self.assertEqual(sum('same physical original' in row['text'] for row in rows), 2)
        self.assertIn('relayed: 0', self.run_cli('handover', 'relay', '--replay', str(note), '--to', 'replacement')[1])

    def test_redaction_carries_exact_source_range_and_hash(self):
        self.send('password=synthetic-private-value')
        source = pathlib.Path(Inbox(Store(self.state, 'leaving')).path)
        raw = source.read_bytes()
        note = self.note()
        self.as_holder('holding')
        self.assertEqual(self.run_cli('handover', 'relay', '--replay', str(note), '--to', 'replacement')[0], 0)
        rows = Store(self.state, 'replacement').read_inbox()
        message = json.loads(next(row['text'].removeprefix('HANDOVER ORIGINAL ') for row in rows
                                  if 'REDACTED' in row['text']))
        self.assertEqual(message['original_source']['byte_start'], 0)
        self.assertEqual(message['original_source']['byte_end'], len(raw))
        self.assertEqual(message['original_source']['sha256'], hashlib.sha256(raw).hexdigest())

    def test_old_cancel_cannot_clear_new_transition_marker(self):
        first = self.note()
        self.as_holder('leaving')
        args = ('handover', 'note', '--to', 'holding', '--role', 'codex-lead',
                '--open-loops', str(self.loops), '--id', 'second')
        self.assertEqual(self.run_cli(*args)[0], 2)
        self.assertEqual(self.run_cli('handover', 'cancel', str(first))[0], 0)
        self.assertEqual(self.run_cli(*args)[0], 0)
        self.assertEqual(self.run_cli('handover', 'cancel', str(first))[0], 2)
        self.assertEqual(self.run_cli('do', 'repo:main', '--wait', '0', '--', 'true')[0], 0)

    def test_signal_after_acquire_write_releases_only_this_request(self):
        original = Store._write
        def interrupt_after_write(store, state):
            original(store, state)
            signal.raise_signal(signal.SIGTERM)
        with patch.object(Store, '_write', interrupt_after_write):
            code, _, _ = self.run_cli('do', 'repo:main', '--wait', '0', '--', 'true')
        self.assertEqual(code, 143)
        self.assertNotIn('repo:main', Store(self.state, 'observer').status()['resources'])

    def test_missing_enrollment_never_consumes_foreign_unread(self):
        pathlib.Path(Store(self.state, 'leaving')._thread_path()).unlink()
        self.send('missing-owner-original')
        self.assertEqual(self.run_cli('inbox')[0], 75)
        self.assertFalse(pathlib.Path(Inbox(Store(self.state, 'leaving')).checkpoint).exists())
        self.assertIn('missing-owner-original', self.run_cli('inbox', '--peek')[1])

    def test_actual_native_binding_works_in_both_claude_hooks(self):
        self.launcher()
        native = 'abcd1234-synthetic-native'
        holder = 'claude-abcd1234'
        os.environ.pop('COMS_SESSION_ID')
        os.environ.update(CLAUDE_CODE_SESSION_ID=native, COMS_HOLDER=holder)
        self.assertEqual(self.run_cli('enroll')[0], 0)
        repo = self.home / 'guarded'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True, capture_output=True)
        (repo / '.git/coms-enabled').write_text('coms guard marker v1\n')
        env = dict(os.environ, PATH=self.env['PATH'])
        for hook in ('coms-context', 'coms-inbox-stop-gate.sh'):
            with self.subTest(hook=hook):
                self.send('QUESTION native-hook-' + hook)
                run = subprocess.run(['bash', str(ROOT / 'hooks' / 'claude' / hook)], env=env,
                                     input=json.dumps({'cwd': str(repo), 'session_id': native}),
                                     capture_output=True, text=True, timeout=3)
                self.assertIn('native-hook-' + hook, run.stdout + run.stderr)
                self.assertEqual(run.returncode, 0 if hook == 'coms-context' else 2)

    def hook_fixture(self, marker='own-explicit-marker'):
        self.launcher()
        native = 'dcba4321-synthetic-native'
        holder = 'claude-dcba4321'
        os.environ.update(COMS_SESSION_ID=marker, CLAUDE_CODE_SESSION_ID=native, COMS_HOLDER=holder)
        self.assertEqual(self.run_cli('enroll')[0], 0)
        repo = self.home / 'guarded'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True, capture_output=True)
        (repo / '.git/coms-enabled').write_text('coms guard marker v1\n')
        return native, holder, repo, dict(os.environ, PATH=self.env['PATH'])

    def test_matching_explicit_override_works_in_both_hooks(self):
        native, holder, repo, env = self.hook_fixture()
        for hook in ('coms-context', 'coms-inbox-stop-gate.sh'):
            self.send('QUESTION override-' + hook)
            run = subprocess.run(['bash', str(ROOT / 'hooks' / 'claude' / hook)], env=env,
                                 input=json.dumps({'cwd': str(repo), 'session_id': native}),
                                 capture_output=True, text=True, timeout=3)
            self.assertIn('override-' + hook, run.stdout + run.stderr)

    def test_hook_foreign_selection_and_native_payload_mismatch_refuse(self):
        native, holder, repo, env = self.hook_fixture()
        self.send('QUESTION own-sensitive-original')
        for foreign in (dict(env, COMS_HOLDER='foreign-holder', COMS_SESSION_ID='foreign-marker'),
                        dict(env, CLAUDE_CODE_SESSION_ID='different-native')):
            for hook in ('coms-context', 'coms-inbox-stop-gate.sh'):
                run = subprocess.run(['bash', str(ROOT / 'hooks' / 'claude' / hook)], env=foreign,
                                     input=json.dumps({'cwd': str(repo), 'session_id': native}),
                                     capture_output=True, text=True, timeout=3)
                self.assertNotIn('own-sensitive-original', run.stdout + run.stderr)
        self.assertFalse((pathlib.Path(self.state) / 'notification-seen' / holder).exists())

    def test_native_printed_monitor_clears_inherited_foreign_override(self):
        os.environ.pop('COMS_SESSION_ID')
        os.environ.update(CLAUDE_CODE_SESSION_ID='native-marker', COMS_HOLDER='claude-owner')
        self.assertEqual(self.run_cli('enroll')[0], 0)
        self.send('QUESTION native-clean-env')
        command = Store(self.state, 'claude-owner').monitor_command(None).replace(' 1800 env', ' 1 env')
        env = dict(self.env, COMS_SESSION_ID='foreign-inherited')
        run = subprocess.run(['bash', '-c', command], env=env, capture_output=True, text=True, timeout=3)
        self.assertIn('native-clean-env', run.stdout, run.stderr)

    def test_partial_inherited_lease_context_is_cleared_before_child(self):
        claim = Store(self.state, 'leaving').acquire('repo:outer')
        for name, value in (('COMS_LEASE_KEY', 'repo:outer'), ('COMS_LEASE_TOKEN', str(claim.token))):
            with self.subTest(variable=name):
                os.environ.pop('COMS_LEASE_KEY', None)
                os.environ.pop('COMS_LEASE_TOKEN', None)
                os.environ[name] = value
                child = self.home / ('should-not-run-' + name)
                code, _, err = self.run_cli('do', 'repo:inner', '--wait', '0', '--', 'touch', str(child))
                self.assertEqual(code, 0, err)
                self.assertIn('stale inherited lease context', err)
                self.assertTrue(child.exists())
                resources = Store(self.state, 'observer').status()['resources']
                self.assertNotIn('repo:inner', resources)
                self.assertEqual(resources['repo:outer']['token'], claim.token)
