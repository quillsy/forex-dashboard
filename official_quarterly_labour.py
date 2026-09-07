"""Official, keyless quarterly SA unemployment observations for NZ and Switzerland.

Only current-release metadata is asserted, never historical vintage availability.
Age limits and signal eligibility belong to the caller. Transport is injectable.
"""
import calendar
import csv
import html
import io
import json
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import requests

NZ_BASE = 'https://www.stats.govt.nz/information-releases/'
CH_CATALOG = 'https://opendata.swiss/api/3/action/package_search'
CH_TITLE = ('Erwerbslosenquote gemäss ILO nach Geschlecht, Nationalität und Altersgruppen, '
            'brutto- und saisonbereinigte Werte. Durchschnittliche Quartalswerte')
CH_RIGHTS = 'https://opendata.swiss/terms-of-use#terms_by'
MONTHS = {name: i for i, name in enumerate(calendar.month_name) if name}


def _now(now):
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError('LABOUR_CHECK_TIME_REQUIRES_TIMEZONE')
    return value.astimezone(timezone.utc)


def _end(year, quarter):
    month = quarter * 3
    return datetime(year, month, calendar.monthrange(year, month)[1]).date()


def _rate(raw):
    if isinstance(raw, bool):
        raise ValueError('LABOUR_RATE_INVALID')
    value = float(raw)
    if not math.isfinite(value) or not 0 <= value <= 25:
        raise ValueError('LABOUR_RATE_INVALID')
    return value


def _timestamp(raw):
    value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('LABOUR_PUBLICATION_TIMEZONE_MISSING')
    return value.astimezone(timezone.utc)


class _PageData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.documents = []

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get('data-value')
        if value is not None:
            try:
                document = json.loads(value)
            except (ValueError, TypeError):
                return
            if isinstance(document, dict):
                self.documents.append(document)


def _text(value):
    if isinstance(value, str):
        return re.sub(r'<[^>]+>', ' ', html.unescape(value))
    if isinstance(value, dict):
        return ' '.join(_text(x) for x in value.values())
    if isinstance(value, list):
        return ' '.join(_text(x) for x in value)
    return ''


