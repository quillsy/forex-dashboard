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

import requests as http

MODEL = "CORE_V2_8_2026_09"
PATH = Path("live_core_data.json")
FACTORS = {"Geldpolitik": 35, "Inflation": 20, "Arbeitsmarkt": 20, "PMI": 20, "GDP": 5}
CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")
OBS_FIELDS = {"value", "policy_rate", "yield_2y", "date", "source", "series_id", "frequency",
              "unit", "seasonal_adjustment", "reference_period", "published_at", "checked_at",
              "next_due_at", "freshness", "m_last", "s_last", "m_ref", "s_ref", "m_src", "s_src"}
OBS_FIELDS.update({"comparison_period_status", "provider_status", "release_date_known", "reference_start", "reference_end", "period_label", "is_estimate", "source_url", "next_due_precision", "needs_hourly_check", "transformation", "publication_basis", "geography", "release_stage", "license", "redistribution_status", "source_title"})


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


def temporary_source_outage(error):
    """Only confirmed temporary transport failures permit bounded cache reuse."""
    if isinstance(error, http.exceptions.JSONDecodeError):
        return False
    if isinstance(error, http.exceptions.HTTPError):
        status = getattr(getattr(error, "response", None), "status_code", None)
        return type(status) is int and (status in (408, 425, 429) or 500 <= status < 600)
    if isinstance(error, http.exceptions.SSLError):
        return False
    if isinstance(error, (http.exceptions.Timeout, http.exceptions.ConnectionError,
                          http.exceptions.ChunkedEncodingError)):
        return True
    return (isinstance(error, http.exceptions.RequestException)
            and error.args == ("STATCAN_OFFICIAL_OUTAGE",))


