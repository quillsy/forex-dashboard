import ast
import copy
import unittest
from pathlib import Path
import requests
from datetime import datetime, timezone
from unittest.mock import Mock
from official_macro import EUROSTAT_SPECS, STATCAN_BASE, STATCAN_CPI_COORD, parse_eurostat_observation, fetch_eurostat_observation, validate_statcan_cpi

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


class StatCanCpiContractTests(unittest.TestCase):
    now = datetime(2026, 10, 2, 22, tzinfo=timezone.utc)

    @staticmethod
    def fixture():
        identity = {'responseStatusCode': 0, 'productId': 18100004, 'vectorId': 41690973,
                    'coordinate': STATCAN_CPI_COORD}
        series = dict(identity, SeriesTitleEn='Canada;All-items', memberUomCode=17,
                      frequencyCode=6, scalarFactorCode=0, decimals=1, terminated=0)
        points = []
        for serial in range(2025 * 12 + 7, 2026 * 12 + 8):
            y, m = divmod(serial, 12); period = f'{y}-{m + 1:02d}-01'
            points.append({'refPer': period, 'refPerRaw': period, 'refPer2': '', 'refPerRaw2': '',
                           'value': 164.8 if len(points) == 0 else 169.8,
                           'frequencyCode': 6, 'scalarFactorCode': 0, 'decimals': 1,
                           'symbolCode': 0, 'statusCode': 0, 'securityLevelCode': 0,
                           'releaseTime': '2026-09-14T08:30'})
        data = dict(identity, vectorDataPoint=points)
        cube = {'responseStatusCode': 0, 'productId': '18100004', 'frequencyCode': 6,
                'archiveStatusCode': '2', 'cubeTitleEn': 'Consumer Price Index, monthly, not seasonally adjusted',
                'cubeEndDate': '2026-08-01', 'releaseTime': '2026-09-14T08:30',
                'dimension': [{'dimensionPositionId': 1, 'dimensionNameEn': 'Geography',
                    'member': [{'memberId': 2, 'memberNameEn': 'Canada', 'terminated': 0}]},
                    {'dimensionPositionId': 2, 'dimensionNameEn': 'Products and product groups',
                    'member': [{'memberId': 2, 'memberNameEn': 'All-items', 'terminated': 0, 'memberUomCode': 17}]}]}
        return [[{'status': 'SUCCESS', 'object': o}] for o in (series, data, cube)]

    def test_exact_index_and_yoy_inputs_unchanged(self):
        payloads = self.fixture(); original = copy.deepcopy(payloads)
        points = validate_statcan_cpi(*payloads, now=self.now)
        self.assertEqual(payloads, original)
        self.assertEqual(len(points), 13)
        self.assertAlmostEqual((points[-1]['value'] / points[0]['value'] - 1) * 100, 3.033980582524265)
        self.assertIsNone(points[-1]['provider_status']); self.assertFalse(points[-1]['is_estimate'])

    def test_series_identity_unit_scaling_frequency_and_termination(self):
        for obj in (0, 1):
            for key, value in [('vectorId', 62305752), ('productId', 36100104),
                               ('coordinate', '1.1.1.30.0.0.0.0.0.0'), ('vectorId', '41690973')]:
                ps = self.fixture(); ps[obj][0]['object'][key] = value
                with self.subTest(obj=obj, key=key), self.assertRaises(ValueError):
                    validate_statcan_cpi(*ps, now=self.now)
        for key, value in [('SeriesTitleEn', 'Canada;Food'), ('memberUomCode', 239),
                           ('frequencyCode', 9), ('scalarFactorCode', 6), ('decimals', 2), ('terminated', 1)]:
            ps = self.fixture(); ps[0][0]['object'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_cube_definition_dimension_and_index_basis(self):
        for key, value in [('productId', '14100287'), ('frequencyCode', 9), ('archiveStatusCode', '1'),
                           ('cubeTitleEn', 'Consumer Price Index, seasonally adjusted')]:
            ps = self.fixture(); ps[2][0]['object'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
        for position, key, value in [(0, 'memberNameEn', 'Ontario'), (1, 'memberUomCode', 239),
                                     (1, 'terminated', 1), (1, 'memberId', True)]:
            ps = self.fixture(); ps[2][0]['object']['dimension'][position]['member'][0][key] = value
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_response_failures_ambiguity_and_boolean_codes(self):
        for pos in range(3):
            for replacement in ([], [{'status': 'FAILURE'}], [None]):
                ps = self.fixture(); ps[pos] = replacement
                with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
            ps = self.fixture(); ps[pos].append(copy.deepcopy(ps[pos][0]))
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
            ps = self.fixture(); ps[pos][0]['object']['responseStatusCode'] = False
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_point_metadata_rejects_wrong_frequency_scaling_status_and_suppression(self):
        for pos in (0, -1):
            for key, value in [('frequencyCode', 9), ('scalarFactorCode', 6), ('decimals', 2),
                               ('statusCode', 1), ('statusCode', 7), ('securityLevelCode', 1),
                               ('symbolCode', 2), ('symbolCode', True), ('scalarFactorCode', False)]:
                ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][pos][key] = value
                with self.subTest(pos=pos, key=key), self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_preliminary_and_revision_flags_survive_for_both_comparison_months(self):
        for pos in (0, -1):
            for code, flag in ((1, 'p'), (3, 'r')):
                ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][pos]['symbolCode'] = code
                points = validate_statcan_cpi(*ps, now=self.now)
                self.assertEqual(points[pos]['provider_status'], flag)
                self.assertEqual(points[pos]['is_estimate'], code == 1)

    def test_missing_month_duplicate_conflict_and_invalid_index(self):
        for position in (0, 6, -1):
            ps = self.fixture(); del ps[1][0]['object']['vectorDataPoint'][position]
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
        ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'].append(copy.deepcopy(ps[1][0]['object']['vectorDataPoint'][-1]))
        with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
        for val in (None, True, 0, -1, float('nan'), float('inf'), '169.8'):
            ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][-1]['value'] = val
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_period_reference_fields_and_future_measurement(self):
        for key, val in [('refPer', '2026-08-31'), ('refPerRaw', '2026-07-01'),
                         ('refPer2', '2026-08-01'), ('refPerRaw2', '2026-08-01')]:
            ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][-1][key] = val
            with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)
        ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][-1].update(refPer='2026-10-01', refPerRaw='2026-10-01')
        with self.assertRaises(ValueError): validate_statcan_cpi(*ps, now=self.now)

    def test_latest_cube_release_must_match_and_future_embargo_blocks(self):
        for key, value in [('cubeEndDate', '2026-09-01'), ('releaseTime', '2026-09-15T08:30')]:
            ps = self.fixture(); ps[2][0]['object'][key] = value
            with self.assertRaisesRegex(ValueError, 'RELEASE_LAG'): validate_statcan_cpi(*ps, now=self.now)
        ps = self.fixture()
        with self.assertRaisesRegex(ValueError, 'FUTURE_RELEASE'):
            validate_statcan_cpi(*ps, now=datetime(2026, 9, 14, 12, 29, tzinfo=timezone.utc))
        validate_statcan_cpi(*ps, now=datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc))

    def loader(self, payloads):
        import pandas as pd
        import live_data
        n = next(n for n in ast.parse(Path(__file__).with_name('app.py').read_text()).body
                 if isinstance(n, ast.FunctionDef) and n.name == 'get_statcan_cpi_data')
        n.decorator_list = []
        client = Mock(); responses = []
        for p in (payloads[1], payloads[0], payloads[2]):
            r = Mock(status_code=200); r.json.return_value = p; responses.append(r)
        client.post.side_effect = responses
        ns = {'pd': pd, 'datetime': datetime, 'requests': client, 'live_data': live_data,
              'check_demo_active': lambda: False}
        exec(compile(ast.Module(body=[n], type_ignores=[]), '<cpi-loader>', 'exec'), ns)
        return ns['get_statcan_cpi_data'], client

    def test_loader_uses_exact_three_checks_and_rejects_wrong_series(self):
        loader, client = self.loader(self.fixture()); frame, _, is_live = loader(propagate_transport=True)
        self.assertTrue(is_live); self.assertEqual(len(frame), 13)
        self.assertEqual(client.post.call_count, 3)
        self.assertEqual([c.args[0] for c in client.post.call_args_list],
                         [STATCAN_BASE + s for s in ('getDataFromVectorsAndLatestNPeriods', 'getSeriesInfoFromVector', 'getCubeMetadata')])
        self.assertTrue(all(c.kwargs['timeout'] == 15 for c in client.post.call_args_list))
        ps = self.fixture(); ps[1][0]['object']['vectorId'] = 62305752
        loader, _ = self.loader(ps)
        self.assertFalse(loader(propagate_transport=True)[-1])

    def test_loader_retains_publication_and_symbol_annotations(self):
        import pandas as pd
        ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][0]['symbolCode'] = 1
        ps[1][0]['object']['vectorDataPoint'][-1]['symbolCode'] = 3
        loader, _ = self.loader(ps); frame, _, is_live = loader()
        self.assertTrue(is_live)
        self.assertEqual(frame.iloc[0]['provider_status'], 'p'); self.assertTrue(frame.iloc[0]['is_estimate'])
        self.assertEqual(frame.iloc[-1]['provider_status'], 'r')
        self.assertEqual(frame.iloc[-1]['release_date'], pd.Timestamp('2026-09-14T12:30'))

    def test_comparison_flags_reach_actual_cpi_writer_and_public_record(self):
        import os
        import pandas as pd
        import live_data
        from unittest.mock import patch
        from test_core_regressions import load_core
        checked = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return checked.astimezone(tz) if tz else checked.replace(tzinfo=None)
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_cpi_yoy_details')
        for position, symbol, expected in ((0, 1, 'p'), (-1, 1, 'p'), (0, 3, 'r'), (-1, 3, 'r')):
            ps = self.fixture(); ps[1][0]['object']['vectorDataPoint'][position]['symbolCode'] = symbol
            loader, _ = self.loader(ps); frame, _, _ = loader()
            core = load_core()
            exec(compile(ast.Module(body=[route], type_ignores=[]), '<actual-cpi-route>', 'exec'), core)
            core.update(datetime=Clock, pd=pd, requests=requests, FRED_KEY='test-only',
                        get_statcan_cpi_data=Mock(return_value=(frame, checked, True)),
                        get_ons_cpi_data=Mock(), get_statsnz_cpi_data=Mock())
            with patch.dict(os.environ, {'FX_COLLECTOR': '1'}):
                detail = core['compute_currency_details']('CAD', checked.date().isoformat(),
                    include_context=False, factors_to_refresh=('Inflation',))
            obs = detail['_observations']['Inflation']
            self.assertAlmostEqual(obs['value'], 3.033980582524265)
            self.assertIn(expected, obs['provider_status'])
            self.assertEqual(obs['is_estimate'], symbol == 1)
            if position == 0: self.assertEqual(obs['comparison_period_status'], expected)
            record = live_data.build_record('Inflation', detail['Inflation'], obs,
                                           detail['_freshness']['Inflation'], checked.isoformat())
            self.assertEqual(record['validation'], 'VALID')
            self.assertEqual(record['observation']['is_estimate'], symbol == 1)
            self.assertEqual(record['observation']['provider_status'], obs['provider_status'])

    def test_metadata_transport_failure_propagates_without_retry(self):
        for failed_call in (1, 2):
            loader, client = self.loader(self.fixture())
            responses = list(client.post.side_effect)
            responses[failed_call] = requests.exceptions.Timeout('test-only')
            client.post.side_effect = responses
            with self.assertRaises(requests.exceptions.Timeout): loader(propagate_transport=True)
            self.assertEqual(client.post.call_count, failed_call + 1)


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


