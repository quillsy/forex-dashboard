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
    result = {'value': value, 'date': date.isoformat(), 'reference_period': period,
            'source': 'Eurostat', 'source_url': EUROSTAT_BASE + 'prc_hicp_minr',
            'series_id': 'prc_hicp_minr:M.RCH_A.TOTAL.' + geo, 'frequency': 'monthly',
            'unit': 'annual percent change', 'seasonal_adjustment': 'NSA',
            'checked_at': checked.isoformat(), 'published_at': None,
            'provider_status': status, 'is_estimate': estimate}
    if geo == 'EA21':
        from source_contracts import scheduled_releases
        for schedule in scheduled_releases('EUR', 'Inflation'):
            due = datetime.fromisoformat(schedule['due_at'])
            minimum_period = schedule['minimum_period_start'][:7]
            if period == schedule['previous_period']:
                result['next_due_at'] = due.isoformat()
                result['next_due_precision'] = schedule['next_due_precision']
                result['needs_hourly_check'] = True
                if checked >= due:
                    result['_validation'] = 'UNVERIFIED'
                    result['_reason'] = 'Eurostat-Flash für ' + minimum_period + ' fällig; API liefert noch ' + period
            elif period == minimum_period:
                result['release_date_known'] = schedule['release_date_known']
                # After the last confirmed transition the later date is
                # unknown. Estimates remain allowed with hourly checks.
                result['next_due_at'] = None
                result['needs_hourly_check'] = True
                if checked < due:
                    result['_validation'] = 'UNVERIFIED'
                    result['_reason'] = 'Eurostat-Flash für ' + minimum_period + ' noch nicht fällig'
    return result


def fetch_hicp(*, now=None, session=None, geo="EA21"):
    if geo not in ("EA21", "CH"):
        raise ValueError("HICP_GEOGRAPHY_NOT_APPROVED")
    checked = now or datetime.now(timezone.utc)
    response = (session or requests).get(EUROSTAT_BASE + 'prc_hicp_minr',
        params={'freq': 'M', 'unit': 'RCH_A', 'coicop18': 'TOTAL', 'geo': geo,
                'sinceTimePeriod': f'{checked.year - 1}-01', 'lang': 'EN'}, timeout=20)
    response.raise_for_status()
    return parse_hicp(response.json(), now=checked, geo=geo)


# The FSO moved statistical products to its new portal. The current HICP25B25
# card links the same DAM asset in its detail and XLSX controls (verified 2026-11-19).
BFS_HICP_PAGE = 'https://statistik.admin.ch/de/themen/preise/harmonisierter-verbraucherpreisindex-hvpi'
BFS_HICP_DISCOVERY = BFS_HICP_PAGE
BFS_ASSET_BASE = 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/'
BFS_HICP_SHORT_TITLE = 'HVPI Schweiz (2025=100), Detailresultate seit 2005'
BFS_HICP_TITLE = (BFS_HICP_SHORT_TITLE + ', Warenkorbstruktur 2025, Indizes, '
                  'Veränderungsraten. [HICP25B25]')
_BFS_HICP_MAX_HTML_BYTES = 2_000_000


