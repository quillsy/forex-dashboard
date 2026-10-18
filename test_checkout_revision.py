import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import live_data as live


HEAD = "a" * 40


class CheckoutRevisionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve() / "app"
        self.root.mkdir()
        self.marker = self.root / ".git"
        self.marker.write_text("gitdir: ../separate-git-directory\n")
        self.module_file = self.root / "live_data.py"
        self.module_file.write_text("# fixture\n")
        module = patch.object(live, "__file__", str(self.module_file))
        module.start()
        self.addCleanup(module.stop)

    def responses(self, diff_code=0, last_head=HEAD):
        return [subprocess.CompletedProcess([], 0, str(self.root) + "\n", ""),
                subprocess.CompletedProcess([], 0, HEAD + "\n", ""),
                subprocess.CompletedProcess([], diff_code, "", "internal stderr"),
                subprocess.CompletedProcess([], 0, last_head + "\n", "")]

    def test_worktree_marker_and_detached_commit_are_supported(self):
        with patch.object(live.subprocess, "run", side_effect=self.responses()) as run:
            self.assertEqual(live.checkout_revision(),
                             {"head": HEAD, "tracked_sources_match": True})
        self.assertEqual(run.call_count, 4)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["cwd"], self.root)
            self.assertFalse(call.kwargs["shell"])
            self.assertEqual(call.kwargs["timeout"], 2)
            self.assertTrue(call.kwargs["capture_output"])
            self.assertIn("core.fsmonitor=false", call.args[0])
        self.assertEqual(run.call_args_list[2].args[0][-8:],
                         ["diff", "--quiet", "--no-ext-diff", "--no-textconv", HEAD,
                          "--", "*.py", "requirements.txt"])

    def test_git_directory_marker_is_supported(self):
        self.marker.unlink()
        self.marker.mkdir()
        with patch.object(live.subprocess, "run", side_effect=self.responses()):
            self.assertTrue(live.checkout_revision()["tracked_sources_match"])

    def test_absent_own_marker_never_uses_parent_repository(self):
        self.marker.unlink()
        (self.root.parent / ".git").mkdir()
        with patch.object(live.subprocess, "run") as run:
            self.assertEqual(live.checkout_revision(),
                             {"head": None, "tracked_sources_match": None})
        run.assert_not_called()

    def test_wrong_repository_root_is_unknown(self):
        for top in (str(self.root.parent), "", "relative"):
            responses = self.responses()
            responses[0].stdout = top + "\n"
            with self.subTest(top=top), \
                 patch.object(live.subprocess, "run", side_effect=responses) as run:
                self.assertIsNone(live.checkout_revision()["head"])
            self.assertEqual(run.call_count, 1)

    def test_git_environment_overrides_are_removed_and_cwd_is_module_root(self):
        overrides = {"GIT_DIR": "/unrelated", "GIT_WORK_TREE": "/unrelated",
                     "GIT_INDEX_FILE": "/unrelated/index", "GIT_CONFIG_COUNT": "1",
                     "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "unwanted",
                     "GIT_EXTERNAL_DIFF": "unwanted", "GIT_NAMESPACE": "unrelated"}
        with patch.dict(os.environ, overrides), \
             patch.object(live, "selected_live_directory", side_effect=AssertionError("not source")), \
             patch.object(live.subprocess, "run", side_effect=self.responses()) as run:
            self.assertEqual(live.checkout_revision()["head"], HEAD)
        for call in run.call_args_list:
            env = call.kwargs["env"]
            self.assertTrue(all(key not in env for key in overrides))
            self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(call.kwargs["cwd"], self.root)

    def test_missing_git_timeout_or_decoding_failure_is_unknown(self):
        for failure in (FileNotFoundError("internal path"),
                        subprocess.TimeoutExpired("git", 2, stderr="secret"),
                        UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid")):
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(live.subprocess, "run", side_effect=failure):
                self.assertEqual(live.checkout_revision(),
                                 {"head": None, "tracked_sources_match": None})

    def test_invalid_or_noncommit_head_is_unknown(self):
        for head, code in (("", 0), ("a" * 39, 0), ("a" * 41, 0),
                           ("z" * 40, 0), (HEAD + "\nextra", 0), (HEAD, 128)):
            responses = self.responses()
            responses[1] = subprocess.CompletedProcess([], code, head + "\n", "sensitive")
            with self.subTest(head=head, code=code), \
                 patch.object(live.subprocess, "run", side_effect=responses):
                self.assertIsNone(live.checkout_revision()["head"])

    def test_diff_codes_distinguish_changes_and_unknown(self):
        for code, expected in ((0, True), (1, False), (128, None)):
            with self.subTest(code=code), \
                 patch.object(live.subprocess, "run", side_effect=self.responses(code)):
                self.assertEqual(live.checkout_revision(),
                                 {"head": HEAD, "tracked_sources_match": expected})

    def test_head_switch_or_final_read_failure_discards_mapping(self):
        for final_head, code in (("b" * 40, 0), (HEAD, 128)):
            responses = self.responses(last_head=final_head)
            responses[-1].returncode = code
            with self.subTest(final_head=final_head, code=code), \
                 patch.object(live.subprocess, "run", side_effect=responses):
                self.assertEqual(live.checkout_revision(),
                                 {"head": None, "tracked_sources_match": None})

    def test_untracked_source_does_not_mean_full_source_cleanliness(self):
        (self.root / "untracked.py").write_text("# outside tracked-file check\n")
        with patch.object(live.subprocess, "run", side_effect=self.responses()):
            self.assertTrue(live.checkout_revision()["tracked_sources_match"])
        self.assertTrue((self.root / "untracked.py").exists())

    def test_render_diagnostic_is_separate_from_factors_and_redacts_errors(self):
        data = {"currencies": {}}
        displays = []
        for revision in ({"head": HEAD, "tracked_sources_match": True},
                         {"head": HEAD, "tracked_sources_match": False},
                         {"head": None, "tracked_sources_match": None}):
            ui = MagicMock()
            with patch.object(live, "load", return_value=data), \
                 patch.object(live, "checkout_revision", return_value=revision):
                live.render_status(ui)
            displays.append(ui.dataframe.call_args_list)
            captions = "\n".join(call.args[0] for call in ui.caption.call_args_list)
            self.assertIn("kein Nachweis des bereits geladenen Prozesscodes", captions)
            self.assertIn("ändert keine Daten- oder Signal-Freigabe", captions)
            self.assertNotIn(str(self.root), captions)
            self.assertNotIn("internal stderr", captions)
            self.assertNotIn("secret", captions)
            self.assertIn("Git-HEAD des Quellcheckouts: " + (revision["head"] or "unbekannt"), captions)
        self.assertEqual(displays[0], displays[1])
        self.assertEqual(displays[0], displays[2])
        self.assertEqual(data, {"currencies": {}})


if __name__ == "__main__":
    unittest.main()
