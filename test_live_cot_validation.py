"""Offline domain and actual-UI regressions for live COT, independent of app import.

Only selected original functions/AST branches are executed. Provider loaders,
manual storage and Streamlit are mocked; no numeric fixture is a live observation.
"""
import ast
import builtins
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlparse, urlsplit

import numpy as np
import pandas as pd


APP_PATH = Path(__file__).with_name("app.py")
TREE = ast.parse(APP_PATH.read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
SOURCE_REFERENCE = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
REQUIRED_DETAILS = {"percentile", "status", "reason", "as_of", "sample_size",
                    "source", "input_mode", "checked_at"}
CURRENCIES = {currency: {"flag": currency} for currency in
              ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")}
COT_SYMBOLS = ast.literal_eval(next(node.value for node in TREE.body
    if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
        and target.id == "COT_SYMBOLS" for target in node.targets)))
CODE = COT_SYMBOLS["GBP"]
LONG = "Noncommercial Positions-Long (All)"
SHORT = "Noncommercial Positions-Short (All)"
DATE = "As of Date in Form YYYY-MM-DD"
CODE_COLUMN = "CFTC Contract Market Code"


def forbidden(*args, **kwargs):
    raise AssertionError("No provider, file, or network access is allowed")


def functions_namespace(*, real_manual_reader=False):
    names = {"finite_number", "parse_live_cot_frames", "get_live_cot_details",
             "get_live_cot_rows", "save_manual_cot_entry", "load_live_manual_cot"}
    nodes = [copy.deepcopy(node) for node in TREE.body
             if isinstance(node, ast.FunctionDef)
             and (node.name in names or node.name.startswith("_live_cot_"))]
    for node in nodes:
        node.decorator_list = []
    ns = {"pd": pd, "np": np, "math": math, "re": re, "json": json,
          "datetime": datetime, "timedelta": timedelta, "timezone": timezone, "os": os,
          "urlparse": urlparse, "urlsplit": urlsplit, "CURRENCIES": CURRENCIES,
          "COT_SYMBOLS": COT_SYMBOLS, "load_cot_year_cached": Mock(return_value=None),
          "load_manual_cot": Mock(side_effect=forbidden), "operator_is_authorized": lambda: False,
          "open": Mock(side_effect=forbidden), "requests": SimpleNamespace(get=forbidden),
          "st": Mock(), "get_cot_signal": Mock(side_effect=forbidden),
          "get_latest_cot_percentile": Mock(side_effect=forbidden)}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0)] + [node for node in TREE.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == "LIVE_COT_SYMBOLS" for target in node.targets)] + nodes,
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<live-cot-functions>", "exec"), ns)
    if not real_manual_reader:
        ns["load_live_manual_cot"] = Mock(return_value={})
    return ns


def frame(nets=(1, 2, 3, 4, 5, 2), *, code=CODE, age=0, end=None):
    latest = pd.Timestamp(end or NOW).normalize() - pd.Timedelta(days=age)
    dates = pd.date_range(end=latest, periods=len(nets), freq="7D")
    # Whole, nonnegative gross positions permit positive and negative net fixtures.
    return pd.DataFrame({CODE_COLUMN: [code] * len(nets),
                         DATE: [value.date().isoformat() for value in dates],
                         LONG: [1000 + max(net, 0) for net in nets],
                         SHORT: [1000 + max(-net, 0) for net in nets]})


def manual(percentile=50.0, *, age=0, **changes):
    entry = {"position": "Neutral", "net_position": 0, "percentile": percentile,
             "date": (NOW - timedelta(days=age)).date().isoformat(),
             "source_reference": SOURCE_REFERENCE}
    entry.update(changes)
    return entry


