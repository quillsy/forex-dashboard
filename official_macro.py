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


# ABS Data API is keyless. Definitions verified from the two dataflow codelists.
# Reuse: https://www.abs.gov.au/website-privacy-copyright-and-disclaimer (CC BY 4.0).
ABS_BASE = 'https://data.api.abs.gov.au/rest/data/'
ABS_SPECS = {
    'Arbeitsmarkt': {
        'flow': 'LF', 'key': 'M13.3.1599.20.AUS.M',
        'dimensions': {'MEASURE': 'M13', 'SEX': '3', 'AGE': '1599', 'TSEST': '20', 'REGION': 'AUS', 'FREQ': 'M'},
        'unit': 'PCT', 'multiplier': '0',
        'release': 'https://www.abs.gov.au/statistics/labour/employment-and-unemployment/labour-force-australia/latest-release',
        'title': 'Labour Force, Australia',
    },
    'GDP': {
        'flow': 'ANA_AGG', 'key': 'M1.GPM.20.AUS.Q',
        'dimensions': {'MEASURE': 'M1', 'DATA_ITEM': 'GPM', 'TSEST': '20', 'REGION': 'AUS', 'FREQ': 'Q'},
        'unit': 'AUD', 'multiplier': '6',
        'release': 'https://www.abs.gov.au/statistics/economy/national-accounts/australian-national-accounts-national-income-expenditure-and-product/latest-release',
        'title': 'Australian National Accounts: National Income, Expenditure and Product',
    },
}


def parse_abs_observation(content, category, *, now=None):
    """Validate a single exact ABS CSV series; GDP levels become matching-quarter YoY.

    Rows may be unordered. Missing/nonfinite latest rows must never fall back to
    an older observation. All supplied rows must satisfy the same unit contract.
    """
    import csv
    import io
    checked, spec = _utc_now(now), ABS_SPECS[category]
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    required = {'DATAFLOW', 'TIME_PERIOD', 'OBS_VALUE', 'UNIT_MEASURE', 'UNIT_MULT', 'OBS_STATUS'} | set(spec['dimensions'])
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not required <= set(fields):
        raise ValueError('Invalid ABS CSV columns')
    rows = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Malformed ABS CSV row')
        if row['DATAFLOW'] != f"ABS:{spec['flow']}(1.0.0)" or any(row[k] != v for k, v in spec['dimensions'].items()):
            raise ValueError('Wrong ABS series')
        if row['UNIT_MEASURE'] != spec['unit'] or row['UNIT_MULT'] != spec['multiplier']:
            raise ValueError('Wrong ABS unit')
        period = row['TIME_PERIOD']
        end = _period_end(period, spec['dimensions']['FREQ'])
        if end > checked.date() or period in rows:
            raise ValueError('Future or duplicate ABS period')
        try:
            value = float(row['OBS_VALUE'])
        except (ValueError, TypeError):
            raise ValueError('Missing ABS observation') from None
        if not math.isfinite(value) or row['OBS_STATUS'] not in ('', 'p', 'r'):
            raise ValueError('Invalid ABS observation')
        if (category == 'Arbeitsmarkt' and not 0 <= value <= 100) or (category == 'GDP' and value <= 0):
            raise ValueError('Out of range ABS observation')
        rows[period] = (end, value, row['OBS_STATUS'])
    if not rows:
        raise ValueError('Empty ABS series')
    period = max(rows)
    end, value, status = rows[period]
    if category == 'GDP':
        prior_period = str(int(period[:4]) - 1) + period[4:]
        if prior_period not in rows:
            raise ValueError('Missing matching ABS prior-year quarter')
        value = 100 * (value / rows[prior_period][1] - 1)
        if not -100 <= value <= 200:
            raise ValueError('Out of range ABS GDP growth')
        status = 'p' if 'p' in (status, rows[prior_period][2]) else status or rows[prior_period][2]
    return {
        'value': value, 'reference_period': period, 'date': end.isoformat(),
        'source': 'ABS', 'source_url': ABS_BASE + spec['flow'] + '/' + spec['key'],
        'series_id': spec['flow'] + ':' + spec['key'],
        'frequency': spec['dimensions']['FREQ'],
        'unit': 'percent YoY' if category == 'GDP' else 'percent of labour force',
        'seasonal_adjustment': 'SA', 'geography': 'AUS',
        'checked_at': checked.isoformat(), 'published_at': None, 'next_due_at': None,
        'provider_status': status or None,
        'reuse_terms': 'https://www.abs.gov.au/website-privacy-copyright-and-disclaimer',
        'transformation': '100 * (current / matching prior-year quarter - 1)' if category == 'GDP' else 'none',
    }


