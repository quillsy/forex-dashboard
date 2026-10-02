"""Exercise the actual live readers and writers without app/provider side effects."""
import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import live_data
from test_core_regressions import load_core
from test_snapshot_regressions import harness


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
PROVIDER_READERS = ('get_cpi_yoy_details', 'get_inflation_expectations_data',
                    'get_estat_cpi_data', 'get_statsnz_cpi_data',
                    'get_verified_policy_rate', 'get_genuine_2y_yield_historical',
                    'get_genuine_5y_yield_historical', 'get_unemployment_value',
                    'get_gdp_yoy_value')


def readers():
    ns = load_core()
    wanted = {'get_cpi_yoy_details', 'get_macro_observation_details',
              'get_live_labour_display_row', 'get_yield_details',
              'live_core_observation_for_display'}
    tree = ast.parse(Path(__file__).with_name('app.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<completed-live-batch>', 'exec'), ns)
    ns['use_live_core_cache'] = lambda *args: True
    ns.update(YIELD_5Y_SERIES={}, YIELD_10Y_SERIES={})
    return ns


def batch(completed_at):
    records = {}
    for factor in ('Geldpolitik', 'Inflation', 'Arbeitsmarkt', 'GDP'):
        observation = {'value': 0.0 if factor == 'Inflation' else 2.5,
                       'date': '2026-09-04' if factor == 'Geldpolitik' else '2026-08-31',
                       'source': 'Official fixture', 'series_id': 'fixture',
                       'frequency': 'quarterly' if factor == 'GDP' else 'monthly'}
        if factor == 'Geldpolitik':
            observation.update(policy_rate=2.5, yield_2y=2.5)
        records[factor] = live_data.build_record(factor, 20.0, observation, 'FRESH', NOW.isoformat())
    records['PMI'] = {'validation': 'UNVERIFIED', 'reason': 'Nutzungsrecht ungeklärt'}
    return {'model_version': live_data.MODEL, 'completed_at': completed_at,
            'currencies': {'CHF': records}}


def writer():
    ns = harness()
    ns.update(live_data=live_data, load_live_signals=Mock(return_value={}), save_live_signals=Mock())
    for name in PROVIDER_READERS:
        ns[name] = Mock(side_effect=AssertionError('live snapshot reached provider: ' + name))
    return ns


def write_batch(ns, data, now=NOW, curr='CHF', old_details=None, total=999, core=999):
    original = repr(data)
    with patch.object(live_data, 'load', side_effect=[data, {}]) as loader, \
            patch.object(live_data, 'now_utc', side_effect=[now, now + timedelta(hours=2)]) as clock, \
            patch.object(live_data, 'details', wraps=live_data.details) as read:
        try:
            return ns['save_currency_snapshot'](curr, total, core, 999, 'Old context',
                old_details if old_details is not None else {'_live_checked': True},
                {'PMI': 1000}, '2026-09-07')
        finally:
            loader.assert_called_once_with()
            clock.assert_called_once_with()
            read.assert_called_once_with(curr, now=now, data=data)
            for name in PROVIDER_READERS:
                ns[name].assert_not_called()
            assert repr(data) == original


