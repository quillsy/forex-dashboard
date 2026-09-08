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
    def test_ch_exact_geography_and_null_latest(self):
        data = fixture()
        data['dimension']['geo']['category']['index'] = {'CH': 0}
        data['value'] = {'0': 0.7, '1': None}
        data['status'] = {}
        result = parse_hicp(data, now=NOW, geo='CH')
        self.assertEqual(result['value'], 0.7)
        self.assertEqual(result['date'], '2026-07-31')
        self.assertTrue(result['series_id'].endswith('.CH'))
        self.assertFalse(result['is_estimate'])
        with self.assertRaises(ValueError): parse_hicp(data, now=NOW)
        with self.assertRaises(ValueError): parse_hicp(fixture(), now=NOW, geo='CH')
        with self.assertRaises(ValueError): parse_hicp(data, now=NOW, geo='US')
    def test_ch_request_filter(self):
        from official_hicp import fetch_hicp
        from unittest.mock import Mock
        data = fixture()
        data['dimension']['geo']['category']['index'] = {'CH': 0}
        client = Mock(); client.get.return_value.json.return_value = data
        fetch_hicp(now=NOW, session=client, geo='CH')
        self.assertEqual(client.get.call_args.kwargs['params']['geo'], 'CH')
        client.get.assert_called_once()
if __name__=='__main__':unittest.main()