def _abs_release_metadata(html, category, observation, checked):
    """Require API period to match the independently fetched official release.

    Date-only future releases use a conservative start-of-day blocking deadline,
    explicitly marked as such, not a claimed publication time. Hourly checks
    remain mandatory independently of the deadline.
    """
    from bs4 import BeautifulSoup
    from datetime import timedelta
    soup = BeautifulSoup(html, 'html.parser')
    spec = ABS_SPECS[category]
    heading = soup.find('h1')
    if not heading or heading.get_text(' ', strip=True) != spec['title']:
        raise ValueError('Wrong ABS release page')
    refs = soup.select('.field--name-field-abs-reference-period .field__item')
    if len(refs) != 1:
        raise ValueError('Missing ABS reference period')
    try:
        reference = datetime.strptime(refs[0].get_text(' ', strip=True), '%B %Y')
    except ValueError:
        raise ValueError('Invalid ABS release reference') from None
    if category == 'GDP' and reference.month % 3:
        raise ValueError('Invalid ABS release quarter')
    period = reference.strftime('%Y-%m') if category == 'Arbeitsmarkt' else f'{reference.year}-Q{reference.month // 3}'
    if observation['reference_period'] != period:
        raise ValueError('ABS API lags or conflicts with latest official release')
    publication = None
    release_date = None
    dates = soup.select('.logged-user-only.field--name-field-abs-release-date .field__item')
    if len(dates) > 1:
        raise ValueError('Ambiguous ABS publication time')
    if dates:
        value = dates[0].get_text(' ', strip=True)
        match = re.fullmatch(r'(\d{1,2}/\d{1,2}/\d{4}) (\d{1,2}:\d{2}[ap]m) (AEST|AEDT)', value)
        if not match:
            raise ValueError('Unrecognised ABS publication time')
        publication = datetime.strptime(match[1] + ' ' + match[2], '%d/%m/%Y %I:%M%p').replace(
            tzinfo=timezone(timedelta(hours=10 if match[3] == 'AEST' else 11))).astimezone(timezone.utc)
        release_date = datetime.strptime(match[1], '%d/%m/%Y').date()
        if publication > checked or publication.date() < _period_end(period, observation['frequency']):
            raise ValueError('Future or inconsistent ABS publication')
    else:
        # Date-only official release is valid metadata, but not an exact timestamp.
        release = soup.select('#release-date-section .field--name-dynamic-twig-fieldnode-release-or-orig-publish .field__item')
        if len(release) != 1:
            raise ValueError('Missing ABS publication date')
        release_date = datetime.strptime(release[0].get_text(strip=True), '%d/%m/%Y').date()
        if release_date > checked.date() or release_date < _period_end(period, observation['frequency']):
            raise ValueError('Future or inconsistent ABS publication date')
    next_dates = []
    for item in soup.select('li.future-release'):
        match = re.search(r'Next Release (\d{1,2}/\d{1,2}/\d{4})', item.get_text(' ', strip=True))
        if match:
            next_dates.append(datetime.strptime(match[1], '%d/%m/%Y').date())
    next_date = min(next_dates) if next_dates else None
    # Conservatively recheck a date-only scheduled release on that Australian day.
    from zoneinfo import ZoneInfo
    australian_today = checked.astimezone(ZoneInfo('Australia/Sydney')).date()
    if next_date and next_date <= australian_today:
        raise ValueError('Scheduled ABS release not yet confirmed')
    next_due = (datetime.combine(next_date, datetime.min.time(), tzinfo=ZoneInfo('Australia/Sydney'))
                .astimezone(timezone.utc).isoformat()) if next_date else None
    return {'published_at': publication.isoformat() if publication else None,
            'release_date_known': release_date.isoformat() if release_date else None,
            'next_release_date': next_date.isoformat() if next_date else None,
            'next_due_at': next_due, 'next_due_precision': 'date_only_start_of_AU_day' if next_due else None,
            'needs_hourly_check': True, 'release_url': spec['release']}


def fetch_abs_observation(category, *, now=None, session=None):
    """Two bounded keyless requests: exact CSV plus official release freshness.

    No process cache: callers must recheck at least hourly. Failures propagate
    without promoting any cached observation's checked_at timestamp.
    """
    checked, spec = _utc_now(now), ABS_SPECS[category]
    client = session or requests
    start = f'{checked.year - 2}-Q1' if category == 'GDP' else f'{checked.year - 1}-01'
    response = client.get(ABS_BASE + spec['flow'] + '/' + spec['key'],
                          params={'startPeriod': start, 'format': 'csv'}, timeout=20)
    response.raise_for_status()
    observation = parse_abs_observation(response.text, category, now=checked)
    release = client.get(spec['release'], timeout=20)
    release.raise_for_status()
    observation.update(_abs_release_metadata(release.text, category, observation, checked))
    observation['frequency'] = 'monthly' if category == 'Arbeitsmarkt' else 'quarterly'
    return observation