def discover_swiss_hicp(content):
    """Resolve the current original HICP25B25 product from its scoped portal links."""
    import re
    from bs4 import BeautifulSoup

    def text(node):
        return ' '.join(node.get_text(' ', strip=True).split())

    try:
        if not isinstance(content, bytes) or not 0 < len(content) <= _BFS_HICP_MAX_HTML_BYTES:
            raise ValueError()
        soup = BeautifulSoup(content.decode('utf-8'), 'html.parser')
        asset_ids = set()
        # Both layouts occur on the official topic page. The second uses an
        # abbreviated heading, but the Details control repeats the full title.
        for container, heading, title in (
                ('article.bfs-dam-asset-card', 'h4.card__title', BFS_HICP_TITLE),
                ('li.accordion__item', 'span.accordion__title', BFS_HICP_SHORT_TITLE)):
            for card in soup.select(container):
                headings = card.select(heading)
                links = card.find_all('a')
                expected_label = 'Weiter lesen auf der Seite ' + BFS_HICP_TITLE
                has_product_label = any(isinstance(a.get('aria-label'), str)
                                        and ' '.join(a['aria-label'].split()) == expected_label
                                        for a in links)
                if not any(text(h) == title for h in headings) and not has_product_label:
                    continue
                # A malformed card that still identifies the original product
                # must not be silently hidden by another well-formed card.
                if len(headings) != 1 or text(headings[0]) != title:
                    raise ValueError()
                details = [a for a in links if text(a) == 'Details']
                downloads = [a for a in links if text(a) == 'xlsx']
                if not details or not downloads:
                    raise ValueError()
                ids = set()
                for link in details:
                    href = link.get('href')
                    match = re.fullmatch(r'(?:https://statistik\.admin\.ch)?/de/asset/([1-9][0-9]*)',
                                         href) if isinstance(href, str) else None
                    label = link.get('aria-label')
                    if (match is None or not isinstance(label, str) or ' '.join(label.split()) != expected_label):
                        raise ValueError()
                    ids.add(match[1])
                if len(ids) != 1:
                    raise ValueError()
                asset_id = next(iter(ids))
                master = BFS_ASSET_BASE + asset_id + '/master'
                # The accordion also links its description to the same master.
                # Every other control in a matching card must bind that master;
                # never follow a link from an unrelated or ambiguous product.
                if any(a.get('href') != master for a in links if text(a) != 'Details'):
                    raise ValueError()
                asset_ids.add(asset_id)
        if len(asset_ids) != 1:
            raise ValueError()
        return BFS_ASSET_BASE + next(iter(asset_ids))
    except (TypeError, ValueError, AttributeError, KeyError):
        raise ValueError('BFS_HICP_DISCOVERY_INVALID') from None