def current_freshness(record, now, factor):
    """Age the displayed badge without treating time passing as a new check."""
    if not isinstance(record, dict) or record.get("freshness") not in ("FRESH", "AGING"):
        return "UNAVAILABLE"
    observation = record.get("observation")
    if not isinstance(observation, dict):
        return "UNAVAILABLE"
    try:
        reference = datetime.strptime(observation.get("date"), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return "UNAVAILABLE"
    quarterly = observation.get("frequency") == "quarterly"
    if factor in ("Inflation", "Arbeitsmarkt", "PMI") and not quarterly:
        import calendar
        reference = reference.replace(day=calendar.monthrange(reference.year, reference.month)[1])
    if factor == "Geldpolitik":
        fresh_days, max_days = 5, 15
    elif factor == "GDP" or (factor == "Arbeitsmarkt" and quarterly):
        fresh_days, max_days = 120, 180
    elif factor == "Inflation" and quarterly:
        fresh_days, max_days = 90, 180
    else:
        fresh_days, max_days = 45, 90
    age = (now.date() - reference).days
    if age < 0:
        return "UNAVAILABLE"
    if age > max_days:
        return "STALE"
    return "AGING" if age > fresh_days or record["freshness"] == "AGING" else "FRESH"


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
        result["_freshness"][factor] = current_freshness(record, now, factor) if valid else "UNAVAILABLE"
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
        if key == "publication_basis" and isinstance(value, str) and re.fullmatch(
                r"https://www\.bea\.gov/news/\d{4}/[a-z0-9-]*gdp[a-z0-9-]*", value):
            result[key] = value
            continue
        if key == "source_url":
            official_links = {
                "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ihyr/pn2",
                "https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ihyr/qna",
                "https://api.beta.ons.gov.uk/v1/data?uri=/economy/grossdomesticproductgdp/timeseries/ihyr/pn2",
                "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/namq_10_gdp",
                "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr",
                "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m",
                "https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms/data",
                "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
                "https://data.api.abs.gov.au/rest/data/CPI/3.10001.10.50.M",
                "https://api.statistiken.bundesbank.de/rest/data/BBSSY/D.REN.EUR.A610.000000WT0202.A",
                "https://www.bankofcanada.ca/valet/observations/BD.CDN.2YR.DQ.YLD/json",
                "https://apps.bea.gov/national/Release/XLS/Survey/Section1All_xls.xlsx",
                "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve",
                "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
                "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410028701",
                "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3610010401",
                "https://data.api.abs.gov.au/rest/data/LF/M13.3.1599.20.AUS.M",
                "https://data.api.abs.gov.au/rest/data/ANA_AGG/M1.GPM.20.AUS.Q",
                "https://www.e-stat.go.jp/en/stat-search/file-download?fileKind=0&statInfId=000031831358",
            }
            if isinstance(value, str) and re.fullmatch(r"https://www\.bea\.gov/news/\d{4}/[a-z0-9-]*gdp[a-z0-9-]*", value):
                result[key] = value
                continue
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
                r"https://(?:www\.stats\.govt\.nz/information-releases/(?:labour-market-statistics-[a-z]+|gross-domestic-product-(?:march|june|september|december)|consumers-price-index-(?:march|june|september|december))-\d{4}-quarter/?|opendata\.swiss/(?:en/)?dataset/erwerbslosenquote-gemass-ilo-[a-z0-9-]+/?)", value):
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
        prior_observation = previous.get("observation", {}) if isinstance(previous, dict) else {}
        prior_observation = prior_observation if isinstance(prior_observation, dict) else {}
        prior_score = number(previous.get("score")) if isinstance(previous, dict) else None
        newly_observed = ((valid_score is not None and
                           (valid_score != prior_score or observation.get("date") is None or
                            any(observation.get(key) != prior_observation.get(key)
                                for key in ("series_id", "source")))) or
                          (observation.get("date") is not None and
                           observation.get("date") != prior_observation.get("date")) or
                          any(number(observation.get(key)) is not None and
                              number(observation.get(key)) != number(prior_observation.get(key))
                              for key in ("value", "policy_rate", "yield_2y")))
        if (isinstance(previous, dict) and previous.get("validation") == "VALID"
                and number(previous.get("score")) is not None
                and validation == "SOURCE_UNAVAILABLE" and not newly_observed):
            record = {key: copy.deepcopy(previous.get(key)) for key in
                      ("factor", "score", "validation", "reason", "freshness", "checked_at", "published_at", "next_due_at", "expires_at")}
            record["observation"] = public_observation(previous.get("observation", {}))
            record["last_error"] = "SOURCE_UNAVAILABLE"
            record["last_attempt_at"] = checked_at
            return record
        # A missing score alone does not prove a transport outage. In
        # particular, a disappeared 2Y yield or a swallowed parser error must
        # revoke the old score instead of being relabelled SOURCE_UNAVAILABLE.
        return {"score": None, "validation": "UNVERIFIED" if validation == "VALID" or newly_observed else validation,
                "reason": "Neu beobachteter Wert oder Referenzperiode ungeprüft" if newly_observed and validation == "SOURCE_UNAVAILABLE"
                          else reason or "Daten fehlen oder Berechnung nicht bestätigt",
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


def record_not_due(record, currency, factor, now):
    """Reuse only qualified observations with a known future release deadline."""
    if not isinstance(record, dict):
        return False
    observation = record.get("observation")
    if not isinstance(observation, dict) or observation.get("needs_hourly_check") is True:
        return False
    due = timestamp(record.get("next_due_at"))
    return due is not None and due > now and eligible(record, now, factor=factor, currency=currency)[0]


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
            return False
        try:
            if series not in metadata:
                response = app.requests.get("https://api.stlouisfed.org/fred/series",
                    params={"series_id": series, "api_key": app.FRED_KEY, "file_type": "json"}, timeout=12)
                response.raise_for_status()
                metadata[series] = response.json()
        except (ValueError, TypeError):
            # Invalid JSON is a contract failure, not a confirmed transport outage.
            return False
        except http.exceptions.RequestException as error:
            return None if temporary_source_outage(error) else False
        except Exception:
            return False
        # A transport failure cannot prove a definition conflict.
        return validate_fred_metadata(metadata[series], series, category)

    for currency in CURRENCIES:
        prior_records = previous.get("currencies", {}).get(currency, {})
        prior_records = prior_records if isinstance(prior_records, dict) else {}
        retained = {factor: copy.deepcopy(prior_records[factor]) for factor in FACTORS
                    if record_not_due(prior_records.get(factor), currency, factor, now_utc())}
        requested = tuple(factor for factor in FACTORS if factor not in retained)
        raw = app.compute_currency_details(currency, include_context=False,
                                          factors_to_refresh=requested) if requested else {}
        data["currencies"][currency] = {}
        for factor in FACTORS:
            if factor in retained:
                # Keep every timestamp and status exactly as last verified.
                data["currencies"][currency][factor] = retained[factor]
                continue
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
            elif factor in ("Arbeitsmarkt", "GDP") and currency not in ("EUR", "GBP") and not (factor == "Arbeitsmarkt" and currency in ("CHF", "NZD", "JPY", "CAD")) and currency not in ("AUD", "JPY", "CAD") and not (factor == "GDP" and currency in ("CHF", "USD", "NZD")):
                contract = fred_contract(observation.get("series_id"), factor)
                if contract is not True:
                    validation = "SOURCE_UNAVAILABLE" if contract is None and validation == "VALID" and number(raw.get(factor)) is not None else "UNVERIFIED"
                    reason = "Metadatenquelle vorübergehend nicht erreichbar" if validation == "SOURCE_UNAVAILABLE" else "Amtliche Daten oder Serien-Metadaten nicht bestätigt"
            elif factor == "Inflation" and currency == "USD":
                contract = fred_contract("CPIAUCNS", factor)
                if contract is not True:
                    validation = "SOURCE_UNAVAILABLE" if contract is None and validation == "VALID" and number(raw.get(factor)) is not None else "UNVERIFIED"
                    reason = "CPI-Metadatenquelle vorübergehend nicht erreichbar" if validation == "SOURCE_UNAVAILABLE" else "CPI-Rohdaten oder Metadaten nicht bestätigt"
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
                        validation = "SOURCE_UNAVAILABLE" if contract is None and validation == "VALID" and number(raw.get(factor)) is not None else "UNVERIFIED"
                        reason = "Rendite-Metadatenquelle vorübergehend nicht erreichbar" if validation == "SOURCE_UNAVAILABLE" else "Rendite-Rohdaten oder Metadaten nicht bestätigt"
                if currency == "USD" and source.startswith("US Treasury"):
                    old = prior_records.get(factor)
                    old_obs = old.get("observation", {}) if isinstance(old, dict) else {}
                    official_urls = {
                        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve",
                        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
                    }
                    if (isinstance(old_obs, dict) and old_obs.get("source_url") in official_urls
                            and observation.get("source_url") in official_urls
                            and old_obs.get("source_url") != observation.get("source_url")
                            and old_obs.get("date") == observation.get("date")
                            and number(old_obs.get("yield_2y")) is not None
                            and number(observation.get("yield_2y")) is not None
                            and number(old_obs["yield_2y"]) != number(observation["yield_2y"])):
                        validation, reason = "UNVERIFIED", "Amtliche Treasury-Ausgaben widersprechen sich für denselben Tag"
                # Policy verification must also have succeeded during this run.
                policy = app.get_verified_policy_rate(currency)
                proofs = policy.get("verification_evidence", [])
                deadlines = [timestamp(p.get("valid_until")) for p in proofs
                             if isinstance(p, dict) and "valid_until" in p] if isinstance(proofs, list) else []
                if deadlines:
                    if any(d is None for d in deadlines):
                        validation, reason = "UNVERIFIED", "Ungültige Leitzins-Ablaufgrenze"
                    else:
                        existing_due = timestamp(observation.get("next_due_at"))
                        deadline = min(deadlines + ([existing_due] if existing_due else []))
                        observation["next_due_at"] = deadline.isoformat()
                        # The yield still needs its regular hourly verification.
                        observation["needs_hourly_check"] = True
                if not app.policy_rate_is_usable(policy):
                    validation, reason = "UNVERIFIED", "Leitzins-Belege ungültig oder angekündigter Zinswechsel fällig"
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
    now = now_utc()
    completed = checked is not None and checked <= now
    st.caption("LIVE-ANALYSE · G8 · Fundamentaler Horizont: 1–2 Wochen")
    if not completed:
        st.error("Noch kein geprüfter Live-Datensatz vorhanden. Paar-Signale sind gesperrt.")
    elif now - checked >= timedelta(hours=1):
        st.warning("Der letzte abgeschlossene Abruf liegt über eine Stunde zurück. Die Aktualität wird je Faktor geprüft; abgelaufene Freigaben sind gesperrt.")
    else:
        st.info("Zentraler Datenabruf: " + checked.strftime("%d.%m.%Y %H:%M UTC") + " · Ziel: neue Veröffentlichungen binnen einer Stunde berücksichtigen.")
    weights = "/".join(str(weight) for weight in FACTORS.values())
    st.caption(f"Die Gesamtzahl zählt geprüfte Faktoren; die CORE-Abdeckung je Währung summiert deren Modellgewichte ({weights}). Beides ist keine Trefferwahrscheinlichkeit. Kontext und historische Detailansichten können unvollständig sein; der Live-Datenstatus unten ist maßgeblich.")
    rows = []
    for currency in CURRENCIES:
        for factor in FACTORS:
            record = data.get("currencies", {}).get(currency, {}).get(factor, {})
            record = record if isinstance(record, dict) else {}
            valid, reason = eligible(record, now, factor=factor, currency=currency)
            if not completed and valid:
                valid, reason = False, "Kein abgeschlossener Live-Datensatz"
            observation = record.get("observation", {})
            rows.append({"Währung": currency, "Faktor": factor,
                         "Status": "Verfügbar" if valid else "Gesperrt", "Grund": reason,
                         "Aktualität": current_freshness(record, now, factor) if valid else "UNAVAILABLE",
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
                         "Nächste Fälligkeit": ((record.get("next_due_at") or "") + " (vorsorglich ab Tagesbeginn " + {"date_only_start_of_NZ_day": "Neuseeland", "date_only_start_of_JP_day": "Japan", "date_only_start_of_AU_day": "Australien", "date_only_start_of_CA_Eastern_day": "Kanada (Eastern Time)"}[observation["next_due_precision"]] + "; Veröffentlichungsuhrzeit unbekannt)") if observation.get("next_due_precision") in ("date_only_start_of_NZ_day", "date_only_start_of_JP_day", "date_only_start_of_AU_day", "date_only_start_of_CA_Eastern_day") else record.get("next_due_at") or "Stündliche Prüfung; Kalender unbekannt"})
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
        valid_rows = [row for row in rows if row["Währung"] == currency and row["Status"] == "Verfügbar"]
        item = {"Währung": currency, "Faktoren": f"{len(valid_rows)}/{len(FACTORS)}",
                "Gewichtete CORE-Abdeckung": f"{sum(FACTORS[row['Faktor']] for row in valid_rows)}%"}
        for factor, label in labels.items():
            row = next(row for row in rows if row["Währung"] == currency and row["Faktor"] == factor)
            value = number(row["Wert"])
            item[label] = f"{value:.2f} · {row['Referenzperiode']}" if row["Status"] == "Verfügbar" and value is not None else "—"
            if item[label] != "—" and row["Veröffentlichungsstatus"] == "Amtlich vorläufig":
                item[label] += " (vorläufig)"
            if item[label] != "—" and factor == "Inflation" and currency in ("EUR", "CHF"):
                item[label] += " · HICP · EA21 fix" if currency == "EUR" else " · HICP"
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
            reasons = []
            for row in rows:
                if row["Währung"] != currency or row["Status"] != "Gesperrt":
                    continue
                prefix = f"{row['Faktor']}:"
                reason = str(row["Grund"]).strip()
                reasons.append(reason if reason.startswith(prefix) else f"{prefix} {reason}")
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
            st.dataframe([{"Anbieter": host, "Letzter Abrufstatus": item.get("status"),
                           "Ergebnisse im letzten Lauf": json.dumps(item.get("outcomes_this_run", {}), sort_keys=True),
                           "Letzter Versuch": item.get("last_attempt_at") or "Unbekannt",
                           "Fehlerzeit im letzten Lauf": item.get("last_failure_at") or "Keiner dokumentiert",
                           "Datenprüfung": item.get("data_status", "Nicht separat gemeldet"),
                           "Anbieter-Wartezeit bis": item.get("retry_after_at") or "Keine bestätigt",
                           "Anfragen im letzten Lauf": item.get("requests_this_run"),
                           "Heute gezählt (UTC)": item.get("requests_observed_utc_day"),
                           "Restkontingent": "Unbekannt", "Limit": "Unbekannt", "Rücksetzung": "Unbekannt",
                           "Nachweis": "Lokal gezählte Abrufe; kein bestätigtes Anbieter-Restbudget"}
                          for host, item in providers.items()], hide_index=True, use_container_width=True)
            st.caption("Andere Anwendungen können denselben Schlüssel verwenden. Lokale Zähler sind daher kein Nachweis des gesamten Kontoverbrauchs.")
