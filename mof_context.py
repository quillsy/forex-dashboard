"""Japanese MoF weekly securities-flow context, separate from CORE."""
import csv
import hashlib
import io
import re
from datetime import date, datetime, time, timedelta, timezone

import requests

SOURCE_URL = 'https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv'
SOURCE_TITLE = 'International Transactions in Securities (Weekly; based on reports from designated major investors)'
SOURCE_UNIT = '100 million Yen'
JAPAN_TIME = timezone(timedelta(hours=9))
MAX_BYTES = 500_000
WINDOW_WEEKS = 52
PERIOD_PATTERN = re.compile(r'^(\d{4})．(\d{1,2})．(\d{1,2})～\s*(?:(\d{4})．)?(\d{1,2})．(\d{1,2})$')
UPDATE_PATTERN = re.compile(r'^Final Update\s+([A-Za-z]+)\s+(\d{1,2})\s*,\s*(\d{4})$')
NUMBER_PATTERN = re.compile(r'^-?\d{1,3}(?:,\d{3})*$|^-?\d+$')
SECTION_HEADERS = {1: '1. Portfolio Investment Assets',
                   12: '2. Portfolio Investment Liabilities'}
CATEGORY_HEADERS = {1: 'Equity and investment fund shares', 4: 'Long-term debt securities',
                    7: 'Subtotal', 8: 'Short-term debt securities', 11: 'Total',
                    12: 'Equity and investment fund shares', 15: 'Long-term debt securities',
                    18: 'Subtotal', 19: 'Short-term debt securities', 22: 'Total'}
MEASURE_HEADERS = [''] + (['Acquisition', 'Disposition', 'Net',
                           'Acquisition', 'Disposition', 'Net', 'Net',
                           'Acquisition', 'Disposition', 'Net', 'Net'] * 2)


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Invalid timestamp')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Naive timestamp')
    return result.astimezone(timezone.utc)


def _week(period):
    match = PERIOD_PATTERN.fullmatch(period.strip())
    if not match:
        raise ValueError('Unexpected MoF period')
    start_year, start_month, start_day, end_year, end_month, end_day = match.groups()
    start = date(int(start_year), int(start_month), int(start_day))
    end = date(int(end_year or start_year), int(end_month), int(end_day))
    if start.weekday() != 6 or end.weekday() != 5 or end - start != timedelta(days=6):
        raise ValueError('Invalid MoF week')
    return start, end


def _number(value):
    value = value.strip()
    if not NUMBER_PATTERN.fullmatch(value):
        raise ValueError('Invalid MoF number')
    return int(value.replace(',', ''))


def _valid_values(values):
    if not isinstance(values, list) or len(values) != 22 or any(type(value) is not int for value in values):
        raise ValueError('Invalid MoF cell count')
    for offset in (0, 11):
        for acquisition, disposition, net in ((0, 1, 2), (3, 4, 5), (7, 8, 9)):
            if (values[offset + acquisition] < 0 or values[offset + disposition] < 0
                    or abs(values[offset + acquisition] - values[offset + disposition]
                           - values[offset + net]) > 1):
                raise ValueError('MoF net identity changed')
        if (abs(values[offset + 2] + values[offset + 5] - values[offset + 6]) > 1
                or abs(values[offset + 6] + values[offset + 9] - values[offset + 10]) > 1):
            raise ValueError('MoF total identity changed')


