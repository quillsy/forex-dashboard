import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from official_macro import EUROSTAT_SPECS, parse_eurostat_observation, fetch_eurostat_observation

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def fixture(category):
    spec = EUROSTAT_SPECS[category]
    times = ["2026-06", "2026-07", "2026-08"] if category == "Arbeitsmarkt" else ["2026-Q1", "2026-Q2"]
    dimensions = {name: {"category": {"index": {code: 0}}} for name, code in spec["filters"].items()}
    dimensions["time"] = {"category": {"index": dict(zip(times, range(len(times))))}}
    return {"class": "dataset", "source": "ESTAT", "extension": {"id": spec["dataset"].upper()}, "id": list(dimensions), "size": [1] * len(spec["filters"]) + [len(times)], "dimension": dimensions, "value": {"0": 6.4, "1": 6.4} if category == "Arbeitsmarkt" else {"0": 0.6, "1": 1.2}}


class EurostatTests(unittest.TestCase):
    def test_actual_shapes_and_unknown_publication(self):
        for category, expected in [("Arbeitsmarkt", (6.4, "2026-07-31", "PC_ACT")), ("GDP", (1.2, "2026-06-30", "CLV_PCH_SM"))]:
            result = parse_eurostat_observation(fixture(category), category, now=NOW)
            self.assertEqual((result["value"], result["date"], result["unit"]), expected)
            self.assertIsNone(result["published_at"])
            self.assertEqual(result["geography"], "EA21")

    def test_swiss_gdp_contract_and_unknown_publication(self):
        data = fixture("GDP")
        data["dimension"]["geo"]["category"]["index"] = {"CH": 0}
        data["extension"]["annotation"] = [{"type": "SOURCE_INSTITUTIONS", "text": "Eurostat"}]
        data["value"]["1"] = 2.6
        result = parse_eurostat_observation(data, "GDP", now=NOW, geo="CH")
        self.assertEqual((result["value"], result["reference_period"], result["geography"]), (2.6, "2026-Q2", "CH"))
        self.assertIsNone(result["published_at"])
        self.assertTrue(result["series_id"].endswith(".CH"))
        session = Mock(); session.get.return_value.json.return_value = data
        self.assertEqual(fetch_eurostat_observation("GDP", now=NOW, session=session, geo="CH")["value"], 2.6)
        self.assertEqual(session.get.call_args.kwargs["params"]["geo"], "CH")

    def test_swiss_wrong_geography_factor_and_third_party_rejected(self):
        data = fixture("GDP")
        data["extension"]["annotation"] = [{"type": "SOURCE_INSTITUTIONS", "text": "Eurostat"}]
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW, geo="CH")
        data["dimension"]["geo"]["category"]["index"] = {"CH": 0}
        for text in ("SECO", "Other", None):
            data["extension"]["annotation"][0]["text"] = text
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW, geo="CH")
        for category, geo in (("Arbeitsmarkt", "CH"), ("GDP", "JP")):
            session = Mock()
            with self.assertRaises(ValueError): fetch_eurostat_observation(category, session=session, geo=geo)
            session.get.assert_not_called()

    def test_dimensions_are_exact(self):
        for dimension, wrong in [("unit", "CLV_PCH_PRE"), ("geo", "EA20"), ("s_adj", "NSA"), ("na_item", "P3")]:
            data = fixture("GDP")
            data["dimension"][dimension]["category"]["index"] = {wrong: 0}
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_bad_values_and_future_quarter_rejected(self):
        for value in [float("nan"), float("inf"), True, "1.2", -101]:
            data = fixture("GDP"); data["value"]["1"] = value
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)
        data = fixture("GDP")
        data["dimension"]["time"]["category"]["index"] = {"2026-Q1": 0, "2026-Q3": 1}
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_empty_null_and_positions(self):
        data = fixture("GDP"); data["value"] = {}
        self.assertIsNone(parse_eurostat_observation(data, "GDP", now=NOW))
        for corrupt in [{"2026-Q1": 0, "2026-Q2": 0}, {"2026-Q1": 0, "2026-Q2": 2}]:
            data = fixture("GDP"); data["dimension"]["time"]["category"]["index"] = corrupt
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)
        data = fixture("GDP"); data["value"]["01"] = 99
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_provisional_retained_confidential_rejected(self):
        data = fixture("GDP"); data["status"] = {"1": "p"}
        self.assertEqual(parse_eurostat_observation(data, "GDP", now=NOW)["provider_status"], "p")
        data["status"]["1"] = "c"
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_single_bounded_keyless_request(self):
        session = Mock(); session.get.return_value.json.return_value = fixture("GDP")
        result = fetch_eurostat_observation("GDP", now=NOW, session=session)
        self.assertEqual(result["value"], 1.2)
        session.get.assert_called_once()
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(kwargs["params"]["unit"], "CLV_PCH_SM")
        self.assertNotIn("api_key", kwargs["params"])
        session.get.return_value.raise_for_status.assert_called_once()

