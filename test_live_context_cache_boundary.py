"""Live UI context must use validated snapshots or show N/A, without provider calls."""

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


TREE = ast.parse(Path(__file__).with_name("app.py").read_text())
FUNCTIONS = {"live_core_observation_for_display", "get_yield_trends",
             "get_series_trend_points", "get_yield_details",
             "get_inflation_expectations_data"}


def load_functions():
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
             and node.name in FUNCTIONS]
    namespace = {"use_live_core_cache": lambda *args: True}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<live-context>", "exec"), namespace)
    return namespace


class LiveContextCacheBoundaryTests(unittest.TestCase):
    def test_blocked_raw_value_is_masked_but_provenance_and_reason_remain(self):
        ns = load_functions()
        details = {"PMI": None,
                   "_observations": {"PMI": {"value": 52.1, "source": "Unverified survey",
                                             "date": "2026-08-01"}},
                   "_blocking_reasons": {"PMI": "Nutzungsrecht ungeklärt"}}
        shown = ns["live_core_observation_for_display"](details, "PMI")
        self.assertIsNone(shown["value"])
        self.assertEqual(shown["source"], "Unverified survey")
        self.assertEqual(shown["date"], "2026-08-01")
        self.assertIn("Nutzungsrecht ungeklärt", shown["_display_status"])
        self.assertEqual(details["_observations"]["PMI"]["value"], 52.1)

    def test_valid_raw_zero_is_not_treated_as_missing(self):
        ns = load_functions()
        details = {"Inflation": 0.0, "_observations": {"Inflation": {"value": 0.0}}}
        shown = ns["live_core_observation_for_display"](details, "Inflation")
        self.assertEqual(shown["value"], 0.0)
        self.assertEqual(shown["_display_status"], "Geprüft")

    def test_malformed_blocked_observation_cannot_break_display(self):
        ns = load_functions()
        details = {"GDP": None, "_observations": {"GDP": None},
                   "_blocking_reasons": {"GDP": "Antwort ungültig"}}
        shown = ns["live_core_observation_for_display"](details, "GDP")
        self.assertIsNone(shown["value"])
        self.assertIn("Antwort ungültig", shown["_display_status"])

    def test_live_trends_do_not_fetch_fred_or_yield_history(self):
        ns = load_functions()
        ns["get_fred_data"] = Mock(side_effect=AssertionError("FRED request"))
        ns["get_yield_series"] = Mock(side_effect=AssertionError("yield request"))
        self.assertIsNone(ns["get_series_trend_points"]("MANEMP"))
        self.assertEqual(ns["get_yield_trends"]("USD"), {})
        ns["get_fred_data"].assert_not_called()
        ns["get_yield_series"].assert_not_called()

    def test_live_5y_10y_and_expectations_do_not_fetch_providers(self):
        ns = load_functions()
        ns.update(YIELD_2Y_SERIES={"USD": "2Y"}, YIELD_5Y_SERIES={"USD": "5Y"},
                  YIELD_10Y_SERIES={"USD": "10Y"}, FRED_KEY="unused")
        ns["get_genuine_5y_yield_historical"] = Mock(side_effect=AssertionError("5Y request"))
        ns["get_genuine_10y_yield_historical"] = Mock(side_effect=AssertionError("10Y request"))
        self.assertIsNone(ns["get_yield_details"]("USD", ns["YIELD_5Y_SERIES"]))
        self.assertIsNone(ns["get_yield_details"]("USD", ns["YIELD_10Y_SERIES"]))
        ns["get_genuine_5y_yield_historical"].assert_not_called()
        ns["get_genuine_10y_yield_historical"].assert_not_called()

        ns["datetime"] = __import__("datetime").datetime
        ns["pd"] = Mock()
        ns["get_cpi_yoy_details"] = Mock(return_value=(2.4, "2026-08", "CPI_YOY", "BLS", "CPI", "FRESH"))
        ns["get_fred_data_historical"] = Mock(side_effect=AssertionError("expectation request"))
        result = ns["get_inflation_expectations_data"]("USD")
        self.assertEqual(result["actual_cpi"], 2.4)
        self.assertIsNone(result["cpi_trend"])
        self.assertIsNone(result["oecd_expectation"])
        self.assertIsNone(result["market_breakeven"])
        ns["get_fred_data_historical"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