def parse_swiss_hicp(content, metadata, *, now=None):
    """FSO all-items monthly HICP YoY, never the national CPI/LIK."""
    import io
    import re
    import openpyxl
    checked = _utc_now(now)
    try:
        description = metadata['description']
        categories = description['categorization']
        for field, code in [('termsOfUse', 'OPEN-BY'), ('dataSource', 'BFS'),
                            ('inquiry', 'HVPI'), ('periodicity', 'MONTHLY')]:
            if code not in {x.get('code') for x in categories[field]}:
                raise ValueError()
        if {x.get('code') for x in categories['termsOfUse']} != {'OPEN-BY'}:
            raise ValueError()
        source_title = description['titles']['main']
        if (not isinstance(source_title, str) or len(source_title) > 180
                or not source_title.startswith('HVPI Schweiz (')
                or 'Detailresultate seit' not in source_title
                or any(ord(char) < 32 for char in source_title)):
            raise ValueError()
        provisional = metadata['bfs']['provisional']
        if type(provisional) is not bool:
            raise ValueError()
        if metadata['bfs']['lifecycleGroup'] != 'CURRENT':
            raise ValueError()
        published = datetime.fromisoformat(metadata['bfs']['embargo'].replace('Z', '+00:00'))
        if published.tzinfo is None or published > checked:
            raise ValueError()
        reference_end = datetime.strptime(description['bibliography']['period'].split('-')[-1], '%d.%m.%Y').date()
        period = reference_end.strftime('%Y-%m')
        if _period_end(period, 'M') != reference_end or reference_end > published.date():
            raise ValueError()
        asset_id = metadata['ids']['damId']
        if type(asset_id) is not int:
            raise ValueError()
        source_url = BFS_ASSET_BASE + str(asset_id) + '/master'
        masters = [x for x in metadata['links'] if x['rel'] == 'master']
        if len(masters) != 1 or masters[0].get('format') != 'xlsx' or masters[0]['href'] != source_url:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('BFS_HICP_METADATA_INVALID') from exc
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            sheet = workbook['%_m-12']
            # FSO files currently declare A1:A1 although the full data are present.
            sheet.reset_dimensions()
            rows = list(sheet.iter_rows(min_row=1, max_row=8, values_only=True))
            if ('HARMONISED INDEX OF CONSUMER PRICES (HICP)' not in str(rows[0][0])
                    or not str(rows[2][0]).startswith('Annual rate of change /')
                    or str(rows[4][0]).strip() != 'Switzerland / Suisse / Schweiz'
                    or list(rows[6][:5]) != ['ITEM', 'Type de position', 'EN', 'FR', 'DE']
                    or list(rows[7][:5]) != ['_T', 1, 'All items', 'Total', 'Total']):
                raise ValueError()
            dates = list(rows[6][5:])
            values = list(rows[7][5:])
            # No empty future placeholders, duplicate periods, or skipped months.
            if not dates or any(not isinstance(x, str) or not re.fullmatch(r'\d{4}-\d{2}', x) for x in dates):
                raise ValueError()
            serial = [int(x[:4]) * 12 + int(x[5:]) for x in dates]
            if any(_period_end(x, 'M') > reference_end for x in dates) or any(b != a + 1 for a, b in zip(serial, serial[1:])):
                raise ValueError()
            if dates[-1] != period or len(values) != len(dates):
                raise ValueError()
            value = values[-1]
            if type(value) not in (int, float) or not math.isfinite(value) or not -25 <= value <= 25:
                raise ValueError()
        finally:
            workbook.close()
    except Exception as exc:
        raise ValueError('BFS_HICP_WORKBOOK_INVALID') from exc
    return {'value': float(value), 'date': reference_end.isoformat(), 'reference_period': period,
            'source': 'BFS', 'source_title': source_title, 'source_url': source_url, 'series_id': 'BFS:HICP:CH:_T:M:RCH_A',
            'frequency': 'monthly', 'unit': 'annual percent change', 'seasonal_adjustment': 'NSA',
            'checked_at': checked.isoformat(), 'published_at': published.isoformat(),
            'next_due_at': None, 'is_estimate': provisional,
            'redistribution_status': 'permitted_with_attribution', 'license': 'OPEN-BY'}


def fetch_swiss_hicp(*, now=None, session=None):
    client = session or requests
    def get(url):
        response = client.get(url, timeout=30)
        response.raise_for_status()
        return response
    def get_json(url, error_code):
        response = get(url)  # Transport errors retain their RequestException type.
        try:
            payload = response.json()
        except ValueError as exc:
            # requests.JSONDecodeError also inherits RequestException; normalize
            # malformed successful responses before the app's transport handler.
            raise ValueError(error_code) from exc
        if not isinstance(payload, dict):
            raise ValueError(error_code)
        return payload
    # The new discovery is a single public HTML GET, not a search/API fallback.
    # Do not follow redirects: resolve only the verified, fixed topic URL.
    discovery = client.get(BFS_HICP_DISCOVERY, timeout=30, allow_redirects=False)
    discovery.raise_for_status()
    if (discovery.status_code != 200 or discovery.url != BFS_HICP_DISCOVERY
            or discovery.history
            or discovery.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'text/html'):
        raise ValueError('BFS_HICP_DISCOVERY_INVALID')
    asset_url = discover_swiss_hicp(discovery.content)
    metadata = get_json(asset_url, 'BFS_HICP_METADATA_INVALID')
    if BFS_ASSET_BASE + str(metadata.get('ids', {}).get('damId')) != asset_url:
        raise ValueError('BFS_HICP_ASSET_ID_MISMATCH')
    # Construct a fixed official-domain URL; never follow metadata-controlled hosts.
    content = get(asset_url + '/master').content
    return parse_swiss_hicp(content, metadata, now=now)
