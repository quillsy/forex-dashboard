"""Keyless ONS quarterly real GDP YoY; original IHYR, not annual IHYP.

https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ihyr/pn2
ONS releaseDate represents a release calendar date at local midnight, not an
observed publication time. Keep published_at unknown and check hourly.
"""
import calendar
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

BASE = "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ihyr/"
PN2_FALLBACK_URL = "https://api.beta.ons.gov.uk/v1/data?uri=/economy/grossdomesticproductgdp/timeseries/ihyr/pn2"
TITLE = "Gross Domestic Product: q-on-q4 growth rate CVM SA %"
# The Q2 national-accounts revision has the same reference period as PN2's
# first estimate. Its announced 07:00 Europe/London release is 06:00 UTC.
# https://www.ons.gov.uk/releases/gdpquarterlynationalaccountsukapriltojune2026
Q2_2026_REVISION_DUE = datetime(2026, 9, 30, 6, tzinfo=timezone.utc)
Q2_2026_REVISION_DATE = "2026-09-30"
Q2_2026_END = "2026-06-30"


def _now(now):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC_CHECK_TIME_REQUIRED")
    return now.astimezone(timezone.utc)


def parse_ons_gdp(payload, dataset, *, now=None):
    """Return latest IHYR observation or None; invalid metadata raises ValueError."""
    checked = _now(now)
    if dataset not in ("PN2", "QNA") or not isinstance(payload, dict):
        raise ValueError("ONS_DATASET_INVALID")
    meta = payload.get("description", {})
    if any(meta.get(k) != v for k, v in {"cdid": "IHYR", "datasetId": dataset, "title": TITLE, "unit": "%"}.items()):
        raise ValueError("ONS_SERIES_MISMATCH")
    release = datetime.fromisoformat(str(meta.get("releaseDate")).replace("Z", "+00:00"))
    if release.tzinfo is None:
        raise ValueError("ONS_RELEASE_DATE_INVALID")
    release_day = release.astimezone(ZoneInfo("Europe/London")).date()
    if release_day > checked.astimezone(ZoneInfo("Europe/London")).date():
        raise ValueError("ONS_FUTURE_RELEASE")
    # releaseDate is only a calendar date. An early API update on the morning
    # of the scheduled bulletin cannot be treated as already published.
    if release_day.isoformat() >= Q2_2026_REVISION_DATE and checked < Q2_2026_REVISION_DUE:
        raise ValueError("ONS_FUTURE_RELEASE")
    quarters = payload.get("quarters")
    if not isinstance(quarters, list):
        raise ValueError("ONS_QUARTERS_MISSING")
    values = {}
    for row in quarters:
        if not isinstance(row, dict) or row.get("sourceDataset") != dataset:
            raise ValueError("ONS_ROW_DATASET_MISMATCH")
        match = re.fullmatch(r"(\d{4}) Q([1-4])", str(row.get("date", "")))
        if not match or row.get("label") != row["date"]:
            raise ValueError("ONS_QUARTER_INVALID")
        year, quarter = map(int, match.groups())
        if row.get("year") != str(year) or row.get("quarter") != f"Q{quarter}":
            raise ValueError("ONS_QUARTER_CONFLICT")
        month = quarter * 3
        end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
        raw = row.get("value")
        if raw in (None, ""):
            continue
        if isinstance(raw, bool):
            raise ValueError("ONS_VALUE_INVALID")
        value = float(raw)
        if not math.isfinite(value) or not -100 <= value <= 200:
            raise ValueError("ONS_VALUE_INVALID")
        if end > checked.date() or end > release_day:
            raise ValueError("ONS_FUTURE_QUARTER")
        if end in values and values[end] != value:
            raise ValueError("ONS_CONFLICTING_QUARTER")
        values[end] = value
    if not values:
        return None
    end = max(values)
    return {"value": values[end], "date": end.isoformat(),
            "reference_period": f"{end.year}-Q{(end.month - 1) // 3 + 1}",
            "source": "ONS", "source_url": BASE + dataset.lower(),
            "series_id": "IHYR/" + dataset, "frequency": "quarterly",
            "unit": "real GDP YoY percent", "seasonal_adjustment": "SA",
            "checked_at": checked.isoformat(), "published_at": None,
            "release_date_known": release_day.isoformat()}


