import csv
import copy
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import requests
import urllib3

from collect_mof_context import (DATA_NAME, JOURNAL_NAME, RAW_NAME, STATUS_NAME,
                                 GitPublisher, PersistenceError, collect)
from collect_niche_context import _digest, _pack_snapshot
from mof_context import SOURCE_TITLE, fetch, parse_mof
from mof_context_panel import load_context, render_mof_context

NOW = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)
VALUES = [100, 90, 10, 200, 180, 20, 30, 50, 40, 10, 40,
          300, 310, -10, 400, 390, 10, 0, 60, 50, 10, 10]


def sample(*, first_week=date(2025, 9, 14), update='September 17 , 2026',
           changed_week=None, bad_net=False, missing_week=False, early_blank=False,
           swapped=False):
    rows = [[''] * 23 for unused in range(14)]
    rows[1][0] = SOURCE_TITLE
    rows[1][19] = 'Final Update  ' + update
    rows[4][19] = 'Unit: 100 million Yen'
    rows[8][1] = '1. Portfolio Investment Assets'
    rows[8][12] = '2. Portfolio Investment Liabilities'
    categories = {1: 'Equity and investment fund shares', 4: 'Long-term debt securities',
                  7: 'Subtotal', 8: 'Short-term debt securities', 11: 'Total',
                  12: 'Equity and investment fund shares', 15: 'Long-term debt securities',
                  18: 'Subtotal', 19: 'Short-term debt securities', 22: 'Total'}
    for index, label in categories.items():
        rows[11][index] = label
    measures = ['Acquisition', 'Disposition', 'Net', 'Acquisition', 'Disposition', 'Net',
                'Net', 'Acquisition', 'Disposition', 'Net', 'Net']
    rows[13][1:] = measures * 2
    for index in range(52):
        start = first_week + timedelta(days=7 * index)
        end = start + timedelta(days=6)
        period = f'{start.year}．{start.month}．{start.day}～'
        if end.year != start.year:
            period += f'{end.year}．'
        period += f'{end.month}．{end.day}'
        values = VALUES.copy()
        if index == changed_week:
            values[0] += 1
            values[2] += 1
            values[6] += 1
            values[10] += 1
        if bad_net and index == 51:
            values[10] += 100
        rows.append([period] + [str(value) for value in values])
    if missing_week:
        rows.pop(30)
    if early_blank:
        rows.insert(-1, [''] * 23)
    if swapped:
        for row in [rows[8], rows[11], rows[13], *rows[14:]]:
            row[1:12], row[12:23] = row[12:23], row[1:12]
    rows.append([''] * 23)
    rows.append(['   (Note 1)', 'Equity and investment fund shares starting from January 2014.'] + [''] * 21)
    rows.append(['   (Note 2)', 'Totals may not add due to rounding.'] + [''] * 21)
    rows.append(['   (Note 3)', 'International Transactions in Securities show net acquisition with a plus sign; net disposition with a minus sign.'] + [''] * 21)
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue().encode('cp932')


class Response:
    def __init__(self, content, mime='text/csv', retry_after=None, status_code=200):
        self.content = content
        self.headers = {'Content-Type': mime}
        if retry_after is not None:
            self.headers['Retry-After'] = retry_after
        self.status_code = 429 if retry_after is not None else status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_code == 429:
            raise requests.HTTPError('redacted rate limit', response=self)

    def iter_content(self, chunk_size):
        yield from (self.content[index:index + chunk_size]
                    for index in range(0, len(self.content), chunk_size))

    def close(self):
        self.closed = True


class Session:
    def __init__(self, content=None, fail=False, mime='text/csv', retry_after=None,
                 status_code=200):
        self.content = content if content is not None else sample()
        self.fail = fail
        self.mime = mime
        self.retry_after = retry_after
        self.status_code = status_code
        self.calls = []
        self.response = None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise requests.ConnectionError('secret-bearing diagnostics must not persist')
        self.response = Response(self.content, self.mime, self.retry_after,
                                 self.status_code)
        return self.response


class CloseFailureBody(io.BytesIO):
    def __init__(self, close_error):
        super().__init__(b'')
        self.close_error = close_error
        self.close_attempts = 0

    def close(self):
        self.close_attempts += 1
        super().close()
        if self.close_error is not None:
            error = self.close_error
            self.close_error = None
            raise error


class RateLimitCloseSession:
    def __init__(self, header, close_error):
        self.body = CloseFailureBody(close_error)
        raw = urllib3.response.HTTPResponse(
            body=self.body, status=429, headers={'Retry-After': header}, preload_content=False)
        self.response = requests.Response()
        self.response.raw = raw
        self.response.status_code = 429
        self.response.headers = requests.structures.CaseInsensitiveDict(raw.headers)
        self.response.url = 'https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv'
        self.response.reason = 'Too Many Requests'
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class Streamlit:
    def __init__(self):
        self.messages = []

    def __getattr__(self, method):
        return lambda *args: self.messages.append((method, args))


