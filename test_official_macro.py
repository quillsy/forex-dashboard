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


if __name__ == '__main__': unittest.main()
