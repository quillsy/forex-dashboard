"""Public, normalized live observations. No credentials or raw responses on disk."""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODEL = "CORE_V2_8_2026_09"
PATH = Path("live_core_data.json")
FACTORS = {"Geldpolitik": 35, "Inflation": 20, "Arbeitsmarkt": 20, "PMI": 20, "GDP": 5}
CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
OBS_FIELDS = {"value", "policy_rate", "yield_2y", "date", "source", "series_id", "frequency",
              "unit", "seasonal_adjustment", "reference_period", "published_at", "checked_at",
              "next_due_at", "freshness", "m_last", "s_last", "m_ref", "s_ref", "m_src", "s_src"}
OBS_FIELDS.update({"provider_status", "release_date_known", "reference_start", "reference_end", "period_label", "is_estimate", "source_url", "next_due_precision", "needs_hourly_check", "transformation", "publication_basis", "geography", "release_stage", "license", "redistribution_status", "source_title"})


def now_utc():
    return datetime.now(timezone.utc)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) else None
    except (TypeError, ValueError, OverflowError):
        return None


def runtime_directory():
    """Instance-local cache location; computing the path creates no files."""
    identity = hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / ("fx-dashboard-live-" + identity)


def _load_file(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) and data.get("model_version") == MODEL else {}
    except (OSError, ValueError):
        return {}


def selected_live_directory():
    """Choose one completed dataset, never blend observations from two runs."""
    directory = Path.cwd()
    if os.environ.get("FX_COLLECTOR") == "1":
        return directory
    now = now_utc()
    latest = None
    for candidate in (directory, runtime_directory()):
        completed = timestamp(_load_file(candidate / PATH).get("completed_at"))
        if completed is not None and completed <= now and (latest is None or completed > latest):
            directory, latest = candidate, completed
    return directory


def load(path=PATH):
    path = Path(path)
    if path == PATH and os.environ.get("FX_COLLECTOR") != "1":
        path = selected_live_directory() / PATH
    return _load_file(path)


