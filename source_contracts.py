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


# Confirmed official releases: a successful mirror fetch cannot make an older
# reference period current again. These are minimum known periods, NOT a
# complete release calendar and never a substitute for ongoing source checks.
KNOWN_RELEASES = {
    ("AUD", "GDP"): {
        "period_start": "2026-04-01", "published_at": "2026-09-02T01:30:00+00:00",
        "label": "2026-Q2",
        "source": "https://www.abs.gov.au/statistics/economy/national-accounts/australian-national-accounts-national-income-expenditure-and-product/jun-2026",
    },
    ("AUD", "Arbeitsmarkt"): {
        "period_start": "2026-07-01", "published_at": "2026-08-20T01:30:00+00:00",
        "label": "2026-07",
        "source": "https://www.abs.gov.au/statistics/labour/employment-and-unemployment/labour-force-australia/jul-2026",
    },
}

# Only the publication date is evidenced for these releases. The guard becomes
# effective at this audit's observation time; do not invent publication times
# or use this audit as historical intraday knowledge.
KNOWN_RELEASES.update({
    ("JPY", "GDP"): {"period_start": "2026-04-01", "label": "2026-Q2",
        "release_date_known": "2026-08-17", "confirmed_at": "2026-09-08T09:39:42+00:00",
        "source": "https://www.esri.cao.go.jp/en/news/index.html"},
    ("JPY", "Arbeitsmarkt"): {"period_start": "2026-07-01", "label": "2026-07",
        "release_date_known": "2026-08-28", "confirmed_at": "2026-09-08T09:39:42+00:00",
        "source": "https://www.stat.go.jp/english/data/roudou/result.html"},
    ("CHF", "GDP"): {"period_start": "2026-04-01", "label": "2026-Q2",
        "release_date_known": "2026-09-03", "confirmed_at": "2026-09-08T09:39:42+00:00",
        "source": "https://www.seco.admin.ch/en/gross-domestic-product"},
})

KNOWN_RELEASES[("CAD", "Arbeitsmarkt")] = {
    "period_start": "2026-08-01", "label": "2026-08",
    "published_at": "2026-09-04T12:30:00+00:00",
    "source": "https://www150.statcan.gc.ca/n1/daily-quotidien/260904/dq260904a-eng.htm",
    "time_basis": "08:30 Eastern official rule; https://www.statcan.gc.ca/en/bcp/daily-key-data-tables",
}

KNOWN_RELEASES[("USD", "Geldpolitik")] = {
    "period_start": "2026-09-04", "label": "2026-09-04",
    "confirmed_at": "2026-09-08T10:00:12+00:00",
    "source": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=202609",
}

# Same definition/period but unresolved disagreement between official API and
# official publication. Never select whichever number produces a desired score.
KNOWN_SOURCE_CONFLICTS = {
    ("EUR", "Inflation", "2026-08"): {
        "confirmed_at": "2026-09-08T10:00:12+00:00",
        "reason": "Amtlicher Quellenkonflikt: Eurostat-API 3,2 %; Veröffentlichung 3,3 % (August 2026)",
        "sources": ["https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr",
                    "https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-01092026-ap"],
    },
}

KNOWN_SOURCE_CONFLICTS[("EUR", "Inflation", "2026-07")] = {
    **KNOWN_SOURCE_CONFLICTS[("EUR", "Inflation", "2026-08")],
    "reason": "Amtlicher Quellenkonflikt: Eurostat-API 3,0 %; Veröffentlichung 2,9 % (Juli 2026)",
}

KNOWN_RELEASES[("CHF", "Inflation")] = {
    "period_start": "2026-08-01", "label": "2026-08 (HICP/HVPI)",
    "published_at": "2026-09-03T06:30:00+00:00",
    "source": "https://dam-api.bfs.admin.ch/hub/api/dam/assets/36835032/master",
}