class LiveCOTParserTests(unittest.TestCase):
    def setUp(self):
        self.ns = functions_namespace()

    def parse(self, frames, code=CODE, now=NOW):
        result = self.ns["parse_live_cot_frames"](code, frames, now)
        self.assertTrue(REQUIRED_DETAILS <= result.keys())
        self.assertIn(result["status"], {"VALID", "UNAVAILABLE", "INVALID", "STALE"})
        self.assertIsInstance(result["sample_size"], int)
        self.assertIsInstance(result["reason"], str)
        self.assertEqual(result["input_mode"], "automatic")
        return result

    def assert_blocked(self, result, status=None):
        self.assertIsNone(result["percentile"])
        self.assertNotEqual(result["status"], "VALID")
        self.assertTrue(result["reason"])
        if status:
            self.assertEqual(result["status"], status)

    def test_missing_frames_do_not_become_neutral_fifty(self):
        for frames in ([], [None], [pd.DataFrame()], [None, pd.DataFrame()]):
            with self.subTest(frames=len(frames)):
                self.assert_blocked(self.parse(frames), "UNAVAILABLE")

    def test_missing_selected_contract_is_unavailable_even_when_other_data_exists(self):
        self.assert_blocked(self.parse([frame(code="092741")]), "UNAVAILABLE")

    def test_genuine_fifty_is_valid_and_visible(self):
        result = self.parse([frame()])
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["percentile"], 50.0)
        self.assertEqual(result["as_of"], "2026-10-12")
        self.assertEqual(result["sample_size"], 6)
        self.assertTrue(result["source"])

    def test_five_observations_are_sufficient_but_four_are_not(self):
        self.assertEqual(self.parse([frame(nets=(1, 2, 3, 4, 2))])["status"], "VALID")
        self.assert_blocked(self.parse([frame(nets=(1, 2, 3, 2))]))

    def test_window_is_at_most_156_unique_observations(self):
        # An all-history rank would be 45/200; the tail rank is exactly 1/156.
        nets = [-10] * 44 + [10] * 155 + [0]
        result = self.parse([frame(nets=nets)])
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["sample_size"], 156)
        self.assertAlmostEqual(result["percentile"], 100.0 / 156)

    def test_ten_day_report_is_valid_eleven_day_report_is_stale(self):
        self.assertEqual(self.parse([frame(age=10)])["status"], "VALID")
        old = self.parse([frame(age=11)])
        self.assert_blocked(old, "STALE")
        self.assertEqual(old["as_of"], "2026-10-01")

    def test_future_latest_report_does_not_fall_back_to_older_report(self):
        values = frame()
        values.loc[values.index[-1], DATE] = "2026-10-13"
        self.assert_blocked(self.parse([values]), "INVALID")

    def test_invalid_current_gross_positions_never_fall_back_to_older_data(self):
        for column in (LONG, SHORT):
            for value in (float("nan"), float("inf"), -1, 0.5, "bad", True):
                with self.subTest(column=column, value=repr(value)):
                    values = frame()
                    values[column] = values[column].astype(object)
                    values.loc[values.index[-1], column] = value
                    self.assert_blocked(self.parse([values]), "INVALID")

    def test_zero_gross_and_negative_net_positions_are_valid(self):
        values = frame(nets=(-3, -2, -1, 0, 1, -1))
        values.loc[values.index[-1], LONG] = 0
        values.loc[values.index[-1], SHORT] = 1
        self.assertEqual(self.parse([values])["status"], "VALID")

    def test_invalid_date_cannot_confirm_freshness(self):
        for value in ("bad", "", None, "2026-02-30"):
            with self.subTest(value=value):
                values = frame()
                values.loc[values.index[-1], DATE] = value
                self.assert_blocked(self.parse([values]), "INVALID")

    def test_iso_and_yymmdd_contract_date_formats_agree(self):
        original = frame()
        compact = original.rename(columns={DATE: "As of Date in Form YYMMDD"})
        compact["As of Date in Form YYMMDD"] = pd.to_datetime(
            compact["As of Date in Form YYMMDD"]).dt.strftime("%y%m%d")
        iso_result, compact_result = self.parse([original]), self.parse([compact])
        for key in ("status", "percentile", "as_of", "sample_size"):
            self.assertEqual(iso_result[key], compact_result[key])

    def test_contract_code_padding_and_supported_column_name(self):
        values = frame(code=96742).rename(columns={CODE_COLUMN: "CFTC_Contract_Market_Code"})
        self.assertEqual(self.parse([values])["status"], "VALID")

    def test_both_date_columns_must_agree_including_newest_report(self):
        values = frame()
        values["As of Date in Form YYMMDD"] = pd.to_datetime(values[DATE]).dt.strftime("%y%m%d")
        self.assertEqual(self.parse([values])["status"], "VALID")
        values.loc[values.index[-1], "As of Date in Form YYMMDD"] = "261013"
        self.assert_blocked(self.parse([values]), "INVALID")

    def test_both_contract_code_columns_must_agree(self):
        values = frame()
        values["CFTC_Contract_Market_Code"] = CODE
        self.assertEqual(self.parse([values])["status"], "VALID")
        values.loc[values.index[-1], "CFTC_Contract_Market_Code"] = "092741"
        self.assert_blocked(self.parse([values]), "INVALID")

    def test_duplicate_column_names_after_whitespace_normalization_are_invalid(self):
        values = frame()
        values[LONG + " "] = values[LONG]
        self.assert_blocked(self.parse([values]), "INVALID")

    def test_euro_fx_and_usd_index_market_names_agree_with_their_original_codes(self):
        for code, name in (("099741", "EURO FX - CHICAGO MERCANTILE EXCHANGE"),
                           ("098662", "USD INDEX - ICE FUTURES U.S.")):
            for column in ("Market and Exchange Names", "Market_and_Exchange_Names"):
                with self.subTest(code=code, column=column):
                    values = frame(code=code)
                    values[column] = name
                    self.assertEqual(self.parse([values], code=code)["status"], "VALID")

    def test_known_market_name_conflict_cannot_pass_a_numeric_contract_check(self):
        for code, wrong_name in (("099741", "USD INDEX - ICE FUTURES U.S."),
                                ("098662", "EURO FX - CHICAGO MERCANTILE EXCHANGE")):
            with self.subTest(code=code):
                values = frame(code=code)
                values["Market and Exchange Names"] = wrong_name
                self.assert_blocked(self.parse([values], code=code), "INVALID")

    def test_exact_duplicate_reports_do_not_inflate_sample_or_change_rank(self):
        values = frame()
        result = self.parse([values, values.copy(deep=True)])
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["sample_size"], 6)
        self.assertEqual(result["percentile"], 50.0)

    def test_conflicting_current_duplicate_is_invalid(self):
        values = frame()
        conflict = values.tail(1).copy()
        conflict[LONG] += 20
        self.assert_blocked(self.parse([values, conflict]), "INVALID")

    def test_input_frames_and_order_are_not_mutated(self):
        values = frame().iloc[::-1].copy()
        saved = values.copy(deep=True)
        self.assertEqual(self.parse([values])["percentile"], 50.0)
        pd.testing.assert_frame_equal(values, saved)

    def test_missing_required_schema_is_blocked(self):
        for column in (DATE, CODE_COLUMN, LONG, SHORT):
            with self.subTest(column=column):
                self.assert_blocked(self.parse([frame().drop(columns=[column])]))

    def test_utc_normalization_preserves_calendar_age_and_reports_check_time(self):
        local_now = datetime(2026, 10, 13, 1, 0, tzinfo=timezone(timedelta(hours=2)))
        result = self.parse([frame(age=10)], now=local_now)
        self.assertEqual(result["status"], "VALID")
        checked = pd.Timestamp(result["checked_at"])
        self.assertIsNotNone(checked.tzinfo)
        self.assertEqual(checked, pd.Timestamp("2026-10-12T23:00:00Z"))
        naive = self.parse([frame()], now=NOW.replace(tzinfo=None))
        self.assertEqual(pd.Timestamp(naive["checked_at"]), pd.Timestamp(NOW))


