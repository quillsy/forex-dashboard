"""Read-only research context display from verified local artifacts."""
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from collect_niche_context import DATA_NAME, ECB_CSV_NAME, JOURNAL_NAME, STATUS_NAME, _digest, _history
from collect_research import read_archive_status
from niche_context import validate_artifact


def load_context(directory, *, now=None):
    now = now or datetime.now(timezone.utc)
    directory = Path(directory)
    status = read_archive_status(directory / STATUS_NAME)
    if (status.get('schema') != 'fx-niche-collection-v1' or status.get('status') != 'success'
            or status.get('mode') != 'research_only' or status.get('core_eligible') is not False
            or status.get('signal') is not None):
        raise ValueError('Context collection not successful')
    events = _history(directory / JOURNAL_NAME, status.get('archive_head'), now=now)
    if not events or len(events) != status.get('archive_event_count'):
        raise ValueError('Context journal missing')
    artifact = read_archive_status(directory / DATA_NAME)
    validate_artifact(artifact, now=now)
    raw_csv = (directory / ECB_CSV_NAME).read_bytes()
    if (hashlib.sha256(raw_csv).hexdigest() != artifact['ecb']['source_sha256']
            or raw_csv != artifact['ecb_raw_csv'].encode('utf-8')):
        raise ValueError('Raw ECB file differs from validated cache')
    if _digest(artifact) != events[-1]['state_hash']:
        raise ValueError('Current context differs from journal')
    if artifact['retrieved_at'] != status.get('last_success_at'):
        raise ValueError('Context status time mismatch')
    age_hours = (now.astimezone(timezone.utc) - datetime.fromisoformat(artifact['retrieved_at'])).total_seconds() / 3600
    if age_hours < 0 or age_hours > 48:
        raise ValueError('Context cache stale')
    return artifact, len(events)


def render_niche_context(st, directory):
    st.markdown('**G8 Nischendaten · Research-Kontext**')
    st.info('Deskriptive Kontextdaten. Keine CORE-Faktoren, Paar-Freigaben oder Trading-Signale; ein Prognosevorteil ist nicht belegt.')
    try:
        artifact, vintages = load_context(directory)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, json.JSONDecodeError):
        st.warning('Nischendaten nicht verlässlich aktuell oder ihre Historie ist ungültig. Es werden keine Werte angezeigt.')
        return
    ecb, statcan = artifact['ecb'], artifact['statcan_energy']
    st.caption(f"Letzter erfolgreicher Abruf: {artifact['retrieved_at']} · {vintages} archivierte Abrufe seit Start der Sammlung. Abruf ist kein Veröffentlichungszeitpunkt.")
    ecb_age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(ecb['period']).date()).days
    if ecb_age <= 7:
        banks = ecb['observations']['EST.B.EU000A2X2A25.NB']['value']
        volume = ecb['observations']['EST.B.EU000A2X2A25.TT']['value']
        st.write(f"EUR · €STR am {ecb['period']}: {banks} Banken mit gemeldeten Transaktionen vor Trimming; {volume} Mio. EUR nominales Transaktionsvolumen vor Trimming.")
        st.caption('Unveränderte ECB-Beobachtungswerte. Handelstag ist nicht Veröffentlichungstag; keine Liquiditäts- oder FX-Prognose.')
    else:
        st.warning('ECB-Beobachtung älter als sieben Kalendertage; Werte ausgeblendet.')
    st.markdown('[Quelle: ECB-Statistik, €STR NB/TT](https://data.ecb.europa.eu/data/datasets/EST) · [Nutzungsbedingungen](https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.ga.html)')
    st.caption(artifact['ecb_attribution'])
    statcan_age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(statcan['period']).date()).days
    if statcan_age <= 120:
        latest = statcan['observations'][-1]
        display_value = f"{latest['value']:,.0f}".replace(',', '.')
        st.write(f"CAD · Inländische Energieexporte in die USA, Referenzmonat {latest['period'][:7]}: {display_value} Tsd. CAD.")
        st.caption(f"Quelle meldet letzten Änderungs-/Releasezeitpunkt {latest['release_time_source_local']} (ohne Zeitzonenangabe; konservativ als UTC−05 plausibilisiert, keine rückwirkende Erstverfügbarkeit). Nominaler, unbereinigter Zollwert; Revisionen möglich. Exportwert bedeutet keinen unmittelbaren CAD-Kauf.")
    else:
        st.warning('StatCan-Referenzmonat älter als 120 Tage; Wert ausgeblendet.')
    st.markdown('[Quelle: Statistics Canada, Tabelle 12-10-0175-01, Vektor 1567083339](https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1210017501) · [Open Licence](https://www.statcan.gc.ca/en/terms-conditions/open-licence)')
    st.caption(artifact['statcan_attribution'])
