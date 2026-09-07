import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from official_macro import EUROSTAT_SPECS, parse_eurostat_observation, fetch_eurostat_observation

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def fixture(category):
    spec = EUROSTAT_SPECS[category]
    times = ["2026-06", "2026-07", "2026-08"] if category == "Arbeitsmarkt" else ["2026-Q1", "2026-Q2"]
    dimensions = {name: {"category": {"index": {code: 0}}} for name, code in spec["filters"].items()}
    dimensions["time"] = {"category": {"index": dict(zip(times, range(len(times))))}}
    return {"class": "dataset", "source": "ESTAT", "extension": {"id": spec["dataset"].upper()}, "id": list(dimensions), "size": [1] * len(spec["filters"]) + [len(times)], "dimension": dimensions, "value": {"0": 6.4, "1": 6.4} if category == "Arbeitsmarkt" else {"0": 0.6, "1": 1.2}}


class EurostatTests(unittest.TestCase):
    def test_actual_shapes_and_unknown_publication(self):
        for category, expected in [("Arbeitsmarkt", (6.4, "2026-07-31", "PC_ACT")), ("GDP", (1.2, "2026-06-30", "CLV_PCH_SM"))]:
            result = parse_eurostat_observation(fixture(category), category, now=NOW)
            self.assertEqual((result["value"], result["date"], result["unit"]), expected)
            self.assertIsNone(result["published_at"])
            self.assertEqual(result["geography"], "EA21")

    def test_dimensions_are_exact(self):
        for dimension, wrong in [("unit", "CLV_PCH_PRE"), ("geo", "EA20"), ("s_adj", "NSA"), ("na_item", "P3")]:
            data = fixture("GDP")
            data["dimension"][dimension]["category"]["index"] = {wrong: 0}
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_bad_values_and_future_quarter_rejected(self):
        for value in [float("nan"), float("inf"), True, "1.2", -101]:
            data = fixture("GDP"); data["value"]["1"] = value
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)
        data = fixture("GDP")
        data["dimension"]["time"]["category"]["index"] = {"2026-Q1": 0, "2026-Q3": 1}
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_empty_null_and_positions(self):
        data = fixture("GDP"); data["value"] = {}
        self.assertIsNone(parse_eurostat_observation(data, "GDP", now=NOW))
        for corrupt in [{"2026-Q1": 0, "2026-Q2": 0}, {"2026-Q1": 0, "2026-Q2": 2}]:
            data = fixture("GDP"); data["dimension"]["time"]["category"]["index"] = corrupt
            with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)
        data = fixture("GDP"); data["value"]["01"] = 99
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_provisional_retained_confidential_rejected(self):
        data = fixture("GDP"); data["status"] = {"1": "p"}
        self.assertEqual(parse_eurostat_observation(data, "GDP", now=NOW)["provider_status"], "p")
        data["status"]["1"] = "c"
        with self.assertRaises(ValueError): parse_eurostat_observation(data, "GDP", now=NOW)

    def test_single_bounded_keyless_request(self):
        session = Mock(); session.get.return_value.json.return_value = fixture("GDP")
        result = fetch_eurostat_observation("GDP", now=NOW, session=session)
        self.assertEqual(result["value"], 1.2)
        session.get.assert_called_once()
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(kwargs["params"]["unit"], "CLV_PCH_SM")
        self.assertNotIn("api_key", kwargs["params"])
        session.get.return_value.raise_for_status.assert_called_once()


if __name__ == "__main__": unittest.main()
