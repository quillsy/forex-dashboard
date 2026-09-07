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


if __name__=='__main__':unittest.main()
