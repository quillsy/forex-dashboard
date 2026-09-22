"""Qualified public context series; no CORE or signal dependencies."""
import csv
import hashlib
import io
import json
import math
from datetime import date, datetime, timedelta, timezone

import requests

ECB_URL = ('https://data-api.ecb.europa.eu/service/data/EST/'
           'B.EU000A2X2A25.TT+NB?lastNObservations=45&format=csvdata')
STATCAN_URL = 'https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods'
STATCAN_INFO_URL = 'https://www150.statcan.gc.ca/t1/wds/rest/getSeriesInfoFromVector'
STATCAN_BODY = [{'vectorId': 1567083339, 'latestN': 15}]
STATCAN_INFO_BODY = [{'vectorId': 1567083339}]
ECB_KEYS = {'EST.B.EU000A2X2A25.NB': ('NB', '_Z', '0'),
            'EST.B.EU000A2X2A25.TT': ('TT', 'EUR', '6')}
STATCAN_PRODUCT = 12100175
STATCAN_COORDINATE = '1.2.3.2.0.0.0.0.0.0'
STATCAN_VECTOR = 1567083339
STATCAN_TITLE = 'Canada;Domestic export;Energy products;United States'


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Invalid timestamp')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Naive timestamp')
    return result.astimezone(timezone.utc)


def _day(value):
    if not isinstance(value, str):
        raise ValueError('Invalid day')
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('Noncanonical day')
    return result


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _json(content):
    return json.loads(content, object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))


def parse_ecb(content, retrieved_at):
    """Preserve original ECB values and metadata; never manufacture a release time."""
    retrieved = _time(retrieved_at)
    if len(content) > 2_000_000:
        raise ValueError('Oversize ECB response')
    reader = csv.DictReader(io.StringIO(content.decode('utf-8-sig')))
    required = {'KEY', 'FREQ', 'BENCHMARK_ITEM', 'DATA_TYPE_EST', 'TIME_PERIOD',
                'OBS_VALUE', 'OBS_STATUS', 'UNIT_MEASURE', 'UNIT_MULT', 'TITLE', 'TITLE_COMPL'}
    if (reader.fieldnames is None or not required.issubset(reader.fieldnames)
            or any(not field for field in reader.fieldnames)
            or len(reader.fieldnames) != len(set(reader.fieldnames))):
        raise ValueError('ECB columns changed')
    observations = {key: {} for key in ECB_KEYS}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Malformed ECB CSV row')
        key = row['KEY']
        if key not in ECB_KEYS:
            raise ValueError('Unknown ECB series')
        data_type, unit, multiplier = ECB_KEYS[key]
        if (row['FREQ'] != 'B' or row['BENCHMARK_ITEM'] != 'EU000A2X2A25'
                or row['DATA_TYPE_EST'] != data_type or row['UNIT_MEASURE'] != unit
                or row['UNIT_MULT'] != multiplier or row['OBS_STATUS'] != 'A'
                or not row['TITLE'] or not row['TITLE_COMPL']):
            raise ValueError('ECB definition or status changed')
        period = _day(row['TIME_PERIOD'])
        if period > retrieved.date() or period.isoformat() in observations[key]:
            raise ValueError('Future or duplicate ECB day')
        raw = row['OBS_VALUE']
        if not raw or not raw.isdigit() or not 0 < int(raw) < 1_000_000:
            raise ValueError('Invalid ECB observation')
        observations[key][period.isoformat()] = {
            'value': raw, 'unit': unit, 'unit_multiplier': multiplier,
            'title': row['TITLE'], 'title_complete': row['TITLE_COMPL']}
    common = sorted(set.intersection(*(set(items) for items in observations.values())))
    if any(len(items) != 45 for items in observations.values()) or not common:
        raise ValueError('Incomplete ECB response window')
    period = common[-1]
    return {'period': period, 'observations': {key: observations[key][period] for key in ECB_KEYS},
            'retrieved_at': retrieved.isoformat(), 'source_url': ECB_URL,
            'source_sha256': _digest(content)}


