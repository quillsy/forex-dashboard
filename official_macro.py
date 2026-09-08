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


def parse_eurostat_observation(payload, category, *, now=None, geo="EA21"):
    """Return latest valid observation dict, or None when the selected series is empty.

    Rejects mismatched dimensions, wrong units, duplicate positions, future and
    non-finite values. Sparse null periods are not treated as new releases.
    Provisional/estimated official observations retain their provider status.
    """
    checked = _utc_now(now)
    spec = EUROSTAT_SPECS[category]
    if geo != "EA21" and not (geo == "CH" and category == "GDP"):
        raise ValueError("Eurostat geography not approved for factor")
    filters = dict(spec["filters"], geo=geo)
    if not isinstance(payload, dict) or payload.get("class") != "dataset" or payload.get("source") != "ESTAT":
        raise ValueError("Not a Eurostat dataset")
    if payload.get("extension", {}).get("id", "").lower() != spec["dataset"]:
        raise ValueError("Wrong dataset")
    if geo == "CH":
        # EFTA GDP is covered by Eurostat reuse terms, but third-party content
        # is excluded. Require the explicit source institution supplied by API.
        annotations = payload.get("extension", {}).get("annotation", [])
        institutions = [a.get("text") for a in annotations if isinstance(a, dict) and a.get("type") == "SOURCE_INSTITUTIONS"]
        if institutions != ["Eurostat"]:
            raise ValueError("Eurostat source institution not confirmed")
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
        "seasonal_adjustment": filters["s_adj"], "geography": geo,
        "checked_at": checked.isoformat(), "published_at": None,
        "provider_status": status,
    }


def fetch_eurostat_observation(category, *, now=None, session=None, geo="EA21"):
    """Fetch a current EUR factor or Swiss GDP with one bounded public request.

    ``now`` is an injectable clock for testing, not a historical as-of query.
    Requests and validation failures propagate; callers must preserve a verified
    prior value without falsely refreshing its checked_at timestamp.
    """
    checked = _utc_now(now)
    spec = EUROSTAT_SPECS[category]
    if geo != "EA21" and not (geo == "CH" and category == "GDP"):
        raise ValueError("Eurostat geography not approved for factor")
    params = dict(spec["filters"], geo=geo, lang="EN", sinceTimePeriod=f"{checked.year - 1}-01" if category == "Arbeitsmarkt" else f"{checked.year - 1}-Q1")
    response = (session or requests).get(EUROSTAT_BASE + spec["dataset"], params=params, timeout=20)
    response.raise_for_status()
    return parse_eurostat_observation(response.json(), category, now=checked, geo=geo)


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


STATCAN_BASE = 'https://www150.statcan.gc.ca/t1/wds/rest/'
STATCAN_LABOUR_COORD = '1.7.1.1.1.1.0.0.0.0'
STATCAN_LABOUR_TITLE = 'Canada;Unemployment rate;Total - Gender;15 years and over;Estimate;Seasonally adjusted'


def _statcan_object(payload):
    if (not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict)
            or payload[0].get('status') != 'SUCCESS'):
        raise ValueError('Unsuccessful or ambiguous StatCan response')
    item = payload[0].get('object')
    if not isinstance(item, dict) or type(item.get('responseStatusCode')) is not int or item['responseStatusCode'] != 0:
        raise ValueError('Invalid StatCan response')
    return item


