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
                with patch('requests.sessions.Session.request', side_effect=requests.ConnectionError('Offline UI test')), \
                     patch('urllib.request.urlopen', side_effect=OSError('Offline UI test')):
                    app = AppTest.from_file(str(Path(temporary, 'app.py')), default_timeout=45).run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertEqual(len(app.tabs), 22)  # 13 main tabs and 9 sub-tabs.
                    self.assertGreater(len(app.dataframe), 10)
                    self.assertEqual(Path('live_signals.json').read_bytes(), original_history)
                    Path('.policy_rates_cache.json').write_text('{}')
                    app.sidebar.selectbox[1].set_value('USD').run()
                    self.assertEqual(len(app.exception), 0, [e.message for e in app.exception])
                    self.assertEqual(Path('live_signals.json').read_bytes(), original_history)
            finally:
                os.chdir(original_cwd)
        self.assertEqual((root / 'live_signals.json').read_bytes(), original_history)


if __name__ == '__main__':
    unittest.main()
