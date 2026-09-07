"""Eurostat current EA21 all-items HICP annual rate, including labelled estimates.

2026 uses prc_hicp_minr/coicop18=TOTAL. prc_hicp_manr ended in 2025.
https://ec.europa.eu/eurostat/cache/metadata/en/prc_hicp_esms.htm
"""
from datetime import datetime, timezone
import math
import requests
from official_macro import EUROSTAT_BASE, _category_index, _period_end, _utc_now


def parse_hicp(payload, *, now=None, geo="EA21"):
    if geo not in ("EA21", "CH"):
        raise ValueError("HICP_GEOGRAPHY_NOT_APPROVED")
    checked = _utc_now(now)
    if not isinstance(payload, dict) or payload.get('class') != 'dataset' or payload.get('source') != 'ESTAT' or payload.get('extension', {}).get('id', '').lower() != 'prc_hicp_minr':
        raise ValueError('HICP_DATASET_INVALID')
    expected = {'freq': 'M', 'unit': 'RCH_A', 'coicop18': 'TOTAL', 'geo': geo}
    dimensions, ids, sizes = payload.get('dimension', {}), payload.get('id', []), payload.get('size', [])
    if len(ids) != len(set(ids)) or set(ids) != set(expected) | {'time'} or len(sizes) != len(ids):
        raise ValueError('HICP_DIMENSIONS_INVALID')
    for key, value in expected.items():
        if _category_index(dimensions[key]['category']) != {value: 0} or sizes[ids.index(key)] != 1:
            raise ValueError('HICP_SERIES_MISMATCH')
    times = _category_index(dimensions['time']['category'])
    if sizes[ids.index('time')] != len(times):
        raise ValueError('HICP_TIME_SHAPE_INVALID')
    values, statuses = payload.get('value', {}), payload.get('status', {})
    if isinstance(values, list):
        if len(values) != len(times):
            raise ValueError('HICP_VALUE_SHAPE_INVALID')
        values = {str(i): value for i, value in enumerate(values)}
    if not isinstance(values, dict) or any(key not in {str(i) for i in range(len(times))} for key in values):
        raise ValueError('HICP_VALUES_INVALID')
    if not isinstance(statuses, (dict, list)):
        raise ValueError('HICP_STATUS_INVALID')
    observations = []
    for period, position in times.items():
        date = _period_end(period, 'M')
        value = values.get(str(position))
        if value is None:
            continue
        if type(value) not in (float, int) or not math.isfinite(value) or not -25 <= value <= 25 or date > checked.date():
            raise ValueError('HICP_RATE_OR_PERIOD_INVALID')
        status = statuses.get(str(position)) if isinstance(statuses, dict) else (statuses[position] if position < len(statuses) else None)
        flags = set(str(status or '').split())
        if flags - {'e', 'p', 'b', 'd', 'r'}:
            raise ValueError('HICP_STATUS_NOT_APPROVED')
        observations.append((date, period, float(value), status, 'e' in flags))
    if not observations:
        return None
    date, period, value, status, estimate = max(observations)
    return {'value': value, 'date': date.isoformat(), 'reference_period': period,
            'source': 'Eurostat', 'source_url': EUROSTAT_BASE + 'prc_hicp_minr',
            'series_id': 'prc_hicp_minr:M.RCH_A.TOTAL.' + geo, 'frequency': 'monthly',
            'unit': 'annual percent change', 'seasonal_adjustment': 'NSA',
            'checked_at': checked.isoformat(), 'published_at': None,
            'provider_status': status, 'is_estimate': estimate}


def fetch_hicp(*, now=None, session=None, geo="EA21"):
    if geo not in ("EA21", "CH"):
        raise ValueError("HICP_GEOGRAPHY_NOT_APPROVED")
    checked = now or datetime.now(timezone.utc)
    response = (session or requests).get(EUROSTAT_BASE + 'prc_hicp_minr',
        params={'freq': 'M', 'unit': 'RCH_A', 'coicop18': 'TOTAL', 'geo': geo,
                'sinceTimePeriod': f'{checked.year - 1}-01', 'lang': 'EN'}, timeout=20)
    response.raise_for_status()
    return parse_hicp(response.json(), now=checked, geo=geo)
