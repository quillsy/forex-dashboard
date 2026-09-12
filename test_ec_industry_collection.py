"""Collector persistence and isolation tests; all fetches are local test doubles."""
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from collect_ec_industry import collect, DATA_NAME, STATUS_NAME, LOCK_NAME

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
RESULT = {'mode': 'research_only', 'core_eligible': False, 'observations': []}


class TestECIndustryCollection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.fetcher = patch('collect_ec_industry.fetch', return_value=RESULT).start()
        self.addCleanup(patch.stopall)

    def test_hourly_restart_and_boundary(self):
        self.assertEqual(collect(self.directory, now=NOW)['status'], 'success')
        self.assertEqual(collect(self.directory, now=NOW + timedelta(minutes=59))['collection_action'], 'not_due')
        self.assertEqual(self.fetcher.call_count, 1)
        collect(self.directory, now=NOW + timedelta(hours=1))
        self.assertEqual(self.fetcher.call_count, 2)
        self.assertEqual(json.loads((self.directory / DATA_NAME).read_text()), RESULT)

    def test_failure_preserves_artifact_and_success_time(self):
        collect(self.directory, now=NOW)
        original = (self.directory / DATA_NAME).read_bytes()
        self.fetcher.side_effect = requests.Timeout('SECRET api_key=hidden')
        result = collect(self.directory, now=NOW + timedelta(hours=1))
        self.assertEqual(result['error_code'], 'EC_INDUSTRY_REQUEST_FAILED')
        self.assertEqual(result['last_success_at'], NOW.isoformat())
        self.assertEqual((self.directory / DATA_NAME).read_bytes(), original)
        self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())
        collect(self.directory, now=NOW + timedelta(minutes=61))
        self.assertEqual(self.fetcher.call_count, 2)

    def test_invalid_response_never_overwrites_or_creates(self):
        for prior in (False, True):
            for invalid in (None, {}, {'mode': 'research_only', 'core_eligible': True},
                            {**RESULT, 'value': float('nan')}):
                with self.subTest(prior=prior, invalid=invalid):
                    (self.directory / STATUS_NAME).unlink(missing_ok=True)
                    (self.directory / DATA_NAME).unlink(missing_ok=True)
                    if prior:
                        (self.directory / DATA_NAME).write_text('prior tested artifact')
                    self.fetcher.return_value = invalid
                    result = collect(self.directory, now=NOW)
                    self.assertEqual(result['error_code'], 'EC_INDUSTRY_RESPONSE_INVALID')
                    self.assertEqual((self.directory / DATA_NAME).exists(), prior)
                    if prior:
                        self.assertEqual((self.directory / DATA_NAME).read_text(), 'prior tested artifact')

    def test_adapter_schema_error_is_sanitized(self):
        self.fetcher.side_effect = ValueError('SECRET raw response')
        result = collect(self.directory, now=NOW)
        self.assertEqual(result['error_code'], 'EC_INDUSTRY_RESPONSE_INVALID')
        self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())

    def test_crash_records_attempt_before_fetch(self):
        self.fetcher.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect(self.directory, now=NOW)
        self.assertEqual(json.loads((self.directory / STATUS_NAME).read_text())['status'], 'attempt_started')
        self.fetcher.side_effect = None
        collect(self.directory, now=NOW + timedelta(minutes=10))
        self.assertEqual(self.fetcher.call_count, 1)

    def test_lock_blocks_duplicate_fetch(self):
        with (self.directory / LOCK_NAME).open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(collect(self.directory, now=NOW)['status'], 'already_running')
            self.fetcher.assert_not_called()

    def test_bis_lock_and_files_are_independent(self):
        (self.directory / 'status.json').write_text('BIS status unchanged')
        with (self.directory / '.collection.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(collect(self.directory, now=NOW)['status'], 'success')
        self.assertEqual((self.directory / 'status.json').read_text(), 'BIS status unchanged')

    def test_corrupt_future_and_foreign_status_cannot_suppress_fetch(self):
        for value in ('bad json', json.dumps({'schema': 'bis-research-collection-v1',
                'last_attempt_at': NOW.isoformat(), 'next_attempt_at': (NOW + timedelta(hours=1)).isoformat()}),
                json.dumps({'schema': 'ec-industry-research-collection-v1',
                'last_attempt_at': (NOW + timedelta(days=1)).isoformat(),
                'next_attempt_at': (NOW + timedelta(days=2)).isoformat()})):
            with self.subTest(value=value):
                (self.directory / STATUS_NAME).write_text(value)
                self.assertEqual(collect(self.directory, now=NOW)['status'], 'success')
        self.assertEqual(self.fetcher.call_count, 3)

    def test_naive_now_rejected_without_request(self):
        with self.assertRaises(ValueError):
            collect(self.directory, now=NOW.replace(tzinfo=None))
        self.fetcher.assert_not_called()


if __name__ == '__main__':
    unittest.main()