def fetch_ons_gdp(*, now=None, session=None):
    """Check both ONS publication families; fail closed if either check fails.

    A stale family cannot prove latest availability while the other is down.
    Same-quarter newer releases are legitimate revisions; conflicting values
    with the same release date reject the observation. Only PN2 transport
    outages may use its official API route once, with the same timeout. Parsing
    and publication dates stay unchanged; no alternate QNA route is approved.
    """
    checked = _now(now)
    results = []
    for dataset in ("PN2", "QNA"):
        client = session or requests
        used_fallback = False
        try:
            response = client.get(BASE + dataset.lower() + "/data", timeout=20)
            response.raise_for_status()
        except requests.exceptions.SSLError:
            raise
        except (requests.Timeout, requests.ConnectionError):
            if dataset != "PN2":
                raise
            used_fallback = True
            response = client.get(PN2_FALLBACK_URL, timeout=20)
            response.raise_for_status()
        except requests.HTTPError as exc:
            if dataset != "PN2" or exc.response is None or exc.response.status_code not in (500, 502, 504):
                raise
            used_fallback = True
            response = client.get(PN2_FALLBACK_URL, timeout=20)
            response.raise_for_status()
        observation = parse_ons_gdp(response.json(), dataset, now=checked)
        if observation:
            if used_fallback:
                observation["source_url"] = PN2_FALLBACK_URL
            results.append(observation)
    if not results:
        return None
    # Check conflicts even if one family has an older latest period.
    by_release = {}
    for result in results:
        key = (result["date"], result["release_date_known"])
        if key in by_release and by_release[key] != result["value"]:
            return None
        by_release[key] = result["value"]
    latest = max(results, key=lambda result: (result["date"], result["release_date_known"]))
    if checked >= Q2_2026_REVISION_DUE and latest["date"] <= Q2_2026_END:
        # A successful PN2 fetch still carries the first Q2 estimate. Require
        # the QNA family itself to contain Q2 in the new release vintage; a
        # changed numeric value is not required because a revision may be zero.
        revised = next((result for result in results
                        if result["series_id"] == "IHYR/QNA"
                        and result["date"] == Q2_2026_END
                        and result["release_date_known"] >= Q2_2026_REVISION_DATE), None)
        if revised is None:
            return None
        # PN2 can share the release date, so a plain max() would keep the
        # first (PN2) item. A still later conflicting PN2 value is unresolved.
        if any(result["date"] == Q2_2026_END
               and result["release_date_known"] >= revised["release_date_known"]
               and result["value"] != revised["value"] for result in results):
            return None
        return revised
    return latest


LABOUR_URL = "https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms/data"
MONTHS = {name: pos for pos, name in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), 1)}

CPI_TITLE = 'CPI ANNUAL RATE 00: ALL ITEMS 2015=100'


