"""Keyless official coupon-government yield readers; no estimated spot proxies."""

import csv
from datetime import date, datetime, timezone
import io
import math
import re

import requests
from bs4 import BeautifulSoup


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


TREASURY_XML_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'
TREASURY_TEXTVIEW_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView'
TREASURY_SOURCE = 'US Treasury nominal 2Y constant maturity'


def parse_treasury_textview_2y(html_text, target_date, month, now=None):
    """Read only the official nominal par-CMT table and its exact 2 Yr column.

    This is a second representation of Treasury's daily yield-curve data, not a
    different bond, maturity, or estimator. HTML structure/identity drift fails
    closed. The page's update date is not an observation publication timestamp.
    """
    target = min(_date(target_date), _date(now or datetime.now(timezone.utc)))
    requested_month = str(month)
    if not re.fullmatch(r'\d{6}', requested_month):
        raise ValueError('TREASURY_MONTH_INVALID')
    soup = BeautifulSoup(html_text, 'html.parser')
    matches = []
    for table in soup.find_all('table'):
        heading = table.find_previous(re.compile(r'^h[1-6]$'))
        if heading is None or heading.get_text(' ', strip=True) != 'Daily Treasury Par Yield Curve Rates':
            continue
        rows = table.find_all('tr')
        headers = []
        for position, row in enumerate(rows):
            cells = row.find_all(['th', 'td'], recursive=False)
            labels = [' '.join(cell.get_text(' ', strip=True).split()) for cell in cells]
            if labels.count('Date') == 1 and labels.count('2 Yr') == 1:
                headers.append((position, labels))
        if headers:
            matches.append((rows, headers))
    if len(matches) != 1 or len(matches[0][1]) != 1:
        raise ValueError('TREASURY_NOMINAL_TABLE_REQUIRED')
    rows, headers = matches[0]
    header_at, labels = headers[0]
    if labels[0] != 'Date' or labels.count('2 Yr') != 1:
        raise ValueError('TREASURY_2YEAR_REQUIRED')
    index = labels.index('2 Yr')
    observations = {}
    for row in rows[header_at + 1:]:
        cells = row.find_all(['th', 'td'], recursive=False)
        if not cells:
            continue
        if len(cells) != len(labels):
            raise ValueError('TREASURY_TABLE_ROW_INVALID')
        raw_date = cells[0].get_text(' ', strip=True)
        if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', raw_date):
            raise ValueError('TREASURY_DATE_INVALID')
        observed = datetime.strptime(raw_date, '%m/%d/%Y').date()
        if observed > _date(now or datetime.now(timezone.utc)) or observed in observations:
            raise ValueError('TREASURY_FUTURE_OR_DUPLICATE_DATE')
        if observed.strftime('%Y%m') != requested_month:
            raise ValueError('TREASURY_WRONG_REQUESTED_MONTH')
        raw_value = cells[index].get_text(' ', strip=True)
        try:
            value = float(raw_value)
        except ValueError:
            raise ValueError('TREASURY_OBSERVATION_INVALID') from None
        if not math.isfinite(value) or not 0 <= value <= 30:
            raise ValueError('TREASURY_OBSERVATION_INVALID')
        observations[observed] = value
    eligible = {day: value for day, value in observations.items() if day <= target}
    if not eligible:
        return None
    observed = max(eligible)
    return {'value': eligible[observed], 'observation_date': observed.isoformat(),
            'source': TREASURY_SOURCE + ' (TextView)', 'series_id': 'BC_2YEAR',
            'equivalent_series_id': 'DGS2', 'unit': 'percent_per_annum',
            'source_url': TREASURY_TEXTVIEW_URL + '?type=daily_treasury_yield_curve',
            'published_at': None}


def _treasury_transport_failure(exc):
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        return status in (408, 429) or isinstance(status, int) and 500 <= status <= 599
    return False


def _fetch_treasury_textview_2y(target, month, client, now, timeout):
    response = client.get(TREASURY_TEXTVIEW_URL,
        params={'type': 'daily_treasury_yield_curve', 'field_tdr_date_value_month': month.strftime('%Y%m')},
        timeout=timeout)
    response.raise_for_status()
    return parse_treasury_textview_2y(response.text, target, month.strftime('%Y%m'), now=now)