def parse_statcan(content, retrieved_at):
    retrieved = _time(retrieved_at)
    if len(content) > 100_000:
        raise ValueError('Oversize StatCan response')
    data = _json(content)
    if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
            or data[0].get('status') != 'SUCCESS'):
        raise ValueError('Unexpected StatCan response')
    item = data[0].get('object')
    if not isinstance(item, dict):
        raise ValueError('Unexpected StatCan series object')
    if (item.get('responseStatusCode') != 0 or item.get('productId') != STATCAN_PRODUCT
            or item.get('coordinate') != STATCAN_COORDINATE
            or item.get('vectorId') != STATCAN_VECTOR):
        raise ValueError('StatCan series identity changed')
    points = item.get('vectorDataPoint')
    if not isinstance(points, list) or len(points) != 15:
        raise ValueError('Invalid StatCan window')
    observations, previous = [], None
    for point in points:
        if not isinstance(point, dict):
            raise ValueError('Invalid StatCan data point')
        period = _day(point['refPer'])
        if (period.day != 1 or period > retrieved.date() or previous and period <= previous
                or point.get('scalarFactorCode') != 3 or point.get('frequencyCode') != 6
                or point.get('decimals') != 0
                or point.get('statusCode') != 0 or point.get('securityLevelCode') != 0
                or point.get('symbolCode') != 0):
            raise ValueError('StatCan dimension, status or chronology changed')
        if previous and (period.year - previous.year) * 12 + period.month - previous.month != 1:
            raise ValueError('Missing StatCan month')
        value = point.get('value')
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 1e10:
            raise ValueError('Invalid StatCan value')
        release = point.get('releaseTime')
        if not isinstance(release, str) or len(release) != 16:
            raise ValueError('Missing StatCan release provenance')
        release_local = datetime.fromisoformat(release)
        if (release_local.tzinfo is not None or release_local.date() < period
                or release_local.replace(tzinfo=timezone(-timedelta(hours=5))) > retrieved):
            raise ValueError('Future or invalid StatCan source time')
        observations.append({'period': period.isoformat(), 'value': value,
                             'release_time_source_local': release})
        previous = period
    return {'period': observations[-1]['period'], 'observations': observations,
            'retrieved_at': retrieved.isoformat(), 'source_url': STATCAN_URL,
            'source_sha256': _digest(content)}


def parse_statcan_info(content):
    if len(content) > 10_000:
        raise ValueError('Oversize StatCan metadata')
    data = _json(content)
    if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
            or data[0].get('status') != 'SUCCESS'):
        raise ValueError('Unexpected StatCan metadata')
    item = data[0].get('object')
    if not isinstance(item, dict):
        raise ValueError('Unexpected StatCan metadata object')
    expected = {'responseStatusCode': 0, 'productId': STATCAN_PRODUCT,
                'coordinate': STATCAN_COORDINATE, 'vectorId': STATCAN_VECTOR,
                'frequencyCode': 6, 'scalarFactorCode': 3, 'decimals': 0,
                'terminated': 0, 'SeriesTitleEn': STATCAN_TITLE, 'memberUomCode': 81}
    if any(item.get(key) != value for key, value in expected.items()):
        raise ValueError('StatCan series metadata changed')
    return STATCAN_TITLE