from official_macro import ABS_SPECS, parse_abs_observation, fetch_abs_observation


def abs_csv(category):
    import csv
    import io
    spec = ABS_SPECS[category]
    fields = ['DATAFLOW', *spec['dimensions'], 'TIME_PERIOD', 'OBS_VALUE', 'UNIT_MEASURE', 'UNIT_MULT', 'OBS_STATUS']
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    values = [('2026-07', '4.46182469'), ('2026-06', '4.43154789')] if category == 'Arbeitsmarkt' else [('2026-Q2', '699461'), ('2025-Q2', '684822'), ('2026-Q1', '696539')]
    for period, value in values:
        writer.writerow({'DATAFLOW': f"ABS:{spec['flow']}(1.0.0)", **spec['dimensions'],
                         'TIME_PERIOD': period, 'OBS_VALUE': value, 'UNIT_MEASURE': spec['unit'],
                         'UNIT_MULT': spec['multiplier'], 'OBS_STATUS': ''})
    return output.getvalue()


def abs_release(category):
    period, published, due = ('July 2026', '20/08/2026', '24/09/2026') if category == 'Arbeitsmarkt' else ('June 2026', '02/09/2026', '02/12/2026')
    return f'''<h1>{ABS_SPECS[category]['title']}</h1>
    <div class="field--name-field-abs-reference-period"><div class="field__item">{period}</div></div>
    <div id="release-date-section"><div class="field--name-dynamic-twig-fieldnode-release-or-orig-publish"><div class="field__item">{published}</div></div></div>
    <div class="logged-user-only field--name-field-abs-release-date"><div class="field__item">{published} 11:30am AEST</div></div>
    <ul><li class="future-release">Next Release {due}</li></ul>'''


