"""Offline regressions: compile only audited functions, never import the app.

No API requests, UI rendering, cache writes or snapshot mutation are performed.
"""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

FUNCTIONS = {
    'finite_number', 'normalized_freshness', 'observation_freshness',
    'get_macro_observation_details', 'get_unemployment_value', 'get_gdp_yoy_value',
    'compute_currency_details', 'compute_currency_professional_score_and_regime',
    'compute_currency_professional_score_and_regime_custom', 'get_pair_signal_and_badge',
    'get_surprise_points', 'format_score', 'pair_core_is_complete',
}
CONSTANTS = {'CORE_FACTOR_WEIGHTS', 'UNEMP_SERIES', 'GDP_SERIES', 'YIELD_2Y_SERIES', 'CPI_SERIES', 'PMI_SERIES'}


def load_core():
    tree = ast.parse(Path(__file__).with_name('app.py').read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets):
            nodes.append(node)
    ns = {'np': np, 'pd': pd, 'datetime': datetime, 'FRED_KEY': None, 'EODHD_KEY': None,
          'st': SimpleNamespace(session_state={}), 'df_cal': None}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<isolated-core>', 'exec'), ns)
    ns.update({
        'policy_rate_is_usable': lambda policy: policy.get('verification_status') == 'VERIFIED',
        'get_verified_policy_rate': lambda curr: {'rate': 4.0, 'verification_status': 'VERIFIED'},
        'get_genuine_2y_yield_historical': lambda *args: (4.0, '2026-09-04', 'FRED'),
        'get_cpi_yoy_details': lambda *args: (3.0, '2026-08-01', 'CPI_YOY', 'Official', 'CPI', '🟢 FRESH (PIT_LIMITED)'),
        'get_macro_observation_details': lambda curr, factor, date: {'value': 4.0 if factor == 'Arbeitsmarkt' else 3.0,
            'date': '2026-08-01', 'source': 'FRED', 'freshness': 'FRESH', 'frequency': 'monthly' if factor == 'Arbeitsmarkt' else 'quarterly'},
        'get_all_pmi_data': lambda *args, **kwargs: {'USD': {'m_last': 55.0, 'm_ref': 'Aug/26', 's_last': 55.0, 's_ref': '2026-08-01'}},
        'get_bci_value': lambda *args: None,
        'get_series_trend_points': lambda *args, **kwargs: 0.0,
        'compute_correction_score': lambda *args: 0.0,
        'detect_market_regime': lambda *args: 'Normal',
        'check_demo_active': lambda: False,
        'use_live_core_cache': lambda *args: False,
        'get_fred_data': lambda *args: (None, None, False),
        'get_worldbank_data_historical': lambda *args: (None, None, False),
        'CURRENCIES': {'USD': {'wb_code': 'USA'}},
    })
    return ns


def fail(*args, **kwargs):
    raise RuntimeError('offline source failed')


class CoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.core = load_core()

    def score(self, weights=None):
        return self.core['compute_currency_professional_score_and_regime_custom']('USD', weights, '2026-09-06')

    def test_frozen_formulas_and_weights_ignore_custom_context(self):
        final, _, base, _, details = self.score({'PMI': 1000, 'EconomicSurprises': 1000, 'Correction': 500})
        self.assertAlmostEqual(base, 130 / 3)
        self.assertEqual(details['_completeness'], 100)
        self.assertEqual(final, base)

    def test_failure_isolated_and_completeness_derived_from_available_values(self):
        self.core['get_cpi_yoy_details'] = fail
        _, _, base, _, details = self.score()
        self.assertEqual(details['_missing'], ['Inflation'])
        self.assertEqual(details['_completeness'], 80)
        self.assertIsNone(details['Inflation'])
        self.assertAlmostEqual(base, (130 / 3 - 10) / .8)

    def test_all_source_errors_block_core_instead_of_valid_zero(self):
        for name in ('get_verified_policy_rate', 'get_cpi_yoy_details', 'get_macro_observation_details', 'get_all_pmi_data', 'get_bci_value'):
            self.core[name] = fail
        final, _, base, _, details = self.score()
        self.assertIsNone(final)
        self.assertIsNone(base)
        self.assertEqual(details['_completeness'], 0)
        self.assertEqual(details['_core_status'], 'INSUFFICIENT DATA')
        self.assertEqual(len(details['_missing']), 5)

    def test_missing_yield_excludes_entire_monetary_and_preserves_none(self):
        self.core['get_genuine_2y_yield_historical'] = lambda *args: (None, None, '')
        _, _, base, _, details = self.score()
        self.assertIsNone(details['Geldpolitik'])
        self.assertEqual(details['_completeness'], 65)
        self.assertAlmostEqual(base, (130 / 3 - 35 / 3) / .65)
        self.assertEqual(self.core['format_score'](details['Geldpolitik']), 'N/A')
        self.assertEqual(self.core['format_score'](0.0), '+0.0')

    def test_unverified_policy_is_excluded_despite_numeric_rate(self):
        self.core['get_verified_policy_rate'] = lambda *args: {'rate': 4, 'verification_status': 'UNVERIFIED_CHANGE', 'status': '🟢 FRESH'}
        self.assertIsNone(self.score()[4]['Geldpolitik'])

    def test_nan_and_infinity_do_not_contribute_coverage(self):
        self.core['get_genuine_2y_yield_historical'] = lambda *args: (np.nan, '2026-09-04', 'FRED')
        self.core['get_cpi_yoy_details'] = lambda *args: (np.inf, '2026-08-01', 'CPI_YOY', 'FRED', 'CPI', 'FRESH')
        final, _, base, _, details = self.score()
        self.assertEqual(details['_completeness'], 45)
        self.assertIsNone(base)
        self.assertIsNone(final)

    def test_coverage_45_blocked_and_55_allowed(self):
        self.core['get_genuine_2y_yield_historical'] = lambda *args: (None, None, '')
        self.core['get_all_pmi_data'] = lambda *args, **kwargs: {}
        self.assertEqual(self.score()[4]['_completeness'], 45)
        self.assertIsNone(self.score()[2])
        self.core = load_core()
        self.core['get_macro_observation_details'] = fail
        self.core['get_all_pmi_data'] = lambda *args, **kwargs: {}
        self.assertEqual(self.score()[4]['_completeness'], 55)
        self.assertIsNotNone(self.score()[2])

    def test_pmi_ignores_stale_missing_and_future_component_dates(self):
        for bad_ref in ('2020-01-01', None, '2026-10-01'):
            with self.subTest(reference=bad_ref):
                self.core['get_all_pmi_data'] = lambda *args, **kwargs: {'USD': {'m_last': 90, 'm_ref': bad_ref, 's_last': 52, 's_ref': 'Aug/26'}}
                self.assertEqual(self.score()[4]['PMI'], 20)
        self.core['get_all_pmi_data'] = lambda *args, **kwargs: {'USD': {'m_last': 90, 'm_ref': '2020-01-01'}}
        self.assertIsNone(self.score()[4]['PMI'])

    def test_monthly_age_boundaries_and_badges(self):
        freshness = self.core['observation_freshness']
        self.assertEqual(freshness('Jun/26', '2026-08-14', 45, 90, True), 'FRESH')
        self.assertEqual(freshness('Jun/26', '2026-08-15', 45, 90, True), 'AGING')
        self.assertEqual(freshness('Jun/26', '2026-09-28', 45, 90, True), 'AGING')
        self.assertEqual(freshness('Jun/26', '2026-09-29', 45, 90, True), 'STALE')
        self.assertEqual(self.core['normalized_freshness']('🟢 FRESH (PIT_LIMITED)'), 'FRESH')
        self.assertEqual(self.core['normalized_freshness']('🟡 AGING'), 'AGING')

    def test_context_cannot_change_base(self):
        baseline = self.score()[2]
        self.core['get_series_trend_points'] = lambda *args, **kwargs: 15
        self.core['get_surprise_points'] = lambda *args, **kwargs: 20
        self.core['compute_correction_score'] = lambda *args: 10
        final, _, base, _, details = self.score()
        self.assertEqual(base, baseline)
        self.assertGreater(final, base)
        self.assertGreater(details['_trend_score'], 0)

    def test_live_surprise_without_release_is_zero(self):
        for curr in ('USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'):
            for factor in ('Geldpolitik', 'Inflation', 'Arbeitsmarkt', 'Wachstum'):
                self.assertEqual(self.core['get_surprise_points'](curr, factor, '2026-09-06'), 0)

    def test_pair_thresholds_and_reverse_symmetry(self):
        for divergence, signal in ((50, 'SB'), (49.99, 'MB'), (20, 'MB'), (19.99, 'NT'), (-19.99, 'NT'), (-20, 'MS'), (-49.99, 'MS'), (-50, 'SS')):
            def scores(curr, *args):
                return 0, 'Normal', divergence if curr == 'USD' else 0, 0, {**dict.fromkeys(('Geldpolitik','Inflation','Arbeitsmarkt','PMI','GDP'), 0.0), '_live_checked': True, '_completeness': 100}
            self.core['compute_currency_professional_score_and_regime'] = scores
            self.assertEqual(self.core['get_pair_signal_and_badge']('USD', 'EUR')[3], signal)
            self.assertEqual(self.core['get_pair_signal_and_badge']('EUR', 'USD')[2], -divergence)
        self.core['compute_currency_professional_score_and_regime'] = lambda *args: (None, 'Normal', None, 0, {'_completeness': 45})
        self.assertIsNone(self.core['get_pair_signal_and_badge']('USD', 'EUR')[2])

    def test_pair_requires_complete_both_sides_but_currency_keeps_original_gate(self):
        complete={**dict.fromkeys(('Geldpolitik','Inflation','Arbeitsmarkt','PMI','GDP'), 0.0), '_live_checked': True, '_completeness':100}
        for missing_side in ('USD','EUR'):
            for coverage in (0, 50, 65, 75, 95, 99.99):
                def score(curr, *args):
                    details=dict(complete)
                    if curr==missing_side: details['_completeness']=coverage
                    return 30,'Normal',30 if curr=='USD' else 0,0,details
                self.core['compute_currency_professional_score_and_regime']=score
                self.assertIsNone(self.core['get_pair_signal_and_badge']('USD','EUR')[2])
        broken=dict(complete, PMI=None)
        self.assertFalse(self.core['pair_core_is_complete'](broken))
        self.assertFalse(self.core['pair_core_is_complete']({}))
        self.assertTrue(self.core['pair_core_is_complete'](complete))

    def actual_macro_loader(self):
        # Restore just this production function after fixture stubbing.
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_macro_observation_details')
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<macro-loader>', 'exec'), self.core)
        return self.core['get_macro_observation_details']

    def test_no_current_annual_fallback_when_fred_unavailable(self):
        loader = self.actual_macro_loader()
        self.core['get_worldbank_data_historical'] = fail
        for factor in ('Arbeitsmarkt', 'GDP'):
            result = loader('USD', factor)
            self.assertIsNone(result['value'])
            self.assertEqual(result['source'], 'UNAVAILABLE')
            self.assertEqual(result['freshness'], 'UNAVAILABLE')

    def test_macro_date_and_value_come_from_same_observation(self):
        loader = self.actual_macro_loader()
        self.core['get_fred_data'] = lambda *args: (pd.DataFrame({'date': pd.to_datetime(['2020-01-01']), 'value': [4.0]}), None, True)
        result = loader('USD', 'Arbeitsmarkt', '2026-09-06')
        self.assertIsNone(result['value'])
        self.assertEqual(result['date'], '2020-01-01')
        self.assertEqual(result['source'], 'FRED')
        self.assertEqual(result['freshness'], 'STALE')

    def test_gdp_yoy_uses_matching_year_not_four_unordered_rows(self):
        loader = self.actual_macro_loader()
        data = pd.DataFrame({'date': pd.to_datetime(['2026-04-01', '2025-07-01', '2025-04-01', '2026-01-01', '2025-10-01']), 'value': [103, 100, 100, 102, 101]})
        self.core['get_fred_data'] = lambda *args: (data, None, True)
        result = loader('USD', 'GDP', '2026-09-06')
        self.assertAlmostEqual(result['value'], 3)
        self.assertEqual(result['date'], '2026-06-30')  # End of the actual reference quarter, not its FRED label.
        self.assertEqual(result['freshness'], 'FRESH')
        data.drop(data[data['date'] == pd.Timestamp('2025-04-01')].index, inplace=True)
        self.assertIsNone(loader('USD', 'GDP', '2026-09-06')['value'])

    def test_no_fabricated_backtest_or_stale_version_ui(self):
        text = Path(__file__).with_name('app.py').read_text()
        ui = text[text.index('    # ----------------- TAB 13:'):]
        for fabricated in ('64.3%', '1.42', '1.85', 'erfolgreich ausgeführt', '100% auf Point-in-Time'):
            self.assertNotIn(fabricated, ui)
        self.assertIn('disabled=True, key="btn_run_bt_lab"', ui)
        self.assertNotIn('CORE_V2_2_2026_08', text)


if __name__ == '__main__':
    unittest.main()