class LiveCOTRowsAndManualTests(unittest.TestCase):
    def setUp(self):
        self.ns = functions_namespace()

    def details(self, entry=None, automatic=None):
        self.ns["load_live_manual_cot"].return_value = {} if entry is None else {"GBP": entry}
        self.ns["load_cot_year_cached"].side_effect = lambda year: automatic if year == 2026 else None
        result = self.ns["get_live_cot_details"]("GBP", NOW)
        self.assertTrue(REQUIRED_DETAILS <= result.keys())
        return result

    def test_all_eight_rows_exist_without_any_valid_data(self):
        rows = self.ns["get_live_cot_rows"](NOW)
        self.assertEqual(len(rows), 8)
        self.assertEqual({row["currency"] for row in rows}, set(CURRENCIES))
        for row in rows:
            self.assertIsNone(row["percentile"])
            self.assertEqual(row["Perzentil"], "N/A")
            self.assertNotEqual(row["status"], "VALID")
            self.assertTrue(REQUIRED_DETAILS <= row.keys())

    def test_four_year_loaders_and_manual_are_called_once_per_shared_batch(self):
        self.ns["load_cot_year_cached"].side_effect = lambda year: frame() if year == 2026 else None
        rows = self.ns["get_live_cot_rows"](NOW)
        self.assertEqual([call.args[0] for call in self.ns["load_cot_year_cached"].call_args_list],
                         [2026, 2025, 2024, 2023])
        self.ns["load_live_manual_cot"].assert_called_once_with()
        self.ns["load_manual_cot"].assert_not_called()
        gbp = next(row for row in rows if row["currency"] == "GBP")
        self.assertEqual(gbp["Perzentil"], "50.0%")
        self.assertEqual(gbp["percentile"], 50.0)
        self.assertTrue(all(row["Perzentil"] == "N/A" for row in rows if row["currency"] != "GBP"))

    def test_currency_without_original_contract_mapping_stays_unavailable(self):
        self.assertNotIn("USD", COT_SYMBOLS)
        result = self.ns["get_live_cot_details"]("USD", NOW)
        self.assertIsNone(result["percentile"])
        self.assertNotEqual(result["status"], "VALID")

    def test_live_euro_uses_original_euro_fx_code_without_rewriting_historical_map(self):
        self.assertEqual(COT_SYMBOLS["EUR"], "098662")
        self.assertEqual(self.ns["LIVE_COT_SYMBOLS"]["EUR"], "099741")
        self.ns["load_cot_year_cached"].side_effect = lambda year: frame(
            code="099741") if year == 2026 else None
        result = self.ns["get_live_cot_details"]("EUR", NOW)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["contract_code"], "099741")
        self.assertEqual(result["percentile"], 50.0)

    def test_usd_index_and_euro_cross_contracts_cannot_be_relabelled_as_euro_fx(self):
        for code in ("098662", "299741", "399741"):
            with self.subTest(code=code):
                self.ns["load_cot_year_cached"].side_effect = lambda year: frame(
                    code=code) if year == 2026 else None
                result = self.ns["get_live_cot_details"]("EUR", NOW)
                self.assertIsNone(result["percentile"])
                self.assertNotEqual(result["status"], "VALID")

    def test_loader_exception_is_contained_and_no_rows_disappear(self):
        self.ns["load_cot_year_cached"].side_effect = RuntimeError("synthetic loader outage")
        rows = self.ns["get_live_cot_rows"](NOW)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row["percentile"] is None for row in rows))

    def test_manual_usd_is_explicit_external_context_without_invented_contract(self):
        self.ns["load_live_manual_cot"].return_value = {"USD": manual(0.0)}
        rows = self.ns["get_live_cot_rows"](NOW)
        usd = next(row for row in rows if row["currency"] == "USD")
        self.assertEqual(usd["percentile"], 0.0)
        self.assertEqual(usd["Perzentil"], "0.0%")
        self.assertEqual(usd["input_mode"], "manual")
        self.assertEqual(usd["sample_size"], 0)
        self.assertIn("nicht automatisch verifiziert", usd["source"])
        self.assertNotIn("USD", COT_SYMBOLS)

    def test_valid_manual_is_identified_when_it_overrides_automatic_context(self):
        result = self.details(manual(0.0), frame())
        self.assertEqual(result["percentile"], 0.0)
        self.assertEqual(result["input_mode"], "manual")
        self.assertEqual(result["sample_size"], 0)

    def test_malformed_manual_record_cannot_drop_a_currency_row(self):
        for entry in ([], "not-a-record", 50):
            with self.subTest(entry=entry):
                self.ns["load_live_manual_cot"].return_value = {"GBP": entry}
                rows = self.ns["get_live_cot_rows"](NOW)
                self.assertEqual(len(rows), 8)
                gbp = next(row for row in rows if row["currency"] == "GBP")
                self.assertIsNone(gbp["percentile"])
                self.assertEqual(gbp["Perzentil"], "N/A")

    def test_uncertified_manual_optional_gross_context_is_rejected(self):
        for changes in ({"net_position": float("nan")}, {"net_position": 0.5},
                        {"position": "made-up-position"}):
            with self.subTest(changes=changes):
                result = self.details(manual(**changes))
                self.assertIsNone(result["percentile"])
                self.assertNotEqual(result["status"], "VALID")

    def test_manual_zero_fifty_and_hundred_are_preserved_and_explicitly_manual(self):
        for value in (0.0, 50.0, 100.0):
            with self.subTest(value=value):
                result = self.details(manual(value))
                self.assertEqual(result["status"], "VALID")
                self.assertEqual(result["percentile"], value)
                self.assertEqual(result["input_mode"], "manual")
                self.assertEqual(result["sample_size"], 0)
                self.assertIn("nicht automatisch verifiziert", result["source"])
                self.assertIn(SOURCE_REFERENCE, result["source"])

    def test_invalid_manual_percentiles_are_not_a_fifty_default(self):
        for value in (None, True, -0.1, 100.1, float("nan"), float("inf"), "bad"):
            with self.subTest(value=repr(value)):
                result = self.details(manual(value))
                self.assertIsNone(result["percentile"])
                self.assertNotEqual(result["status"], "VALID")
        missing = manual()
        missing.pop("percentile")
        self.assertIsNone(self.details(missing)["percentile"])

    def test_missing_invalid_and_future_manual_dates_are_blocked(self):
        for value in (None, "", "bad", "2026-02-30", "2026-10-13"):
            with self.subTest(value=value):
                result = self.details(manual(date=value))
                self.assertIsNone(result["percentile"])
                self.assertNotEqual(result["status"], "VALID")

    def test_manual_age_boundary_is_ten_days(self):
        self.assertEqual(self.details(manual(age=10))["status"], "VALID")
        result = self.details(manual(age=11))
        self.assertEqual(result["status"], "STALE")
        self.assertIsNone(result["percentile"])

    def test_manual_reference_requires_safe_https_source_and_redacts_rejected_value(self):
        references = (None, "", "http://example.org/report", "https://u:password@example.org/r",
                      "https://example.org/r?private=secret", "https://example.org/r#private",
                      "https://example.org/<script>", "https://example.org/with space", "not-a-url")
        for value in references:
            with self.subTest(value=value):
                result = self.details(manual(source_reference=value))
                self.assertIsNone(result["percentile"])
                self.assertNotEqual(result["status"], "VALID")
                if value:
                    self.assertNotIn(value, result["reason"])

    def test_invalid_legacy_manual_can_use_valid_automatic_with_visible_warning(self):
        legacy = {"position": "Neutral", "net_position": 0, "percentile": 99,
                  "date": "2024-01-01"}
        result = self.details(legacy, frame())
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["percentile"], 50.0)
        self.assertEqual(result["input_mode"], "automatic")
        self.assertTrue(result["reason"])
        self.assertTrue(any(word in result["reason"].lower() for word in ("manuell", "manual")))

    def test_invalid_manual_entries_and_original_store_are_not_mutated(self):
        store = {"EUR": manual(float("nan")), "USD": {"old": "preserve"}}
        saved = copy.deepcopy(store)
        self.ns["load_live_manual_cot"].return_value = store
        self.ns["get_live_cot_rows"](NOW)
        self.assertEqual(store["USD"], saved["USD"])
        self.assertEqual(store["EUR"].keys(), saved["EUR"].keys())
        self.assertTrue(math.isnan(store["EUR"]["percentile"]))
        self.ns["open"].assert_not_called()


