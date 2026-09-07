"""Small isolated tests of live scalar and view-model delegation, no app import."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def functions():
    tree=ast.parse(Path(__file__).with_name('app.py').read_text())
    wanted={'get_unemp_rate_value','get_live_labour_display_row','get_yield_details'}
    nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in wanted]
    ns={'datetime':datetime,'FRED_KEY':'unused','EODHD_KEY':None,
        'UNEMP_SERIES':{'GBP':'old-id'},'YIELD_2Y_SERIES':{'EUR':'old-id'},'YIELD_5Y_SERIES':{},
        'use_live_core_cache':lambda *args:True,'finite_number':lambda value:float(value) if value is not None else None}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<live-ui>','exec'),ns)
    return ns


class LiveLabourUITests(unittest.TestCase):
    def test_live_scalar_uses_current_provider_without_fred(self):
        ns=functions(); ns['get_macro_observation_details']=Mock(return_value={'value':4.9})
        ns['get_fred_data_historical']=Mock(side_effect=AssertionError('unexpected legacy request'))
        self.assertEqual(ns['get_unemp_rate_value']('GBP'),4.9)
        ns['get_fred_data_historical'].assert_not_called()
    def test_historical_scalar_keeps_old_reader(self):
        ns=functions();ns['use_live_core_cache']=lambda *args:False
        ns['get_fred_data_historical']=Mock(return_value=(4.5,None,None))
        self.assertEqual(ns['get_unemp_rate_value']('GBP','2024-01-01'),4.5)
        ns['get_fred_data_historical'].assert_called_once_with('old-id','2024-01-01','unused')
    def test_view_preserves_rolling_metadata_and_missing_status(self):
        ns=functions(); observation={'value':4.9,'source':'ONS','reference_period':'2026-04-01/2026-06-30',
            'period_label':'Rollierende 3-Monats-Quote','freshness':'AGING'}
        ns['get_macro_observation_details']=lambda *args:observation
        row=ns['get_live_labour_display_row']('GBP',{'flag':'UK'})
        self.assertEqual(row['Quelle'],'ONS');self.assertEqual(row['Messzeitraum'],observation['period_label'])
        self.assertEqual(row['Referenzperiode'],observation['reference_period'])
        observation['value']=None
        self.assertEqual(ns['get_live_labour_display_row']('GBP',{'flag':'UK'})['Status'],'Nicht verfügbar')
    def test_yield_keeps_real_provenance_and_unknown_changes(self):
        ns=functions(); observation={'yield_2y':2.96,'source':'Bundesbank Germany 2Y','series_id':'official-series','date':'2026-09-04'}
        detail={'Geldpolitik':10,'_observations':{'Geldpolitik':observation}}
        ns['live_data']=SimpleNamespace(details=lambda curr:detail)
        row=ns['get_yield_details']('EUR')
        self.assertEqual(row['value'],2.96);self.assertEqual(row['date'],'2026-09-04')
        self.assertEqual(row['source'],observation['source']);self.assertEqual(row['series_id'],'official-series')
        self.assertIsNone(row['chg_1w']);self.assertIsNone(row['chg_1m']);self.assertEqual(row['trend'],'N/A')
        detail['Geldpolitik']=None
        self.assertIsNone(ns['get_yield_details']('EUR'))


if __name__=='__main__':unittest.main()
