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
        details = {'Geldpolitik': 20.0, '_completeness': 35, '_missing': ['Inflation', 'Arbeitsmarkt', 'PMI', 'GDP']}
        self.assertTrue(ns['save_currency_snapshot']('CHF', None, None, 0, 'Normal', details, {}, '2026-09-04'))
        snapshot = next(iter(ns['save_live_signals'].call_args.args[0].values()))
        self.assertEqual(snapshot['core_status'], 'INSUFFICIENT DATA')
        self.assertEqual(snapshot['data_quality'], 35)


if __name__ == '__main__':
    unittest.main()
