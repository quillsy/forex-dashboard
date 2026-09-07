import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from official_ons import TITLE, parse_ons_gdp, fetch_ons_gdp

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