class LiveCOTWriterTests(unittest.TestCase):
    def setUp(self):
        self.ns = functions_namespace()
        self.old = {"USD": {"legacy": True, "percentile": 50, "date": "2022-01-01"}}
        self.ns["load_live_manual_cot"].return_value = copy.deepcopy(self.old)
        self.file = MagicMock()
        self.ns["open"] = MagicMock(return_value=self.file)
        self.dump = Mock()
        self.ns["json"] = SimpleNamespace(dump=self.dump)
        self.current_date = datetime.now(timezone.utc).date().isoformat()

    def write(self, **changes):
        arguments = {"curr": "EUR", "position": "Neutral", "net_pos": 0,
                     "percentile": 50.0, "date_str": self.current_date,
                     "source_reference": SOURCE_REFERENCE}
        arguments.update(changes)
        return self.ns["save_manual_cot_entry"](**arguments)

    def test_authorization_is_required_before_read_or_write_even_for_invalid_input(self):
        with self.assertRaises(PermissionError):
            self.write(percentile=float("nan"))
        self.ns["load_live_manual_cot"].assert_not_called()
        self.ns["open"].assert_not_called()
        self.dump.assert_not_called()

    def test_authorized_write_preserves_other_legacy_entries_and_valid_zero(self):
        self.ns["operator_is_authorized"] = lambda: True
        self.ns.update(open=builtins.open, json=json)
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory(prefix="fx-cot-offline-test-") as directory:
            try:
                os.chdir(directory)
                path = Path("manual_cot.json")
                path.write_text(json.dumps(self.old), encoding="utf-8")
                self.write(percentile=0.0, net_pos=-7)
                result = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(result["USD"], self.old["USD"])
                self.assertEqual(result["EUR"]["percentile"], 0.0)
                self.assertEqual(result["EUR"]["net_position"], -7)
                self.assertEqual(result["EUR"]["source_reference"], SOURCE_REFERENCE)
            finally:
                os.chdir(original_cwd)

    def test_invalid_authorized_inputs_never_reach_the_file_writer(self):
        self.ns["operator_is_authorized"] = lambda: True
        for changes in ({"percentile": float("nan")}, {"percentile": 101},
                        {"percentile": True}, {"net_pos": float("inf")}, {"net_pos": 0.5},
                        {"net_pos": True}, {"position": "unknown"}, {"curr": "XXX"},
                        {"date_str": "not-a-date"}, {"date_str": "2099-01-01"},
                        {"source_reference": None}, {"source_reference": "http://example.org/r"}):
            with self.subTest(changes=changes):
                self.ns["open"].reset_mock()
                self.dump.reset_mock()
                with self.assertRaises(ValueError):
                    self.write(**changes)
                self.ns["open"].assert_not_called()
                self.dump.assert_not_called()

    def test_rejected_private_reference_never_appears_in_writer_exception(self):
        self.ns["operator_is_authorized"] = lambda: True
        reference = "https://example.org/r?api_key=synthetic-private"
        with self.assertRaises(ValueError) as caught:
            self.write(source_reference=reference)
        self.assertNotIn(reference, str(caught.exception))
        self.assertNotIn("synthetic-private", str(caught.exception))
        self.ns["open"].assert_not_called()


