"""Render the real Streamlit app offline, in a disposable copy of its data."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest


class UIRegressions(unittest.TestCase):
    def test_all_tabs_and_invalid_pair_render_without_sources_or_history_writes(self):
        root = Path(__file__).resolve().parent
        original_history = (root / 'live_signals.json').read_bytes()
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory(prefix='fx-ui-test-') as temporary:
            for file in root.iterdir():
                if file.name == 'app.py' or file.suffix == '.json':
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


if __name__ == '__main__':
    unittest.main()
