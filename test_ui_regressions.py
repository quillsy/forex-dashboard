"""Render the real Streamlit app offline, in a disposable copy of its data."""
import ast
import copy
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import requests
import numpy as np
from streamlit.testing.v1 import AppTest


class UIRegressions(unittest.TestCase):
    def test_all_tabs_and_invalid_pair_render_without_sources_or_history_writes(self):
        root = Path(__file__).resolve().parent
        original_history = (root / 'live_signals.json').read_bytes()
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='fx-ui-test-') as temporary:
            for file in root.iterdir():
                # Keep local app imports available even in an isolated test run.
                if file.is_file() and file.suffix in {'.py', '.json'}:
                    shutil.copy2(file, Path(temporary, file.name))
            try:
                os.chdir(temporary)
                with patch.dict(os.environ, {'DASHBOARD_OPERATOR_PASSWORD': 'fixture-operator-password-2026',
                                             'FRED_API_KEY': 'fixture-provider-key-not-a-credential'}), \
                     patch('requests.sessions.Session.request', side_effect=requests.ConnectionError('Offline UI test')), \
                     patch('urllib.request.urlopen', side_effect=OSError('Offline UI test')):
                    app = AppTest.from_file(str(Path(temporary, 'app.py')), default_timeout=45).run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertEqual(len(app.tabs), 22)  # 13 main tabs and 9 sub-tabs.
                    self.assertGreater(len(app.dataframe), 10)
                    pair_metrics = [metric for metric in app.metric if metric.label == 'BASE CORE Score']
                    self.assertEqual(len(pair_metrics), 2)
                    self.assertTrue(all(metric.delta == '' for metric in pair_metrics))
                    core_only_labels = [element.value for element in app.markdown
                                        if element.value.startswith('- Score (nur CORE; Context nicht geprüft):')]
                    self.assertEqual(len(core_only_labels), 2)
                    history = next(e.value for e in app.dataframe
                                   if 'Einzelstatus (gespeichert)' in e.value.columns)
                    self.assertIn('CORE-Abdeckung (gespeichert)', history.columns)
                    self.assertIn('Collectorabschluss (gespeichert)', history.columns)
                    self.assertIn('Freigabeprüfung (gespeichert)', history.columns)
                    self.assertIn('Modellversion', history.columns)
                    self.assertIn('Sperrhinweise (gespeichert)', history.columns)
                    bond_table = next(e.value for e in app.dataframe if 'Trend (2Y)' in e.value.columns)
                    missing_trends = bond_table.loc[bond_table['2Y Rendite'] == 'N/A', 'Trend (2Y)']
                    self.assertGreater(len(missing_trends), 0)
                    self.assertEqual(set(missing_trends), {'N/A'})
                    self.assertEqual(Path('live_signals.json').read_bytes(), original_history)
                    self.assertNotIn('🔑 API-Konfiguration', [e.label for e in app.sidebar.get('expander')])
                    self.assertNotIn('📝 Streamlit Secrets Anleitung', [e.label for e in app.sidebar.get('expander')])
                    self.assertFalse(any('API / Datenquelle' in e.value.columns for e in app.dataframe))
                    app.sidebar.text_input[0].set_value('incorrect-operator-password-2026').run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertNotIn('🔑 API-Konfiguration', [e.label for e in app.sidebar.get('expander')])
                    self.assertNotIn('📝 Streamlit Secrets Anleitung', [e.label for e in app.sidebar.get('expander')])
                    self.assertFalse(any('API / Datenquelle' in e.value.columns for e in app.dataframe))
                    app.sidebar.text_input[0].set_value('fixture-operator-password-2026').run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertIn('🔑 API-Konfiguration', [e.label for e in app.sidebar.get('expander')])
                    self.assertIn('📝 Streamlit Secrets Anleitung', [e.label for e in app.sidebar.get('expander')])
                    self.assertEqual(sum('API / Datenquelle' in e.value.columns for e in app.dataframe), 1)
                    configuration = '\n'.join(element.value for element in app.sidebar.markdown)
                    self.assertIn('FRED_API_KEY: Konfiguriert; Verbindung ungeprüft', configuration)
                    self.assertNotIn('fixture-provider-key-not-a-credential', configuration)
                    self.assertEqual(Path('live_signals.json').read_bytes(), original_history)
                    app.sidebar.text_input[0].set_value('').run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertNotIn('🔑 API-Konfiguration', [e.label for e in app.sidebar.get('expander')])
                    self.assertNotIn('📝 Streamlit Secrets Anleitung', [e.label for e in app.sidebar.get('expander')])
                    self.assertFalse(any('API / Datenquelle' in e.value.columns for e in app.dataframe))
                    Path('.policy_rates_cache.json').write_text('{}')
                    app.sidebar.selectbox[1].set_value('USD').run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertEqual(Path('live_signals.json').read_bytes(), original_history)
            finally:
                os.chdir(original_cwd)
        self.assertEqual((root / 'live_signals.json').read_bytes(), original_history)


class CurrencyHistoryDisplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).with_name('app.py')
        names = {'finite_number', 'format_score', 'currency_snapshot_history_row'}
        functions = [node for node in ast.parse(source.read_text()).body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in functions} == names
        namespace = {'np': np}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
        cls.render = staticmethod(namespace['currency_snapshot_history_row'])

    def test_partial_snapshot_shows_saved_quality_and_provenance_without_mutation(self):
        snapshot = {'currency': 'USD', 'date': '2026-10-12', 'core_score': 0,
                    'core_status': 'VALID', 'data_quality': 80, 'missing_factors': ['PMI'],
                    'model_version': 'CORE_V2_8_2026_09',
                    'collector_completed_at': '2026-10-12T22:37:32+00:00',
                    'eligibility_checked_at': '2026-10-12T22:37:34+00:00',
                    'factor_scores': {'Inflation': 0, 'PMI': None},
                    'observations': {'PMI': {'_display_status': 'Gesperrt: Freigabe fehlt'}}}
        original = copy.deepcopy(snapshot)
        row = self.render('CURR_USD', snapshot)
        self.assertEqual(row['BASE CORE'], '+0.0')
        self.assertEqual(row['CORE-Abdeckung (gespeichert)'], '80%')
        self.assertEqual(row['Einzelstatus (gespeichert)'], 'VALID')
        self.assertEqual(row['Fehlende Faktoren'], 'PMI')
        self.assertIn('Gesperrt: Freigabe fehlt', row['Sperrhinweise (gespeichert)'])
        self.assertEqual(row['Inflation (20%)'], '+0.0')
        self.assertEqual(row['PMI (20%)'], 'N/A')
        self.assertEqual(row['Collectorabschluss (gespeichert)'], snapshot['collector_completed_at'])
        self.assertEqual(row['Freigabeprüfung (gespeichert)'], snapshot['eligibility_checked_at'])
        self.assertEqual(snapshot, original)

    def test_legacy_unknown_metadata_is_not_complete_or_valid(self):
        row = self.render('old', {'metadata': {'date': '2020-01-01', 'currency': 'EUR'},
                                  'score': 12.5, 'factors': {'GDP': -2}})
        self.assertEqual(row['Währung'], 'EUR')
        self.assertEqual(row['BASE CORE'], '+12.5')
        self.assertEqual(row['GDP (5%)'], '-2.0')
        self.assertEqual(row['Geldpolitik (35%)'], 'N/A')
        for field in ('CORE-Abdeckung (gespeichert)', 'Einzelstatus (gespeichert)',
                      'Fehlende Faktoren', 'Sperrhinweise (gespeichert)', 'Modellversion',
                      'Collectorabschluss (gespeichert)', 'Freigabeprüfung (gespeichert)'):
            self.assertEqual(row[field], 'Unbekannt', field)

    def test_explicit_missing_core_never_uses_other_score(self):
        row = self.render('partial', {'core_score': None, 'score': 99, 'total_score': 100,
                                      'data_quality': 0, 'missing_factors': []})
        self.assertEqual(row['BASE CORE'], 'N/A')
        self.assertEqual(row['CORE-Abdeckung (gespeichert)'], '0%')
        self.assertEqual(row['Fehlende Faktoren'], 'Keine laut Aufzeichnung')
        unknown = self.render('legacy', {'missing_factors': []})
        self.assertEqual(unknown['Fehlende Faktoren'], 'Keine laut Aufzeichnung')
        self.assertEqual(unknown['CORE-Abdeckung (gespeichert)'], 'Unbekannt')
        self.assertEqual(unknown['Einzelstatus (gespeichert)'], 'Unbekannt')

    def test_invalid_quality_and_factor_values_stay_unknown_or_missing(self):
        for invalid in (True, False, float('nan'), float('inf'), -1, 101, 'bad', {}, []):
            with self.subTest(value=invalid):
                row = self.render('bad', {'data_quality': invalid,
                                         'factor_scores': {'PMI': invalid}})
                self.assertEqual(row['CORE-Abdeckung (gespeichert)'], 'Unbekannt')
                if not isinstance(invalid, (int, float)) or isinstance(invalid, bool) or not np.isfinite(invalid):
                    self.assertEqual(row['PMI (20%)'], 'N/A')

    def test_malformed_nested_metadata_does_not_claim_no_missing_factors(self):
        for invalid in (None, 'PMI', {}, ['PMI', None], ['']):
            with self.subTest(value=invalid):
                row = self.render('bad', {'metadata': [], 'factors': [], 'factor_scores': 'bad',
                                         'missing_factors': invalid, 'observations': []})
                self.assertEqual(row['Fehlende Faktoren'], 'Unbekannt')
                self.assertEqual(row['Sperrhinweise (gespeichert)'], 'Unbekannt')
                self.assertEqual(row['PMI (20%)'], 'N/A')


if __name__ == '__main__':
    unittest.main()
