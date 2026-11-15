"""Focused current-view and explicit replacement proofs, using private fixtures."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from coms import cli, decisions, owner_asks

FLAG = '-' * 2


class CurrentDecisionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='coms-current-',
                                                dir=os.path.realpath(tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            'COMS_DIR': self.temp.name,
            'COMS_HOLDER': 'codex-lead-window',
            'COMS_SESSION_ID': '00000000-0000-0000-0000-000000000001',
            'COMS_HANDOVER_DIR': self.temp.name + '/handovers',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.log = decisions.DecisionLog(self.temp.name, 'fixture', clock=lambda: 100)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def fixture(self, topic, answer, **extra):
        row = dict(ts=100, by='fixture', repo='', topic=topic, answer=answer, quote='owner')
        row.update(extra)
        with open(self.log.path, 'a') as destination:
            destination.write(json.dumps(row) + '\n')
        return row

    def current(self, prefix=''):
        code, out, err = self.run_cli('decisions', FLAG + 'current', FLAG + 'json', prefix)
        self.assertEqual((code, err), (0, ''))
        return json.loads(out)

    def test_current_prefix_latest_links_expiry_and_no_resurrection(self):
        self.fixture('sample-old', 'old')
        self.fixture('sample-new', 'new', supersedes='sample-old')
        self.fixture('Sample-New', 'latest')
        self.fixture('sample-temp', 'prior permanent')
        self.fixture('sample-temp', 'expired', expires_at=100)
        self.fixture('sample-active', 'active', expires_at=101)
        self.fixture('other-topic', 'mentions sample')
        self.fixture('unrelated', 'later', supersedes='sample-active')
        with mock.patch.object(decisions.time, 'time', return_value=100):
            self.assertEqual([(r['topic'], r['answer']) for r in self.current('SAMPLE')],
                             [('Sample-New', 'latest')])
        self.assertEqual(len(self.log.entries()), 8)
        # Links apply before prefix filtering. The legacy search is unchanged.
        code, out, _ = self.run_cli('decisions', 'sample', FLAG + 'json')
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)), 5)

    def test_supersedes_flag_appends_link_and_retains_legacy_bytes(self):
        self.log.record('old-topic', 'old', 'owner')
        before = Path(self.log.path).read_bytes()
        code, out, err = self.run_cli('decide', 'new-topic', 'new', FLAG + 'quote',
                                    'owner words', FLAG + 'supersedes', ' OLD-TOPIC ')
        self.assertEqual((code, out, err), (0, 'coms: recorded decision on new-topic\n', ''))
        self.assertTrue(Path(self.log.path).read_bytes().startswith(before))
        self.assertEqual(self.log.entries()[-1]['supersedes'], 'OLD-TOPIC')
        self.assertEqual([r['topic'] for r in self.current()], ['new-topic'])
        legacy = self.run_cli('decisions', FLAG + 'json')
        self.assertEqual([r['topic'] for r in json.loads(legacy[1])], ['new-topic', 'old-topic'])
        self.assertTrue(all(set(r) == set(decisions.FIELDS) for r in json.loads(legacy[1])))
        self.assertEqual(self.current()[0]['supersedes'], 'OLD-TOPIC')
        self.assertEqual(self.run_cli('decide', 'plain', 'yes', FLAG + 'quote', 'yes')[:2],
                         (0, 'coms: recorded decision on plain\n'))
        self.assertEqual(set(self.log.entries()[-1]), set(decisions.FIELDS))

    def test_supersedes_rejects_unknown_self_blank_before_write(self):
        self.log.record('old', 'yes', 'yes')
        before = Path(self.log.path).read_bytes()
        for target in ('missing', 'new', '   '):
            code, _, err = self.run_cli('decide', 'new', 'yes', FLAG + 'quote', 'yes',
                                       FLAG + 'supersedes', target)
            self.assertEqual(code, 2)
            self.assertIn('supersedes', err)
            self.assertEqual(Path(self.log.path).read_bytes(), before)

    def test_current_reintroduced_topic_and_expired_successor(self):
        self.fixture('a', 'original')
        self.fixture('b', 'replacement', supersedes='a', expires_at=99)
        with mock.patch.object(decisions.time, 'time', return_value=100):
            self.assertEqual(self.current(), [])
        self.fixture('a', 'explicit later ruling')
        with mock.patch.object(decisions.time, 'time', return_value=100):
            self.assertEqual([r['answer'] for r in self.current()], ['explicit later ruling'])
        self.assertEqual(self.run_cli('decisions', FLAG + 'current', FLAG + 'all')[0], 2)

    def test_expiry_flag_requires_explicit_timezone_and_keeps_legacy_shape(self):
        code, _, err = self.run_cli('decide', 'temporary', 'yes', FLAG + 'quote', 'yes',
                                   FLAG + 'expires-at', '2026-10-11T14:00:00-04:00')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(self.log.entries()[-1]['expires_at'], 1791741600)
        before = Path(self.log.path).read_bytes()
        for expiry in ('tomorrow', '2026-10-11T14:00:00', 'NaN'):
            self.assertEqual(self.run_cli('decide', 'bad', 'yes', FLAG + 'quote', 'yes',
                                         FLAG + 'expires-at', expiry)[0], 2)
            self.assertEqual(Path(self.log.path).read_bytes(), before)

    def test_current_ignores_malformed_optional_metadata(self):
        self.fixture('a', 'original')
        self.fixture('bad', 'bad', supersedes=['a'], expires_at='yesterday')
        self.fixture('bad-number', 'bad', expires_at=True)
        self.assertEqual([r['topic'] for r in self.current()], ['bad-number', 'bad', 'a'])

    def test_optional_metadata_remains_readable_to_owner_ask_validator(self):
        plain = dict(ts=100, by='fixture', repo='', topic='new', answer='yes', quote='owner')
        def encode(row):
            return (json.dumps(row) + '\n').encode()
        for extra in ({}, {'supersedes': 'old'}, {'expires_at': 200},
                      {'supersedes': 'old', 'expires_at': 200}):
            row = dict(plain, **extra)
            self.assertEqual(owner_asks._validate_decisions(encode(row)), [row])
        for extra in ({'unrecognized': 'value'}, {'supersedes': []}, {'supersedes': ''},
                      {'supersedes': ' NEW '}, {'expires_at': True},
                      {'expires_at': 'tomorrow'}, {'expires_at': float('inf')}):
            with self.assertRaises(ValueError):
                owner_asks._validate_decisions(encode(dict(plain, **extra)))


if __name__ == '__main__':
    unittest.main()
