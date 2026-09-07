"""Offline checks for Japan MOF identity, date and failure handling."""

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

import requests

from official_yields import JAPAN_MOF_CURRENT_URL, fetch_japan_mof_2y, parse_japan_mof_2y


FIXTURE = """Interest Rate (September 2026),,,(Unit : %)
Date,1Y,2Y,3Y
2026/9/3,1.563,1.85,1.994
2026/9/4,1.546,1.83,1.955
,,,
"If you cannot download the latest csv data, clear the browser cache.",,,
"""
NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


class JapanMofYieldTests(unittest.TestCase):
    def parse(self, text=FIXTURE, target="2026-09-07", now=NOW):
        return parse_japan_mof_2y(text, target, now=now)

    def test_exact_series_value_and_provenance(self):
        result = self.parse()
        self.assertEqual(result["value"], 1.83)
        self.assertEqual(result["observation_date"], "2026-09-04")
        self.assertEqual(result["source"], "Japan MOF 2Y constant maturity")
        self.assertEqual(result["source_url"], JAPAN_MOF_CURRENT_URL)
        self.assertNotIn("publication_date", result)

    def test_target_and_now_both_bound_selection(self):
        self.assertEqual(self.parse(target="2026-09-03")["value"], 1.85)
        self.assertEqual(self.parse(target="2099-01-01", now="2026-09-03")["value"], 1.85)
        self.assertIsNone(self.parse(target="2026-08-31"))

    def test_units_and_identity_are_required(self):
        for text in (FIXTURE.replace("Unit : %", "Unit : basis points"),
                     FIXTURE.replace("Date,1Y,2Y,3Y", "Date,1Y,20Y,3Y"),
                     FIXTURE.replace("Date,1Y,2Y,3Y", "Date,2Y,2Y,3Y")):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                self.parse(text)

    def test_nonfinite_and_implausible_values_rejected(self):
        for value in ("NaN", "inf", "-inf", "30.01", "-5.01", "oops"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(FIXTURE.replace(",1.83,", f",{value},"))

    def test_zero_and_negative_yields_are_valid(self):
        for value in (0, -0.25, -5, 30):
            with self.subTest(value=value):
                self.assertEqual(self.parse(FIXTURE.replace(",1.83,", f",{value},"))["value"], value)

    def test_conflicts_rejected_identical_duplicates_allowed(self):
        self.assertEqual(self.parse(FIXTURE + "2026/9/4,1.546,1.83,1.955\n")["value"], 1.83)
        for value in ("1.84", "-"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(FIXTURE + f"2026/9/4,1.546,{value},1.955\n")

    def test_missing_observation_is_not_zero(self):
        self.assertEqual(self.parse(FIXTURE.replace(",1.83,", ",-,"))["observation_date"], "2026-09-03")

    def test_injected_transport_and_failure(self):
        client = Mock()
        client.get.return_value.text = FIXTURE
        self.assertEqual(fetch_japan_mof_2y("2026-09-07", client=client, now=NOW)["value"], 1.83)
        client.get.assert_called_once_with(JAPAN_MOF_CURRENT_URL, timeout=15)
        client.get.return_value.raise_for_status.side_effect = requests.HTTPError("private details")
        self.assertIsNone(fetch_japan_mof_2y("2026-09-07", client=client, now=NOW))


if __name__ == "__main__":
    unittest.main()
