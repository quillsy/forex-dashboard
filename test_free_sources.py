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
import live_data

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
              check_demo_active=lambda: False, use_live_core_cache=lambda *args: False)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<free-sources>', 'exec'), ns)
    return ns


def nz_release(series='CPI all groups (annual)', published='2025-07-21 10:45:00'):
    payload = {'Title': 'Consumers price index: June 2025 quarter',
               'DateTaxonomyTerm': {'PublicationDate': published},
               'FeaturedMedia': {'SeriesData': [{'GraphCsvData': f'Quarter,Mar-25,Jun-25\r\n{series},2.5,2.7\r\n'}]}}
    return '<div data-value="1"></div><div data-value="'+html.escape(json.dumps(payload), quote=True)+'"></div>'


def nz_release_with_confirmed_next_date(duplicate=False):
    next_date_block = {'ClassName': 'TextBlock',
        'Title': 'Consumers price index: June 2026 quarter – next release date',
        'Content': '<h2>Next release</h2><p><em>Consumers price index: September 2026 quarter</em> will be released on 22 October 2026.</p>'}
    payload = {
        'Title': 'Consumers price index: June 2026 quarter',
        'DateTaxonomyTerm': {'PublicationDate': '2026-07-21 10:45:00'},
        'FeaturedMedia': {'SeriesData': [{'GraphCsvData':
            'Quarter,Mar-26,Jun-26\r\nCPI all groups (annual),3.1,4.1\r\n'}]},
        'PageBlocks': [next_date_block] * (2 if duplicate else 1),
    }
    return '<div data-value="'+html.escape(json.dumps(payload), quote=True)+'"></div>'


