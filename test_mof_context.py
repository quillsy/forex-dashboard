import csv
import copy
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import requests

from collect_mof_context import DATA_NAME, JOURNAL_NAME, STATUS_NAME, collect
from mof_context import SOURCE_TITLE, parse_mof
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
            status_path.write_text(json.dumps(status))
            incomplete_session = Session()
            incomplete = collect(temp, now=NOW + timedelta(hours=25),
                                 session=incomplete_session)
            self.assertEqual(incomplete['error_code'], 'MOF_PRIOR_ATTEMPT_INCOMPLETE')
            self.assertEqual(incomplete_session.calls, [])
            recovered = collect(temp, now=NOW + timedelta(hours=48, seconds=1),
                                session=Session())
            self.assertEqual(recovered['status'], 'success')
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


if __name__ == '__main__':
    unittest.main()
