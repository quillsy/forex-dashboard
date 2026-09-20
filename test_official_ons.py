import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call

import requests
from official_ons import BASE, PN2_FALLBACK_URL, TITLE, parse_ons_gdp, fetch_ons_gdp

NOW = datetime(2026, 9, 7, 15, tzinfo=timezone.utc)


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
                self.assertEqual(result, dict(parse_ons_gdp(fixture(), 'PN2', now=NOW), source_url=PN2_FALLBACK_URL))
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
        self.assertEqual(result, dict(parse_ons_gdp(old, 'PN2', now=NOW), source_url=PN2_FALLBACK_URL))
        self.assertEqual(result['date'], '2025-06-30')
        self.assertEqual(result['release_date_known'], '2025-08-13')
        self.assertIsNone(result['published_at'])
        self.assertNotIn('next_due_at', result)
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

if __name__ == '__main__': unittest.main()
