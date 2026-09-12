"""Prospective research state journals. Caller holds the source collector lock.

Hash chains detect accidental changes against saved anchors, not coordinated
rewrites of every journal and anchor. Git history is an independent audit trail.
"""
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from research_panel import validate_research_artifact
from ec_industry_research import validate_artifact

SCHEMA = 'research-vintages-v1'
DEFINITIONS = {
    'bis_nz_policy': {
        'source': 'BIS; underlying national source Reserve Bank of New Zealand',
        'series': 'BIS:WS_CBPOL(1.0):D.NZ', 'instrument': 'Official Cash Rate',
        'geography': 'NZ', 'currency': 'NZD', 'unit': 'percent_per_year',
        'frequency': 'daily', 'seasonal_adjustment': 'not_seasonally_adjusted',
        'endpoint': 'https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.NZ',
        'query_identity': {'dataflow': 'BIS:WS_CBPOL:1.0', 'key': 'D.NZ'},
    },
    'ec_industry': {
        'source': 'European Commission DG ECFIN via Eurostat',
        'series': 'EI_BSIN_M_R2:M.BS-ICI.SA.BAL.EA21',
        'instrument': 'Industry confidence indicator (not PMI)',
        'geography': 'EA21', 'currency': 'EUR', 'unit': 'balance_points',
        'frequency': 'monthly', 'seasonal_adjustment': 'seasonally_adjusted_not_calendar_adjusted',
        'endpoint': 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ei_bsin_m_r2',
        'query_identity': {'freq': 'M', 'indic': 'BS-ICI', 's_adj': 'SA', 'unit': 'BAL', 'geo': 'EA21'},
    },
}
ENVELOPE_FIELDS = {'schema', 'source_id', 'mode', 'core_eligible', 'signal', 'events'}
EVENT_FIELDS = {'sequence', 'previous_event_hash', 'first_observed_at', 'request_started_at',
                'response_sha256', 'state_sha256', 'snapshot', 'dataset_updated_at',
                'published_at', 'origin', 'event_hash'}
SNAPSHOT_FIELDS = {'definition', 'window_start', 'window_end', 'observations', 'absent_calendar_periods'}


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value)
    else:
        raise ValueError('Invalid timestamp')
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Naive timestamp')
    return result.astimezone(timezone.utc)