class CompletedLiveBatchTests(unittest.TestCase):
    def test_invalid_batch_masks_all_live_readers_and_core_score(self):
        ns = readers()
        for completed in (None, 'invalid', (NOW + timedelta(seconds=1)).isoformat()):
            with self.subTest(completed_at=completed):
                data = batch(completed)
                original = copy.deepcopy(data)
                with patch.object(live_data, 'load', return_value=data), patch.object(live_data, 'now_utc', return_value=NOW):
                    cpi = ns['get_cpi_yoy_details']('CHF')
                    self.assertIsNone(cpi[0])
                    self.assertEqual(cpi[-1], 'UNAVAILABLE')
                    self.assertEqual(cpi[3], 'Official fixture')
                    for factor in ('Arbeitsmarkt', 'GDP'):
                        row = ns['get_macro_observation_details']('CHF', factor)
                        self.assertIsNone(row['value'])
                        self.assertEqual(row['freshness'], 'UNAVAILABLE')
                    labour = ns['get_live_labour_display_row']('CHF', {'flag': 'CH'})
                    self.assertEqual(labour['Arbeitslosenquote'], 'N/A')
                    self.assertEqual(labour['Status'], 'Nicht verfügbar')
                    self.assertIsNone(ns['get_yield_details']('CHF'))
                    final, _, core, _, details = ns['compute_currency_professional_score_and_regime_custom']('CHF')
                    self.assertIsNone(final)
                    self.assertIsNone(core)
                    self.assertEqual(details['_core_status'], 'INSUFFICIENT DATA')
                    self.assertEqual(details['_completeness'], 0)
                    for factor in live_data.FACTORS:
                        shown = ns['live_core_observation_for_display'](details, factor)
                        for key in ('value', 'policy_rate', 'yield_2y'):
                            self.assertIsNone(shown[key])
                        self.assertTrue(shown['_display_status'].startswith('Gesperrt:'))
                self.assertEqual(data, original)

    def test_completed_partial_batch_preserves_values_zero_and_coverage(self):
        ns = readers()
        with patch.object(live_data, 'load', return_value=batch(NOW.isoformat())), patch.object(live_data, 'now_utc', return_value=NOW):
            self.assertEqual(ns['get_cpi_yoy_details']('CHF')[0], 0.0)
            self.assertEqual(ns['get_macro_observation_details']('CHF', 'GDP')['value'], 2.5)
            self.assertEqual(ns['get_yield_details']('CHF')['value'], 2.5)
            final, _, core, _, details = ns['compute_currency_professional_score_and_regime_custom']('CHF')
            self.assertEqual(final, 20.0)
            self.assertEqual(core, 20.0)
            self.assertEqual(details['_completeness'], 80)
            self.assertEqual(details['_missing'], ['PMI'])
            shown = ns['live_core_observation_for_display'](details, 'Inflation')
            self.assertEqual(shown['value'], 0.0)
            self.assertEqual(shown['_display_status'], 'Geprüft')

    def test_invalid_batch_cannot_write_a_currency_snapshot_or_fetch_values(self):
        ns = harness()
        for name in ('load_live_signals', 'save_live_signals', 'get_cpi_yoy_details', 'get_verified_policy_rate'):
            ns[name] = Mock(side_effect=AssertionError('invalid batch reached IO'))
        for completed in (None, 'invalid', (NOW + timedelta(seconds=1)).isoformat()):
            with self.subTest(completed_at=completed):
                details = live_data.details('CHF', NOW, batch(completed))
                with self.assertRaisesRegex(ValueError, 'CURRENCY_REQUIRES_COMPLETED_LIVE_BATCH'):
                    ns['save_currency_snapshot']('CHF', 20, 20, 0, 'Normal', details, {}, '2026-09-07')
        for name in ('load_live_signals', 'save_live_signals', 'get_cpi_yoy_details', 'get_verified_policy_rate'):
            ns[name].assert_not_called()

    def test_historical_currency_snapshot_does_not_require_live_batch(self):
        ns = harness()
        ns['use_live_core_cache'] = lambda *args: False
        ns['load_live_signals'] = lambda: {}
        ns['save_live_signals'] = Mock()
        ns['live_data'] = Mock()
        details = {'Geldpolitik': 20.0, '_completeness': 35, '_missing': ['Inflation', 'Arbeitsmarkt', 'PMI', 'GDP']}
        self.assertTrue(ns['save_currency_snapshot']('CHF', None, None, 0, 'Normal', details, {}, '2026-09-04'))
        snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
        self.assertEqual(snapshot['core_status'], 'INSUFFICIENT DATA')
        self.assertEqual(snapshot['data_quality'], 35)
        self.assertEqual(ns['live_data'].mock_calls, [])

    def test_writer_derives_current_partial_batch_and_preserves_real_zero(self):
        data = batch(NOW.isoformat())
        data['currencies']['CHF']['Inflation']['score'] = 0.0
        for given in (None, 0.0, 999.0):
            with self.subTest(given_score=given):
                ns = writer()
                self.assertTrue(write_batch(ns, data, total=given, core=given))
                snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
                self.assertEqual(snapshot['data_quality'], 80)
                self.assertEqual(snapshot['missing_factors'], ['PMI'])
                self.assertAlmostEqual(snapshot['core_score'], 15.0, places=12)
                self.assertAlmostEqual(snapshot['total_score'], 15.0, places=12)
                self.assertEqual(snapshot['factor_scores']['Inflation'], 0.0)
                self.assertEqual(snapshot['cpi_value'], 0.0)
                self.assertEqual(snapshot['raw_values']['yield_2y'], 2.5)
                self.assertEqual(snapshot['raw_values']['unrate'], 2.5)
                self.assertEqual(snapshot['raw_values']['gdp_yoy'], 2.5)
                self.assertEqual(snapshot['collector_completed_at'], NOW.isoformat())
                self.assertEqual(snapshot['eligibility_checked_at'], NOW.isoformat())
                self.assertEqual({factor: snapshot['original_weights'][factor] for factor in live_data.FACTORS},
                                 live_data.FACTORS)
                for key in ('correction_score', 'trend_score', 'surprise_score', 'cpi_change_pp'):
                    self.assertIsNone(snapshot[key])
                for key in ('yield_5y', 'policy_rate_previous', 'policy_rate_verified_at'):
                    self.assertIsNone(snapshot['raw_values'][key])

    def test_complete_current_batch_can_have_a_genuine_zero_core(self):
        data = batch(NOW.isoformat())
        data['currencies']['CHF']['PMI'] = live_data.build_record('PMI', 0.0,
            {'value': 50.0, 'date': '2026-08-31', 'source': 'Official fixture'}, 'FRESH', NOW.isoformat())
        for record in data['currencies']['CHF'].values():
            record['score'] = 0.0
        ns = writer()
        self.assertTrue(write_batch(ns, data, total=None, core=None))
        snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
        self.assertEqual(snapshot['data_quality'], 100)
        self.assertEqual(snapshot['core_status'], 'VALID')
        self.assertEqual(snapshot['core_score'], 0.0)
        self.assertEqual(snapshot['total_score'], 0.0)
        self.assertEqual(snapshot['missing_factors'], [])

    def test_writer_rechecks_due_factors_and_original_core_threshold(self):
        for factor, coverage, core in (('Inflation', 60, 20.0), ('Geldpolitik', 45, None)):
            with self.subTest(expired_factor=factor):
                data = batch(NOW.isoformat())
                due = NOW + timedelta(minutes=1)
                for record in data['currencies']['CHF'].values():
                    if record.get('validation') == 'VALID':
                        record['next_due_at'] = (NOW + timedelta(hours=2)).isoformat()
                data['currencies']['CHF'][factor]['next_due_at'] = due.isoformat()
                old_details = live_data.details('CHF', now=NOW, data=data)
                self.assertEqual(old_details['_completeness'], 80)
                ns = writer()
                self.assertTrue(write_batch(ns, data, now=due, old_details=old_details, total=20, core=20))
                snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
                self.assertEqual(snapshot['data_quality'], coverage)
                if core is None:
                    self.assertIsNone(snapshot['core_score'])
                    self.assertIsNone(snapshot['total_score'])
                else:
                    self.assertAlmostEqual(snapshot['core_score'], core, places=12)
                    self.assertAlmostEqual(snapshot['total_score'], core, places=12)
                self.assertIsNone(snapshot['factor_scores'][factor])
                self.assertIn(factor, snapshot['missing_factors'])
                self.assertEqual(snapshot['core_status'], 'VALID' if core is not None else 'INSUFFICIENT DATA')
                self.assertAlmostEqual(snapshot['diagnostic_partial_score'], 20.0, places=12)
                keys = ('policy_rate', 'yield_2y') if factor == 'Geldpolitik' else ('value',)
                for key in keys:
                    self.assertIsNone(snapshot['observations'][factor][key])
                if factor == 'Inflation':
                    self.assertIsNone(snapshot['cpi_value'])
                    self.assertEqual(snapshot['cpi_freshness'], 'UNAVAILABLE')
                else:
                    self.assertIsNone(snapshot['raw_values']['policy_rate'])
                    self.assertIsNone(snapshot['raw_values']['yield_2y'])

    def test_writer_uses_revised_provenance_even_when_score_did_not_change(self):
        old_data = batch(NOW.isoformat())
        old_details = live_data.details('CHF', now=NOW, data=old_data)
        current = copy.deepcopy(old_data)
        current['completed_at'] = (NOW + timedelta(seconds=1)).isoformat()
        record = current['currencies']['CHF']['Inflation']
        record['published_at'] = '2026-09-05T09:00:00+00:00'
        record['observation'].update(date='2026-08-01', source='Revised official source',
            series_id='revised-series', release_date_known='2026-09-05',
            transformation='Official year-on-year percent', geography='Switzerland')
        ns = writer()
        self.assertTrue(write_batch(ns, current, now=NOW + timedelta(seconds=1), old_details=old_details))
        snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
        self.assertAlmostEqual(snapshot['core_score'], 20.0, places=12)
        self.assertEqual(snapshot['cpi_source'], 'Revised official source')
        self.assertEqual(snapshot['cpi_series'], 'revised-series')
        self.assertEqual(snapshot['cpi_observation_date'], '2026-08-01')
        self.assertEqual(snapshot['cpi_release_date'], '2026-09-05')
        self.assertEqual(snapshot['raw_values']['cpi_dataset'], 'revised-series')
        self.assertEqual(snapshot['raw_values']['metric_calculation'], 'Official year-on-year percent')
        self.assertEqual(snapshot['raw_values']['cpi_geography'], 'Switzerland')
        self.assertEqual(snapshot['observations']['Inflation']['source'], 'Revised official source')
        self.assertEqual(snapshot['collector_completed_at'], current['completed_at'])

    def test_old_true_flag_cannot_authorize_a_new_uncompleted_batch(self):
        old_details = live_data.details('CHF', now=NOW, data=batch(NOW.isoformat()))
        for completed in (None, 'invalid', (NOW + timedelta(seconds=1)).isoformat()):
            with self.subTest(completed_at=completed):
                ns = writer()
                with self.assertRaisesRegex(ValueError, 'CURRENCY_REQUIRES_COMPLETED_LIVE_BATCH'):
                    write_batch(ns, batch(completed), old_details=old_details)
                ns['load_live_signals'].assert_not_called()
                ns['save_live_signals'].assert_not_called()

    def test_missing_or_invalid_current_score_is_not_replaced_by_old_score(self):
        for bad_score in (None, float('nan'), float('inf'), True):
            with self.subTest(score=bad_score):
                data = batch(NOW.isoformat())
                data['currencies']['CHF']['Inflation']['score'] = bad_score
                ns = writer()
                self.assertTrue(write_batch(ns, data))
                snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
                self.assertEqual(snapshot['data_quality'], 60)
                self.assertIsNone(snapshot['factor_scores']['Inflation'])
                self.assertIsNone(snapshot['cpi_value'])

    def test_live_writer_never_fetches_jpy_or_nzd_legacy_metadata(self):
        for curr in ('JPY', 'NZD'):
            with self.subTest(currency=curr):
                data = batch(NOW.isoformat())
                data['currencies'] = {curr: data['currencies']['CHF']}
                ns = writer()
                self.assertTrue(write_batch(ns, data, curr=curr))
                snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
                self.assertIsNone(snapshot['raw_values']['yield_5y'])

    def test_missing_or_malformed_inflation_provenance_stays_masked(self):
        for record in ('absent', None, 'malformed', {'validation': 'VALID', 'score': 20,
                                         'factor': 'Inflation', 'observation': None}):
            with self.subTest(record=record):
                data = batch(NOW.isoformat())
                if record == 'absent':
                    del data['currencies']['CHF']['Inflation']
                else:
                    data['currencies']['CHF']['Inflation'] = record
                ns = writer()
                self.assertTrue(write_batch(ns, data))
                snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
                self.assertEqual(snapshot['data_quality'], 60)
                self.assertIsNone(snapshot['factor_scores']['Inflation'])
                self.assertIsNone(snapshot['cpi_value'])
                self.assertIsNone(snapshot['cpi_observation_date'])
                self.assertIsNone(snapshot['cpi_release_date'])
                self.assertEqual(snapshot['cpi_freshness'], 'UNAVAILABLE')
                self.assertEqual(snapshot['cpi_source'], 'UNAVAILABLE')
                self.assertTrue(snapshot['observations']['Inflation']['_display_status'].startswith('Gesperrt:'))

    def test_first_currency_snapshot_wins_after_a_factor_expires(self):
        data = batch(NOW.isoformat())
        ns = writer()
        self.assertTrue(write_batch(ns, data))
        saved = copy.deepcopy(ns['save_live_signals'].call_args.args[0])
        ns['load_live_signals'].return_value = saved
        ns['save_live_signals'].reset_mock()
        self.assertFalse(write_batch(ns, data, now=NOW + timedelta(hours=1)))
        ns['save_live_signals'].assert_not_called()
        self.assertEqual(ns['load_live_signals'].return_value, saved)


if __name__ == '__main__':
    unittest.main()
