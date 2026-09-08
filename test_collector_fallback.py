import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import subprocess

import run_data_collection as collector
import live_data

class FallbackTests(unittest.TestCase):
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
