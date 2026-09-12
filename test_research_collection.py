import fcntl
import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import requests
from collect_research import collect, DATA_NAME, STATUS_NAME
from test_bis_research import NOW, ROW, payload


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

    def test_corrupted_status_recovered(self):
        (self.directory / STATUS_NAME).write_text('invalid')
        self.assertEqual(collect(self.directory, now=NOW, session=session())['status'], 'success')
        self.assertFalse(list(self.directory.glob('*.tmp')))

    def test_http_redirect_rejected_without_following(self):
        redirect = session()
        redirect.get.return_value.status_code = 302
        result = collect(self.directory, now=NOW, session=redirect)
        self.assertEqual(result['error_code'], 'BIS_REQUEST_FAILED')
        self.assertFalse(redirect.get.call_args.kwargs['allow_redirects'])

if __name__ == '__main__':
    unittest.main()
