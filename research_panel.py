"""Read-only display of qualified, dated research artifacts; no CORE imports."""
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

SOURCE_URL = 'https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.NZ'
RESEARCH_WARNING_DAYS = 14


def render_archive_summary(st, directory, source_id):
    """Show verified archive metadata only; never display unvalidated events."""
    from research_vintages import inspect_vintage
    from collect_research import read_archive_status
    directory = Path(directory)
    if source_id not in ('bis_nz_policy', 'ec_industry'):
        raise ValueError('Unknown research source')
    status_name = 'status.json' if source_id == 'bis_nz_policy' else 'ec_industry_status.json'
    try:
        status_path = directory / status_name
        status = read_archive_status(status_path)
        if status.get('archive_event_count', 0) and status.get('archive_head') is None:
            raise ValueError('Recorded history is missing its anchor')
        if status.get('error_code') == 'RESEARCH_ARCHIVE_INVALID':
            st.warning('Research-Historie: Integritäts- oder Schreibfehler. Der letzte Abruf wurde nicht als neuer verlässlicher Datenstand übernommen.')
        result = inspect_vintage(directory / (source_id + '_vintages.json'),
                                 source_id, anchor=status.get('archive_head'))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        st.warning('Research-Historie nicht verlässlich lesbar. Frühere Informationsstände können derzeit nicht bestätigt werden.')
        return
    if result['event_count'] == 0:
        st.caption('Research-Historie: noch kein tatsächlich erfasster Datenstand archiviert.')
    else:
        st.caption(f"Research-Historie: {result['event_count']} erfasste Datenstände · letzter neuer Stand erkannt: {result['first_observed_at']}")
        st.caption('Erfasst seit Beginn dieser Historie, keine rekonstruierte Erstveröffentlichung. Unveränderte Folgeabrufe erzeugen keinen neuen Eintrag; Revisionen bleiben nachvollziehbar.')


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Invalid observation date')
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError('Noncanonical observation date')
    return result