def parse_mof(content, observed_at):
    observed = _time(observed_at)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError('Oversize or empty MoF response')
    try:
        rows = list(csv.reader(io.StringIO(content.decode('cp932'))))
    except (csv.Error, UnicodeError) as error:
        raise ValueError('Invalid MoF CSV encoding or structure') from error
    if (len(rows) < WINDOW_WEEKS + 14 or any(len(row) != 23 for row in rows)
            or rows[1][0] != SOURCE_TITLE or rows[4][19] != 'Unit: ' + SOURCE_UNIT
            or {index: value.strip() for index, value in enumerate(rows[8]) if value.strip()}
               != SECTION_HEADERS
            or {index: value.strip() for index, value in enumerate(rows[11]) if value.strip()}
               != CATEGORY_HEADERS
            or [value.strip() for value in rows[13]] != MEASURE_HEADERS):
        raise ValueError('MoF layout or series identity changed')
    update_match = UPDATE_PATTERN.fullmatch(rows[1][19].strip())
    if not update_match:
        raise ValueError('MoF update date missing')
    source_update = datetime.strptime(' '.join(update_match.groups()), '%B %d %Y').date()
    japan_observed = observed.astimezone(JAPAN_TIME)
    if (source_update > japan_observed.date()
            or source_update == japan_observed.date() and japan_observed.time() < time(8, 50)):
        raise ValueError('Future MoF source update')
    data_rows = []
    trailer_start = None
    for index, row in enumerate(rows[14:], 14):
        if not row[0].strip():
            if any(cell.strip() for cell in row):
                raise ValueError('Unexpected MoF data separator')
            trailer_start = index + 1
            break
        data_rows.append(row)
    if len(data_rows) < WINDOW_WEEKS or trailer_start is None:
        raise ValueError('Incomplete MoF history')
    trailer = rows[trailer_start:]
    if any(PERIOD_PATTERN.fullmatch(row[0].strip()) for row in trailer):
        raise ValueError('MoF data after footer')
    notes = [(row[0].strip(), row[1].strip()) for row in trailer
             if row[0].strip().startswith('(Note ')]
    expected_notes = {'(Note 1)', '(Note 2)', '(Note 3)'}
    notes_by_label = dict(notes)
    if (len(notes) != 3 or set(notes_by_label) != expected_notes
            or 'starting from January 2014' not in notes_by_label['(Note 1)']
            or 'rounding' not in notes_by_label['(Note 2)']
            or 'net acquisition with a plus sign' not in notes_by_label['(Note 3)']
            or 'net disposition with a minus sign' not in notes_by_label['(Note 3)']):
        raise ValueError('MoF sign or classification note changed')
    dates = [_week(row[0]) for row in data_rows]
    if any(dates[index][0] != dates[index - 1][1] + timedelta(days=1)
           for index in range(1, len(dates))):
        raise ValueError('Missing or reordered MoF week')
    selected_rows = data_rows[-WINDOW_WEEKS:]
    selected_dates = dates[-WINDOW_WEEKS:]
    if selected_dates[0][0] < date(2014, 1, 1):
        raise ValueError('Pre-2014 MoF sign convention')
    if (source_update < selected_dates[-1][1]
            or selected_dates[-1][1] > observed.astimezone(JAPAN_TIME).date()):
        raise ValueError('MoF observation or update chronology changed')
    observations = []
    for row, (start, end) in zip(selected_rows, selected_dates):
        values = [_number(value) for value in row[1:]]
        _valid_values(values)
        observations.append({'week_start': start.isoformat(), 'week_end': end.isoformat(),
                             'values': values})
    return {'schema': 'fx-mof-context-v1', 'mode': 'research_only', 'core_eligible': False,
            'signal': None, 'source_url': SOURCE_URL, 'source_title': SOURCE_TITLE,
            'unit': SOURCE_UNIT, 'source_sha256': hashlib.sha256(content).hexdigest(),
            'source_update_date': source_update.isoformat(), 'first_observed_at': observed.isoformat(),
            'observations': observations}


def validate_artifact(artifact, *, now=None):
    now = now or datetime.now(timezone.utc)
    if (not isinstance(artifact, dict) or set(artifact) !=
            {'schema', 'mode', 'core_eligible', 'signal', 'source_url', 'source_title',
             'unit', 'source_sha256', 'source_update_date', 'first_observed_at',
             'observations'}):
        raise ValueError('MoF artifact schema changed')
    observed = _time(artifact['first_observed_at'])
    if observed > now.astimezone(timezone.utc):
        raise ValueError('Future MoF first observation')
    if (artifact.get('schema') != 'fx-mof-context-v1' or artifact.get('mode') != 'research_only'
            or artifact.get('core_eligible') is not False or artifact.get('signal') is not None
            or artifact.get('source_url') != SOURCE_URL or artifact.get('source_title') != SOURCE_TITLE
            or artifact.get('unit') != SOURCE_UNIT or not isinstance(artifact.get('source_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', artifact['source_sha256'])):
        raise ValueError('Unqualified MoF context')
    update = date.fromisoformat(artifact['source_update_date'])
    if update > observed.astimezone(JAPAN_TIME).date():
        raise ValueError('Future MoF update')
    observations = artifact['observations']
    if not isinstance(observations, list) or len(observations) != WINDOW_WEEKS:
        raise ValueError('Incomplete MoF window')
    previous_end = None
    for item in observations:
        if not isinstance(item, dict) or set(item) != {'week_start', 'week_end', 'values'}:
            raise ValueError('MoF observation schema changed')
        start = date.fromisoformat(item['week_start'])
        end = date.fromisoformat(item['week_end'])
        if (start < date(2014, 1, 1) or start.weekday() != 6 or end.weekday() != 5
                or end - start != timedelta(days=6) or previous_end and start != previous_end + timedelta(days=1)):
            raise ValueError('Invalid MoF cached week')
        _valid_values(item['values'])
        previous_end = end
    if previous_end > update or previous_end > observed.astimezone(JAPAN_TIME).date():
        raise ValueError('Future MoF cached period')
    return artifact


def fetch(session=None, *, now=None):
    session = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    response = session.get(SOURCE_URL, timeout=(10, 30), stream=True,
                           allow_redirects=False,
                           headers={'User-Agent': 'G8-FX-Research/1.0 (public MoF weekly CSV; once daily)'})
    try:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Unexpected MoF HTTP status')
        mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if mime not in ('text/csv', 'text/plain', 'application/csv',
                        'application/octet-stream', 'application/vnd.ms-excel'):
            raise ValueError('Unexpected MoF content type')
        content = bytearray()
        for chunk in response.iter_content(chunk_size=65_536):
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise ValueError('Oversize MoF response')
        raw = bytes(content)
        artifact = parse_mof(raw, now.isoformat())
        return artifact, raw
    finally:
        response.close()