def _keys(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Unexpected journal fields')


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _number(value, source_id):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Invalid numeric observation')
    if not (-10 if source_id == 'bis_nz_policy' else -100) <= value <= 100:
        raise ValueError('Observation outside source contract')
    return int(value) if value == int(value) else value


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _path(path, source_id):
    if source_id not in DEFINITIONS:
        raise ValueError('Unsupported research source')
    path = Path(path)
    if path.name != source_id + '_vintages.json' or path.is_symlink():
        raise ValueError('Unexpected journal path')
    return path


def _period(period, source_id):
    if not isinstance(period, str):
        raise ValueError('Invalid period')
    if source_id == 'ec_industry':
        if not re.fullmatch('[0-9]{4}-[0-9]{2}', period):
            raise ValueError('Invalid month')
        return date.fromisoformat(period + '-01')
    result = date.fromisoformat(period)
    if result.isoformat() != period or result < date(1999, 3, 17):
        raise ValueError('Invalid BIS date')
    return result


def _absent_months(periods):
    cursor, end = date.fromisoformat(periods[0] + '-01'), date.fromisoformat(periods[-1] + '-01')
    if (end.year-cursor.year)*12 + end.month-cursor.month > 120:
        raise ValueError('Unexpected observation window')
    present, absent = set(periods), []
    while cursor < end:
        key = cursor.strftime('%Y-%m')
        if key not in present:
            absent.append(key)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return absent


def _validate_snapshot(snapshot, source_id, request_started):
    _keys(snapshot, SNAPSHOT_FIELDS)
    if snapshot['definition'] != DEFINITIONS[source_id]:
        raise ValueError('Source definition changed')
    observations = snapshot['observations']
    if not isinstance(observations, list) or not observations:
        raise ValueError('Missing timeline')
    if len(observations) > (13 if source_id == 'ec_industry' else 366):
        raise ValueError('Unexpected response window size')
    normalized, periods = [], []
    for item in observations:
        _keys(item, {'period', 'value', 'status'})
        period, value, status = item['period'], item['value'], item['status']
        day = _period(period, source_id)
        if day > request_started.date():
            raise ValueError('Future observation')
        if source_id == 'bis_nz_policy':
            if status not in ('A', 'M') or (value is None) != (status == 'M'):
                raise ValueError('Unreviewed BIS status')
        elif (value is not None and status is not None) or (value is None and status not in (None, ':')):
            raise ValueError('Unreviewed EC status')
        normalized.append({'period': period, 'value': None if value is None else _number(value, source_id), 'status': status})
        periods.append(period)
    if periods != sorted(set(periods)):
        raise ValueError('Duplicate or unordered observation')
    if snapshot['window_start'] != periods[0] or snapshot['window_end'] != periods[-1]:
        raise ValueError('Window mismatch')
    expected_absent = _absent_months(periods) if source_id == 'ec_industry' else []
    if snapshot['absent_calendar_periods'] != expected_absent:
        raise ValueError('Absent-period mismatch')
    return {
        'definition': DEFINITIONS[source_id], 'window_start': periods[0], 'window_end': periods[-1],
        'observations': normalized, 'absent_calendar_periods': expected_absent,
    }


def _snapshot(source_id, artifact, observed_at):
    if source_id == 'bis_nz_policy':
        validate_research_artifact(artifact, now=observed_at)
        timeline = [{'period': item['observation_date'], 'value': item['value'], 'status': 'A'} for item in artifact['observations']]
        missing = artifact.get('missing_observation_dates')
        if not isinstance(missing, list):
            raise ValueError('Missing BIS missing-date evidence')
        timeline += [{'period': day, 'value': None, 'status': 'M'} for day in missing]
        absent = []
    else:
        checked = validate_artifact(artifact, now=observed_at)
        timeline = [{'period': item['reference_period'], 'value': item['value'], 'status': item['status']} for item in checked['observations']]
        absent = checked['absent_calendar_periods']
    timeline.sort(key=lambda item: item['period'])
    snapshot = {'definition': DEFINITIONS[source_id], 'window_start': timeline[0]['period'],
                'window_end': timeline[-1]['period'], 'observations': timeline, 'absent_calendar_periods': absent}
    return _validate_snapshot(snapshot, source_id, _timestamp(artifact['retrieved_at']))


def _head(events):
    last = events[-1] if events else None
    return {'sequence': last['sequence'] if last else None,
            'event_hash': last['event_hash'] if last else None,
            'event_count': len(events), 'first_observed_at': last['first_observed_at'] if last else None}


def _validate_anchor(anchor, events):
    if anchor is None:
        return
    _keys(anchor, {'sequence', 'event_hash'})
    sequence = anchor['sequence']
    if type(sequence) is not int or sequence < 1 or not _hash(anchor['event_hash']):
        raise ValueError('Invalid archive anchor')
    if sequence > len(events) or events[sequence-1]['event_hash'] != anchor['event_hash']:
        raise ValueError('Archive anchor missing from prefix')


def _read(path, source_id, anchor):
    path = _path(path, source_id)
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        _validate_anchor(anchor, [])
        return None
    journal = json.loads(raw, object_pairs_hook=_object,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))
    _keys(journal, ENVELOPE_FIELDS)
    if (journal['schema'] != SCHEMA or journal['source_id'] != source_id or journal['mode'] != 'research_only'
            or journal['core_eligible'] is not False or journal['signal'] is not None):
        raise ValueError('Invalid journal identity')
    events = journal['events']
    if not isinstance(events, list) or not events:
        raise ValueError('Existing journal cannot be empty')
    previous_hash, previous_observed, previous_request, previous_state = None, None, None, None
    now = _now()
    for sequence, event in enumerate(events, 1):
        _keys(event, EVENT_FIELDS)
        if type(event['sequence']) is not int or event['sequence'] != sequence or event['previous_event_hash'] != previous_hash:
            raise ValueError('Broken event sequence')
        observed, request_started = _timestamp(event['first_observed_at']), _timestamp(event['request_started_at'])
        if event['first_observed_at'] != observed.isoformat() or event['request_started_at'] != request_started.isoformat():
            raise ValueError('Noncanonical event timestamps')
        if request_started > observed or observed > now or (previous_observed and observed <= previous_observed) or (previous_request and request_started < previous_request):
            raise ValueError('Invalid event chronology')
        if event['origin'] != 'collector_observation' or event['published_at'] is not None:
            raise ValueError('Unreviewed availability claim')
        updated = event['dataset_updated_at']
        if updated is not None:
            if source_id != 'ec_industry' or _timestamp(updated) > request_started or updated != _timestamp(updated).isoformat():
                raise ValueError('Invalid database update provenance')
        snapshot = _validate_snapshot(event['snapshot'], source_id, request_started)
        if not all(_hash(event[key]) for key in ('response_sha256', 'state_sha256', 'event_hash')):
            raise ValueError('Invalid digest format')
        if _digest(snapshot) != event['state_sha256'] or event['state_sha256'] == previous_state:
            raise ValueError('Invalid or duplicate adjacent state')
        body = {key: value for key, value in event.items() if key != 'event_hash'}
        if _digest(body) != event['event_hash']:
            raise ValueError('Event digest mismatch')
        previous_hash, previous_state = event['event_hash'], event['state_sha256']
        previous_observed, previous_request = observed, request_started
    _validate_anchor(anchor, events)
    return journal


