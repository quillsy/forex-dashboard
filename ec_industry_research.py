"""Local research prototype, pending rights and publication qualification.

Separate research collection/display; no CORE dependencies or signal computation.
"""
import calendar
import hashlib
import json
import math
import re
from pathlib import Path

import requests
from datetime import date, datetime, timezone

ENDPOINT = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/ei_bsin_m_r2'
PARAMS = dict(freq='M', indic='BS-ICI', s_adj='SA', unit='BAL', geo='EA21', lastTimePeriod=13, lang='EN')
DIMENSIONS = ['freq', 'indic', 's_adj', 'unit', 'geo', 'time']


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be text')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timestamp must have a timezone')
    return result.astimezone(timezone.utc)


def _month(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', value):
        raise ValueError('Invalid reference month')
    return date.fromisoformat(value + '-01')


def _indexed_values(mapping, count, label):
    if not isinstance(mapping, dict):
        raise ValueError('Unsupported ' + label + ' layout')
    for key in mapping:
        if not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]*', key) or int(key) >= count:
            raise ValueError('Invalid ' + label + ' index')
    return mapping


def parse_response(response, *, retrieved_at, now=None):
    """Validate one exact EA21 series. Missing cells remain None.

    Age is explicitly reference-month age, never time since publication.
    """
    retrieved_at = _timestamp(retrieved_at.isoformat() if isinstance(retrieved_at, datetime) else retrieved_at)
    now = now or datetime.now(timezone.utc)
    now = _timestamp(now.isoformat() if isinstance(now, datetime) else now)
    if retrieved_at > now:
        raise ValueError('Future retrieval')
    if (not isinstance(response, dict) or response.get('version') != '2.0'
            or response.get('class') != 'dataset' or response.get('source') != 'ESTAT'
            or response.get('id') != DIMENSIONS):
        raise ValueError('Unexpected JSON-stat dataset or dimension order')
    extension = response.get('extension', {})
    if not isinstance(extension, dict) or extension.get('id') != 'EI_BSIN_M_R2' or extension.get('agencyId') != 'ESTAT':
        raise ValueError('Dataset identity mismatch')
    sizes, dimensions = response.get('size'), response.get('dimension')
    if (not isinstance(sizes, list) or len(sizes) != 6 or any(type(n) is not int or n < 1 for n in sizes)
            or sizes[-1] > 13 or sizes[:5] != [1]*5 or not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS)):
        raise ValueError('Unexpected dimensions')
    for name in DIMENSIONS[:5]:
        index = dimensions[name]['category']['index']
        if index != {PARAMS[name]: 0} or type(index[PARAMS[name]]) is not int:
            raise ValueError('Series definition mismatch')
    index = dimensions['time']['category']['index']
    if (not isinstance(index, dict) or len(index) != sizes[-1]
            or any(type(n) is not int for n in index.values())
            or set(index.values()) != set(range(sizes[-1]))):
        raise ValueError('Time index must be bijective')
    months = sorted(index, key=index.get)
    dates = [_month(month) for month in months]
    if dates != sorted(dates) or any(day > retrieved_at.date().replace(day=1) for day in dates):
        raise ValueError('Unordered or future reference month')
    values = _indexed_values(response.get('value'), len(months), 'value')
    flags = _indexed_values(response.get('status', {}), len(months), 'status')
    observations = []
    for i, month in enumerate(months):
        value, flag = values.get(str(i)), flags.get(str(i))
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not -100 <= value <= 100):
            raise ValueError('Invalid balance points')
        if flag is not None and flag != ':':
            raise ValueError('Unreviewed status flag')
        if value is not None and flag is not None:
            raise ValueError('Numeric observations must be unflagged')
        observations.append(dict(reference_period=month, value=value, status=flag))
    available = [item for item in observations if item['value'] is not None]
    latest = available[-1] if available else None
    updated = _timestamp(response['updated']) if response.get('updated') is not None else None
    if updated and updated > retrieved_at:
        raise ValueError('Future dataset update')
    missing_calendar_months = []
    cursor = dates[0]
    while cursor < dates[-1]:
        if cursor not in dates:
            missing_calendar_months.append(cursor.strftime('%Y-%m'))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    latest_month = _month(latest['reference_period']) if latest else None
    period_end = latest_month.replace(day=calendar.monthrange(latest_month.year, latest_month.month)[1]) if latest else None
    return dict(
        schema='ec-industry-ea21-research-v1', mode='research_only', core_eligible=False,
        qualification_status='research_source_reviewed_2026_09_12', signal=None,
        source='European Commission DG ECFIN via Eurostat', source_url=ENDPOINT,
        source_parameters=PARAMS.copy(), series='EI_BSIN_M_R2:M.BS-ICI.SA.BAL.EA21',
        instrument='Industry confidence indicator (not PMI)', unit='balance_points',
        seasonal_adjustment='seasonally_adjusted_not_calendar_adjusted',
        geography='EA21', frequency='monthly', retrieved_at=retrieved_at.isoformat(),
        dataset_updated_at=updated.isoformat() if updated else None, published_at=None,
        reference_period=latest['reference_period'] if latest else None,
        value=latest['value'] if latest else None,
        latest_indexed_period=months[-1], latest_indexed_period_missing=observations[-1]['value'] is None,
        missing_periods=[o['reference_period'] for o in observations if o['value'] is None],
        absent_calendar_periods=missing_calendar_months,
        reference_period_age_days=max(0, (now.date()-period_end).days) if period_end else None,
        age_basis='days since reference month end; current incomplete month is zero; not publication age',
        observations=observations,
    )