def validate_ons_cpi(payload, *, now=None):
    """Validate D7G7/MM23 annual headline CPI before the existing PIT loader.

    The title names the index base, but D7G7 itself is a 12-month percentage
    change. ONS releaseDate is a calendar date at London midnight, not proof
    of the publication hour. Do not replace the loader's historical dates.
    """
    checked = _now(now)
    if not isinstance(payload, dict):
        raise ValueError('ONS_CPI_PAYLOAD_INVALID')
    meta = payload.get('description')
    expected = {'cdid': 'D7G7', 'datasetId': 'MM23', 'title': CPI_TITLE, 'unit': '%'}
    if not isinstance(meta, dict) or any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError('ONS_CPI_SERIES_MISMATCH')
    if str(meta.get('keyNote', '')).strip() != 'Change over 12 months':
        raise ValueError('ONS_CPI_MEASUREMENT_MISMATCH')
    try:
        release = datetime.fromisoformat(str(meta.get('releaseDate')).replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('ONS_CPI_RELEASE_DATE_INVALID') from None
    if release.tzinfo is None:
        raise ValueError('ONS_CPI_RELEASE_DATE_INVALID')
    release_day = release.astimezone(ZoneInfo('Europe/London')).date()
    if release_day > checked.astimezone(ZoneInfo('Europe/London')).date():
        raise ValueError('ONS_FUTURE_RELEASE')
    rows = payload.get('months')
    if not isinstance(rows, list) or not rows:
        raise ValueError('ONS_MONTHS_MISSING')
    values = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('sourceDataset') != 'MM23':
            raise ValueError('ONS_ROW_DATASET_MISMATCH')
        match = re.fullmatch(r'(\d{4}) ([A-Z]{3})', str(row.get('date', '')))
        if not match or match[2] not in MONTHS:
            raise ValueError('ONS_MONTH_INVALID')
        year, month = int(match[1]), MONTHS[match[2]]
        if (row.get('label') != row['date'] or row.get('year') != str(year)
                or row.get('month') != calendar.month_name[month]):
            raise ValueError('ONS_MONTH_CONFLICT')
        period = datetime(year, month, 1).date()
        period_end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
        if period_end > checked.date() or period_end > release_day:
            raise ValueError('ONS_FUTURE_MONTH')
        raw = row.get('value')
        if raw in (None, ''):
            continue
        if isinstance(raw, bool):
            raise ValueError('ONS_VALUE_INVALID')
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError('ONS_VALUE_INVALID') from None
        if not math.isfinite(value) or value < -100:
            raise ValueError('ONS_VALUE_INVALID')
        if row['date'] in values and values[row['date']][1] != value:
            raise ValueError('ONS_CONFLICTING_PERIOD')
        values[row['date']] = (period, value)
    if not values:
        raise ValueError('ONS_CPI_VALUES_MISSING')
    latest = max(values, key=lambda key: values[key][0])
    summary = meta.get('number')
    try:
        summary_value = float(summary)
    except (TypeError, ValueError):
        raise ValueError('ONS_CPI_SUMMARY_MISMATCH') from None
    if (isinstance(summary, bool) or meta.get('date') != latest
            or not math.isfinite(summary_value) or summary_value != values[latest][1]):
        raise ValueError('ONS_CPI_SUMMARY_MISMATCH')
    return rows


def parse_ons_labour(payload, *, now=None):
    """MGSX rolling three-month unemployment, labelled by actual period ends."""
    checked = _now(now)
    meta = payload.get('description', {})
    expected = {'cdid': 'MGSX', 'datasetId': 'LMS', 'title': 'Unemployment rate (aged 16 and over, seasonally adjusted): %',
                'unit': '%', 'monthLabelStyle': 'three month average'}
    if any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError('ONS_LABOUR_SERIES_MISMATCH')
    release = datetime.fromisoformat(str(meta.get('releaseDate')).replace('Z', '+00:00'))
    if release.tzinfo is None:
        raise ValueError('ONS_RELEASE_DATE_INVALID')
    release_day = release.astimezone(ZoneInfo('Europe/London')).date()
    if release_day > checked.astimezone(ZoneInfo('Europe/London')).date():
        raise ValueError('ONS_FUTURE_RELEASE')
    rows = payload.get('months')
    if not isinstance(rows, list):
        raise ValueError('ONS_MONTHS_MISSING')
    values = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('sourceDataset') != 'LMS':
            raise ValueError('ONS_ROW_DATASET_MISMATCH')
        # ONS monthly date is the middle month, not the observation period end.
        match = re.fullmatch(r'(\d{4}) ([A-Z]{3})', str(row.get('date', '')))
        if not match or match[2] not in MONTHS:
            raise ValueError('ONS_MONTH_INVALID')
        year, month = int(match[1]), MONTHS[match[2]]
        serial = year * 12 + month - 1
        start_year, start_zero = divmod(serial - 1, 12)
        end_year, end_zero = divmod(serial + 1, 12)
        start_month, end_month = start_zero + 1, end_zero + 1
        start = datetime(start_year, start_month, 1).date()
        end = datetime(end_year, end_month, calendar.monthrange(end_year, end_month)[1]).date()
        # ONS labels use the starting year, including cross-year periods (2025 DEC-FEB).
        expected_label = (f'{start_year} {calendar.month_abbr[start_month].upper()}-'
                          f'{calendar.month_abbr[end_month].upper()}')
        if row.get('label') != expected_label:
            raise ValueError('ONS_ROLLING_PERIOD_MISMATCH')
        raw = row.get('value')
        if raw in (None, ''):
            continue
        if isinstance(raw, bool):
            raise ValueError('ONS_VALUE_INVALID')
        value = float(raw)
        if not math.isfinite(value) or not 0 <= value <= 100 or end > checked.date() or end > release_day:
            raise ValueError('ONS_VALUE_OR_PERIOD_INVALID')
        if end in values and values[end][0] != value:
            raise ValueError('ONS_CONFLICTING_PERIOD')
        values[end] = (value, start)
    if not values:
        return None
    end = max(values)
    value, start = values[end]
    return {'value': value, 'date': end.isoformat(), 'reference_period': f'{start.isoformat()}/{end.isoformat()}',
            'source': 'ONS', 'source_url': LABOUR_URL, 'series_id': 'MGSX/LMS',
            'frequency': 'rolling_three_month_monthly_release', 'unit': 'percent labour force age 16+',
            'seasonal_adjustment': 'SA', 'checked_at': checked.isoformat(), 'published_at': None,
            'release_date_known': release_day.isoformat()}


def fetch_ons_labour(*, now=None, session=None):
    checked = _now(now)
    response = (session or requests).get(LABOUR_URL, timeout=20)
    response.raise_for_status()
    return parse_ons_labour(response.json(), now=checked)