def save(data, path=PATH):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".live-core-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def eligible(record, now=None, factor=None, currency=None):
    now = now or now_utc()
    if not isinstance(record, dict):
        return False, "Daten fehlen oder sind nicht validiert"
    if record.get("validation") != "VALID":
        return False, record.get("reason") or "Daten fehlen oder Quellenprüfung offen"
    if number(record.get("score")) is None:
        return False, record.get("reason") or "Daten fehlen oder sind nicht validiert"
    observation = record.get("observation")
    if factor is not None and record.get("factor") != factor:
        return False, "Faktor-Zuordnung widersprüchlich"
    factor = factor if factor is not None else record.get("factor")
    if not isinstance(observation, dict) or factor not in FACTORS:
        return False, "Belegte Faktor-Beobachtung fehlt"
    values = ("policy_rate", "yield_2y") if factor == "Geldpolitik" else ("value",)
    if any(number(observation.get(key)) is None for key in values):
        return False, "Ungültiger Beobachtungswert"
    try:
        reference = datetime.strptime(observation.get("date"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "Referenzperiode fehlt"
    from source_contracts import KNOWN_RELEASES, KNOWN_SOURCE_CONFLICTS
    conflict = KNOWN_SOURCE_CONFLICTS.get((currency, factor, reference.strftime("%Y-%m")))
    if conflict and now >= timestamp(conflict["confirmed_at"]):
        return False, conflict["reason"]
    release = KNOWN_RELEASES.get((currency, factor))
    if release and now >= timestamp(release.get("published_at") or release["confirmed_at"]):
        minimum = datetime.strptime(release["period_start"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if reference < minimum:
            return False, "Neuere amtliche Referenzperiode veröffentlicht: " + release["label"]
    if reference > now or record.get("freshness") not in ("FRESH", "AGING"):
        return False, "Referenzperiode oder Altersprüfung ungültig"
    if any(record.get(field) is not None and timestamp(record[field]) is None
           for field in ("published_at", "next_due_at", "checked_at", "expires_at")):
        return False, "Ungültige Zeitangabe"
    checked = timestamp(record.get("checked_at"))
    if checked is None or checked > now:
        return False, "Erfolgreiche Aktualitätsprüfung fehlt"
    published = timestamp(record.get("published_at"))
    if published and published > now:
        return False, "Veröffentlichung liegt in der Zukunft"
    due = timestamp(record.get("next_due_at"))
    expires = timestamp(record.get("expires_at"))
    if expires is None or now >= expires:
        return False, "Altersgrenze überschritten"
    if due is not None:
        if now >= due:
            return False, "Neue Veröffentlichung oder Prüfung fällig"
    if (due is None or observation.get("needs_hourly_check") is True) and now - checked >= timedelta(hours=1):
        return False, "Aktualität seit über einer Stunde unbestätigt"
    return True, "Geprüft" if not record.get("last_error") else "Gespeicherte Daten gültig; Quelle gestört"


def details(currency, now=None, data=None):
    now = now or now_utc()
    data = load() if data is None else data
    records = data.get("currencies", {}).get(currency, {})
    completed = timestamp(data.get("completed_at"))
    result = {"BCI": None, "_observations": {}, "_freshness": {}, "_live_reasons": {},
              "_blocking_reasons": {}, "_live_checked": completed is not None and completed <= now}
    for factor in FACTORS:
        record = records.get(factor, {})
        record = record if isinstance(record, dict) else {}
        valid, reason = eligible(record, now, factor=factor, currency=currency)
        result[factor] = number(record.get("score")) if valid else None
        result["_freshness"][factor] = record.get("freshness", "FRESH") if valid else "UNAVAILABLE"
        result["_observations"][factor] = copy.deepcopy(record.get("observation", {}))
        result["_live_reasons"][factor] = reason
        result["_blocking_reasons"][factor] = None if valid else reason
    result["_missing"] = [factor for factor in FACTORS if result[factor] is None]
    result["_completeness"] = sum(weight for factor, weight in FACTORS.items() if result[factor] is not None)
    return result


def public_observation(observation):
    """Strict field projection; never persist provider exception bodies or URLs."""
    result = {}
    for key in OBS_FIELDS:
        value = observation.get(key)
        if key == "source_url":
            official_links = {
                "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_gdp",
                "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve",
                "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410028701",
                "https://data.api.abs.gov.au/rest/data/LF/M13.3.1599.20.AUS.M",
                "https://data.api.abs.gov.au/rest/data/ANA_AGG/M1.GPM.20.AUS.Q",
                "https://www.e-stat.go.jp/en/stat-search/file-download?fileKind=0&statInfId=000031831358",
            }
            if isinstance(value, str) and re.fullmatch(r"https://dam-api\.bfs\.admin\.ch/hub/api/dam/assets/[1-9]\d*/master", value):
                result[key] = value
                continue
            if isinstance(value, str) and re.fullmatch(
                    r"https://www\.esri\.cao\.go\.jp/jp/sna/data/data_list/sokuhou/files/(\d{4})/qe(\d{3})_([12])/tables/gaku-jk\2\3\.csv", value):
                result[key] = value
                continue
            if isinstance(value, str) and value in official_links:
                result[key] = value
                continue
            if isinstance(value, str) and len(value) <= 400 and re.fullmatch(
                r"https://(?:www\.stats\.govt\.nz/information-releases/labour-market-statistics-[a-z]+-\d{4}-quarter/?|opendata\.swiss/(?:en/)?dataset/erwerbslosenquote-gemass-ilo-[a-z0-9-]+/?)", value):
                result[key] = value
            continue
        if value is None or isinstance(value, (bool, int, float)):
            result[key] = value if value is None or isinstance(value, bool) else number(value)
        elif isinstance(value, str) and len(value) <= 180 and not any(x in value.lower() for x in ("http", "token=", "key=", "bearer ")):
            result[key] = value
    return result


def build_record(factor, score, observation, freshness, checked_at, previous=None, validation="VALID", reason=None):
    checked = timestamp(checked_at)
    if checked is None:
        raise ValueError("UTC_CHECK_TIME_REQUIRED")
    valid_score = number(score)
    # An unsuccessful check must not advance the last good check timestamp.
    if valid_score is None or validation != "VALID":
        if (isinstance(previous, dict) and previous.get("validation") == "VALID"
                and number(previous.get("score")) is not None
                and validation in ("VALID", "SOURCE_UNAVAILABLE")):
            record = {key: copy.deepcopy(previous.get(key)) for key in
                      ("factor", "score", "validation", "reason", "freshness", "checked_at", "published_at", "next_due_at", "expires_at")}
            record["observation"] = public_observation(previous.get("observation", {}))
            record["last_error"] = "SOURCE_UNAVAILABLE"
            record["last_attempt_at"] = checked_at
            return record
        return {"score": None, "validation": validation, "reason": reason or "Daten fehlen",
                "last_attempt_at": checked_at, "observation": public_observation(observation)}
    observed = observation.get("date")
    try:
        # Source parsers provide ISO reference dates. Preserve their semantics.
        reference = datetime.fromisoformat(str(observed)[:10]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {"score": None, "validation": "UNVERIFIED", "reason": "Referenzperiode nicht eindeutig",
                "observation": public_observation(observation), "last_attempt_at": checked_at}
    if reference > checked:
        return {"score": None, "validation": "FAILED", "reason": "Zukünftige Referenzperiode"}
    # Match the existing maximum ages, and never extend them with a fetch.
    if factor in ("Inflation", "Arbeitsmarkt", "PMI"):
        import calendar
        reference = reference.replace(day=calendar.monthrange(reference.year, reference.month)[1])
    max_days = 15 if factor == "Geldpolitik" else 180 if factor == "GDP" or observation.get("frequency") == "quarterly" else 90
    freshness = "AGING" if "AGING" in str(freshness) else "FRESH" if "FRESH" in str(freshness) else "UNAVAILABLE"
    return {"factor": factor, "score": valid_score, "validation": "VALID", "freshness": freshness,
            "checked_at": checked_at, "last_attempt_at": checked_at,
            "published_at": observation.get("published_at"),
            "next_due_at": observation.get("next_due_at"),
            "expires_at": (reference + timedelta(days=max_days)).isoformat(),
            "observation": public_observation(observation)}


def collect(app, path=PATH):
    """One cold collector run; only individually qualified observations publish."""
    from source_contracts import validate_fred_metadata
    previous = load(path)
    checked_at = now_utc().isoformat()
    data = {"model_version": MODEL, "last_attempt_at": checked_at, "currencies": {}}
    metadata = {}

    def fred_contract(series, category):
        if not series:
            return False
        if not app.FRED_KEY:
            return None
        try:
            if series not in metadata:
                response = app.requests.get("https://api.stlouisfed.org/fred/series",
                    params={"series_id": series, "api_key": app.FRED_KEY, "file_type": "json"}, timeout=12)
                response.raise_for_status()
                metadata[series] = response.json()
        except Exception:
            return None
        # A transport failure cannot prove a definition conflict.
        return validate_fred_metadata(metadata[series], series, category)

    for currency in CURRENCIES:
        raw = app.compute_currency_details(currency, include_context=False)
        data["currencies"][currency] = {}
        for factor in FACTORS:
            observation = dict(raw.get("_observations", {}).get(factor, {}))
            if observation.get("date") is not None:
                observation["date"] = str(observation["date"])[:10]
            observation.setdefault("published_at", None)
            observation.setdefault("frequency", "daily" if factor == "Geldpolitik" else "quarterly" if factor == "GDP" or (factor == "Inflation" and currency == "NZD") else "monthly")
            validation = observation.pop("_validation", "VALID")
            reason = observation.pop("_reason", None)
            if factor == "PMI":
                validation, reason = "UNVERIFIED", "PMI: Survey-Identität und öffentliche Nutzungsrechte noch nicht bestätigt"
                if currency in ("USD", "EUR", "GBP", "JPY", "CAD", "AUD"):
                    reason = "PMI: Anbieterfreigabe für automatisierten Abruf und öffentliche Nutzung fehlt"
                else:
                    reason = "PMI: Nutzungsfreigabe für beide Original-Erhebungen noch nicht nachgewiesen"
            elif factor in ("Arbeitsmarkt", "GDP") and currency not in ("EUR", "GBP") and not (factor == "Arbeitsmarkt" and currency in ("CHF", "NZD", "JPY", "CAD")) and currency not in ("AUD", "JPY") and not (factor == "GDP" and currency == "CHF"):
                contract = fred_contract(observation.get("series_id"), factor)
                if contract is not True:
                    validation = "SOURCE_UNAVAILABLE" if contract is None else "UNVERIFIED"
                    reason = "Metadatenquelle vorübergehend nicht erreichbar" if contract is None else "Amtliche Serien-Metadaten fehlen oder passen nicht"
            elif factor == "Inflation" and currency == "USD":
                contract = fred_contract("CPIAUCNS", factor)
                if contract is not True:
                    validation = "SOURCE_UNAVAILABLE" if contract is None else "UNVERIFIED"
                    reason = "CPI-Metadatenquelle vorübergehend nicht erreichbar" if contract is None else "CPI-Metadaten nicht bestätigt"
            elif factor == "Geldpolitik":
                source = observation.get("source") or ""
                if "EODHD" in source:
                    validation, reason = "UNVERIFIED", "Aktualität der gespeicherten Rendite nicht erneut bestätigt"
                elif currency == "USD" and source.startswith("US Treasury"):
                    if source.endswith("SOURCE_CONFLICT"):
                        validation, reason = "UNVERIFIED", "Amtliche Treasury-Renditereihe widersprüchlich oder ungeprüft"
                    elif source.endswith("SOURCE_UNAVAILABLE"):
                        validation, reason = "SOURCE_UNAVAILABLE", "Treasury-Quelle vorübergehend nicht erreichbar"
                elif currency == "USD":
                    contract = fred_contract("DGS2", factor)
                    if contract is not True:
                        validation = "SOURCE_UNAVAILABLE" if contract is None else "UNVERIFIED"
                        reason = "Rendite-Metadatenquelle vorübergehend nicht erreichbar" if contract is None else "Rendite-Metadaten nicht bestätigt"
                # Policy verification must also have succeeded during this run.
                policy = app.get_verified_policy_rate(currency)
                verified = policy.get("verification_timestamp") or policy.get("verified_at")
                if verified is None:
                    verified = policy.get("last_verified_at")
                verified_at = timestamp(verified)
                if verified_at is None or now_utc() - verified_at >= timedelta(hours=1):
                    validation, reason = "UNVERIFIED", "Aktuelle Leitzinsprüfung fehlt"
                if currency == "NZD" and os.environ.get("FX_RBNZ_AUTOMATION_APPROVED") != "1":
                    validation, reason = "UNVERIFIED", "RBNZ: Freigabe für automatisierten Zugriff fehlt; 2J-Rendite ebenfalls ungeprüft"
                elif validation == "VALID" and number(observation.get("yield_2y")) is None:
                    reason = "Keine aktuell geprüfte 2J-Rendite mit passender Definition verfügbar"
            if validation == "VALID":
                observation.setdefault("unit", "percent per annum" if factor == "Geldpolitik" else "percent YoY" if factor in ("Inflation", "GDP") else "percent of labour force")
                if factor in ("Arbeitsmarkt", "GDP"):
                    observation.setdefault("seasonal_adjustment", "SA")
                elif factor == "Inflation":
                    observation.setdefault("seasonal_adjustment", "NSA")
            record = build_record(factor, raw.get(factor), observation,
                raw.get("_freshness", {}).get(factor, "UNAVAILABLE"), checked_at,
                previous.get("currencies", {}).get(currency, {}).get(factor), validation, reason)
            data["currencies"][currency][factor] = record
    counts = [eligible(record, factor=factor, currency=currency)[0]
              for currency, records in data["currencies"].items() for factor, record in records.items()]
    data["eligible_factors"] = sum(counts)
    data["status"] = "SUCCESS" if all(counts) else "PARTIAL" if any(counts) else "FAILED"
    data["completed_at"] = now_utc().isoformat()
    save(data, path)
    return {"status": data["status"], "eligible_factors": sum(counts), "total_factors": 40}


def render_status(st, authorized=False):
    data = load()
    checked = timestamp(data.get("completed_at"))
    st.caption("LIVE-ANALYSE · G8 · Fundamentaler Horizont: 1–2 Wochen")
    if checked is None or checked > now_utc():
        st.error("Noch kein geprüfter Live-Datensatz vorhanden. Paar-Signale sind gesperrt.")
    elif now_utc() - checked >= timedelta(hours=1):
        st.warning("Der letzte abgeschlossene Abruf liegt über eine Stunde zurück. Die Aktualität wird je Faktor geprüft; abgelaufene Freigaben sind gesperrt.")
    else:
        st.info("Zentraler Datenabruf: " + checked.strftime("%d.%m.%Y %H:%M UTC") + " · Ziel: neue Veröffentlichungen binnen einer Stunde berücksichtigen.")
    st.caption("Abdeckung misst verfügbare geprüfte Faktoren, keine Trefferwahrscheinlichkeit. Kontext und historische Detailansichten können unvollständig sein; der Live-Datenstatus unten ist maßgeblich.")
    rows = []
    for currency in CURRENCIES:
        for factor in FACTORS:
            record = data.get("currencies", {}).get(currency, {}).get(factor, {})
            record = record if isinstance(record, dict) else {}
            valid, reason = eligible(record, factor=factor, currency=currency)
            observation = record.get("observation", {})
            rows.append({"Währung": currency, "Faktor": factor,
                         "Status": "Verfügbar" if valid else "Gesperrt", "Grund": reason,
                         "Letzter Abruf": ("Fehlgeschlagen; letzter geprüfter Wert" if number(record.get("score")) is not None else "Fehlgeschlagen; kein geprüfter Wert") if record.get("last_error") else "Siehe Prüfzeit",
                         "Wert": observation.get("yield_2y") if factor == "Geldpolitik" else observation.get("value"),
                         "Leitzins (%)": observation.get("policy_rate") if factor == "Geldpolitik" else None,
                         "Referenzperiode": observation.get("reference_period") or observation.get("date"),
                         "Quelle": observation.get("source"), "Datensatz": observation.get("source_title"), "Einheit": observation.get("unit"),
                         "Quellenlink": public_observation(observation).get("source_url"),
                         "Messzeitraum": observation.get("period_label") or observation.get("frequency"),
                         "Veröffentlichungsstatus": "Amtlich vorläufig" if observation.get("is_estimate") is True or any(flag in str(observation.get("provider_status") or "").split() for flag in ("e", "p")) else observation.get("provider_status") or "Keine Vorläufigkeitskennzeichnung gemeldet",
                         "Veröffentlicht": record.get("published_at") or (str(observation["release_date_known"]) + " (Uhrzeit unbekannt)" if observation.get("release_date_known") else "Unbekannt"),
                         "Erfolgreich geprüft": record.get("checked_at") or "Nicht bestätigt",
                         "Nächste Fälligkeit": ((record.get("next_due_at") or "") + " (vorsorglich ab Tagesbeginn " + {"date_only_start_of_NZ_day": "Neuseeland", "date_only_start_of_JP_day": "Japan", "date_only_start_of_AU_day": "Australien"}[observation["next_due_precision"]] + "; Veröffentlichungsuhrzeit unbekannt)") if observation.get("next_due_precision") in ("date_only_start_of_NZ_day", "date_only_start_of_JP_day", "date_only_start_of_AU_day") else record.get("next_due_at") or "Stündliche Prüfung; Kalender unbekannt"})
    available = sum(row["Status"] == "Verfügbar" for row in rows)
    retained = sum(row["Status"] == "Verfügbar" and row["Letzter Abruf"] == "Fehlgeschlagen; letzter geprüfter Wert" for row in rows)
    st.caption(f"Aktuell zulässig: {available}/40 CORE-Faktoren · davon {retained} nach fehlgeschlagenem Abruf aus dem geprüften Zwischenspeicher · {40 - available} gesperrt.")
    if retained:
        st.warning("Einzelne Quellen konnten zuletzt nicht bestätigt werden. Ihre gespeicherten Werte bleiben nur innerhalb der bestehenden Freigabefrist nutzbar; Details stehen in der Quellentabelle.")
    st.markdown("**Verfügbare Fundamentaldaten je Währung**")
    overview = []
    labels = {"Geldpolitik": "2J-Rendite (%)", "Inflation": "Inflation (% zum Vorjahr)",
              "Arbeitsmarkt": "Arbeitslosenquote (%)", "PMI": "PMI (Index)", "GDP": "Reales GDP (% zum Vorjahr)"}
    for currency in CURRENCIES:
        item = {"Währung": currency}
        for factor, label in labels.items():
            row = next(row for row in rows if row["Währung"] == currency and row["Faktor"] == factor)
            value = number(row["Wert"])
            item[label] = f"{value:.2f} · {row['Referenzperiode']}" if row["Status"] == "Verfügbar" and value is not None else "—"
            if item[label] != "—" and row["Veröffentlichungsstatus"] == "Amtlich vorläufig":
                item[label] += " (vorläufig)"
            if item[label] != "—" and factor == "Inflation" and currency in ("EUR", "CHF"):
                item[label] += " · HICP"
        overview.append(item)
    st.dataframe(overview, hide_index=True, use_container_width=True)
    st.caption("Wert · Referenzperiode. Einzelne geprüfte Daten bleiben unabhängig von der Paar-Freigabe sichtbar. — bedeutet fehlend, ungeprüft oder aktuell nicht freigegeben. Arbeitsmarkt-Messzeiträume und Quellen stehen unten; die britische Quote misst drei Monate, CHF und NZD ein Quartal. Keine Handelssignale aus dieser Tabelle ableiten.")
    with st.expander("Datenstatus und Quellen · alle 40 CORE-Faktoren", expanded=False):
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Eurostat-Daten: Quelle Eurostat, Abrufzeit siehe Tabelle. CORE-Scores sind eigene Berechnungen; Eurostat ist für diese Berechnungen nicht verantwortlich.")
        st.caption("Schweizer HICP/HVPI: Bundesamt für Statistik (BFS), Datensatztitel und Originaldatei siehe Quellentabelle; Nutzung mit Quellenangabe (OPEN-BY). Scores sind eigene Berechnungen.")
        st.caption("Quartals-Arbeitsmarkt: Stats NZ, Labour market statistics ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)), und Bundesamt für Statistik, Erwerbslosenquote gemäss ILO ([Nutzung mit Quellenangabe](https://opendata.swiss/terms-of-use#terms_by)). Originalquellen stehen in der Tabelle. Scores und Darstellungsänderungen sind eigene Berechnungen.")
    with st.expander("Sperrgründe je Währung", expanded=True):
        blocked = []
        for currency in CURRENCIES:
            reasons = [f"{row['Faktor']}: {row['Grund']}" for row in rows
                       if row['Währung'] == currency and row['Status'] == 'Gesperrt']
            if reasons:
                blocked.append({"Währung": currency, "Sperrgründe": "; ".join(reasons)})
        if blocked:
            st.dataframe(blocked, hide_index=True, use_container_width=True)
        else:
            st.caption("Alle 40 Faktoren sind aktuell freigegeben.")
    if authorized:
        with st.expander("Anbieter und Anfragebudget", expanded=False):
            try:
                status = json.loads((selected_live_directory() / "data_collection_status.json").read_text())
            except (OSError, ValueError):
                status = {}
            providers = status.get("providers", {})
            st.dataframe([{"Anbieter": host, "Status": item.get("status"),
                           "Datenprüfung": item.get("data_status", "Nicht separat gemeldet"),
                           "Anbieter-Wartezeit bis": item.get("retry_after_at") or "Keine bestätigt",
                           "Anfragen im letzten Lauf": item.get("requests_this_run"),
                           "Heute gezählt (UTC)": item.get("requests_observed_utc_day"),
                           "Restkontingent": "Unbekannt", "Limit": "Unbekannt", "Rücksetzung": "Unbekannt",
                           "Nachweis": "Lokal gezählte Abrufe; kein bestätigtes Anbieter-Restbudget"}
                          for host, item in providers.items()], hide_index=True, use_container_width=True)
            st.caption("Andere Anwendungen können denselben Schlüssel verwenden. Lokale Zähler sind daher kein Nachweis des gesamten Kontoverbrauchs.")