def parse_json(text, *, retrieved_at, now=None):
    response = json.loads(text, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))
    result = parse_response(response, retrieved_at=retrieved_at, now=now)
    result['payload_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return result


def _snapshot(response):
    """Public allowlist; provider annotations, arbitrary text and URLs excluded."""
    return {
        **{key: response[key] for key in ('version', 'class', 'source', 'id', 'size', 'value')},
        'updated': response.get('updated'), 'status': response.get('status', {}),
        'extension': {key: response['extension'][key] for key in ('id', 'agencyId')},
        'dimension': {key: {'category': {'index': response['dimension'][key]['category']['index']}} for key in DIMENSIONS},
    }


def fetch(*, session=None, now=None):
    now = now or datetime.now(timezone.utc)
    session = session or requests.Session()
    response = session.get(ENDPOINT, params=PARAMS, timeout=40, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('Unexpected response status')
    result = parse_json(response.text, retrieved_at=now, now=now)
    source = json.loads(response.text, object_pairs_hook=_unique_object)
    result['source_snapshot'] = _snapshot(source)
    return result


def validate_artifact(data, *, now=None):
    now = now or datetime.now(timezone.utc)
    if (not isinstance(data, dict) or data.get('schema') != 'ec-industry-ea21-research-v1'
            or data.get('mode') != 'research_only' or data.get('core_eligible') is not False
            or 'signal' not in data or data['signal'] is not None
            or not re.fullmatch(r'[0-9a-f]{64}', str(data.get('payload_sha256', '')))):
        raise ValueError('Invalid research artifact')
    result = parse_response(data['source_snapshot'], retrieved_at=data['retrieved_at'], now=now)
    for field in ('source_parameters', 'series', 'unit', 'seasonal_adjustment', 'geography',
                  'instrument', 'published_at', 'dataset_updated_at', 'reference_period', 'value',
                  'latest_indexed_period', 'latest_indexed_period_missing', 'observations',
                  'missing_periods', 'absent_calendar_periods'):
        if field not in data or data[field] != result[field]:
            raise ValueError('Artifact disagrees with validated source')
    # A JSON boolean equals 0/1 numerically; validate duplicated top-level value too.
    if data['value'] is not None and type(data['value']) not in (int, float):
        raise ValueError('Invalid artifact value')
    moment = _timestamp(now.isoformat() if isinstance(now, datetime) else now)
    result['retrieval_age_hours'] = (moment - _timestamp(data['retrieved_at'])).total_seconds()/3600
    period = result['reference_period']
    if period is None or result['latest_indexed_period_missing']:
        result['freshness_state'] = 'missing_observation'
    elif period < '2026-08':
        result['freshness_state'] = 'behind_verified_release'
    elif period == '2026-08':
        # Midnight UTC is explicitly a conservative day guard, not a claimed release time.
        result['freshness_state'] = 'release_due_unconfirmed' if moment.date() >= date(2026, 9, 29) else 'verified_calendar_window'
    else:
        result['freshness_state'] = 'calendar_unknown_recent_check' if result['retrieval_age_hours'] <= 1 else 'calendar_unknown_check_overdue'
    return result


def _render_status(st, path):
    try:
        status = json.loads(Path(path).with_name('ec_industry_status.json').read_text(encoding='utf-8'))
        if (not isinstance(status, dict) or status.get('schema') != 'ec-industry-research-collection-v1'
                or status.get('mode') != 'research_only' or status.get('core_eligible') is not False
                or status.get('status') not in ('success', 'failed', 'attempt_started')):
            raise ValueError('Invalid status')
        attempt = _timestamp(status['last_attempt_at'])
        if attempt > datetime.now(timezone.utc):
            raise ValueError('Future attempt')
    except FileNotFoundError:
        st.caption('EA21-Research: kein Collector-Laufnachweis vorhanden.')
        return
    except (OSError, ValueError, TypeError, KeyError):
        st.warning('EA21-Research: Laufstatus ungültig; Aktualisierung nicht bestätigt.')
        return
    st.caption('Letzter EA21-Research-Abrufversuch: ' + attempt.isoformat())
    if status['status'] == 'failed':
        st.warning('EA21-Research-Abruf fehlgeschlagen. Eventuell angezeigte Daten stammen aus einem früheren erfolgreichen Abruf.')
    elif status['status'] == 'attempt_started':
        st.warning('EA21-Research-Abruf ohne Abschlussnachweis; Aktualisierung nicht bestätigt.')


def render_ec_industry(st, path):
    st.markdown('**EUR · EA21-Industrievertrauen (Research)**')
    st.caption('Umfragesaldo der Europäischen Kommission, kein PMI. Keine CORE-Abdeckung, kein Score, kein Handelssignal und keine belegte Signalverbesserung.')
    _render_status(st, path)
    try:
        data = validate_artifact(json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        st.warning('Keine gültigen EA21-Research-Daten verfügbar; kein Zahlenwert angezeigt.')
        return
    if data['value'] is None:
        st.warning('EA21-Research: keine verfügbare Beobachtung; fehlende Werte werden nicht ersetzt.')
        return
    st.metric('Industrievertrauen · Saldopunkte (historische Beobachtung)', f"{data['value']:.1f}")
    st.write(f"Referenzmonat: {data['reference_period']} · {data['reference_period_age_days']} Tage seit Monatsende")
    st.write(f"Saisonbereinigt, nicht kalenderbereinigt · Gebiet: EA21 · Abgerufen: {data['retrieved_at']}")
    st.write(f"Datenbank aktualisiert: {data['dataset_updated_at'] or 'unbekannt'} · Erstveröffentlichungszeit dieser Beobachtung: unbekannt")
    st.caption('Das Alter wird beim Lesen berechnet. Datenbank-Aktualisierung und Abruf sind keine Erstveröffentlichung. Historische Werte können revidiert sein; keine unveränderte historische Vintage.')
    state = data['freshness_state']
    if state == 'verified_calendar_window':
        st.caption('Geprüfter vollständiger BCS-Bericht: 28.08.2026; nächster vollständiger Bericht: 29.09.2026. Kalenderzeit 11:00, Zeitzone nicht bestätigt. Kalender nur bis zu diesem Termin geprüft. Dies bestätigt den Kalender, nicht jeden später revidierten August-Wert.')
        if data['retrieval_age_hours'] > 1:
            st.warning('Stündlicher EA21-Research-Abruf überfällig; letzter erfolgreicher Abruf liegt mehr als einer Stunde zurück.')
    elif state == 'release_due_unconfirmed':
        st.warning('Neue vollständige Veröffentlichung am 29.09.2026 fällig; weiterhin nur August vorhanden. Ab Tagesbeginn UTC konservativ unbestätigt, kein behaupteter exakter Veröffentlichungstermin.')
    elif state == 'calendar_unknown_check_overdue':
        st.warning('Veröffentlichungskalender für diesen neueren Monat unbekannt; keine erfolgreiche Aktualitätsprüfung innerhalb der letzten Stunde. Beobachtung nur historisch auswerten.')
    elif state == 'calendar_unknown_recent_check':
        st.caption('Veröffentlichungskalender für diesen neueren Monat unbekannt. Erfolgreicher Abruf innerhalb einer Stunde; kein Nachweis einer bestimmten Erstveröffentlichung.')
    else:
        st.warning('Neuester indexierter Monat fehlt oder Daten liegen hinter der bereits geprüften August-Veröffentlichung. Ältere Beobachtung wird ausdrücklich historisch angezeigt.')
    if data['missing_periods'] or data['absent_calendar_periods']:
        st.warning('Datenlücken vorhanden; keine Fortschreibung oder Interpolation. Fehlende Monate: ' + ', '.join(sorted(set(data['missing_periods'] + data['absent_calendar_periods']))))
    st.markdown('[Quelle: Europäische Kommission, DG ECFIN / Eurostat, ei_bsin_m_r2](' + ENDPOINT + ') · [Veröffentlichungen](https://economy-finance.ec.europa.eu/economic-forecast-and-surveys/business-and-consumer-surveys/download-business-and-consumer-survey-data/press-releases_en) · [Wiederverwendung](https://ec.europa.eu/eurostat/web/main/help/copyright-notice)')
    st.caption('Eigene deutsche Darstellung und Altersberechnung; Eurostat übernimmt keine Verantwortung für diese Auswertung. © Europäische Union 2026, Quelle angegeben.')