class MofContextTests(unittest.TestCase):
    def test_pinned_official_excerpt_and_swapped_blocks(self):
        fixture = json.loads(Path(__file__).with_name(
            'mof_week_official_excerpt_2026_09_17.json').read_text(encoding='utf-8'))
        self.assertEqual(fixture['origin_sha256'],
                         'de9ba9847fdc8bebebdb8f7ee2adcc572a899296c5555d540b58a110c0f9744b')
        self.assertEqual(fixture['origin_bytes'], 255655)

        def encode_rows(rows):
            output = io.StringIO()
            csv.writer(output).writerows(rows)
            return output.getvalue().encode('cp932')

        artifact = parse_mof(encode_rows(fixture['rows']), NOW.isoformat())
        latest = artifact['observations'][-1]
        self.assertEqual((latest['week_end'], latest['values'][10], latest['values'][21]),
                         ('2026-09-12', 14029, -4995))
        swapped = copy.deepcopy(fixture['rows'])
        for row in [swapped[8], swapped[11], swapped[13], *swapped[14:66]]:
            row[1:12], row[12:23] = row[12:23], row[1:12]
        with self.assertRaises(ValueError):
            parse_mof(encode_rows(swapped), NOW.isoformat())

    def test_official_excerpt_renders_correct_sides_and_attribution(self):
        fixture = json.loads(Path(__file__).with_name(
            'mof_week_official_excerpt_2026_09_17.json').read_text(encoding='utf-8'))
        output = io.StringIO()
        csv.writer(output).writerows(fixture['rows'])
        artifact = parse_mof(output.getvalue().encode('cp932'), NOW.isoformat())
        st = Streamlit()
        with patch('mof_context_panel.load_context', return_value=(
                artifact, {'last_success_at': NOW.isoformat()},
                [{'revision_week_ends': []}])):
            render_mof_context(st, 'unused')
        values = [arguments[0] for method, arguments in st.messages if method == 'write']
        self.assertEqual(len(values), 1)
        self.assertIn('japanischer Anleger im Ausland: 14.029', values[0])
        self.assertIn('ausländischer Anleger in Japan: -4.995', values[0])
        sources = ' '.join(arguments[0] for method, arguments in st.messages
                           if method == 'markdown')
        self.assertIn('week.csv', sources)
        self.assertIn('20240705_resources_data_outline_05.pdf', sources)

    def test_exact_identity_arithmetic_sign_boundary_and_chronology(self):
        parsed = parse_mof(sample(), NOW.isoformat())
        self.assertEqual(len(parsed['observations']), 52)
        self.assertEqual(parsed['observations'][-1]['week_end'], '2026-09-12')
        self.assertEqual(parsed['observations'][-1]['values'][10], 40)
        self.assertEqual(parsed['observations'][-1]['values'][21], 10)
        for raw in (sample(bad_net=True), sample(missing_week=True),
                    sample(first_week=date(2013, 1, 6)), sample(early_blank=True),
                    sample(swapped=True),
                    sample(update='October 1 , 2026'),
                    sample().replace(b'net acquisition with a plus sign',
                                     b'net acquisition with a minus sign'),
                    sample().replace(b'100 million Yen', b'1000 million Yen'),
                    b'<html>blocked</html>'):
            with self.assertRaises(ValueError):
                parse_mof(raw, NOW.isoformat())
        with self.assertRaises(ValueError):
            parse_mof(sample(), datetime(2026, 9, 16, 23, 30,
                                         tzinfo=timezone.utc).isoformat())

    def test_fetch_preserves_http_error_despite_close_failure(self):
        for close_error in (OSError('Close I/O failure'), RuntimeError('Close failure'),
                            requests.ConnectionError('Close connection failure')):
            with self.subTest(close_error=type(close_error).__name__):
                client = RateLimitCloseSession('172800', close_error)
                with self.assertRaises(requests.HTTPError) as raised:
                    fetch(session=client, now=NOW)
                self.assertIs(raised.exception.response, client.response)
                self.assertEqual(raised.exception.response.headers['Retry-After'], '172800')
                self.assertEqual(client.body.close_attempts, 1)
                self.assertEqual(len(client.calls), 1)

    def test_fetch_preserves_parse_error_despite_close_failure(self):
        response = Response(b'not a MoF CSV')
        client = Session()
        with patch.object(client, 'get', return_value=response) as request:
            with patch.object(response, 'close', side_effect=OSError('Close I/O failure')) as close:
                with self.assertRaisesRegex(ValueError, 'MoF'):
                    fetch(session=client, now=NOW)
        request.assert_called_once()
        close.assert_called_once()

    def test_fetch_does_not_hide_close_failure_after_success(self):
        response = Response(sample())
        client = Session()
        with patch.object(client, 'get', return_value=response) as request:
            with patch.object(response, 'close', side_effect=OSError('Close I/O failure')) as close:
                with self.assertRaisesRegex(OSError, 'Close I/O failure'):
                    fetch(session=client, now=NOW)
        request.assert_called_once()
        close.assert_called_once()

    def test_prospective_revision_and_point_in_time(self):
        with tempfile.TemporaryDirectory() as temp:
            first = collect(temp, now=NOW, session=Session())
            self.assertEqual(first['status'], 'success')
            self.assertEqual(first['archive_event_count'], 1)
            initial = load_context(temp, now=NOW)[0]
            self.assertEqual(initial['observations'][-1]['values'][10], 40)
            self.assertEqual(first['last_success_at'], initial['first_observed_at'])
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW - timedelta(seconds=1))
            unchanged = collect(temp, now=NOW + timedelta(days=1), session=Session())
            self.assertEqual(unchanged['archive_event_count'], 1)
            revised_time = NOW + timedelta(days=2)
            revised = collect(temp, now=revised_time, session=Session(sample(changed_week=51)))
            self.assertEqual(revised['archive_event_count'], 2)
            events = json.loads(Path(temp, JOURNAL_NAME).read_text())['events']
            self.assertEqual(events[-1]['revision_week_ends'], ['2026-09-12'])
            self.assertEqual(events[-1]['added_week_ends'], [])
            self.assertEqual(events[-1]['first_observed_at'], revised_time.isoformat())
            self.assertEqual(load_context(temp, now=revised_time)[0]['observations'][-1]['values'][10], 41)

    def test_failure_and_corruption_hide_values(self):
        with tempfile.TemporaryDirectory() as temp:
            collect(temp, now=NOW, session=Session())
            original = Path(temp, DATA_NAME).read_bytes()
            failed = collect(temp, now=NOW + timedelta(days=1), session=Session(fail=True))
            self.assertEqual(failed['error_code'], 'MOF_REQUEST_FAILED')
            self.assertEqual(Path(temp, DATA_NAME).read_bytes(), original)
            self.assertNotIn('secret-bearing', Path(temp, STATUS_NAME).read_text())
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW + timedelta(days=1))
            st = Streamlit()
            render_mof_context(st, temp)
            self.assertFalse(any(method == 'write' for method, unused in st.messages))
            collect(temp, now=NOW + timedelta(days=2), session=Session())
            journal_path = Path(temp, JOURNAL_NAME)
            journal = json.loads(journal_path.read_text())
            journal['events'][0]['event_hash'] = '0' * 64
            journal_path.write_text(json.dumps(journal))
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW + timedelta(days=2))

    def test_period_rollback_preserves_validated_files_and_hides_values(self):
        for update in ('September 17 , 2026', 'September 18 , 2026'):
            with self.subTest(update=update), tempfile.TemporaryDirectory() as temp:
                collect(temp, now=NOW, session=Session())
                originals = {name: Path(temp, name).read_bytes()
                             for name in (DATA_NAME, RAW_NAME, JOURNAL_NAME)}
                later = NOW + timedelta(days=1)
                regressed = collect(temp, now=later, session=Session(sample(
                    first_week=date(2025, 9, 7), update=update)))
                self.assertEqual(regressed['status'], 'failed')
                self.assertEqual(regressed['error_code'], 'MOF_RESPONSE_OR_ARCHIVE_INVALID')
                for name, original in originals.items():
                    self.assertEqual(Path(temp, name).read_bytes(), original)
                with self.assertRaises(ValueError):
                    load_context(temp, now=later)
                recovered_at = NOW + timedelta(days=2)
                recovered = collect(temp, now=recovered_at, session=Session(sample(
                    first_week=date(2025, 9, 21), update='September 24 , 2026')))
                self.assertEqual(recovered['status'], 'success')
                self.assertEqual(load_context(temp, now=recovered_at)[0]['observations'][-1]['week_end'],
                                 '2026-09-19')

    def test_rehashed_period_rollback_in_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            status = collect(temp, now=NOW, session=Session())
            later = NOW + timedelta(days=1)
            raw = sample(first_week=date(2025, 9, 7), update='September 18 , 2026')
            artifact = parse_mof(raw, later.isoformat())
            journal = json.loads(Path(temp, JOURNAL_NAME).read_text())
            event = {'sequence': 2, 'previous_hash': status['archive_head'],
                     'first_observed_at': later.isoformat(), 'published_at': None,
                     'mode': 'research_only', 'core_eligible': False, 'signal': None,
                     'state_hash': _digest(artifact), 'snapshot': _pack_snapshot(artifact),
                     'revision_week_ends': [], 'added_week_ends': ['2025-09-13']}
            event['event_hash'] = _digest(event)
            journal['events'].append(event)
            status.update(archive_head=event['event_hash'], archive_event_count=2,
                          last_attempt_at=later.isoformat(), last_success_at=later.isoformat(),
                          attempt_completed_at=later.isoformat(),
                          next_allowed_at=(later + timedelta(hours=20)).isoformat(),
                          source_sha256=artifact['source_sha256'])
            Path(temp, RAW_NAME).write_bytes(raw)
            for name, content in ((DATA_NAME, artifact), (JOURNAL_NAME, journal),
                                  (STATUS_NAME, status)):
                Path(temp, name).write_text(json.dumps(content))
            with self.assertRaisesRegex(ValueError, 'period moved backwards'):
                load_context(temp, now=later)
            client = Session()
            result = collect(temp, now=later + timedelta(days=1), session=client)
            self.assertEqual(result['error_code'], 'MOF_ARCHIVE_INVALID')
            self.assertEqual(client.calls, [])

    def test_corrupt_status_blocks_collection_and_display_without_erasing_evidence(self):
        corruptions = (
            {'next_allowed_at': 'damaged-timestamp'},
            {'next_allowed_at': None},
            {'next_allowed_at': (NOW + timedelta(hours=1)).isoformat()},
            {'last_attempt_at': (NOW + timedelta(days=2)).isoformat()},
            {'last_attempt_at': NOW.replace(tzinfo=None).isoformat()},
            {'last_success_at': None},
            {'last_success_at': (NOW + timedelta(days=2)).isoformat()},
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                status = collect(temp, now=NOW, session=Session())
                status.update(corruption)
                Path(temp, STATUS_NAME).write_text(json.dumps(status))
                originals = {name: Path(temp, name).read_bytes()
                             for name in (DATA_NAME, RAW_NAME, JOURNAL_NAME, STATUS_NAME)}
                later = NOW + timedelta(days=1)
                client = Session()
                result = collect(temp, now=later, session=client)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error_code'], 'MOF_ARCHIVE_INVALID')
                self.assertEqual(client.calls, [])
                for name, original in originals.items():
                    self.assertEqual(Path(temp, name).read_bytes(), original)
                with self.assertRaises(ValueError):
                    load_context(temp, now=later)
                st = Streamlit()
                with patch('mof_context_panel.load_context',
                           side_effect=lambda directory: load_context(directory, now=later)):
                    render_mof_context(st, temp)
                self.assertTrue(any(method == 'warning' for method, unused in st.messages))
                self.assertFalse(any(method == 'write' for method, unused in st.messages))

    def test_post_fetch_success_time_remains_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            completed = NOW + timedelta(seconds=10)
            with patch('collect_mof_context.datetime', wraps=datetime) as clock:
                clock.now.side_effect = [NOW, completed, completed]
                result = collect(temp, session=Session())
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['last_attempt_at'], NOW.isoformat())
            self.assertEqual(result['last_success_at'], completed.isoformat())
            self.assertEqual(load_context(temp, now=completed)[1]['status'], 'success')
            client = Session()
            blocked = collect(temp, now=completed + timedelta(hours=1), session=client)
            self.assertEqual(blocked['collection_action'], 'not_due')
            self.assertEqual(client.calls, [])

    def test_interrupted_publish_preserves_history_and_stays_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('collect_mof_context._atomic_csv', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    collect(temp, now=NOW, session=Session())
            status = json.loads(Path(temp, STATUS_NAME).read_text())
            self.assertEqual(status['status'], 'attempt_started')
            self.assertIsNone(status['archive_head'])
            original_journal = Path(temp, JOURNAL_NAME).read_bytes()
            client = Session()
            blocked = collect(temp, now=NOW + timedelta(hours=1), session=client)
            self.assertEqual(blocked['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
            self.assertEqual(client.calls, [])
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW + timedelta(hours=1))
            original_status = Path(temp, STATUS_NAME).read_bytes()
            for later in (NOW + timedelta(hours=20, seconds=1), NOW + timedelta(days=60)):
                with self.subTest(later=later):
                    client = Session()
                    blocked = collect(temp, now=later, session=client)
                    self.assertEqual(blocked['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
                    self.assertEqual(blocked['collection_action'], 'blocked_incomplete')
                    self.assertEqual(client.calls, [])
                    self.assertEqual(Path(temp, STATUS_NAME).read_bytes(), original_status)
                    self.assertEqual(Path(temp, JOURNAL_NAME).read_bytes(), original_journal)
                    with self.assertRaises(ValueError):
                        load_context(temp, now=later)

    def test_failed_request_has_cooldown_without_reattempt(self):
        with tempfile.TemporaryDirectory() as temp:
            failed = collect(temp, now=NOW, session=Session(fail=True))
            self.assertEqual(failed['status'], 'failed')
            retry_session = Session()
            blocked = collect(temp, now=NOW + timedelta(hours=1), session=retry_session)
            self.assertEqual(blocked['collection_action'], 'not_due')
            self.assertEqual(blocked['error_code'], 'MOF_REQUEST_FAILED')
            self.assertEqual(retry_session.calls, [])
            recovered = collect(temp, now=NOW + timedelta(hours=20, seconds=1),
                                session=Session())
            self.assertEqual(recovered['status'], 'success')

    def test_429_retry_after_and_incomplete_attempt_are_not_retried_early(self):
        with tempfile.TemporaryDirectory() as temp:
            limited = Session(retry_after='172800')
            failed = collect(temp, now=NOW, session=limited)
            self.assertEqual(failed['next_allowed_at'],
                             (NOW + timedelta(hours=48)).isoformat())
            self.assertTrue(limited.response.closed)
            retry_session = Session()
            blocked = collect(temp, now=NOW + timedelta(hours=24), session=retry_session)
            self.assertEqual(blocked['status'], 'failed')
            self.assertEqual(blocked['collection_action'], 'not_due')
            self.assertEqual(retry_session.calls, [])
            status_path = Path(temp, STATUS_NAME)
            status = json.loads(status_path.read_text())
            status['status'] = 'attempt_started'
            status['error_code'] = None
            if 'attempt_completed_at' in status:
                status['attempt_completed_at'] = None
            status_path.write_text(json.dumps(status))
            incomplete_session = Session()
            incomplete = collect(temp, now=NOW + timedelta(hours=25),
                                 session=incomplete_session)
            self.assertEqual(incomplete['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
            self.assertEqual(incomplete_session.calls, [])
            original = status_path.read_bytes()
            late_session = Session()
            blocked = collect(temp, now=NOW + timedelta(days=60), session=late_session)
            self.assertEqual(blocked['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
            self.assertEqual(blocked['collection_action'], 'blocked_incomplete')
            self.assertEqual(late_session.calls, [])
            self.assertEqual(status_path.read_bytes(), original)
        with tempfile.TemporaryDirectory() as temp:
            date_header = format_datetime(NOW + timedelta(hours=72))
            failed = collect(temp, now=NOW, session=Session(retry_after=date_header))
            self.assertEqual(failed['next_allowed_at'],
                             (NOW + timedelta(hours=72)).isoformat())
        with tempfile.TemporaryDirectory() as temp:
            failed = collect(temp, now=NOW, session=Session(retry_after='invalid'))
            self.assertEqual(failed['next_allowed_at'],
                             (NOW + timedelta(days=1)).isoformat())
        with tempfile.TemporaryDirectory() as temp:
            failed = collect(temp, now=NOW, session=Session(retry_after='0'))
            self.assertEqual(failed['next_allowed_at'],
                             (NOW + timedelta(hours=20)).isoformat())

    def test_legacy_open_attempt_without_id_never_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            state = collect(temp, now=NOW, session=Session())
            state['status'] = 'attempt_started'
            state.pop('attempt_id', None)
            state.pop('attempt_completed_at', None)
            path = Path(temp, STATUS_NAME)
            path.write_text(json.dumps(state))
            original = path.read_bytes()
            client = Session()
            result = collect(temp, now=NOW + timedelta(days=60), session=client)
            self.assertEqual(result['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
            self.assertEqual(result['collection_action'], 'blocked_incomplete')
            self.assertEqual(client.calls, [])
            self.assertEqual(path.read_bytes(), original)

    def test_cooldown_starts_after_processing_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            observed = NOW + timedelta(hours=1)
            completed = NOW + timedelta(hours=2)
            with patch('collect_mof_context.datetime', wraps=datetime) as clock:
                clock.now.side_effect = [NOW, observed, completed]
                result = collect(temp, session=Session())
            self.assertEqual(result['attempt_completed_at'], completed.isoformat())
            self.assertEqual(result['next_allowed_at'],
                             (completed + timedelta(hours=20)).isoformat())
            self.assertEqual(load_context(temp, now=completed)[1]['status'], 'success')

    def test_stale_source_and_bounded_network(self):
        with tempfile.TemporaryDirectory() as temp:
            collect(temp, now=NOW, session=Session())
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW + timedelta(days=8))
            later = NOW + timedelta(days=22)
            collect(temp, now=later, session=Session())
            with self.assertRaises(ValueError):
                load_context(temp, now=later)
        with tempfile.TemporaryDirectory() as temp:
            oversized = Session(b'x' * 500_001)
            result = collect(temp, now=NOW, session=oversized)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(oversized.response.closed)
            self.assertEqual(result['error_code'], 'MOF_RESPONSE_OR_ARCHIVE_INVALID')
        with tempfile.TemporaryDirectory() as temp:
            html = collect(temp, now=NOW, session=Session(sample(), mime='text/html'))
            self.assertEqual(html['status'], 'failed')
        with tempfile.TemporaryDirectory() as temp:
            redirect = Session(status_code=302)
            result = collect(temp, now=NOW, session=redirect)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(redirect.calls[0][1]['allow_redirects'])

    def test_invalid_attempt_metadata_blocks_collection_and_display(self):
        corruptions = (
            {'attempt_id': None},
            {'attempt_id': 'not-an-attempt-id'},
            {'attempt_completed_at': None},
            {'attempt_completed_at': 'damaged-timestamp'},
            {'attempt_completed_at': (NOW - timedelta(seconds=1)).isoformat()},
            {'attempt_completed_at': (NOW + timedelta(days=3)).isoformat()},
            {'attempt_completed_at': (NOW + timedelta(hours=1)).isoformat()},
            {'status': 'attempt_started'},
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temp:
                status = collect(temp, now=NOW, session=Session())
                status.update(corruption)
                Path(temp, STATUS_NAME).write_text(json.dumps(status))
                originals = {name: Path(temp, name).read_bytes()
                             for name in (STATUS_NAME, DATA_NAME, RAW_NAME, JOURNAL_NAME)}
                later = NOW + timedelta(days=2)
                client = Session()
                result = collect(temp, now=later, session=client)
                self.assertEqual(result['error_code'], 'MOF_ARCHIVE_INVALID')
                self.assertEqual(client.calls, [])
                with self.assertRaises(ValueError):
                    load_context(temp, now=later)
                for name, original in originals.items():
                    self.assertEqual(Path(temp, name).read_bytes(), original)
        for missing in ('attempt_id', 'attempt_completed_at'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                status = collect(temp, now=NOW, session=Session())
                del status[missing]
                Path(temp, STATUS_NAME).write_text(json.dumps(status))
                client = Session()
                result = collect(temp, now=NOW + timedelta(days=1), session=client)
                self.assertEqual(result['error_code'], 'MOF_ARCHIVE_INVALID')
                self.assertEqual(client.calls, [])

    def test_valid_legacy_terminal_statuses_migrate_without_losing_history(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                status = collect(temp, now=NOW, session=Session(fail=fail))
                del status['attempt_id']
                del status['attempt_completed_at']
                Path(temp, STATUS_NAME).write_text(json.dumps(status))
                if not fail:
                    self.assertEqual(load_context(temp, now=NOW)[1], status)
                client = Session()
                later = NOW + timedelta(days=1)
                result = collect(temp, now=later, session=client)
                self.assertEqual(result['status'], 'success')
                self.assertEqual(len(client.calls), 1)
                self.assertRegex(result['attempt_id'], '^[0-9a-f]{32}$')
                self.assertEqual(result['attempt_completed_at'], later.isoformat())
                self.assertEqual(result['archive_event_count'], 1)
                self.assertEqual(load_context(temp, now=later)[1], result)

    def test_workflow_uses_durable_publication_without_later_push(self):
        workflow = Path(__file__).with_name('.github') / 'workflows' / 'mof_context.yml'
        source = workflow.read_text()
        self.assertIn('python collect_mof_context.py --publish', source)
        self.assertIn('fetch-depth: 0', source)
        self.assertIn('group: g10-live-data-collection', source)
        self.assertNotIn('continue-on-error:', source)
        self.assertNotIn('git push', source)


class MofDurableAttemptTests(unittest.TestCase):
    """Exercise real Git transitions against temporary local bare remotes only."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.remote = self.root / 'remote.git'
        self.git(self.root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        seed = self.runner()
        (seed / '.gitignore').write_text('research_data/.mof_context.lock\n')
        (seed / 'README.md').write_text('Local-only MoF persistence test.\n')
        self.git(seed, 'add', '.')
        self.git(seed, 'commit', '-m', 'Seed local test repository')
        self.git(seed, 'push', 'origin', 'HEAD:refs/heads/main')
        self.base = self.git(seed, 'rev-parse', 'HEAD')

    def git(self, directory, *args):
        return subprocess.run(['git', *args], cwd=directory, check=True,
                              capture_output=True, text=True, timeout=15).stdout.strip()

    def runner(self):
        path = self.root / f'runner-{len(list(self.root.glob("runner-*")))}'
        self.git(self.root, 'clone', str(self.remote), str(path))
        self.git(path, 'config', 'user.name', 'Local MoF test')
        self.git(path, 'config', 'user.email', 'mof-test@example.invalid')
        return path

    def remote_status(self):
        return json.loads(self.git(self.remote, 'show', f'main:research_data/{STATUS_NAME}'))

    def advance_main(self):
        other = self.runner()
        (other / 'unrelated.txt').write_text('Concurrent main update\n')
        self.git(other, 'add', 'unrelated.txt')
        self.git(other, 'commit', '-m', 'Concurrent local test update')
        self.git(other, 'push', 'origin', 'HEAD:refs/heads/main')

    def assert_fresh_runner_blocked(self, *, now=NOW + timedelta(days=60)):
        runner = self.runner()
        directory = runner / 'research_data'
        before = (directory / STATUS_NAME).read_bytes()
        session = Session()
        result = collect(directory, now=now, session=session,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
        self.assertEqual(result['collection_action'], 'blocked_incomplete')
        self.assertEqual(session.calls, [])
        self.assertEqual((directory / STATUS_NAME).read_bytes(), before)
        self.assertEqual(self.remote_status()['status'], 'attempt_started')

    def test_get_sees_published_reservation_and_completion_survives_fresh_runner(self):
        runner = self.runner()
        directory = runner / 'research_data'
        test = self

        class InspectSession(Session):
            def get(self, *args, **kwargs):
                state = test.remote_status()
                test.assertEqual(state['status'], 'attempt_started')
                test.assertTrue(state['attempt_id'])
                changed = test.git(test.remote, 'diff', '--name-only', test.base, 'main')
                test.assertEqual(changed, f'research_data/{STATUS_NAME}')
                return super().get(*args, **kwargs)

        session = InspectSession()
        result = collect(directory, now=NOW, session=session,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['status'], 'success')
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(self.remote_status(), result)
        self.assertEqual(int(self.git(self.remote, 'rev-list', '--count',
                                     f'{self.base}..main')), 2)
        fresh = self.runner()
        load_context(fresh / 'research_data', now=NOW)
        blocked_session = Session()
        blocked = collect(fresh / 'research_data', now=NOW + timedelta(hours=19),
                          session=blocked_session,
                          publisher=GitPublisher(fresh / 'research_data'))
        self.assertEqual(blocked['collection_action'], 'not_due')
        self.assertEqual(blocked_session.calls, [])
        due_session = Session()
        due = collect(fresh / 'research_data', now=NOW + timedelta(hours=20),
                      session=due_session,
                      publisher=GitPublisher(fresh / 'research_data'))
        self.assertEqual(due['status'], 'success')
        self.assertNotEqual(due['attempt_id'], result['attempt_id'])
        self.assertEqual(len(due_session.calls), 1)

    def test_sigkill_after_reservation_and_after_get_survives_runner_loss(self):
        script = '''
import os
import signal
import sys
import socket
import requests
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('CHILD_OFFLINE_NETWORK_ATTEMPT')

requests.adapters.HTTPAdapter.send = forbidden
socket.create_connection = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.getaddrinfo = forbidden
from collect_mof_context import GitPublisher, collect
from test_mof_context import NOW, Session
directory, phase, calls = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
class KillPublisher(GitPublisher):
    def reserve(self, state):
        if phase == 'before_publish':
            os.kill(os.getpid(), signal.SIGKILL)
        super().reserve(state)
        if phase == 'before_get':
            os.kill(os.getpid(), signal.SIGKILL)
class KillSession(Session):
    def get(self, *args, **kwargs):
        response = super().get(*args, **kwargs)
        calls.write_text('1')
        os.kill(os.getpid(), signal.SIGKILL)
collect(directory, now=NOW, session=KillSession(), publisher=KillPublisher(directory))
'''
        for phase in ('before_publish', 'before_get', 'after_get'):
            with self.subTest(phase=phase):
                if phase != 'before_publish':
                    self.remote = self.root / f'{phase}-remote.git'
                    self.git(self.root, 'clone', '--bare', str(self.root / 'remote.git'),
                             str(self.remote))
                    self.git(self.remote, 'update-ref', 'refs/heads/main', self.base)
                runner = self.runner()
                calls = self.root / f'{phase}-calls'
                process = subprocess.run(
                    [sys.executable, '-B', '-c', script, str(runner / 'research_data'),
                     phase, str(calls)], cwd=Path(__file__).parent,
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(process.returncode, -signal.SIGKILL, process.stderr)
                self.assertEqual(calls.exists(), phase == 'after_get')
                if phase == 'before_publish':
                    self.assertNotIn(STATUS_NAME, self.git(
                        self.remote, 'ls-tree', '-r', '--name-only', 'main'))
                    fresh = self.runner()
                    session = Session()
                    result = collect(fresh / 'research_data', now=NOW, session=session,
                                     publisher=GitPublisher(fresh / 'research_data'))
                    self.assertEqual(result['status'], 'success')
                    self.assertEqual(len(session.calls), 1)
                    continue
                self.assertEqual(self.remote_status()['status'], 'attempt_started')
                self.assert_fresh_runner_blocked(now=NOW + timedelta(minutes=5))
                self.assert_fresh_runner_blocked()

    def test_reservation_push_conflict_prevents_get(self):
        runner = self.runner()
        directory = runner / 'research_data'
        test = self

        class ConflictPublisher(GitPublisher):
            def reserve(self, state):
                test.advance_main()
                super().reserve(state)

        session = Session()
        result = collect(directory, now=NOW, session=session,
                         publisher=ConflictPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(session.calls, [])
        files = self.git(self.remote, 'ls-tree', '-r', '--name-only', 'main')
        self.assertNotIn(f'research_data/{STATUS_NAME}', files)
        self.assertIn('unrelated.txt', files)

    def test_unconfirmed_successful_reservation_does_not_start_get(self):
        runner = self.runner()
        directory = runner / 'research_data'

        class UnconfirmedPublisher(GitPublisher):
            def reserve(self, state):
                super().reserve(state)
                raise PersistenceError('Push acknowledgement lost')

        session = Session()
        result = collect(directory, now=NOW, session=session,
                         publisher=UnconfirmedPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(session.calls, [])
        self.assert_fresh_runner_blocked()

    def test_competing_runner_reserves_first_and_only_one_get_is_made(self):
        runner = self.runner()
        directory = runner / 'research_data'
        other = self.runner()
        other_session = Session()
        test = self

        class LosingPublisher(GitPublisher):
            def reserve(self, state):
                winner = collect(other / 'research_data', now=NOW, session=other_session,
                                 publisher=GitPublisher(other / 'research_data'))
                test.assertEqual(winner['status'], 'success')
                super().reserve(state)

        session = Session()
        result = collect(directory, now=NOW, session=session,
                         publisher=LosingPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(session.calls, [])
        self.assertEqual(len(other_session.calls), 1)
        self.assertEqual(self.remote_status()['status'], 'success')

    def test_completion_push_conflict_keeps_remote_and_local_attempt_open(self):
        runner = self.runner()
        directory = runner / 'research_data'
        test = self

        class ConflictSession(Session):
            def get(self, *args, **kwargs):
                response = super().get(*args, **kwargs)
                test.advance_main()
                return response

        session = ConflictSession()
        result = collect(directory, now=NOW, session=session,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(json.loads((directory / STATUS_NAME).read_text())['status'],
                         'attempt_started')
        self.assertIn('unrelated.txt', self.git(self.remote, 'ls-tree', '-r',
                                              '--name-only', 'main'))
        self.assert_fresh_runner_blocked()

    def check_durable_retry_after(self, header, hours, close_error=None):
        runner = self.runner()
        directory = runner / 'research_data'
        client = (RateLimitCloseSession(header, close_error) if close_error is not None
                  else Session(retry_after=header))
        result = collect(directory, now=NOW, session=client,
                         publisher=GitPublisher(directory))
        deadline = NOW + timedelta(hours=hours)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'MOF_REQUEST_FAILED')
        self.assertEqual(len(client.calls), 1)
        if close_error is not None:
            self.assertEqual(client.body.close_attempts, 1)
        self.assertEqual(self.remote_status(), result)
        self.assertEqual(self.remote_status()['next_allowed_at'], deadline.isoformat())
        initial_attempt_id = result['attempt_id']
        fresh = self.runner()
        original = (fresh / 'research_data' / STATUS_NAME).read_bytes()
        session = Session()
        for observed in (NOW + timedelta(hours=24), deadline - timedelta(seconds=1)):
            with self.subTest(observed=observed):
                blocked = collect(fresh / 'research_data', now=observed,
                                  session=session, publisher=GitPublisher(fresh / 'research_data'))
                self.assertEqual(blocked['collection_action'], 'not_due')
                self.assertEqual(blocked['next_allowed_at'], deadline.isoformat())
                self.assertEqual(session.calls, [])
                self.assertEqual((fresh / 'research_data' / STATUS_NAME).read_bytes(), original)
        result = collect(fresh / 'research_data', now=deadline, session=session,
                         publisher=GitPublisher(fresh / 'research_data'))
        self.assertEqual(result['status'], 'success')
        self.assertNotEqual(result['attempt_id'], initial_attempt_id)
        self.assertEqual(len(session.calls), 1)

    def test_retry_after_seconds_survives_fresh_runner(self):
        self.check_durable_retry_after('172800', 48)

    def test_retry_after_http_date_survives_fresh_runner(self):
        self.check_durable_retry_after(format_datetime(NOW + timedelta(hours=72)), 72)

    def test_retry_after_48h_survives_close_failure_and_fresh_runner(self):
        self.check_durable_retry_after('172800', 48, OSError('Close I/O failure'))

    def test_retry_after_http_date_survives_close_failure_and_fresh_runner(self):
        self.check_durable_retry_after(format_datetime(NOW + timedelta(hours=72)), 72,
                                       RuntimeError('Close failure'))

    def test_interrupted_response_with_close_failure_keeps_attempt_open(self):
        runner = self.runner()
        directory = runner / 'research_data'
        response = Response(sample())
        client = Session()
        with patch.object(client, 'get', return_value=response) as request:
            with patch.object(response, 'iter_content', side_effect=KeyboardInterrupt):
                with patch.object(response, 'close', side_effect=OSError('Close I/O failure')) as close:
                    with self.assertRaises(KeyboardInterrupt):
                        collect(directory, now=NOW, session=client,
                                publisher=GitPublisher(directory))
        request.assert_called_once()
        close.assert_called_once()
        self.assertEqual(self.remote_status()['status'], 'attempt_started')
        self.assert_fresh_runner_blocked()

    def test_interruption_before_429_completion_publish_does_not_expire(self):
        class Interrupted(BaseException):
            pass

        class InterruptedPublisher(GitPublisher):
            def finish(self, state):
                raise Interrupted()

        runner = self.runner()
        directory = runner / 'research_data'
        session = Session(retry_after='172800')
        with self.assertRaises(Interrupted):
            collect(directory, now=NOW, session=session,
                    publisher=InterruptedPublisher(directory))
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.response.closed)
        self.assertEqual(json.loads((directory / STATUS_NAME).read_text())['next_allowed_at'],
                         (NOW + timedelta(hours=48)).isoformat())
        self.assert_fresh_runner_blocked()

    def test_unpublished_local_completion_cannot_bypass_remote_open_attempt(self):
        class Interrupted(BaseException):
            pass

        class InterruptedPublisher(GitPublisher):
            def _git(self, *args):
                if args[:1] == ('push',) and self.reservation is not None:
                    raise Interrupted()
                return super()._git(*args)

        runner = self.runner()
        directory = runner / 'research_data'
        with self.assertRaises(Interrupted):
            collect(directory, now=NOW, session=Session(),
                    publisher=InterruptedPublisher(directory))
        self.assertEqual(json.loads((directory / STATUS_NAME).read_text())['status'], 'success')
        self.assertEqual(self.git(runner, 'status', '--porcelain'), '')
        session = Session()
        result = collect(directory, now=NOW + timedelta(days=30), session=session,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(session.calls, [])
        self.assert_fresh_runner_blocked()

    def test_429_completion_push_conflict_keeps_unknown_attempt_blocked(self):
        runner = self.runner()
        directory = runner / 'research_data'
        test = self

        class ConflictSession(Session):
            def get(self, *args, **kwargs):
                response = super().get(*args, **kwargs)
                test.advance_main()
                return response

        client = ConflictSession(retry_after='172800')
        result = collect(directory, now=NOW, session=client,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.response.closed)
        self.assertEqual(self.remote_status()['status'], 'attempt_started')
        self.assertEqual(json.loads((directory / STATUS_NAME).read_text())['status'],
                         'attempt_started')
        self.assert_fresh_runner_blocked(now=NOW + timedelta(hours=24))
        self.assert_fresh_runner_blocked()

    def test_unconfirmed_completion_preserves_remote_cooldown(self):
        class UnconfirmedPublisher(GitPublisher):
            def finish(self, state):
                super().finish(state)
                raise PersistenceError('Completion acknowledgement lost')

        runner = self.runner()
        directory = runner / 'research_data'
        client = Session(retry_after='172800')
        result = collect(directory, now=NOW, session=client,
                         publisher=UnconfirmedPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(json.loads((directory / STATUS_NAME).read_text())['status'],
                         'attempt_started')
        self.assertEqual(self.remote_status()['status'], 'failed')
        self.assertEqual(self.remote_status()['next_allowed_at'],
                         (NOW + timedelta(hours=48)).isoformat())
        fresh = self.runner()
        client = Session()
        blocked = collect(fresh / 'research_data', now=NOW + timedelta(hours=24),
                          session=client, publisher=GitPublisher(fresh / 'research_data'))
        self.assertEqual(blocked['collection_action'], 'not_due')
        self.assertEqual(client.calls, [])

    def test_unexpected_staged_files_prevent_reservation_and_get(self):
        runner = self.runner()
        directory = runner / 'research_data'
        test = self

        class StagedPublisher(GitPublisher):
            def reserve(self, state):
                (runner / 'unrelated.txt').write_text('Must not be published\n')
                test.git(runner, 'add', '--', 'unrelated.txt')
                super().reserve(state)

        client = Session()
        result = collect(directory, now=NOW, session=client,
                         publisher=StagedPublisher(directory))
        self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
        self.assertEqual(client.calls, [])
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), self.base)

    def test_dirty_or_detached_checkout_prevents_get(self):
        for condition in ('dirty', 'detached'):
            with self.subTest(condition=condition):
                runner = self.runner()
                if condition == 'dirty':
                    (runner / 'README.md').write_text('Uncommitted operator changes\n')
                else:
                    self.git(runner, 'checkout', '--detach')
                directory = runner / 'research_data'
                client = Session()
                result = collect(directory, now=NOW, session=client,
                                 publisher=GitPublisher(directory))
                self.assertEqual(result['error_code'], 'MOF_PERSISTENCE_FAILED')
                self.assertEqual(client.calls, [])
                self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), self.base)

    def test_published_open_attempt_hides_previous_valid_values(self):
        runner = self.runner()
        directory = runner / 'research_data'
        initial = collect(directory, now=NOW, session=Session(),
                          publisher=GitPublisher(directory))
        self.assertEqual(initial['status'], 'success')
        test = self
        later = NOW + timedelta(days=1)

        class InspectSession(Session):
            def get(self, *args, **kwargs):
                fresh = test.runner()
                with test.assertRaisesRegex(ValueError, 'not successful'):
                    load_context(fresh / 'research_data', now=later)
                return super().get(*args, **kwargs)

        client = InspectSession()
        result = collect(directory, now=later, session=client,
                         publisher=GitPublisher(directory))
        self.assertEqual(result['status'], 'success')
        self.assertNotEqual(initial['attempt_id'], result['attempt_id'])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(load_context(directory, now=later)[1], result)


if __name__ == '__main__':
    unittest.main()
