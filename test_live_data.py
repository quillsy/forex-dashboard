import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import requests
import live_data as live
from provider_transport import CollectorTransport

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

class LiveDataTests(unittest.TestCase):
    def record(self):
        return live.build_record('GDP', 20, {'value': 1.8, 'date': '2026-06-30', 'source': 'Eurostat', 'frequency': 'quarterly'}, 'FRESH', NOW.isoformat())

    def test_hourly_check_expires_at_boundary(self):
        row = self.record()
        self.assertTrue(live.eligible(row, NOW)[0])
        self.assertFalse(live.eligible(row, NOW + timedelta(hours=1))[0])

    def test_calendar_deadline_and_age_both_enforced(self):
        row = self.record()
        row['next_due_at'] = (NOW + timedelta(days=1)).isoformat()
        self.assertTrue(live.eligible(row, NOW + timedelta(hours=2))[0])
        self.assertFalse(live.eligible(row, NOW + timedelta(days=1))[0])
        row['expires_at'] = NOW.isoformat()
        self.assertFalse(live.eligible(row, NOW)[0])

    def test_failed_fetch_keeps_original_check_and_value(self):
        row = self.record()
        updated = live.build_record('GDP', None, {}, 'UNAVAILABLE', (NOW + timedelta(minutes=30)).isoformat(), row)
        self.assertEqual(updated['checked_at'], row['checked_at'])
        self.assertEqual(updated['score'], 20)
        self.assertIn('last_error', updated)
        self.assertFalse(live.eligible(updated, NOW + timedelta(hours=1))[0])

    def test_conflicting_or_unverified_source_never_reuses_old_value(self):
        row = live.build_record('GDP', 30, {'date': '2026-06-30'}, 'FRESH', NOW.isoformat(), self.record(), 'UNVERIFIED', 'Conflict')
        self.assertFalse(live.eligible(row, NOW)[0])

    def test_future_check_release_and_nonfinite_rejected(self):
        for changes in ({'checked_at': (NOW+timedelta(seconds=1)).isoformat()}, {'published_at': (NOW+timedelta(seconds=1)).isoformat()}, {'score': float('nan')}, {'score': True}):
            row = self.record(); row.update(changes)
            self.assertFalse(live.eligible(row,NOW)[0])

    def test_malformed_metadata_and_missing_observations_block(self):
        for changes in ({'published_at':'not-a-date'}, {'next_due_at':'tomorrow'}, {'observation':{}}, {'freshness':'STALE'}):
            row=self.record();row.update(changes)
            self.assertFalse(live.eligible(row,NOW)[0])

    def test_failed_fetch_projects_previous_public_record(self):
        row=self.record();row['api_key']='private';row['observation']['secret']='private'
        out=live.build_record('GDP',None,{},'UNAVAILABLE',NOW.isoformat(),row)
        self.assertNotIn('private',str(out))

    def test_cache_restarts_do_not_renew_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            data={'model_version':live.MODEL,'currencies':{'EUR':{'GDP':self.record()}}}
            live.save(data,path)
            loaded=live.load(path)
            self.assertEqual(loaded,data)
            result=live.details('EUR',NOW+timedelta(hours=2),loaded)
            self.assertEqual(result['_completeness'],0)
            self.assertIsNone(result['GDP'])

    def test_40_factor_gate_uses_values_not_reported_coverage(self):
        records={factor:live.build_record(factor,20,{'value':1.8,'policy_rate':3.6,'yield_2y':3.6,'date':'2026-09-04'},'FRESH',NOW.isoformat()) for factor in live.FACTORS}
        data={'currencies':{'EUR':records}}
        self.assertEqual(live.details('EUR',NOW,data)['_completeness'],100)
        records['PMI']['validation']='UNVERIFIED'
        self.assertEqual(live.details('EUR',NOW,data)['_completeness'],80)

    def test_public_projection_drops_credentials_raw_and_query_urls(self):
        out=live.public_observation({'value':2,'api_key':'private','raw_response':{'token':'private'},'source':'https://example.com/?token=private','date':'2026-08-01'})
        self.assertNotIn('private',str(out))
        self.assertEqual(out['value'],2)

    def test_official_attribution_links_exclude_queries_and_other_hosts(self):
        url='https://www.stats.govt.nz/information-releases/labour-market-statistics-june-2026-quarter/'
        self.assertEqual(live.public_observation({'source_url':url})['source_url'], url)
        for bad in (url+'?token=private',url+'#private',url.replace('www.stats.govt.nz','evil.example'),url.replace('https:','http:')):
            self.assertNotIn('source_url', live.public_observation({'source_url':bad}))

    def test_source_table_shows_both_policy_and_yield_when_value_is_null(self):
        record=live.build_record('Geldpolitik',20,{'value':None,'yield_2y':4.34,'policy_rate':4.25,
            'date':'2026-09-04','source':'FRED'},'FRESH',NOW.isoformat())
        data={'completed_at':NOW.isoformat(),'currencies':{'USD':{'Geldpolitik':record},
            'GBP':{'Geldpolitik':{'score':None,'last_error':'SOURCE_UNAVAILABLE','observation':{}}}}}
        st=MagicMock()
        with patch.object(live,'load',return_value=data), patch.object(live,'now_utc',return_value=NOW):
            live.render_status(st)
        rows=st.dataframe.call_args_list[1].args[0]
        usd=next(r for r in rows if r['Währung']=='USD' and r['Faktor']=='Geldpolitik')
        gbp=next(r for r in rows if r['Währung']=='GBP' and r['Faktor']=='Geldpolitik')
        self.assertEqual(usd['Wert'],4.34)
        self.assertEqual(usd['Leitzins (%)'],4.25)
        self.assertEqual(gbp['Letzter Abruf'],'Fehlgeschlagen; kein geprüfter Wert')

    def test_individual_observation_visible_below_score_threshold_but_expired_hidden(self):
        record=live.build_record('Arbeitsmarkt',20,{'value':5.6,'date':'2026-06-30',
            'frequency':'quarterly','next_due_at':'2026-11-03T11:00:00+00:00'},'FRESH',NOW.isoformat())
        expired=self.record()
        data={'completed_at':NOW.isoformat(),'currencies':{'NZD':{'Arbeitsmarkt':record,'GDP':expired}}}
        st=MagicMock()
        with patch.object(live,'load',return_value=data), patch.object(live,'now_utc',return_value=NOW+timedelta(hours=2)):
            live.render_status(st)
        overview=st.dataframe.call_args_list[0].args[0]
        nzd=next(row for row in overview if row['Währung']=='NZD')
        self.assertEqual(nzd['Arbeitslosenquote (%)'],'5.60 · 2026-06-30')
        self.assertEqual(nzd['Reales GDP (% zum Vorjahr)'],'—')
        self.assertEqual(live.details('NZD',NOW+timedelta(hours=2),data)['_completeness'],20)

    def test_overview_preserves_official_provisional_flag(self):
        for flag in ('p', 'e'):
            record=live.build_record('Inflation',20,{'value':3.2,'date':'2026-08-01',
                'reference_period':'2026-08','provider_status':flag},'FRESH',NOW.isoformat())
            data={'completed_at':NOW.isoformat(),'currencies':{'EUR':{'Inflation':record}}}
            st=MagicMock()
            with patch.object(live,'load',return_value=data), patch.object(live,'now_utc',return_value=NOW):
                live.render_status(st)
            overview=st.dataframe.call_args_list[0].args[0]
            eur=next(row for row in overview if row['Währung']=='EUR')
            self.assertEqual(eur['Inflation (% zum Vorjahr)'],'3.20 · 2026-08 (vorläufig)')

    def test_quarterly_labour_age_still_expires_and_release_deadline_wins(self):
        row=live.build_record('Arbeitsmarkt',20,{'value':5.6,'date':'2026-06-30','frequency':'quarterly',
            'next_due_at':'2026-11-03T11:00:00+00:00'},'FRESH',NOW.isoformat())
        self.assertEqual(row['expires_at'],'2026-12-27T00:00:00+00:00')
        self.assertTrue(live.eligible(row,datetime(2026,10,1,tzinfo=timezone.utc))[0])
        self.assertFalse(live.eligible(row,datetime(2026,11,3,11,tzinfo=timezone.utc))[0])