class AbsTests(unittest.TestCase):
    def fetch(self, category, csv=None, html=None, now=NOW):
        session = Mock()
        session.get.side_effect = [Mock(text=csv if csv is not None else abs_csv(category)),
                                   Mock(text=html if html is not None else abs_release(category))]
        return fetch_abs_observation(category, session=session, now=now), session

    def test_actual_csv_shapes_sorted_and_exact_yoy(self):
        labour, _ = self.fetch('Arbeitsmarkt')
        self.assertEqual((labour['value'], labour['reference_period']), (4.46182469, '2026-07'))
        gdp, _ = self.fetch('GDP')
        self.assertAlmostEqual(gdp['value'], 2.137635765206136)
        self.assertEqual(gdp['date'], '2026-06-30')
        self.assertEqual(gdp['published_at'], '2026-09-02T01:30:00+00:00')
        self.assertEqual(gdp['next_release_date'], '2026-12-02')
        self.assertEqual(gdp['next_due_at'], '2026-12-01T13:00:00+00:00')
        self.assertEqual(gdp['next_due_precision'], 'date_only_start_of_AU_day')
        self.assertTrue(gdp['needs_hourly_check'])
        self.assertEqual(gdp['frequency'], 'quarterly')
        self.assertEqual(labour['frequency'], 'monthly')

    def test_contract_rejects_wrong_series_units_frequency_adjustment(self):
        data = abs_csv('GDP')
        for old, new in [('M1', 'M2'), ('GPM', 'GPM_PCA'), (',20,', ',30,'), (',AUS,', ',1,'), (',Q,', ',A,'), (',AUD,6,', ',PCT,0,'), ('(1.0.0)', '(2.0.0)')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                parse_abs_observation(data.replace(old, new), 'GDP', now=NOW)
        for old, new in [('M13', 'M6'), (',3,', ',1,'), ('1599', '1564'), (',PCT,0,', ',PCT,3,')]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                parse_abs_observation(abs_csv('Arbeitsmarkt').replace(old, new), 'Arbeitsmarkt', now=NOW)

    def test_latest_missing_invalid_or_unavailable_never_falls_back(self):
        for value in ['', 'NaN', 'inf', '-1', '101']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_abs_observation(abs_csv('Arbeitsmarkt').replace('4.46182469', value), 'Arbeitsmarkt', now=NOW)
        data = abs_csv('Arbeitsmarkt').replace('4.46182469,PCT,0,', '4.46182469,PCT,0,q')
        with self.assertRaises(ValueError): parse_abs_observation(data, 'Arbeitsmarkt', now=NOW)

    def test_duplicate_and_future_and_missing_matching_quarter(self):
        data = abs_csv('GDP')
        for bad in [data + data.splitlines()[1] + '\n', data.replace('2026-Q2', '2026-Q3'), data.replace('2025-Q2', '2025-Q1')]:
            with self.assertRaises(ValueError): parse_abs_observation(bad, 'GDP', now=NOW)
        with self.assertRaises(ValueError): parse_abs_observation(data.splitlines()[0] + '\n', 'GDP', now=NOW)

    def test_api_mirror_lag_rejected_even_when_request_succeeds(self):
        with self.assertRaisesRegex(ValueError, 'lags or conflicts'):
            self.fetch('Arbeitsmarkt', csv=abs_csv('Arbeitsmarkt').replace('2026-07', '2026-05'))
        with self.assertRaisesRegex(ValueError, 'lags or conflicts'):
            self.fetch('GDP', html=abs_release('GDP').replace('June 2026', 'March 2026'))

    def test_future_publication_due_release_and_ambiguous_page(self):
        for html in [abs_release('GDP').replace('02/09/2026', '09/09/2026'), abs_release('GDP').replace('02/12/2026', '07/09/2026'), abs_release('GDP').replace('<h1>', '<h1>Wrong ')]:
            with self.assertRaises(ValueError): self.fetch('GDP', html=html)

    def test_related_article_dates_are_not_release_dates(self):
        html = abs_release('Arbeitsmarkt') + '<a class="card"><div class="field--name-field-abs-release-date"><div class="field__item">20 August 2026</div></div></a>'
        observation, _ = self.fetch('Arbeitsmarkt', html=html)
        self.assertEqual(observation['published_at'], '2026-08-20T01:30:00+00:00')

    def test_date_only_publication_remains_unknown(self):
        import re
        html = re.sub(r'<div class="logged-user-only field--name-field-abs-release-date">.*?</div></div>', '', abs_release('GDP'))
        observation, _ = self.fetch('GDP', html=html)
        self.assertIsNone(observation['published_at'])
        self.assertEqual(observation['release_date_known'], '2026-09-02')
        self.assertEqual(observation['next_due_at'], '2026-12-01T13:00:00+00:00')

    def test_date_only_deadline_australian_day_boundary(self):
        before = datetime(2026, 9, 23, 13, 59, tzinfo=timezone.utc)
        result, _ = self.fetch('Arbeitsmarkt', now=before)
        self.assertEqual(result['next_due_at'], '2026-09-23T14:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'Scheduled ABS release'):
            self.fetch('Arbeitsmarkt', now=datetime(2026, 9, 23, 14, tzinfo=timezone.utc))

    def test_two_bounded_keyless_uncached_requests_and_http_failure(self):
        _, session = self.fetch('GDP')
        self.assertEqual(session.get.call_count, 2)
        for call in session.get.call_args_list: self.assertEqual(call.kwargs['timeout'], 20)
        self.assertEqual(session.get.call_args_list[0].kwargs['params']['format'], 'csv')
        session = Mock()
        session.get.return_value.raise_for_status.side_effect = RuntimeError('HTTP unavailable')
        with self.assertRaises(RuntimeError): fetch_abs_observation('GDP', now=NOW, session=session)
        session.get.assert_called_once()




from official_macro import STATCAN_LABOUR_COORD, STATCAN_LABOUR_TITLE, parse_statcan_labour, fetch_statcan_labour


def statcan_fixture():
    identity = {'responseStatusCode': 0, 'productId': 14100287, 'coordinate': STATCAN_LABOUR_COORD, 'vectorId': 2062815}
    series = dict(identity, SeriesTitleEn=STATCAN_LABOUR_TITLE, memberUomCode=239, frequencyCode=6, scalarFactorCode=0, decimals=1, terminated=0)
    points = [dict(refPer=period, value=value, decimals=1, scalarFactorCode=0, symbolCode=0, statusCode=0,
                   securityLevelCode=0, releaseTime='2026-09-04T08:30', frequencyCode=6)
              for period,value in [('2026-08-01',6.4),('2026-07-01',6.4),('2026-06-01',6.5)]]
    members = [('Geography',1,'Canada'),('Labour force characteristics',7,'Unemployment rate'),('Gender',1,'Total - Gender'),
               ('Age group',1,'15 years and over'),('Statistics',1,'Estimate'),('Data type',1,'Seasonally adjusted')]
    dims = [{'dimensionPositionId': i+1, 'dimensionNameEn': name,
             'member': [{'memberId': code, 'memberNameEn': label, 'terminated': 0, 'memberUomCode': 239 if i==1 else None}]}
            for i,(name,code,label) in enumerate(members)]
    cube = {'responseStatusCode':0,'productId':'14100287','frequencyCode':6,'archiveStatusCode':'2',
            'cubeTitleEn':'Labour force characteristics, monthly, seasonally adjusted and trend-cycle',
            'cubeEndDate':'2026-08-01','releaseTime':'2026-09-04T08:30','dimension':dims}
    return [[{'status':'SUCCESS','object':obj}] for obj in (series, dict(identity,vectorDataPoint=points),cube)]


class StatcanLabourTests(unittest.TestCase):
    def test_exact_current_observation_and_eastern_time(self):
        result=parse_statcan_labour(*statcan_fixture(),now=NOW)
        self.assertEqual((result['value'],result['reference_period']), (6.4,'2026-08'))
        self.assertEqual(result['published_at'],'2026-09-04T12:30:00+00:00')
        self.assertTrue(result['needs_hourly_check'])
        self.assertIsNone(result['next_due_at'])
        self.assertEqual(result['frequency'],'monthly')

    def test_wrong_series_units_and_dimension_are_rejected(self):
        for key,value in [('vectorId',1),('coordinate','1.7.1.1.1.3.0.0.0.0'),('memberUomCode',428),('scalarFactorCode',3),('frequencyCode',9),('SeriesTitleEn','Other'),('terminated',1)]:
            data=statcan_fixture();data[0][0]['object'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)
        data=statcan_fixture();data[2][0]['object']['dimension'][5]['member'][0]['memberNameEn']='Trend-cycle'
        with self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)

    def test_invalid_latest_never_falls_back(self):
        for value in [None,float('nan'),float('inf'),True,'6.4',-1,101]:
            data=statcan_fixture();data[1][0]['object']['vectorDataPoint'][0]['value']=value
            with self.subTest(value=value),self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)
        for key in ('statusCode','symbolCode','securityLevelCode','scalarFactorCode'):
            data=statcan_fixture();data[1][0]['object']['vectorDataPoint'][0][key]=1
            with self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)

    def test_latest_cube_mismatch_and_duplicate_future_periods(self):
        data=statcan_fixture();data[1][0]['object']['vectorDataPoint'].pop(0)
        with self.assertRaisesRegex(ValueError,'lags latest'):parse_statcan_labour(*data,now=NOW)
        for change in ('duplicate','future','release'):
            data=statcan_fixture();points=data[1][0]['object']['vectorDataPoint']
            if change=='duplicate':points.append(dict(points[0]))
            elif change=='future':points[0]['refPer']='2026-09-01'
            else:points[0]['releaseTime']='2026-09-08T08:30'
            with self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)

    def test_ambiguous_and_error_responses(self):
        for payload in [[], [{'status':'FAILED'}], statcan_fixture()[0]*2]:
            data=statcan_fixture();data[0]=payload
            with self.assertRaises(ValueError):parse_statcan_labour(*data,now=NOW)

    def test_three_keyless_bounded_requests_and_failed_fetch(self):
        session=Mock();session.post.side_effect=[Mock(json=Mock(return_value=p)) for p in statcan_fixture()]
        self.assertEqual(fetch_statcan_labour(now=NOW,session=session)['value'],6.4)
        self.assertEqual(session.post.call_count,3)
        for call in session.post.call_args_list:self.assertEqual(call.kwargs['timeout'],20)
        self.assertNotIn('latestN',session.post.call_args_list[0].kwargs['json'][0])
        self.assertEqual(session.post.call_args_list[1].kwargs['json'][0]['latestN'],3)
        session=Mock();session.post.return_value.raise_for_status.side_effect=RuntimeError('409')
        with self.assertRaises(RuntimeError):fetch_statcan_labour(now=NOW,session=session)
        session.post.assert_called_once()


