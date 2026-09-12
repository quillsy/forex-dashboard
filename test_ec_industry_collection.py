"""Collector persistence and isolation tests; all fetches are local test doubles."""
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from collect_ec_industry import collect as raw_collect, DATA_NAME, STATUS_NAME, LOCK_NAME, ARCHIVE_NAME
from test_ec_industry_research import fixture
from ec_industry_research import parse_json, _snapshot

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
RESULT = parse_json(json.dumps(fixture()), retrieved_at=NOW, now=NOW)
RESULT['source_snapshot'] = _snapshot(fixture())

def collect(directory, **kwargs):
    kwargs.setdefault('clock', lambda: kwargs.get('now', NOW) + timedelta(seconds=1))
    with patch('research_vintages._now', return_value=NOW + timedelta(days=10)):
        return raw_collect(directory, **kwargs)


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
                    (self.directory / ARCHIVE_NAME).unlink(missing_ok=True)
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

    def test_corrupt_status_rejected_future_and_foreign_rechecked(self):
        for value in ('bad json', json.dumps({'schema': 'bis-research-collection-v1',
                'last_attempt_at': NOW.isoformat(), 'next_attempt_at': (NOW + timedelta(hours=1)).isoformat()}),
                json.dumps({'schema': 'ec-industry-research-collection-v1',
                'last_attempt_at': (NOW + timedelta(days=1)).isoformat(),
                'next_attempt_at': (NOW + timedelta(days=2)).isoformat()})):
            with self.subTest(value=value):
                (self.directory / STATUS_NAME).write_text(value)
                result = collect(self.directory, now=NOW)
                self.assertEqual(result['status'], 'failed' if value == 'bad json' else 'success')
                if value == 'bad json':
                    self.assertEqual((self.directory / STATUS_NAME).read_text(), value)
                    self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual(self.fetcher.call_count, 2)

    def test_naive_now_rejected_without_request(self):
        with self.assertRaises(ValueError):
            collect(self.directory, now=NOW.replace(tzinfo=None))
        self.fetcher.assert_not_called()



    def test_archive_first_capture_anchor_and_no_bootstrap(self):
        (self.directory / DATA_NAME).write_text('old pre-archive artifact')
        result = collect(self.directory, now=NOW)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['archive_event_count'], 1)
        self.assertEqual(result['archive_head']['sequence'], 1)
        journal = json.loads((self.directory / ARCHIVE_NAME).read_text())
        self.assertEqual(len(journal['events']), 1)
        event = journal['events'][0]
        self.assertEqual(event['first_observed_at'], (NOW + timedelta(seconds=1)).isoformat())
        result2 = collect(self.directory, now=NOW + timedelta(hours=1))
        self.assertEqual(result2['archive_head'], result['archive_head'])
        self.assertEqual(result2['archive_event_count'], 1)

    def test_deleted_or_corrupt_archive_blocks_fetch_even_not_due(self):
        collect(self.directory, now=NOW)
        original = (self.directory / DATA_NAME).read_bytes()
        for raw in (None, 'bad SECRET raw json', '{}'):
            with self.subTest(raw=raw):
                path = self.directory / ARCHIVE_NAME
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(raw)
                with patch('collect_ec_industry.fetch') as fetcher:
                    result = collect(self.directory, now=NOW + timedelta(minutes=1))
                    fetcher.assert_not_called()
                self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
                self.assertEqual((self.directory / DATA_NAME).read_bytes(), original)
                self.assertEqual(result['archive_head']['sequence'], 1)
                self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())

    def test_archive_write_failure_does_not_replace_latest(self):
        collect(self.directory, now=NOW)
        data = (self.directory / DATA_NAME).read_bytes()
        history = (self.directory / ARCHIVE_NAME).read_bytes()
        with patch('collect_ec_industry.append_vintage', side_effect=OSError('SECRET disk error')):
            result = collect(self.directory, now=NOW + timedelta(hours=1))
        self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual((self.directory / DATA_NAME).read_bytes(), data)
        self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), history)
        self.assertEqual(result['last_success_at'], NOW.isoformat())
        self.assertNotIn('SECRET', (self.directory / STATUS_NAME).read_text())

    def test_interruption_after_archive_or_latest_recovers_without_duplicate(self):
        import collect_ec_industry as collector_module
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
                with patch('collect_ec_industry.atomic_json', side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        collect(self.directory, now=NOW)
                self.assertTrue((self.directory / ARCHIVE_NAME).exists())
                self.assertEqual(json.loads((self.directory / STATUS_NAME).read_text())['status'], 'attempt_started')
                state = collect(self.directory, now=NOW + timedelta(hours=1))
                self.assertEqual(state['status'], 'success')
                self.assertEqual(state['archive_event_count'], 1)
                self.assertEqual(state['archive_head']['sequence'], 1)

    def test_regressed_postfetch_clock_preserves_latest_and_history(self):
        collect(self.directory, now=NOW)
        old_data = (self.directory / DATA_NAME).read_bytes()
        old_history = (self.directory / ARCHIVE_NAME).read_bytes()
        result = collect(self.directory, now=NOW + timedelta(hours=1),
                         clock=lambda: NOW)
        self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
        self.assertEqual((self.directory / DATA_NAME).read_bytes(), old_data)
        self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), old_history)


    def test_established_archive_corrupt_status_preserves_all_evidence(self):
        collect(self.directory, now=NOW)
        history = (self.directory / ARCHIVE_NAME).read_bytes()
        latest = (self.directory / DATA_NAME).read_bytes()
        for raw in ('not JSON', '[]', 'null', '{"archive_head":{},"archive_head":null}',
                    '{"archive_head":NaN}'):
            with self.subTest(raw=raw):
                (self.directory / STATUS_NAME).write_text(raw)
                with patch('collect_ec_industry.fetch') as fetcher:
                    result = collect(self.directory, now=NOW + timedelta(days=1))
                    fetcher.assert_not_called()
                self.assertEqual(result['error_code'], 'RESEARCH_ARCHIVE_INVALID')
                self.assertEqual((self.directory / STATUS_NAME).read_text(), raw)
                self.assertEqual((self.directory / ARCHIVE_NAME).read_bytes(), history)
                self.assertEqual((self.directory / DATA_NAME).read_bytes(), latest)

if __name__ == '__main__':
    unittest.main()
