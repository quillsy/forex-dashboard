import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess

import run_data_collection as collector
import live_data
from provider_transport import CollectorTransport
import requests

class FallbackTests(unittest.TestCase):
    def resolver(self, secrets=None):
        import ast
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in {'load_api_key', 'fcs_credential_status'}]
        st = Mock(); st.secrets = secrets or {}
        ns = {'os': os, 'st': st, 'FCS_KEY': 'test-only'}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<keys>', 'exec'), ns)
        return ns

    def test_unrotated_ui_credentials_block_env_and_streamlit_aliases(self):
        from provider_transport import _CREDENTIAL_HOSTS
        for alias in _CREDENTIAL_HOSTS:
            for streamlit in (False, True):
                with self.subTest(alias=alias, streamlit=streamlit), patch.dict(os.environ, {}, clear=True):
                    ns = self.resolver({alias.lower(): 'test-only'} if streamlit else {})
                    if not streamlit:
                        os.environ[alias] = 'test-only'
                    self.assertIsNone(ns['load_api_key'](alias))
                    self.assertIsNone(ns['load_api_key']('ALTERNATE', [alias]))

    def test_rotation_ack_is_normalized_and_does_not_unlock_other_providers(self):
        with patch.dict(os.environ, {'FX_ROTATED_PROVIDER_HOSTS': ' API-V4.FCSAPI.COM , ',
                                    'FCS_API_KEY': 'test-only', 'AV_API_KEY': 'blocked'}, clear=True):
            ns = self.resolver()
            self.assertEqual(ns['load_api_key']('FCS_API_KEY'), 'test-only')
            self.assertIsNone(ns['load_api_key']('ALPHA_VANTAGE_API_KEY', ['AV_API_KEY']))
            self.assertEqual(ns['fcs_credential_status'](), 'Schlüssel vorhanden (Verbindung ungeprüft)')
        with patch.dict(os.environ, {'FX_ROTATED_PROVIDER_HOSTS': ' STOCKDATA.ORG '}, clear=True):
            ns = self.resolver({'stockdata_token': 'test-only'})
            self.assertEqual(ns['load_api_key']('STOCKDATA_API_KEY', ['STOCKDATA_TOKEN']), 'test-only')

    def test_rotation_guard_preserves_unrelated_keys_and_fallback_allowlist(self):
        with patch.dict(os.environ, {}, clear=True):
            ns = self.resolver({'fred_api_key': 'test-fred', 'estat_app_id': 'test-estat'})
            self.assertEqual(ns['load_api_key']('FRED_API_KEY'), 'test-fred')
            self.assertEqual(ns['load_api_key']('ESTAT_APP_ID'), 'test-estat')
            self.assertIn('Gesperrt', ns['fcs_credential_status']())
        with patch.dict(os.environ, {'FX_FALLBACK_MODE': '1', 'FX_ROTATED_PROVIDER_HOSTS': 'fcsapi.com',
                                    'FCS_API_KEY': 'test-only', 'FRED_API_KEY': 'test-fred'}, clear=True):
            ns = self.resolver()
            self.assertIsNone(ns['load_api_key']('FCS_API_KEY'))
            self.assertEqual(ns['load_api_key']('FRED_API_KEY'), 'test-fred')

    def test_seed_preserves_local_counts_and_longest_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'remote.json';destination=Path(tmp)/'local.json'
            source.write_text(json.dumps({'providers':{'example.org':{'requests_observed_utc_day':900,
                'retry_after_at':'2026-09-08T13:00:00+00:00'},'new.org':{'requests_observed_utc_day':500,
                'retry_after_at':'2026-09-08T16:00:00+00:00'}}}))
            destination.write_text(json.dumps({'providers':{'example.org':{'requests_observed_utc_day':12,
                'retry_after_at':'2026-09-08T15:00:00+00:00'}}}))
            collector._seed_fallback_status(source,destination)
            providers=json.loads(destination.read_text())['providers']
            self.assertEqual(providers['example.org']['requests_observed_utc_day'],12)
            self.assertEqual(providers['example.org']['retry_after_at'],'2026-09-08T15:00:00+00:00')
            self.assertNotIn('requests_observed_utc_day',providers['new.org'])
            self.assertEqual(providers['new.org']['retry_after_at'],'2026-09-08T16:00:00+00:00')

    def test_seed_preserves_newer_local_bls_budgets_without_mixing_observations(self):
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / 'repo.json', Path(tmp) / 'runtime.json'
            remote = {'model_version': live_data.MODEL, 'completed_at': (at + timedelta(minutes=5)).isoformat(),
                      'currencies': {'USD': {'Inflation': {'checked_at': 'repo-check', 'score': 70}}},
                      'bls_release_states': {'Inflation': {'proof': 'repo-proof'}},
                      'bls_pdf_attempts': {'Inflation': (at - timedelta(minutes=50)).isoformat()},
                      'bls_api_attempts': {}}
            local = {'model_version': live_data.MODEL, 'completed_at': at.isoformat(),
                     'currencies': {'USD': {'Inflation': {'checked_at': 'local-check', 'score': 10}}},
                     'bls_release_states': {'Inflation': {'proof': 'local-proof'}},
                     'bls_pdf_attempts': {'Inflation': at.isoformat()},
                     'bls_api_reservations': {'Inflation': at.isoformat()}}
            source.write_text(json.dumps(remote)); destination.write_text(json.dumps(local))
            collector._seed_fallback_live(source, destination)
            seeded = json.loads(destination.read_text())
            for field in ('completed_at', 'currencies', 'bls_release_states'):
                self.assertEqual(seeded[field], remote[field])
            self.assertEqual(seeded['bls_pdf_attempts']['Inflation'], at.isoformat())
            self.assertEqual(seeded['bls_api_reservations']['Inflation'], at.isoformat())
            self.assertEqual(json.loads(source.read_text()), remote)
            later = at + timedelta(minutes=36)
            self.assertEqual(live_data._bls_budget_marker(seeded, 'pdf', 'Inflation', later), (at, False))
            self.assertEqual(live_data._bls_budget_marker(seeded, 'api', 'Inflation', later), (at, False))

    def test_seed_cannot_turn_corrupt_or_future_local_bls_marks_into_fresh_budget(self):
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        remote = {'model_version': live_data.MODEL, 'completed_at': at.isoformat(),
                  'currencies': {'USD': {'GDP': {'score': 0, 'checked_at': 'kept'}}}}
        corruptions = ["not json", json.dumps({'bls_api_attempts': {'Inflation': 'bad'}}),
                       json.dumps({'bls_pdf_reservations': None}),
                       json.dumps({'bls_pdf_attempts': {'Inflation': (at + timedelta(days=1)).isoformat()}})]
        with tempfile.TemporaryDirectory() as tmp:
            source, destination = Path(tmp) / 'repo.json', Path(tmp) / 'runtime.json'
            source.write_text(json.dumps(remote))
            for index, local in enumerate(corruptions):
                with self.subTest(index=index):
                    destination.write_text(local)
                    collector._seed_fallback_live(source, destination)
                    seeded = json.loads(destination.read_text())
                    self.assertEqual(seeded['currencies'], remote['currencies'])
                    kind = 'api' if index == 1 else 'pdf'
                    self.assertTrue(live_data._bls_budget_marker(seeded, kind, 'Inflation', at)[1])

    def test_main_reload_does_not_double_count_immediately_saved_requests(self):
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            status_path = directory / 'data_collection_status.json'
            status_path.write_text(json.dumps({'providers': {'official.example': {
                'counted_day_utc': at.date().isoformat(), 'requests_observed_utc_day': 5}}}))
            response = requests.Response(); response.status_code = 200; response._content = b'{}'
            client = Mock(get=Mock(return_value=response))
            transport = CollectorTransport(client, status_path=status_path, clock=lambda: at)
            app = Mock(requests=transport, FRED_KEY='test-only')
            def policy(fred_key):
                transport.get('https://official.example/data?key=test-only')
                return {currency: {'verification_status': '🟢'} for currency in live_data.CURRENCIES}
            app.refresh_all_verified_policy_rates.side_effect = policy
            before = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['collector', '--live-only']), \
                     patch.object(collector, 'datetime', wraps=datetime) as clock, \
                     patch.object(collector, 'import_collection_app', return_value=app), \
                     patch.object(live_data, 'collect', return_value={'status': 'PARTIAL'}):
                    clock.now.return_value = at
                    self.assertEqual(collector.main(), 0)
            finally:
                os.chdir(before)
            saved = json.loads(status_path.read_text())
            self.assertEqual(saved['providers']['official.example']['requests_observed_utc_day'], 6)
            self.assertEqual(saved['providers']['official.example']['requests_reserved_utc_day'], 6)
            self.assertEqual(saved['providers']['official.example']['requests_uncertain_utc_day'], 0)
            self.assertEqual(saved['providers']['official.example']['requests_this_run'], 1)
            self.assertEqual(saved['total_partial_runs'], 1)
            client.get.assert_called_once()

    def test_main_abort_before_final_save_retains_confirmed_retry_after(self):
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); status_path = directory / 'data_collection_status.json'
            status_path.write_text(json.dumps({'last_run_timestamp': 'old', 'last_run_status': 'SUCCESS'}))
            response = requests.Response(); response.status_code = 429
            response.headers['Retry-After'] = '7200'; response._content = b'private-response'
            client = Mock(get=Mock(return_value=response))
            transport = CollectorTransport(client, status_path=status_path, clock=lambda: at)
            app = Mock(requests=transport, FRED_KEY='test-only')
            def policy(fred_key):
                transport.get('https://official.example/data?key=test-only')
                return {}
            app.refresh_all_verified_policy_rates.side_effect = policy
            before = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['collector', '--live-only']), \
                     patch.object(collector, 'datetime', wraps=datetime) as clock, \
                     patch.object(collector, 'import_collection_app', return_value=app), \
                     patch.object(live_data, 'collect', side_effect=KeyboardInterrupt()):
                    clock.now.return_value = at
                    with self.assertRaises(KeyboardInterrupt):
                        collector.main()
            finally:
                os.chdir(before)
            saved = json.loads(status_path.read_text())
            self.assertEqual(saved['last_run_timestamp'], 'old')
            self.assertEqual(saved['last_run_status'], 'SUCCESS')
            self.assertEqual(saved['providers']['official.example']['retry_after_at'],
                             (at + timedelta(hours=2)).isoformat())
            client.get.reset_mock()
            restarted = CollectorTransport(client, status_path=status_path,
                                           clock=lambda: at + timedelta(minutes=31), durable=True)
            with patch.dict(os.environ, {'FX_COLLECTOR': '1'}):
                with self.assertRaisesRegex(requests.RequestException, 'PROVIDER_COOLDOWN'):
                    restarted.get('https://official.example/other')
            client.get.assert_not_called()
            self.assertNotIn('test-only', status_path.read_text())

    def test_corrupt_local_status_is_not_replaced_by_seed_or_two_main_runs(self):
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        for raw in ('invalid json', '{"providers":null}'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp); path = directory / 'data_collection_status.json'
                path.write_text(raw)
                source = directory / 'repo-status.json'
                source.write_text(json.dumps({'providers': {'official.example': {
                    'retry_after_at': (at + timedelta(hours=2)).isoformat()}}}))
                with self.assertRaisesRegex(ValueError, 'INVALID_PROVIDER_STATUS'):
                    collector._seed_fallback_status(source, path)
                self.assertEqual(path.read_text(), raw)
                client = Mock()
                before = Path.cwd()
                try:
                    os.chdir(directory)
                    for _ in range(2):
                        transport = CollectorTransport(client, status_path=path, clock=lambda: at)
                        app = Mock(requests=transport, FRED_KEY='test-only')
                        app.refresh_all_verified_policy_rates.side_effect = (
                            lambda fred_key: transport.get('https://official.example/data'))
                        with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['collector', '--live-only']), \
                             patch.object(collector, 'datetime', wraps=datetime) as clock, \
                             patch.object(collector, 'import_collection_app', return_value=app), \
                             patch.object(live_data, 'collect', return_value={'status': 'FAILED'}):
                            clock.now.return_value = at
                            self.assertEqual(collector.main(), 1)
                        self.assertEqual(path.read_text(), raw)
                        self.assertTrue(transport.persistence_failed)
                    client.get.assert_not_called()
                finally:
                    os.chdir(before)

    def test_fallback_invalid_provider_seed_does_not_launch_child_or_replace_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'runtime'; directory.mkdir()
            source = Path(tmp) / 'repo'; source.mkdir()
            status = directory / 'data_collection_status.json'; status.write_text('invalid json')
            with patch.object(live_data, 'runtime_directory', return_value=directory), \
                 patch.object(live_data, 'selected_live_directory', return_value=source), \
                 patch.object(live_data, 'load', return_value={}), patch('subprocess.run') as process:
                self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY': 'test-only'}), 'unavailable')
            process.assert_not_called()
            self.assertEqual(status.read_text(), 'invalid json')

    def test_running_manual_collector_blocks_seed_and_child_with_the_same_writer_lock(self):
        import fcntl
        at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'runtime'; directory.mkdir()
            source = Path(tmp) / 'repo'; source.mkdir()
            status_path = directory / 'data_collection_status.json'
            live_path = directory / 'live_core_data.json'
            live_data.save({'model_version': live_data.MODEL, 'completed_at': at.isoformat(),
                            'currencies': {}, 'bls_api_reservations': {'Arbeitsmarkt': at.isoformat()}}, live_path)
            live_data.save({'model_version': live_data.MODEL,
                            'completed_at': (at + timedelta(minutes=5)).isoformat(), 'currencies': {}}, source / live_path.name)
            (source / status_path.name).write_text('{"providers":{}}')
            response = requests.Response(); response.status_code = 429
            response.headers['Retry-After'] = '7200'; response._content = b'private-response'
            client = Mock(get=Mock(return_value=response))
            with (directory / '.data_collection.lock').open('a') as writer:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                transport = CollectorTransport(client, status_path=status_path, clock=lambda: at, durable=True)
                with patch.dict(os.environ, {'FX_COLLECTOR': '1'}):
                    transport.get('https://official.example/data')
                before_status, before_live = status_path.read_bytes(), live_path.read_bytes()
                with patch.dict(os.environ, {'FX_COLLECTOR': '0'}), \
                     patch.object(live_data, 'runtime_directory', return_value=directory), \
                     patch.object(live_data, 'selected_live_directory', return_value=source), \
                     patch.object(live_data, 'load', return_value={}), \
                     patch.object(collector, '_seed_fallback_status') as seed, patch('subprocess.run') as process:
                    self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY': 'test-only'}), 'running')
                seed.assert_not_called(); process.assert_not_called()
                self.assertEqual(status_path.read_bytes(), before_status)
                self.assertEqual(live_path.read_bytes(), before_live)
            client.get.reset_mock()
            restarted = CollectorTransport(client, status_path=status_path,
                                           clock=lambda: at + timedelta(minutes=31), durable=True)
            with patch.dict(os.environ, {'FX_COLLECTOR': '1'}):
                with self.assertRaisesRegex(requests.RequestException, 'PROVIDER_COOLDOWN'):
                    restarted.get('https://official.example/after')
            client.get.assert_not_called()

    def test_main_unsupported_transport_fails_without_provider_io_or_budget_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); path = directory / 'data_collection_status.json'
            providers = {'official.example': {'requests_observed_utc_day': 7,
                          'counted_day_utc': '2026-09-27', 'retry_after_at': '2026-10-01T00:00:00Z'}}
            path.write_text(json.dumps({'providers': providers}))
            app = Mock(requests=requests)
            before = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['collector', '--live-only']), \
                     patch.object(collector, 'import_collection_app', return_value=app), \
                     patch.object(live_data, 'collect') as live_collect, patch.object(requests, 'get') as http_get:
                    self.assertEqual(collector.main(), 1)
                app.refresh_all_verified_policy_rates.assert_not_called()
                live_collect.assert_not_called(); http_get.assert_not_called()
            finally:
                os.chdir(before)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['last_run_status'], 'FAILED')
            self.assertEqual(saved['providers'], providers)

    def test_main_midnight_aggregates_current_day_for_requested_and_unrequested_hosts(self):
        started = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc)
        finished = started + timedelta(minutes=2)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); path = directory / 'data_collection_status.json'
            previous = {'counted_day_utc': started.date().isoformat(), 'requests_observed_utc_day': 3,
                        'requests_reserved_utc_day': 4, 'requests_uncertain_utc_day': 1,
                        'usage_complete': False, 'last_attempt_at': started.isoformat()}
            path.write_text(json.dumps({'providers': {'sleeping.example': previous}}))
            response = requests.Response(); response.status_code = 200; response._content = b'{}'
            client = Mock(get=Mock(return_value=response)); current = [started]
            transport = CollectorTransport(client, status_path=path, clock=lambda: current[0])
            app = Mock(requests=transport, FRED_KEY='test-only')
            def policy(fred_key):
                transport.get('https://early.example/data')
                transport.get('https://both.example/before')
                return {currency: {'verification_status': '🟢'} for currency in live_data.CURRENCIES}
            def live(_app):
                current[0] = finished
                transport.get('https://both.example/after')
                return {'status': 'PARTIAL'}
            app.refresh_all_verified_policy_rates.side_effect = policy
            before = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['collector', '--live-only']), \
                     patch.object(collector, 'datetime', wraps=datetime) as clock, \
                     patch.object(collector, 'import_collection_app', return_value=app), \
                     patch.object(live_data, 'collect', side_effect=live):
                    clock.now.side_effect = [started, finished]
                    self.assertEqual(collector.main(), 0)
            finally:
                os.chdir(before)
            saved = json.loads(path.read_text())['providers']
            for host in saved:
                self.assertEqual(saved[host]['counted_day_utc'], finished.date().isoformat())
                expected = 1 if host == 'both.example' else 0
                self.assertEqual(saved[host]['requests_observed_utc_day'], expected)
                self.assertEqual(saved[host]['requests_reserved_utc_day'], expected)
                self.assertEqual(saved[host]['requests_uncertain_utc_day'], 0)
            self.assertEqual(saved['both.example']['requests_this_run'], 2)
            self.assertEqual(saved['early.example']['requests_this_run'], 1)
            self.assertTrue(saved['sleeping.example']['prior_usage_uncertain'])
            self.assertFalse(saved['sleeping.example']['usage_complete'])
            self.assertTrue(saved['early.example']['usage_complete'])

    def test_fresh_data_never_starts_process(self):
        with patch.object(live_data,'load',return_value={'completed_at':datetime.now(timezone.utc).isoformat()}), patch('subprocess.run') as run:
            self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY':'test'}),'fresh')
            run.assert_not_called()

    def test_no_key_no_start(self):
        with patch.object(live_data,'load',return_value={}), patch('subprocess.run') as run:
            self.assertEqual(collector.maybe_start_live_fallback({}),'unavailable')
            run.assert_not_called()

    def test_shared_lock_and_persistent_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source';source.mkdir(); directory=root/'runtime'
            (source/'live_core_data.json').write_text('{}')
            (source/'.env').write_text('PRIVATE=test-only')
            started=threading.Event();release=threading.Event(); finished=threading.Event()
            def run(*args,**kwargs):
                started.set(); release.wait(5); return Mock(returncode=0)
            original=collector._fallback_state
            def save(path,state):
                original(path,state)
                if state.get('state')=='completed':finished.set()
            with patch.object(live_data,'runtime_directory',return_value=directory),patch.object(live_data,'selected_live_directory',return_value=source),patch.object(live_data,'load',return_value={}),patch('subprocess.run',side_effect=run) as process,patch.object(collector,'_fallback_state',side_effect=save):
                with patch.dict(os.environ,{'EODHD_API_KEY':'never-inherit','DASHBOARD_OPERATOR_PASSWORD':'never-inherit'}):
                    self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY':'test-only','DASHBOARD_OPERATOR_PASSWORD':'never-pass'}),'started')
                self.assertTrue(started.wait(2))
                self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY':'test-only'}),'running')
                self.assertNotEqual(os.environ.get('FX_COLLECTOR'),'1')
                release.set();self.assertTrue(finished.wait(2))
                # State survives a new invocation and prevents a duplicate.
                result=collector.maybe_start_live_fallback({'FRED_API_KEY':'test-only'})
                self.assertIn(result,('running','cooldown'))
                self.assertEqual(process.call_count,1)
                args,kwargs=process.call_args
                self.assertEqual(args[0][-1],'--live-only')
                self.assertEqual(args[0][1],'-B')
                self.assertEqual(kwargs['cwd'],str(directory))
                self.assertEqual(kwargs['timeout'],600)
                self.assertEqual(kwargs['stdout'],subprocess.DEVNULL)
                self.assertEqual(kwargs['stderr'],subprocess.DEVNULL)
                self.assertEqual(kwargs['env']['FRED_API_KEY'],'test-only')
                self.assertEqual(kwargs['env']['FX_FALLBACK_MODE'],'1')
                self.assertNotIn('EODHD_API_KEY',kwargs['env'])
                self.assertNotIn('DASHBOARD_OPERATOR_PASSWORD',kwargs['env'])
                self.assertNotIn('test-only',' '.join(args[0]))
                self.assertFalse((directory/'.env').exists())
                self.assertNotIn('test-only',(directory/'.launch-state.json').read_text())

    def test_timeout_preserves_dataset_and_marks_incomplete_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); completed=threading.Event()
            original=collector._fallback_state
            def save(path,state):
                original(path,state)
                if state.get('state')=='timeout':completed.set()
            with patch.object(live_data,'runtime_directory',return_value=directory),patch.object(live_data,'selected_live_directory',return_value=directory),patch.object(live_data,'load',return_value={}),patch('subprocess.run',side_effect=subprocess.TimeoutExpired('collector',600)),patch.object(collector,'_fallback_state',side_effect=save):
                self.assertEqual(collector.maybe_start_live_fallback({'FRED_API_KEY':'test-only'}),'started')
                self.assertTrue(completed.wait(2))
                state=json.loads((directory/'.launch-state.json').read_text())
                self.assertFalse(state['usage_complete'])
                self.assertFalse((directory/'live_core_data.json').exists())

    def test_fallback_key_resolution_cannot_read_other_secrets(self):
        import ast
        tree=ast.parse(Path(__file__).with_name('app.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_api_key')
        st=Mock();ns={'os':os,'st':st}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<key-resolver>','exec'),ns)
        with patch.dict(os.environ,{'FX_FALLBACK_MODE':'1','FRED_API_KEY':'test-only','EODHD_API_KEY':'must-not-read'}):
            self.assertEqual(ns['load_api_key']('FRED_API_KEY'),'test-only')
            self.assertIsNone(ns['load_api_key']('EODHD_API_KEY'))
            st.secrets.get.assert_not_called()

if __name__=='__main__':unittest.main()