def parse_statcan_labour(series_payload, data_payload, cube_payload, *, now=None):
    """Exact national SA unemployment vector, cross-checked against latest cube.

    WDS releaseTime is the current publication/revision time, not a historical
    first-release timestamp. Canada national LFS excludes the territories.
    """
    from zoneinfo import ZoneInfo
    checked = _utc_now(now)
    series, data, cube = map(_statcan_object, (series_payload, data_payload, cube_payload))
    for item in (series, data):
        if (type(item.get('productId')) is not int or item['productId'] != 14100287
                or type(item.get('vectorId')) is not int or item['vectorId'] != 2062815
                or item.get('coordinate') != STATCAN_LABOUR_COORD):
            raise ValueError('Wrong StatCan labour series')
    expected = {'SeriesTitleEn': STATCAN_LABOUR_TITLE, 'memberUomCode': 239,
                'frequencyCode': 6, 'scalarFactorCode': 0, 'decimals': 1, 'terminated': 0}
    if any(type(series.get(k)) is not type(v) or series[k] != v for k, v in expected.items()):
        raise ValueError('Wrong StatCan labour metadata')
    if (str(cube.get('productId')) != '14100287' or cube.get('frequencyCode') != 6
            or cube.get('archiveStatusCode') != '2'
            or cube.get('cubeTitleEn') != 'Labour force characteristics, monthly, seasonally adjusted and trend-cycle'):
        raise ValueError('Wrong or inactive StatCan labour cube')
    # Validate the actual dimension members, not merely a title string.
    members = [(1, 'Geography', 1, 'Canada'), (2, 'Labour force characteristics', 7, 'Unemployment rate'),
               (3, 'Gender', 1, 'Total - Gender'), (4, 'Age group', 1, '15 years and over'),
               (5, 'Statistics', 1, 'Estimate'), (6, 'Data type', 1, 'Seasonally adjusted')]
    dimensions = cube.get('dimension', [])
    if not isinstance(dimensions, list) or len(dimensions) != 6:
        raise ValueError('Invalid StatCan cube dimensions')
    for position, title, code, label in members:
        dims = [d for d in dimensions if isinstance(d, dict) and d.get('dimensionPositionId') == position]
        if len(dims) != 1 or dims[0].get('dimensionNameEn') != title:
            raise ValueError('Conflicting StatCan dimension')
        selected = [m for m in dims[0].get('member', []) if isinstance(m, dict) and m.get('memberId') == code]
        if len(selected) != 1 or selected[0].get('memberNameEn') != label or selected[0].get('terminated') != 0:
            raise ValueError('Wrong StatCan dimension member')
        if position == 2 and selected[0].get('memberUomCode') != 239:
            raise ValueError('Wrong StatCan unemployment unit')

    def publication(raw):
        if not isinstance(raw, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', raw):
            raise ValueError('Invalid StatCan release time')
        value = datetime.strptime(raw, '%Y-%m-%dT%H:%M').replace(tzinfo=ZoneInfo('America/Toronto')).astimezone(timezone.utc)
        if value > checked:
            raise ValueError('Future StatCan publication')
        return value

    cube_release = publication(cube.get('releaseTime'))
    points = data.get('vectorDataPoint')
    if not isinstance(points, list) or not points:
        raise ValueError('Empty StatCan labour observations')
    observations = {}
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('Malformed StatCan observation')
        period = point.get('refPer')
        if not isinstance(period, str) or not re.fullmatch(r'\d{4}-\d{2}-01', period):
            raise ValueError('Invalid StatCan monthly period')
        end = _period_end(period[:7], 'M')
        if period in observations or end > checked.date():
            raise ValueError('Duplicate or future StatCan period')
        for key, expected_value in {'frequencyCode': 6, 'scalarFactorCode': 0, 'decimals': 1,
                                    'symbolCode': 0, 'statusCode': 0, 'securityLevelCode': 0}.items():
            if type(point.get(key)) is not int or point[key] != expected_value:
                raise ValueError('Invalid or unavailable StatCan observation')
        if point.get('refPer2', '') or point.get('refPerRaw2', '') or point.get('refPerRaw', period) != period:
            raise ValueError('Conflicting StatCan reference period')
        value = point.get('value')
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('Missing or invalid StatCan unemployment rate')
        published = publication(point.get('releaseTime'))
        if published.date() < end or published > cube_release:
            raise ValueError('Inconsistent StatCan release chronology')
        observations[period] = (float(value), end, published)
    period = max(observations)
    value, end, published = observations[period]
    if period != cube.get('cubeEndDate') or published != cube_release:
        raise ValueError('StatCan vector lags latest cube publication')
    return {'value': value, 'date': end.isoformat(), 'reference_period': period[:7],
            'source': 'Statistics Canada', 'source_url': 'https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410028701',
            'series_id': 'v2062815', 'frequency': 'monthly', 'unit': 'percent of labour force',
            'seasonal_adjustment': 'SA', 'geography': 'Canada excluding territories',
            'published_at': published.isoformat(), 'publication_basis': 'current WDS publication/revision time',
            'release_date_known': published.astimezone(ZoneInfo('America/Toronto')).date().isoformat(),
            'checked_at': checked.isoformat(), 'next_due_at': None, 'needs_hourly_check': True,
            'reuse_terms': 'https://www.statcan.gc.ca/en/terms-conditions/open-licence'}


def fetch_statcan_labour(*, now=None, session=None):
    """Three keyless requests: exact series, latest values, latest cube metadata.

    No cache/retry. HTTP errors (including locked-for-update 409) propagate and
    never refresh previous observations. WDS read requests use POST JSON bodies.
    """
    checked, client = _utc_now(now), session or requests
    identity = {'productId': 14100287, 'coordinate': STATCAN_LABOUR_COORD}
    payloads = []
    for method, body in [('getSeriesInfoFromCubePidCoord', [identity]),
                         ('getDataFromCubePidCoordAndLatestNPeriods', [dict(identity, latestN=3)]),
                         ('getCubeMetadata', [{'productId': 14100287}])]:
        response = client.post(STATCAN_BASE + method, json=body, timeout=20)
        response.raise_for_status()
        payloads.append(response.json())
    return parse_statcan_labour(*payloads, now=checked)


"""Official Japan GDP live source.

Official source: Cabinet Office ESRI quarterly GDP, expenditure approach.
Rights checked 2026-09-08: https://www.cao.go.jp/en/notice-e.html
Numerical data are freely reusable; cite Cabinet Office and mark calculated YoY
as our calculation. General content terms permit commercial reuse/CC BY 4.0.
"""
import calendar
import csv
import io
import math
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

JP_GDP_LATEST = 'https://www.esri.cao.go.jp/en/sna/sokuhou/sokuhou_top.html'
JP_GDP_RIGHTS = 'https://www.cao.go.jp/en/notice-e.html'
_JP_GDP_QUARTERS = {'Jan.-Mar.': 1, 'Apr.-Jun.': 2, 'Jul.-Sep.': 3, 'Oct.-Dec.': 4}


def _jp_gdp_now(now):
    checked = now or datetime.now(timezone.utc)
    if not isinstance(checked, datetime) or checked.tzinfo is None:
        raise ValueError('JP_GDP_CHECK_TIME_INVALID')
    return checked.astimezone(timezone.utc)


def _jp_gdp_url(base, href):
    url = urljoin(base, href)
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc != 'www.esri.cao.go.jp'
            or parts.query or parts.fragment or '/..' in parts.path):
        raise ValueError('JP_GDP_SOURCE_URL_INVALID')
    return url


