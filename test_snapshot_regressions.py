"""Offline snapshot/outcome regressions: execute only audited function definitions."""
import ast
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd


SOURCE = Path(__file__).with_name("app.py")
FUNCTIONS = {
    "finite_number", "pair_core_is_complete", "load_live_signals", "save_live_signals", "_live_snapshot_weights", "_live_run_summary", "_finish_live_summary",
    "_live_positive_price", "_live_price_history", "save_live_signal_snapshot",
    "save_currency_snapshot", "save_all_g10_live_snapshots", "update_open_outcomes",
}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 4, 22, 0, 0, tzinfo=tz)


def harness():
    tree = ast.parse(SOURCE.read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    assert {n.name for n in selected} == FUNCTIONS
    namespace = {
        "os": os, "json": json, "np": np, "pd": pd, "datetime": Clock,
        "CURRENT_MODEL_VERSION": "CORE_V2_7_2026_09", "FCS_KEY": "offline-test",
        "CURRENCIES": {"USD": {}, "EUR": {}},
        "st": SimpleNamespace(session_state={}),
        "check_demo_active": lambda: False,
        "compute_checklist_snapshot": lambda weights: [],
        "compute_currency_details": lambda *args: {**dict.fromkeys(("Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP"), 20.0), "_live_checked": True, "_completeness": 100.0, "_missing": []},
        "compute_currency_professional_score_and_regime_custom": lambda *args: (20, "Normal", 20, 0, {}),
        "get_pair_signal_and_badge": lambda *args: ("MID BUY", "green", 25, "BUY"),
        "get_vix_value": lambda *args: None,
        "get_oil_price": lambda *args: None,
        "get_milk_price": lambda *args: None,
        "get_cpi_yoy_details": lambda *args: (2.1, "2026-08-01", "YoY", "FRED", "CPIAUCNS", "FRESH"),
        "get_inflation_expectations_data": lambda *args: {},
        "get_verified_policy_rate": lambda *args: {},
        "get_genuine_2y_yield_historical": lambda *args: (None,),
        "get_genuine_5y_yield_historical": lambda *args: (None,),
        "get_unemployment_value": lambda *args: None,
        "get_gdp_yoy_value": lambda *args: None,
        "get_fcs_history_data": lambda *args: (None, None, False),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def snapshot(entry=100.0, date="2026-08-03", signal="MID BUY"):
    return {
        "metadata": {"date": date, "entry_price_date": date, "pair": "EUR/USD"},
        "entry_price": entry, "pair_signal": {"signal": signal, "divergence": 25.0},
        "outcome_status": "OPEN",
        "outcomes": {str(n): {"exit_price": None, "exit_date": None, "return_pct": None,
                              "directional_return_pct": None, "status": None, "mfe": None, "mae": None}
                     for n in (1, 3, 5, 10, 15, 20)},
    }


def prices():
    dates = pd.bdate_range("2026-08-03", periods=23)
    closes = np.arange(100.0, 123.0)
    return pd.DataFrame({"date": dates, "close": closes, "high": closes + 1, "low": closes - 1})


class SnapshotRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.chdir, self.cwd)
        self.ns = harness()

    def put(self, data):
        Path("live_signals.json").write_text(json.dumps(data))

    def read(self):
        return json.loads(Path("live_signals.json").read_text())

    def run_outcomes(self, records, df=None):
        self.put(records)
        self.ns["get_fcs_history_data"] = lambda *args: (df if df is not None else prices(), None, True)
        summary = self.ns["update_open_outcomes"]()
        return self.read(), summary

    def save_pair(self, price=100, date="2026-09-04", badge="MID BUY"):
        return self.ns["save_live_signal_snapshot"]("EUR/USD", "EUR", "USD", 20, -5, 25, badge,
                                                     price, entry_price_date=date)

    def test_load_preserves_legacy_record_verbatim(self):
        original = {"old": {"metadata": {"date": "2020-01-01"}, "unknown": "preserved"}}
        self.put(original)
        self.assertEqual(self.ns["load_live_signals"](), original)
        self.ns["save_live_signals"](self.ns["load_live_signals"]())
        self.assertEqual(self.read(), original)

    def test_damaged_file_raises_and_remains_intact(self):
        Path("live_signals.json").write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            self.ns["load_live_signals"]()
        self.assertEqual(Path("live_signals.json").read_text(), "{broken")
        self.put([])
        with self.assertRaises(ValueError):
            self.ns["load_live_signals"]()

    def test_failed_atomic_write_does_not_truncate_history(self):
        self.put({"original": 1})
        def fail(data, stream, **kwargs):
            stream.write("{partial")
            raise OSError("simulated disk error")
        with patch.object(json, "dump", fail):
            with self.assertRaises(OSError):
                self.ns["save_live_signals"]({"new": 2})
        self.assertEqual(self.read(), {"original": 1})
        self.assertEqual(list(Path('.').glob('.live_signals_*.tmp')), [])

    def test_pair_first_snapshot_wins_even_if_signal_changes(self):
        self.assertTrue(self.save_pair())
        original = self.read()
        self.assertFalse(self.save_pair(price=120, badge="MID SELL"))
        self.assertEqual(self.read(), original)
        stored = next(iter(original.values()))
        self.assertEqual(stored["metadata"]["entry_price_date"], "2026-09-04")
        self.assertEqual(stored["base_currency_details"]["factor_scores"]["PMI"], 20.0)

    def test_currency_first_snapshot_wins_and_retains_missing_factors(self):
        weights = {"Geldpolitik": 35, "Inflation": 20, "Arbeitsmarkt": 20, "PMI": 20, "GDP": 5}
        details = {"Geldpolitik": 20, "PMI": None, "_missing": ["PMI"], "_completeness": 80}
        fn = self.ns["save_currency_snapshot"]
        self.assertTrue(fn("USD", 20, 20, 0, "Normal", details, weights, "2026-09-04"))
        original = self.read()
        self.assertFalse(fn("USD", -40, -40, 10, "Changed", {}, weights, "2026-09-04"))
        self.assertEqual(self.read(), original)
        self.assertIsNone(next(iter(original.values()))["factor_scores"]["PMI"])

    def test_snapshot_metadata_always_uses_frozen_weights(self):
        self.ns["st"].session_state.update({"active_live_model_weights": {"PMI": 100}, "active_live_model": "Custom experiment"})
        self.save_pair()
        stored = next(iter(self.read().values()))
        weights = stored["metadata"]["core_model_weights"]
        self.assertEqual([weights[k] for k in ("Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP")], [35, 20, 20, 20, 5])
        self.assertEqual(stored["metadata"]["core_model_name"], "CORE v1 - Baseline")
        self.assertEqual(stored["base_currency_details"]["original_weights"], weights)

    def test_incomplete_pair_cannot_be_written_directly(self):
        self.ns['compute_currency_details']=lambda *args: {'Geldpolitik':20,'_completeness':75}
        with self.assertRaisesRegex(ValueError,'PAIR_REQUIRES_COMPLETE_CORE'):
            self.save_pair()
        self.assertFalse(Path('live_signals.json').exists())

    def test_invalid_or_undated_entries_never_create_snapshot(self):
        for value in (0, -1, None, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.save_pair(price=value)
        with self.assertRaises(ValueError):
            self.save_pair(date="2026-09-03")
        self.assertFalse(Path("live_signals.json").exists())

    def test_invalid_old_entry_does_not_block_good_snapshot_or_rewrite_evidence(self):
        old = snapshot(entry=0)
        good = snapshot()
        result, summary = self.run_outcomes({"bad": old, "good": good})
        self.assertEqual(result["bad"]["outcome_status"], "INVALID")
        self.assertEqual(result["bad"]["outcome_error"], "INVALID_ENTRY_PRICE")
        self.assertEqual(result["bad"]["entry_price"], 0)
        self.assertEqual(result["bad"]["metadata"], old["metadata"])
        self.assertEqual(result["bad"]["pair_signal"], old["pair_signal"])
        self.assertEqual(result["bad"]["outcomes"], old["outcomes"])
        self.assertEqual(result["good"]["outcome_status"], "COMPLETED")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["status"], "PARTIAL")

    def test_neutral_has_raw_returns_but_no_directional_hit_or_excursions(self):
        result, _ = self.run_outcomes({"neutral": snapshot(signal="NEUTRAL")})
        horizon = result["neutral"]["outcomes"]["5"]
        self.assertEqual(horizon["return_pct"], 5.0)
        self.assertEqual(horizon["status"], "NO_TRADE")
        for key in ("directional_return_pct", "mfe", "mae"):
            self.assertIsNone(horizon[key])

    def test_observed_trading_bar_horizons_and_long_short_signs(self):
        df = prices()
        # A weekend row must not shorten the five-trading-day horizon.
        df = pd.concat([df, pd.DataFrame([{"date": pd.Timestamp("2026-08-08"), "close": 105, "high": 106, "low": 104}])])
        result, _ = self.run_outcomes({"long": snapshot(), "short": snapshot(signal="MID SELL")}, df)
        long = result["long"]["outcomes"]["5"]
        short = result["short"]["outcomes"]["5"]
        self.assertEqual(long["exit_date"], "2026-08-10")
        self.assertEqual(long["directional_return_pct"], 5.0)
        self.assertEqual(short["directional_return_pct"], -5.0)
        self.assertEqual(long["mfe"], 6.0)
        self.assertEqual(short["mae"], -6.0)

    def test_missing_entry_date_never_falls_forward(self):
        original = snapshot(date="2026-08-02")
        result, summary = self.run_outcomes({"weekend": original})
        self.assertEqual(result["weekend"], original)
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertEqual(summary["issues"][0]["reason"], "ENTRY_PRICE_DATE_UNAVAILABLE")

    def test_pending_horizons_and_existing_outcomes_are_not_rewritten(self):
        original = snapshot()
        original["outcomes"]["1"] = {"exit_price": 777, "status": "historical"}
        result, summary = self.run_outcomes({"partial": original}, prices().iloc[:4])
        self.assertEqual(result["partial"]["outcomes"]["1"], original["outcomes"]["1"])
        self.assertEqual(result["partial"]["outcomes"]["3"]["exit_price"], 103)
        self.assertIsNone(result["partial"]["outcomes"]["5"]["exit_price"])
        self.assertEqual(result["partial"]["outcome_status"], "OPEN")
        self.assertEqual(summary["status"], "SUCCESS")

    def test_mock_history_cannot_produce_outcomes(self):
        original = {"pair": snapshot()}
        self.put(original)
        self.ns["get_fcs_history_data"] = lambda *args: (prices(), None, False)
        summary = self.ns["update_open_outcomes"]()
        self.assertEqual(self.read(), original)
        self.assertEqual(summary["status"], "PARTIAL")

    def test_malformed_one_snapshot_does_not_abort_others(self):
        result, summary = self.run_outcomes({"broken": "bad", "good": snapshot()})
        self.assertEqual(result["broken"], "bad")
        self.assertEqual(result["good"]["outcome_status"], "COMPLETED")
        self.assertEqual(summary["errors"], 1)

    def test_bad_bar_blocks_affected_horizon_without_shifting_dates(self):
        df = prices()
        df.loc[3, "high"] = None
        result, summary = self.run_outcomes({"pair": snapshot()}, df)
        self.assertEqual(result["pair"]["outcomes"]["1"]["exit_date"], "2026-08-04")
        self.assertIsNone(result["pair"]["outcomes"]["3"]["exit_price"])
        self.assertIsNone(result["pair"]["outcomes"]["5"]["exit_price"])
        self.assertEqual(summary["errors"], 1)

    def test_collector_reports_missing_prices_and_continues_after_currency_error(self):
        def save(curr, *args):
            if curr == "USD":
                raise ValueError("simulated")
            return True
        self.ns["save_currency_snapshot"] = save
        summary = self.ns["save_all_g10_live_snapshots"]()
        self.assertEqual(summary["attempted"], 12)
        self.assertEqual(summary["written"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["skipped"], 10)
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertFalse(Path("live_signals.json").exists())

    def test_collector_rejects_stale_quote_and_accepts_current_observed_quote(self):
        self.ns["save_currency_snapshot"] = lambda *args: False
        self.ns["get_fcs_history_data"] = lambda *args: (prices(), None, True)
        summary = self.ns["save_all_g10_live_snapshots"]()
        self.assertEqual(summary["pairs"]["written"], 0)
        self.assertTrue(all(i["reason"] == "ENTRY_PRICE_STALE" for i in summary["pairs"]["issues"]))
        current = pd.DataFrame([{"date": "2026-09-04", "close": 100}])
        self.ns["get_fcs_history_data"] = lambda *args: (current, None, True)
        summary = self.ns["save_all_g10_live_snapshots"]()
        self.assertEqual(summary["pairs"]["written"], 10)
        self.assertEqual(summary["status"], "SUCCESS")
        rerun = self.ns["save_all_g10_live_snapshots"]()
        self.assertEqual(rerun["pairs"]["written"], 0)
        self.assertEqual(rerun["pairs"]["skipped"], 10)


if __name__ == "__main__":
    unittest.main()