def inspect_vintage(path, source_id, anchor=None):
    """Validate before fetching; a missing anchored archive is an error."""
    journal = _read(path, source_id, anchor)
    return _head(journal['events'] if journal else [])


def _atomic_write(path, journal):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(journal) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_vintage(path, source_id, artifact, observed_at, anchor=None):
    """Append one changed response state atomically. Never infer publication time.

    observed_at is supplied after a real fetch/validation by the caller. Importing
    old artifacts is not an endorsed bootstrap path; this API never backdates.
    """
    path = _path(path, source_id)
    journal = _read(path, source_id, anchor)
    observed_at = _timestamp(observed_at)
    if observed_at > _now():
        raise ValueError('Future observation timestamp')
    request_started = _timestamp(artifact['retrieved_at'])
    if request_started > observed_at:
        raise ValueError('Observed before retrieval')
    snapshot = _snapshot(source_id, artifact, observed_at)
    state_hash = _digest(snapshot)
    events = journal['events'] if journal else []
    last = events[-1] if events else None
    if last:
        last_observed, last_request = _timestamp(last['first_observed_at']), _timestamp(last['request_started_at'])
        if observed_at < last_observed or request_started < last_request:
            raise ValueError('Clock or request chronology regressed')
        if state_hash == last['state_sha256']:
            return _head(events)
        if observed_at <= last_observed:
            raise ValueError('Changed event requires a later observation time')
    response_hash = artifact.get('payload_sha256')
    if not _hash(response_hash):
        raise ValueError('Missing response digest')
    updated = artifact.get('dataset_updated_at') if source_id == 'ec_industry' else None
    event = {
        'sequence': len(events)+1, 'previous_event_hash': last['event_hash'] if last else None,
        'first_observed_at': observed_at.isoformat(), 'request_started_at': request_started.isoformat(),
        'response_sha256': response_hash, 'state_sha256': state_hash, 'snapshot': snapshot,
        'dataset_updated_at': updated, 'published_at': None, 'origin': 'collector_observation',
    }
    event['event_hash'] = _digest(event)
    journal = {'schema': SCHEMA, 'source_id': source_id, 'mode': 'research_only',
               'core_eligible': False, 'signal': None, 'events': events + [event]}
    _atomic_write(path, journal)
    return _head(journal['events'])