class TransportTests(unittest.TestCase):
    def response(self,status=200):
        response=requests.Response();response.status_code=status;response._content=b'{"value":2}'
        return response

    def test_ui_never_sends_requests(self):
        client=Mock(); transport=CollectorTransport(client)
        with patch.dict(os.environ,{'FX_COLLECTOR':'0'}), self.assertRaises(requests.RequestException):
            transport.get('https://example.com',params={'api_key':'private'})
        client.get.assert_not_called()

    def test_duplicate_request_only_spends_once(self):
        client=Mock();client.get.return_value=self.response();transport=CollectorTransport(client)
        with patch.dict(os.environ,{'FX_COLLECTOR':'1'}):
            transport.get('https://example.com',params={'api_key':'private'})
            transport.get('https://example.com',params={'api_key':'private'})
        self.assertEqual(client.get.call_count,1)
        self.assertNotIn('private',str(transport.usage))

    def test_exhaustion_does_not_retry_or_echo_error(self):
        client=Mock();response=self.response(429);response._content=b'api_key=private';client.get.return_value=response
        transport=CollectorTransport(client)
        with patch.dict(os.environ,{'FX_COLLECTOR':'1'}):
            result=transport.get('https://example.com')
            self.assertNotIn('private',result.text)
            with self.assertRaises(requests.RequestException): transport.get('https://example.com/other')
        self.assertEqual(client.get.call_count,1)

    def test_compromised_provider_requires_explicit_rotation(self):
        client=Mock();transport=CollectorTransport(client)
        with patch.dict(os.environ,{'FX_COLLECTOR':'1','FX_ROTATED_PROVIDER_HOSTS':''}), self.assertRaises(requests.RequestException):
            transport.get('https://api-v4.fcsapi.com/forex/history')
        client.get.assert_not_called()

if __name__ == '__main__': unittest.main()
