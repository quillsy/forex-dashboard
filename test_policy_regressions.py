"""Offline policy verification: real functions, synthetic official response fixtures."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pandas as pd
import live_data
from bs4 import BeautifulSoup

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def load_policy():
    tree = ast.parse(Path(__file__).with_name('app.py').read_text())
    names = {'operator_is_authorized', 'find_current_rate_episode_start', 'fetch_official_policy_rate_live', 'policy_rate_is_usable',
             'load_policy_rates_cache', 'save_policy_rates_cache', 'get_verified_policy_rate',
             'get_all_verified_policy_rates', 'refresh_all_verified_policy_rates'}
    constants = {'POLICY_RATE_DEFINITIONS', 'POLICY_VERIFICATION_MAX_AGE_DAYS', 'POLICY_OFFICIAL_HOSTS', 'POLICY_RATES_CACHE_FILE'}
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and (n.name.startswith('_policy_') or n.name in names))
             or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets))]
    scope = dict(datetime=datetime, timedelta=timedelta, io=io, json=json, os=os, pd=pd, live_data=live_data,
                 st=SimpleNamespace(session_state={}), requests=SimpleNamespace(get=Mock(side_effect=AssertionError('Network forbidden'))))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<policy-functions>', 'exec'), scope)
    scope['load_api_key'] = lambda name: None
    scope['_policy_now'] = lambda: NOW
    return scope


class PolicyRegressions(unittest.TestCase):
    def setUp(self):
        self.p = load_policy()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p['POLICY_RATES_CACHE_FILE'] = str(Path(self.tmp.name, 'policy.json'))

    def valid(self, currency='USD', rate=3.5):
        obj = self.p['_policy_empty'](currency)
        hosts = sorted(self.p['POLICY_OFFICIAL_HOSTS'][currency])
        obj.update(rate=rate, previous_rate=rate+.25, rate_effective_date='2025-12-11',
                   last_policy_decision_date='2026-07-29', verified_at=(NOW-timedelta(hours=2)).isoformat(),
                   verification_status='🟢 VERIFIED')
        obj['verification_evidence'] = [self.p['_policy_evidence'](currency, f'https://{hosts[0]}/{index}', rate,
            rate_effective_date=obj['rate_effective_date'], previous_rate=obj['previous_rate'],
            last_policy_decision_date=obj['last_policy_decision_date']) for index in (1, 2)]
        return obj

    def refresh(self, obj=None, result=None):
        self.p['POLICY_RATE_DEFINITIONS'] = {'USD': self.p['POLICY_RATE_DEFINITIONS']['USD']}
        self.p['save_policy_rates_cache']({'USD':obj} if obj else {})
        self.p['fetch_official_policy_rate_live'] = lambda *args: result or {'evidence': [], 'error': 'Timeout'}
        return self.p['refresh_all_verified_policy_rates']()['USD']

    def test_cache_miss_and_legacy_defaults_never_verify_or_write(self):
        for currency in self.p['POLICY_RATE_DEFINITIONS']:
            value=self.p['get_verified_policy_rate'](currency)
            self.assertIsNone(value['rate'])
            self.assertFalse(self.p['policy_rate_is_usable'](value))
        self.assertFalse(Path(self.p['POLICY_RATES_CACHE_FILE']).exists())
        legacy={'USD':{'rate':3.5, 'verification_status':'🟢 VERIFIED', 'verified_at':NOW.isoformat()}}
        self.p['save_policy_rates_cache'](legacy)
        value=self.p['get_verified_policy_rate']('USD')
        self.assertFalse(self.p['policy_rate_is_usable'](value))
        self.assertIsNone(value['rate'])
        self.assertIsNone(value['verified_at'])
        self.assertEqual(self.p['load_policy_rates_cache'](),legacy)

    def test_two_actual_distinct_official_proofs_are_required(self):
        valid=self.valid()
        self.assertTrue(self.p['policy_rate_is_usable'](valid))
        for mutation in ('one','same','wrong_host','wrong_rate','wrong_currency','wrong_instrument','future','stale','nat'):
            obj=deepcopy(valid)
            if mutation=='one': obj['verification_evidence'].pop()
            if mutation=='same': obj['verification_evidence'][1]=deepcopy(obj['verification_evidence'][0])
            if mutation=='wrong_host': obj['verification_evidence'][1]['source_url']='https://example.com/rate'
            if mutation=='wrong_rate': obj['verification_evidence'][1]['rate']=4
            if mutation=='wrong_currency': obj['verification_evidence'][1]['currency']='CAD'
            if mutation=='wrong_instrument': obj['verification_evidence'][1]['instrument']='Effective federal funds rate'
            if mutation=='future': obj['verification_evidence'][1]['retrieved_at']=(NOW+timedelta(days=1)).isoformat()
            if mutation=='stale': obj['verification_evidence'][1]['retrieved_at']=(NOW-timedelta(days=8)).isoformat()
            if mutation=='nat': obj['verification_evidence'][1]['retrieved_at']='NaT'
            with self.subTest(mutation=mutation): self.assertFalse(self.p['policy_rate_is_usable'](obj))

    def test_future_partial_and_missing_effective_dates_rejected(self):
        for date in ('2026-10-01','2026-06','2026',None):
            obj=self.valid(); obj['rate_effective_date']=date
            self.assertFalse(self.p['policy_rate_is_usable'](obj))
        self.assertEqual(self.p['_policy_date']('03.06.2026'),'2026-06-03')

    def test_rate_episode_preserves_zero_and_requires_boundary(self):
        episode=self.p['find_current_rate_episode_start']
        self.assertEqual(episode([('2025-03-21',.25),('2025-06-20',0),('2026-06-18',0)],0),'2025-06-20')
        self.assertEqual(episode([('2024-01-01',4.35),('2025-01-01',4.1),('2026-05-06',4.35)],4.35),'2026-05-06')
        self.assertIsNone(episode([('2026-01-01',0),('2026-06-18',0)],0))
        self.assertIsNone(episode([('2026-01-01',1),('2026-01-01',0)],0))

    def test_failed_refresh_preserves_last_verified_time_and_rate(self):
        old=self.valid(); result=self.refresh(old)
        self.assertEqual(result['rate'],old['rate'])
        self.assertEqual(result['verified_at'],old['verified_at'])
        self.assertEqual(result['verification_evidence'],old['verification_evidence'])
        self.assertEqual(result['verification_status'],'🟡 LAST VERIFIED')

    def test_one_source_change_and_conflicting_sources_block_activation(self):
        old=self.valid(); changed=self.valid(rate=3.75)['verification_evidence']
        for proofs in ([changed[0]], [changed[0],old['verification_evidence'][1]]):
            result=self.refresh(old,{'evidence':proofs})
            self.assertEqual(result['rate'],3.5)
            self.assertEqual(result['verification_status'],'🔴 UNVERIFIED CHANGE')
            self.assertFalse(self.p['policy_rate_is_usable'](result))

    def test_hold_preserves_episode_and_two_proofs_activate_change(self):
        old=self.valid(); held=deepcopy(old['verification_evidence'])
        held[0]['rate_effective_date']=None
        held[1]['last_policy_decision_date']='2026-09-02'
        result=self.refresh(old,{'evidence':held})
        self.assertEqual(result['rate_effective_date'],old['rate_effective_date'])
        self.assertEqual(result['last_policy_decision_date'],'2026-09-02')
        self.assertEqual(result['verification_status'],'🟢 VERIFIED_UNCHANGED')
        new=self.valid(rate=3.75)['verification_evidence']
        new[0]['rate_effective_date']='2026-09-02'
        result=self.refresh(old,{'evidence':new})
        self.assertEqual(result['rate'],3.75)
        self.assertEqual(result['previous_rate'],3.5)
        self.assertEqual(result['verified_at'],NOW.isoformat())

    def test_refresh_error_never_verifies_even_with_partial_proofs(self):
        old=self.valid()
        result=self.refresh(old,{'evidence':old['verification_evidence'],'error':'HTTPError'})
        self.assertEqual(result['verified_at'],old['verified_at'])
        self.assertEqual(result['verification_status'],'🟡 LAST VERIFIED')

    def test_cached_wrong_currency_cannot_be_used_and_override_requires_activation(self):
        self.p['save_policy_rates_cache']({'CHF':self.valid()})
        self.assertFalse(self.p['policy_rate_is_usable'](self.p['get_verified_policy_rate']('CHF')))
        manual={'rate':1,'verification_status':'🔴 MANUAL OVERRIDE'}
        self.assertFalse(self.p['policy_rate_is_usable'](manual))
        self.p['st'].session_state.update(emergency_manual_rates_override=True,manual_rate_JPY=1.0)
        self.assertFalse(self.p['policy_rate_is_usable'](manual))
        self.p['load_api_key'] = lambda name: 'fixture-password-12345'
        self.p['st'].session_state['operator_password'] = 'fixture-password-12345'
        obj=self.p['get_verified_policy_rate']('JPY')
        self.assertTrue(self.p['policy_rate_is_usable'](obj))
        self.assertIsNone(obj['verified_at'])

    def test_ecb_transport_fallback_requires_distinct_matching_official_documents(self):
        import requests
        self.p['_policy_request'] = Mock(side_effect=requests.RequestException('PROVIDER_REQUEST_FAILED'))
        def html(currency, url):
            if 'key_ecb_interest_rates' in url:
                text = '<table><tr><td>2026</td><td>17 Jun.</td><td>2.25</td><td>2.40</td><td>-</td><td>2.65</td></tr><tr><td>2025</td><td>11 Jun.</td><td>2</td><td>2.15</td><td>-</td><td>2.40</td></tr></table>'
            elif url.endswith('index.en.html'):
                text = '<a href="/ecb.mp260723~a.en.html">Decision</a>'
            else:
                text = 'The deposit facility will remain unchanged at 2.25%'
            return BeautifulSoup(text, 'html.parser')
        self.p['_policy_html'] = html
        result = self.p['fetch_official_policy_rate_live']('EUR')
        self.assertNotIn('error', result)
        first, second = result['evidence']
        self.assertEqual(first['rate'], 2.25)
        self.assertEqual(second['rate'], 2.25)
        self.assertEqual(first['rate_effective_date'], '2026-06-17')
        self.assertEqual(second['last_policy_decision_date'], '2026-07-23')
        self.assertNotEqual(first['source_url'], second['source_url'])
        self.p['_policy_html'] = lambda currency, url: (BeautifulSoup('The deposit facility remains at 2.0%', 'html.parser')
            if url.endswith('~a.en.html') else html(currency, url))
        self.assertIn('error', self.p['fetch_official_policy_rate_live']('EUR'))

    def test_ecb_api_invalid_json_schema_or_http_error_cannot_use_fallback(self):
        import requests
        for failure in (requests.exceptions.JSONDecodeError('bad JSON', 'x', 0), KeyError('dataSets')):
            self.p['_policy_request'] = Mock(return_value=SimpleNamespace(json=Mock(side_effect=failure)))
            self.p['_policy_html'] = Mock(side_effect=AssertionError('No fallback for invalid data'))
            self.assertIn('error', self.p['fetch_official_policy_rate_live']('EUR'))
            self.p['_policy_html'].assert_not_called()
        self.p['_policy_request'] = Mock(side_effect=requests.HTTPError('HTTP 403'))
        self.assertIn('error', self.p['fetch_official_policy_rate_live']('EUR'))
        self.p['_policy_html'].assert_not_called()

    def test_all_eight_parsers_with_official_document_formats(self):
        # Synthetic fixtures deliberately include hold rows, competing instruments,
        # a non-English ECB link and BOJ PDF word splitting seen in real responses.
        tables={
          'GBP':'<tr><td>18 Dec 2025</td><td>3.75</td></tr><tr><td>7 Aug 2025</td><td>4</td></tr>',
          'AUD':'<tr><td>12 Aug 2026</td><td>0</td><td>4.35</td><td><a href="/decision">Statement</a></td></tr><tr><td>6 May 2026</td><td>.25</td><td>4.35</td></tr><tr><td>18 Mar 2026</td><td>0</td><td>4.1</td></tr>',
          'NZD':'<tr><td>2 September 2026</td><td>2.75</td><td><a href="/decision">Media release</a></td></tr><tr><td>8 July 2026</td><td>2.5</td></tr>',
          'EUR':'<tr><td>2026</td><td>17 Jun.</td><td>2.25</td><td>2.40</td><td>-</td><td>2.65</td></tr><tr><td>2025</td><td>11 Jun.</td><td>2</td><td>2.15</td><td>-</td><td>2.40</td></tr>'}
        def html(currency,url):
            if 'Bank-Rate.asp' in url or url.endswith('cash-rate/') or url.endswith('monetary-policy-decisions') or 'key_ecb_interest_rates' in url:
                text='<table>'+tables[currency]+'</table> following day'
            elif currency=='USD':
                text='<a href="/newsevents/pressreleases/monetary20260729a.htm">Statement</a>' if 'fomccalendars' in url else 'maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent'
            elif currency=='EUR':
                text='<a href="/ecb.mp260723~a.sv.html">SV</a><a href="/ecb.mp260723~a.en.html">EN</a>' if url.endswith('index.en.html') else 'The deposit facility will remain unchanged at 2.25%'
                if url.endswith('.sv.html'): raise AssertionError('Parser selected non-English document')
            elif currency=='GBP': text='Current Bank Rate 3.75% Published on 30 July 2026'
            elif currency=='CAD': text='<a href="/2026/09/fad-press-release-2026-09-02/">Decision</a>' if 'key-interest-rate' in url else 'hold its target for the overnight rate at 2.25%'
            elif currency=='CHF': text='SNB policy rate 0.00% valid from 20.06.2025' if 'current_interest' in url else '<a href="/pre_20260618">Decision</a>' if url.endswith('/decisions') else 'leave the SNB policy rate unchanged at 0%'
            elif currency=='AUD': text='<time datetime="2026-08-11"></time> leave the cash rate target unchanged at 4.35 per cent'
            elif currency=='NZD': text='<time datetime="2026-09-02"></time> increase the Official Cash Rate (OCR) to 2.75%'
            else: text='4. Interest Rate The interest rate shall be 1.0 percent' if 'yoryo36' in url else '<a href="/k260731a.pdf">Statement</a><a href="/k260616a.pdf">Change in the Guideline for Money Market Operations</a>'
            return BeautifulSoup(text,'html.parser')
        def request(currency,url,params=None):
            payload={'observations':[{'date':'2025-12-10','value':'3.75'},{'date':'2025-12-11','value':'3.5'}]} if currency=='USD' else {'observations':[{'d':'2025-10-29','V39079':{'v':'2.5'}},{'d':'2025-10-30','V39079':{'v':'2.25'}}]} if currency=='CAD' else {'dataSets':[{'series':{'0':{'observations':{'0':[2.0],'1':[2.25]}}}}], 'structure':{'dimensions':{'observation':[{'values':[{'id':'2025-06-11'},{'id':'2026-06-17'}]}]}}}
            return SimpleNamespace(json=lambda:payload)
        self.p['_policy_html']=html; self.p['_policy_request']=request
        self.p['_policy_pdf_text']=lambda currency,url: 'The uncollateralized o vernight call rate will remain at around 1.0 percent. The new guideline will be effective from June 17, 2026.'
        expected={'USD':(3.5,'2025-12-11'),'EUR':(2.25,'2026-06-17'),'GBP':(3.75,'2025-12-18'),'CAD':(2.25,'2025-10-30'),'CHF':(0,'2025-06-20'),'AUD':(4.35,'2026-05-06'),'NZD':(2.75,'2026-09-02'),'JPY':(1,'2026-06-17')}
        for currency,(rate,date) in expected.items():
            with self.subTest(currency=currency):
                result=self.p['fetch_official_policy_rate_live'](currency,fred_key='offline-fixture')
                self.assertNotIn('error',result)
                first,second=result['evidence']
                self.assertEqual(first['rate'],rate); self.assertEqual(second['rate'],rate)
                self.assertEqual(first['rate_effective_date'],date)
                self.assertTrue(second['last_policy_decision_date'])


if __name__ == '__main__':
    unittest.main()