def _value(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not -10 <= value <= 100:
        raise ValueError('Invalid rate')
    return value


def validate_research_artifact(data, *, now=None):
    """Validate identity and chronology, returning only explicit display fields.

    The artifact is provenance metadata, not a new verification of the provider's
    current rate. Cached age/status fields deliberately do not determine display.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Current time must have a timezone')
    now = now.astimezone(timezone.utc)
    expected = {
        'schema': 'bis-nz-policy-research-v1', 'mode': 'research_only',
        'currency': 'NZD', 'series': 'BIS:WS_CBPOL(1.0):D.NZ',
        'instrument': 'Official Cash Rate', 'unit': 'percent_per_year',
        'seasonal_adjustment': 'not_seasonally_adjusted',
        'observation_frequency': 'daily', 'source_url': SOURCE_URL,
    }
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in expected.items()):
        raise ValueError('Unqualified research identity')
    if data.get('core_eligible') is not False:
        raise ValueError('Research must never qualify for CORE')
    for field in ('published_at', 'next_publication_at', 'rate_effective_date'):
        if field not in data or data[field] is not None:
            raise ValueError('Unreviewed publication/effective metadata')
    if not re.fullmatch(r'[0-9a-f]{64}', str(data.get('payload_sha256', ''))):
        raise ValueError('Missing payload fingerprint')
    retrieved = datetime.fromisoformat(data['retrieved_at'])
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError('Retrieval time must have a timezone')
    retrieved = retrieved.astimezone(timezone.utc)
    if retrieved > now:
        raise ValueError('Future retrieval')
    observed = _date(data['observation_date'])
    value = _value(data['value'])
    if observed < date(1999, 3, 17) or observed > retrieved.date():
        raise ValueError('Observation outside supported history')
    history = data.get('observations')
    if not isinstance(history, list) or not history:
        raise ValueError('Missing observation evidence')
    seen = {}
    for item in history:
        if not isinstance(item, dict):
            raise ValueError('Malformed observation')
        day, rate = _date(item['observation_date']), _value(item['value'])
        if day in seen or day < date(1999, 3, 17) or day > observed:
            raise ValueError('Conflicting observation history')
        seen[day] = rate
    if max(seen) != observed or seen[observed] != value:
        raise ValueError('Latest value disagrees with history')
    age = (now.date() - observed).days
    return {
        'value': value, 'observation_date': observed.isoformat(),
        'retrieved_at': retrieved.isoformat(), 'observation_age_days': age,
        'retrieval_age_hours': (now - retrieved).total_seconds() / 3600,
        'stale': age > RESEARCH_WARNING_DAYS,
        'mode': 'research_only', 'core_eligible': False,
    }


def load_research_artifact(artifact_path, *, now=None):
    with Path(artifact_path).open(encoding='utf-8') as handle:
        data = json.load(handle)
    return validate_research_artifact(data, now=now)


def _render_collection_status(st, artifact_path):
    """Fixed messages only: provider exceptions or arbitrary status text stay private."""
    path = Path(artifact_path).with_name('status.json')
    if not path.exists():
        st.caption('Research-Abrufstatus: kein Laufnachweis vorhanden.')
        return
    try:
        status = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(status, dict) or status.get('schema') != 'bis-research-collection-v1'
                or status.get('mode') != 'research_only' or status.get('core_eligible') is not False
                or status.get('status') not in ('success', 'failed', 'attempt_started')):
            raise ValueError('Invalid status')
        attempt = datetime.fromisoformat(status['last_attempt_at'])
        if attempt.tzinfo is None or attempt.utcoffset() is None or attempt > datetime.now(timezone.utc):
            raise ValueError('Invalid attempt timestamp')
    except (OSError, ValueError, TypeError, KeyError):
        st.warning('Research-Abrufstatus ungültig; letzte Aktualisierung nicht bestätigt.')
        return
    st.caption('Letzter Research-Abrufversuch: ' + attempt.astimezone(timezone.utc).isoformat())
    if status['status'] == 'failed':
        st.warning('Letzter Research-Abruf fehlgeschlagen. Ein eventuell angezeigter Wert stammt aus einem früheren Abruf; seine Aktualität wurde dadurch nicht bestätigt.')
    elif status['status'] == 'attempt_started':
        st.warning('Für den letzten Research-Abrufversuch liegt kein Abschlussnachweis vor. Angezeigte Werte sind nicht durch diesen Versuch aktualisiert.')
    else:
        st.caption('Research-Abruf abgeschlossen. Ein erfolgreicher Abruf bestätigt keine aktuelle RBNZ-Entscheidung.')


def render_research_panel(st, artifact_path):
    """Render without network, quota usage, cache refresh or signal mutation."""
    st.subheader('Research: kostenlose Alternativdaten')
    _render_collection_status(st, artifact_path)
    st.info('Getrennte Forschung: Diese Daten erhöhen weder die CORE-Abdeckung noch die Signal-Freigabe. Eine Verbesserung der Signalqualität ist nicht belegt.')
    try:
        data = load_research_artifact(artifact_path)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        st.warning('Keine gültige BIS-Research-Beobachtung verfügbar. Fehlende oder widersprüchliche Nachweise werden ohne Zahlenwert angezeigt.')
    else:
        st.markdown('**NZD · datierte BIS-Beobachtung des Official Cash Rate**')
        st.metric('Historischer Beobachtungswert (% pro Jahr)', f"{data['value']:.2f} %")
        st.write(f"Beobachtungsdatum: {data['observation_date']} · Alter beim Lesen: {data['observation_age_days']} Tage")
        st.write(f"Abgerufen: {data['retrieved_at']} · Abruf liegt {data['retrieval_age_hours']:.1f} Stunden zurück")
        if data['retrieval_age_hours'] > 24:
            st.warning('Der tägliche Research-Abruf ist überfällig. Dieser gespeicherte Wert wurde seit mehr als 24 Stunden nicht erfolgreich erneut abgerufen.')
        st.write('Veröffentlichungszeit: unbekannt · Wirksamkeitsdatum: unbekannt · Nächste Veröffentlichung: unbekannt')
        st.caption('BIS verteilt diese Reihe wöchentlich; tägliche Beobachtungen belegen keine stündliche Aktualität. Der Abrufzeitpunkt ist kein Veröffentlichungszeitpunkt. Dies ist keine Bestätigung des aktuell gültigen RBNZ-Leitzinses.')
        if data['stale']:
            st.warning('Historische Research-Beobachtung älter als 14 Tage. Diese Grenze ist eine Anzeige-Warnung und keine CORE-Altersgrenze oder Zusage des Anbieters.')
        st.markdown(f'[Quelle: BIS, Central bank policy rates; Reserve Bank of New Zealand]({SOURCE_URL}) · [Methodik](https://www.bis.org/statistics/cbpol/cbpol_doc.pdf) · [Nutzungsbedingungen](https://data.bis.org/help/legal)')
        st.caption('Deutsche Beschreibungen sind keine offizielle Übersetzung der BIS. Der gespeicherte Quellen-Hash dokumentiert den Abruf; er ersetzt keine inhaltliche Quellenprüfung.')
    st.caption('OECD-Kandidaten bleiben in der Quellenprüfung: Nutzungsrechte, Definitionen, Veröffentlichungsstände und G8-Vergleichbarkeit sind noch nicht abschließend qualifiziert. Daher werden hier keine OECD-Werte oder daraus abgeleiteten Signale veröffentlicht.')