def abs_schedule(category):
    if category == 'Arbeitsmarkt':
        period, due, utc_due = 'August 2026', '24/09/2026 11:30am AEST', '2026-09-24T01:30:00Z'
    else:
        period, due, utc_due = 'September 2026', '02/12/2026 11:30am AEDT', '2026-12-02T00:30:00Z'
    title = ABS_SPECS[category]['title']
    return f'''<h1>{title}</h1><div class="view-content"><div class="views-row">
    {title}, {period}<span class="release-date-label">Release date</span>
    <time class="datetime" datetime="{utc_due}">{due}</time></div></div>'''


class AbsTests(unittest.TestCase):
    def fetch(self, category, csv=None, html=None, schedule=None, now=NOW):
        session = Mock()
        session.get.side_effect = [Mock(text=csv if csv is not None else abs_csv(category)),
                                   Mock(text=html if html is not None else abs_release(category)),
                                   Mock(text=schedule if schedule is not None else abs_schedule(category))]
        return fetch_abs_observation(category, session=session, now=now), session

    def test_actual_csv_shapes_sorted_and_exact_yoy(self):
        labour, _ = self.fetch('Arbeitsmarkt')
        self.assertEqual((labour['value'], labour['reference_period']), (4.46182469, '2026-07'))
        gdp, _ = self.fetch('GDP')
        self.assertAlmostEqual(gdp['value'], 2.137635765206136)
        self.assertEqual(gdp['date'], '2026-06-30')
        self.assertEqual(gdp['published_at'], '2026-09-02T01:30:00+00:00')
        self.assertEqual(gdp['next_release_date'], '2026-12-02')
        self.assertEqual(gdp['next_due_at'], '2026-12-02T00:30:00+00:00')
        self.assertEqual(gdp['next_due_precision'], 'official_scheduled_time')
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
        self.assertEqual(observation['next_due_at'], '2026-12-02T00:30:00+00:00')

    def test_official_deadline_allows_july_until_release_then_blocks_missing_august(self):
        before = datetime(2026, 9, 24, 1, 29, tzinfo=timezone.utc)
        result, _ = self.fetch('Arbeitsmarkt', now=before)
        self.assertEqual((result['value'], result['reference_period']), (4.46182469, '2026-07'))
        self.assertEqual(result['next_due_at'], '2026-09-24T01:30:00+00:00')
        with self.assertRaisesRegex(ValueError, 'Scheduled ABS release'):
            self.fetch('Arbeitsmarkt', now=datetime(2026, 9, 24, 1, 30, tzinfo=timezone.utc))

    def test_missing_or_conflicting_calendar_keeps_conservative_date_only_gate(self):
        for schedule in ('<h1>Wrong series</h1>',
                         abs_schedule('Arbeitsmarkt').replace('01:30:00Z', '02:30:00Z'),
                         abs_schedule('Arbeitsmarkt').replace('August 2026', 'September 2026')):
            result, _ = self.fetch('Arbeitsmarkt', schedule=schedule,
                                   now=datetime(2026, 9, 23, 13, 59, tzinfo=timezone.utc))
            self.assertEqual(result['next_due_at'], '2026-09-23T14:00:00+00:00')
            self.assertEqual(result['next_due_precision'], 'date_only_start_of_AU_day')
            with self.assertRaisesRegex(ValueError, 'Scheduled ABS release'):
                self.fetch('Arbeitsmarkt', schedule=schedule,
                           now=datetime(2026, 9, 23, 14, tzinfo=timezone.utc))

    def test_calendar_http_failure_keeps_date_only_gate(self):
        session = Mock()
        calendar = Mock()
        calendar.raise_for_status.side_effect = requests.HTTPError('calendar unavailable')
        session.get.side_effect = [Mock(text=abs_csv('Arbeitsmarkt')),
                                   Mock(text=abs_release('Arbeitsmarkt')), calendar]
        result = fetch_abs_observation('Arbeitsmarkt', session=session, now=NOW)
        self.assertEqual(result['next_due_at'], '2026-09-23T14:00:00+00:00')
        self.assertEqual(result['next_due_precision'], 'date_only_start_of_AU_day')

    def test_three_bounded_keyless_uncached_requests_and_http_failure(self):
        _, session = self.fetch('GDP')
        self.assertEqual(session.get.call_count, 3)
        for call in session.get.call_args_list: self.assertEqual(call.kwargs['timeout'], 20)
        self.assertEqual(session.get.call_args_list[0].kwargs['params']['format'], 'csv')
        self.assertEqual(session.get.call_args_list[2].args[0], ABS_SPECS['GDP']['release'].removesuffix('/latest-release'))
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
        self.assertFalse(result['needs_hourly_check'])
        self.assertEqual(result['next_due_at'],'2026-10-09T12:30:00+00:00')
        self.assertEqual(result['frequency'],'monthly')

    def test_cached_observation_survives_outage_until_next_official_release(self):
        import live_data
        observation = parse_statcan_labour(*statcan_fixture(), now=NOW)
        first = live_data.build_record('Arbeitsmarkt', 50.0, observation, 'FRESH', NOW.isoformat())
        outage_time = datetime(2026, 9, 24, 0, 10, tzinfo=timezone.utc)
        retained = live_data.build_record('Arbeitsmarkt', None, {}, 'UNAVAILABLE',
                                          outage_time.isoformat(), first, 'SOURCE_UNAVAILABLE')
        import json
        retained = json.loads(json.dumps(retained))  # persisted record after a process restart
        self.assertEqual(retained['checked_at'], first['checked_at'])
        self.assertTrue(live_data.eligible(retained, now=outage_time,
                                           factor='Arbeitsmarkt', currency='CAD')[0])
        self.assertTrue(live_data.record_not_due(retained, 'CAD', 'Arbeitsmarkt', outage_time))
        due = datetime(2026, 10, 9, 12, 30, tzinfo=timezone.utc)
        self.assertFalse(live_data.eligible(retained, now=due,
                                            factor='Arbeitsmarkt', currency='CAD')[0])
        self.assertFalse(live_data.record_not_due(retained, 'CAD', 'Arbeitsmarkt', due))
        with self.assertRaisesRegex(ValueError, 'Scheduled StatCan labour release'):
            parse_statcan_labour(*statcan_fixture(), now=due)

    def test_next_month_uses_its_own_deadline_and_unknown_calendar_fails_hourly(self):
        data = statcan_fixture()
        data[1][0]['object']['vectorDataPoint'][0].update(
            refPer='2026-09-01', releaseTime='2026-10-09T08:30')
        data[2][0]['object'].update(cubeEndDate='2026-09-01', releaseTime='2026-10-09T08:30')
        result = parse_statcan_labour(*data, now=datetime(2026, 10, 9, 13, tzinfo=timezone.utc))
        self.assertEqual(result['next_due_at'], '2026-11-06T13:30:00+00:00')
        data[1][0]['object']['vectorDataPoint'][0].update(
            refPer='2027-02-01', releaseTime='2027-03-12T08:30')
        data[2][0]['object'].update(cubeEndDate='2027-02-01', releaseTime='2027-03-12T08:30')
        result = parse_statcan_labour(*data, now=datetime(2027, 3, 12, 14, tzinfo=timezone.utc))
        self.assertIsNone(result['next_due_at'])
        self.assertTrue(result['needs_hourly_check'])

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

    def test_official_html_outage_is_transport_failure_but_other_html_is_not(self):
        for body, unavailable in [
            ("<html><title>Statistics Canada - We're sorry! The website is currently unavailable / Statistique Canada</title></html>", True),
            ("<html><title>Unexpected upstream page</title></html>", False),
        ]:
            with self.subTest(unavailable=unavailable):
                response = requests.Response()
                response.status_code = 200
                response.headers['Content-Type'] = 'text/html; charset=UTF-8'
                response._content = body.encode()
                session = Mock()
                session.post.return_value = response
                if unavailable:
                    with self.assertRaisesRegex(requests.RequestException, 'STATCAN_OFFICIAL_OUTAGE'):
                        fetch_statcan_labour(now=NOW, session=session)
                    session.note_official_outage.assert_called_once_with('www150.statcan.gc.ca')
                else:
                    with self.assertRaises(requests.exceptions.JSONDecodeError):
                        fetch_statcan_labour(now=NOW, session=session)
                    session.note_official_outage.assert_not_called()
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

    def test_gdp_official_html_outage_is_distinct_from_bad_json(self):
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'text/html'
        response._content = b"<title>Statistics Canada - We're sorry! The website is currently unavailable</title>"
        session = Mock()
        session.post.return_value = response
        with self.assertRaisesRegex(requests.RequestException, 'STATCAN_OFFICIAL_OUTAGE'):
            fetch_statcan_gdp(now=NOW, session=session)
        session.note_official_outage.assert_called_once_with('www150.statcan.gc.ca')
        session.post.assert_called_once()

    def test_confirmed_next_release_blocks_at_eastern_day_boundary(self):
        before = datetime(2026, 11, 30, 4, 59, tzinfo=timezone.utc)
        result = parse_statcan_gdp(*statcan_gdp_fixture(), now=before)
        self.assertEqual(result['next_due_at'], '2026-11-30T05:00:00+00:00')
        self.assertEqual(result['next_due_precision'], 'date_only_start_of_CA_Eastern_day')
        self.assertTrue(result['needs_hourly_check'])
        with self.assertRaisesRegex(ValueError, 'Scheduled StatCan GDP'):
            parse_statcan_gdp(*statcan_gdp_fixture(), now=datetime(2026,11,30,5,tzinfo=timezone.utc))
