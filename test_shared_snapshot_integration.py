"""One render pins CORE, policy and status without provider I/O."""
import ast
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import live_data
import run_data_collection
from test_completed_live_batch import readers, batch, NOW


def app_setup(st):
    tree = ast.parse(Path(__file__).with_name('app.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'prepare_live_snapshot')
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'POLICY_RATES_CACHE_FILE' for t in n.targets))
    ns = {'os': os, 'st': st, 'live_data': live_data}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<render-setup>', 'exec'), ns)
    return ns, assignment


class SnapshotIntegrationTests(unittest.TestCase):
    def tearDown(self):
        live_data.clear_render_directory()

    def test_promotion_mid_render_cannot_mix_core_policy_or_operator_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, old, new = [Path(tmp) / name for name in ('checkout', 'old', 'new')]
            for path in (base, old, new):
                path.mkdir()
            before = {'model_version': live_data.MODEL, 'completed_at': NOW.isoformat(), 'currencies': {}}
            after = dict(before, completed_at=(NOW + timedelta(minutes=1)).isoformat())
            live_data.save(before, old / live_data.PATH)
            live_data.save(after, new / live_data.PATH)
            (old / '.policy_rates_cache.json').write_text('{"batch":"old"}')
            (old / 'data_collection_status.json').write_text('{"providers":{"old.example":{"status":"SUCCESS"}}}')
            (new / '.policy_rates_cache.json').write_text('{"batch":"new"}')
            (new / 'data_collection_status.json').write_text('{"providers":{"new.example":{"status":"SUCCESS"}}}')
            ns, policy_assignment = app_setup(Mock(_mock_mode=False))
            with patch.dict(os.environ, {'FX_COLLECTOR': '0'}), \
                 patch.object(live_data.Path, 'cwd', return_value=base), \
                 patch.object(live_data, 'now_utc', return_value=NOW + timedelta(minutes=2)), \
                 patch('shared_snapshot.current_shared_directory', return_value=old) as pointer, \
                 patch('shared_snapshot.refresh_shared_snapshot', return_value={'outcome': 'UPDATED'}) as refresh:
                self.assertEqual(ns['prepare_live_snapshot'](), {'outcome': 'UPDATED'})
                refresh.assert_called_once_with()
                pointer.return_value = new
                exec(compile(ast.Module(body=[policy_assignment], type_ignores=[]), '<policy-path>', 'exec'), ns)
                self.assertEqual(json.loads(Path(ns['POLICY_RATES_CACHE_FILE']).read_text())['batch'], 'old')
                self.assertEqual(live_data.load(), before)
                st = MagicMock()
                live_data.render_status(st, authorized=True)
                self.assertEqual(st.dataframe.call_args_list[-1].args[0][0]['Anbieter'], 'old.example')
                # The next render may adopt the new generation, never mid-render.
                ns['prepare_live_snapshot']()
                self.assertEqual(live_data.load(), after)

    def test_ui_preparation_cannot_collect_provider_data_in_two_instances(self):
        ns, _ = app_setup(Mock(_mock_mode=False))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'FX_COLLECTOR': '0'}), \
             patch('subprocess.run') as child, patch('requests.Session') as http, \
             patch('shared_snapshot.refresh_shared_snapshot', return_value={'outcome': 'UNAVAILABLE'}) as refresh:
            for name in ('one', 'two'):
                directory = Path(tmp) / name
                directory.mkdir()
                with patch.object(live_data, 'runtime_directory', return_value=directory):
                    ns['prepare_live_snapshot']()
                    self.assertEqual(run_data_collection.maybe_start_live_fallback({'FRED_API_KEY': 'test-only'}), 'disabled')
            self.assertEqual(refresh.call_count, 2)
            child.assert_not_called(); http.assert_not_called()

    def test_collector_and_mock_imports_do_not_poll_or_change_render_pin(self):
        for collecting, mock in (('1', False), ('0', True)):
            ns, _ = app_setup(Mock(_mock_mode=mock))
            with patch.dict(os.environ, {'FX_COLLECTOR': collecting}), \
                 patch('shared_snapshot.refresh_shared_snapshot') as refresh, \
                 patch.object(live_data, 'pin_render_directory') as pin:
                self.assertEqual(ns['prepare_live_snapshot'](), {'outcome': 'DISABLED'})
                refresh.assert_not_called(); pin.assert_not_called()

    def test_ui_policy_refresh_and_direct_save_cannot_mutate_a_shared_generation(self):
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in {'refresh_all_verified_policy_rates', 'save_policy_rates_cache'}]
        cached = {'USD': {'rate': 3.5}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.policy_rates_cache.json'
            path.write_text(json.dumps(cached))
            before = path.read_bytes()
            reader = Mock(return_value=cached)
            provider = Mock(side_effect=AssertionError('provider called from UI'))
            ns = {'os': os, 'get_all_verified_policy_rates': reader,
                  'fetch_official_policy_rate_live': provider, 'POLICY_RATES_CACHE_FILE': str(path)}
            exec(compile(ast.Module(body=functions, type_ignores=[]), '<policy-write-guard>', 'exec'), ns)
            with patch.dict(os.environ, {'FX_COLLECTOR': '0'}):
                self.assertEqual(ns['refresh_all_verified_policy_rates']('test-only'), cached)
                with self.assertRaisesRegex(RuntimeError, 'POLICY_CACHE_COLLECTOR_ONLY'):
                    ns['save_policy_rates_cache']({'USD': {'rate': 99}})
            provider.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            # The actual sidebar button only rerenders; it cannot call a provider.
            source = Path(__file__).with_name('app.py').read_text()
            self.assertNotIn('checked_rates = refresh_all_verified_policy_rates', source)

    def test_legacy_flat_runtime_provider_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, runtime = Path(tmp) / 'checkout', Path(tmp) / 'runtime'
            base.mkdir(); runtime.mkdir()
            data = {'model_version': live_data.MODEL, 'completed_at': NOW.isoformat()}
            live_data.save(data, runtime / live_data.PATH)
            with patch.dict(os.environ, {'FX_COLLECTOR': '0'}), \
                 patch.object(live_data.Path, 'cwd', return_value=base), \
                 patch.object(live_data, 'runtime_directory', return_value=runtime):
                self.assertEqual(live_data.selected_live_directory(), runtime / 'unavailable-snapshot')
                self.assertEqual(live_data.load(), {})

    def test_copy_or_poll_does_not_extend_hourly_deadline_or_pair_gate(self):
        data = batch(NOW.isoformat())
        # The canonical reader sees the same immutable snapshot at different clocks.
        data['currencies']['CHF']['GDP']['observation']['needs_hourly_check'] = True
        ns = readers()
        ns['use_live_core_cache'] = lambda *args: True
        original = copy.deepcopy(data)
        with patch.object(live_data, 'load', return_value=data):
            with patch.object(live_data, 'now_utc', return_value=NOW):
                self.assertIsNotNone(live_data.details('CHF')['GDP'])
                self.assertEqual(ns['get_pair_signal_and_badge']('CHF', 'USD')[3], 'INSUFFICIENT DATA')
            with patch.object(live_data, 'now_utc', return_value=NOW + timedelta(hours=1, seconds=1)):
                self.assertIsNone(live_data.details('CHF')['GDP'])
                self.assertEqual(ns['get_pair_signal_and_badge']('CHF', 'USD')[3], 'INSUFFICIENT DATA')
        self.assertEqual(data, original)


if __name__ == '__main__':
    unittest.main()
