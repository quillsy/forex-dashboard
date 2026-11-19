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


class LiveDateRolloverTests(unittest.TestCase):
    @staticmethod
    def ui_tab(name):
        return next(node for node in ast.walk(TREE) if isinstance(node, ast.With)
                    and any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == name
                            for item in node.items))

    @staticmethod
    def date_picker():
        return [node for node in ast.walk(TREE) if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'dashboard_date' for target in node.targets)]

    def namespace(self, demo=False):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        import pandas as pd
        import requests

        class Clock(datetime):
            reads = 0

            @classmethod
            def now(cls, tz=None):
                cls.reads += 1
                moment = (datetime(2026, 11, 19, 23, 59, 59) if cls.reads == 1
                          else datetime(2026, 11, 20))
                return moment if tz is None else moment.replace(tzinfo=timezone.utc).astimezone(tz)

        def observation(score, value):
            return {'Inflation': score, '_observations': {'Inflation': {
                'value': value, 'date': '2026-09-01', 'source': 'Fixture', 'series_id': 'fixture'}},
                '_freshness': {'Inflation': 'UNAVAILABLE' if score is None else 'FRESH'}}

        records = {'USD': observation(None, 3.0), 'EUR': observation(20.0, 2.4),
                   'CHF': observation(0.0, 0.0)}
        ui = Mock()
        ui.session_state = {'demo_mode_chk': demo}
        ns = {'st': ui, 'datetime': Clock, 'timedelta': timedelta, 'pd': pd, 'requests': requests,
              'os': SimpleNamespace(environ={}), 'live_data': Mock(details=Mock(side_effect=records.__getitem__)),
              'CURRENCIES': {currency: {'flag': currency} for currency in records},
              'CPI_SERIES': {'USD': 'CPIAUCNS', 'EUR': 'fixture', 'CHF': 'fixture'},
              'FRED_KEY': 'fixture', 'OECD_INFLATION_EXP_SERIES': {'USD': 'fixture'}}
        for name in ('get_fred_data', 'get_fred_data_historical', 'get_current_official_cpi'):
            ns[name] = Mock(side_effect=AssertionError('Unexpected live provider read'))
        wanted = {'check_demo_active', 'use_live_core_cache', 'get_cpi_yoy_details',
                  'get_inflation_expectations_data'}
        nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<cpi-readers>', 'exec'), ns)
        return ns

    def assert_no_provider_reads(self, ns):
        for name in ('get_fred_data', 'get_fred_data_historical', 'get_current_official_cpi'):
            ns[name].assert_not_called()

    def test_actual_inflation_tab_remains_in_live_cache_at_midnight(self):
        from contextlib import nullcontext
        ns = self.namespace()
        ns['tab4'] = nullcontext()
        nodes = self.date_picker() + [self.ui_tab('tab4')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<cpi-tab>', 'exec'), ns)
        self.assertGreaterEqual(ns['datetime'].reads, 2)  # clock crossed midnight during rendering
        self.assertTrue(any('nur zentral geprüfte CPI-Werte' in call.args[0]
                            for call in ns['st'].caption.call_args_list))
        rows = ns['st'].dataframe.call_args.args[0]
        self.assertEqual(list(rows['Inflation YoY']), ['N/A', '2.40% (HICP YoY)', '0.00% (HICP YoY)'])
        self.assertEqual(list(rows['Freshness']), ['UNAVAILABLE', 'FRESH', 'FRESH'])
        for column in ('Change', 'Trend', 'OECD Expectations'):
            self.assertEqual(list(rows[column]), ['N/A'] * 3)
        self.assert_no_provider_reads(ns)

    def test_live_inflation_expectations_keep_selected_cache_route(self):
        for date in (None, '2026-11-19'):
            with self.subTest(date=date):
                ns = self.namespace()
                reader = Mock(wraps=ns['get_cpi_yoy_details'])
                ns['get_cpi_yoy_details'] = reader
                result = ns['get_inflation_expectations_data']('CHF', date)
                reader.assert_called_once_with('CHF', None)
                self.assertEqual(result['actual_cpi'], 0.0)
                for field in ('cpi_trend', 'oecd_expectation', 'market_breakeven'):
                    self.assertIsNone(result[field])
                self.assert_no_provider_reads(ns)

    def test_demo_and_explicit_historical_dates_still_reach_the_dated_reader(self):
        for demo in (False, True):
            with self.subTest(demo=demo):
                ns = self.namespace(demo=demo)
                if demo:
                    exec(compile(ast.Module(body=self.date_picker(), type_ignores=[]), '<demo-date>', 'exec'), ns)
                    date = ns['dashboard_date']
                    self.assertEqual(date, '2026-11-19')
                else:
                    date = '2026-10-01'
                reader = Mock(side_effect=[(2.0, '2026-09-01', 'CPI_YOY', 'Fixture', 'fixture', 'FRESH'),
                                           (1.0, '2026-08-01', 'CPI_YOY', 'Fixture', 'fixture', 'FRESH')])
                ns.update(get_cpi_yoy_details=reader, FRED_KEY=None)
                result = ns['get_inflation_expectations_data']('USD', date)
                self.assertEqual(reader.call_args_list[0].args, ('USD', date))
                self.assertEqual(result['date'], date)
                self.assertEqual(result['cpi_trend'], 1.0)
                self.assert_no_provider_reads(ns)

    def test_other_macro_tabs_use_shared_today_route(self):
        for tab, readers in {'tab1': {'get_cpi_yoy_value': 1, 'get_gdp_yoy_value': 1},
                             'tab2': {'get_series_trend_points': 1},
                             'tab5': {'get_live_labour_display_row': 2},
                             'tab7': {'get_macro_observation_details': 2},
                             'tab8': {'get_cpi_yoy_value': 1, 'get_gdp_yoy_value': 1}}.items():
            for reader, position in readers.items():
                with self.subTest(tab=tab, reader=reader):
                    calls = [node for node in ast.walk(self.ui_tab(tab)) if isinstance(node, ast.Call)
                             and isinstance(node.func, ast.Name) and node.func.id == reader]
                    self.assertTrue(calls)
                    self.assertTrue(all(ast.unparse(call.args[position]) == 'dashboard_date' for call in calls))


if __name__=='__main__':unittest.main()
