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


JP_LABOUR_FILE = 'https://www.e-stat.go.jp/en/stat-search/file-download?fileKind=0&statInfId=000031831358'
JP_LABOUR_RESULTS = 'https://www.stat.go.jp/english/data/roudou/result.html'
JP_LABOUR_CALENDAR = 'https://www.stat.go.jp/english/data/roudou/1543.html'
JP_LABOUR_RIGHTS = 'https://www.stat.go.jp/english/info/riyou.html'


def _jp_space(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def parse_japan_labour_release(results_html, calendar_html, *, now=None):
    """Validate the published monthly period against the independent release calendar.

    Calendar dates are not observed publication times. Conservatively expire at
    the next release's start of day in Japan, explicitly labelled as date-only.
    """
    from bs4 import BeautifulSoup
    checked = _now(now)
    local_day = checked.astimezone(ZoneInfo('Asia/Tokyo')).date()
    releases = []
    for tr in BeautifulSoup(results_html, 'html.parser').find_all('tr'):
        cells = [_jp_space(x.get_text(' ', strip=True)) for x in tr.find_all(['td', 'th'], recursive=False)]
        if cells and cells[0] == 'Monthly':
            if len(cells) < 2:
                raise ValueError('JP_LABOUR_RELEASE_INVALID')
            match = re.fullmatch(r'- ([A-Za-z]+) (\d{4}) - \(Released on ([A-Za-z]+) (\d{1,2}), (\d{4})\) Main results', cells[1])
            if not match:
                raise ValueError('JP_LABOUR_RELEASE_INVALID')
            month, year, release_month, day, release_year = match.groups()
            period = f'{int(year):04d}-{MONTHS[month]:02d}'
            released = datetime(int(release_year), MONTHS[release_month], int(day)).date()
            releases.append((period, released))
    if len(releases) != 1:
        raise ValueError('JP_LABOUR_RELEASE_IDENTITY_INVALID')
    period, released = releases[0]
    if released > local_day:
        raise ValueError('JP_LABOUR_FUTURE_RELEASE')
    schedule = {}
    year = None
    for tr in BeautifulSoup(calendar_html, 'html.parser').find_all('tr'):
        cells = [_jp_space(x.get_text(' ', strip=True)) for x in tr.find_all(['td', 'th'], recursive=False)]
        if len(cells) != 4 or cells[0] == 'Reference month':
            continue
        match = re.match(r'(?:(\d{4}) )?([A-Za-z]+)(?:,|$)', cells[0])
        due_match = re.fullmatch(r'([A-Za-z]+) (\d{1,2})(?:, (\d{4}))?', cells[1])
        if not match or not due_match or match[2] not in MONTHS or due_match[1] not in MONTHS:
            raise ValueError('JP_LABOUR_CALENDAR_INVALID')
        if match[1]:
            year = int(match[1])
        if year is None:
            raise ValueError('JP_LABOUR_CALENDAR_YEAR_MISSING')
        month = MONTHS[match[2]]
        due_year = int(due_match[3]) if due_match[3] else year
        due = datetime(due_year, MONTHS[due_match[1]], int(due_match[2])).date()
        end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
        key = f'{year:04d}-{month:02d}'
        if due <= end or key in schedule:
            raise ValueError('JP_LABOUR_CALENDAR_CONFLICT')
        schedule[key] = due
    if schedule.get(period) != released:
        raise ValueError('JP_LABOUR_RELEASE_CALENDAR_CONFLICT')
    newer = [(p, d) for p, d in schedule.items() if p > period]
    if not newer:
        raise ValueError('JP_LABOUR_NEXT_RELEASE_UNKNOWN')
    next_period, next_day = min(newer)
    y, m = map(int, period.split('-'))
    serial = y * 12 + m
    expected_y, expected_m0 = divmod(serial, 12)
    if next_period != f'{expected_y:04d}-{expected_m0 + 1:02d}':
        raise ValueError('JP_LABOUR_CALENDAR_GAP')
    if next_day <= local_day:
        raise ValueError('JP_LABOUR_NEW_RELEASE_DUE')
    return {'reference_period': period, 'release_date_known': released.isoformat(),
            'next_due_at': datetime.combine(next_day, datetime.min.time(), ZoneInfo('Asia/Tokyo')).astimezone(timezone.utc).isoformat()}


def parse_japan_labour(content, release, *, now=None):
    """Read official historical 1-a-1 SA both-sexes rate; reject incomplete periods."""
    from openpyxl import load_workbook
    checked = _now(now)
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        if '季節調整値' not in workbook.sheetnames:
            raise ValueError('JP_LABOUR_SA_SHEET_MISSING')
        rows = list(workbook['季節調整値'].iter_rows(values_only=True))
    finally:
        workbook.close()
    if len(rows) < 11 or len(rows[6]) != 22:
        raise ValueError('JP_LABOUR_SCHEMA_INVALID')
    if ('Historical data 1 a-1' not in _jp_space(rows[1][4]) or
            'Whole Japan, Monthly Data' not in _jp_space(rows[1][4]) or
            'Seasonally adjusted series' not in _jp_space(rows[4][4]) or
            _jp_space(rows[6][19]) != 'Unemployment rate (percent)' or
            _jp_space(rows[8][19]) != 'Both sexes'):
        raise ValueError('JP_LABOUR_SERIES_IDENTITY_INVALID')
    values = {}
    year, last_month = None, None
    for row in rows[10:]:
        month_match = re.fullmatch(r'(\d{1,2})月', _jp_space(row[1]))
        if not month_match:
            continue
        month = int(month_match[1])
        if not 1 <= month <= 12:
            raise ValueError('JP_LABOUR_MONTH_INVALID')
        label = _jp_space(row[0])
        if label == '※注_Notes':
            label = ''  # Official note marker, not a year reset.
        explicit_year = None
        if re.fullmatch(r'\d{4}', label):
            explicit_year = int(label)
        elif label:
            era = re.fullmatch(r'(昭和|平成|令和)\s*(\d+|元)年', label)
            if not era:
                raise ValueError('JP_LABOUR_YEAR_INVALID')
            explicit_year = {'昭和': 1925, '平成': 1988, '令和': 2018}[era[1]] + (1 if era[2] == '元' else int(era[2]))
        if year is None:
            if explicit_year is None:
                raise ValueError('JP_LABOUR_YEAR_MISSING')
            year = explicit_year
        else:
            expected_year = year + (1 if last_month == 12 and month == 1 else 0)
            if month != last_month % 12 + 1 or (explicit_year is not None and explicit_year != expected_year):
                raise ValueError('JP_LABOUR_PERIOD_SEQUENCE_INVALID')
            year = expected_year
        last_month = month
        if row[19] is None:
            continue
        period = f'{year:04d}-{month:02d}'
        if period > release['reference_period']:
            raise ValueError('JP_LABOUR_UNCONFIRMED_PERIOD')
        values[period] = _rate(row[19])
    if not values or max(values) != release['reference_period']:
        raise ValueError('JP_LABOUR_LATEST_PERIOD_MISSING')
    period = max(values)
    year, month = map(int, period.split('-'))
    end = datetime(year, month, calendar.monthrange(year, month)[1]).date()
    release_day = datetime.fromisoformat(release['release_date_known']).date()
    due = _timestamp(release['next_due_at'])
    if end >= release_day or release_day > checked.astimezone(ZoneInfo('Asia/Tokyo')).date() or due <= checked:
        raise ValueError('JP_LABOUR_RELEASE_TIME_INVALID')
    return {'value': values[period], 'date': end.isoformat(), 'reference_period': period,
            'unit': 'percent of labour force age 15+', 'frequency': 'monthly',
            'seasonal_adjustment': 'SA', 'source': 'Statistics Bureau of Japan (Labour Force Survey)',
            'source_url': JP_LABOUR_FILE, 'series_id': 'Historical1-a-1:unemployment_rate:BothSexes:SA:M',
            'checked_at': checked.isoformat(), 'published_at': None,
            'release_date_known': release['release_date_known'], 'next_due_at': release['next_due_at'],
            'next_due_precision': 'date_only_start_of_JP_day', 'reuse_terms': JP_LABOUR_RIGHTS}


def fetch_japan_labour(*, now=None, session=None):
    checked = _now(now)
    transport = session or requests
    pages = []
    for url in (JP_LABOUR_RESULTS, JP_LABOUR_CALENDAR):
        response = transport.get(url, timeout=20)
        response.raise_for_status()
        pages.append(response.text)
    release = parse_japan_labour_release(*pages, now=checked)
    response = transport.get(JP_LABOUR_FILE, timeout=20)
    response.raise_for_status()
    return parse_japan_labour(response.content, release, now=checked)
