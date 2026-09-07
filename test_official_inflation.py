import unittest
from unittest.mock import Mock

import requests

from official_inflation import fetch_official_cpi, parse_abs_cpi, parse_estat_cpi

NOW = "2026-09-07T12:00:00+00:00"


def estat():
    classes = [("tab", "3", "前年同月比"), ("cat01", "0001", "0001 総合"), ("area", "00000", "全国")]
    return {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {
        "TABLE_INF": {"@id": "0004052037", "STAT_NAME": {"@code": "00200573"}},
        "CLASS_INF": {"CLASS_OBJ": [{"@id": id_, "CLASS": {"@code": code, "@name": name, "@unit": "%"}}
                                   for id_, code, name in classes]},
        "DATA_INF": {"VALUE": [{"@tab": "3", "@cat01": "0001", "@area": "00000",
                                "@unit": "%", "@time": "2026000707", "$": "1.9"}]}}}}


def abs_data():
    dims = [("MEASURE", "3", "Percentage change from previous year"), ("INDEX", "10001", "All groups CPI"),
            ("TSEST", "10", "Original"), ("REGION", "50", "Australia"), ("FREQ", "M", "Monthly")]
    return {"dataSets": [{"series": {"0:0:0:0:0": {"attributes": [0], "observations": {"0": [3.5], "1": [3.8]}}}}],
        "structure": {"dimensions": {"series": [{"id": id_, "values": [{"id": code, "name": name}]}
                                                for id_, code, name in dims],
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2026-07"}, {"id": "2026-06"}]}]},
            "attributes": {"series": [{"id": "UNIT_MEASURE", "values": [{"id": "PCT", "name": "Percent"}]}]}}}


class OfficialInflationTests(unittest.TestCase):
    def test_correct_latest_and_unknown_publication(self):
        for parse, fixture, value in [(parse_estat_cpi, estat, 1.9), (parse_abs_cpi, abs_data, 3.5)]:
            row = parse(fixture(), NOW)
            self.assertEqual(row["value"], value)
            self.assertEqual(row["refperiod"], "2026-07")
            self.assertIsNone(row["published_at"])

    def test_estat_rejects_old_base_wrong_unit_and_area(self):
        for path, value in [("table", "0003427113"), ("unit", "index"), ("area", "13100")]:
            data = estat(); root = data["GET_STATS_DATA"]["STATISTICAL_DATA"]
            if path == "table": root["TABLE_INF"]["@id"] = value
            if path == "unit": root["CLASS_INF"]["CLASS_OBJ"][0]["CLASS"]["@unit"] = value
            if path == "area": root["DATA_INF"]["VALUE"][0]["@area"] = value
            with self.subTest(path=path), self.assertRaises(ValueError): parse_estat_cpi(data, NOW)

    def test_estat_quarterly_and_future_are_not_monthly(self):
        for period in ["2026000709", "2026000000", "2026000909", "2026001010"]:
            data = estat(); data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["@time"] = period
            self.assertIsNone(parse_estat_cpi(data, NOW))

    def test_abs_identity_unit_and_quarterly_rejected(self):
        for index in range(5):
            data = abs_data(); data["structure"]["dimensions"]["series"][index]["values"][0]["id"] = "wrong"
            with self.subTest(index=index), self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
        data = abs_data(); data["structure"]["attributes"]["series"][0]["values"][0]["id"] = "IX"
        with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
        data = abs_data(); data["structure"]["dimensions"]["observation"][0]["values"][0]["id"] = "2026-Q2"
        with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)

    def test_abs_future_ignored(self):
        data = abs_data(); data["structure"]["dimensions"]["observation"][0]["values"][0]["id"] = "2026-10"
        self.assertEqual(parse_abs_cpi(data, NOW)["refperiod"], "2026-06")

    def test_nonfinite_and_conflicting_values_fail(self):
        for value in [float("nan"), float("inf"), 26]:
            data = abs_data(); data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = [value]
            with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
            data = estat(); data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["$"] = value
            with self.assertRaises(ValueError): parse_estat_cpi(data, NOW)
        data = estat(); values = data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
        values.append({**values[0], "$": "2.1"})
        with self.assertRaises(ValueError): parse_estat_cpi(data, NOW)

    def test_transport_injection_and_no_legacy_fallback(self):
        client = Mock(); client.get.return_value.json.return_value = estat()
        self.assertEqual(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW)["value"], 1.9)
        self.assertEqual(client.get.call_args.kwargs["params"]["statsDataId"], "0004052037")
        client.get.reset_mock(); client.get.return_value.raise_for_status.side_effect = requests.HTTPError()
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW))
        self.assertEqual(client.get.call_count, 1)
        client.get.reset_mock()
        self.assertIsNone(fetch_official_cpi("JPY", client=client, now=NOW)); client.get.assert_not_called()


if __name__ == "__main__": unittest.main()
