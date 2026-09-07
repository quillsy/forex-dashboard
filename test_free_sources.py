"""Offline source-contract and public-write protection tests."""
import ast
from datetime import datetime
import html
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd
import requests

NAMES = {'fetch_fcs_history_live', 'parse_statsnz_cpi_release', 'get_statsnz_cpi_data', 'get_official_2y_data',
         'get_genuine_2y_yield_historical', 'finite_number', 'observation_freshness',
         'operator_is_authorized', 'save_manual_cot_entry'}


def load_sources():
    tree = ast.parse(Path(__file__).with_name('app.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    for n in nodes:
        n.decorator_list = []
    ns = dict(io=io, json=json, datetime=datetime, pd=pd, np=np, FRED_KEY=None, EODHD_KEY=None,
              st=SimpleNamespace(session_state={}), load_api_key=lambda name: None,
              requests=SimpleNamespace(get=Mock(side_effect=AssertionError('Network forbidden')),
                                       RequestException=requests.RequestException),
              check_demo_active=lambda: False)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<free-sources>', 'exec'), ns)
    return ns


def nz_release(series='CPI all groups (annual)', published='2025-07-21 10:45:00'):
    payload = {'Title': 'Consumers price index: June 2025 quarter',
               'DateTaxonomyTerm': {'PublicationDate': published},
               'FeaturedMedia': {'SeriesData': [{'GraphCsvData': f'Quarter,Mar-25,Jun-25\r\n{series},2.5,2.7\r\n'}]}}
    return '<div data-value="1"></div><div data-value="'+html.escape(json.dumps(payload), quote=True)+'"></div>'


class FreeSources(unittest.TestCase):
    def setUp(self):
        self.n = load_sources()

    def test_nz_extracts_annual_data_and_actual_publication_not_heartbeat(self):
        row = self.n['parse_statsnz_cpi_release'](nz_release(), '2025Q2')
        self.assertEqual(row['value'], 2.7)
        self.assertEqual(row['date'], pd.Timestamp('2025-06-30'))
        self.assertEqual(row['release_date'], pd.Timestamp('2025-07-20 22:45'))
        self.assertIsNone(row['index_level'])
        self.assertIsNone(self.n['parse_statsnz_cpi_release']('{"status":"OK"}', '2025Q2'))

    def test_nz_wrong_period_quarterly_series_and_future_release_rejected(self):
        for payload, period in [(nz_release(),'2025Q1'),
                                (nz_release(series='CPI all groups (quarterly)'), '2025Q2'),
                                (nz_release(published='2099-07-21 10:45:00'), '2025Q2')]:
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](payload, period))

    def test_nz_has_no_hardcoded_live_records(self):
        self.n['requests'].get = Mock(return_value=SimpleNamespace(status_code=200, text='API OK'))
        self.assertIsNone(self.n['get_statsnz_cpi_data']()[0])
        self.assertEqual(self.n['requests'].get.call_count, 3)
        self.assertNotIn('NZD_STATS_NZ_CPI_RECORDS', Path(__file__).with_name('app.py').read_text())

    def bbk(self, extra='', unit='PROZENT', series='BBSSY.D.REN.EUR.A610.000000WT0202.A'):
        csv=f'"",{series},{series}_FLAGS\nunit,{unit},\nunit multiplier,One,\n2025-09-03,-0.20,\n2025-09-04,.,No value available\n2099-01-01,9.0,\n'+extra
        self.n['requests'].get = Mock(return_value=SimpleNamespace(text=csv, raise_for_status=lambda: None))
        return self.n['get_official_2y_data']('EUR','2025-09-05')

    def test_bundesbank_keeps_negative_yield_and_rejects_future_and_missing(self):
        result=self.bbk()
        self.assertEqual(len(result),1)
        self.assertEqual(result.iloc[0]['value'],-.2)
        self.assertIn('Germany 2Y',result.attrs['source'])

    def test_bundesbank_wrong_unit_tenor_or_conflicting_observation_rejected(self):
        self.assertIsNone(self.bbk(unit='INDEX'))
        self.assertIsNone(self.bbk(series='BBSSY.D.REN.EUR.A620.000000WT0505.A'))
        self.assertIsNone(self.bbk(extra='2025-09-03,3.1,\n'))

    def test_bundesbank_flagged_observation_not_used(self):
        frame=self.bbk(extra='2025-09-05,2.5,Estimated\n')
        self.assertEqual(len(frame),1)

    def test_canada_exact_benchmark_series_and_latest_observation(self):
        series='BD.CDN.2YR.DQ.YLD'
        payload={'seriesDetail':{series:{'label':'Benchmark bond yield: 2 year'}},
                 'observations':[{'d':'2025-09-04',series:{'v':'3.1'}},{'d':'2025-09-03',series:{'v':'3.0'}}]}
        self.n['requests'].get=Mock(return_value=SimpleNamespace(json=lambda:payload,raise_for_status=lambda:None))
        frame=self.n['get_official_2y_data']('CAD','2025-09-05')
        self.assertEqual(frame.iloc[-1]['value'],3.1)
        payload['seriesDetail'][series]['label']='Benchmark bond yield: 5 year'
        self.assertIsNone(self.n['get_official_2y_data']('CAD','2025-09-05'))

    def test_official_failure_can_use_existing_cache_without_api_key(self):
        self.n['get_official_2y_data']=lambda *args: None
        self.n['load_eodhd_bonds_cache']=lambda:{'DE2Y.GBOND':{}}
        self.n['get_eodhd_bond_historical']=lambda *args:(2.5,pd.Timestamp('2025-09-04'),True)
        value,date,source=self.n['get_genuine_2y_yield_historical']('EUR','2025-09-05')
        self.assertEqual(value,2.5)

    def fcs(self):
        payload={'status':True,'info':{'symbol':'EUR/USD','period':'1D'},
                 'response':{'1':{'t':1756944000,'o':1.10,'h':1.12,'l':1.09,'c':1.11}}}
        self.n['requests'].get=Mock(return_value=SimpleNamespace(status_code=200,json=lambda:payload))
        return payload

    def test_fcs_v4_dictionary_response_and_documented_daily_request(self):
        self.fcs()
        frame=self.n['fetch_fcs_history_live']('EUR/USD','fixture-key')
        self.assertEqual(frame.iloc[-1]['close'],1.11)
        self.assertEqual(self.n['requests'].get.call_args.kwargs['params']['period'],'1D')
        self.assertEqual(self.n['requests'].get.call_args.kwargs['params']['symbol'],'EURUSD')

    def test_fcs_wrong_pair_and_interval_cannot_supply_entry(self):
        for field,value in [('symbol','USDJPY'),('period','1h')]:
            payload=self.fcs();payload['info'][field]=value
            with self.assertRaisesRegex(ValueError,'SERIES_MISMATCH'):
                self.n['fetch_fcs_history_live']('EUR/USD','fixture-key')

    def test_fcs_current_profile_and_ticker_metadata_must_agree(self):
        payload=self.fcs()
        payload['info']={'ticker':'FCM:EURUSD','profile':{'symbol':'EURUSD'},'period':'1D'}
        self.assertEqual(len(self.n['fetch_fcs_history_live']('EUR/USD','fixture-key')),1)
        payload['info']['ticker']='FCM:USDJPY'
        with self.assertRaisesRegex(ValueError,'SERIES_MISMATCH'):
            self.n['fetch_fcs_history_live']('EUR/USD','fixture-key')

    def test_fcs_future_zero_and_impossible_candles_cannot_supply_entry(self):
        for field,value in [('c',0),('h',1.0),('t',4070908800)]:
            payload=self.fcs();payload['response']['1'][field]=value
            with self.assertRaisesRegex(ValueError,'NO_VALID_CANDLES'):
                self.n['fetch_fcs_history_live']('EUR/USD','fixture-key')

    def test_public_session_cannot_write_cot_or_authorize_itself(self):
        self.n['st'].session_state={'operator_authorized':True,'operator_password':'anything'}
        self.assertFalse(self.n['operator_is_authorized']())
        with self.assertRaises(PermissionError):
            self.n['save_manual_cot_entry']('USD','Bullish',1,90,'2025-09-01')
        self.n['load_api_key']=lambda name:'fixture-password-12345'
        self.assertFalse(self.n['operator_is_authorized']())
        self.n['st'].session_state['operator_password']='fixture-password-12345'
        self.assertTrue(self.n['operator_is_authorized']())


if __name__=='__main__':
    unittest.main()
