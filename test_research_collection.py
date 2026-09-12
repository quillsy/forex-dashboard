import fcntl
import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import requests
from collect_research import collect as raw_collect, DATA_NAME, STATUS_NAME, ARCHIVE_NAME
from test_bis_research import NOW, ROW, payload


def collect(directory, **kwargs):
    kwargs.setdefault('clock', lambda: kwargs.get('now', NOW) + timedelta(seconds=1))
    with patch('research_vintages._now', return_value=NOW + timedelta(days=10)):
        return raw_collect(directory, **kwargs)


def session(text=None, error=None):
    s = Mock()
    if error:
        s.get.side_effect = error
    else:
        s.get.return_value.status_code = 200
        s.get.return_value.text = text if text is not None else payload([ROW])
    return s


class TestCollection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)

    def test_restart_cache_due_and_no_secret_params(self):
        first = session()
        result = collect(self.directory, now=NOW, session=first)
        self.assertEqual(result['status'], 'success')
        first.get.assert_called_once()
        self.assertEqual(set(first.get.call_args.kwargs), {'params', 'timeout', 'allow_redirects'})
        cached = session()
        result = collect(self.directory, now=NOW + timedelta(hours=23), session=cached)
        cached.get.assert_not_called()
        self.assertEqual(result['collection_action'], 'not_due')
        collect(self.directory, now=NOW + timedelta(days=1), session=cached)
        cached.get.assert_called_once()

    def test_failure_retains_data_and_sanitizes(self):
        collect(self.directory, now=NOW, session=session())
        original = (self.directory / DATA_NAME).read_bytes()
        failed = session(error=requests.Timeout('secret-key=DO-NOT-LOG'))
        result = collect(self.directory, now=NOW + timedelta(days=1), session=failed)
        self.assertEqual(result['error_code'], 'BIS_REQUEST_FAILED')
        self.assertEqual(result['last_success_at'], NOW.isoformat())
        self.assertEqual(original, (self.directory / DATA_NAME).read_bytes())
        self.assertNotIn('DO-NOT-LOG', (self.directory / STATUS_NAME).read_text())
        collect(self.directory, now=NOW + timedelta(days=1, hours=1), session=failed)
        self.assertEqual(failed.get.call_count, 1)

    def test_malformed_never_creates_or_overwrites_data(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                (self.directory / STATUS_NAME).unlink(missing_ok=True)
                if existing:
                    collect(self.directory, now=NOW, session=session())
                original = (self.directory / DATA_NAME).read_bytes() if existing else None
                bad = session('<html>secret invalid content</html>')
                result = collect(self.directory, now=NOW + timedelta(days=1), session=bad)
                self.assertEqual(result['error_code'], 'BIS_RESPONSE_INVALID')
                if existing:
                    self.assertEqual(original, (self.directory / DATA_NAME).read_bytes())
                else:
                    self.assertFalse((self.directory / DATA_NAME).exists())
                self.assertNotIn('secret', (self.directory / STATUS_NAME).read_text())

    def test_interrupted_attempt_remains_due_aware(self):
        crashing = session(error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            collect(self.directory, now=NOW, session=crashing)
        self.assertEqual(json.loads((self.directory / STATUS_NAME).read_text())['status'], 'attempt_started')
        restart = session()
        collect(self.directory, now=NOW + timedelta(minutes=1), session=restart)
        restart.get.assert_not_called()

    def test_concurrent_process_no_request(self):
        with (self.directory / '.collection.lock').open('a') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            other = session()
            self.assertEqual(collect(self.directory, now=NOW, session=other)['status'], 'already_running')
            other.get.assert_not_called()

    def test_corrupted_status_preserved_and_rejected(self):
        (self.directory / STATUS_NAME).write_text('invalid')
        with patch('collect_research.fetch') as fetcher:
            result = collect(self.directory, now=NOW, session=session())
            fetcher.assert_not_called()
        self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual((self.directory / STATUS_NAME).read_text(), 'invalid')
        self.assertFalse(list(self.directory.glob('*.tmp')))

    def test_http_redirect_rejected_without_following(self):
        redirect = session()
        redirect.get.return_value.status_code = 302
        result = collect(self.directory, now=NOW, session=redirect)
        self.assertEqual(result['error_code'], 'BIS_REQUEST_FAILED')
        self.assertFalse(redirect.get.call_args.kwargs['allow_redirects'])


    def test_archive_first_capture_anchor_and_no_bootstrap(self):
        (self.directory / DATA_NAME).write_text('old pre-archive artifact')
        result = collect(self.directory, now=NOW, session=session())
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['archive_event_count'], 1)
        self.assertEqual(result['archive_head']['sequence'], 1)
        journal = json.loads((self.directory / ARCHIVE_NAME).read_text())
        self.assertEqual(len(journal['events']), 1)
        event = journal['events'][0]
        self.assertEqual(event['first_observed_at'], (NOW + timedelta(seconds=1)).isoformat())
        result2 = collect(self.directory, now=NOW + timedelta(days=1), session=session())
        self.assertEqual(result2['archive_head'], result['archive_head'])
        self.assertEqual(result2['archive_event_count'], 1)

    def test_deleted_or_corrupt_archive_blocks_fetch_even_not_due(self):
        collect(self.directory, now=NOW, session=session())
        original = (self.directory / DATA_NAME).read_bytes()
        for raw in (None, 'bad SECRET raw json', '{}'):
            with self.subTest(raw=raw):
                path = self.directory / ARCHIVE_NAME
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(raw)
                with patch('collect_research.fetch') as fetcher:
                    result = collect(self.directory, now=NOW + timedelta(minutes=1))
                    fetcher.assert_not_called()
                self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
                self.assertEqual((self.directory / DATA_NAME).read_bytes(), original)
                self.assertEqual(result['archive_head']['sequence'], 1)
                self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())

    def test_archive_write_failure_does_not_replace_latest(self):
        collect(self.directory, now=NOW, session=session())
        data = (self.directory / DATA_NAME).read_bytes()
        history = (self.directory / ARCHIVE_NAME).read_bytes()
        with patch('collect_research.append_vintage', side_effect=OSError('SECRET disk error')):
            result = collect(self.directory, now=NOW + timedelta(days=1), session=session())
        self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual((self.directory / DATA_NAME).read_bytes(), data)
        self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), history)
        self.assertEqual(result['last_success_at'], NOW.isoformat())
        self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())

    def test_interruption_after_archive_or_latest_recovers_without_duplicate(self):
        import collect_research as collector_module
        actual_write = collector_module.atomic_json
        for boundary in ('latest', 'final_status'):
            with self.subTest(boundary=boundary):
                for path in self.directory.glob('*.json'):
                    path.unlink()
                def interrupted(path, value):
                    if ((boundary == 'latest' and Path(path).name == DATA_NAME)
                            or (boundary == 'final_status' and Path(path).name == STATUS_NAME
                                and value.get('status') == 'success')):
                        raise KeyboardInterrupt()
                    actual_write(path, value)
                with patch('collect_research.atomic_json', side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        collect(self.directory, now=NOW, session=session())
                self.assertTrue((self.directory / ARCHIVE_NAME).exists())
                self.assertEqual(json.loads((self.directory / STATUS_NAME).read_text())['status'], 'attempt_started')
                state = collect(self.directory, now=NOW + timedelta(days=1), session=session())
                self.assertEqual(state['status'], 'success')
                self.assertEqual(state['archive_event_count'], 1)
                self.assertEqual(state['archive_head']['sequence'], 1)

    def test_regressed_postfetch_clock_preserves_latest_and_history(self):
        collect(self.directory, now=NOW, session=session())
        old_data = (self.directory / DATA_NAME).read_bytes()
        old_history = (self.directory / ARCHIVE_NAME).read_bytes()
        result = collect(self.directory, now=NOW + timedelta(days=1),
                         clock=lambda: NOW, session=session())
        self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual((self.directory / DATA_NAME).read_bytes(), old_data)
        self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), old_history)


    def test_established_archive_corrupt_status_preserves_all_evidence(self):
        collect(self.directory, now=NOW, session=session())
        history = (self.directory / ARCHIVE_NAME).read_bytes()
        latest = (self.directory / DATA_NAME).read_bytes()
        for raw in ('not JSON', '[]', 'null', '{"archive_head":{},"archive_head":null}',
                    '{"archive_head":NaN}'):
            with self.subTest(raw=raw):
                (self.directory / STATUS_NAME).write_text(raw)
                with patch('collect_research.fetch') as fetcher:
                    result = collect(self.directory, now=NOW + timedelta(days=1))
                    fetcher.assert_not_called()
                self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
                self.assertEqual((self.directory / STATUS_NAME).read_text(), raw)
                self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), history)
                self.assertEqual((self.directory / DATA_NAME).read_bytes(), latest)

if __name__ == '__main__':
    unittest.main()
