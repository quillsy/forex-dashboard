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

    def test_known_release_blocks_successful_but_older_mirror_observation(self):
        release = datetime(2026, 9, 2, 1, 30, tzinfo=timezone.utc)
        old = live.build_record('GDP', 20, {'value': 2.5, 'date': '2026-03-31',
            'source': 'FRED', 'frequency': 'quarterly'}, 'AGING',
            (release - timedelta(minutes=1)).isoformat())
        self.assertTrue(live.eligible(old, release-timedelta(seconds=1), currency='AUD')[0])
        self.assertFalse(live.eligible(old, release, currency='AUD')[0])
        old['checked_at'] = (release+timedelta(minutes=1)).isoformat()
        allowed, reason = live.eligible(old, release+timedelta(minutes=2), currency='AUD')
        self.assertFalse(allowed)
        self.assertIn('2026-Q2', reason)
        # The same quarter may be dated at its start or end by a provider.
        for reference in ('2026-04-01', '2026-06-30'):
            current = live.build_record('GDP', 20, {'value': 2.1, 'date': reference,
                'source': 'FRED', 'frequency': 'quarterly'}, 'FRESH', release.isoformat())
            self.assertTrue(live.eligible(current, release, currency='AUD')[0])

    def test_all_known_release_floors_block_old_periods(self):
        from source_contracts import KNOWN_RELEASES
        now = datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc)
        for (currency, factor), release in KNOWN_RELEASES.items():
            with self.subTest(currency=currency, factor=factor):
                old_date = '2026-09-03' if factor == 'Geldpolitik' else '2026-03-31' if factor == 'GDP' else '2026-06-01'
                row = live.build_record(factor, 20, {'value': 2.5, 'date': old_date, 'policy_rate': 4.0, 'yield_2y': 4.34},
                                        'AGING', now.isoformat())
                self.assertFalse(live.eligible(row, now, currency=currency)[0])
                row['observation']['date'] = release['period_start']
                self.assertTrue(live.eligible(row, now, currency=currency)[0])

    def test_known_release_is_enforced_on_persisted_data_and_status(self):
        row = live.build_record('Arbeitsmarkt', 20, {'value': 4.4, 'date': '2026-06-01',
            'frequency': 'monthly', 'source': 'FRED'}, 'AGING', NOW.isoformat())
        data = {'model_version': live.MODEL, 'completed_at': NOW.isoformat(),
                'currencies': {'AUD': {'Arbeitsmarkt': row}}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'live.json'
            live.save(data, path)
            restored = live.load(path)
        result = live.details('AUD', NOW, restored)
        self.assertIsNone(result['Arbeitsmarkt'])
        self.assertIn('2026-07', result['_blocking_reasons']['Arbeitsmarkt'])
        st = MagicMock()
        with patch.object(live, 'load', return_value=restored), patch.object(live, 'now_utc', return_value=NOW):
            live.render_status(st)
        rows = st.dataframe.call_args_list[1].args[0]
        aud = next(r for r in rows if r['Währung']=='AUD' and r['Faktor']=='Arbeitsmarkt')
        self.assertEqual(aud['Status'], 'Gesperrt')
        self.assertIn('2026-07', aud['Grund'])

    def test_official_same_period_conflict_blocks_either_selected_value(self):
        now = datetime(2026, 9, 8, 10, 1, tzinfo=timezone.utc)
        for value in (3.2, 3.3):
            row = live.build_record('Inflation', 60, {'value': value, 'date': '2026-08-31'}, 'FRESH', now.isoformat())
            allowed, reason = live.eligible(row, now, currency='EUR')
            self.assertFalse(allowed)
            self.assertIn('Quellenkonflikt', reason)
            self.assertTrue(live.eligible(row, now, currency='GBP')[0])

    def test_release_calendar_does_not_override_required_hourly_check(self):
        row = self.record()
        row['next_due_at'] = (NOW + timedelta(days=30)).isoformat()
        row['observation']['needs_hourly_check'] = True
        self.assertTrue(live.eligible(row, NOW+timedelta(minutes=59))[0])
        self.assertFalse(live.eligible(row, NOW+timedelta(hours=1))[0])
        projected = live.public_observation(row['observation'])
        self.assertIs(projected['needs_hourly_check'], True)

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

    def test_bea_publication_evidence_url_is_retained_without_untrusted_urls(self):
        url='https://www.bea.gov/news/2026/gdp-second-estimate-and-corporate-profits-2nd-quarter-2026'
        self.assertEqual(live.public_observation({'publication_basis':url})['publication_basis'],url)
        for bad in (url+'?token=private',url.replace('www.bea.gov','evil.example')):
            self.assertNotIn('publication_basis',live.public_observation({'publication_basis':bad}))

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
            self.assertEqual(eur['Inflation (% zum Vorjahr)'],'3.20 · 2026-08 (vorläufig) · HICP')

    def test_bfs_provisional_flag_and_dataset_attribution_are_visible(self):
        record=live.build_record('Inflation',20,{'value':0.9,'date':'2026-08-31',
            'reference_period':'2026-08','is_estimate':True,'source':'BFS',
            'source_title':'HVPI Schweiz (2025=100): Detailresultate seit 2005',
            'source_url':'https://dam-api.bfs.admin.ch/hub/api/dam/assets/36835033/master'},
            'FRESH',NOW.isoformat())
        st=MagicMock()
        with patch.object(live,'load',return_value={'currencies':{'CHF':{'Inflation':record}}}), patch.object(live,'now_utc',return_value=NOW):
            live.render_status(st)
        row=next(r for r in st.dataframe.call_args_list[1].args[0] if r['Währung']=='CHF' and r['Faktor']=='Inflation')
        self.assertEqual(row['Veröffentlichungsstatus'],'Amtlich vorläufig')
        self.assertEqual(row['Datensatz'],record['observation']['source_title'])
        self.assertIn('dam-api.bfs.admin.ch',row['Quellenlink'])

    def test_quarterly_labour_age_still_expires_and_release_deadline_wins(self):
        row=live.build_record('Arbeitsmarkt',20,{'value':5.6,'date':'2026-06-30','frequency':'quarterly',
            'next_due_at':'2026-11-03T11:00:00+00:00'},'FRESH',NOW.isoformat())
        self.assertEqual(row['expires_at'],'2026-12-27T00:00:00+00:00')
        self.assertTrue(live.eligible(row,datetime(2026,10,1,tzinfo=timezone.utc))[0])
        self.assertFalse(live.eligible(row,datetime(2026,11,3,11,tzinfo=timezone.utc))[0])

    def test_block_reason_survives_missing_score(self):
        row = {'score': None, 'validation': 'UNVERIFIED', 'reason': 'PMI licence unresolved'}
        result = live.details('EUR', NOW, {'currencies': {'EUR': {'PMI': row}}})
        self.assertEqual(result['_blocking_reasons']['PMI'], 'PMI licence unresolved')
        st = MagicMock()
        with patch.object(live, 'load', return_value={'currencies': {'EUR': {'PMI': row}}}):
            live.render_status(st)
        block_rows = st.dataframe.call_args_list[2].args[0]
        eur = next(item for item in block_rows if item['Währung'] == 'EUR')
        self.assertIn('PMI: PMI licence unresolved', eur['Sperrgründe'])

    def test_read_checks_expected_factor_and_completed_run(self):
        row = self.record()
        data = {'completed_at': NOW.isoformat(), 'currencies': {'EUR': {'Geldpolitik': row}}}
        result = live.details('EUR', NOW, data)
        self.assertIsNone(result['Geldpolitik'])
        self.assertIn('Zuordnung', result['_blocking_reasons']['Geldpolitik'])
        self.assertTrue(result['_live_checked'])
        for timestamp in (None, 'bad', (NOW + timedelta(seconds=1)).isoformat()):
            data['completed_at'] = timestamp
            self.assertFalse(live.details('EUR', NOW, data)['_live_checked'])

    def test_collector_metadata_outage_retains_but_conflict_invalidates(self):
        previous = self.record()
        previous['factor'] = 'Arbeitsmarkt'
        previous['observation']['series_id'] = 'TEST_GDP'
        previous['observation']['needs_hourly_check'] = True
        previous['next_due_at'] = (NOW + timedelta(days=1)).isoformat()
        app = Mock()
        app.FRED_KEY = 'test-only'
        app.compute_currency_details.return_value = {
            'Arbeitsmarkt': 99, '_observations': {'Arbeitsmarkt': dict(previous['observation'])},
            '_freshness': {'Arbeitsmarkt': 'FRESH'}}
        app.get_verified_policy_rate.return_value = {'verification_timestamp': NOW.isoformat()}
        for outage in (True, False, "invalid_json"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'live.json'
                live.save({'model_version': live.MODEL, 'currencies': {'USD': {'Arbeitsmarkt': previous}}}, path)
                app.requests.get.side_effect = requests.RequestException('unavailable') if outage is True else None
                app.requests.get.return_value = Mock()
                if outage == "invalid_json":
                    app.requests.get.return_value.json.side_effect = requests.exceptions.JSONDecodeError("bad", "invalid", 0)
                with patch.object(live, 'now_utc', return_value=NOW + timedelta(minutes=30)), patch('source_contracts.validate_fred_metadata', return_value=False):
                    live.collect(app, path)
                result = live.load(path)['currencies']['USD']['Arbeitsmarkt']
                if outage is True:
                    self.assertEqual(result['checked_at'], previous['checked_at'])
                    self.assertEqual(result['score'], previous['score'])
                    self.assertEqual(result['last_error'], 'SOURCE_UNAVAILABLE')
                    self.assertTrue(live.eligible(result, NOW + timedelta(minutes=30))[0])
                    self.assertFalse(live.eligible(result, NOW + timedelta(hours=1))[0])
                    self.assertFalse(live.eligible(result, NOW + timedelta(days=1))[0])
                else:
                    self.assertEqual(result['validation'], 'UNVERIFIED')
                    self.assertFalse(live.eligible(result, NOW)[0])

    def test_runtime_path_is_stable_per_checkout_and_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(live.tempfile, 'gettempdir', return_value=tmp):
            with patch.object(live.Path, 'cwd', return_value=Path(tmp) / 'one'):
                first = live.runtime_directory()
                self.assertEqual(first, live.runtime_directory())
                self.assertFalse(first.exists())
            with patch.object(live.Path, 'cwd', return_value=Path(tmp) / 'two'):
                self.assertNotEqual(first, live.runtime_directory())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_ui_selects_newest_complete_model_without_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, runtime = Path(tmp) / 'base', Path(tmp) / 'runtime'
            base.mkdir(); runtime.mkdir()
            older = {'model_version': live.MODEL, 'completed_at': (NOW - timedelta(minutes=30)).isoformat(),
                     'currencies': {'EUR': {'GDP': self.record()}}}
            newer = {'model_version': live.MODEL, 'completed_at': NOW.isoformat(), 'currencies': {}}
            live.save(older, base / live.PATH); live.save(newer, runtime / live.PATH)
            with patch.object(live.Path, 'cwd', return_value=base), patch.object(live, 'runtime_directory', return_value=runtime), patch.object(live, 'now_utc', return_value=NOW), patch.dict(os.environ, {'FX_COLLECTOR': '0'}):
                self.assertEqual(live.selected_live_directory(), runtime)
                self.assertEqual(live.load(), newer)
                self.assertEqual(live.load(base / live.PATH), older)
                live.save(newer, base / live.PATH); live.save(older, runtime / live.PATH)
                self.assertEqual(live.selected_live_directory(), base)
                self.assertEqual(live.load(), newer)

    def test_runtime_invalid_future_or_other_model_is_never_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, runtime = Path(tmp) / 'base', Path(tmp) / 'runtime'
            base.mkdir(); runtime.mkdir()
            good = {'model_version': live.MODEL, 'completed_at': NOW.isoformat()}
            live.save(good, base / live.PATH)
            candidates = [None, [], {}, {'model_version': live.MODEL, 'completed_at': 'bad'},
                          dict(good, completed_at=(NOW + timedelta(seconds=1)).isoformat()),
                          dict(good, model_version='CORE_OTHER')]
            with patch.object(live.Path, 'cwd', return_value=base), patch.object(live, 'runtime_directory', return_value=runtime), patch.object(live, 'now_utc', return_value=NOW), patch.dict(os.environ, {'FX_COLLECTOR': '0'}):
                for bad in candidates:
                    import json
                    (runtime / live.PATH).write_text(json.dumps(bad))
                    self.assertEqual(live.selected_live_directory(), base)
                    self.assertEqual(live.load(), good)
                (runtime / live.PATH).write_text('{broken')
                self.assertEqual(live.load(), good)

    def test_collector_does_not_select_newer_ui_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, runtime = Path(tmp) / 'base', Path(tmp) / 'runtime'
            base.mkdir(); runtime.mkdir()
            live.save({'model_version': live.MODEL, 'completed_at': NOW.isoformat()}, runtime / live.PATH)
            with patch.object(live.Path, 'cwd', return_value=base), patch.object(live, 'runtime_directory', return_value=runtime), patch.dict(os.environ, {'FX_COLLECTOR': '1'}):
                self.assertEqual(live.selected_live_directory(), base)
                # An explicit path also remains completely independent of selection.
                self.assertEqual(live.load(base / live.PATH), {})

    def test_operator_budget_comes_from_selected_dataset_directory(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'data_collection_status.json').write_text(json.dumps({'providers': {'runtime.example': {'status': 'SUCCESS'}}}))
            st = MagicMock()
            with patch.object(live, 'selected_live_directory', return_value=path), patch.object(live, 'load', return_value={}):
                live.render_status(st, authorized=True)
            providers = st.dataframe.call_args_list[-1].args[0]
            self.assertEqual(providers[0]['Anbieter'], 'runtime.example')

    def test_invalid_previous_record_does_not_hide_current_failure_reason(self):
        for previous in ({'score': None, 'validation': 'VALID', 'reason': 'Old generic reason'},
                         {'score': 20, 'validation': 'UNVERIFIED', 'reason': 'Old generic reason'}):
            row = live.build_record('Geldpolitik', None, {}, 'UNAVAILABLE', NOW.isoformat(),
                                    previous=previous, reason='No verified 2Y yield')
            self.assertEqual(row['reason'], 'No verified 2Y yield')
            self.assertNotIn('last_error', row)
            self.assertFalse(live.eligible(row, NOW)[0])


class ReleaseAwareCollectionTests(unittest.TestCase):
    def setUp(self):
        from test_core_regressions import load_core
        from types import SimpleNamespace
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        self.core = load_core()
        self.core['datetime'] = Clock
        self.macro = Mock(return_value={'value': 3.0, 'date': '2026-06-30',
            'source': 'Cabinet Office ESRI', 'frequency': 'quarterly', 'freshness': 'FRESH'})
        self.core['get_macro_observation_details'] = self.macro
        self.other = {}
        for name in ('get_verified_policy_rate', 'get_genuine_2y_yield_historical',
                     'get_cpi_yoy_details', 'get_all_pmi_data', 'get_bci_value'):
            self.other[name] = Mock(side_effect=AssertionError('Unselected provider called: ' + name))
            self.core[name] = self.other[name]
        self.compute = Mock(wraps=self.core['compute_currency_details'])
        self.app = SimpleNamespace(compute_currency_details=self.compute, FRED_KEY=None,
                                   requests=Mock())

    def record(self, factor='GDP'):
        return live.build_record(factor, 20,
            {'value': 3.0, 'date': '2026-06-30' if factor == 'GDP' else '2026-07-31',
             'source': 'Cabinet Office ESRI', 'frequency': 'quarterly' if factor == 'GDP' else 'monthly',
             'next_due_at': (self.now + timedelta(days=1)).isoformat()}, 'FRESH',
            (self.now - timedelta(minutes=30)).isoformat())

    def collect_records(self, records, now=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live.json'
            live.save({'model_version': live.MODEL, 'currencies': {'JPY': records}}, path)
            with patch.object(live, 'CURRENCIES', ('JPY',)), \
                 patch.object(live, 'FACTORS', {factor: live.FACTORS[factor] for factor in records}), \
                 patch.object(live, 'now_utc', return_value=now or self.now):
                live.collect(self.app, path)
            result = live.load(path)['currencies']['JPY']
        self.app.requests.get.assert_not_called()
        return result

    def test_known_future_release_reuses_complete_record_without_calling_app(self):
        previous = self.record()
        previous['last_attempt_at'] = (self.now - timedelta(minutes=15)).isoformat()
        previous['last_error'] = 'SOURCE_UNAVAILABLE'
        result = self.collect_records({'GDP': previous})
        self.assertEqual(result['GDP'], previous)
        self.compute.assert_not_called()
        self.macro.assert_not_called()

    def test_mixed_plan_calls_only_due_factor_and_preserves_other_record(self):
        gdp, labour = self.record(), self.record('Arbeitsmarkt')
        labour['observation']['needs_hourly_check'] = True
        result = self.collect_records({'GDP': gdp, 'Arbeitsmarkt': labour})
        self.compute.assert_called_once_with('JPY', include_context=False, factors_to_refresh=('Arbeitsmarkt',))
        self.macro.assert_called_once_with('JPY', 'Arbeitsmarkt', '2026-09-08')
        self.assertEqual(result['GDP'], gdp)
        for provider in self.other.values():
            provider.assert_not_called()

    def test_at_release_deadline_or_hourly_check_required_the_factor_is_requested(self):
        for kind in ('due', 'hourly', 'unknown', 'expired'):
            with self.subTest(kind=kind):
                self.compute.reset_mock(); self.macro.reset_mock()
                previous = self.record()
                if kind == 'due': previous['next_due_at'] = self.now.isoformat()
                elif kind == 'hourly': previous['observation']['needs_hourly_check'] = True
                elif kind == 'unknown': previous['next_due_at'] = None
                else: previous['expires_at'] = self.now.isoformat()
                self.collect_records({'GDP': previous})
                self.compute.assert_called_once_with('JPY', include_context=False, factors_to_refresh=('GDP',))
                self.macro.assert_called_once_with('JPY', 'GDP', '2026-09-08')

    def test_known_source_conflict_or_new_release_never_skips_refresh(self):
        import source_contracts
        previous = self.record()
        conflict = {('JPY', 'GDP', '2026-06'): {'confirmed_at': self.now.isoformat(), 'reason': 'Conflicting official value'}}
        with patch.dict(source_contracts.KNOWN_SOURCE_CONFLICTS, conflict):
            row = self.collect_records({'GDP': previous})['GDP']
            self.assertFalse(live.eligible(row, self.now, factor='GDP', currency='JPY')[0])
        self.compute.assert_called_once()
        self.compute.reset_mock(); self.macro.reset_mock()
        release = {('JPY', 'GDP'): {'confirmed_at': self.now.isoformat(), 'period_start': '2026-07-01', 'label': '2026-Q3'}}
        with patch.dict(source_contracts.KNOWN_RELEASES, release):
            self.collect_records({'GDP': previous})
        self.compute.assert_called_once()

    def test_empty_selection_performs_no_core_or_context_provider_calls(self):
        self.core['compute_currency_details']('JPY', include_context=False, factors_to_refresh=())
        self.macro.assert_not_called()
        for provider in self.other.values():
            provider.assert_not_called()


class DirectMacroCollectorIntegrationTests(unittest.TestCase):
    """Actual app routing/scoring -> collector -> persisted read, offline adapters."""
    def setUp(self):
        import ast
        from test_core_regressions import load_core
        from types import SimpleNamespace
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        self.core = load_core()
        # load_core stubs this function for score unit tests; restore the real
        # route so these tests exercise error metadata passing end to end.
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name == 'get_macro_observation_details')
        exec(compile(ast.Module(body=[route], type_ignores=[]), '<macro-route>', 'exec'), self.core)
        self.macro_route = self.core['get_macro_observation_details']
        self.transport = Mock(exceptions=requests.exceptions)
        self.transport.get.side_effect = AssertionError('Direct macro route must not request FRED metadata')
        self.core.update(datetime=Clock, requests=self.transport)
        self.app = SimpleNamespace(FRED_KEY='test-only', requests=self.transport,
            compute_currency_details=self.core['compute_currency_details'])

    def collect_case(self, currency, factor, failure=None):
        # Exercise only the requested macro factor; compute_currency_details
        # also visits other factors, whose independent adapters are out of scope.
        self.core['get_macro_observation_details'] = lambda curr, category, target_date=None: (
            self.macro_route(curr, category, target_date) if category == factor
            else {'value': None, 'date': None, 'source': 'UNAVAILABLE', 'freshness': 'UNAVAILABLE'})
        fred = Mock(side_effect=AssertionError('Direct route must not fall back to FRED'))
        self.core['get_fred_data'] = fred
        previous_observation = {'value': 3.0, 'date': '2026-06-30' if factor == 'GDP' else '2026-08-31' if currency == 'CAD' else '2026-07-31',
            'frequency': 'quarterly' if factor == 'GDP' else 'monthly',
            'source': {'AUD': 'ABS', 'JPY': 'Cabinet Office ESRI' if factor == 'GDP' else 'Statistics Bureau of Japan',
                       'NZD': 'Stats NZ GDP expenditure', 'CAD': 'Statistics Canada', 'CHF': 'Eurostat', 'USD': 'BEA'}[currency],
            'series_id': 'official-direct', 'next_due_at': (self.now + timedelta(days=1)).isoformat(),
            # This test exercises an attempted refresh, independently of scheduling.
            'needs_hourly_check': True}
        previous = live.build_record(factor, 10, previous_observation, 'FRESH',
                                     (self.now - timedelta(minutes=30)).isoformat())
        def response(*args, **kwargs):
            if failure:
                raise failure
            observation = dict(previous_observation)
            observation['checked_at'] = self.now.isoformat()
            return observation
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live.json'
            live.save({'model_version': live.MODEL, 'currencies': {currency: {factor: previous}}}, path)
            with patch('official_macro.fetch_abs_observation', side_effect=response), \
                 patch('official_quarterly_labour.fetch_japan_labour', side_effect=response) as japan_labour, \
                 patch('official_macro.fetch_japan_gdp', side_effect=response) as japan_gdp, \
                 patch('official_macro.fetch_statcan_labour', side_effect=response) as statcan, \
                 patch('official_macro.fetch_statcan_gdp', side_effect=response) as statcan_gdp, \
                 patch('official_macro.fetch_bea_gdp', side_effect=response) as bea, \
                 patch('official_macro.fetch_nz_gdp', side_effect=response) as nz_gdp, \
                 patch('official_macro.fetch_eurostat_observation', side_effect=response) as eurostat, \
                 patch.object(live, 'now_utc', return_value=self.now), \
                 patch.object(live, 'CURRENCIES', (currency,)), \
                 patch.object(live, 'FACTORS', {factor: live.FACTORS[factor]}):
                live.collect(self.app, path)
                if currency == 'JPY':
                    if factor == 'GDP':
                        japan_gdp.assert_called_once_with(session=self.transport)
                        japan_labour.assert_not_called()
                    else:
                        japan_labour.assert_called_once_with(session=self.transport)
                        japan_gdp.assert_not_called()
                elif currency == 'CAD':
                    (statcan_gdp if factor == 'GDP' else statcan).assert_called_once_with(session=self.transport)
                    (statcan if factor == 'GDP' else statcan_gdp).assert_not_called()
                    eurostat.assert_not_called()
                elif currency == 'NZD':
                    nz_gdp.assert_called_once_with(session=self.transport)
                    eurostat.assert_not_called()
                elif currency == 'USD':
                    bea.assert_called_once_with(session=self.transport)
                    eurostat.assert_not_called()
                elif currency == 'CHF':
                    eurostat.assert_called_once_with('GDP', geo='CH', session=self.transport)
                    statcan.assert_not_called()
            result = live.load(path)['currencies'][currency][factor]
        self.transport.get.assert_not_called()
        fred.assert_not_called()
        return previous, result

    def test_parser_conflict_revokes_previous_valid_direct_observation(self):
        for currency, factor in (('AUD', 'GDP'), ('AUD', 'Arbeitsmarkt'), ('JPY', 'Arbeitsmarkt'),
                                 ('NZD', 'GDP'), ('CAD', 'Arbeitsmarkt'), ('CAD', 'GDP'), ('USD', 'GDP'), ('CHF', 'GDP'), ('JPY', 'GDP')):
            with self.subTest(currency=currency, factor=factor):
                previous, row = self.collect_case(currency, factor, ValueError('source contract conflict'))
                self.assertEqual(row['validation'], 'UNVERIFIED')
                self.assertIsNone(row['score'])
                self.assertNotIn('last_error', row)
                self.assertFalse(live.eligible(row, self.now, factor=factor, currency=currency)[0])
                self.assertNotIn('_validation', row['observation'])

    def test_transport_outage_preserves_only_original_release_and_age_limits(self):
        for currency, factor in (('AUD', 'GDP'), ('AUD', 'Arbeitsmarkt'), ('JPY', 'Arbeitsmarkt'),
                                 ('NZD', 'GDP'), ('CAD', 'Arbeitsmarkt'), ('CAD', 'GDP'), ('USD', 'GDP'), ('CHF', 'GDP'), ('JPY', 'GDP')):
            with self.subTest(currency=currency, factor=factor):
                previous, row = self.collect_case(currency, factor, requests.RequestException('offline'))
                for field in ('score', 'checked_at', 'expires_at', 'next_due_at'):
                    self.assertEqual(row[field], previous[field])
                self.assertEqual(row['last_error'], 'SOURCE_UNAVAILABLE')
                self.assertTrue(live.eligible(row, self.now, factor=factor, currency=currency)[0])
                expiry = self.now + timedelta(minutes=30)
                self.assertFalse(live.eligible(row, expiry, factor=factor, currency=currency)[0])

    def test_successful_direct_observation_is_validated_without_fred_metadata(self):
        for currency, factor in (('AUD', 'GDP'), ('AUD', 'Arbeitsmarkt'), ('JPY', 'Arbeitsmarkt'),
                                 ('NZD', 'GDP'), ('CAD', 'Arbeitsmarkt'), ('CAD', 'GDP'), ('USD', 'GDP'), ('CHF', 'GDP'), ('JPY', 'GDP')):
            with self.subTest(currency=currency, factor=factor):
                previous, row = self.collect_case(currency, factor)
                self.assertEqual(row['validation'], 'VALID')
                self.assertEqual(row['checked_at'], self.now.isoformat())
                self.assertNotEqual(row['score'], previous['score'])
                self.assertTrue(live.eligible(row, self.now, factor=factor, currency=currency)[0])
                self.assertNotIn('last_error', row)


class SwissHicpCollectorIntegrationTests(unittest.TestCase):
    """Real CHF inflation route and record persistence, with isolated adapters."""
    def setUp(self):
        import ast
        from types import SimpleNamespace
        from test_core_regressions import load_core
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        self.core = load_core()
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        routes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name in {'get_current_official_cpi', 'get_cpi_yoy_details'}]
        for route in routes:
            route.decorator_list = []
        exec(compile(ast.Module(body=routes, type_ignores=[]), '<swiss-hicp-route>', 'exec'), self.core)
        self.transport = Mock(exceptions=requests.exceptions)
        self.transport.get.side_effect = AssertionError('Direct HICP must not request fallback metadata')
        self.core.update(datetime=Clock, requests=self.transport)
        self.app = SimpleNamespace(FRED_KEY='test-only', requests=self.transport,
            compute_currency_details=self.core['compute_currency_details'])

    def collect_case(self, failure=None):
        observation = {'value': 0.9, 'date': '2026-08-31', 'source': 'BFS',
            'series_id': 'HICP CP00', 'frequency': 'monthly', 'unit': 'annual percent change',
            'seasonal_adjustment': 'NSA', 'reference_period': '2026-08',
            'needs_hourly_check': True}
        previous = live.build_record('Inflation', -55.0, observation, 'FRESH',
            (self.now - timedelta(minutes=30)).isoformat())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live.json'
            live.save({'model_version': live.MODEL,
                       'currencies': {'CHF': {'Inflation': previous}}}, path)
            with patch('official_hicp.fetch_swiss_hicp', return_value=observation,
                       side_effect=failure) as swiss, \
                 patch('official_hicp.fetch_hicp', side_effect=AssertionError('No Eurostat fallback')) as eurostat, \
                 patch.object(live, 'now_utc', return_value=self.now), \
                 patch.object(live, 'CURRENCIES', ('CHF',)), \
                 patch.object(live, 'FACTORS', {'Inflation': live.FACTORS['Inflation']}):
                live.collect(self.app, path)
                row = live.load(path)['currencies']['CHF']['Inflation']
                self.assertTrue(swiss.called)
                for call in swiss.call_args_list:
                    self.assertEqual(call.kwargs, {'session': self.transport})
                eurostat.assert_not_called()
        self.transport.get.assert_not_called()
        return previous, row

    def test_current_swiss_hicp_routes_to_bfs_without_eurostat_fallback(self):
        previous, row = self.collect_case()
        self.assertEqual(row['validation'], 'VALID')
        self.assertEqual(row['observation']['value'], 0.9)
        self.assertEqual(row['observation']['reference_period'], '2026-08')
        self.assertAlmostEqual(row['score'], -55.0)
        self.assertEqual(row['checked_at'], self.now.isoformat())
        self.assertTrue(live.eligible(row, self.now, factor='Inflation', currency='CHF')[0])

    def test_swiss_hicp_parser_conflict_revokes_previous_valid_record(self):
        previous, row = self.collect_case(ValueError('HICP_SCHEMA_CONFLICT'))
        self.assertEqual(row['validation'], 'UNVERIFIED')
        self.assertIsNone(row['score'])
        self.assertNotIn('last_error', row)
        self.assertFalse(live.eligible(row, self.now, factor='Inflation', currency='CHF')[0])

    def test_swiss_hicp_transport_failure_keeps_original_freshness_deadlines(self):
        previous, row = self.collect_case(requests.RequestException('offline'))
        for field in ('score', 'observation', 'checked_at', 'next_due_at', 'expires_at'):
            self.assertEqual(row[field], previous[field])
        self.assertEqual(row['last_error'], 'SOURCE_UNAVAILABLE')
        self.assertTrue(live.eligible(row, self.now, factor='Inflation', currency='CHF')[0])
        self.assertFalse(live.eligible(row, self.now + timedelta(minutes=30),
                                     factor='Inflation', currency='CHF')[0])


class TreasuryCollectorIntegrationTests(unittest.TestCase):
    """Real current-date routing and scoring through the persisted live gate."""
    def setUp(self):
        import ast
        from types import SimpleNamespace
        from test_core_regressions import load_core
        self.now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)
        self.core = load_core()
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == 'get_genuine_2y_yield_historical')
        exec(compile(ast.Module(body=[route], type_ignores=[]), '<treasury-route>', 'exec'), self.core)
        self.transport = Mock(exceptions=requests.exceptions)
        self.transport.get.side_effect = AssertionError('Treasury must not need FRED metadata')
        self.fred = Mock(side_effect=AssertionError('Current Treasury must not fall back to FRED'))
        policy = lambda curr: {'rate': 4.0, 'verification_status': 'VERIFIED',
                               'verification_timestamp': now.isoformat()}
        self.core.update(datetime=Clock, requests=self.transport,
                         get_verified_policy_rate=policy, get_fred_data_historical=self.fred)
        self.app = SimpleNamespace(FRED_KEY='test-only', requests=self.transport,
            get_verified_policy_rate=policy, policy_rate_is_usable=self.core['policy_rate_is_usable'], compute_currency_details=self.core['compute_currency_details'])

    def collect_case(self, failure=None):
        previous = live.build_record('Geldpolitik', 10,
            {'policy_rate': 4.0, 'yield_2y': 4.0, 'date': '2026-09-04',
             'source': 'US Treasury nominal 2Y constant maturity'}, 'FRESH',
            (self.now - timedelta(minutes=30)).isoformat())
        observation = {'value': 4.37, 'observation_date': '2026-09-04',
                       'source': 'US Treasury nominal 2Y constant maturity'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live.json'
            live.save({'model_version': live.MODEL,
                       'currencies': {'USD': {'Geldpolitik': previous}}}, path)
            with patch('official_yields.fetch_treasury_2y', return_value=observation,
                       side_effect=failure) as treasury, \
                 patch.object(live, 'now_utc', return_value=self.now), \
                 patch.object(live, 'CURRENCIES', ('USD',)), \
                 patch.object(live, 'FACTORS', {'Geldpolitik': live.FACTORS['Geldpolitik']}):
                live.collect(self.app, path)
                row = live.load(path)['currencies']['USD']['Geldpolitik']
                treasury.assert_called_once_with('2026-09-08', client=self.transport)
        self.fred.assert_not_called()
        self.transport.get.assert_not_called()
        return previous, row

    def test_current_treasury_value_routes_scores_and_validates_without_fred(self):
        previous, row = self.collect_case()
        self.assertEqual(row['validation'], 'VALID')
        self.assertEqual(row['observation']['yield_2y'], 4.37)
        self.assertAlmostEqual(row['score'], ((4.0 - 3) / 3 * 100 + (4.37 - 3) / 3 * 100) / 2)
        self.assertEqual(row['observation']['series_id'], 'BC_2YEAR (FRED equivalent DGS2)')
        self.assertIn('home.treasury.gov', row['observation']['source_url'])
        self.assertEqual(row['checked_at'], self.now.isoformat())
        self.assertTrue(live.eligible(row, self.now, factor='Geldpolitik', currency='USD')[0])

    def test_treasury_transport_failure_preserves_only_original_deadlines(self):
        previous, row = self.collect_case(requests.RequestException('offline'))
        for field in ('score', 'checked_at', 'expires_at', 'next_due_at', 'observation'):
            self.assertEqual(row[field], previous[field])
        self.assertEqual(row['last_error'], 'SOURCE_UNAVAILABLE')
        self.assertTrue(live.eligible(row, self.now, factor='Geldpolitik', currency='USD')[0])
        self.assertFalse(live.eligible(row, self.now + timedelta(minutes=30),
                                     factor='Geldpolitik', currency='USD')[0])

    def test_treasury_parser_conflict_revokes_previous_valid_value(self):
        previous, row = self.collect_case(ValueError('TREASURY_SERIES_INVALID'))
        self.assertEqual(row['validation'], 'UNVERIFIED')
        self.assertIsNone(row['score'])
        self.assertNotIn('last_error', row)
        self.assertFalse(live.eligible(row, self.now, factor='Geldpolitik', currency='USD')[0])

    def test_historical_usd_keeps_existing_fred_route(self):
        self.fred.side_effect = None
        self.fred.return_value = (3.5, '2026-08-31', False)
        with patch('official_yields.fetch_treasury_2y') as treasury:
            result = self.core['get_genuine_2y_yield_historical']('USD', '2026-08-31', 'test-only')
        self.assertEqual(result, (3.5, '2026-08-31', 'FRED'))
        self.fred.assert_called_once_with('DGS2', '2026-08-31', 'test-only')
        treasury.assert_not_called()


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


class PolicyDeadlineCollectorTests(unittest.TestCase):
    """Full collector persistence with real proof validation and read-time gate."""
    def setUp(self):
        from test_policy_regressions import PolicyRegressions
        self.fixture = PolicyRegressions()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.ecb_pending_fixture()
        self.p = self.fixture.p
        self.now = datetime(2026, 9, 15, 21, 59, tzinfo=timezone.utc)
        self.p['_policy_now'] = lambda: self.now
        self.policy = self.fixture.valid('EUR', 2.25)
        self.policy.update(verification_evidence=self.p['fetch_official_policy_rate_live']('EUR')['evidence'],
            rate_effective_date='2026-06-17', last_policy_decision_date='2026-09-10', verified_at=self.now.isoformat())

    def collect_policy(self, existing_due=None):
        from types import SimpleNamespace
        observation = {'policy_rate': 2.25, 'yield_2y': 2.0, 'date': '2026-09-15', 'source': 'ECB'}
        if existing_due:
            observation['next_due_at'] = existing_due
        app = SimpleNamespace(get_verified_policy_rate=lambda c: self.policy,
            policy_rate_is_usable=self.p['policy_rate_is_usable'], FRED_KEY=None,
            compute_currency_details=lambda *a, **k: {'Geldpolitik': 20,
                '_observations': {'Geldpolitik': observation}, '_freshness': {'Geldpolitik': 'FRESH'}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'live.json'
            with patch.object(live, 'CURRENCIES', ('EUR',)), \
                 patch.object(live, 'FACTORS', {'Geldpolitik': live.FACTORS['Geldpolitik']}), \
                 patch.object(live, 'now_utc', return_value=self.now):
                live.collect(app, path)
            return live.load(path)['currencies']['EUR']['Geldpolitik']

    def test_projects_earliest_deadline_and_blocks_on_read_without_new_collector(self):
        row = self.collect_policy('2026-09-16T12:00:00+00:00')
        self.assertEqual(row['next_due_at'], '2026-09-15T22:00:00+00:00')
        self.assertTrue(row['observation']['needs_hourly_check'])
        self.assertTrue(live.eligible(row, self.now, factor='Geldpolitik', currency='EUR')[0])
        self.assertFalse(live.eligible(row, datetime(2026, 9, 15, 22, tzinfo=timezone.utc), factor='Geldpolitik', currency='EUR')[0])
        earlier = self.collect_policy('2026-09-15T21:59:30+00:00')
        self.assertEqual(earlier['next_due_at'], '2026-09-15T21:59:30+00:00')

    def test_invalid_missing_or_expired_proofs_cannot_publish_valid_factor(self):
        from copy import deepcopy
        original = deepcopy(self.policy)
        for mutation in ('invalid_deadline', 'one_proof', 'expired', 'future_rate', 'null_proofs', 'bad_proof'):
            self.policy = deepcopy(original)
            if mutation == 'invalid_deadline':
                self.policy['verification_evidence'][1]['valid_until'] = 'bad'
            elif mutation == 'one_proof':
                self.policy['verification_evidence'].pop()
            elif mutation == 'expired':
                self.policy['verification_evidence'][1]['valid_until'] = self.now.isoformat()
            elif mutation == 'null_proofs':
                self.policy['verification_evidence'] = None
            elif mutation == 'bad_proof':
                self.policy['verification_evidence'][1] = 'malformed'
            else:
                self.policy['verification_evidence'][1]['rate'] = 2.5
            with self.subTest(mutation=mutation):
                row = self.collect_policy()
                self.assertEqual(row['validation'], 'UNVERIFIED')
                self.assertFalse(live.eligible(row, self.now, factor='Geldpolitik', currency='EUR')[0])
