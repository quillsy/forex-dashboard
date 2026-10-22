import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import collect_niche_context as collector

from collect_niche_context import collect, DATA_NAME, JOURNAL_NAME, STATUS_NAME, _pack_snapshot, _unpack_snapshot
from niche_context import parse_ecb, parse_statcan, ECB_URL, STATCAN_URL, STATCAN_INFO_URL
from niche_context_panel import load_context, render_niche_context

NOW = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)
ECB_DAYS = []
cursor = date(2026, 9, 21)
while len(ECB_DAYS) < 45:
    if cursor.weekday() < 5:
        ECB_DAYS.append(cursor)
    cursor -= timedelta(days=1)
ECB_DAYS.reverse()
ECB_CSV = ('KEY,FREQ,BENCHMARK_ITEM,DATA_TYPE_EST,TIME_PERIOD,OBS_VALUE,OBS_STATUS,'
           'UNIT_MEASURE,UNIT_MULT,TITLE,TITLE_COMPL\n' + ''.join(
               f'EST.B.EU000A2X2A25.NB,B,EU000A2X2A25,NB,{day},47,A,_Z,0,Active banks,Active banks complete\n'
               f'EST.B.EU000A2X2A25.TT,B,EU000A2X2A25,TT,{day},66794,A,EUR,6,Total volume,Total volume complete\n'
               for day in ECB_DAYS)).encode()
STATCAN_MONTHS = [date((2026 * 12 + 7 - offset - 1) // 12,
                       (2026 * 12 + 7 - offset - 1) % 12 + 1, 1)
                  for offset in range(14, -1, -1)]
STATCAN = [{'status': 'SUCCESS', 'object': {'responseStatusCode': 0, 'productId': 12100175,
            'coordinate': '1.2.3.2.0.0.0.0.0.0', 'vectorId': 1567083339,
            'vectorDataPoint': [{'refPer': period.isoformat(), 'value': 16070458.0,
                                 'scalarFactorCode': 3, 'frequencyCode': 6, 'decimals': 0, 'statusCode': 0,
                                 'securityLevelCode': 0, 'symbolCode': 0,
                                 'releaseTime': '2026-09-03T08:30'} for period in STATCAN_MONTHS]}}]
STATCAN_INFO = [{'status': 'SUCCESS', 'object': {
    'responseStatusCode': 0, 'productId': 12100175,
    'coordinate': '1.2.3.2.0.0.0.0.0.0', 'vectorId': 1567083339,
    'frequencyCode': 6, 'scalarFactorCode': 3, 'decimals': 0, 'terminated': 0,
    'SeriesTitleEn': 'Canada;Domestic export;Energy products;United States',
    'memberUomCode': 81}}]


class Response:
    def __init__(self, content, content_type):
        self.content = content
        self.headers = {'Content-Type': content_type}

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, *, fail=False, energy=None):
        self.fail = fail
        self.energy = energy or STATCAN
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise requests.ConnectionError('secret-bearing diagnostics must not persist')
        return Response(ECB_CSV, 'text/csv')

    def post(self, url, **kwargs):
        self.calls.append(url)
        return Response(json.dumps(STATCAN_INFO if url.endswith('getSeriesInfoFromVector')
                                   else self.energy).encode(), 'application/json')


class Streamlit:
    def __init__(self):
        self.messages = []

    def __getattr__(self, method):
        return lambda *args: self.messages.append((method, args))


