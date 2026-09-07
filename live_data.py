"""Public, normalized live observations. No credentials or raw responses on disk."""
import copy
import json
import math
import os
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


def load(path=PATH):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) and data.get("model_version") == MODEL else {}
    except (OSError, ValueError):
        return {}


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


def eligible(record, now=None):
    now = now or now_utc()
    if not isinstance(record, dict) or number(record.get("score")) is None:
        return False, "Daten fehlen oder sind nicht validiert"
    if record.get("validation") != "VALID":
        return False, record.get("reason", "Quellenprüfung offen")
    observation = record.get("observation")
    factor = record.get("factor")
    if not isinstance(observation, dict) or factor not in FACTORS:
        return False, "Belegte Faktor-Beobachtung fehlt"
    values = ("policy_rate", "yield_2y") if factor == "Geldpolitik" else ("value",)
    if any(number(observation.get(key)) is None for key in values):
        return False, "Ungültiger Beobachtungswert"
    try:
        reference = datetime.strptime(observation.get("date"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False, "Referenzperiode fehlt"
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
    elif now - checked >= timedelta(hours=1):
        return False, "Aktualität seit über einer Stunde unbestätigt"
    return True, "Geprüft" if not record.get("last_error") else "Gespeicherte Daten gültig; Quelle gestört"


def details(currency, now=None, data=None):
    now = now or now_utc()
    data = load() if data is None else data
    records = data.get("currencies", {}).get(currency, {})
    result = {"BCI": None, "_observations": {}, "_freshness": {}, "_live_reasons": {}, "_live_checked": True}
    for factor in FACTORS:
        record = records.get(factor, {})
        valid, reason = eligible(record, now)
        result[factor] = number(record.get("score")) if valid else None
        result["_freshness"][factor] = record.get("freshness", "FRESH") if valid else "UNAVAILABLE"
        result["_observations"][factor] = copy.deepcopy(record.get("observation", {}))
        result["_live_reasons"][factor] = reason
    result["_missing"] = [factor for factor in FACTORS if result[factor] is None]
    result["_completeness"] = sum(weight for factor, weight in FACTORS.items() if result[factor] is not None)
    return result


def public_observation(observation):
    """Strict field projection; never persist provider exception bodies or URLs."""
    result = {}
    for key in OBS_FIELDS:
        value = observation.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            result[key] = number(value) if isinstance(value, (int, float)) else value
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
        if previous and validation == "VALID":
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
        if not series or not app.FRED_KEY:
            return False
        try:
            if series not in metadata:
                response = app.requests.get("https://api.stlouisfed.org/fred/series",
                    params={"series_id": series, "api_key": app.FRED_KEY, "file_type": "json"}, timeout=12)
                response.raise_for_status()
                metadata[series] = response.json()
            return validate_fred_metadata(metadata[series], series, category)
        except Exception:
            return False

    for currency in CURRENCIES:
        raw = app.compute_currency_details(currency)
        data["currencies"][currency] = {}
        for factor in FACTORS:
            observation = dict(raw.get("_observations", {}).get(factor, {}))
            if observation.get("date") is not None:
                observation["date"] = str(observation["date"])[:10]
            observation.setdefault("published_at", None)
            observation.setdefault("frequency", "daily" if factor == "Geldpolitik" else "quarterly" if factor == "GDP" or (factor == "Inflation" and currency == "NZD") else "monthly")
            validation, reason = "VALID", None
            if factor == "PMI":
                validation, reason = "UNVERIFIED", "PMI: Survey-Identität und öffentliche Nutzungsrechte noch nicht bestätigt"
            elif factor == "Inflation" and currency in ("EUR", "CHF", "AUD", "JPY"):
                validation, reason = "UNVERIFIED", "Inflation: Gebietsstand, Messgröße oder Einheitenprüfung noch offen"
            elif factor in ("Arbeitsmarkt", "GDP") and currency != "EUR":
                if not fred_contract(observation.get("series_id"), factor):
                    validation, reason = "UNVERIFIED", "Amtliche Serien-Metadaten fehlen oder passen nicht"
            elif factor == "Inflation" and currency == "USD":
                if not fred_contract("CPIAUCNS", factor):
                    validation, reason = "UNVERIFIED", "CPI-Metadaten nicht bestätigt"
            elif factor == "Geldpolitik":
                source = observation.get("source") or ""
                if "EODHD" in source:
                    validation, reason = "UNVERIFIED", "Aktualität der gespeicherten Rendite nicht erneut bestätigt"
                elif currency == "USD" and not fred_contract("DGS2", factor):
                    validation, reason = "UNVERIFIED", "Rendite-Metadaten nicht bestätigt"
                # Policy verification must also have succeeded during this run.
                policy = app.get_verified_policy_rate(currency)
                verified = policy.get("verification_timestamp") or policy.get("verified_at")
                if verified is None:
                    verified = policy.get("last_verified_at")
                verified_at = timestamp(verified)
                if verified_at is None or now_utc() - verified_at >= timedelta(hours=1):
                    validation, reason = "UNVERIFIED", "Aktuelle Leitzinsprüfung fehlt"
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
    counts = [eligible(record)[0] for records in data["currencies"].values() for record in records.values()]
    data["eligible_factors"] = sum(counts)
    data["status"] = "SUCCESS" if all(counts) else "PARTIAL" if any(counts) else "FAILED"
    data["completed_at"] = now_utc().isoformat()
    save(data, path)
    return {"status": data["status"], "eligible_factors": sum(counts), "total_factors": 40}


def render_status(st, authorized=False):
    data = load()
    checked = timestamp(data.get("completed_at"))
    st.caption("LIVE-ANALYSE · G8 · Fundamentaler Horizont: 1–2 Wochen")
    if checked is None:
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
            valid, reason = eligible(record)
            observation = record.get("observation", {})
            rows.append({"Währung": currency, "Faktor": factor,
                         "Status": "Verfügbar" if valid else "Gesperrt", "Grund": reason,
                         "Wert": observation.get("value", observation.get("yield_2y")),
                         "Referenzperiode": observation.get("reference_period") or observation.get("date"),
                         "Quelle": observation.get("source"), "Einheit": observation.get("unit"),
                         "Veröffentlicht": record.get("published_at") or "Unbekannt",
                         "Erfolgreich geprüft": record.get("checked_at") or "Nicht bestätigt",
                         "Nächste Fälligkeit": record.get("next_due_at") or "Stündliche Prüfung; Kalender unbekannt"})
    with st.expander("Datenstatus und Quellen · alle 40 CORE-Faktoren", expanded=False):
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("Eurostat-Daten: Quelle Eurostat, Abrufzeit siehe Tabelle. CORE-Scores sind eigene Berechnungen; Eurostat ist für diese Berechnungen nicht verantwortlich.")
    if authorized:
        with st.expander("Anbieter und Anfragebudget", expanded=False):
            try:
                status = json.loads(Path("data_collection_status.json").read_text())
            except (OSError, ValueError):
                status = {}
            providers = status.get("providers", {})
            st.dataframe([{"Anbieter": host, "Status": item.get("status"),
                           "Anfragen im letzten Lauf": item.get("requests_this_run"),
                           "Heute gezählt (UTC)": item.get("requests_observed_utc_day"),
                           "Restkontingent": "Unbekannt", "Limit": "Unbekannt", "Rücksetzung": "Unbekannt",
                           "Nachweis": "Lokal gezählte Abrufe; kein bestätigtes Anbieter-Restbudget"}
                          for host, item in providers.items()], hide_index=True, use_container_width=True)
            st.caption("Andere Anwendungen können denselben Schlüssel verwenden. Lokale Zähler sind daher kein Nachweis des gesamten Kontoverbrauchs.")
