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


TREASURY_XML_URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml'


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
    """One current/target month request; preceding month only if feed is empty.

    At month boundaries weekends/holidays may precede the first observation.
    Invalid or null observations raise, never trigger older-month fallback.
    """
    from datetime import timedelta
    target = min(_date(target_date), _date(now or datetime.now(timezone.utc)))
    month = target.replace(day=1)
    for attempt in range(2):
        response = (client or requests).get(TREASURY_XML_URL,
            params={'data': 'daily_treasury_yield_curve', 'field_tdr_date_value_month': month.strftime('%Y%m')}, timeout=timeout)
        response.raise_for_status()
        result = parse_treasury_2y(response.text, target, now=now)
        if result is not None:
            if result['observation_date'][:7] != month.strftime('%Y-%m'):
                raise ValueError('TREASURY_WRONG_REQUESTED_MONTH')
            return result
        month = (month - timedelta(days=1)).replace(day=1)
    return None
