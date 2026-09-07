import unittest
from datetime import datetime, timezone
from official_hicp import parse_hicp
NOW = datetime(2026, 9, 7, 15, tzinfo=timezone.utc)
def fixture():
    filters = {'freq': 'M', 'unit': 'RCH_A', 'coicop18': 'TOTAL', 'geo': 'EA21'}
    return {'class': 'dataset', 'source': 'ESTAT', 'extension': {'id': 'PRC_HICP_MINR'},
            'id': list(filters) + ['time'], 'size': [1,1,1,1,2],
            'dimension': {**{k: {'category': {'index': {v: 0}}} for k,v in filters.items()},
                          'time': {'category': {'index': {'2026-07': 0, '2026-08': 1}}}},
            'value': {'0': 3.0, '1': 3.2}, 'status': {'1': 'e'}}
class HICPTests(unittest.TestCase):
    def test_estimate_label_preserved(self):
        result = parse_hicp(fixture(), now=NOW)
        self.assertEqual(result['value'], 3.2)
        self.assertEqual(result['date'], '2026-08-31')
        self.assertTrue(result['is_estimate'])
        self.assertEqual(result['provider_status'], 'e')
        self.assertIsNone(result['published_at'])
    def test_wrong_unit_geography_status_and_values(self):
        for key, value in [('unit','I25'), ('geo','EA20'), ('coicop18','CP01')]:
            data=fixture(); data['dimension'][key]['category']['index']={value:0}
            with self.assertRaises(ValueError):parse_hicp(data,now=NOW)
        for value in [float('nan'),True,300,'3.2']:
            data=fixture();data['value']['1']=value
            with self.assertRaises(ValueError):parse_hicp(data,now=NOW)
        data=fixture();data['status']['1']='c'
        with self.assertRaises(ValueError):parse_hicp(data,now=NOW)
    def test_negative_inflation_and_future_rejection(self):
        data=fixture();data['value']['1']=-0.2
        self.assertEqual(parse_hicp(data,now=NOW)['value'],-0.2)
        data['dimension']['time']['category']['index']={'2026-07':0,'2026-09':1}
        with self.assertRaises(ValueError):parse_hicp(data,now=NOW)
if __name__=='__main__':unittest.main()