def discover_japan_gdp_menu(content):
    soup = BeautifulSoup(content, 'html.parser')
    heading = soup.find('h1')
    if not heading or heading.get_text(' ', strip=True) != 'Quarterly Estimates of GDP':
        raise ValueError('JP_GDP_INDEX_IDENTITY_INVALID')
    links = [_jp_gdp_url(JP_GDP_LATEST, a['href']) for a in soup.find_all('a', href=True)
             if a.get_text(' ', strip=True) == 'Time series table']
    if len(links) != 1:
        raise ValueError('JP_GDP_LATEST_MENU_AMBIGUOUS')
    if not re.fullmatch(r'https://www\.esri\.cao\.go\.jp/en/sna/data/sokuhou/files/\d{4}/qe\d{3}_[12]/gdemenuea\.html', links[0]):
        raise ValueError('JP_GDP_MENU_PATH_INVALID')
    return links[0]


def parse_japan_gdp_menu(content, menu_url):
    _jp_gdp_url(JP_GDP_LATEST, menu_url)
    path = re.fullmatch(r'https://www\.esri\.cao\.go\.jp/en/sna/data/sokuhou/files/(\d{4})/qe(\d{2})([1-4])_([12])/gdemenuea\.html', menu_url)
    if not path or int(path[1]) % 100 != int(path[2]):
        raise ValueError('JP_GDP_MENU_PATH_INVALID')
    soup = BeautifulSoup(content, 'html.parser')
    heading = soup.find('h1')
    title = heading.get_text(' ', strip=True) if heading else ''
    match = re.fullmatch(r'(Jan\.-Mar\.|Apr\.-Jun\.|Jul\.-Sep\.|Oct\.-Dec\.)(\d{4}) \(The (1st|2nd) preliminary\)', title)
    if not match or (int(match[2]), _JP_GDP_QUARTERS[match[1]], int(match[3][0])) != (int(path[1]), int(path[3]), int(path[4])):
        raise ValueError('JP_GDP_MENU_IDENTITY_INVALID')
    suffix = f'{path[2]}{path[3]}{path[4]}'
    expected = f'https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/{path[1]}/qe{path[2]}{path[3]}_{path[4]}/tables/gaku-jk{suffix}.csv'
    links = []
    for a in soup.find_all('a', href=True):
        if re.fullmatch(r'Real, Seasonally Adjusted Series \(csv:[0-9]+KB\)', a.get_text(' ', strip=True)):
            link = _jp_gdp_url(menu_url, a['href'])
            if link == expected:
                links.append(link)
    if len(links) != 1:
        raise ValueError('JP_GDP_LEVEL_CSV_AMBIGUOUS')
    return {'reference_period': f'{path[1]}-Q{path[3]}', 'release_stage': match[3],
            'source_url': links[0], 'release_url': menu_url,
            'archive_url': f'https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/{path[1]}/toukei_{path[1]}.html'}


