"""Fail-closed contracts for FRED metadata checked against /fred/series.

Official metadata and titles verified 2026-09-07. No wildcard ID acceptance:
LRUNTTTTEZM156S, LRUNTTTTGBM156S, LRUNTTTTCHM156S and LRUNTTTTNZM156S
returned HTTP 400 and have no approved contract. Unknown or changed metadata
needs explicit review. Validity here does not establish observation freshness.

GDPC1 is a real level (SAAR): calculate YoY separately using matching quarters.
CPIAUCNS is an NSA price index: request/calculate pc1 separately, never score its
raw index as inflation. Source: https://fred.stlouisfed.org/docs/api/fred/series.html
"""


def _contract(category, title, units, frequency, adjustment):
    return {"category": category, "title": title, "units": units,
            "frequency": frequency, "seasonal_adjustment": adjustment}


FRED_SERIES_CONTRACTS = {
    "UNRATE": _contract("Arbeitsmarkt", "Unemployment Rate", "Percent", "Monthly", "Seasonally Adjusted"),
    "GDPC1": _contract("GDP", "Real Gross Domestic Product", "Billions of Chained 2017 Dollars", "Quarterly", "Seasonally Adjusted Annual Rate"),
    "CPIAUCNS": _contract("Inflation", "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average", "Index 1982-1984=100", "Monthly", "Not Seasonally Adjusted"),
    "DGS2": _contract("Geldpolitik", "Market Yield on U.S. Treasury Securities at 2-Year Constant Maturity, Quoted on an Investment Basis", "Percent", "Daily", "Not Seasonally Adjusted"),
}
for _code, _country in {"JP": "Japan", "CA": "Canada", "AU": "Australia"}.items():
    FRED_SERIES_CONTRACTS[f"LRUNTTTT{_code}M156S"] = _contract(
        "Arbeitsmarkt", f"Infra-Annual Labor Statistics: Unemployment Rate Total: 15 Years or over for {_country}",
        "Percent", "Monthly", "Seasonally Adjusted")
for _code, _country in {"JPN": "Japan", "CHE": "Switzerland", "CAN": "Canada", "AUS": "Australia", "NZL": "New Zealand"}.items():
    FRED_SERIES_CONTRACTS[f"{_code}GDPRQPSMEI"] = _contract(
        "GDP", f"National Accounts: GDP by Expenditure: Constant Prices: Gross Domestic Product: Total for {_country}",
        "Growth rate same period previous year", "Quarterly", "Seasonally Adjusted")


def validate_fred_metadata(payload, series_id, category):
    """Return bool for one exact original-unit /fred/series JSON response.

    Require the singular ``seriess`` API container; conflicting/duplicate rows,
    missing identity, wrong factor, frequency, unit or adjustment return False.
    This function never makes requests or includes provider payloads in errors.
    """
    if not isinstance(series_id, str) or not isinstance(category, str):
        return False
    contract = FRED_SERIES_CONTRACTS.get(series_id)
    if not contract or contract["category"] != category or not isinstance(payload, dict):
        return False
    rows = payload.get("seriess")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    row = rows[0]
    if row.get("id") != series_id:
        return False
    if not all(row.get(field) == expected for field, expected in contract.items() if field != "category"):
        return False
    # Short labels are optional, but if supplied they must agree with full labels.
    expected_short = {
        "frequency_short": {"Daily": "D", "Monthly": "M", "Quarterly": "Q"}[contract["frequency"]],
        "seasonal_adjustment_short": {"Seasonally Adjusted": "SA", "Not Seasonally Adjusted": "NSA", "Seasonally Adjusted Annual Rate": "SAAR"}[contract["seasonal_adjustment"]],
    }
    return all(field not in row or row[field] == expected for field, expected in expected_short.items())