class FrozenHistoricalAndCoreTests(unittest.TestCase):
    def test_original_historical_getters_and_core_functions_are_ast_unchanged(self):
        # Captured independently from fixed baseline 2e56e927, not from the patch.
        expected = {
            "load_manual_cot": "e07521ad2350d1e11ceb1d6a2ac82958310b77fb4a6f360d35958223acaddd5c",
            "get_cot_signal": "8fa2018f076eeb8e757ba4b5fa200faabd6177be71cdb98daf50c5452c13407c",
            "get_latest_cot_percentile": "4df2928d650b6de1330f8f8977ac0284cf1e017f41b10ab78400dab6228b2c44",
            "compute_correction_score": "6e188ef2fb69e4b8a3d042909e55fc4cbbb5f31037a40859b94cad70da3b461a",
        }
        functions = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}
        for name, digest in expected.items():
            with self.subTest(function=name):
                self.assertEqual(hashlib.sha256(ast.dump(functions[name],
                    include_attributes=False).encode()).hexdigest(), digest)


class TemporaryStorePreservationTests(unittest.TestCase):
    """Real file behavior is restricted to a fresh temporary directory per test."""
    def setUp(self):
        self.ns = functions_namespace()
        self.ns.update(open=builtins.open, os=os, tempfile=tempfile, json=json,
                       operator_is_authorized=lambda: True)
        self.original_cwd = os.getcwd()
        self.directory = tempfile.TemporaryDirectory(prefix="fx-cot-offline-test-")
        os.chdir(self.directory.name)
        self.path = Path("manual_cot.json")
        self.original = {"USD": {"legacy": True, "percentile": 50,
                                 "date": "2022-01-01", "unknown_field": "retain"}}
        self.path.write_text(json.dumps(self.original), encoding="utf-8")

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.directory.cleanup()

    def write(self):
        return self.ns["save_manual_cot_entry"]("GBP", "Neutral", -7, 0.0,
            datetime.now(timezone.utc).date().isoformat(), source_reference=SOURCE_REFERENCE)

    def test_corrupt_or_nonobject_json_is_never_overwritten(self):
        for original in (b"{broken", b"\xffbad-encoding", b"[]", b"null", b"42",
                         b'{"USD":{"percentile":NaN}}', b'{"USD":{"percentile":Infinity}}',
                         b'{"EUR":{},"EUR":{"percentile":50}}',
                         b'{"USD":{"date":"2026-10-12","date":"2024-01-01"}}'):
            with self.subTest(original=original):
                self.path.write_bytes(original)
                # Simulate the old forgiving reader, proving the writer must read strictly.
                self.ns["load_live_manual_cot"].return_value = {}
                with self.assertRaises(ValueError):
                    self.write()
                self.assertEqual(self.path.read_bytes(), original)

    def test_successful_atomic_write_retains_old_currency_records(self):
        self.ns["load_live_manual_cot"].return_value = copy.deepcopy(self.original)
        self.write()
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["USD"], self.original["USD"])
        self.assertEqual(saved["GBP"]["percentile"], 0.0)
        self.assertEqual(saved["GBP"]["net_position"], -7)
        self.assertEqual(saved["GBP"]["source_reference"], SOURCE_REFERENCE)
        self.assertEqual({path.name for path in Path(".").iterdir()}, {"manual_cot.json"})

    def test_failed_atomic_replace_keeps_original_bytes_and_cleans_temporary_file(self):
        before = self.path.read_bytes()
        self.ns["load_live_manual_cot"].return_value = copy.deepcopy(self.original)
        with patch("os.replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(ValueError):
                self.write()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({path.name for path in Path(".").iterdir()}, {"manual_cot.json"})

    def test_failed_serialization_keeps_original_bytes_and_cleans_temporary_file(self):
        before = self.path.read_bytes()
        self.ns["load_live_manual_cot"].return_value = copy.deepcopy(self.original)
        with patch("json.dumps", side_effect=ValueError("synthetic serialization failure")):
            with self.assertRaises(ValueError):
                self.write()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual({path.name for path in Path(".").iterdir()}, {"manual_cot.json"})


class StrictLiveManualReaderTests(unittest.TestCase):
    """Read actual temporary JSON without ever accepting forgiving last-wins data."""
    def setUp(self):
        self.ns = functions_namespace(real_manual_reader=True)
        self.ns.update(open=builtins.open, os=os, json=json)
        self.original_cwd = os.getcwd()
        self.directory = tempfile.TemporaryDirectory(prefix="fx-cot-live-reader-test-")
        os.chdir(self.directory.name)
        self.path = Path("manual_cot.json")

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.directory.cleanup()

    def corrupt_payloads(self):
        first = json.dumps(manual(0.0)).encode()
        last = json.dumps(manual(100.0)).encode()
        return (b"{broken PRIVATE_CONTENT_MUST_NOT_LEAK", b"\xffbad-encoding",
                b"[]", b"null", b"42", b'{"GBP":{"percentile":NaN}}',
                b'{"GBP":{"percentile":Infinity}}',
                b'{"GBP":' + first + b',"GBP":' + last + b'}',
                b'{"GBP":{"date":"2024-01-01","date":"2026-10-12"}}')

    def assert_safe_rows(self, rows, *, automatic=False):
        self.assertEqual(len(rows), 8)
        for row in rows:
            self.assertNotEqual(row["input_mode"], "manual")
            self.assertIn("manuell", row["reason"].lower())
            self.assertNotIn("PRIVATE_CONTENT_MUST_NOT_LEAK", row["reason"])
            self.assertNotIn("PRIVATE_PATH_MUST_NOT_LEAK", row["reason"])
            if automatic and row["currency"] == "GBP":
                self.assertEqual(row["percentile"], 50.0)
                self.assertEqual(row["status"], "VALID")
                self.assertEqual(row["input_mode"], "automatic")
            else:
                self.assertIsNone(row["percentile"])
                self.assertEqual(row["Perzentil"], "N/A")
        self.ns["load_manual_cot"].assert_not_called()

    def test_missing_manual_file_is_empty_and_is_not_created_by_reader(self):
        self.assertEqual(self.ns["load_live_manual_cot"](), {})
        self.assertFalse(self.path.exists())
        self.ns["load_manual_cot"].assert_not_called()

    def test_valid_manual_file_is_read_once_per_eight_rows_without_mutation(self):
        self.path.write_text(json.dumps({"GBP": manual(0.0)}), encoding="utf-8")
        original = self.path.read_bytes()
        reader = self.ns["load_live_manual_cot"]
        self.ns["load_live_manual_cot"] = Mock(wraps=reader)
        rows = self.ns["get_live_cot_rows"](NOW)
        self.ns["load_live_manual_cot"].assert_called_once_with()
        self.assertEqual(len(rows), 8)
        gbp = next(row for row in rows if row["currency"] == "GBP")
        self.assertEqual(gbp["percentile"], 0.0)
        self.assertEqual(gbp["input_mode"], "manual")
        self.assertEqual(self.path.read_bytes(), original)
        self.ns["load_manual_cot"].assert_not_called()

    def test_corrupt_constants_duplicates_and_nonobjects_raise_without_mutation(self):
        for original in self.corrupt_payloads():
            with self.subTest(original=original):
                self.path.write_bytes(original)
                with self.assertRaises(ValueError) as caught:
                    self.ns["load_live_manual_cot"]()
                self.assertNotIn("PRIVATE_CONTENT_MUST_NOT_LEAK", str(caught.exception))
                self.assertEqual(self.path.read_bytes(), original)
        self.ns["load_manual_cot"].assert_not_called()

    def test_rejected_store_cannot_supply_any_manual_row_and_every_row_warns(self):
        for original in self.corrupt_payloads():
            with self.subTest(original=original):
                self.path.write_bytes(original)
                self.assert_safe_rows(self.ns["get_live_cot_rows"](NOW))
                self.assertEqual(self.path.read_bytes(), original)

    def test_valid_automatic_fallback_keeps_warning_for_each_rejected_store(self):
        self.ns["load_cot_year_cached"].side_effect = lambda year: frame() if year == 2026 else None
        reader = self.ns["load_live_manual_cot"]
        self.ns["load_live_manual_cot"] = Mock(wraps=reader)
        for original in self.corrupt_payloads():
            with self.subTest(original=original):
                self.path.write_bytes(original)
                self.ns["load_live_manual_cot"].reset_mock()
                self.assert_safe_rows(self.ns["get_live_cot_rows"](NOW), automatic=True)
                self.ns["load_live_manual_cot"].assert_called_once_with()
                self.assertEqual(self.path.read_bytes(), original)

    def test_read_error_is_redacted_and_fallback_never_accepts_manual_data(self):
        self.path.write_text(json.dumps({"GBP": manual(100.0)}), encoding="utf-8")
        original = self.path.read_bytes()
        self.ns["open"] = Mock(side_effect=PermissionError("PRIVATE_PATH_MUST_NOT_LEAK"))
        with self.assertRaises(ValueError) as caught:
            self.ns["load_live_manual_cot"]()
        self.assertNotIn("PRIVATE_PATH_MUST_NOT_LEAK", str(caught.exception))
        self.ns["load_cot_year_cached"].side_effect = lambda year: frame() if year == 2026 else None
        self.assert_safe_rows(self.ns["get_live_cot_rows"](NOW), automatic=True)
        self.assertEqual(self.path.read_bytes(), original)


class ActualLiveCOTUITests(unittest.TestCase):
    def render_actual_cot_branches(self, manual_zero=False, store_error=False):
        """Run the original shared assignment, sidebar and Tab 11 table ASTs."""
        ns = functions_namespace()
        ns["load_cot_year_cached"].side_effect = lambda year: frame() if year == 2026 else None
        if manual_zero:
            ns["load_live_manual_cot"].return_value = {"USD": manual(0.0)}
        if store_error:
            ns["load_live_manual_cot"].side_effect = ValueError("PRIVATE_PATH_MUST_NOT_LEAK")
        rows = ns["get_live_cot_rows"](NOW)
        ui = MagicMock()
        ns.update(st=ui, cot_live_rows=None, get_live_cot_rows=Mock(return_value=rows))
        assignments = [node for node in ast.walk(TREE) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "cot_live_rows"
                               for target in node.targets)
                       and isinstance(node.value, ast.Constant) and node.value.value is None]
        self.assertEqual(len(assignments), 1, "A shared batch must start uninitialized")
        exec(compile(ast.Module(body=assignments, type_ignores=[]), "<cot-shared-batch>", "exec"), ns)
        sidebars = [node for node in ast.walk(TREE) if isinstance(node, ast.With)
                    and any(isinstance(item.context_expr, ast.Call)
                            and item.context_expr.args
                            and isinstance(item.context_expr.args[0], ast.Constant)
                            and item.context_expr.args[0].value == "📅 COT-Status"
                            for item in node.items)]
        self.assertEqual(len(sidebars), 1)
        exec(compile(ast.Module(body=sidebars, type_ignores=[]), "<cot-sidebar-ui>", "exec"), ns)
        tab = next(node for node in ast.walk(TREE) if isinstance(node, ast.With)
                   and any(isinstance(item.context_expr, ast.Name) and item.context_expr.id == "tab11"
                           for item in node.items))
        start = next(index for index, node in enumerate(tab.body) if isinstance(node, ast.Expr)
                     and isinstance(node.value, ast.Call) and node.value.args
                     and isinstance(node.value.args[0], ast.Constant)
                     and isinstance(node.value.func, ast.Attribute)
                     and node.value.func.attr == "subheader"
                     and "COT" in node.value.args[0].value)
        exec(compile(ast.Module(body=tab.body[start:], type_ignores=[]), "<cot-tab11-table-ui>", "exec"), ns)
        ns["get_live_cot_rows"].assert_called_once_with()
        ns["get_cot_signal"].assert_not_called()
        ns["get_latest_cot_percentile"].assert_not_called()
        self.assertEqual(ui.dataframe.call_count, 2)
        displayed = [call.args[0] for call in ui.dataframe.call_args_list]
        for table in displayed:
            self.assertIsInstance(table, pd.DataFrame)
            self.assertEqual(len(table), 8)
            self.assertEqual(set(table.columns), {"Währung", "Perzentil", "Bericht (As-of)",
                                                 "Stichprobe", "Quelle", "Status / Warnung"})
        pd.testing.assert_frame_equal(*displayed)
        return displayed[0], ui

    def test_actual_sidebar_and_tab11_share_eight_rows_and_preserve_genuine_fifty(self):
        table, _ = self.render_actual_cot_branches()
        self.assertEqual(table.loc[table["Währung"] == "GBP", "Perzentil"].iloc[0], "50.0%")
        self.assertTrue((table.loc[table["Währung"] != "GBP", "Perzentil"] == "N/A").all())
        self.assertFalse(table["Status / Warnung"].str.contains("Gesund").any())

    def test_actual_sidebar_and_tab11_preserve_manual_zero_with_source_and_asof(self):
        table, _ = self.render_actual_cot_branches(manual_zero=True)
        usd = table.loc[table["Währung"] == "USD"].iloc[0]
        self.assertEqual(usd["Perzentil"], "0.0%")
        self.assertEqual(usd["Bericht (As-of)"], "2026-10-12")
        self.assertEqual(usd["Stichprobe"], "Manuell")
        self.assertIn("nicht automatisch verifiziert", usd["Quelle"])

    def test_actual_sidebar_and_tab11_show_reader_failure_with_automatic_fallback(self):
        table, _ = self.render_actual_cot_branches(store_error=True)
        self.assertEqual(table.loc[table["Währung"] == "GBP", "Perzentil"].iloc[0], "50.0%")
        self.assertTrue((table.loc[table["Währung"] != "GBP", "Perzentil"] == "N/A").all())
        self.assertTrue(table["Status / Warnung"].str.lower().str.contains("manuell").all())
        self.assertFalse(table["Status / Warnung"].str.contains("PRIVATE_PATH_MUST_NOT_LEAK").any())

    def test_actual_manual_ui_passes_source_to_protected_writer(self):
        calls = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "save_manual_cot_entry"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(len(calls[0].args) >= 6 or any(
            item.arg == "source_reference" for item in calls[0].keywords))
        branches = [node for node in ast.walk(TREE) if isinstance(node, ast.If)
                    and any(call is calls[0] for call in ast.walk(node))]
        self.assertTrue(any(any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                              and call.func.id == "operator_is_authorized"
                              for call in ast.walk(branch.test)) for branch in branches))


if __name__ == "__main__":
    unittest.main()
