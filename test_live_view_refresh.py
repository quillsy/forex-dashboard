"""Time-dependent views must refresh without a user click or a provider fetch."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

class LiveViewRefreshTests(unittest.TestCase):
    def test_idle_view_triggers_full_rerun_at_deadline_only(self):
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'refresh_live_view_if_due')
        st = Mock()
        ns = {'st': st}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<refresh>', 'exec'), ns)
        refresh = ns['refresh_live_view_if_due']
        refresh(100, now=100)
        refresh(100, now=129.99)
        st.rerun.assert_not_called()
        refresh(100, now=130)
        st.rerun.assert_called_once_with()

    def test_fragment_has_timer_and_invokes_revalidation(self):
        tree = ast.parse(Path(__file__).with_name('app.py').read_text())
        fragment = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                        and n.name == 'revalidate_open_live_view')
        self.assertEqual(ast.literal_eval(fragment.decorator_list[0].keywords[0].value), 30)
        ns = {'refresh_live_view_if_due': Mock()}
        fragment.decorator_list = []
        exec(compile(ast.Module(body=[fragment], type_ignores=[]), '<fragment>', 'exec'), ns)
        ns['revalidate_open_live_view'](100)
        ns['refresh_live_view_if_due'].assert_called_once_with(100)

if __name__ == '__main__':
    unittest.main()