def fetch(session=None, now=None):
    session = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    retrieved_at = now.astimezone(timezone.utc).isoformat()
    headers = {'User-Agent': 'FXContextResearch/1.0 (official data, once daily)'}
    ecb = session.get(ECB_URL, timeout=30, headers=headers)
    ecb.raise_for_status()
    if 'csv' not in ecb.headers.get('Content-Type', '').lower():
        raise ValueError('Unexpected ECB content type')
    ecb_result = parse_ecb(ecb.content, retrieved_at)
    statcan = session.post(STATCAN_URL, json=STATCAN_BODY, timeout=30, headers=headers)
    statcan.raise_for_status()
    if 'json' not in statcan.headers.get('Content-Type', '').lower():
        raise ValueError('Unexpected StatCan content type')
    statcan_result = parse_statcan(statcan.content, retrieved_at)
    statcan_info = session.post(STATCAN_INFO_URL, json=STATCAN_INFO_BODY, timeout=30, headers=headers)
    statcan_info.raise_for_status()
    if 'json' not in statcan_info.headers.get('Content-Type', '').lower():
        raise ValueError('Unexpected StatCan metadata content type')
    statcan_title = parse_statcan_info(statcan_info.content)
    reference_date = statcan_result['period']
    return {'schema': 'fx-niche-context-v1', 'mode': 'research_only', 'core_eligible': False,
            'signal': None, 'retrieved_at': retrieved_at, 'ecb': ecb_result,
            'ecb_raw_csv': ecb.content.decode('utf-8'), 'statcan_energy': statcan_result,
            'ecb_attribution': 'Source: ECB statistics.',
            'statcan_series_title': statcan_title,
            'statcan_attribution': ('Adapted from Statistics Canada, International merchandise trade by '
                                    'province, commodity, and Principal Trading Partners, '
                                    + reference_date + '. This does not constitute an endorsement '
                                    'by Statistics Canada of this product.')}


def validate_artifact(artifact, now=None):
    now = now or datetime.now(timezone.utc)
    retrieved = _time(artifact['retrieved_at'])
    if retrieved > now.astimezone(timezone.utc):
        raise ValueError('Future retrieval')
    if (artifact.get('schema') != 'fx-niche-context-v1' or artifact.get('mode') != 'research_only'
            or artifact.get('core_eligible') is not False or artifact.get('signal') is not None):
        raise ValueError('Unqualified context artifact')
    ecb, statcan = artifact['ecb'], artifact['statcan_energy']
    if artifact.get('ecb_attribution') != 'Source: ECB statistics.':
        raise ValueError('Missing ECB attribution')
    if artifact.get('statcan_series_title') != STATCAN_TITLE:
        raise ValueError('StatCan cached description changed')
    expected_attribution = ('Adapted from Statistics Canada, International merchandise trade by '
                            'province, commodity, and Principal Trading Partners, '
                            + statcan['period'] + '. This does not constitute an endorsement '
                            'by Statistics Canada of this product.')
    if artifact.get('statcan_attribution') != expected_attribution:
        raise ValueError('Missing StatCan attribution')
    if (not isinstance(artifact.get('ecb_raw_csv'), str)
            or parse_ecb(artifact['ecb_raw_csv'].encode('utf-8'), artifact['retrieved_at']) != ecb):
        raise ValueError('ECB raw cache differs from validated observation')
    if (ecb['source_url'] != ECB_URL or statcan['source_url'] != STATCAN_URL
            or ecb['retrieved_at'] != artifact['retrieved_at']
            or statcan['retrieved_at'] != artifact['retrieved_at']):
        raise ValueError('Source identity mismatch')
    for item in (ecb, statcan):
        if len(item['source_sha256']) != 64 or _day(item['period']) > retrieved.date():
            raise ValueError('Invalid provenance')
    if set(ecb['observations']) != set(ECB_KEYS):
        raise ValueError('ECB series missing')
    for key, (_, unit, multiplier) in ECB_KEYS.items():
        value = ecb['observations'][key]
        if (value['unit'] != unit or value['unit_multiplier'] != multiplier
                or not str(value['value']).isdigit()
                or not 0 < int(value['value']) < 1_000_000
                or not value['title'] or not value['title_complete']):
            raise ValueError('ECB cached value changed')
    points = statcan['observations']
    if not isinstance(points, list) or not points or points[-1]['period'] != statcan['period']:
        raise ValueError('StatCan observations missing')
    previous = None
    for point in points:
        period = _day(point['period'])
        if previous and period <= previous or period > retrieved.date():
            raise ValueError('StatCan cached chronology changed')
        if type(point['value']) not in (int, float) or not math.isfinite(point['value']):
            raise ValueError('StatCan cached value invalid')
        source_time = datetime.fromisoformat(point['release_time_source_local'])
        if (source_time.tzinfo is not None or source_time.date() < period
                or source_time.replace(tzinfo=timezone(-timedelta(hours=5))) > retrieved):
            raise ValueError('Future StatCan cached source time')
        previous = period
    return artifact
