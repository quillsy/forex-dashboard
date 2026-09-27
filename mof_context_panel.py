"""Fail-closed display of verified, independent MoF research context."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from collect_mof_context import DATA_NAME, JOURNAL_NAME, RAW_NAME, STATUS_NAME, _history
from collect_niche_context import _digest
from collect_research import read_archive_status
from mof_context import parse_mof, validate_artifact


def load_context(directory, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Naive display clock')
    now = now.astimezone(timezone.utc)
    directory = Path(directory)
    status = read_archive_status(directory / STATUS_NAME)
    if (status.get('schema') != 'fx-mof-collection-v1' or status.get('status') != 'success'
            or status.get('mode') != 'research_only' or status.get('core_eligible') is not False
            or status.get('signal') is not None or status.get('error_code') is not None):
        raise ValueError('MoF collection not successful')
    events = _history(directory / JOURNAL_NAME, status.get('archive_head'), now=now)
    if not events or len(events) != status.get('archive_event_count'):
        raise ValueError('MoF history missing')
    artifact = read_archive_status(directory / DATA_NAME)
    validate_artifact(artifact, now=now)
    raw = (directory / RAW_NAME).read_bytes()
    if (hashlib.sha256(raw).hexdigest() != artifact['source_sha256']
            or parse_mof(raw, artifact['first_observed_at']) != artifact
            or _digest(artifact) != events[-1]['state_hash']
            or status.get('source_sha256') != artifact['source_sha256']):
        raise ValueError('MoF cache and archive disagree')
    refreshed = datetime.fromisoformat(status['last_success_at'])
    if (refreshed.tzinfo is None or refreshed > now
            or now - refreshed > timedelta(days=7)
            or refreshed < datetime.fromisoformat(artifact['first_observed_at'])):
        raise ValueError('MoF retrieval stale')
    update = date.fromisoformat(artifact['source_update_date'])
    week_end = date.fromisoformat(artifact['observations'][-1]['week_end'])
    if (now.date() - update > timedelta(days=21)
            or now.date() - week_end > timedelta(days=21)):
        raise ValueError('MoF source stale')
    return artifact, status, events


def render_mof_context(st, directory):
    st.markdown('**JPY · Japanische Wertpapierflüsse (MoF, Research-Kontext)**')
    st.info('Wöchentliche, von bestimmten großen Berichterstattern gemeldete Wertpapierabschlüsse; keine gemessenen Devisenkäufe, kein Prognosevorteil, kein CORE-Faktor oder Handelssignal.')
    try:
        artifact, status, events = load_context(directory)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, json.JSONDecodeError):
        st.warning('MoF-Daten nicht verlässlich aktuell oder ihre Historie ist ungültig. Es werden keine Werte angezeigt.')
        return
    latest = artifact['observations'][-1]
    assets = f"{latest['values'][10]:,}".replace(',', '.')
    liabilities = f"{latest['values'][21]:,}".replace(',', '.')
    st.write(f"Woche {latest['week_start']} bis {latest['week_end']} · Nettoerwerb japanischer Anleger im Ausland: {assets}; Nettoerwerb ausländischer Anleger in Japan: {liabilities}. Einheit: 100 Mio. JPY.")
    st.caption('Positiv bedeutet Nettoerwerb, negativ Nettoveräußerung; die beiden Seiten werden nicht saldiert oder in FX-Käufe umgerechnet.')
    st.caption(f"Quellkopf aktualisiert: {artifact['source_update_date']} (kein punktgenauer Veröffentlichungszeitpunkt). Eigener erster Nachweis dieses Dateistands: {artifact['first_observed_at']}; letzter erfolgreicher Abruf: {status['last_success_at']}.")
    last_revisions = events[-1]['revision_week_ends']
    st.caption(f"{len(events)} prospektive Dateistände seit Beginn der Sammlung; im letzten neuen Stand {len(last_revisions)} geänderte überlappende Wochen. Vorherige Erstveröffentlichungen sind unbekannt. Rundungsabweichung bis 1 in der Quelldatei möglich; Fondsklassifikation und Netto-Vorzeichen änderten sich 2014.")
    st.markdown('[Amtliche MoF-Wochenreihe (CSV)](https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv) · [MoF-Nutzungsbedingungen](https://www.mof.go.jp/about_mof/notice/index.html) · [PDL 1.0, maßgebliches japanisches Original](https://www.digital.go.jp/assets/contents/node/basic_page/field_ref_resources/f7fde41d-ffca-4b2a-9b25-94b8a701a037/24afdf33/20240705_resources_data_outline_05.pdf)')
    st.caption('Quelle: Ministry of Finance, Japan, International Transactions in Securities (Weekly). Auswahl und deutsche Beschreibung durch G8 FX Dashboard bearbeitet; keine Billigung durch das Ministerium.')