class FreeSources(unittest.TestCase):
    def setUp(self):
        self.n = load_sources()

    def nz_payload(self, payload):
        return '<div data-value="'+html.escape(json.dumps(payload), quote=True)+'"></div>'

    def nz_original_payload(self):
        from bs4 import BeautifulSoup
        tag = BeautifulSoup(nz_release_with_confirmed_next_date(), 'html.parser').select_one('[data-value]')
        return json.loads(tag['data-value'])

    def test_nz_conflicting_values_in_rows_charts_and_payloads_are_rejected(self):
        import copy
        original = self.nz_original_payload()
        changed = copy.deepcopy(original)
        chart = changed['FeaturedMedia']['SeriesData'][0]
        chart['GraphCsvData'] = chart['GraphCsvData'].replace('3.1,4.1', '3.1,9.9')
        for payload in (self.nz_payload(original)+self.nz_payload(changed),
                        self.nz_payload(changed)+self.nz_payload(original)):
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](payload, '2026Q2'))
        for reverse in (False, True):
            together = copy.deepcopy(original)
            charts = [original['FeaturedMedia']['SeriesData'][0], chart]
            together['FeaturedMedia']['SeriesData'] = charts[::-1] if reverse else charts
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(together), '2026Q2'))
            rows = ['CPI all groups (annual),3.1,4.1', 'CPI all groups (annual),3.1,9.9']
            together['FeaturedMedia']['SeriesData'] = [{'GraphCsvData':
                'Quarter,Mar-26,Jun-26\r\n'+'\r\n'.join(rows[::-1] if reverse else rows)+'\r\n'}]
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(together), '2026Q2'))

    def test_nz_agreeing_duplicates_preserve_value_publication_and_deadline(self):
        import copy
        original = self.nz_original_payload()
        baseline = self.n['parse_statsnz_cpi_release'](self.nz_payload(original), '2026Q2')
        repeated = copy.deepcopy(original)
        repeated['FeaturedMedia']['SeriesData'] *= 2
        repeated['PageBlocks'] *= 2
        for payload in (self.nz_payload(repeated), self.nz_payload(original)*2,
                        nz_release()+self.nz_payload(repeated)):
            self.assertEqual(self.n['parse_statsnz_cpi_release'](payload, '2026Q2'), baseline)

    def test_nz_conflicting_or_invalid_release_metadata_cannot_hide_after_valid_copy(self):
        import copy
        original = self.nz_original_payload()
        for publication in ('2026-07-22 10:45:00', '2099-07-21 10:45:00', 'invalid', None, 'NaT'):
            changed = copy.deepcopy(original)
            changed['DateTaxonomyTerm']['PublicationDate'] = publication
            for payload in (self.nz_payload(original)+self.nz_payload(changed),
                            self.nz_payload(changed)+self.nz_payload(original)):
                self.assertIsNone(self.n['parse_statsnz_cpi_release'](payload, '2026Q2'))
        changed = copy.deepcopy(original)
        changed['PageBlocks'].append({**changed['PageBlocks'][0],
            'Content': changed['PageBlocks'][0]['Content'].replace('22 October', '23 October')})
        self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(changed), '2026Q2'))
        changed = copy.deepcopy(original)
        changed['PageBlocks'][0]['Content'] = changed['PageBlocks'][0]['Content'].replace('22 October', '23 October')
        self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(original)+self.nz_payload(changed), '2026Q2'))

    def test_nz_invalid_target_cell_or_truncated_annual_row_cannot_hide_after_valid_copy(self):
        import copy
        original = self.nz_original_payload()
        for raw in ('NaN', 'inf', '-', '', '26'):
            changed = copy.deepcopy(original)
            changed['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = (
                changed['FeaturedMedia']['SeriesData'][0]['GraphCsvData'].replace('3.1,4.1', '3.1,'+raw))
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(original)+self.nz_payload(changed), '2026Q2'))
        changed['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = 'Quarter,Mar-26,Jun-26\r\nCPI all groups (annual),3.1\r\n'
        self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(original)+self.nz_payload(changed), '2026Q2'))

    def test_nz_contradictory_taxonomy_or_incomplete_matching_copy_blocks(self):
        import copy
        original = self.nz_original_payload()
        for fields in ({'DisplayName': 'Different release'}, {'DateString': '22 July 2026'},
                       {'DateString': 'invalid'}):
            changed = copy.deepcopy(original)
            changed['DateTaxonomyTerm'].update(fields)
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(changed), '2026Q2'))
        for fields in ({}, {'PublicationDate': '2026-07-22 10:45:00'}):
            changed = copy.deepcopy(original)
            changed['DateTaxonomyTerm'].update(fields)
            changed['FeaturedMedia']['SeriesData'] = []
            for payload in (self.nz_payload(original)+self.nz_payload(changed),
                            self.nz_payload(changed)+self.nz_payload(original)):
                self.assertIsNone(self.n['parse_statsnz_cpi_release'](payload, '2026Q2'))
        original['DateTaxonomyTerm'].update(DisplayName=original['Title'], DateString='21 July 2026')
        self.assertEqual(self.n['parse_statsnz_cpi_release'](self.nz_payload(original), '2026Q2')['value'], 4.1)

    def test_nz_missing_calendar_differs_from_malformed_present_calendar(self):
        import copy
        original = self.nz_original_payload()
        absent = copy.deepcopy(original)
        absent.pop('PageBlocks')
        self.assertEqual(self.n['parse_statsnz_cpi_release'](self.nz_payload(absent), '2026Q2')['value'], 4.1)
        self.assertNotIn('next_due_at', self.n['parse_statsnz_cpi_release'](self.nz_payload(absent), '2026Q2'))
        for invalid_blocks in ({}, None, [None]):
            changed = copy.deepcopy(original)
            changed['PageBlocks'] = invalid_blocks
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(changed), '2026Q2'))
        for field, value in (('ClassName', 'WrongClass'), ('Title', 'Wrong next release title'),
                             ('Content', None), ('Content', 'Next release invalid.')):
            changed = copy.deepcopy(original)
            changed['PageBlocks'][0][field] = value
            self.assertIsNone(self.n['parse_statsnz_cpi_release'](self.nz_payload(changed), '2026Q2'))

    def test_nz_conflicting_release_revokes_previous_valid_collector_record(self):
        import copy
        import os
        from datetime import timedelta, timezone
        from unittest.mock import patch
        from test_core_regressions import load_core
        checked = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return checked.astimezone(tz) if tz else checked.replace(tzinfo=None)

        class Timestamp:
            def __new__(cls, *args, **kwargs):
                return pd.Timestamp(*args, **kwargs)

            @classmethod
            def now(cls, tz=None):
                return pd.Timestamp(checked).tz_convert(tz) if tz else pd.Timestamp(checked).tz_localize(None)

        frozen_pd = SimpleNamespace(**{name: getattr(pd, name) for name in ('Period', 'DataFrame', 'Timedelta', 'isna', 'to_datetime')},
                                    Timestamp=Timestamp)
        self.n.update(pd=frozen_pd, datetime=Clock, live_data=live_data)
        original = self.nz_original_payload()
        changed = copy.deepcopy(original)
        changed['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = (
            changed['FeaturedMedia']['SeriesData'][0]['GraphCsvData'].replace('3.1,4.1', '3.1,9.9'))
        # An older complete page cannot overrule an invalid newer release.
        older = copy.deepcopy(original)
        older['Title'] = 'Consumers price index: March 2026 quarter'
        older['DateTaxonomyTerm']['PublicationDate'] = '2026-04-21 10:45:00'
        older['PageBlocks'] = [{'ClassName': 'TextBlock',
            'Title': older['Title']+' – next release date',
            'Content': 'Next release Consumers price index: June 2026 quarter will be released on 28 September 2026.'}]
        current_html = self.nz_payload(original)+self.nz_payload(changed)
        def response(url, **kwargs):
            if 'june-2026' in url:
                return SimpleNamespace(status_code=200, text=current_html)
            if 'march-2026' in url:
                return SimpleNamespace(status_code=200, text=self.nz_payload(older))
            return SimpleNamespace(status_code=404, text='')
        self.n['requests'].get = Mock(side_effect=response)
        self.assertEqual(self.n['parse_statsnz_cpi_release'](self.nz_payload(original), '2026Q2')['value'], 4.1)
        self.assertIsNone(self.n['get_statsnz_cpi_data'](propagate_transport=True)[0])
        core = load_core()
        route = next(node for node in ast.parse(Path(__file__).with_name('app.py').read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name == 'get_cpi_yoy_details')
        exec(compile(ast.Module(body=[route], type_ignores=[]), '<nz-cpi-route>', 'exec'), core)
        core.update(datetime=Clock, requests=requests, get_statsnz_cpi_data=self.n['get_statsnz_cpi_data'],
                    get_ons_cpi_data=Mock(side_effect=AssertionError('Unexpected ONS call')),
                    get_statcan_cpi_data=Mock(side_effect=AssertionError('Unexpected StatCan call')))
        previous = live_data.build_record('Inflation', 100, {
            'value': 4.1, 'date': '2026-06-30', 'source': 'Stats NZ', 'series_id': 'CPIQ.SE9A',
            'frequency': 'quarterly', 'next_due_at': '2026-10-21T11:00:00+00:00',
            'needs_hourly_check': True}, 'AGING', (checked-timedelta(minutes=30)).isoformat())
        self.assertTrue(live_data.eligible(previous, checked, factor='Inflation', currency='NZD')[0])
        app = SimpleNamespace(requests=self.n['requests'], FRED_KEY=None,
                              compute_currency_details=core['compute_currency_details'])
        for current_html, valid in ((self.nz_payload(original), True),
                                    (self.nz_payload(original)+self.nz_payload(changed), False)):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'live.json'
                live_data.save({'model_version': live_data.MODEL, 'currencies': {'NZD': {'Inflation': previous}}}, path)
                with patch.dict(os.environ, {'FX_COLLECTOR': '1'}), \
                     patch.object(live_data, 'now_utc', return_value=checked), \
                     patch.object(live_data, 'CURRENCIES', ('NZD',)), \
                     patch.object(live_data, 'FACTORS', {'Inflation': live_data.FACTORS['Inflation']}):
                    live_data.collect(app, path)
                record = live_data.load(path)['currencies']['NZD']['Inflation']
                self.assertEqual(record['validation'], 'VALID' if valid else 'UNVERIFIED')
                self.assertEqual(record['score'], 100 if valid else None)
                self.assertNotIn('last_error', record)
                self.assertEqual(live_data.eligible(record, checked, factor='Inflation', currency='NZD')[0], valid)

    def test_nz_extracts_annual_data_and_actual_publication_not_heartbeat(self):
        row = self.n['parse_statsnz_cpi_release'](nz_release(), '2025Q2')
        self.assertEqual(row['value'], 2.7)
        self.assertEqual(row['date'], pd.Timestamp('2025-06-30'))
        self.assertEqual(row['release_date'], pd.Timestamp('2025-07-20 22:45'))
        self.assertIsNone(row['index_level'])
        self.assertIsNone(self.n['parse_statsnz_cpi_release']('{"status":"OK"}', '2025Q2'))

    def test_nz_official_date_only_release_blocks_prior_quarter(self):
        row = self.n['parse_statsnz_cpi_release'](nz_release_with_confirmed_next_date(), '2026Q2')
        self.assertEqual(row['value'], 4.1)
        self.assertEqual(row['next_due_at'], '2026-10-21T11:00:00+00:00')
        self.assertEqual(row['next_due_precision'], 'date_only_start_of_NZ_day')
        repeated = self.n['parse_statsnz_cpi_release'](nz_release_with_confirmed_next_date(duplicate=True), '2026Q2')
        self.assertEqual(repeated['next_due_at'], row['next_due_at'])
        record = live_data.build_record('Inflation', 100,
            {'value': row['value'], 'date': '2026-06-30', 'frequency': 'quarterly',
             'next_due_at': row['next_due_at'], 'next_due_precision': row['next_due_precision']},
            'AGING', '2026-09-24T22:00:00+00:00')
        before = datetime.fromisoformat('2026-10-21T10:59:59+00:00')
        due = datetime.fromisoformat(row['next_due_at'])
        self.assertTrue(live_data.eligible(record, before, factor='Inflation', currency='NZD')[0])
        self.assertFalse(live_data.eligible(record, due, factor='Inflation', currency='NZD')[0])

    def test_nz_present_invalid_next_release_blocks_instead_of_becoming_unknown(self):
        original = nz_release_with_confirmed_next_date()
        for changed in (original.replace('September 2026 quarter', 'December 2026 quarter'),
                        original.replace('22 October 2026', '22 October 2099')):
            row = self.n['parse_statsnz_cpi_release'](changed, '2026Q2')
            self.assertIsNone(row)

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
        # Current official metadata changed its label but not series/tenor.
        payload['seriesDetail'][series]['label']='Benchmark bond yield, 2-year'
        frame=self.n['get_official_2y_data']('CAD','2025-09-05')
        self.assertEqual(frame.iloc[-1]['value'],3.1)
        payload['seriesDetail']={'BD.CDN.5YR.DQ.YLD':{'label':'Benchmark bond yield, 2-year'}}
        self.assertIsNone(self.n['get_official_2y_data']('CAD','2025-09-05'))
        payload['seriesDetail']={series:{'label':'Benchmark bond yield, 2-year'}}
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
