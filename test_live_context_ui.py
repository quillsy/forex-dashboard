"""Exercise actual live VIX scalar and UI branches without importing Streamlit app."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

TREE=ast.parse(Path(__file__).with_name('app.py').read_text())

def load_functions():
    nodes=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in {'get_vix_value','detect_market_regime'}]
    namespace={'use_live_core_cache':lambda *a:True}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<context>','exec'),namespace)
    return namespace


class LiveContextTests(unittest.TestCase):
    def test_no_fabricated_vix_or_provider_calls_in_live_mode(self):
        ns=load_functions()
        ns['get_fred_data_historical']=Mock(side_effect=AssertionError('no live request'))
        ns['get_tiingo_prices']=Mock(side_effect=AssertionError('ETF cannot become VIX'))
        self.assertIsNone(ns['get_vix_value']())
        self.assertEqual(ns['detect_market_regime']('USD'),'Unbekannt (VIX nicht geprüft)')
        ns['get_fred_data_historical'].assert_not_called()
        ns['get_tiingo_prices'].assert_not_called()
    def test_historical_vix_reader_is_retained(self):
        ns=load_functions();ns['use_live_core_cache']=lambda *a:False
        ns['FRED_KEY']='unused';ns['get_fred_data_historical']=Mock(return_value=(21.5,None,True))
        self.assertEqual(ns['get_vix_value']('2024-01-01'),21.5)
    def test_both_actual_ui_regime_branches_handle_missing_vix(self):
        for name in ('global_regime','current_regime'):
            branches=[n for n in ast.walk(TREE) if isinstance(n,ast.If) and n.body and isinstance(n.body[0],ast.Assign)
                      and any(isinstance(t,ast.Name) and t.id==name for t in n.body[0].targets)
                      and isinstance(n.test,ast.Compare) and isinstance(n.test.left,ast.Name) and n.test.left.id=='vix'
                      and isinstance(n.test.ops[0],ast.Is)]
            self.assertEqual(len(branches),1,name)
            ns={'vix':None,'cpi_us':4.0,'gdp_us':0.5}
            exec(compile(ast.Module(body=branches,type_ignores=[]),'<ui-regime>','exec'),ns)
            self.assertIn('Unbekannt',ns[name])
    def test_actual_metric_formatters_accept_none(self):
        labels={'Globale Marktphase','Aktueller VIX Index'}
        calls=[n for n in ast.walk(TREE) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='metric'
               and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value in labels]
        self.assertEqual(len(calls),2)
        ns={'st':Mock(),'vix':None,'global_regime':'Unbekannt'}
        for call in calls:
            exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=call)],type_ignores=[])),'<ui-metric>','exec'),ns)
        self.assertEqual(ns['st'].metric.call_count,2)
    def test_snapshot_missing_vix_is_not_risk_on(self):
        values=[value for node in ast.walk(TREE) if isinstance(node,ast.Dict) for key,value in zip(node.keys,node.values)
                if isinstance(key,ast.Constant) and key.value=='risk_on_off']
        self.assertEqual(len(values),1)
        expression=compile(ast.Expression(body=values[0]),'<risk-label>','eval')
        self.assertIn('Unbekannt',eval(expression,{'vix_val':None}))
    def test_new_snapshot_does_not_invent_confidence_probability(self):
        function=next(n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name=='save_live_signal_snapshot')
        values=[value for node in ast.walk(function) if isinstance(node,ast.Dict) for key,value in zip(node.keys,node.values)
                if isinstance(key,ast.Constant) and key.value=='confidence']
        self.assertEqual(len(values),1)
        self.assertIsNone(ast.literal_eval(values[0]))

    def test_checklist_never_turns_divergence_into_probability(self):
        function = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'compute_checklist_snapshot')
        for divergence in (None, 20.0, 50.0, -100.0):
            ns = {'get_pair_signal_and_badge': Mock(return_value=('STRONG BUY', '', divergence, 'SB')),
                  'compute_currency_professional_score_and_regime_custom': Mock(return_value=(0, 'Unknown', 0, 0, {}))}
            exec(compile(ast.Module(body=[function], type_ignores=[]), '<checklist>', 'exec'), ns)
            rows = ns['compute_checklist_snapshot']({})
            self.assertEqual(len(rows), 10)
            self.assertTrue(all(row['confidence'] is None for row in rows))
            self.assertTrue(all(row['data_quality'] == 0 for row in rows))

    def correction_rows(self, correction, details):
        # Execute the actual market-context table loop, not a replica formatter.
        loops = [node for node in ast.walk(TREE) if isinstance(node, ast.For)
                 and any(isinstance(child, ast.Dict) and any(
                     isinstance(key, ast.Constant) and key.value == 'Struktur-Korrekturfaktor'
                     for key in child.keys) for child in ast.walk(node))]
        self.assertEqual(len(loops), 1)
        formatter = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
                         and node.name == 'format_score')
        import math
        ns = {'com_rows': [], 'CURRENCIES': {currency: {'flag': currency} for currency in
              ('USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD')},
              'compute_currency_professional_score_and_regime': Mock(
                  return_value=(20, 'Context nicht geprüft', 20, correction, details)),
              'finite_number': lambda value: float(value) if value is not None and
                  math.isfinite(float(value)) else None}
        exec(compile(ast.Module(body=[formatter, loops[0]], type_ignores=[]),
                     '<context-table>', 'exec'), ns)
        self.assertEqual(len(ns['com_rows']), 8)
        return [row['Struktur-Korrekturfaktor'] for row in ns['com_rows']]

    def test_uncollected_live_context_is_not_a_neutral_correction(self):
        self.assertEqual(self.correction_rows(0.0, {'_context_status': 'NOT_COLLECTED'}), ['N/A'] * 8)

    def test_uncollected_context_never_exposes_a_numeric_placeholder(self):
        for value in (12.5, -3.5, None):
            self.assertEqual(self.correction_rows(value, {'_context_status': 'NOT_COLLECTED'}), ['N/A'] * 8)

    def test_historical_neutral_and_nonzero_corrections_remain_visible(self):
        for value, expected in ((0.0, '+0.0'), (-3.5, '-3.5'), (12.5, '+12.5')):
            self.assertEqual(self.correction_rows(value, {}), [expected] * 8)

    def test_missing_historical_correction_is_not_zero(self):
        self.assertEqual(self.correction_rows(None, {}), ['N/A'] * 8)

    def policy_chart(self, manual=False):
        import pandas as pd
        from types import SimpleNamespace
        tab = next(node for node in ast.walk(TREE) if isinstance(node, ast.With)
                   and any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == 'tab3'
                           for item in node.items))
        start = next(index for index, node in enumerate(tab.body) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == 'rates_data' for target in node.targets))
        end = next(index for index, node in enumerate(tab.body) if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Call) and node.value.args
                   and isinstance(node.value.args[0], ast.Constant)
                   and node.value.args[0].value == '🏦 Bond Market & 2Y Benchmark Yields')
        figure, bar, ui = Mock(), Mock(), Mock()
        ns = {'pd': pd, 'st': ui, 'go': SimpleNamespace(Figure=Mock(return_value=figure), Bar=bar),
              'CURRENCIES': {'USD': {'wb_code': 'USA', 'name': 'US Dollar'},
                             'JPY': {'wb_code': 'JPN', 'name': 'Japanese Yen'}},
              'get_country_rate': lambda code, *args, **kwargs: (
                  (9.0, None, 'MANUAL OVERRIDE' if manual else 'Federal Reserve') if code == 'USA'
                  else (None, None, 'UNAVAILABLE')),
              'use_live_core_cache': lambda: True, 'FRED_KEY': None,
              'finite_number': lambda value: value if value is not None and pd.notna(value) else None}
        exec(compile(ast.Module(body=tab.body[start:end], type_ignores=[]), '<policy-chart>', 'exec'), ns)
        return ns, figure, bar, ui

    def test_manual_policy_plot_has_local_warning_title_and_source(self):
        ns, figure, bar, ui = self.policy_chart(manual=True)
        self.assertTrue(ns['manual_policy_display'])
        self.assertIn('manuell', ui.warning.call_args.args[0])
        self.assertIn('manuelle Werte', figure.update_layout.call_args.kwargs['title'])
        self.assertEqual(list(bar.call_args.kwargs['customdata']), ['MANUAL OVERRIDE', 'UNAVAILABLE'])
        self.assertIn('Quelle:', bar.call_args.kwargs['hovertemplate'])
        self.assertEqual(ns['df_rates_plot'].iloc[0]['Zinssatz'], 9.0)
        self.assertTrue(ns['df_rates_plot']['Zinssatz'].isna().iloc[1])

    def test_official_policy_plot_retains_title_and_does_not_claim_manual_input(self):
        ns, figure, bar, ui = self.policy_chart(manual=False)
        self.assertFalse(ns['manual_policy_display'])
        ui.warning.assert_not_called()
        self.assertEqual(figure.update_layout.call_args.kwargs['title'], '<b>Zentralbank-Leitzinsen der G8</b>')
        self.assertEqual(list(bar.call_args.kwargs['customdata']), ['Federal Reserve', 'UNAVAILABLE'])


if __name__=='__main__':unittest.main()