class SwissHicpTests(unittest.TestCase):
    def fixture(self):
        import io
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = '%_m-12'
        for row in [
            ['HARMONISED INDEX OF CONSUMER PRICES (HICP)'], [],
            ['Annual rate of change / Vorjahresmonat'], [],
            [' Switzerland / Suisse / Schweiz'], [],
            ['ITEM', 'Type de position', 'EN', 'FR', 'DE', '2026-07', '2026-08'],
            ['_T', 1, 'All items', 'Total', 'Total', 0.7, 0.9],
        ]:
            sheet.append(row)
        metadata = {'ids': {'damId': 123}, 'bfs': {'embargo': '2026-09-03T06:30:00Z',
                    'lifecycleGroup': 'CURRENT', 'provisional': False},
                    'description': {'titles': {'main': 'HVPI Schweiz (2025=100), Detailresultate seit 2005'},
                        'bibliography': {'period': '1.1.2006-31.8.2026'},
                        'categorization': {k: [{'code': v}] for k, v in
                            [('termsOfUse', 'OPEN-BY'), ('dataSource', 'BFS'), ('inquiry', 'HVPI'), ('periodicity', 'MONTHLY')]}},
                    'links': [{'rel': 'master', 'format': 'xlsx', 'href': 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/123/master'}]}
        return book, metadata

    def parse(self, book, metadata):
        import io
        from official_hicp import parse_swiss_hicp
        stream = io.BytesIO()
        book.save(stream)
        return parse_swiss_hicp(stream.getvalue(), metadata, now=datetime(2026, 9, 8, tzinfo=timezone.utc))

    def test_official_yoy_not_monthly_or_national_cpi(self):
        book, metadata = self.fixture()
        result = self.parse(book, metadata)
        self.assertEqual((result['value'], result['reference_period']), (0.9, '2026-08'))
        self.assertEqual(result['published_at'], '2026-09-03T06:30:00+00:00')
        self.assertIsNone(result['next_due_at'])
        for cell, value in [('A1', 'NATIONAL CPI'), ('A3', 'Monthly rate of change'), ('C8', 'Food')]:
            book, metadata = self.fixture()
            book.active[cell] = value
            with self.assertRaisesRegex(ValueError, 'WORKBOOK_INVALID'):
                self.parse(book, metadata)

    def test_missing_future_or_wrong_period_not_accepted(self):
        for cell, value in [('G8', None), ('G8', True), ('G7', '2026-09'), ('F7', '2026-06'), ('G7', '2026-07')]:
            book, metadata = self.fixture()
            book.active[cell] = value
            with self.assertRaisesRegex(ValueError, 'WORKBOOK_INVALID'):
                self.parse(book, metadata)

    def test_rights_release_and_source_contract_fail_closed(self):
        for field, bad in [('termsOfUse', 'RESTRICTED'), ('dataSource', 'OTHER'), ('inquiry', 'LIK')]:
            book, metadata = self.fixture()
            metadata['description']['categorization'][field] = [{'code': bad}]
            with self.assertRaisesRegex(ValueError, 'METADATA_INVALID'):
                self.parse(book, metadata)
        book, metadata = self.fixture()
        metadata['bfs']['embargo'] = '2026-09-09T06:30:00Z'
        with self.assertRaisesRegex(ValueError, 'METADATA_INVALID'):
            self.parse(book, metadata)

    def test_dynamic_discovery_and_ambiguous_results(self):
        import json
        from official_hicp import discover_swiss_hicp
        item = {'damId': 987, 'url': 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/987',
                'title': 'HVPI Schweiz (2025=100), Detailresultate seit 2005'}
        self.assertEqual(discover_swiss_hicp({'assetListAsJson': json.dumps({'list': [item]})}), item['url'])
        for items in [[], [item, item], [dict(item, url='https://example.org/private')]]:
            with self.assertRaisesRegex(ValueError, 'DISCOVERY_INVALID'):
                discover_swiss_hicp({'assetListAsJson': json.dumps({'list': items})})

    def test_fetch_uses_discovered_asset_and_propagates_transport_failure(self):
        import io
        import json
        import requests
        from unittest.mock import Mock
        from official_hicp import fetch_swiss_hicp, BFS_HICP_DISCOVERY
        book, metadata = self.fixture()
        stream = io.BytesIO()
        book.save(stream)
        asset = 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/123'
        discovery = {'assetListAsJson': json.dumps({'list': [{'damId': 123, 'url': asset,
                     'title': metadata['description']['titles']['main']}]})}
        responses = [Mock(), Mock(), Mock()]
        responses[0].json.return_value = discovery
        responses[1].json.return_value = metadata
        responses[2].content = stream.getvalue()
        session = Mock()
        session.get.side_effect = responses
        self.assertEqual(fetch_swiss_hicp(session=session, now=datetime(2026, 9, 8, tzinfo=timezone.utc))['value'], 0.9)
        self.assertEqual([x.args[0] for x in session.get.call_args_list], [BFS_HICP_DISCOVERY, asset, asset + '/master'])
        session.get.side_effect = requests.Timeout('offline')
        with self.assertRaises(requests.Timeout):
            fetch_swiss_hicp(session=session)

    def test_provisional_must_be_explicit_boolean_and_title_is_preserved(self):
        book, metadata = self.fixture()
        metadata['bfs']['provisional'] = True
        result = self.parse(book, metadata)
        self.assertTrue(result['is_estimate'])
        self.assertEqual(result['source_title'], metadata['description']['titles']['main'])
        for value in (None, 'false', 'true', 0, 1):
            book, metadata = self.fixture()
            metadata['bfs']['provisional'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'METADATA_INVALID'):
                self.parse(book, metadata)
        book, metadata = self.fixture()
        del metadata['bfs']['provisional']
        with self.assertRaisesRegex(ValueError, 'METADATA_INVALID'):
            self.parse(book, metadata)

    def test_invalid_json_is_contract_failure_not_transport_failure(self):
        import json
        from unittest.mock import Mock
        import requests
        from official_hicp import fetch_swiss_hicp
        asset = 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/123'
        discovery = {'assetListAsJson': json.dumps({'list': [{'damId': 123, 'url': asset,
                     'title': 'HVPI Schweiz (2025=100), Detailresultate seit 2005'}]})}
        for position, code in [(0, 'DISCOVERY_INVALID'), (1, 'METADATA_INVALID')]:
            responses = [Mock(), Mock()]
            responses[0].json.return_value = discovery
            responses[position].json.side_effect = requests.exceptions.JSONDecodeError('bad json', '<html>', 0)
            session = Mock()
            session.get.side_effect = responses
            with self.subTest(position=position), self.assertRaisesRegex(ValueError, code) as caught:
                fetch_swiss_hicp(session=session)
            self.assertNotIsInstance(caught.exception, requests.RequestException)
            self.assertEqual(session.get.call_count, position + 1)