def parse_treasury_2y(xml_text, target_date, now=None):
    """Direct nominal Treasury CMT, the underlying definition of FRED DGS2.

    FRED DGS2 links H.15 note 9 and Treasury methodology; H.15 note 9 identifies
    these yields as Treasury-interpolated nominal CMTs. Unit: percent per annum.
    Evidence: https://fred.stlouisfed.org/series/DGS2 and
    https://www.federalreserve.gov/releases/h15/ (nominal CMT footnote 9).
    Feed updated is NOT an observation publication time and is never used as one.
    """
    import xml.etree.ElementTree as ET
    if '<!DOCTYPE' in xml_text.upper() or '<!ENTITY' in xml_text.upper():
        raise ValueError('TREASURY_UNSAFE_XML')
    root = ET.fromstring(xml_text)
    ns = {'a': 'http://www.w3.org/2005/Atom', 'd': 'http://schemas.microsoft.com/ado/2007/08/dataservices',
          'm': 'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'}
    if root.tag != '{'+ns['a']+'}feed' or root.findtext('a:title', namespaces=ns) != 'DailyTreasuryYieldCurveRateData':
        raise ValueError('TREASURY_NOMINAL_FEED_REQUIRED')
    if root.findall('a:link[@rel="next"]', ns):
        raise ValueError('TREASURY_INCOMPLETE_PAGINATED_FEED')
    today, target = _date(now or datetime.now(timezone.utc)), _date(target_date)
    observations = {}
    for entry in root.findall('a:entry', ns):
        categories = entry.findall('a:category', ns)
        if len(categories) != 1 or categories[0].get('term') != 'TreasuryDataWarehouseModel.DailyTreasuryYieldCurveRateDatum':
            raise ValueError('TREASURY_SERIES_INVALID')
        properties = entry.findall('a:content/m:properties', ns)
        if len(properties) != 1:
            raise ValueError('TREASURY_PROPERTIES_INVALID')
        dates, values = properties[0].findall('d:NEW_DATE', ns), properties[0].findall('d:BC_2YEAR', ns)
        if len(dates) != 1 or len(values) != 1:
            raise ValueError('TREASURY_2YEAR_REQUIRED')
        stamp, node = dates[0], values[0]
        if stamp.get('{'+ns['m']+'}type') != 'Edm.DateTime' or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00', stamp.text or ''):
            raise ValueError('TREASURY_DATE_INVALID')
        observed = date.fromisoformat(stamp.text[:10])
        if observed > today or observed in observations:
            raise ValueError('TREASURY_FUTURE_OR_DUPLICATE_DATE')
        if node.get('{'+ns['m']+'}null') in ('true', '1') or node.get('{'+ns['m']+'}type') != 'Edm.Double':
            raise ValueError('TREASURY_OBSERVATION_UNAVAILABLE')
        try:
            value = float(node.text)
        except (ValueError, TypeError):
            raise ValueError('TREASURY_OBSERVATION_INVALID') from None
        if not math.isfinite(value) or not 0 <= value <= 30:
            raise ValueError('TREASURY_OBSERVATION_INVALID')
        observations[observed] = value
    eligible = {day: value for day,value in observations.items() if day <= target}
    if not eligible:
        return None
    observed = max(eligible)
    return {'value': eligible[observed], 'observation_date': observed.isoformat(),
            'source': 'US Treasury nominal 2Y constant maturity', 'series_id': 'BC_2YEAR',
            'equivalent_series_id': 'DGS2', 'unit': 'percent_per_annum',
            'source_url': TREASURY_XML_URL + '?data=daily_treasury_yield_curve',
            'published_at': None}


def fetch_treasury_2y(target_date, client=None, now=None, timeout=20):
    """Use Treasury XML; try its official TextView only on transport failure.

    At month boundaries weekends/holidays may precede the first observation.
    Invalid or null XML observations raise, never trigger HTML or older-month
    fallback. A TextView schema/value conflict also raises and fails closed.
    """
    from datetime import timedelta
    target = min(_date(target_date), _date(now or datetime.now(timezone.utc)))
    month = target.replace(day=1)
    transport = client or requests
    for attempt in range(2):
        try:
            response = transport.get(TREASURY_XML_URL,
                params={'data': 'daily_treasury_yield_curve', 'field_tdr_date_value_month': month.strftime('%Y%m')}, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            if not _treasury_transport_failure(exc):
                raise
            result = _fetch_treasury_textview_2y(target, month, transport, now, timeout)
        else:
            result = parse_treasury_2y(response.text, target, now=now)
        if result is not None:
            if result['observation_date'][:7] != month.strftime('%Y-%m'):
                raise ValueError('TREASURY_WRONG_REQUESTED_MONTH')
            return result
        if attempt == 0 and target.day > 5:
            # A blank current month well after its first business day cannot
            # justify silently refreshing from last month's observations.
            return None
        month = (month - timedelta(days=1)).replace(day=1)
    return None