def parse_japan_gdp_release(content, metadata, *, now=None):
    checked = _jp_gdp_now(now)
    soup = BeautifulSoup(content, 'html.parser')
    heading = soup.find('h1')
    year = metadata['reference_period'][:4]
    if not heading or heading.get_text(' ', strip=True) != f'Quarterly Estimates of GDP - Release Archive - {year}':
        raise ValueError('JP_GDP_ARCHIVE_IDENTITY_INVALID')
    releases = []
    for row in soup.find_all('tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        for a in row.find_all('a', href=True):
            if not re.search(r'/qe\d{3}_[12]/gdemenuea\.html$', a['href']):
                continue
            link = _jp_gdp_url(metadata['archive_url'], a['href'])
            if len(cells) < 2:
                raise ValueError('JP_GDP_ARCHIVE_SCHEMA_INVALID')
            try:
                day = datetime.strptime(cells[0].get_text(' ', strip=True), '%b %d, %Y').date()
            except ValueError:
                raise ValueError('JP_GDP_RELEASE_DATE_INVALID') from None
            releases.append((day, link))
    matches = [day for day, link in releases if link == metadata['release_url']]
    if len(matches) != 1 or not releases or matches[0] != max(day for day, link in releases):
        raise ValueError('JP_GDP_RELEASE_NOT_LATEST')
    day = matches[0]
    if day > checked.astimezone(ZoneInfo('Asia/Tokyo')).date():
        raise ValueError('JP_GDP_FUTURE_RELEASE')
    return {'release_date_known': day.isoformat(), 'published_at': None,
            'next_due_at': None, 'needs_hourly_check': True}


def parse_japan_gdp_csv(content, metadata, *, now=None):
    """Matching-year-quarter ratio of real SAAR levels from one current vintage."""
    checked = _jp_gdp_now(now)
    try:
        text = content.decode('cp932')
    except (UnicodeDecodeError, AttributeError):
        raise ValueError('JP_GDP_CSV_ENCODING_INVALID') from None
    rows = list(csv.reader(io.StringIO(text)))
    if (len(rows) < 8 or len(rows[1]) != 33 or len(rows[5]) != 33
            or rows[0][0] != '実質季節調整系列'
            or rows[1][0] != 'Real, Seasonally Adjusted Series'
            or rows[1][29] != '(Billions of Chained (2020) Yen)'
            or rows[2][1] != '国内総生産(支出側)'
            or rows[5][1] != 'GDP(Expenditure Approach)'):
        raise ValueError('JP_GDP_SERIES_IDENTITY_INVALID')
    levels = {}
    year = None
    previous_serial = None
    footnotes = False
    for row in rows[7:]:
        if not row or not any(value.strip() for value in row):
            continue
        if row[0].startswith('＊'):
            footnotes = True
            continue
        match = re.fullmatch(r'(?:(\d{4})/\s*)?(1-\s*3\.|4-\s*6\.|7-\s*9\.|10-\s*12\.)', row[0].strip())
        if not match or footnotes or len(row) != 33:
            raise ValueError('JP_GDP_PERIOD_SCHEMA_INVALID')
        start_month = int(match[2].split('-')[0])
        quarter = (start_month + 2) // 3
        if match[1]:
            if quarter != 1:
                raise ValueError('JP_GDP_YEAR_LABEL_INVALID')
            year = int(match[1])
        if year is None:
            raise ValueError('JP_GDP_YEAR_MISSING')
        serial = year * 4 + quarter - 1
        if previous_serial is not None and serial != previous_serial + 1:
            raise ValueError('JP_GDP_QUARTER_GAP_OR_DUPLICATE')
        previous_serial = serial
        period = f'{year}-Q{quarter}'
        value = row[1].strip()
        if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', value):
            raise ValueError('JP_GDP_LEVEL_INVALID')
        level = float(value.replace(',', ''))
        if not math.isfinite(level) or level <= 0:
            raise ValueError('JP_GDP_LEVEL_INVALID')
        levels[period] = level
    if '＊年率で表示している。' not in text:
        raise ValueError('JP_GDP_SAAR_METADATA_MISSING')
    period = metadata['reference_period']
    if not levels or max(levels) != period:
        raise ValueError('JP_GDP_CSV_RELEASE_PERIOD_CONFLICT')
    year, quarter = int(period[:4]), int(period[-1])
    previous = f'{year - 1}-Q{quarter}'
    if previous not in levels:
        raise ValueError('JP_GDP_MATCHING_PRIOR_QUARTER_MISSING')
    month = quarter * 3
    end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
    released = datetime.fromisoformat(metadata['release_date_known']).date()
    if end >= released or released > checked.astimezone(ZoneInfo('Asia/Tokyo')).date():
        raise ValueError('JP_GDP_PERIOD_RELEASE_INVALID')
    value = 100 * (levels[period] / levels[previous] - 1)
    if not math.isfinite(value) or not -100 < value <= 200:
        raise ValueError('JP_GDP_GROWTH_INVALID')
    return {'value': value, 'date': end.isoformat(), 'reference_period': period,
            'unit': 'percent YoY', 'frequency': 'quarterly', 'seasonal_adjustment': 'SA',
            'source': 'Cabinet Office ESRI', 'source_url': metadata['source_url'],
            'series_id': 'ESRI:GDP:Expenditure:Real:SA:2020:YoY',
            'release_date_known': metadata['release_date_known'], 'published_at': None,
            'release_stage': metadata['release_stage'], 'provider_status': 'p', 'is_estimate': True,
            'checked_at': checked.isoformat(),
            'next_due_at': None, 'needs_hourly_check': True,
            'transformation': '100 * (current / matching prior-year quarter - 1); same-vintage real SAAR levels',
            'reuse_terms': JP_GDP_RIGHTS}


def fetch_japan_gdp(*, now=None, session=None):
    """Discover the current release every run; four keyless bounded requests."""
    checked = _jp_gdp_now(now)
    client = session or requests
    def get(url):
        _jp_gdp_url(JP_GDP_LATEST, url)
        response = client.get(url, timeout=20)
        response.raise_for_status()
        if isinstance(response.url, str) and response.url != url:
            raise ValueError('JP_GDP_UNEXPECTED_REDIRECT')
        if len(response.content) > 2_000_000:
            raise ValueError('JP_GDP_RESPONSE_TOO_LARGE')
        return response.content
    menu_url = discover_japan_gdp_menu(get(JP_GDP_LATEST))
    metadata = parse_japan_gdp_menu(get(menu_url), menu_url)
    metadata.update(parse_japan_gdp_release(get(metadata['archive_url']), metadata, now=checked))
    return parse_japan_gdp_csv(get(metadata['source_url']), metadata, now=checked)
