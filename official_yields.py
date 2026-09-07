"""Keyless official coupon-government yield readers; no estimated spot proxies."""

import csv
from datetime import date, datetime, timezone
import io
import math
import re

import requests


JAPAN_MOF_CURRENT_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def parse_japan_mof_2y(csv_text, target_date, now=None):
    """Return latest eligible MOF 2Y coupon CMT observation, or None.

    MOF publishes the previous business day's closing yields at 09:30 JST.
    Observation dates are not publication timestamps. This parser deliberately
    does not infer a successful release time from a scheduled release time.
    Invalid schemas and conflicting duplicate observations fail closed.
    """
    target = min(_date(target_date), _date(now or datetime.now(timezone.utc)))
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
    if len(rows) < 2:
        raise ValueError("MOF_SCHEMA_INVALID")
    metadata = [cell.strip() for cell in rows[0]]
    if not metadata[0].startswith("Interest Rate (") or "(Unit : %)" not in metadata:
        raise ValueError("MOF_UNIT_INVALID")
    header = [cell.strip() for cell in rows[1]]
    if header.count("Date") != 1 or header.count("2Y") != 1 or header[0] != "Date":
        raise ValueError("MOF_SERIES_INVALID")
    index = header.index("2Y")
    observations = {}
    for row in rows[2:]:
        if not row or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", row[0].strip()):
            # The published file has blank lines and a cache-help footer.
            continue
        year, month, day = map(int, row[0].strip().split("/"))
        observed = date(year, month, day)
        if observed > target:
            continue
        if len(row) <= index:
            raise ValueError("MOF_OBSERVATION_INVALID")
        raw = row[index].strip()
        if raw in {"", "-", "--"}:
            value = None
        else:
            value = float(raw)
            if not math.isfinite(value) or not -5 <= value <= 30:
                raise ValueError("MOF_OBSERVATION_INVALID")
        if observed in observations and observations[observed] != value:
            raise ValueError("MOF_CONFLICTING_OBSERVATION")
        observations[observed] = value
    eligible = {day: value for day, value in observations.items() if value is not None}
    if not eligible:
        return None
    observed = max(eligible)
    return {
        "value": eligible[observed],
        "observation_date": observed.isoformat(),
        "source": "Japan MOF 2Y constant maturity",
        "series_id": "2Y",
        "unit": "percent_per_annum",
        "source_url": JAPAN_MOF_CURRENT_URL,
    }


def fetch_japan_mof_2y(target_date, client=None, now=None, timeout=15):
    """Fetch current-month data with an injectable requests-compatible client.

    No history endpoint is queried implicitly. Older target dates can therefore
    return None. Transport errors never expose response bodies or request URLs.
    """
    try:
        response = (client or requests).get(JAPAN_MOF_CURRENT_URL, timeout=timeout)
        response.raise_for_status()
        return parse_japan_mof_2y(response.text, target_date, now=now)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return None