if __name__ == '__main__': unittest.main()


from official_macro import parse_statcan_gdp, fetch_statcan_gdp


def statcan_gdp_fixture():
    # Captured official contract and five observations, 2026-09-08.
    return [[{'object': {'SeriesTitleEn': 'Canada;Chained (2017) dollars;Seasonally adjusted at annual rates;Gross '
                                   'domestic product at market prices',
                  'SeriesTitleFr': 'Canada;Dollars enchaînés (2017);Désaisonnalisées au taux annuel;Produit '
                                   'intérieur brut aux prix du marché',
                  'coordinate': '1.1.1.30.0.0.0.0.0.0',
                  'decimals': 0,
                  'frequencyCode': 9,
                  'memberUomCode': 81,
                  'productId': 36100104,
                  'responseStatusCode': 0,
                  'scalarFactorCode': 6,
                  'terminated': 0,
                  'vectorId': 62305752},
       'status': 'SUCCESS'}],
     [{'object': {'coordinate': '1.1.1.30.0.0.0.0.0.0',
                  'productId': 36100104,
                  'responseStatusCode': 0,
                  'vectorDataPoint': [{'decimals': 0,
                                       'frequencyCode': 9,
                                       'refPer': '2025-04-01',
                                       'refPer2': '',
                                       'refPerRaw': '2025-06-01',
                                       'refPerRaw2': '',
                                       'releaseTime': '2026-05-29T08:30',
                                       'scalarFactorCode': 6,
                                       'securityLevelCode': 0,
                                       'statusCode': 0,
                                       'symbolCode': 0,
                                       'value': 2495975.0},
                                      {'decimals': 0,
                                       'frequencyCode': 9,
                                       'refPer': '2025-07-01',
                                       'refPer2': '',
                                       'refPerRaw': '2025-09-01',
                                       'refPerRaw2': '',
                                       'releaseTime': '2026-05-29T08:30',
                                       'scalarFactorCode': 6,
                                       'securityLevelCode': 0,
                                       'statusCode': 0,
                                       'symbolCode': 0,
                                       'value': 2507754.0},
                                      {'decimals': 0,
                                       'frequencyCode': 9,
                                       'refPer': '2025-10-01',
                                       'refPer2': '',
                                       'refPerRaw': '2025-12-01',
                                       'refPerRaw2': '',
                                       'releaseTime': '2026-05-29T08:30',
                                       'scalarFactorCode': 6,
                                       'securityLevelCode': 0,
                                       'statusCode': 0,
                                       'symbolCode': 0,
                                       'value': 2501573.0},
                                      {'decimals': 0,
                                       'frequencyCode': 9,
                                       'refPer': '2026-01-01',
                                       'refPer2': '',
                                       'refPerRaw': '2026-03-01',
                                       'refPerRaw2': '',
                                       'releaseTime': '2026-08-28T08:30',
                                       'scalarFactorCode': 6,
                                       'securityLevelCode': 0,
                                       'statusCode': 0,
                                       'symbolCode': 0,
                                       'value': 2503604.0},
                                      {'decimals': 0,
                                       'frequencyCode': 9,
                                       'refPer': '2026-04-01',
                                       'refPer2': '',
                                       'refPerRaw': '2026-06-01',
                                       'refPerRaw2': '',
                                       'releaseTime': '2026-08-28T08:30',
                                       'scalarFactorCode': 6,
                                       'securityLevelCode': 0,
                                       'statusCode': 0,
                                       'symbolCode': 0,
                                       'value': 2524127.0}],
                  'vectorId': 62305752},
       'status': 'SUCCESS'}],
     [{'object': {'archiveStatusCode': '2',
                  'cubeEndDate': '2026-04-01',
                  'cubeTitleEn': 'Gross domestic product, expenditure-based, Canada, quarterly',
                  'dimension': [{'dimensionNameEn': 'Geography',
                                 'dimensionPositionId': 1,
                                 'hasUom': False,
                                 'member': [{'classificationCode': '11124',
                                             'classificationTypeCode': '1',
                                             'geoLevel': 0,
                                             'memberId': 1,
                                             'memberNameEn': 'Canada',
                                             'memberNameFr': 'Canada',
                                             'memberUomCode': None,
                                             'parentMemberId': None,
                                             'terminated': 0,
                                             'vintage': 2016}]},
                                {'dimensionNameEn': 'Prices',
                                 'dimensionPositionId': 2,
                                 'hasUom': True,
                                 'member': [{'classificationCode': None,
                                             'classificationTypeCode': None,
                                             'geoLevel': None,
                                             'memberId': 1,
                                             'memberNameEn': 'Chained (2017) dollars',
                                             'memberNameFr': 'Dollars enchaînés (2017)',
                                             'memberUomCode': 81,
                                             'parentMemberId': None,
                                             'terminated': 0,
                                             'vintage': None}]},
                                {'dimensionNameEn': 'Seasonal adjustment',
                                 'dimensionPositionId': 3,
                                 'hasUom': False,
                                 'member': [{'classificationCode': None,
                                             'classificationTypeCode': None,
                                             'geoLevel': None,
                                             'memberId': 1,
                                             'memberNameEn': 'Seasonally adjusted at annual rates',
                                             'memberNameFr': 'Désaisonnalisées au taux annuel',
                                             'memberUomCode': None,
                                             'parentMemberId': None,
                                             'terminated': 0,
                                             'vintage': None}]},
                                {'dimensionNameEn': 'Estimates',
                                 'dimensionPositionId': 4,
                                 'hasUom': False,
                                 'member': [{'classificationCode': None,
                                             'classificationTypeCode': None,
                                             'geoLevel': None,
                                             'memberId': 30,
                                             'memberNameEn': 'Gross domestic product at market prices',
                                             'memberNameFr': 'Produit intérieur brut aux prix du marché',
                                             'memberUomCode': None,
                                             'parentMemberId': None,
                                             'terminated': 0,
                                             'vintage': None}]}],
                  'frequencyCode': 9,
                  'productId': '36100104',
                  'releaseTime': '2026-08-28T08:30',
                  'responseStatusCode': 0},
       'status': 'SUCCESS'}]]

