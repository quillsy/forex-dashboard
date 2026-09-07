import unittest
from source_contracts import FRED_SERIES_CONTRACTS, validate_fred_metadata


def fixture(series_id):
    return {"seriess": [{"id": series_id, **{k: v for k, v in FRED_SERIES_CONTRACTS[series_id].items() if k != "category"}}]}


class FredContractTests(unittest.TestCase):
    def test_all_verified_contracts(self):
        for series_id, contract in FRED_SERIES_CONTRACTS.items():
            self.assertTrue(validate_fred_metadata(fixture(series_id), series_id, contract["category"]), series_id)

    def test_wrong_identity_category_and_labels(self):
        for field, value in [("id", "GDP"), ("title", "Nominal GDP"), ("units", "Percent"), ("frequency", "Monthly"), ("seasonal_adjustment", "Seasonally Adjusted"), ("frequency_short", "A"), ("seasonal_adjustment_short", "NSA")]:
            data = fixture("GDPC1"); data["seriess"][0][field] = value
            self.assertFalse(validate_fred_metadata(data, "GDPC1", "GDP"), field)
        self.assertFalse(validate_fred_metadata(fixture("GDPC1"), "GDPC1", "Inflation"))

    def test_fail_closed_unknown_missing_and_conflicting_metadata(self):
        for data in [None, [], {}, {"seriess": []}, {"seriess": [None]}, {"seriess": [{"id": "UNRATE"}]}, {"seriess": fixture("UNRATE")["seriess"] * 2}]:
            self.assertFalse(validate_fred_metadata(data, "UNRATE", "Arbeitsmarkt"))
        for series_id in ["LRUNTTTTGBM156S", "LRUNTTTTNZM156S", "LRUNTTTTCHM156S", "LRUNTTTTEZM156S", "UKNGDPM", "MANEMP", "UNKNOWN"]:
            self.assertFalse(validate_fred_metadata(fixture("UNRATE"), series_id, "Arbeitsmarkt"))

    def test_yoy_and_index_not_interchangeable(self):
        data = fixture("JPNGDPRQPSMEI"); data["seriess"][0]["units"] = "Growth rate previous period"
        self.assertFalse(validate_fred_metadata(data, "JPNGDPRQPSMEI", "GDP"))
        data = fixture("CPIAUCNS"); data["seriess"][0]["units"] = "Percent Change from Year Ago"
        self.assertFalse(validate_fred_metadata(data, "CPIAUCNS", "Inflation"))


if __name__ == "__main__": unittest.main()
