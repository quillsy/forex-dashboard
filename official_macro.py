"""Keyless Eurostat live observations; never an as-of historical data service.

Definitions: https://ec.europa.eu/eurostat/cache/metadata/en/une_rt_m_esms.htm
and https://ec.europa.eu/eurostat/cache/metadata/en/namq_10_gdp_esms.htm
API: https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics
EA21 is the current euro area (from 2026). API dataset update times are NOT
individual release dates. Their absence is deliberately represented by None.
"""
import calendar
import math
import re
from datetime import datetime, timezone

import requests

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
EUROSTAT_SPECS = {
    "Arbeitsmarkt": {
        "dataset": "une_rt_m",
        "filters": {"freq": "M", "s_adj": "SA", "age": "TOTAL", "unit": "PC_ACT", "sex": "T", "geo": "EA21"},
    },
    "GDP": {
        "dataset": "namq_10_gdp",
        "filters": {"freq": "Q", "unit": "CLV_PCH_SM", "s_adj": "SCA", "na_item": "B1GQ", "geo": "EA21"},
    },
}


def _utc_now(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("A timezone-aware check time is required")
    return now.astimezone(timezone.utc)


def _category_index(category):
    index = category.get("index")
    if isinstance(index, list):
        if len(set(index)) != len(index):
            raise ValueError("Duplicate categories")
        index = {name: pos for pos, name in enumerate(index)}
    if not isinstance(index, dict) or any(type(pos) is not int for pos in index.values()):
        raise ValueError("Invalid category index")
    if sorted(index.values()) != list(range(len(index))):
        raise ValueError("Conflicting category positions")
    return index


def _period_end(period, frequency):
    pattern = r"(\d{4})-(\d{2})" if frequency == "M" else r"(\d{4})-Q([1-4])"
    match = re.fullmatch(pattern, period)
    if not match:
        raise ValueError("Invalid reference period")
    year, subperiod = map(int, match.groups())
    month = subperiod if frequency == "M" else subperiod * 3
    return datetime(year, month, calendar.monthrange(year, month)[1], tzinfo=timezone.utc).date()


def parse_eurostat_observation(payload, category, *, now=None):
    """Return latest valid observation dict, or None when the selected series is empty.

    Rejects mismatched dimensions, wrong units, duplicate positions, future and
    non-finite values. Sparse null periods are not treated as new releases.
    Provisional/estimated official observations retain their provider status.
    """
    checked = _utc_now(now)
    spec = EUROSTAT_SPECS[category]
    filters = spec["filters"]
    if not isinstance(payload, dict) or payload.get("class") != "dataset" or payload.get("source") != "ESTAT":
        raise ValueError("Not a Eurostat dataset")
    if payload.get("extension", {}).get("id", "").lower() != spec["dataset"]:
        raise ValueError("Wrong dataset")
    ids, sizes = payload.get("id", []), payload.get("size", [])
    if len(ids) != len(set(ids)) or set(ids) != set(filters) | {"time"} or len(ids) != len(sizes):
        raise ValueError("Ambiguous dimensions")
    dimensions = payload.get("dimension", {})
    for dimension, expected in filters.items():
        index = _category_index(dimensions[dimension]["category"])
        if index != {expected: 0} or sizes[ids.index(dimension)] != 1:
            raise ValueError("Wrong series dimension: " + dimension)
    times = _category_index(dimensions["time"]["category"])
    if sizes[ids.index("time")] != len(times):
        raise ValueError("Time dimension mismatch")
    values = payload.get("value", {})
    if isinstance(values, list):
        if len(values) != len(times):
            raise ValueError("Value shape mismatch")
        values = {str(pos): value for pos, value in enumerate(values)}
    if not isinstance(values, dict) or any(key not in {str(i) for i in range(len(times))} for key in values):
        raise ValueError("Invalid observation positions")
    statuses = payload.get("status", {})
    if not isinstance(statuses, (dict, list)):
        raise ValueError("Invalid observation status")
    observations = []
    for period, pos in times.items():
        date = _period_end(period, filters["freq"])
        raw = values.get(str(pos))
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError("Invalid observation value")
        if date > checked.date():
            raise ValueError("Future reference period")
        if category == "Arbeitsmarkt" and not 0 <= raw <= 100:
            raise ValueError("Invalid unemployment percentage")
        if category == "GDP" and not -100 <= raw <= 200:
            raise ValueError("Invalid GDP growth")
        status = statuses.get(str(pos)) if isinstance(statuses, dict) else (statuses[pos] if pos < len(statuses) else None)
        if status and any(flag in str(status).split() for flag in ("c", ":")):
            raise ValueError("Confidential or unavailable observation")
        observations.append((date, period, float(raw), status))
    if not observations:
        return None
    date, period, value, status = max(observations)
    return {
        "value": value, "reference_period": period, "date": date.isoformat(),
        "source": "Eurostat", "source_url": EUROSTAT_BASE + spec["dataset"],
        "series_id": spec["dataset"] + ":" + ".".join(filters.values()),
        "frequency": filters["freq"], "unit": filters["unit"],
        "seasonal_adjustment": filters["s_adj"], "geography": "EA21",
        "checked_at": checked.isoformat(), "published_at": None,
        "provider_status": status,
    }


def fetch_eurostat_observation(category, *, now=None, session=None):
    """Fetch one current EUR factor, one public request, bounded timeout/no retries.

    ``now`` is an injectable clock for testing, not a historical as-of query.
    Requests and validation failures propagate; callers must preserve a verified
    prior value without falsely refreshing its checked_at timestamp.
    """
    checked = _utc_now(now)
    spec = EUROSTAT_SPECS[category]
    params = dict(spec["filters"], lang="EN", sinceTimePeriod=f"{checked.year - 1}-01" if category == "Arbeitsmarkt" else f"{checked.year - 1}-Q1")
    response = (session or requests).get(EUROSTAT_BASE + spec["dataset"], params=params, timeout=20)
    response.raise_for_status()
    return parse_eurostat_observation(response.json(), category, now=checked)