class StatCanGDPTests(unittest.TestCase):
    def test_current_same_vintage_four_quarter_yoy(self):
        result = parse_statcan_gdp(*statcan_gdp_fixture(), now=NOW)
        self.assertAlmostEqual(result['value'], 1.127895912418997)
        self.assertNotAlmostEqual(result['value'], 100 * ((2524127 / 2503604) ** 4 - 1))
        self.assertEqual(result['reference_period'], '2026-Q2')
        self.assertEqual(result['date'], '2026-06-30')
        self.assertEqual(result['published_at'], '2026-08-28T12:30:00+00:00')
        self.assertTrue(result['needs_hourly_check'])

    def test_wrong_series_units_and_adjustment_rejected(self):
        for key, value in [('vectorId',1), ('frequencyCode',6), ('scalarFactorCode',0),
                           ('memberUomCode',239), ('SeriesTitleEn','Monthly GDP by industry')]:
            payloads=statcan_gdp_fixture(); payloads[0][0]['object'][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                parse_statcan_gdp(*payloads, now=NOW)
        payloads=statcan_gdp_fixture()
        payloads[2][0]['object']['dimension'][2]['member'][0]['memberNameEn']='Unadjusted'
        with self.assertRaises(ValueError): parse_statcan_gdp(*payloads,now=NOW)

    def test_bad_latest_or_baseline_never_falls_back(self):
        for position in (0,4):
            for value in (None, True, '100', float('nan'), float('inf'), 0, -1):
                payloads=statcan_gdp_fixture(); payloads[1][0]['object']['vectorDataPoint'][position]['value']=value
                with self.subTest(position=position,value=value), self.assertRaises(ValueError):
                    parse_statcan_gdp(*payloads,now=NOW)

    def test_missing_duplicate_and_wrong_raw_quarter_rejected(self):
        for mutation in ('missing','duplicate','raw','gap','monthly'):
            payloads=statcan_gdp_fixture(); points=payloads[1][0]['object']['vectorDataPoint']
            if mutation=='missing': points.pop(2)
            elif mutation=='duplicate': points[1]=dict(points[0])
            elif mutation=='raw': points[-1]['refPerRaw']='2026-04-01'
            elif mutation=='monthly': points[-1]['refPer']='2026-05-01'
            else: points[1].update(refPer='2024-07-01', refPerRaw='2024-09-01')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parse_statcan_gdp(*payloads,now=NOW)

    def test_future_or_unavailable_and_cube_lag_rejected(self):
        for field,value in [('releaseTime','2026-09-10T08:30'),('symbolCode',1),('statusCode',1),('securityLevelCode',1)]:
            payloads=statcan_gdp_fixture();payloads[1][0]['object']['vectorDataPoint'][-1][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError):parse_statcan_gdp(*payloads,now=NOW)
        for field,value in [('cubeEndDate','2026-07-01'),('releaseTime','2026-09-01T08:30')]:
            payloads=statcan_gdp_fixture();payloads[2][0]['object'][field]=value
            with self.assertRaisesRegex(ValueError,'lags latest'):parse_statcan_gdp(*payloads,now=NOW)

    def test_keyless_fetch_and_json_contract_failure(self):
        import requests
        session=Mock();session.post.side_effect=[Mock(json=Mock(return_value=p)) for p in statcan_gdp_fixture()]
        self.assertAlmostEqual(fetch_statcan_gdp(now=NOW,session=session)['value'],1.127895912418997)
        self.assertEqual(session.post.call_count,3)
        self.assertEqual(session.post.call_args_list[1].kwargs['json'][0]['latestN'],5)
        self.assertTrue(all(c.kwargs['timeout']==20 for c in session.post.call_args_list))
        session=Mock();session.post.return_value.json.side_effect=requests.exceptions.JSONDecodeError('bad','x',0)
        with self.assertRaisesRegex(ValueError,'STATCAN_GDP_JSON_INVALID') as caught:fetch_statcan_gdp(now=NOW,session=session)
        self.assertNotIsInstance(caught.exception,requests.RequestException)
        session=Mock();session.post.side_effect=requests.Timeout('offline')
        with self.assertRaises(requests.Timeout):fetch_statcan_gdp(now=NOW,session=session)

    def test_confirmed_next_release_blocks_at_eastern_day_boundary(self):
        before = datetime(2026, 11, 30, 4, 59, tzinfo=timezone.utc)
        result = parse_statcan_gdp(*statcan_gdp_fixture(), now=before)
        self.assertEqual(result['next_due_at'], '2026-11-30T05:00:00+00:00')
        self.assertEqual(result['next_due_precision'], 'date_only_start_of_CA_Eastern_day')
        self.assertTrue(result['needs_hourly_check'])
        with self.assertRaisesRegex(ValueError, 'Scheduled StatCan GDP'):
            parse_statcan_gdp(*statcan_gdp_fixture(), now=datetime(2026,11,30,5,tzinfo=timezone.utc))
