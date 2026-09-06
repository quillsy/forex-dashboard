"""Offline regression tests. No app import, provider requests, or real data writes."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent


def load_eodhd_functions():
    names = {
        'EODHD_EVENTS_CACHE_FILE', 'EODHD_BONDS_CACHE_FILE', 'EODHD_STATUS_FILE',
        'EODHD_DAILY_LIMIT', 'EODHD_2Y_TICKERS', 'EODHD_5Y_TICKERS', '_EODHD_STATUS_INFO',
        'load_eodhd_events_cache', 'save_eodhd_events_cache', 'get_eodhd_batched_economic_events',
        'get_eodhd_pmi_fallback', 'get_eodhd_pmi_historical', '_atomic_eodhd_json',
        '_eodhd_utc_now', 'load_eodhd_status', 'save_eodhd_status', 'get_eodhd_status_label',
        'load_eodhd_bonds_cache', 'save_eodhd_bonds_cache', '_parse_eodhd_bond_entry',
        'get_eodhd_bond_data', '_eodhd_daily_status', '_summarize_eodhd_status',
        'prefetch_eodhd_production_data', 'get_eodhd_bond_historical'
    }
    nodes = []
    for node in ast.parse((ROOT / 'app.py').read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            nodes.append(node)
    namespace = dict(os=os, json=json, datetime=datetime, timedelta=timedelta, pd=pd, np=np,
                     requests=types.SimpleNamespace(get=Mock()), EODHD_KEY='test-only')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), namespace)
    return namespace


class EodhdRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.ns = load_eodhd_functions()
        self.now = datetime(2026, 9, 6, 22, tzinfo=timezone.utc)
        self.ns['_eodhd_utc_now'] = lambda: self.now
        self.network = self.ns['requests'].get
        self.network.side_effect = self.response

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    def response(self, url, **kwargs):
        ledger = self.ns['load_eodhd_status']()
        self.assertEqual(ledger['calls'], self.network.call_count)
        self.assertEqual(sum(v['requested'] for v in ledger['attempts'].values()), ledger['calls'])
        if url.endswith('economic-events'):
            data = [{'date': '2026-09-04', 'country': 'EMU', 'name': 'Manufacturing PMI', 'actual': 51, 'previous': 50}]
        else:
            data = [{'date': '2026-09-04', 'close': 2.5}]
        return types.SimpleNamespace(status_code=200, json=lambda: data)

    def run_prefetch(self):
        return self.ns['prefetch_eodhd_production_data']('test-only')

    def test_reserves_14_in_priority_order_and_rerun_spends_zero(self):
        result = self.run_prefetch()
        self.assertEqual((result['calls'], result['p1_2y_calls'], result['p2_pmi_calls'], result['p3_5y_calls']), (14, 7, 1, 6))
        self.assertEqual(result['collection_status'], 'SUCCESS')
        urls = [call.args[0] for call in self.network.call_args_list]
        self.assertTrue(urls[7].endswith('economic-events'))
        self.assertFalse(any('SW5Y' in url for url in urls))
        self.run_prefetch()
        self.assertEqual(self.network.call_count, 14)

    def test_failures_are_counted_and_never_report_green(self):
        self.network.side_effect = TimeoutError('private api URL must not be retained')
        result = self.run_prefetch()
        self.assertEqual(result['calls'], 14)
        self.assertEqual(result['collection_status'], 'FAILED')
        self.assertNotIn('private', json.dumps(result))
        self.run_prefetch()
        self.assertEqual(self.network.call_count, 14)

    def test_http_limit_stops_all_lower_priorities(self):
        self.network.side_effect = lambda *a, **k: types.SimpleNamespace(status_code=402)
        result = self.run_prefetch()
        self.assertEqual(result['calls'], 1)
        self.assertIn('LIMIT EXHAUSTED', result['status'])
        self.run_prefetch()
        self.assertEqual(self.network.call_count, 1)

    def test_shared_persisted_budget_never_exceeds_20(self):
        self.ns['save_eodhd_status']({'day_utc': '2026-09-06', 'calls': 19, 'attempts': {}})
        self.network.side_effect = lambda *a, **k: types.SimpleNamespace(status_code=500)
        result = self.run_prefetch()
        self.assertEqual(result['calls'], 20)
        self.assertEqual(self.network.call_count, 1)
        self.assertTrue(result['budget_exhausted'])

    def test_truncated_global_events_report_partial(self):
        normal = self.response
        def response(url, **kwargs):
            result = normal(url, **kwargs)
            if url.endswith('economic-events'):
                result.json = lambda: [{'date': '2026-09-04', 'country': 'EMU', 'name': 'Manufacturing PMI', 'actual': 51}] * 1000
            return result
        self.network.side_effect = response
        result = self.run_prefetch()
        self.assertEqual(result['collection_status'], 'PARTIAL')
        self.assertFalse(result['attempts']['economic-events']['coverage_complete'])
        self.assertEqual(result['calls'], 14)

    def test_zero_network_readers_and_cached_values_without_key(self):
        self.ns['save_eodhd_bonds_cache']('DE2Y.GBOND', [{'date': '2026-09-04', 'value': 2.5}])
        self.ns['save_eodhd_events_cache']([{'date': '2026-09-04', 'country': 'EMU', 'name': 'Manufacturing PMI', 'actual': 51}])
        self.assertIsNotNone(self.ns['get_eodhd_bond_data']('DE2Y.GBOND', None))
        self.assertIsNone(self.ns['get_eodhd_bond_data']('MISSING', 'test-only'))
        self.assertEqual(len(self.ns['get_eodhd_batched_economic_events']('test-only')), 1)
        self.network.assert_not_called()

    def test_euro_area_has_no_country_proxy_and_same_day_timestamp_counts(self):
        self.ns['save_eodhd_events_cache']([
            {'date': '2026-09-05', 'country': 'DEU', 'name': 'Manufacturing PMI', 'actual': 55},
            {'date': '2026-09-04 10:00:00', 'country': 'EMU', 'name': 'Manufacturing PMI', 'actual': 51}
        ])
        current = self.ns['get_eodhd_pmi_fallback']('EUR', 'Manufacturing PMI', 'test-only')
        historical = self.ns['get_eodhd_pmi_historical']('EUR', 'Manufacturing PMI', '2026-09-04', 'test-only')
        self.assertEqual(current['last'], 51)
        self.assertEqual(historical['last'], 51)

    def test_empty_responses_and_missing_key_are_not_available(self):
        self.network.side_effect = lambda *a, **k: types.SimpleNamespace(status_code=200, json=lambda: [])
        result = self.run_prefetch()
        self.assertEqual(result['collection_status'], 'FAILED')
        self.now += timedelta(days=1)
        result = self.ns['prefetch_eodhd_production_data'](None)
        self.assertEqual(result['calls'], 0)
        self.assertEqual(result['collection_status'], 'FAILED')

    def test_stale_success_response_is_not_green(self):
        self.network.side_effect = lambda *a, **k: types.SimpleNamespace(status_code=200, json=lambda: [{"date": "2020-01-01", "close": 2.5}])
        result = self.run_prefetch()
        self.assertEqual(result['collection_status'], 'FAILED')
        self.assertEqual(result['attempts']['DE2Y.GBOND']['result'], 'STALE_DATA')

    def test_utc_rollover_keeps_new_day_reservations_for_reruns(self):
        def response(url, **kwargs):
            if self.network.call_count == 1:
                self.now += timedelta(days=1)
            data = [{'date': '2026-09-04', 'close': 2.5}]
            if url.endswith('economic-events'):
                data = [{'date': '2026-09-04', 'country': 'EMU', 'name': 'Manufacturing PMI', 'actual': 51}]
            return types.SimpleNamespace(status_code=200, json=lambda: data)
        self.network.side_effect = response
        first = self.run_prefetch()
        self.assertEqual(first['day_utc'], '2026-09-07')
        self.assertEqual(first['calls'], 13)
        second = self.run_prefetch()
        self.assertEqual(second['calls'], 14)
        self.assertEqual(self.network.call_count, 15)

    def test_parallel_prefetches_share_lock_and_attempts(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.run_prefetch())) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(results), 2)
        self.assertEqual(self.network.call_count, 14)
        self.assertEqual(self.ns['load_eodhd_status']()['calls'], 14)


class CollectorRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('collector', ROOT / 'run_data_collection.py')
        cls.collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.collector)

    def test_import_guard_is_enabled_during_import_and_restored(self):
        fake_st = types.SimpleNamespace(session_state={}, _mock_mode=False)
        with patch.dict('sys.modules', {'streamlit': fake_st}):
            def importing(name):
                self.assertEqual(name, 'app')
                self.assertTrue(fake_st._mock_mode)
                self.assertFalse(fake_st.session_state['demo_mode_chk'])
                return 'app-object'
            with patch.object(self.collector.importlib, 'import_module', side_effect=importing):
                self.assertEqual(self.collector.import_collection_app(), 'app-object')
        self.assertFalse(fake_st._mock_mode)

    def test_partial_component_cannot_be_reported_success(self):
        fake_app = types.SimpleNamespace(
            FRED_KEY='test-only',
            refresh_all_verified_policy_rates=lambda **kwargs: {str(i): {'verification_status': '🟢 VERIFIED'} for i in range(8)},
            prefetch_eodhd_production_data=lambda: {'status': '🟡 EODHD DATA PARTIAL', 'collection_status': 'PARTIAL'},
            save_all_g10_live_snapshots=lambda: {'status': 'SUCCESS', 'written': 8},
            update_open_outcomes=lambda: {'status': 'FAILED', 'errors': 1}
        )
        overall, results = self.collector.collect(fake_app)
        self.assertEqual(overall, 'PARTIAL')
        self.assertEqual(results['outcomes']['errors'], 1)


if __name__ == '__main__':
    unittest.main()