class NicheContextTests(unittest.TestCase):
    def test_exact_series_units_and_restricted_status(self):
        observation = parse_ecb(ECB_CSV, NOW.isoformat())
        self.assertEqual(observation['observations']['EST.B.EU000A2X2A25.TT']['unit_multiplier'], '6')
        with self.assertRaises(ValueError):
            parse_ecb(ECB_CSV.replace(b',EUR,6,', b',EUR,0,'), NOW.isoformat())
        with self.assertRaises(ValueError):
            parse_ecb(ECB_CSV.replace(b',47,A,', b',47,M,'), NOW.isoformat())
        duplicate_header = ECB_CSV.replace(b'OBS_VALUE,OBS_STATUS', b'OBS_VALUE,OBS_VALUE,OBS_STATUS', 1)
        with self.assertRaises(ValueError):
            parse_ecb(duplicate_header, NOW.isoformat())
        extra_fields = ECB_CSV.splitlines()
        extra_fields[1] += b',bogus'
        with self.assertRaises(ValueError):
            parse_ecb(b'\n'.join(extra_fields) + b'\n', NOW.isoformat())
        with self.assertRaises(ValueError):
            parse_ecb(b'\n'.join(ECB_CSV.splitlines()[:3]) + b'\n', NOW.isoformat())
        self.assertEqual(parse_statcan(json.dumps(STATCAN).encode(), NOW.isoformat())['period'], '2026-07-01')
        changed = json.loads(json.dumps(STATCAN))
        changed[0]['object']['coordinate'] = '1.2.3.1.0.0.0.0.0.0'
        with self.assertRaises(ValueError):
            parse_statcan(json.dumps(changed).encode(), NOW.isoformat())
        duplicate_json = json.dumps(STATCAN).replace('"vectorId": 1567083339',
                                                     '"vectorId": 1, "vectorId": 1567083339', 1)
        with self.assertRaises(ValueError):
            parse_statcan(duplicate_json.encode(), NOW.isoformat())
        with self.assertRaises(ValueError):
            parse_statcan(b'[[]]', NOW.isoformat())
        shortened = json.loads(json.dumps(STATCAN))
        shortened[0]['object']['vectorDataPoint'].pop()
        with self.assertRaises(ValueError):
            parse_statcan(json.dumps(shortened).encode(), NOW.isoformat())
        future_release = json.loads(json.dumps(STATCAN))
        future_release[0]['object']['vectorDataPoint'][-1]['releaseTime'] = '2027-01-01T08:30'
        with self.assertRaises(ValueError):
            parse_statcan(json.dumps(future_release).encode(), NOW.isoformat())
        same_day_future = json.loads(json.dumps(STATCAN))
        same_day_future[0]['object']['vectorDataPoint'][-1]['releaseTime'] = '2026-09-22T18:00'
        with self.assertRaises(ValueError):
            parse_statcan(json.dumps(same_day_future).encode(), NOW.isoformat())

    def test_cooldown_revision_and_invalid_journal_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            first = collect(temp, now=NOW, session=Session())
            self.assertEqual(first['status'], 'success')
            self.assertEqual(first['archive_event_count'], 1)
            self.assertEqual(load_context(temp, now=NOW)[1], 1)
            again = Session()
            self.assertEqual(collect(temp, now=NOW + timedelta(hours=1), session=again)['collection_action'], 'not_due')
            self.assertEqual(again.calls, [])
            changed = json.loads(json.dumps(STATCAN))
            changed[0]['object']['vectorDataPoint'][-1]['value'] = 16070500.0
            later = NOW + timedelta(days=1, seconds=1)
            result = collect(temp, now=later, session=Session(energy=changed))
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['archive_event_count'], 2)
            journal = json.loads(Path(temp, JOURNAL_NAME).read_text())
            self.assertLess(Path(temp, JOURNAL_NAME).stat().st_size,
                            len(Path(temp, DATA_NAME).read_bytes()) * 2)
            self.assertEqual([_unpack_snapshot(event['snapshot'])['statcan_energy']['observations'][-1]['value']
                              for event in journal['events']], [16070458.0, 16070500.0])
            self.assertEqual(load_context(temp, now=later)[1], 2)
            corrupted = _unpack_snapshot(journal['events'][0]['snapshot'])
            corrupted['statcan_energy']['observations'][0]['value'] = 1
            journal['events'][0]['snapshot'] = _pack_snapshot(corrupted)
            Path(temp, JOURNAL_NAME).write_text(json.dumps(journal))
            with self.assertRaises(ValueError):
                load_context(temp, now=later)

    def test_failed_refresh_hides_old_values(self):
        with tempfile.TemporaryDirectory() as temp:
            collect(temp, now=NOW, session=Session())
            old = Path(temp, DATA_NAME).read_bytes()
            result = collect(temp, now=NOW + timedelta(days=1), session=Session(fail=True))
            self.assertEqual(result['error_code'], 'NICHE_REQUEST_FAILED')
            self.assertEqual(Path(temp, DATA_NAME).read_bytes(), old)
            self.assertNotIn('secret-bearing', Path(temp, STATUS_NAME).read_text())
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW + timedelta(days=1))
            st = Streamlit()
            render_niche_context(st, temp)
            self.assertFalse(any(method == 'write' for method, _ in st.messages))

    def test_malformed_provider_envelope_records_safe_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            result = collect(temp, now=NOW, session=Session(energy=[[]]))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error_code'], 'NICHE_RESPONSE_INVALID')
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW)

    def test_recovers_valid_journal_tail_after_interrupted_status_write(self):
        with tempfile.TemporaryDirectory() as temp:
            first = collect(temp, now=NOW, session=Session())
            later = NOW + timedelta(days=1, seconds=1)
            second = collect(temp, now=later, session=Session())
            self.assertEqual(second['archive_event_count'], 2)
            status_path = Path(temp, STATUS_NAME)
            status = json.loads(status_path.read_text())
            status['archive_head'] = first['archive_head']
            status['archive_event_count'] = 1
            status_path.write_text(json.dumps(status))
            with self.assertRaises(ValueError):
                load_context(temp, now=later)
            recovery = collect(temp, now=later + timedelta(minutes=1), session=Session())
            self.assertEqual(recovery['status'], 'success')
            self.assertEqual(recovery['archive_event_count'], 3)
            self.assertEqual(load_context(temp, now=later + timedelta(minutes=1))[1], 3)