def parse_nz_labour(page, *, year, quarter, now=None, source_url=None):
    """Parse exact release graph, keeping the total SA survey separate from sexes."""
    checked = _now(now)
    if quarter not in (1, 2, 3, 4):
        raise ValueError('NZ_LABOUR_QUARTER_INVALID')
    period_end = _end(year, quarter)
    if period_end > checked.date():
        raise ValueError('NZ_LABOUR_FUTURE_PERIOD')
    title = f'Labour market statistics: {calendar.month_name[quarter * 3]} {year} quarter'
    parser = _PageData()
    parser.feed(page)
    releases = [x for x in parser.documents if x.get('Title') == title]
    if len(releases) != 1:
        raise ValueError('NZ_LABOUR_RELEASE_IDENTITY_INVALID')
    release = releases[0]
    publication = datetime.strptime(release['DateTaxonomyTerm']['PublicationDate'], '%Y-%m-%d %H:%M:%S')
    publication = publication.replace(tzinfo=ZoneInfo('Pacific/Auckland')).astimezone(timezone.utc)
    if publication > checked or publication.astimezone(ZoneInfo('Pacific/Auckland')).date() < period_end:
        raise ValueError('NZ_LABOUR_PUBLICATION_INVALID')
    media = release.get('FeaturedMedia', {})
    heading = media.get('GraphHeading', '')
    if not re.fullmatch(r'Unemployment rate by sex, seasonally adjusted, [A-Za-z]+ \d{4}[–-]'
                        + re.escape(f'{calendar.month_name[quarter * 3]} {year} quarters'), heading):
        raise ValueError('NZ_LABOUR_SURVEY_IDENTITY_INVALID')
    values = []
    for group in media.get('SeriesData', []):
        rows = list(csv.reader(io.StringIO(group.get('GraphCsvData', ''))))
        if not rows or rows[0][0] != 'Quarter':
            raise ValueError('NZ_LABOUR_CSV_HEADER_INVALID')
        headers = rows[0][1:]
        if len(headers) != len(set(headers)):
            raise ValueError('NZ_LABOUR_DUPLICATE_PERIOD')
        periods = []
        for column in headers:
            match = re.fullmatch(r'(Mar|Jun|Sep|Sept|Dec)-(\d{2})', column)
            if not match:
                raise ValueError('NZ_LABOUR_PERIOD_INVALID')
            q = {'Mar': 1, 'Jun': 2, 'Sep': 3, 'Sept': 3, 'Dec': 4}[match[1]]
            # StatsNZ graph uses two-digit years. Resolve relative to the release.
            y = (year // 100) * 100 + int(match[2])
            if y > year + 1:
                y -= 100
            end = _end(y, q)
            if end > period_end:
                raise ValueError('NZ_LABOUR_FUTURE_GRAPH_PERIOD')
            periods.append(end)
        if not periods or max(periods) != period_end:
            raise ValueError('NZ_LABOUR_LATEST_PERIOD_MISSING')
        totals = [row for row in rows[1:] if row and row[0] == 'Total']
        if len(totals) != 1 or len(totals[0]) != len(rows[0]):
            raise ValueError('NZ_LABOUR_TOTAL_INVALID')
        values.append(_rate(totals[0][1 + periods.index(period_end)]))
    if not values or len(set(values)) != 1:
        raise ValueError('NZ_LABOUR_VALUE_CONFLICT')
    due_dates = set()
    body = re.sub(r'\s+', ' ', _text(release))
    pattern = (r'Labour market statistics: (March|June|September|December) (\d{4}) quarter'
               r'\s+will be released on\s+(\d{1,2}) ([A-Za-z]+) (\d{4})')
    for month, next_year, day, due_month, due_year in re.findall(pattern, body):
        next_end = _end(int(next_year), MONTHS[month] // 3)
        if next_end <= period_end:
            continue
        due = datetime(int(due_year), MONTHS[due_month], int(day), tzinfo=ZoneInfo('Pacific/Auckland'))
        if due.date() < next_end or due <= publication:
            raise ValueError('NZ_LABOUR_NEXT_RELEASE_INVALID')
        due_dates.add(due.astimezone(timezone.utc))
    if len(due_dates) > 1:
        raise ValueError('NZ_LABOUR_NEXT_RELEASE_CONFLICT')
    return {'value': values[0], 'date': period_end.isoformat(),
            'reference_period': f'{year}-Q{quarter}', 'unit': 'percent of labour force',
            'frequency': 'quarterly', 'seasonal_adjustment': 'SA', 'source': 'Stats NZ',
            'source_url': source_url or NZ_BASE + f'labour-market-statistics-{calendar.month_name[quarter * 3].lower()}-{year}-quarter/',
            'series_id': 'HLFS:unemployment_rate:Total:SA:Q', 'checked_at': checked.isoformat(),
            'published_at': publication.isoformat(),
            'next_due_at': next(iter(due_dates)).isoformat() if due_dates else None,
            'next_due_precision': 'date_only_start_of_NZ_day' if due_dates else None,
            'reuse_terms': 'https://creativecommons.org/licenses/by/4.0/'}


def fetch_nz_labour(*, now=None, session=None):
    """Probe last completed quarter first; only genuine 404 permits older release."""
    checked = _now(now)
    local = checked.astimezone(ZoneInfo('Pacific/Auckland'))
    serial = local.year * 4 + (local.month - 1) // 3 - 1
    transport = session or requests
    for offset in range(3):
        year, q0 = divmod(serial - offset, 4)
        quarter = q0 + 1
        url = NZ_BASE + f'labour-market-statistics-{calendar.month_name[quarter * 3].lower()}-{year}-quarter/'
        response = transport.get(url, timeout=20)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        return parse_nz_labour(response.text, year=year, quarter=quarter, now=checked, source_url=url)
    return None


def select_ch_labour_resources(payload, *, now=None):
    """Discover newest published official CSV, not a fixed release asset number."""
    checked = _now(now)
    if not isinstance(payload, dict):
        raise ValueError('CH_LABOUR_CATALOG_INVALID')
    result = payload.get('result', {})
    packages = result.get('results')
    if payload.get('success') is not True or not isinstance(packages, list) or result.get('count') != len(packages):
        raise ValueError('CH_LABOUR_CATALOG_INCOMPLETE')
    candidates = []
    for package in packages:
        if package.get('title', {}).get('de') != CH_TITLE:
            continue
        if package.get('organization', {}).get('name') != 'bundesamt-fur-statistik-bfs' or package.get('private') is True:
            raise ValueError('CH_LABOUR_PUBLISHER_INVALID')
        slug = package.get('name', '')
        if not re.fullmatch(r'[a-z0-9-]+', slug):
            raise ValueError('CH_LABOUR_DATASET_URL_INVALID')
        dataset_url = 'https://opendata.swiss/dataset/' + slug
        for resource in package.get('resources', []):
            if resource.get('format', '').upper() != 'CSV':
                continue
            url = resource.get('url', '')
            if resource.get('rights') != CH_RIGHTS or not re.fullmatch(r'https://dam-api\.bfs\.admin\.ch/hub/api/dam/assets/\d+/master', url):
                raise ValueError('CH_LABOUR_RESOURCE_INVALID')
            issued = _timestamp(resource.get('issued'))
            if issued > checked:
                continue
            candidates.append((issued, url, dataset_url))
    if not candidates:
        return []
    latest = max(issued for issued, _, _ in candidates)
    return [{'issued': issued.isoformat(), 'url': url, 'source_url': dataset_url}
            for issued, url, dataset_url in sorted(set(candidates)) if issued == latest]


def parse_ch_labour(content, *, published_at, source_url, now=None):
    checked = _now(now)
    published = _timestamp(published_at)
    if published > checked:
        raise ValueError('CH_LABOUR_FUTURE_PUBLICATION')
    text = content.decode('utf-8-sig') if isinstance(content, bytes) else content.lstrip('\ufeff')
    reader = csv.DictReader(io.StringIO(text))
    required = {'INDICATORS_HRCHY', 'INDICATORS_DE', 'SEASON_D', 'DETAILS_DE', 'PERIOD', 'FREQ', 'MEASURE_DE', 'VALUE', 'STATUS'}
    if not reader.fieldnames or not required.issubset(reader.fieldnames) or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError('CH_LABOUR_CSV_HEADER_INVALID')
    values = {}
    for row in reader:
        if row.get('INDICATORS_HRCHY') != 'P' or row.get('SEASON_D') != 'saisonbereinigte':
            continue
        if any(row.get(k) != v for k, v in {'INDICATORS_DE': 'TOTAL', 'DETAILS_DE': 'Total', 'FREQ': 'Q',
                                           'MEASURE_DE': 'Durchschnittliche Quartalswerte', 'STATUS': 'A'}.items()):
            raise ValueError('CH_LABOUR_TOTAL_METADATA_INVALID')
        match = re.fullmatch(r'(\d{4})-Q([1-4])', row.get('PERIOD', ''))
        if not match:
            raise ValueError('CH_LABOUR_PERIOD_INVALID')
        year, quarter = map(int, match.groups())
        end = _end(year, quarter)
        if end > checked.date() or end > published.date():
            raise ValueError('CH_LABOUR_FUTURE_PERIOD')
        value = _rate(row['VALUE'])
        if end in values and values[end] != value:
            raise ValueError('CH_LABOUR_VALUE_CONFLICT')
        values[end] = value
    if not values:
        raise ValueError('CH_LABOUR_TOTAL_MISSING')
    end = max(values)
    return {'value': values[end], 'date': end.isoformat(), 'reference_period': f'{end.year}-Q{end.month // 3}',
            'unit': 'percent of labour force', 'frequency': 'quarterly', 'seasonal_adjustment': 'SA',
            'source': 'BFS (Swiss Labour Force Survey, ILO)', 'source_url': source_url,
            'series_id': 'BFS:ts-x-03.03.01.03b:P:SA:Q', 'checked_at': checked.isoformat(),
            'published_at': published.isoformat(), 'next_due_at': None, 'reuse_terms': CH_RIGHTS}


def fetch_ch_labour(*, now=None, session=None):
    checked = _now(now)
    transport = session or requests
    response = transport.get(CH_CATALOG, params={
        'q': '"Erwerbslosenquote gemäss ILO nach Geschlecht, Nationalität und Altersgruppen"', 'rows': 100}, timeout=20)
    response.raise_for_status()
    resources = select_ch_labour_resources(response.json(), now=checked)
    observations = []
    for resource in resources:
        response = transport.get(resource['url'], timeout=20)
        response.raise_for_status()
        observations.append(parse_ch_labour(response.content, published_at=resource['issued'],
                                            source_url=resource['source_url'], now=checked))
    if not observations:
        return None
    if len({(x['reference_period'], x['value']) for x in observations}) != 1:
        raise ValueError('CH_LABOUR_RELEASE_CONFLICT')
    return observations[0]


def fetch_quarterly_labour(currency, *, now=None, session=None):
    """Common collector entrypoint; no currencies or definitions are substituted."""
    if currency == 'NZD':
        result = fetch_nz_labour(now=now, session=session)
    elif currency == 'CHF':
        result = fetch_ch_labour(now=now, session=session)
    else:
        raise ValueError('QUARTERLY_LABOUR_CURRENCY_NOT_SUPPORTED')
    if result is not None:
        result['period_label'] = 'Saisonbereinigte Quartalsquote'
    return result
