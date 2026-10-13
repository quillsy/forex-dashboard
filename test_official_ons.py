import ast
import copy
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, call

import requests
from official_ons import (BASE, PN2_FALLBACK_URL, TITLE, CPI_TITLE, CPI_URL,
    CPI_2026_SEPTEMBER_DUE, GDP_2026_Q3_FIRST_DUE, GDP_2026_Q3_REVISION_DUE,
    parse_ons_gdp, fetch_ons_gdp, validate_ons_cpi, ons_live_release_metadata)

NOW = datetime(2026, 9, 7, 15, tzinfo=timezone.utc)


class ONSCpiContractTests(unittest.TestCase):
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)

    @staticmethod
    def fixture(value='3.1'):
        # Contract fields from the public D7G7/MM23 response; no account data.
        return {'description': {'cdid': 'D7G7', 'datasetId': 'MM23', 'title': CPI_TITLE,
                'unit': '%', 'keyNote': 'Change over 12 months  ',
                'releaseDate': '2026-09-15T23:00:00.000Z', 'date': '2026 AUG', 'number': value},
                'months': [{'date': '2026 AUG', 'label': '2026 AUG', 'year': '2026',
                'month': 'August', 'sourceDataset': 'MM23', 'value': value,
                'updateDate': '2026-09-15T23:00:00.000Z'}]}

    def test_exact_annual_rate_and_zero_are_preserved(self):
        for value in ('3.1', '0', '-0.5'):
            payload = self.fixture(value)
            self.assertIs(validate_ons_cpi(payload, now=self.now), payload['months'])

    def test_missing_or_wrong_identity_and_measurement_rejected(self):
        for key, value in [('cdid', 'D7BT'), ('datasetId', 'LMS'), ('title', 'CPI INDEX'),
                           ('unit', 'Index'), ('keyNote', 'Change over 1 month')]:
            payload = self.fixture(); payload['description'][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                validate_ons_cpi(payload, now=self.now)
        for payload in ([], {}, dict(self.fixture(), description=None)):
            with self.assertRaises(ValueError): validate_ons_cpi(payload, now=self.now)

    def test_wrong_row_source_or_period_metadata_rejected(self):
        for key, value in [('sourceDataset', 'LMS'), ('date', '2026AUG'), ('label', '2026 JUL'),
                           ('year', '2025'), ('month', 'July')]:
            payload = self.fixture(); payload['months'][0][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                validate_ons_cpi(payload, now=self.now)

    def test_nonfinite_bool_invalid_and_impossible_values_rejected(self):
        for value in ('NaN', 'Infinity', True, {}, 'invalid', '-100.1'):
            payload = self.fixture(); payload['months'][0]['value'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_ons_cpi(payload, now=self.now)

    def test_missing_values_and_conflicting_duplicates_rejected(self):
        for rows in ([], None, [None], [dict(self.fixture()['months'][0], value='')]):
            payload = self.fixture(); payload['months'] = rows
            with self.assertRaises(ValueError): validate_ons_cpi(payload, now=self.now)
        payload = self.fixture()
        payload['months'].append(dict(payload['months'][0], value='3.2'))
        with self.assertRaisesRegex(ValueError, 'CONFLICTING'):
            validate_ons_cpi(payload, now=self.now)
        payload['months'][1]['value'] = '3.1'
        self.assertEqual(len(validate_ons_cpi(payload, now=self.now)), 2)

    def test_summary_cannot_hide_a_missing_latest_observation(self):
        for key, value in [('date', '2026 SEP'), ('number', '3.2'), ('number', 'NaN'), ('number', True)]:
            payload = self.fixture(); payload['description'][key] = value
            with self.subTest(field=key, value=value), self.assertRaisesRegex(ValueError, 'SUMMARY'):
                validate_ons_cpi(payload, now=self.now)

    def test_release_date_is_a_calendar_date_and_future_is_rejected(self):
        payload = self.fixture()
        validate_ons_cpi(payload, now=datetime(2026, 9, 16, 0, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, 'FUTURE_RELEASE'):
            validate_ons_cpi(payload, now=datetime(2026, 9, 15, 12, tzinfo=timezone.utc))
        for release in (None, 'invalid', '2026-09-16'):
            payload['description']['releaseDate'] = release
            with self.assertRaises(ValueError): validate_ons_cpi(payload, now=self.now)

    def test_unfinished_month_cannot_be_qualified(self):
        payload = self.fixture()
        payload['months'][0].update(date='2026 SEP', label='2026 SEP', month='September')
        payload['description']['date'] = '2026 SEP'
        with self.assertRaisesRegex(ValueError, 'FUTURE_MONTH'):
            validate_ons_cpi(payload, now=self.now)

    def test_loader_rejects_bad_contract_and_preserves_existing_pit_dates(self):
        import pandas as pd
        import live_data
        node = next(n for n in ast.parse(Path(__file__).with_name('app.py').read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'get_ons_cpi_data')
        node.decorator_list = []
        response = Mock(status_code=200)
        client = Mock()
        client.get.return_value = response
        ns = {'requests': client, 'pd': pd, 'datetime': datetime,
              'live_data': live_data, 'check_demo_active': lambda: False}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<ons-loader>', 'exec'), ns)
        payload = self.fixture(); response.json.return_value = payload
        frame, _, is_live = ns['get_ons_cpi_data'](propagate_transport=True)
        self.assertTrue(is_live); self.assertEqual(frame.iloc[0]['value'], 3.1)
        self.assertEqual(frame.iloc[0]['release_date'], pd.Timestamp('2026-09-15T23:00:00'))
        self.assertFalse(frame.iloc[0]['is_pit_limited'])
        payload['months'][0]['updateDate'] = '2015-10-12T23:00:00.000Z'
        frame, _, is_live = ns['get_ons_cpi_data']()
        self.assertTrue(is_live); self.assertTrue(frame.iloc[0]['is_pit_limited'])
        self.assertEqual(frame.iloc[0]['release_date'], pd.Timestamp('2026-09-25'))
        bad = copy.deepcopy(payload); bad['description']['unit'] = 'INDEX'
        response.json.return_value = bad
        self.assertFalse(ns['get_ons_cpi_data'](propagate_transport=True)[-1])


def fixture(dataset='PN2', value='1.2', release='2026-08-12T23:00:00.000Z'):
    return {'description': {'cdid': 'IHYR', 'datasetId': dataset, 'title': TITLE, 'unit': '%', 'releaseDate': release},
            'quarters': [{'date': '2026 Q2', 'label': '2026 Q2', 'year': '2026', 'quarter': 'Q2', 'sourceDataset': dataset, 'value': value}]}


def session_for(*payloads):
    session = Mock()
    responses = []
    for payload in payloads:
        response = Mock(); response.json.return_value = payload; responses.append(response)
    session.get.side_effect = responses
    return session


class ONSTests(unittest.TestCase):
    @staticmethod
    def qna_q1():
        previous = fixture('QNA', '0.9', '2026-06-29T23:00:00Z')
        previous['quarters'][0].update(date='2026 Q1', label='2026 Q1', quarter='Q1')
        return previous

    def test_date_not_fake_publication_timestamp(self):
        result = parse_ons_gdp(fixture(), 'PN2', now=NOW)
        self.assertEqual(result['value'], 1.2)
        self.assertEqual(result['date'], '2026-06-30')
        self.assertEqual(result['release_date_known'], '2026-08-13')
        self.assertIsNone(result['published_at'])
        self.assertNotIn('next_due_at', result)

    def test_reject_wrong_series_units_quarter_and_future(self):
        for key, value in [('cdid', 'IHYP'), ('unit', 'GBP'), ('title', 'Nominal GDP'), ('datasetId', 'LMS'), ('releaseDate', '2026-09-30T00:00:00Z')]:
            data = fixture(); data['description'][key] = value
            with self.assertRaises(ValueError): parse_ons_gdp(data, 'PN2', now=NOW)
        for key, value in [('quarter', 'Q3'), ('sourceDataset', 'QNA'), ('value', 'NaN'), ('value', True)]:
            data = fixture(); data['quarters'][0][key] = value
            with self.assertRaises(ValueError): parse_ons_gdp(data, 'PN2', now=NOW)

    def test_same_release_conflict_and_new_revision(self):
        session = session_for(fixture(), fixture('QNA', '1.3'))
        self.assertIsNone(fetch_ons_gdp(now=NOW, session=session))
        session = session_for(fixture(), fixture('QNA', '1.3', '2026-09-01T23:00:00Z'))
        self.assertEqual(fetch_ons_gdp(now=NOW, session=session)['value'], 1.3)
        self.assertEqual(session.get.call_count, 2)

    def test_missing_and_conflicting_rows(self):
        data = fixture(); data['quarters'] = []
        self.assertIsNone(parse_ons_gdp(data, 'PN2', now=NOW))
        data = fixture(); row = dict(data['quarters'][0], value='9'); data['quarters'].append(row)
        with self.assertRaises(ValueError): parse_ons_gdp(data, 'PN2', now=NOW)

    def test_q2_revision_due_requires_qna_vintage_not_a_changed_value(self):
        before = datetime(2026, 9, 30, 5, 59, 59, tzinfo=timezone.utc)
        due = datetime(2026, 9, 30, 6, tzinfo=timezone.utc)
        old_qna = self.qna_q1()
        self.assertEqual(fetch_ons_gdp(now=before, session=session_for(fixture(), old_qna))['series_id'], 'IHYR/PN2')
        self.assertIsNone(fetch_ons_gdp(now=due, session=session_for(fixture(), old_qna)))

        for value in ('1.2', '1.3'):
            with self.subTest(value=value):
                revised = fixture('QNA', value, '2026-09-29T23:00:00Z')
                result = fetch_ons_gdp(now=due, session=session_for(fixture(), revised))
                self.assertEqual(result['series_id'], 'IHYR/QNA')
                self.assertEqual(result['date'], '2026-06-30')
                self.assertEqual(result['value'], float(value))
                self.assertEqual(result['release_date_known'], '2026-09-30')
                self.assertIsNone(result['published_at'])
                self.assertEqual(result['next_due_at'], GDP_2026_Q3_FIRST_DUE.isoformat())
                self.assertEqual(result['next_due_precision'], 'official_scheduled_time')
                self.assertTrue(result['needs_hourly_check'])

        # A new envelope date is insufficient if QNA still contains only Q1.
        lagging = self.qna_q1()
        lagging['description']['releaseDate'] = '2026-09-29T23:00:00Z'
        self.assertIsNone(fetch_ons_gdp(now=due, session=session_for(fixture(), lagging)))
        # PN2 updating first cannot stand in for the quarterly-accounts vintage.
        new_pn2 = fixture(release='2026-09-29T23:00:00Z')
        self.assertIsNone(fetch_ons_gdp(now=due, session=session_for(new_pn2, old_qna)))
        matching_qna = fixture('QNA', '1.2', '2026-09-29T23:00:00Z')
        self.assertEqual(fetch_ons_gdp(now=due, session=session_for(new_pn2, matching_qna))['series_id'], 'IHYR/QNA')
        later_pn2 = fixture(value='1.4', release='2026-09-30T23:00:00Z')
        self.assertIsNone(fetch_ons_gdp(now=datetime(2026, 10, 1, 6, tzinfo=timezone.utc),
            session=session_for(later_pn2, matching_qna)))

    def test_q2_revision_calendar_date_is_not_a_midnight_publication(self):
        early = datetime(2026, 9, 30, 5, 59, 59, tzinfo=timezone.utc)
        for dataset in ('PN2', 'QNA'):
            with self.subTest(dataset=dataset):
                preloaded = fixture(dataset, release='2026-09-29T23:00:00Z')
                with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
                    parse_ons_gdp(preloaded, dataset, now=early)
        # The same releaseDate is usable only when its row and family qualify.
        confirmed = fixture('QNA', '1.2', '2026-09-29T23:00:00Z')
        self.assertEqual(parse_ons_gdp(confirmed, 'QNA',
            now=datetime(2026, 9, 30, 6, tzinfo=timezone.utc))['value'], 1.2)


class ONSFollowingReleaseTests(unittest.TestCase):
    """Synthetic successor values only; no future ONS value is asserted."""

    @staticmethod
    def cpi_observation(period='2026-08-01', vintage='2026-09-16'):
        return {'date': period, 'source': 'ONS', 'source_url': CPI_URL,
                'series_id': 'D7G7', 'release_date_known': vintage}

    @staticmethod
    def gdp_observation(period='2026-06-30', dataset='QNA', vintage='2026-09-30'):
        return {'date': period, 'source': 'ONS', 'source_url': BASE + dataset.lower(),
                'series_id': 'IHYR/' + dataset, 'release_date_known': vintage}

    @staticmethod
    def q3_payload(dataset='PN2', value='1.7', release='2026-11-12T00:00:00Z'):
        payload = fixture(dataset, value, release)
        payload['quarters'][0].update(date='2026 Q3', label='2026 Q3', quarter='Q3')
        return payload

    @staticmethod
    def q2_qna():
        return fixture('QNA', '1.4', '2026-09-29T23:00:00Z')

    def test_cpi_old_period_deadline_and_legacy_vintage(self):
        before = CPI_2026_SEPTEMBER_DUE - timedelta(seconds=1)
        old = self.cpi_observation()
        old.pop('release_date_known')
        old.pop('source_url')
        metadata = ons_live_release_metadata('Inflation', old, now=before)
        self.assertEqual(metadata, {'release_date_known': None,
            'next_due_at': CPI_2026_SEPTEMBER_DUE.isoformat(),
            'next_due_precision': 'official_scheduled_time', 'needs_hourly_check': True})
        old['source_url'] = CPI_URL
        with self.assertRaisesRegex(ValueError, 'ONS_RELEASE_DUE'):
            ons_live_release_metadata('Inflation', old, now=CPI_2026_SEPTEMBER_DUE)
        new = self.cpi_observation('2026-09-01', '2026-10-21')
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            ons_live_release_metadata('Inflation', new, now=before)
        with self.assertRaisesRegex(ValueError, 'ONS_CHECK_BEFORE_RELEASE'):
            ons_live_release_metadata('Inflation', new, now=CPI_2026_SEPTEMBER_DUE,
                                      checked_at=before.isoformat())
        metadata = ons_live_release_metadata('Inflation', new, now=CPI_2026_SEPTEMBER_DUE,
                                              checked_at=CPI_2026_SEPTEMBER_DUE.isoformat())
        self.assertEqual(metadata, {'release_date_known': '2026-10-21',
            'next_due_at': None, 'next_due_precision': None, 'needs_hourly_check': True})
        for bad_vintage in (None, '2026-10-20'):
            with self.subTest(vintage=bad_vintage), self.assertRaisesRegex(ValueError, 'ONS_RELEASE_DUE'):
                ons_live_release_metadata('Inflation', self.cpi_observation('2026-09-01', bad_vintage),
                                          now=CPI_2026_SEPTEMBER_DUE)

    def test_cpi_parser_preserves_synthetic_new_rows_and_zero_negative_values(self):
        before = CPI_2026_SEPTEMBER_DUE - timedelta(seconds=1)
        for value in ('0', '-0.5'):
            with self.subTest(value=value):
                payload = ONSCpiContractTests.fixture(value)
                payload['months'][0].update(date='2026 SEP', label='2026 SEP', month='September')
                payload['description'].update(date='2026 SEP', releaseDate='2026-10-20T23:00:00Z')
                with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
                    validate_ons_cpi(payload, now=before)
                self.assertIs(validate_ons_cpi(payload, now=CPI_2026_SEPTEMBER_DUE), payload['months'])
        with self.assertRaisesRegex(ValueError, 'ONS_RELEASE_DUE'):
            validate_ons_cpi(ONSCpiContractTests.fixture(), now=CPI_2026_SEPTEMBER_DUE)

    def test_gdp_first_estimate_deadline_keeps_old_qna_component_parseable(self):
        before = GDP_2026_Q3_FIRST_DUE - timedelta(seconds=1)
        new_pn2 = self.q3_payload()
        old_qna = self.q2_qna()
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            parse_ons_gdp(new_pn2, 'PN2', now=before)
        self.assertEqual(parse_ons_gdp(old_qna, 'QNA', now=GDP_2026_Q3_FIRST_DUE)['date'], '2026-06-30')
        result = fetch_ons_gdp(now=GDP_2026_Q3_FIRST_DUE,
                               session=session_for(new_pn2, old_qna))
        self.assertEqual((result['date'], result['series_id'], result['value']),
                         ('2026-09-30', 'IHYR/PN2', 1.7))
        self.assertEqual(result['next_due_at'], GDP_2026_Q3_REVISION_DUE.isoformat())
        self.assertEqual(result['next_due_precision'], 'official_scheduled_time')
        self.assertTrue(result['needs_hourly_check'])
        with self.assertRaisesRegex(ValueError, 'ONS_RELEASE_DUE'):
            fetch_ons_gdp(now=GDP_2026_Q3_FIRST_DUE,
                          session=session_for(fixture(), old_qna))

    def test_gdp_q3_revision_requires_qna_vintage_even_same_value(self):
        before = GDP_2026_Q3_REVISION_DUE - timedelta(seconds=1)
        first = self.q3_payload(value='1.7')
        revision = self.q3_payload('QNA', '1.7', '2026-12-22T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            parse_ons_gdp(revision, 'QNA', now=before)
        with self.assertRaisesRegex(ValueError, 'ONS_Q3_REVISION_DUE'):
            fetch_ons_gdp(now=GDP_2026_Q3_REVISION_DUE,
                          session=session_for(first, self.q2_qna()))
        selected = fetch_ons_gdp(now=GDP_2026_Q3_REVISION_DUE,
                                 session=session_for(first, revision))
        self.assertEqual((selected['series_id'], selected['value'], selected['release_date_known']),
                         ('IHYR/QNA', 1.7, '2026-12-22'))
        self.assertIsNone(selected['next_due_at'])
        prior_vintage = self.q3_payload('QNA', '1.7', '2026-12-20T23:00:00Z')
        with self.assertRaisesRegex(ValueError, 'ONS_Q3_REVISION_DUE'):
            fetch_ons_gdp(now=GDP_2026_Q3_REVISION_DUE,
                          session=session_for(first, prior_vintage))

    def test_qna_q3_november_first_estimate_is_allowed_until_revision(self):
        pn2_first = self.q3_payload(value='1.7')
        qna_first = self.q3_payload('QNA', '1.7', '2026-11-12T00:00:00Z')
        self.assertEqual(parse_ons_gdp(qna_first, 'QNA', now=GDP_2026_Q3_FIRST_DUE)['date'],
                         '2026-09-30')
        same = fetch_ons_gdp(now=GDP_2026_Q3_FIRST_DUE,
                             session=session_for(pn2_first, qna_first))
        self.assertEqual((same['series_id'], same['value'], same['next_due_at']),
                         ('IHYR/PN2', 1.7, GDP_2026_Q3_REVISION_DUE.isoformat()))
        qna_later = self.q3_payload('QNA', '1.7', '2026-11-13T00:00:00Z')
        later = fetch_ons_gdp(now=GDP_2026_Q3_FIRST_DUE + timedelta(days=1),
                              session=session_for(pn2_first, qna_later))
        self.assertEqual((later['series_id'], later['next_due_at']),
                         ('IHYR/QNA', GDP_2026_Q3_REVISION_DUE.isoformat()))
        same_release_conflict = self.q3_payload('QNA', '1.8', '2026-11-12T00:00:00Z')
        self.assertIsNone(fetch_ons_gdp(now=GDP_2026_Q3_FIRST_DUE,
                                        session=session_for(pn2_first, same_release_conflict)))
        with self.assertRaisesRegex(ValueError, 'ONS_Q3_REVISION_DUE'):
            fetch_ons_gdp(now=GDP_2026_Q3_REVISION_DUE,
                          session=session_for(pn2_first, qna_first))

    def test_future_day_vintage_is_blocked_without_a_new_numeric_period(self):
        before_cpi = CPI_2026_SEPTEMBER_DUE - timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            ons_live_release_metadata('Inflation', self.cpi_observation(vintage='2026-10-21'),
                                      now=before_cpi)
        before_gdp = GDP_2026_Q3_FIRST_DUE - timedelta(seconds=1)
        preloaded_qna = fixture('QNA', '1.4', '2026-11-12T00:00:00Z')
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            parse_ons_gdp(preloaded_qna, 'QNA', now=before_gdp)
        self.assertEqual(parse_ons_gdp(preloaded_qna, 'QNA', now=GDP_2026_Q3_FIRST_DUE)['date'],
                         '2026-06-30')

    def test_exact_identity_and_check_time_are_required_after_due(self):
        new = self.cpi_observation('2026-09-01', '2026-10-21')
        for field, value in (('source', 'OTHER'), ('series_id', 'D7BT'), ('source_url', BASE + 'pn2')):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'ONS_RELEASE_IDENTITY_INVALID'):
                wrong = dict(new, **{field: value})
                ons_live_release_metadata('Inflation', wrong, now=CPI_2026_SEPTEMBER_DUE)
        for bad_time in ('2026-10-21T06:00:00', 'bad', '2026-10-22T00:00:00+00:00'):
            with self.subTest(check=bad_time), self.assertRaisesRegex(ValueError, 'ONS_CHECK_TIME_INVALID'):
                ons_live_release_metadata('Inflation', new, now=CPI_2026_SEPTEMBER_DUE,
                                          checked_at=bad_time)
        q3 = self.gdp_observation('2026-09-30', 'PN2', '2026-11-12')
        q3['source_url'] = PN2_FALLBACK_URL
        self.assertEqual(ons_live_release_metadata('GDP', q3, now=GDP_2026_Q3_FIRST_DUE,
            checked_at=GDP_2026_Q3_FIRST_DUE.isoformat())['next_due_at'],
            GDP_2026_Q3_REVISION_DUE.isoformat())
        q3['source_url'] = BASE + 'qna'
        with self.assertRaisesRegex(ValueError, 'ONS_RELEASE_IDENTITY_INVALID'):
            ons_live_release_metadata('GDP', q3, now=GDP_2026_Q3_FIRST_DUE)


class ONSFallbackTests(unittest.TestCase):
    def response(self, payload=None, status=200):
        response = requests.Response()
        response.status_code = status
        response.json = Mock(return_value=payload)
        return response

    def session(self, *outcomes):
        return Mock(get=Mock(side_effect=list(outcomes)))

    def test_success_uses_only_existing_routes(self):
        session = self.session(self.response(fixture()), self.response(fixture('QNA')))
        fetch_ons_gdp(now=NOW, session=session)
        self.assertEqual(session.get.call_args_list, [call(BASE+'pn2/data', timeout=20),
                                                     call(BASE+'qna/data', timeout=20)])

    def test_only_eligible_failures_use_one_exact_pn2_route(self):
        for failure in [requests.Timeout(), requests.ConnectionError(),
                        self.response(status=500), self.response(status=502), self.response(status=504)]:
            with self.subTest(failure=failure):
                session = self.session(failure, self.response(fixture()), self.response(fixture('QNA')))
                result = fetch_ons_gdp(now=NOW, session=session)
                expected = dict(parse_ons_gdp(fixture(), 'PN2', now=NOW), source_url=PN2_FALLBACK_URL)
                expected.update(ons_live_release_metadata('GDP', expected, now=NOW, checked_at=expected['checked_at']))
                self.assertEqual(result, expected)
                self.assertEqual(session.get.call_args_list, [call(BASE+'pn2/data', timeout=20),
                    call(PN2_FALLBACK_URL, timeout=20), call(BASE+'qna/data', timeout=20)])

    def test_denials_cooldowns_tls_and_other_errors_never_fallback(self):
        failures = [self.response(status=status) for status in (400, 401, 403, 404, 429, 503)]
        failures[-1].headers['Retry-After'] = '3600'
        failures += [requests.exceptions.SSLError(), requests.RequestException('PROVIDER_COOLDOWN'),
                     requests.RequestException('PROVIDER_REQUEST_FAILED')]
        for failure in failures:
            with self.subTest(failure=failure):
                session = self.session(failure)
                with self.assertRaises(requests.RequestException):
                    fetch_ons_gdp(now=NOW, session=session)
                self.assertEqual(session.get.call_count, 1)

    def test_primary_invalid_or_empty_data_does_not_fallback(self):
        wrong = fixture(); wrong['description']['cdid'] = 'IHYP'
        future = fixture(release='2026-09-30T00:00:00Z')
        conflict = fixture(); conflict['quarters'].append(dict(conflict['quarters'][0], value='9'))
        malformed = self.response(); malformed.json.side_effect = ValueError('Invalid JSON')
        for response in [self.response(wrong), self.response(future), self.response(conflict), malformed]:
            with self.subTest(response=response):
                session = self.session(response)
                with self.assertRaises(ValueError): fetch_ons_gdp(now=NOW, session=session)
                self.assertEqual(session.get.call_count, 1)
        empty = fixture(); empty['quarters'] = []
        session = self.session(self.response(empty), self.response(fixture('QNA')))
        self.assertEqual(fetch_ons_gdp(now=NOW, session=session)['series_id'], 'IHYR/QNA')
        self.assertNotIn(call(PN2_FALLBACK_URL, timeout=20), session.get.call_args_list)

    def test_fallback_errors_and_invalid_data_are_not_retried(self):
        wrong = fixture(); wrong['description']['datasetId'] = 'QNA'
        future = fixture(release='2026-09-30T00:00:00Z')
        malformed = self.response(); malformed.json.side_effect = ValueError('Invalid JSON')
        for failure in [requests.Timeout(), self.response(status=503), self.response(wrong),
                        self.response(future), malformed]:
            with self.subTest(failure=failure):
                session = self.session(requests.Timeout(), failure)
                with self.assertRaises((requests.RequestException, ValueError)):
                    fetch_ons_gdp(now=NOW, session=session)
                self.assertEqual(session.get.call_count, 2)

    def test_fallback_old_dates_are_not_extended_and_empty_stays_empty(self):
        old = fixture(release='2025-08-12T23:00:00Z')
        old['quarters'][0].update(date='2025 Q2', label='2025 Q2', year='2025')
        empty = fixture('QNA'); empty['quarters'] = []
        session = self.session(requests.Timeout(), self.response(old), self.response(empty))
        result = fetch_ons_gdp(now=NOW, session=session)
        expected = dict(parse_ons_gdp(old, 'PN2', now=NOW), source_url=PN2_FALLBACK_URL)
        expected.update(ons_live_release_metadata('GDP', expected, now=NOW, checked_at=expected['checked_at']))
        self.assertEqual(result, expected)
        self.assertEqual(result['date'], '2025-06-30')
        self.assertEqual(result['release_date_known'], '2025-08-13')
        self.assertIsNone(result['published_at'])
        self.assertEqual(result['next_due_at'], '2026-09-30T06:00:00+00:00')
        pn2_empty = fixture(); pn2_empty['quarters'] = []
        session = self.session(requests.Timeout(), self.response(pn2_empty), self.response(empty))
        self.assertIsNone(fetch_ons_gdp(now=NOW, session=session))

    def test_qna_failure_blocks_even_with_successful_pn2_fallback(self):
        for failure in [requests.Timeout(), self.response(status=502), self.response(status=429)]:
            session = self.session(requests.Timeout(), self.response(fixture()), failure)
            with self.assertRaises(requests.RequestException): fetch_ons_gdp(now=NOW, session=session)
            self.assertEqual(session.get.call_count, 3)

    def test_fallback_preserves_same_release_conflict_and_revision_selection(self):
        session = self.session(requests.Timeout(), self.response(fixture()), self.response(fixture('QNA', '1.3')))
        self.assertIsNone(fetch_ons_gdp(now=NOW, session=session))
        session = self.session(requests.Timeout(), self.response(fixture()),
                               self.response(fixture('QNA', '1.3', '2026-09-01T23:00:00Z')))
        result = fetch_ons_gdp(now=NOW, session=session)
        self.assertEqual(result['value'], 1.3)
        self.assertEqual(result['series_id'], 'IHYR/QNA')



class LabourTests(unittest.TestCase):
    def fixture(self, date='2026 MAY', label='2026 APR-JUN'):
        return {'description': {'cdid':'MGSX','datasetId':'LMS','title':'Unemployment rate (aged 16 and over, seasonally adjusted): %','unit':'%','monthLabelStyle':'three month average','releaseDate':'2026-08-17T23:00:00Z'},
                'months':[{'date':date,'label':label,'sourceDataset':'LMS','value':'4.9'}]}
    def test_actual_rolling_reference(self):
        from official_ons import parse_ons_labour
        result=parse_ons_labour(self.fixture(),now=NOW)
        self.assertEqual(result['date'],'2026-06-30')
        self.assertEqual(result['reference_period'],'2026-04-01/2026-06-30')
        self.assertEqual(result['frequency'],'rolling_three_month_monthly_release')
    def test_year_boundary(self):
        from official_ons import parse_ons_labour
        result=parse_ons_labour(self.fixture('2026 JAN','2025 DEC-FEB'),now=NOW)
        self.assertEqual(result['reference_period'],'2025-12-01/2026-02-28')
    def test_reject_monthly_relabel(self):
        from official_ons import parse_ons_labour
        with self.assertRaises(ValueError):parse_ons_labour(self.fixture(label='2026 MAY'),now=NOW)

    def release_fixture(self, release='2026-09-14T23:00:00Z'):
        data = self.fixture('2026 JUN', '2026 MAY-JUL')
        data['description']['releaseDate'] = release
        return data

    def test_labour_calendar_deadline_and_hourly_checks(self):
        from official_ons import parse_ons_labour
        from zoneinfo import ZoneInfo
        due = datetime(2026, 10, 20, 7, tzinfo=ZoneInfo('Europe/London')).astimezone(timezone.utc)
        result = parse_ons_labour(self.release_fixture(), now=datetime(2026, 10, 13, tzinfo=timezone.utc))
        self.assertEqual(due.isoformat(), '2026-10-20T06:00:00+00:00')
        self.assertEqual(result['next_due_at'], due.isoformat())
        self.assertTrue(result['needs_hourly_check'])

    def test_old_labour_vintage_blocks_at_confirmed_release_even_after_new_fetch(self):
        from official_ons import parse_ons_labour
        before = datetime(2026, 10, 20, 5, 59, 59, tzinfo=timezone.utc)
        self.assertNotIn('_validation', parse_ons_labour(self.release_fixture(), now=before))
        for minute in (0, 30):
            result = parse_ons_labour(self.release_fixture(), now=datetime(2026, 10, 20, 6, minute, tzinfo=timezone.utc))
            self.assertEqual(result['_validation'], 'UNVERIFIED')
            self.assertIn('ONS', result['_reason'])

    def test_new_labour_vintage_cannot_be_used_before_scheduled_hour(self):
        from official_ons import parse_ons_labour
        before = datetime(2026, 10, 20, 5, 59, 59, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, 'ONS_FUTURE_RELEASE'):
            parse_ons_labour(self.release_fixture('2026-10-19T23:00:00Z'), now=before)

    def test_new_labour_vintage_can_preserve_values_and_reference(self):
        from official_ons import parse_ons_labour
        result = parse_ons_labour(self.release_fixture('2026-10-19T23:00:00Z'),
                                 now=datetime(2026, 10, 20, 6, tzinfo=timezone.utc))
        self.assertNotIn('_validation', result)
        self.assertEqual(result['release_date_known'], '2026-10-20')
        self.assertEqual(result['reference_period'], '2026-05-01/2026-07-31')
        self.assertEqual(result['value'], 4.9)
        self.assertIsNone(result['next_due_at'])
        self.assertTrue(result['needs_hourly_check'])

    def test_labour_reader_cannot_revive_old_or_early_vintage_without_cache_deadline(self):
        from official_ons import parse_ons_labour
        from live_data import build_record, eligible
        at = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)
        before = at.replace(hour=5, minute=59, second=59)
        for vintage, check, expected in (('2026-09-15', before, True),
                                         ('2026-09-15', at, False),
                                         ('2026-10-20', before, False),
                                         ('2026-10-20', at, True),
                                         (None, at, False), ('2026-99-20', at, False)):
            with self.subTest(vintage=vintage, check=check):
                observation = parse_ons_labour(self.release_fixture(), now=datetime(2026, 10, 13, tzinfo=timezone.utc))
                observation.pop('next_due_at', None)
                observation['release_date_known'] = vintage
                record = build_record('Arbeitsmarkt', 10, observation, 'FRESH', check.isoformat())
                self.assertEqual(eligible(record, check, factor='Arbeitsmarkt', currency='GBP')[0], expected)

    def test_labour_reader_requires_exact_source_and_check_after_release(self):
        from official_ons import parse_ons_labour
        from live_data import build_record, eligible
        at = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)
        obs = parse_ons_labour(self.release_fixture('2026-10-19T23:00:00Z'), now=at)
        for key, value in (('source', 'Mirror'), ('series_id', 'OTHER/LMS'), ('source_url', 'https://example.com')):
            with self.subTest(key=key):
                altered = dict(obs, **{key: value})
                record = build_record('Arbeitsmarkt', 10, altered, 'FRESH', at.isoformat())
                self.assertFalse(eligible(record, at, factor='Arbeitsmarkt', currency='GBP')[0])
        record = build_record('Arbeitsmarkt', 10, obs, 'FRESH', '2026-10-20T05:59:59+00:00')
        self.assertFalse(eligible(record, at, factor='Arbeitsmarkt', currency='GBP')[0])

    def test_labour_reader_rejects_future_vintage_even_after_confirmed_release(self):
        from official_ons import parse_ons_labour
        from live_data import build_record, eligible
        at = datetime(2026, 10, 20, 6, tzinfo=timezone.utc)
        obs = parse_ons_labour(self.release_fixture('2026-10-19T23:00:00Z'), now=at)
        obs['release_date_known'] = '2026-10-21'
        record = build_record('Arbeitsmarkt', 10, obs, 'FRESH', at.isoformat())
        self.assertFalse(eligible(record, at, factor='Arbeitsmarkt', currency='GBP')[0])

if __name__ == '__main__': unittest.main()