SECRET = 'DO_NOT_PUBLISH_TOKEN_OR_PROVIDER_BODY'


class ScriptedSession:
    def __init__(self, replace_index=None, replacement=None):
        self.responses = [Response(ECB_CSV, 'text/csv'),
                          Response(json.dumps(STATCAN).encode(), 'application/json'),
                          Response(json.dumps(STATCAN_INFO).encode(), 'application/json')]
        if replace_index is not None:
            self.responses[replace_index] = replacement
        self.calls = []

    def _request(self, method, url, kwargs):
        response = self.responses[len(self.calls)]
        self.calls.append((method, url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self._request('GET', url, kwargs)

    def post(self, url, **kwargs):
        return self._request('POST', url, kwargs)


class NicheDiagnosticTests(unittest.TestCase):
    def assert_failure(self, session, source, stage, code='NICHE_RESPONSE_INVALID'):
        with tempfile.TemporaryDirectory() as temp:
            result = collector.collect(temp, now=NOW, session=session)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error_code'], code)
            self.assertEqual(result.get('diagnostic_source'), source)
            self.assertEqual(result.get('diagnostic_stage'), stage)
            self.assertIsNone(result['last_success_at'])
            self.assertEqual(result['archive_event_count'], 0)
            self.assertFalse(Path(temp, collector.DATA_NAME).exists())
            self.assertFalse(Path(temp, collector.JOURNAL_NAME).exists())
            self.assertNotIn(SECRET, Path(temp, collector.STATUS_NAME).read_text())
            self.assertIs(result['core_eligible'], False)
            self.assertIsNone(result['signal'])
            with self.assertRaises(ValueError):
                load_context(temp, now=NOW)
            return result

    def test_content_type_identifies_each_source_and_stops_without_retry(self):
        for index, source in enumerate(('ECB_EST', 'STATCAN_DATA_1567083339',
                                        'STATCAN_INFO_1567083339')):
            with self.subTest(source=source):
                session = ScriptedSession(index, Response(SECRET.encode(), 'text/html'))
                self.assert_failure(session, source, 'CONTENT_TYPE')
                self.assertEqual(len(session.calls), index + 1)

    def test_malformed_body_identifies_each_parser_and_stops(self):
        for index, (source, content_type) in enumerate((('ECB_EST', 'text/csv'),
                ('STATCAN_DATA_1567083339', 'application/json'),
                ('STATCAN_INFO_1567083339', 'application/json'))):
            with self.subTest(source=source):
                session = ScriptedSession(index, Response(SECRET.encode(), content_type))
                self.assert_failure(session, source, 'PARSE')
                self.assertEqual(len(session.calls), index + 1)

    def test_metadata_identity_rejection_remains_strict(self):
        changed = json.loads(json.dumps(STATCAN_INFO))
        changed[0]['object']['vectorId'] = 1
        self.assert_failure(ScriptedSession(2, Response(json.dumps(changed).encode(),
                            'application/json')), 'STATCAN_INFO_1567083339', 'PARSE')

    def test_request_errors_keep_existing_code_for_all_sources(self):
        for index, source in enumerate(('ECB_EST', 'STATCAN_DATA_1567083339',
                                        'STATCAN_INFO_1567083339')):
            with self.subTest(source=source):
                session = ScriptedSession(index, requests.ConnectionError(SECRET))
                self.assert_failure(session, source, 'HTTP_REQUEST', 'NICHE_REQUEST_FAILED')
                self.assertEqual(len(session.calls), index + 1)

    def test_http_status_error_is_attributed_without_logging_response(self):
        response = Response(SECRET.encode(), 'application/json')
        response.raise_for_status = lambda: (_ for _ in ()).throw(requests.HTTPError(SECRET))
        self.assert_failure(ScriptedSession(2, response), 'STATCAN_INFO_1567083339',
                            'HTTP_REQUEST', 'NICHE_REQUEST_FAILED')

    def test_existing_response_exception_classes_remain_fail_closed(self):
        for error_type in (ValueError, KeyError, TypeError, AttributeError, OverflowError, UnicodeError):
            with self.subTest(error_type=error_type.__name__):
                with patch('niche_context.parse_ecb', side_effect=error_type(SECRET)):
                    self.assert_failure(ScriptedSession(), 'ECB_EST', 'PARSE')

    def test_artifact_failure_is_not_misattributed_to_last_provider(self):
        with patch('collect_niche_context.validate_artifact', side_effect=ValueError(SECRET)):
            session = ScriptedSession()
            self.assert_failure(session, 'ARTIFACT', 'ARTIFACT_VALIDATION')
            self.assertEqual(len(session.calls), 3)

    def test_unknown_exception_location_uses_fixed_unknown_labels(self):
        with patch('collect_niche_context.fetch', side_effect=ValueError(SECRET)):
            self.assert_failure(ScriptedSession(), 'UNKNOWN', 'UNKNOWN')

    def test_diagnostics_are_allowlisted_not_arbitrary_injected_values(self):
        def fail(**kwargs):
            kwargs['diagnostics'].update(diagnostic_source=SECRET, diagnostic_stage=SECRET)
            raise ValueError(SECRET)
        with patch('collect_niche_context.fetch', side_effect=fail):
            self.assert_failure(ScriptedSession(), 'UNKNOWN', 'UNKNOWN')

    def test_failed_refresh_preserves_success_cache_journal_and_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            first = collector.collect(temp, now=NOW, session=Session())
            paths = [Path(temp, name) for name in (collector.DATA_NAME, collector.ECB_CSV_NAME,
                                                  collector.JOURNAL_NAME)]
            old = [p.read_bytes() for p in paths]
            later = NOW + timedelta(days=1)
            session = ScriptedSession(1, Response(SECRET.encode(), 'application/json'))
            failure = collector.collect(temp, now=later, session=session)
            self.assertEqual(failure['last_success_at'], first['last_success_at'])
            self.assertEqual(failure['archive_head'], first['archive_head'])
            self.assertEqual(failure['archive_event_count'], 1)
            self.assertEqual([p.read_bytes() for p in paths], old)
            self.assertEqual(failure.get('diagnostic_source'), 'STATCAN_DATA_1567083339')
            blocked = ScriptedSession()
            again = collector.collect(temp, now=later + timedelta(hours=1), session=blocked)
            self.assertEqual(again['collection_action'], 'not_due')
            self.assertEqual(again.get('diagnostic_source'), failure.get('diagnostic_source'))
            self.assertEqual(blocked.calls, [])
            with self.assertRaises(ValueError):
                load_context(temp, now=later)
            recovery = collector.collect(temp, now=later + timedelta(days=1), session=ScriptedSession())
            self.assertEqual(recovery['status'], 'success')
            self.assertNotIn('diagnostic_source', recovery)
            self.assertNotIn('diagnostic_stage', recovery)

    def test_success_keeps_request_contract_and_has_no_error_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            session = ScriptedSession()
            result = collector.collect(temp, now=NOW, session=session)
            self.assertEqual(result['status'], 'success')
            self.assertEqual([(method, url) for method, url, _ in session.calls],
                             [('GET', ECB_URL), ('POST', STATCAN_URL), ('POST', STATCAN_INFO_URL)])
            self.assertEqual([kwargs.get('json') for _, _, kwargs in session.calls],
                             [None, [{'vectorId': 1567083339, 'latestN': 15}],
                              [{'vectorId': 1567083339}]])
            self.assertTrue(all(kwargs['timeout'] == 30 for _, _, kwargs in session.calls))
            self.assertNotIn('diagnostic_source', result)
            self.assertNotIn('diagnostic_stage', result)
            self.assertEqual(load_context(temp, now=NOW)[1], 1)

    def test_cli_prints_fixed_diagnostics_without_provider_text(self):
        with tempfile.TemporaryDirectory() as temp:
            args = ['collect_niche_context.py', '--output-dir', temp]
            output = io.StringIO()
            with patch('sys.argv', args), patch('collect_niche_context.fetch',
                    side_effect=ValueError(SECRET)), contextlib.redirect_stdout(output):
                exit_code = collector.main()
            self.assertEqual(exit_code, 1)
            result = json.loads(output.getvalue())
            self.assertEqual(result.get('diagnostic_source'), 'UNKNOWN')
            self.assertEqual(result.get('diagnostic_stage'), 'UNKNOWN')
            self.assertNotIn(SECRET, output.getvalue())


if __name__ == '__main__':
    unittest.main()
