import os
import io
import time
import itertools
import streamlit as st
import pandas as pd
import numpy as np
from provider_transport import transport as requests
import live_data
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

# ----------------- Load Environment Variables -----------------
if os.environ.get("FX_FALLBACK_MODE") != "1":
    load_dotenv()

CURRENT_MODEL_VERSION = "CORE_V2_8_2026_09"


def refresh_live_view_if_due(rendered_at, now=None):
    """Revalidate the whole displayed view; never fetch or alter CORE data here."""
    now = time.monotonic() if now is None else now
    if now - rendered_at >= 30:
        st.rerun()


# Set up page config
st.set_page_config(
    page_title="Institutional Forex Fundamental Dashboard",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- AUTOMATED VERIFIED G8 POLICY RATE ENGINE -----------------
import json

POLICY_RATES_CACHE_FILE = str(live_data.selected_live_directory() / ".policy_rates_cache.json")

def find_current_rate_episode_start(observations, current_rate, default_effective_date=None):
    """Walk only the current uninterrupted episode; require an observed boundary."""
    records = {}
    for obs in observations or []:
        if isinstance(obs, dict):
            date = obs.get("date", obs.get("d"))
            value = next((obs[k] for k in ("value", "v", "rate") if k in obs and obs[k] is not None), None)
        elif isinstance(obs, (list, tuple)) and len(obs) >= 2:
            date, value = obs[:2]
        else:
            continue
        date = _policy_date(date)
        try:
            value = _policy_rate(value)
        except (TypeError, ValueError):
            continue
        if date:
            if date in records and abs(records[date] - value) > 1e-8:
                return None
            records[date] = value
    ordered = sorted(records.items())
    if not ordered or abs(ordered[-1][1] - float(current_rate)) > 1e-8:
        return None
    start = ordered[-1][0]
    for date, value in reversed(ordered):
        if abs(value - float(current_rate)) > 1e-8:
            return start
        start = date
    # A truncated constant history cannot establish when this episode began.
    return default_effective_date

POLICY_RATE_DEFINITIONS = {
    "USD": {
        "instrument": "Federal Funds Target Range (Lower Bound)",
        "central_bank": "Federal Reserve"
    },
    "EUR": {
        "instrument": "ECB Deposit Facility Rate",
        "central_bank": "European Central Bank"
    },
    "GBP": {
        "instrument": "Bank of England Official Bank Rate",
        "central_bank": "Bank of England"
    },
    "CAD": {
        "instrument": "Target for the Overnight Rate",
        "central_bank": "Bank of Canada"
    },
    "CHF": {
        "instrument": "SNB Policy Rate",
        "central_bank": "Swiss National Bank"
    },
    "AUD": {
        "instrument": "Cash Rate Target",
        "central_bank": "Reserve Bank of Australia"
    },
    "NZD": {
        "instrument": "Official Cash Rate (OCR)",
        "central_bank": "Reserve Bank of New Zealand"
    },
    "JPY": {
        "instrument": "Short-Term Policy Interest Rate",
        "central_bank": "Bank of Japan"
    }
}

POLICY_VERIFICATION_MAX_AGE_DAYS = 7
POLICY_OFFICIAL_HOSTS = {
    "USD": {"api.stlouisfed.org", "fred.stlouisfed.org", "www.federalreserve.gov"},
    "EUR": {"data-api.ecb.europa.eu", "www.ecb.europa.eu"},
    "GBP": {"www.bankofengland.co.uk"},
    "JPY": {"www.boj.or.jp"},
    "CHF": {"data.snb.ch", "www.snb.ch"},
    "CAD": {"www.bankofcanada.ca"},
    "AUD": {"www.rba.gov.au"},
    "NZD": {"www.rbnz.govt.nz"},
}


def _policy_now():
    from datetime import timezone
    return datetime.now(timezone.utc)


def _policy_date(value):
    import re
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    if re.fullmatch(r"\d{4}(?:-\d{2})?", value):
        return None  # A month/year cannot establish an exact effective date.
    try:
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            return datetime.strptime(value, "%d.%m.%Y").strftime("%Y-%m-%d")
        parsed = pd.to_datetime(value.replace("Sept.", "Sep").replace("Jun.", "Jun"), utc=True)
        return parsed.strftime("%Y-%m-%d") if not pd.isna(parsed) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _policy_rate(value):
    import math
    if isinstance(value, bool):
        raise ValueError("INVALID_RATE")
    text = str(value).strip().replace("%", "").replace("−", "-")
    for fraction, decimal in {"¼": ".25", "½": ".5", "¾": ".75"}.items():
        text = text.replace(fraction, decimal)
    # Fed statements use mixed fractions, e.g. 3-1/2 to 3-3/4.
    import re
    mixed = re.fullmatch(r"(\d+)[\s‐‑–-]+(\d+)/(\d+)", text)
    number = (float(mixed[1]) + float(mixed[2]) / float(mixed[3])) if mixed else float(text)
    if not math.isfinite(number) or not -10 <= number <= 30:
        raise ValueError("INVALID_RATE")
    return number


def _policy_url_allowed(currency, url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in POLICY_OFFICIAL_HOSTS.get(currency, set()) and not parsed.username


def _policy_request(currency, url, params=None):
    if not _policy_url_allowed(currency, url):
        raise ValueError("UNAPPROVED_POLICY_SOURCE")
    headers = {"User-Agent": "ForexDashboard/2.6 official policy verification"} if currency == "GBP" else {}
    response = requests.get(url, params=params, timeout=(5, 15), headers=headers)
    response.raise_for_status()
    if not _policy_url_allowed(currency, response.url):
        raise ValueError("UNAPPROVED_POLICY_REDIRECT")
    return response


def _policy_html(currency, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_policy_request(currency, url).text, "html.parser")
    for node in soup(["script", "style", "nav", "header", "footer"]):
        node.decompose()
    return soup


def _policy_text(soup):
    return " ".join(soup.get_text(" ", strip=True).split())


def _policy_match_rate(text, patterns):
    import re
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _policy_rate(match[1])
    raise ValueError("POLICY_INSTRUMENT_NOT_FOUND")


def _policy_published_date(soup):
    import re
    for node in soup.select('time[datetime], meta[property="article:published_time"], meta[name="date"]'):
        value = _policy_date(node.get("datetime") or node.get("content"))
        if value:
            return value
    for node in soup.select(".date, .published-date, .release-date"):
        value = _policy_date(node.get_text(" ", strip=True))
        if value:
            return value
    match = re.search(r"Published (?:on )?(\d{1,2}\s+[A-Za-z]+\s+20\d{2})", _policy_text(soup), re.I)
    return _policy_date(match[1]) if match else None


def _policy_evidence(currency, url, rate, **metadata):
    if not _policy_url_allowed(currency, url):
        raise ValueError("UNAPPROVED_POLICY_SOURCE")
    return dict(currency=currency, instrument=POLICY_RATE_DEFINITIONS[currency]["instrument"],
                source_url=url, rate=_policy_rate(rate), retrieved_at=_policy_now().isoformat(), **metadata)


def _policy_history_evidence(currency, url, observations):
    records = []
    today = _policy_now().date().isoformat()
    for date, value in observations:
        date = _policy_date(date)
        try:
            value = _policy_rate(value)
        except (TypeError, ValueError):
            continue
        if date and date <= today:
            records.append((date, value))
    records.sort()
    if not records:
        raise ValueError("EMPTY_POLICY_HISTORY")
    rate = records[-1][1]
    effective = find_current_rate_episode_start(records, rate)
    previous = next((v for d, v in reversed(records) if abs(v - rate) > 1e-8), None)
    return _policy_evidence(currency, url, rate, rate_effective_date=effective,
                            previous_rate=previous, observation_date=records[-1][0])


def _policy_table_history(currency, url, soup, rate_column=1, ecb=False):
    records, linked_rows = [], []
    year = None
    for row in soup.find_all("tr"):
        cells = [x.get_text(" ", strip=True) for x in row.find_all(["td", "th"])]
        if not cells:
            continue
        date_text = cells[0]
        rate_index = rate_column
        if ecb:
            if len(cells) >= 6 and cells[0].isdigit() and len(cells[0]) == 4:
                year, date_text, rate_index = cells[0], cells[1], 2
            elif year and len(cells) >= 5:
                rate_index = 1
            else:
                continue
            date_text = date_text.replace(".", "") + " " + year
        elif not any(c.isalpha() for c in date_text) and not (len(date_text) >= 10 and "-" in date_text):
            continue
        date = _policy_date(date_text)
        if not date or date > _policy_now().date().isoformat() or len(cells) <= rate_index:
            continue
        try:
            value = _policy_rate(cells[rate_index])
        except (TypeError, ValueError):
            continue
        records.append((date, value))
        linked_rows.append((date, row))
    evidence = _policy_history_evidence(currency, url, records)
    return evidence, sorted(linked_rows, key=lambda item: item[0], reverse=True)


def _policy_latest_link(currency, base_url, soup, pattern):
    import re
    from urllib.parse import urljoin
    candidates = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        match = re.search(pattern, url)
        if match and _policy_url_allowed(currency, url):
            raw_date = match[1]
            if len(raw_date) == 6:
                raw_date = "20" + raw_date
            date = _policy_date(raw_date)
            if date and date <= _policy_now().date().isoformat():
                candidates.append((date, url))
    if not candidates:
        raise ValueError("OFFICIAL_DECISION_NOT_FOUND")
    return max(candidates)


def _policy_pdf_text(currency, url):
    from pypdf import PdfReader
    return " ".join(" ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(_policy_request(currency, url).content)).pages).split())



def _policy_ecb_pending(text, decision, table_soup, current, api_available):
    """Verify an announced rate against its exact future effective table row."""
    import re
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    match = re.search(r'deposit facility.{0,180}?(?:at|to|be)\s+(\d+(?:\.\d+)?)\s*%.{0,160}?with effect from (\d{1,2} [A-Za-z]+ 20\d{2})', text, re.I)
    if not match:
        raise ValueError('ECB_DECISION_TABLE_CONFLICT')
    rate, effective = _policy_rate(match[1]), _policy_date(match[2])
    now = _policy_now()
    if not effective or decision > effective:
        raise ValueError('ECB_EFFECTIVE_DATE_INVALID')
    cutoff = datetime.fromisoformat(effective).replace(tzinfo=ZoneInfo('Europe/Berlin')).astimezone(timezone.utc)
    if now >= cutoff or not api_available:
        raise ValueError('ECB_CURRENT_RATE_UNCONFIRMED')
    rows, year = [], None
    for row in table_soup.find_all('tr'):
        cells = [c.get_text(' ', strip=True) for c in row.find_all(['td','th'])]
        if len(cells) >= 6 and cells[0].isdigit() and len(cells[0]) == 4:
            year, date_text, value = cells[0], cells[1], cells[2]
        elif year and len(cells) >= 5:
            date_text, value = cells[0], cells[1]
        else:
            continue
        date = _policy_date(date_text.replace('.', '')+' '+year)
        if date == effective:
            rows.append(_policy_rate(value))
    if len(rows) != 1 or abs(rows[0]-rate) > 1e-8 or abs(current-rate) < 1e-8:
        raise ValueError('ECB_PENDING_TABLE_CONFLICT')
    return {'announced_rate': rate, 'announced_effective_date': effective,
            'valid_until': cutoff.isoformat(), 'deadline_basis': 'ECB date-only effective date; conservative start of Frankfurt day'}


def fetch_official_policy_rate_live(currency, fred_key=None):
    """Fetch two independent official documents. No inferred or default rates."""
    import re
    from urllib.parse import urljoin
    number = r"([+-]?\d+(?:\.\d+|[¼½¾]|[\s‐‑–-]+\d+/\d+)?)"
    evidence = []
    try:
        if currency == "USD":
            url = "https://api.stlouisfed.org/fred/series/observations"
            if fred_key:
                data = _policy_request(currency, url, {"series_id": "DFEDTARL", "api_key": fred_key, "file_type": "json", "observation_start": "2000-01-01"}).json()
                rows = [(x["date"], x["value"]) for x in data.get("observations", [])]
            else:
                csv_url = "https://fred.stlouisfed.org/graph/graph.csv?id=DFEDTARL"
                frame = pd.read_csv(io.StringIO(_policy_request(currency, csv_url).text))
                rows = list(zip(frame.iloc[:, 0], frame["DFEDTARL"]))
            evidence.append(_policy_history_evidence(currency, "https://fred.stlouisfed.org/series/DFEDTARL", rows))
            index = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
            decision, statement = _policy_latest_link(currency, index, _policy_html(currency, index), r"monetary(\d{8})a\.htm$")
            text = _policy_text(_policy_html(currency, statement))
            match = re.search(r"target range for the federal funds rate.{0,90}?" + number + r"\s+to\s+" + number + r"\s+(?:percent|per cent)", text, re.I)
            if not match:
                raise ValueError("FED_TARGET_RANGE_NOT_FOUND")
            lower, upper = _policy_rate(match[1]), _policy_rate(match[2])
            if not lower < upper <= lower + 1:
                raise ValueError("FED_TARGET_RANGE_INVALID")
            evidence.append(_policy_evidence(currency, statement, lower, upper_bound=upper, last_policy_decision_date=decision))

        elif currency == "EUR":
            url = "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.DFR.LEV"
            from requests.exceptions import RequestException, HTTPError
            try:
                api_response = _policy_request(currency, url, {"startPeriod": "1999-01-01", "format": "jsondata"})
            except HTTPError:
                raise
            except RequestException:
                # A transport outage may use the two existing official documents.
                # Parse outside this handler: malformed/conflicting API data must fail.
                api_response = None
            if api_response is not None:
                payload = api_response.json()
                series = next(iter(payload["dataSets"][0]["series"].values()))["observations"]
                dates = payload["structure"]["dimensions"]["observation"][0]["values"]
                evidence.append(_policy_history_evidence(currency, url, [(dates[int(k)]["id"], v[0]) for k, v in series.items()]))
            url2 = "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html"
            table_soup = _policy_html(currency, url2)
            secondary, _ = _policy_table_history(currency, url2, table_soup, ecb=True)
            # The decision index explicitly advertises its public year snippets.
            index = "https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html"
            index_soup = _policy_html(currency, index)
            snippets = index_soup.select_one("[data-snippets]")
            if snippets:
                year_soup = _policy_html(currency, urljoin(index, snippets["data-snippets"].split(",")[0]))
            else:
                year_soup = index_soup
            decision, statement = _policy_latest_link(currency, index, year_soup, r"ecb\.mp(\d{6})~[^/]+\.en\.html$")
            text = _policy_text(_policy_html(currency, statement))
            value = _policy_match_rate(text, [r"deposit facility.{0,180}?(?:at|to|be)\s+" + number + r"\s*%"])
            if abs(value - secondary["rate"]) > 1e-8:
                pending = _policy_ecb_pending(text, decision, table_soup, secondary["rate"], api_response is not None)
                secondary.update(pending)
            secondary["last_policy_decision_date"] = decision
            secondary["decision_source"] = statement
            if api_response is None:
                evidence.append(secondary)
                evidence.append(_policy_evidence(currency, statement, value, last_policy_decision_date=decision))
            else:
                evidence.append(secondary)

        elif currency == "GBP":
            primary_url = "https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp"
            primary, _ = _policy_table_history(currency, primary_url, _policy_html(currency, primary_url))
            evidence.append(primary)
            url = "https://www.bankofengland.co.uk/monetary-policy/the-interest-rate-bank-rate"
            soup = _policy_html(currency, url)
            rate = _policy_match_rate(_policy_text(soup), [r"Current Bank Rate\s*" + number + r"\s*%"])
            evidence.append(_policy_evidence(currency, url, rate, last_policy_decision_date=_policy_published_date(soup)))

        elif currency == "CAD":
            url = "https://www.bankofcanada.ca/valet/observations/V39079/json"
            data = _policy_request(currency, url, {"start_date": "2000-01-01"}).json()
            evidence.append(_policy_history_evidence(currency, url, [(x["d"], x.get("V39079", {}).get("v")) for x in data.get("observations", [])]))
            index = "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/"
            decision, url2 = _policy_latest_link(currency, index, _policy_html(currency, index), r"fad-press-release-(\d{4}-\d{2}-\d{2})/")
            text = _policy_text(_policy_html(currency, url2))
            rate = _policy_match_rate(text, [r"target for the overnight rate.{0,90}?(?:at|to)\s+" + number + r"\s*%"])
            evidence.append(_policy_evidence(currency, url2, rate, last_policy_decision_date=decision))

        elif currency == "CHF":
            # The monthly cube does not identify the exact effective day.
            url = "https://www.snb.ch/en/the-snb/mandates-goals/statistics/statistics-pub/current_interest_exchange_rates"
            text = _policy_text(_policy_html(currency, url))
            match = re.search(r"SNB policy rate\s+" + number + r"\s*%\s+valid from\s+(\d{2}\.\d{2}\.\d{4})", text, re.I)
            if not match:
                raise ValueError("SNB_DATED_POLICY_RATE_NOT_FOUND")
            evidence.append(_policy_evidence(currency, url, match[1], rate_effective_date=_policy_date(match[2])))
            index = "https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/decisions"
            decision, url2 = _policy_latest_link(currency, index, _policy_html(currency, index), r"/pre_(\d{8})(?:_\d+)?$")
            text = _policy_text(_policy_html(currency, url2))
            rate = _policy_match_rate(text, [r"SNB policy rate.{0,100}?(?:at|to)\s+" + number + r"\s*%"])
            evidence.append(_policy_evidence(currency, url2, rate, last_policy_decision_date=decision))

        elif currency in {"AUD", "NZD"}:
            url = "https://www.rba.gov.au/statistics/cash-rate/" if currency == "AUD" else "https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions"
            soup = _policy_html(currency, url)
            primary, rows = _policy_table_history(currency, url, soup, rate_column=2 if currency == "AUD" else 1)
            evidence.append(primary)
            latest_date, row = rows[0]
            link = next((a for a in row.find_all("a", href=True) if a.get_text(" ", strip=True).lower() in {"statement", "media release"}), None)
            if link is None:
                raise ValueError("OFFICIAL_DECISION_NOT_FOUND")
            url2 = urljoin(url, link["href"])
            decision_soup = _policy_html(currency, url2)
            text = _policy_text(decision_soup)
            pattern = r"cash rate target.{0,100}?(?:at|to)\s+" if currency == "AUD" else r"(?:official cash rate(?:\s*\(OCR\))?|OCR).{0,100}?(?:at|to)\s+"
            rate = _policy_match_rate(text, [pattern + number + r"\s*(?:%|per cent|percent)"])
            decision = _policy_published_date(decision_soup)
            if not decision and currency == "NZD":
                # RBNZ's primary table explicitly lists the OCR announcement date.
                decision = latest_date
            if not decision and currency == "AUD" and "following day" in _policy_text(soup):
                # RBA explicitly defines table dates as effective on the day after the decision.
                decision = (pd.Timestamp(latest_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            evidence.append(_policy_evidence(currency, url2, rate, last_policy_decision_date=decision))

        elif currency == "JPY":
            url = "https://www.boj.or.jp/en/mopo/measures/term_cond/yoryo36.htm"
            text = _policy_text(_policy_html(currency, url))
            rate = _policy_match_rate(text, [r"4\. Interest Rate\s+The interest rate shall be\s+" + number + r"\s+percent"])
            primary = _policy_evidence(currency, url, rate)
            evidence.append(primary)
            index = f"https://www.boj.or.jp/en/mopo/mpmdeci/mpr_{_policy_now().year}/index.htm"
            soup = _policy_html(currency, index)
            decision, statement = _policy_latest_link(currency, index, soup, r"/k(\d{6})a\.pdf$")
            text = _policy_pdf_text(currency, statement)
            call_rate_pattern = r"uncollateralized\s+o\s*vernight\s+call\s+rate.{0,70}?around\s+" + number + r"\s+percent"
            latest_rate = _policy_match_rate(text, [call_rate_pattern])
            secondary = _policy_evidence(currency, statement, latest_rate, last_policy_decision_date=decision)
            # Find the latest explicit rate-changing statement, not the latest hold.
            change_links = []
            for a in soup.find_all("a", href=True):
                if "Change in the Guideline for Money Market Operations" in a.get_text(" ", strip=True) and "Reference" not in a.get_text():
                    candidate = urljoin(index, a["href"])
                    match = re.search(r"/k(\d{6})a\.pdf$", candidate)
                    if match and _policy_date("20" + match[1]) <= decision:
                        change_links.append(candidate)
            if change_links:
                change_url = max(change_links)
                change_text = text if change_url == statement else _policy_pdf_text(currency, change_url)
                changed_rate = _policy_match_rate(change_text, [call_rate_pattern])
                effective_match = re.search(r"new guideline.{0,50}?effective from\s+([A-Za-z]+\s+\d{1,2},?\s+20\d{2})", change_text, re.I)
                if abs(changed_rate - rate) < 1e-8 and effective_match:
                    primary["rate_effective_date"] = _policy_date(effective_match[1])
                    primary["effective_date_source"] = change_url
            evidence.append(secondary)
        else:
            raise ValueError("UNSUPPORTED_CURRENCY")
        return {"evidence": evidence}
    except Exception as exc:
        # Never propagate URLs/request objects or secrets into logs/cache/UI.
        return {"evidence": evidence, "error": type(exc).__name__}


def _policy_proofs_valid(obj, check_age=True):
    try:
        currency, rate = obj["currency"], _policy_rate(obj["rate"])
        verified = pd.to_datetime(obj.get("verified_at"), utc=True)
        if verified is None or pd.isna(verified):
            return False
        verified_age = (_policy_now() - verified).total_seconds()
        if verified_age < -300 or (check_age and verified_age > POLICY_VERIFICATION_MAX_AGE_DAYS * 86400):
            return False
        proofs = obj.get("verification_evidence", [])
        if (not isinstance(proofs, list) or len(proofs) != 2
                or any(not isinstance(p, dict) for p in proofs)
                or len({p.get("source_url") for p in proofs}) != 2):
            return False
        for proof in proofs:
            if (proof.get("currency") != currency or proof.get("instrument") != POLICY_RATE_DEFINITIONS[currency]["instrument"]
                    or not _policy_url_allowed(currency, proof.get("source_url", "")) or abs(_policy_rate(proof["rate"]) - rate) > 1e-8):
                return False
            if "valid_until" in proof:
                deadline = pd.to_datetime(proof["valid_until"], utc=True)
                if pd.isna(deadline) or _policy_now() >= deadline:
                    return False
            checked = pd.to_datetime(proof["retrieved_at"], utc=True)
            if pd.isna(checked):
                return False
            age = (_policy_now() - checked).total_seconds()
            if age < -300 or (check_age and age > POLICY_VERIFICATION_MAX_AGE_DAYS * 86400):
                return False
        effective = _policy_date(obj.get("rate_effective_date"))
        decision = _policy_date(obj.get("last_policy_decision_date"))
        return bool(effective and decision and effective <= _policy_now().date().isoformat() and decision <= _policy_now().date().isoformat())
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def policy_rate_is_usable(obj):
    if not isinstance(obj, dict):
        return False
    status = obj.get("verification_status", "")
    if status == "🔴 MANUAL OVERRIDE":
        # Only the explicitly activated session override may bypass source verification.
        try:
            _policy_rate(obj.get("rate"))
        except (TypeError, ValueError):
            return False
        return operator_is_authorized() and bool(st.session_state.get("emergency_manual_rates_override", False))
    return status in {"🟢 VERIFIED", "🟢 VERIFIED_UNCHANGED", "🟡 LAST VERIFIED"} and _policy_proofs_valid(obj)


def load_policy_rates_cache():
    try:
        with open(POLICY_RATES_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
            return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def save_policy_rates_cache(cache_dict):
    import tempfile
    directory = os.path.dirname(os.path.abspath(POLICY_RATES_CACHE_FILE))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False, encoding="utf-8") as f:
            temporary = f.name
            json.dump(cache_dict, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, POLICY_RATES_CACHE_FILE)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _policy_empty(currency):
    definition = POLICY_RATE_DEFINITIONS[currency]
    return dict(currency=currency, rate=None, previous_rate=None, upper_bound=None,
                instrument=definition["instrument"], central_bank=definition["central_bank"],
                rate_effective_date=None, last_policy_decision_date=None, verified_at=None,
                primary_source=None, secondary_source=None, verification_evidence=[],
                verification_status="🔴 OFFICIAL SOURCE UNAVAILABLE")


def get_verified_policy_rate(currency):
    """Read-only canonical interface; legacy defaults cannot create verification."""
    if currency not in POLICY_RATE_DEFINITIONS:
        raise ValueError("UNSUPPORTED_CURRENCY")
    if operator_is_authorized() and st.session_state.get("emergency_manual_rates_override", False):
        value = st.session_state.get(f"manual_rate_{currency}")
        if value is not None:
            obj = _policy_empty(currency)
            obj.update(rate=_policy_rate(value), previous_rate=st.session_state.get(f"manual_rate_{currency}_prev"),
                       primary_source="MANUAL OVERRIDE", secondary_source="USER EMERGENCY INPUT",
                       verification_status="🔴 MANUAL OVERRIDE")
            return obj
    cached = load_policy_rates_cache().get(currency)
    obj = _policy_empty(currency)
    if isinstance(cached, dict):
        obj.update(cached)
    obj["currency"] = currency
    if not _policy_proofs_valid(obj, check_age=False):
        # Legacy labels/defaults are not evidence of a previous verification.
        obj = _policy_empty(currency)
    for field in ("rate", "previous_rate", "upper_bound"):
        try:
            obj[field] = _policy_rate(obj.get(field))
        except (TypeError, ValueError):
            obj[field] = None
    if not policy_rate_is_usable(obj) and obj.get("verification_status") != "🔴 UNVERIFIED CHANGE":
        obj["verification_status"] = "🔴 OFFICIAL SOURCE UNAVAILABLE"
    return obj


def get_all_verified_policy_rates():
    return {currency: get_verified_policy_rate(currency) for currency in POLICY_RATE_DEFINITIONS}


def refresh_all_verified_policy_rates(fred_key=None):
    """Only two agreeing, dated official proofs may activate a candidate rate."""
    import fcntl
    lock_path = POLICY_RATES_CACHE_FILE + ".lock"
    with open(lock_path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = load_policy_rates_cache()
        for currency in POLICY_RATE_DEFINITIONS:
            existing = cache.get(currency)
            old = dict(existing) if isinstance(existing, dict) else _policy_empty(currency)
            if not _policy_proofs_valid(old, check_age=False):
                old = _policy_empty(currency)
            now = _policy_now().isoformat()
            # Repeat UI/collector runs within one hour reuse actual verified evidence.
            try:
                recent = (_policy_now() - pd.to_datetime(old.get("verified_at"), utc=True)).total_seconds() < 3600
            except (TypeError, ValueError):
                recent = False
            if recent and policy_rate_is_usable(old):
                continue
            result = fetch_official_policy_rate_live(currency, fred_key)
            proofs = result.get("evidence", []) if isinstance(result, dict) else []
            candidate = _policy_empty(currency)
            if len(proofs) == 2 and not result.get("error"):
                first, second = proofs
                candidate.update(rate=first.get("rate"), previous_rate=first.get("previous_rate"),
                    upper_bound=second.get("upper_bound"), rate_effective_date=first.get("rate_effective_date"),
                    last_policy_decision_date=second.get("last_policy_decision_date"), verified_at=now,
                    primary_source=first.get("source_url"), secondary_source=second.get("source_url"),
                    verification_evidence=proofs, verification_status="🟢 VERIFIED")
                if _policy_proofs_valid(old, check_age=False) and old.get("rate") == candidate.get("rate"):
                    # A hold preserves the validated episode and preceding different rate.
                    candidate["rate_effective_date"] = old["rate_effective_date"]
                    candidate["previous_rate"] = old.get("previous_rate")
                if _policy_proofs_valid(candidate):
                    unchanged = old.get("rate") == candidate["rate"] and _policy_proofs_valid(old, check_age=False)
                    candidate["verification_status"] = "🟢 VERIFIED_UNCHANGED" if unchanged else "🟢 VERIFIED"
                    if not unchanged and _policy_proofs_valid(old, check_age=False):
                        candidate["previous_rate"] = old["rate"]
                    candidate["last_attempt_at"] = now
                    cache[currency] = candidate
                    continue
            changed = any(p.get("rate") != old.get("rate") for p in proofs)
            conflicting = len(proofs) == 2 and proofs[0].get("rate") != proofs[1].get("rate")
            old["last_attempt_at"] = now
            old["last_attempt_error"] = (result or {}).get("error", "INCOMPLETE_OFFICIAL_VERIFICATION")
            if changed or conflicting:
                old["verification_status"] = "🔴 UNVERIFIED CHANGE"
                old["candidate_rate"] = proofs[0].get("rate") if proofs else None
            else:
                old["verification_status"] = "🟡 LAST VERIFIED" if _policy_proofs_valid(old) else "🔴 OFFICIAL SOURCE UNAVAILABLE"
            # Preserve verified_at, original evidence and last accepted rate on failure.
            cache[currency] = old
        save_policy_rates_cache(cache)
        return cache


# ----------------- Obsidian Dark Theme CSS -----------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Roboto+Mono:wght@400;700&display=swap');
    
    /* General overrides */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif !important;
    }
    
    .stApp {
        background-color: #070708 !important;
        color: #b2b2be !important;
    }
    
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Inter', sans-serif !important;
        color: #f0f0f5 !important;
        font-weight: 600 !important;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #0c0c0e !important;
        border-right: 1px solid #1f2026 !important;
    }
    
    /* Card design */
    .metric-card-custom {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 12px;
    }
    
    .metric-label {
        font-size: 0.72rem;
        text-transform: uppercase;
        color: #7d7d8a;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    
    .metric-value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #f0f0f5;
        margin: 4px 0;
        font-family: 'Roboto Mono', monospace;
    }
    
    .source-tag {
        font-family: 'Roboto Mono', monospace;
        font-size: 0.62rem;
        color: #8c8c9a;
        background-color: rgba(255, 255, 255, 0.02);
        border: 1px solid #1f2026;
        padding: 1px 5px;
        border-radius: 3px;
        display: inline-block;
        margin-top: 4px;
    }
    
    .source-tag-live {
        color: #10b981;
        background-color: rgba(16, 185, 129, 0.04);
        border: 1px solid rgba(16, 185, 129, 0.15);
    }
    
    /* News Ticker Card Style (Bottom) */
    .news-card-custom {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 14px;
        margin-bottom: 12px;
        transition: border-color 0.2s, background-color 0.2s;
    }
    .news-card-custom:hover {
        border-color: #e2b13c;
        background-color: #111114;
    }
    .news-title-custom {
        font-size: 0.9rem;
        font-weight: 600;
        color: #f0f0f5 !important;
        text-decoration: none;
        display: block;
        margin-bottom: 4px;
    }
    .news-title-custom:hover {
        color: #e2b13c !important;
        text-decoration: underline;
    }
    .news-meta-custom {
        font-size: 0.7rem;
        color: #7d7d8a;
        margin-bottom: 6px;
    }

    /* News & Research Hub - Full Card style (Grid) */
    .news-card {
        background-color: #0c0c0e;
        border: 1px solid #1f2026;
        border-radius: 6px;
        padding: 16px;
        margin-bottom: 15px;
        transition: border-color 0.2s, background-color 0.2s;
        height: 420px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }
    .news-card:hover {
        border-color: #e2b13c;
        background-color: #111114;
    }
    .news-title {
        font-size: 0.95rem;
        font-weight: 600;
        color: #f0f0f5 !important;
        margin-bottom: 6px;
        text-decoration: none;
        display: block;
        line-height: 1.35;
    }
    .news-title:hover {
        color: #e2b13c !important;
        text-decoration: underline;
    }
    .news-meta {
        font-size: 0.72rem;
        color: #7d7d8a;
        margin-bottom: 8px;
    }
    .news-desc {
        font-size: 0.82rem;
        color: #b2b2be;
        margin-bottom: 8px;
        line-height: 1.4;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- Load API Keys from Env & Secrets -----------------
def load_api_key(name, alt_names=None):
    """
    Central, secure resolver for API keys across Streamlit Cloud (st.secrets),
    local development (.env / os.environ), and GitHub Actions CI (env variables).
    Never prints, logs, or leaks secrets into exception messages or tracebacks.
    """
    candidates = [name]
    if alt_names:
        candidates.extend(alt_names)
    if os.environ.get("FX_FALLBACK_MODE") == "1":
        from run_data_collection import LIVE_FALLBACK_KEYS
        return next((os.environ[key].strip() for key in candidates
                     if key in LIVE_FALLBACK_KEYS and os.environ.get(key, "").strip()), None)
        
    for key_name in candidates:
        val = os.getenv(key_name)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
        try:
            if hasattr(st, "secrets"):
                val = st.secrets.get(key_name) or st.secrets.get(key_name.lower())
                if val and isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:
            pass
    return None

def operator_is_authorized():
    import hmac
    expected = load_api_key("DASHBOARD_OPERATOR_PASSWORD")
    supplied = st.session_state.get("operator_password", "")
    return bool(expected and len(expected) >= 16 and isinstance(supplied, str)
                and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")))


def get_estat_app_id():
    """
    Central resolver for Japan e-Stat Application ID (ESTAT_APP_ID).
    Returns the sanitized secret string if configured, otherwise None.
    """
    return load_api_key("ESTAT_APP_ID")

def get_stats_nz_api_key():
    """
    Central resolver for Statistics New Zealand API Key (STATS_NZ_API_KEY).
    Priority: 1. STATS_NZ_API_KEY, 2. STATSNZ_API_KEY, 3. OCP_APIM_SUBSCRIPTION_KEY
    """
    return load_api_key("STATS_NZ_API_KEY", alt_names=["STATSNZ_API_KEY", "OCP_APIM_SUBSCRIPTION_KEY", "OCP_APIM_KEY"])

FRED_KEY = load_api_key("FRED_API_KEY")
AV_KEY = load_api_key("ALPHA_VANTAGE_API_KEY", alt_names=["AV_API_KEY"])
NEWSDATA_KEY = load_api_key("NEWSDATA_API_KEY")
NEWSAPI_KEY = load_api_key("NEWSAPI_KEY")
BENZINGA_KEY = load_api_key("BENZINGA_API_KEY")
FINNHUB_KEY = load_api_key("FINNHUB_API_KEY")
ITICK_KEY = load_api_key("ITICK_API_KEY")
FCS_KEY = load_api_key("FCS_API_KEY")
STOCKDATA_KEY = load_api_key("STOCKDATA_API_KEY", alt_names=["STOCKDATA_KEY", "STOCKDATA_TOKEN", "STOCK_DATA_API_KEY", "STOCKDATA_API_TOKEN", "STOCK_DATA_KEY"])
TIINGO_KEY = load_api_key("TIINGO_API_KEY")
BLS_KEY = load_api_key("BLS_API_KEY")
APIFREAKS_KEY = load_api_key("APIFREAKS_API_KEY")
EODHD_KEY = load_api_key("EODHD_API_KEY")
ESTAT_APP_ID = get_estat_app_id()
STATS_NZ_KEY = get_stats_nz_api_key()
OCP_APIM_KEY = STATS_NZ_KEY


# ----------------- Constants & Configuration -----------------
CURRENCIES = {
    "USD": {"name": "US Dollar", "flag": "🇺🇸", "country": "United States", "wb_code": "USA"},
    "EUR": {"name": "Euro", "flag": "🇪🇺", "country": "Euro area", "wb_code": "EMU"},
    "GBP": {"name": "British Pound", "flag": "🇬🇧", "country": "United Kingdom", "wb_code": "GBR"},
    "CHF": {"name": "Swiss Franc", "flag": "🇨🇭", "country": "Switzerland", "wb_code": "CHE"},
    "CAD": {"name": "Canadian Dollar", "flag": "🇨🇦", "country": "Canada", "wb_code": "CAN"},
    "AUD": {"name": "Australian Dollar", "flag": "🇦🇺", "country": "Australia", "wb_code": "AUS"},
    "NZD": {"name": "New Zealand Dollar", "flag": "🇳🇿", "country": "New Zealand", "wb_code": "NZL"},
    "JPY": {"name": "Japanese Yen", "flag": "🇯🇵", "country": "Japan", "wb_code": "JPN"}
}

# ----------------- 0. MOCK DATA GENERATORS (Graceful Fallback) -----------------
def generate_mock_fred(series_id):
    np.random.seed(42)
    dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="ME")
    if series_id == "FEDFUNDS":
        values = np.clip(np.linspace(0.25, 5.25, len(dates)) + np.random.normal(0, 0.15, len(dates)), 0.05, 7.0)
    elif series_id == "CPIAUCSL":
        values = np.linspace(235.0, 312.0, len(dates)) + np.random.normal(0, 0.4, len(dates))
    elif series_id == "GDPC1":
        dates = pd.date_range(start="2015-01-01", end=datetime.now(), freq="QE")
        values = np.linspace(17500.0, 22500.0, len(dates)) + np.random.normal(0, 80.0, len(dates))
    elif series_id == "UNRATE":
        values = np.clip(np.linspace(5.5, 3.8, len(dates)) + np.random.normal(0, 0.15, len(dates)), 3.0, 15.0)
    elif series_id == "NAPM":
        values = np.clip(50.0 + np.random.normal(0, 3.0, len(dates)), 35.0, 65.0)
    elif series_id == "BOEBASE":
        values = np.clip(np.linspace(0.5, 5.25, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.1, 6.0)
    elif series_id == "JPNIR":
        values = np.clip(np.linspace(-0.1, 0.25, len(dates)) + np.random.normal(0, 0.05, len(dates)), -0.15, 0.5)
    elif series_id == "CANIR":
        values = np.clip(np.linspace(0.75, 5.0, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.25, 6.0)
    elif series_id == "AUDIR":
        values = np.clip(np.linspace(1.5, 4.35, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.1, 5.5)
    elif series_id == "NZLIR":
        values = np.clip(np.linspace(1.5, 5.5, len(dates)) + np.random.normal(0, 0.2, len(dates)), 0.25, 6.5)
    else:
        values = np.zeros(len(dates))
    return pd.DataFrame({"date": dates, "value": values})

def generate_mock_av(from_symbol, to_symbol):
    np.random.seed(33)
    dates = pd.date_range(end=datetime.now(), periods=250, freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120}
    base = base_prices.get(pair, 1.0)
    prices = [base]
    for _ in range(249):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.0035)))
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.0025 for p in prices],
        "low": [p * 0.9975 for p in prices],
        "close": prices
    })

def generate_mock_news():
    return [
        {"title": "FED signalisiert Zinswende: Dollar gewinnt an Stärke gegenüber dem Euro", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die US-Notenbank deutet eine längere Phase hoher Leitzinsen an.", "urlToImage": None, "api_source": "MOCK-News"},
        {"title": "EZB hält Leitzins unverändert: EUR/USD gerät unter Druck", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die EZB bestätigt den Leitzins. Analysten erwarten schwächere Euro-Notierungen.", "urlToImage": None, "api_source": "MOCK-News"},
        {"title": "Bank of Japan erhöht Leitzins minimal: JPY reagiert volatil", "source": "MockNews", "publishedAt": datetime.now().strftime("%Y-%m-%d %H:%M"), "url": "#", "description": "Die japanische Notenbank hebt den Zinssatz leicht an, um dem schwachen Yen entgegenzuwirken.", "urlToImage": None, "api_source": "MOCK-News"}
    ]

def generate_mock_benzinga():
    events = [
        {"time": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "FOMC Meeting Minutes", "consensus": "5.25%", "actual": None, "prior": "5.25%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M"), "country": "DEU", "event": "German GDP Growth QoQ", "consensus": "0.1%", "actual": None, "prior": "-0.2%", "importance": "Medium"},
        {"time": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M"), "country": "GBR", "event": "BoE Interest Rate Decision", "consensus": "5.00%", "actual": None, "prior": "5.25%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d %H:%M"), "country": "USA", "event": "Non-Farm Payrolls (NFP)", "consensus": "180K", "actual": None, "prior": "210K", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M"), "country": "EUR", "event": "Eurozone CPI Inflation YoY", "consensus": "2.4%", "actual": None, "prior": "2.6%", "importance": "High"},
        {"time": (datetime.now() + timedelta(days=12)).strftime("%Y-%m-%d %H:%M"), "country": "JPN", "event": "BoJ Press Conference", "consensus": "-", "actual": None, "prior": "-", "importance": "Medium"}
    ]
    return pd.DataFrame(events)

def generate_mock_finnhub(pair):
    # Deterministic based on pair name
    import random
    random.seed(hash(pair) % 20000)
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
    base = base_prices.get(pair, 1.0)
    
    buy = random.randint(10, 20)
    hold = random.randint(5, 12)
    sell = random.randint(1, 5)
    strong_buy = random.randint(2, 8)
    strong_sell = random.randint(0, 2)
    
    target_mean = base * random.uniform(0.98, 1.02)
    target_high = target_mean * random.uniform(1.02, 1.05)
    target_low = target_mean * random.uniform(0.95, 0.98)
    
    # History list of dicts
    history = [
        {"date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"), "firm": "Goldman Sachs", "rating": "Buy", "target": round(target_mean * 1.01, 4)},
        {"date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"), "firm": "JPMorgan Chase", "rating": "Hold", "target": round(target_mean * 0.99, 4)},
        {"date": (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d"), "firm": "Morgan Stanley", "rating": "Buy", "target": round(target_mean * 1.02, 4)},
        {"date": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"), "firm": "Barclays", "rating": "Sell", "target": round(target_mean * 0.96, 4)}
    ]
    
    return {
        "buy": buy + strong_buy,
        "hold": hold,
        "sell": sell + strong_sell,
        "strongBuy": strong_buy,
        "buy_only": buy,
        "strongSell": strong_sell,
        "sell_only": sell,
        "target_high": round(target_high, 4),
        "target_low": round(target_low, 4),
        "target_mean": round(target_mean, 4),
        "history": history
    }

def generate_mock_itick(pair):
    import random
    from datetime import datetime
    random.seed(hash(pair) % 10000)
    base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
    base = base_prices.get(pair, 1.0)
    change = random.normalvariate(0, 0.005)
    close = base * (1 + change)
    op = base * (1 + change * 0.5)
    hi = max(op, close) * 1.002
    lo = min(op, close) * 0.998
    vol = random.uniform(50000, 150000)
    return {
        "open": op,
        "high": hi,
        "low": lo,
        "close": close,
        "volume": vol,
        "timestamp": int(datetime.now().timestamp() * 1000)
    }

def generate_mock_fcs_history(from_symbol, to_symbol):
    np.random.seed(95)
    dates = pd.date_range(start="1995-01-01", end=datetime.now(), freq="D")
    pair = f"{from_symbol}/{to_symbol}"
    base_prices = {"EUR/USD": 1.15, "GBP/USD": 1.55, "USD/JPY": 105.0, "USD/CHF": 1.12, "AUD/USD": 0.72, "USD/CAD": 1.25, "NZD/USD": 0.65, "EUR/GBP": 0.85}
    base = base_prices.get(pair, 1.0)
    prices = [base]
    for _ in range(len(dates)-1):
        prices.append(prices[-1] * (1 + np.random.normal(0, 0.005)))
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.004 for p in prices],
        "low": [p * 0.996 for p in prices],
        "close": prices
    })

def generate_mock_fcs_correlation():
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP"]
    matrix = [
        [1.0, 0.78, -0.45, -0.68, 0.58, -0.52, 0.61, 0.15],
        [0.78, 1.0, -0.38, -0.59, 0.52, -0.48, 0.55, -0.45],
        [-0.45, -0.38, 1.0, 0.72, -0.31, 0.35, -0.28, -0.12],
        [-0.68, -0.59, 0.72, 1.0, -0.49, 0.44, -0.42, -0.18],
        [0.58, 0.52, -0.31, -0.49, 1.0, -0.65, 0.85, 0.05],
        [-0.52, -0.48, 0.35, 0.44, -0.65, 1.0, -0.59, -0.08],
        [0.61, 0.55, -0.28, -0.42, 0.85, -0.59, 1.0, 0.02],
        [0.15, -0.45, -0.12, -0.18, 0.05, -0.08, 0.02, 1.0]
    ]
    return pd.DataFrame(matrix, index=pairs, columns=pairs)

def generate_mock_stockdata():
    return np.clip(np.random.normal(1.5, 3.5), -10.0, 10.0)


def generate_mock_worldbank(wb_code, indicator):
    np.random.seed(99)
    years = list(range(2015, 2026))
    if indicator == "NY.GDP.MKTP.KD.ZG":
        # GDP YoY
        values = np.clip(np.random.normal(2.0, 1.2, len(years)), -5.0, 10.0)
    else:
        # CPI YoY
        values = np.clip(np.random.normal(2.5, 1.5, len(years)), -1.0, 15.0)
    return pd.DataFrame({"date": pd.to_datetime([f"{y}-12-31" for y in years]), "value": values})


# ----------------- 1. LIVE DATA FETCHING FUNCTIONS -----------------
def fetch_fred_live(series_id, key, units=None):
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_start=2015-01-01"
    if units:
        url += f"&units={units}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    parsed = []
    for o in obs:
        if o["value"] != ".":
            parsed.append({"date": o["date"], "value": float(o["value"])})
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

def fetch_av_live(from_symbol, to_symbol, key):
    url = f"https://www.alphavantage.co/query?function=FX_DAILY&from_symbol={from_symbol}&to_symbol={to_symbol}&outputsize=full&apikey={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    data = r.json()
    if "Time Series FX (Daily)" not in data:
        raise ValueError("Invalid AV API response structure")
    ts = data["Time Series FX (Daily)"]
    parsed = []
    for k, v in ts.items():
        parsed.append({
            "date": k,
            "open": float(v["1. open"]),
            "high": float(v["2. high"]),
            "low": float(v["3. low"]),
            "close": float(v["4. close"])
        })
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)

def fetch_benzinga_live(key):
    url = f"https://api.benzinga.com/api/v2.1/calendar/economics?token={key}"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
    r.raise_for_status()
    res = r.json()
    calendar = res.get("economics", [])
    parsed = []
    for item in calendar:
        dt = item.get("date") or ""
        tm = item.get("time") or ""
        combined_time = f"{dt} {tm}".strip()
        
        act_val = item.get("actual")
        if act_val is not None and str(act_val).strip() != "":
            act_unit = item.get("actual_t") or ""
            actual_str = f"{act_val}{act_unit}"
        else:
            actual_str = None

        cons_val = item.get("consensus")
        if cons_val is not None and str(cons_val).strip() != "":
            cons_unit = item.get("consensus_t") or ""
            consensus_str = f"{cons_val}{cons_unit}"
        else:
            consensus_str = "-"

        prior_val = item.get("prior")
        if prior_val is not None and str(prior_val).strip() != "":
            prior_unit = item.get("prior_t") or ""
            prior_str = f"{prior_val}{prior_unit}"
        else:
            prior_str = "-"

        imp_raw = item.get("importance")
        if imp_raw == 3 or imp_raw == "3" or imp_raw == "High":
            imp = "High"
        elif imp_raw == 2 or imp_raw == "2" or imp_raw == "Medium":
            imp = "Medium"
        else:
            imp = "Low"

        parsed.append({
            "time": combined_time,
            "country": item.get("country") or "",
            "event": item.get("event_name") or "",
            "consensus": consensus_str,
            "actual": actual_str,
            "prior": prior_str,
            "importance": imp
        })
    df = pd.DataFrame(parsed)
    if not df.empty:
        df["dt_temp"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.sort_values("dt_temp", ascending=True).drop(columns=["dt_temp"])
    return df

def fetch_finnhub_live(pair, key):
    symbol = f"OANDA:{pair.replace('/', '_')}"
    url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if not isinstance(res, list) or len(res) == 0:
        raise ValueError(f"No Finnhub recommendations for symbol {symbol}")
    
    latest = res[0]
    buy = int(latest.get("buy") or 0)
    hold = int(latest.get("hold") or 0)
    sell = int(latest.get("sell") or 0)
    strong_buy = int(latest.get("strongBuy") or 0)
    strong_sell = int(latest.get("strongSell") or 0)
    
    target_mean = 1.0
    target_high = 1.0
    target_low = 1.0
    try:
        url_target = f"https://finnhub.io/api/v1/stock/price-target?symbol={symbol}&token={key}"
        rt = requests.get(url_target, timeout=5)
        if rt.status_code == 200:
            target_data = rt.json()
            target_mean = float(target_data.get("targetMean") or 1.0)
            target_high = float(target_data.get("targetHigh") or 1.0)
            target_low = float(target_data.get("targetLow") or 1.0)
    except Exception:
        pass
        
    history = []
    for item in res[:5]:
        history.append({
            "date": item.get("period") or "",
            "firm": "Finnhub Consensus",
            "rating": f"Buy: {item.get('buy')}, Hold: {item.get('hold')}, Sell: {item.get('sell')}",
            "target": target_mean
        })
        
    return {
        "buy": buy + strong_buy,
        "hold": hold,
        "sell": sell + strong_sell,
        "strongBuy": strong_buy,
        "buy_only": buy,
        "strongSell": strong_sell,
        "sell_only": sell,
        "target_high": target_high,
        "target_low": target_low,
        "target_mean": target_mean,
        "history": history
    }

def fetch_itick_live(pair, key):
    symbol = pair.replace("/", "")
    url = f"https://api.itick.org/forex/quote?region=GB&code={symbol}"
    r = requests.get(url, headers={"Accept": "application/json", "token": key}, timeout=8)
    r.raise_for_status()
    res = r.json()
    if res.get("code") != 0 or "data" not in res:
        raise ValueError(res.get("msg") or "Invalid response format from iTick")
    data = res["data"]
    return {
        "open": float(data["o"]),
        "high": float(data["h"]),
        "low": float(data["l"]),
        "close": float(data["ld"]),
        "volume": float(data.get("v") or 0.0),
        "timestamp": data.get("t")
    }

def fetch_fcs_history_live(pair, key):
    """Read FCS v4 daily candles; reject another pair, interval or invalid OHLC."""
    symbol = pair.replace("/", "").upper()
    if len(symbol) != 6 or not symbol.isalpha() or not key:
        raise ValueError("FCS_INVALID_REQUEST")
    response = requests.get("https://api-v4.fcsapi.com/forex/history",
                            params={"symbol": symbol, "period": "1D", "access_key": key}, timeout=8)
    if response.status_code != 200:
        raise ValueError(f"FCS_HTTP_{response.status_code}")
    payload = response.json()
    if payload.get("status") is not True:
        raise ValueError("FCS_HISTORY_UNAVAILABLE")
    info = payload.get("info", {})
    identifiers = [info.get("symbol"), info.get("profile", {}).get("symbol"), info.get("ticker")]
    identifiers = [str(value).rsplit(":", 1)[-1].replace("/", "").upper() for value in identifiers if value]
    if not identifiers or any(value != symbol for value in identifiers) or str(info.get("period", "")).upper() != "1D":
        raise ValueError("FCS_SERIES_MISMATCH")
    candles = payload.get("response", {})
    if isinstance(candles, dict):
        candles = list(candles.values())
    if not isinstance(candles, list):
        raise ValueError("FCS_INVALID_CANDLES")
    parsed = []
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        values = {name: finite_number(candle.get(field))
                  for name, field in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
        if any(value is None or value <= 0 for value in values.values()):
            continue
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]):
            continue
        timestamp = finite_number(candle.get("t"))
        if timestamp is not None:
            date = pd.to_datetime(timestamp, unit="s", utc=True, errors="coerce").tz_localize(None)
        else:
            raw_date = candle.get("date") or candle.get("tm")
            if not isinstance(raw_date, str) or not raw_date.strip():
                continue
            date = pd.to_datetime(raw_date, utc=True, errors="coerce").tz_localize(None)
        if pd.notna(date) and date <= now:
            parsed.append({"date": date, **values})
    if not parsed:
        raise ValueError("FCS_NO_VALID_CANDLES")
    frame = pd.DataFrame(parsed).sort_values("date").drop_duplicates().reset_index(drop=True)
    if frame["date"].dt.normalize().duplicated().any():
        raise ValueError("FCS_CONFLICTING_DAILY_CANDLES")
    return frame


def fetch_stockdata_live(pair, key):
    if not key:
        raise ValueError("API-Key nicht konfiguriert")
        
    symbol_raw = pair.replace("/", "")
    base_curr, quote_curr = pair.split("/") if "/" in pair else (pair[:3], pair[3:])
    
    urls_to_try = [
        f"https://api.stockdata.org/v1/news/all?language=en&symbols={symbol_raw}&api_token={key}",
        f"https://api.stockdata.org/v1/news/all?language=en&search={base_curr}%20{quote_curr}&api_token={key}",
        f"https://api.stockdata.org/v1/news/all?language=en&search={pair}&api_token={key}"
    ]
    
    last_error = None
    articles = []
    
    for url in urls_to_try:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 401 or r.status_code == 403:
                raise ValueError("API-Key ungültig")
            elif r.status_code == 429:
                raise ValueError("API Rate Limit erreicht")
            elif r.status_code >= 500:
                raise ValueError("API momentan nicht erreichbar")
            r.raise_for_status()
            
            res = r.json()
            if "error" in res:
                err_msg = str(res["error"].get("message", res["error"]))
                if "invalid" in err_msg.lower() or "token" in err_msg.lower():
                    raise ValueError("API-Key ungültig")
                elif "rate" in err_msg.lower() or "limit" in err_msg.lower():
                    raise ValueError("API Rate Limit erreicht")
                else:
                    raise ValueError(f"API Fehler: {err_msg}")
                    
            articles = res.get("data", [])
            if articles:
                break
        except requests.exceptions.RequestException as req_err:
            if hasattr(req_err, 'response') and req_err.response is not None:
                st_code = req_err.response.status_code
                if st_code == 401 or st_code == 403:
                    raise ValueError("API-Key ungültig")
                elif st_code == 429:
                    raise ValueError("API Rate Limit erreicht")
                elif st_code >= 500:
                    raise ValueError("API momentan nicht erreichbar")
            last_error = req_err

    if not articles:
        if last_error:
            raise last_error
        raise ValueError("Keine aktuellen Nachrichten für dieses Paar gefunden")
        
    scores = []
    symbol_raw = pair.replace("/", "").upper()
    for art in articles:
        found_entity = False
        entities = art.get("entities", [])
        for ent in entities:
            ent_sym = str(ent.get("symbol", "")).upper()
            if ent_sym == symbol_raw:
                s_val = ent.get("sentiment_score")
                if s_val is not None:
                    scores.append(float(s_val))
                    found_entity = True
                    break
        if not found_entity:
            if "sentiment_score" in art and art["sentiment_score"] is not None:
                scores.append(float(art["sentiment_score"]))
                
    if not scores:
        raise ValueError("Keine Sentiment-Bewertung in Nachrichten vorhanden")
        
    avg_score = float(sum(scores) / len(scores))
    scaled_sentiment = avg_score * 10.0
    return float(np.clip(scaled_sentiment, -10.0, 10.0))

def fetch_worldbank_live(country_code, indicator):
    # Auto-translate ZG to ZS for unemployment to get live data from World Bank
    if indicator == "SL.UEM.TOTL.ZG":
        indicator = "SL.UEM.TOTL.ZS"
    url = f"http://api.worldbank.org/v2/country/{country_code}/indicator/{indicator}?format=json&date=2015:2026"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    res = r.json()
    if len(res) < 2 or not isinstance(res[1], list):
        raise ValueError("World Bank data format invalid")
    parsed = []
    for item in res[1]:
        val = item.get("value")
        date_str = item.get("date")
        if val is not None:
            parsed.append({"date": f"{date_str}-12-31", "value": float(val)})
    df = pd.DataFrame(parsed)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


# ----------------- NEW DATA FETCHERS & HELPERS (Tiingo, BLS, IMF, EODHD, World Bank) -----------------
@st.cache_data(ttl=3600)
def get_tiingo_prices(ticker, api_key):
    if not api_key:
        return None
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Token {api_key}"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list):
                # Ascending order: data[-1] is the most recent
                return data[-1]
    except Exception:
        pass
    return None

@st.cache_data(ttl=3600)
def get_apifreaks_prices(api_key):
    if not api_key:
        return None
    url = "https://api.apifreaks.com/v1.0/commodity/rates/latest"
    params = {
        "apiKey": api_key,
        "symbols": "XAU,XAG,WTIOIL-SPOT,BRENTOIL-SPOT,VIX",
        "updates": "1m"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and data.get("success") and "rates" in data:
                return data
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400)
def get_bls_data(api_key):
    if not api_key:
        return None
    url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    headers = {"Content-type": "application/json"}
    
    current_year = datetime.now().year
    start_year = str(current_year - 2)
    end_year = str(current_year)
    
    payload = {
        "seriesid": ["CES0000000001", "CES0500000003", "LNS11300000"],
        "startyear": start_year,
        "endyear": end_year,
        "registrationkey": api_key
    }
    try:
        import json
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=12)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("status") == "REQUEST_SUCCEEDED":
                return res_json
    except Exception:
        pass
    return None

def parse_bls_series(bls_data, series_id):
    if not bls_data:
        return pd.DataFrame()
    try:
        series_list = bls_data.get("Results", {}).get("series", [])
        for s in series_list:
            if s.get("seriesID") == series_id:
                data_points = s.get("data", [])
                if not data_points:
                    return pd.DataFrame()
                
                records = []
                for dp in data_points:
                    year = dp.get("year")
                    period = dp.get("period")
                    period_name = dp.get("periodName")
                    val_str = dp.get("value")
                    try:
                        val = float(val_str)
                    except ValueError:
                        continue
                    
                    if period.startswith("M") and period[1:].isdigit():
                        month = int(period[1:])
                        date_obj = datetime(int(year), month, 1)
                        records.append({
                            "date": date_obj,
                            "value": val,
                            "period_name": period_name,
                            "year": year
                        })
                df = pd.DataFrame(records)
                if not df.empty:
                    df = df.sort_values("date").reset_index(drop=True)
                return df
    except Exception:
        pass
    return pd.DataFrame()

from html.parser import HTMLParser

class TradingEconomicsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell_data = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ["td", "th"] and self.in_row:
            self.in_cell = True
            self.current_cell_data = []

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            self.rows.append(self.current_row)
        elif tag in ["td", "th"] and self.in_cell:
            self.in_cell = False
            cell_text = " ".join(self.current_cell_data).strip()
            self.current_row.append(cell_text)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell_data.append(data)

@st.cache_data(ttl=3600)
def parse_tradingeconomics_pmi(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            parser = TradingEconomicsParser()
            parser.feed(r.text)
            
            g8_names = {
                "United States": "USA",
                "Euro Area": "EUR",
                "United Kingdom": "GBP",
                "Switzerland": "CHF",
                "Canada": "CAD",
                "Australia": "AUD",
                "New Zealand": "NZD",
                "Japan": "JPY"
            }
            
            results = {}
            for row in parser.rows:
                if len(row) >= 4:
                    country = row[0].strip()
                    if country in g8_names:
                        code = g8_names[country]
                        try:
                            last_val = float(row[1])
                        except (ValueError, TypeError):
                            last_val = None
                        try:
                            prev_val = float(row[2])
                        except (ValueError, TypeError):
                            prev_val = None
                        ref_date = row[3].strip()
                        results[code] = {
                            "last": last_val,
                            "previous": prev_val,
                            "reference": ref_date
                        }
            return results
    except Exception:
        pass
    return None

EODHD_EVENTS_CACHE_FILE = ".eodhd_events_cache.json"

def load_eodhd_events_cache():
    if os.path.exists(EODHD_EVENTS_CACHE_FILE):
        try:
            with open(EODHD_EVENTS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_eodhd_events_cache(events_list):
    sanitized = []
    for ev in events_list:
        if isinstance(ev, dict):
            sanitized.append({
                "date": str(ev.get("date", "")),
                "country": str(ev.get("country", "")),
                "name": str(ev.get("name", "")),
                "actual": ev.get("actual"),
                "previous": ev.get("previous"),
                "estimate": ev.get("estimate"),
                "change_percentage": ev.get("change_percentage")
            })
    _atomic_eodhd_json(EODHD_EVENTS_CACHE_FILE, sanitized)


def get_eodhd_batched_economic_events(api_key, start_date=None, end_date=None):
    """Dashboard reads never spend EODHD quota; only the collector refreshes."""
    return load_eodhd_events_cache()


def get_eodhd_pmi_fallback(country_code, indicator_keyword, api_key):
    events = get_eodhd_batched_economic_events(api_key)
    if not events:
        return None
    country_map = {
        "EUR": ["EMU", "EUR"], "GBP": ["GBR", "UK"], "CHF": ["CHE", "CH"],
        "CAD": ["CAN", "CA"], "AUD": ["AUS", "AU"], "NZD": ["NZL", "NZ"], "JPY": ["JPN", "JP"]
    }
    allowed_countries = country_map.get(country_code, [country_code])
    matched = []
    for ev in events:
        c = ev.get("country", "")
        name = ev.get("name", "").upper()
        if (c in allowed_countries or c == country_code) and indicator_keyword.upper() in name:
            matched.append(ev)
    if matched:
        matched.sort(key=lambda x: x.get("date", ""), reverse=True)
        latest = matched[0]
        try:
            last_val = float(latest.get("actual"))
        except (ValueError, TypeError):
            last_val = None
        try:
            prev_val = float(latest.get("previous"))
        except (ValueError, TypeError):
            prev_val = None
        ref_date = latest.get("date", "")
        return {
            "last": last_val,
            "previous": prev_val,
            "reference": ref_date
        }
    return None

def get_eodhd_pmi_historical(country_code, indicator_keyword, target_date, api_key):
    target_dt = pd.to_datetime(target_date)
    start_dt = target_dt - timedelta(days=120)
    events = get_eodhd_batched_economic_events(api_key, start_date=start_dt.strftime("%Y-%m-%d"), end_date=target_dt.strftime("%Y-%m-%d"))
    if not events:
        return None
    country_map = {
        "EUR": ["EMU", "EUR"], "GBP": ["GBR", "UK"], "CHF": ["CHE", "CH"],
        "CAD": ["CAN", "CA"], "AUD": ["AUS", "AU"], "NZD": ["NZL", "NZ"], "JPY": ["JPN", "JP"]
    }
    allowed_countries = country_map.get(country_code, [country_code])
    matched = []
    target_str = target_dt.strftime("%Y-%m-%d")
    for ev in events:
        c = ev.get("country", "")
        ev_date = str(ev.get("date", ""))
        name = ev.get("name", "").upper()
        if (c in allowed_countries or c == country_code) and indicator_keyword.upper() in name:
            if ev_date[:10] <= target_str:
                matched.append(ev)
    if matched:
        matched.sort(key=lambda x: x.get("date", ""))
        latest = matched[-1]
        try:
            last_val = float(latest.get("actual"))
        except (ValueError, TypeError):
            last_val = None
        try:
            prev_val = float(latest.get("previous"))
        except (ValueError, TypeError):
            prev_val = None
        ref_date = latest.get("date", "")
        return {
            "last": last_val,
            "previous": prev_val,
            "reference": ref_date
        }
    return None

FRED_PMI_SERIES = {
    "USD": {"m": "NAPM", "s": "NMFPT"},
    "EUR": {"m": "EUROPAMIMIPDSMEI", "s": "EUROPASEIPDSMEI"},
    "GBP": {"m": "GBRPAMIMIPDSMEI", "s": "GBRPASEIPDSMEI"},
    "JPY": {"m": "JPNPAMIMIPDSMEI", "s": "JPNPASEIPDSMEI"},
    "CAD": {"m": "CANPAMIMIPDSMEI", "s": "CANPASEIPDSMEI"},
    "AUD": {"m": "AUSPAMIMIPDSMEI", "s": "AUSPASEIPDSMEI"},
    "CHF": {"m": "CHEPAMIMIPDSMEI", "s": "CHEPASEIPDSMEI"},
    "NZD": {"m": "NZLPAMIMIPDSMEI", "s": "NZLPASEIPDSMEI"}
}

def get_all_pmi_data_historical(fred_key, eodhd_key, target_date):
    pmi_results = {}
    
    # USD (FRED)
    usa_m_last, usa_m_dt, _ = get_fred_data_historical("NAPM", target_date, fred_key)
    usa_m_prev = None
    if fred_key and usa_m_last is not None:
        df_napm, _, _ = get_fred_data("NAPM", fred_key)
        if df_napm is not None and not df_napm.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df_napm[df_napm["date"] <= target_dt].sort_values("date")
            if len(df_filtered) >= 2:
                usa_m_prev = float(df_filtered.iloc[-2]["value"])
                
    usa_s_last, usa_s_dt, _ = get_fred_data_historical("NMFPT", target_date, fred_key)
    usa_s_prev = None
    if fred_key and usa_s_last is not None:
        df_nmfpt, _, _ = get_fred_data("NMFPT", fred_key)
        if df_nmfpt is not None and not df_nmfpt.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df_nmfpt[df_nmfpt["date"] <= target_dt].sort_values("date")
            if len(df_filtered) >= 2:
                usa_s_prev = float(df_filtered.iloc[-2]["value"])
                
    usa_m_ref_str = usa_m_dt.strftime("%Y-%m-%d") if isinstance(usa_m_dt, datetime) else str(usa_m_dt) if usa_m_dt else None
    usa_s_ref_str = usa_s_dt.strftime("%Y-%m-%d") if isinstance(usa_s_dt, datetime) else str(usa_s_dt) if usa_s_dt else None
    usa_m_src = "FRED"
    usa_s_src = "FRED"

    pmi_results["USD"] = {
        "m_last": usa_m_last, "m_prev": usa_m_prev, "m_ref": usa_m_ref_str, "m_src": usa_m_src,
        "s_last": usa_s_last, "s_prev": usa_s_prev, "s_ref": usa_s_ref_str, "s_src": usa_s_src
    }
    
    # Other G8 Currencies (EODHD with FRED fallback)
    for code in ["EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]:
        m_last, m_prev, m_ref, m_src = None, None, None, "EODHD"
        s_last, s_prev, s_ref, s_src = None, None, None, "EODHD"
        
        if eodhd_key or load_eodhd_events_cache():
            res_m = get_eodhd_pmi_historical(code, "Manufacturing PMI", target_date, eodhd_key)
            if res_m:
                m_last = res_m["last"]
                m_prev = res_m["previous"]
                m_ref = res_m["reference"]
                
            res_s = get_eodhd_pmi_historical(code, "Services PMI", target_date, eodhd_key)
            if res_s:
                s_last = res_s["last"]
                s_prev = res_s["previous"]
                s_ref = res_s["reference"]
                
        # FRED Fallback
        if m_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                val, dt, _ = get_fred_data_historical(series_info["m"], target_date, fred_key)
                if val is not None:
                    m_last = val
                    m_ref = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
                    m_src = "FRED"
                    df_m, _, _ = get_fred_data(series_info["m"], fred_key)
                    if df_m is not None and not df_m.empty:
                        target_dt = pd.to_datetime(target_date)
                        df_filtered = df_m[df_m["date"] <= target_dt].sort_values("date")
                        if len(df_filtered) >= 2:
                            m_prev = float(df_filtered.iloc[-2]["value"])

        if s_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                val, dt, _ = get_fred_data_historical(series_info["s"], target_date, fred_key)
                if val is not None:
                    s_last = val
                    s_ref = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
                    s_src = "FRED"
                    df_s, _, _ = get_fred_data(series_info["s"], fred_key)
                    if df_s is not None and not df_s.empty:
                        target_dt = pd.to_datetime(target_date)
                        df_filtered = df_s[df_s["date"] <= target_dt].sort_values("date")
                        if len(df_filtered) >= 2:
                            s_prev = float(df_filtered.iloc[-2]["value"])
                            
        pmi_results[code] = {
            "m_last": m_last, "m_prev": m_prev, "m_ref": m_ref, "m_src": m_src,
            "s_last": s_last, "s_prev": s_prev, "s_ref": s_ref, "s_src": s_src
        }
        
    return pmi_results

def get_all_pmi_data(fred_key, eodhd_key, target_date=None):
    if not check_demo_active() and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        # Survey identity and redistribution permission are not established yet.
        # Historical research paths are preserved; live CORE stays unavailable.
        return {}
    is_today_or_yesterday = False
    if target_date is not None:
        try:
            target_dt = pd.to_datetime(target_date).date()
            today_dt = datetime.now().date()
            is_today_or_yesterday = (today_dt - target_dt).days <= 2
        except Exception:
            pass
            
    if target_date is not None and not is_today_or_yesterday:
        return get_all_pmi_data_historical(fred_key, eodhd_key, target_date)
        
    te_m = parse_tradingeconomics_pmi("https://tradingeconomics.com/country-list/manufacturing-pmi")
    te_s = parse_tradingeconomics_pmi("https://tradingeconomics.com/country-list/services-pmi")
    
    pmi_results = {}
    
    # USD
    usa_m_last, usa_m_prev, usa_m_ref = None, None, None
    usa_s_last, usa_s_prev, usa_s_ref = None, None, None
    
    if fred_key:
        df_napm, _, is_live = get_fred_data("NAPM", fred_key)
        if is_live and df_napm is not None and not df_napm.empty:
            df_napm = df_napm.sort_values("date", ascending=False)
            if len(df_napm) >= 1:
                usa_m_last = float(df_napm.iloc[0]["value"])
                usa_m_ref = df_napm.iloc[0]["date"].strftime("%b/%y")
            if len(df_napm) >= 2:
                usa_m_prev = float(df_napm.iloc[1]["value"])
                
    if usa_m_last is None and te_m and "USA" in te_m:
        usa_m_last = te_m["USA"]["last"]
        usa_m_prev = te_m["USA"]["previous"]
        usa_m_ref = te_m["USA"]["reference"]
        
    if te_s and "USA" in te_s:
        usa_s_last = te_s["USA"]["last"]
        usa_s_prev = te_s["USA"]["previous"]
        usa_s_ref = te_s["USA"]["reference"]
        
    pmi_results["USD"] = {
        "m_last": usa_m_last, "m_prev": usa_m_prev, "m_ref": usa_m_ref, "m_src": "FRED/TE" if fred_key else "TE",
        "s_last": usa_s_last, "s_prev": usa_s_prev, "s_ref": usa_s_ref, "s_src": "TE"
    }
    
    for code in ["EUR", "GBP", "CHF", "CAD", "AUD", "NZD", "JPY"]:
        m_last, m_prev, m_ref, m_src = None, None, None, "TE"
        s_last, s_prev, s_ref, s_src = None, None, None, "TE"
        
        if te_m and code in te_m:
            m_last = te_m[code]["last"]
            m_prev = te_m[code]["previous"]
            m_ref = te_m[code]["reference"]
        
        if m_last is None:
            res_eod = get_eodhd_pmi_fallback(code, "Manufacturing PMI", eodhd_key)
            if res_eod:
                m_last = res_eod["last"]
                m_prev = res_eod["previous"]
                m_ref = res_eod["reference"]
                m_src = "EODHD"
                
        # FRED Fallback for Manufacturing
        if m_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                df_m, _, _ = get_fred_data(series_info["m"], fred_key)
                if df_m is not None and not df_m.empty:
                    m_last = float(df_m.iloc[-1]["value"])
                    m_ref = df_m.iloc[-1]["date"].strftime("%Y-%m-%d")
                    m_src = "FRED"
                    if len(df_m) >= 2:
                        m_prev = float(df_m.iloc[-2]["value"])
                        
        if te_s and code in te_s:
            s_last = te_s[code]["last"]
            s_prev = te_s[code]["previous"]
            s_ref = te_s[code]["reference"]
            
        if s_last is None:
            res_eod = get_eodhd_pmi_fallback(code, "Services PMI", eodhd_key)
            if res_eod:
                s_last = res_eod["last"]
                s_prev = res_eod["previous"]
                s_ref = res_eod["reference"]
                s_src = "EODHD"
                
        # FRED Fallback for Services
        if s_last is None and fred_key:
            series_info = FRED_PMI_SERIES.get(code)
            if series_info:
                df_s, _, _ = get_fred_data(series_info["s"], fred_key)
                if df_s is not None and not df_s.empty:
                    s_last = float(df_s.iloc[-1]["value"])
                    s_ref = df_s.iloc[-1]["date"].strftime("%Y-%m-%d")
                    s_src = "FRED"
                    if len(df_s) >= 2:
                        s_prev = float(df_s.iloc[-2]["value"])
                        
        pmi_results[code] = {
            "m_last": m_last, "m_prev": m_prev, "m_ref": m_ref, "m_src": m_src,
            "s_last": s_last, "s_prev": s_prev, "s_ref": s_ref, "s_src": s_src
        }
        
    return pmi_results

@st.cache_data(ttl=604800) # 1 week
def get_imf_data(indicator):
    url = f"https://www.imf.org/external/datamapper/api/v1/{indicator}"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None

def get_latest_imf_value(curr, indicator):
    mapping = {
        "USD": ["USA"],
        "EUR": ["EUR", "EMU", "U2", "DEU"],
        "GBP": ["GBR"],
        "CHF": ["CHE"],
        "CAD": ["CAN"],
        "AUD": ["AUS"],
        "NZD": ["NZL"],
        "JPY": ["JPN"]
    }
    candidates = mapping.get(curr, [curr])
    data = get_imf_data(indicator)
    if not data:
        return None
    try:
        indicator_data = data.get("values", {}).get(indicator, {})
        for code in candidates:
            values_dict = indicator_data.get(code, {})
            if values_dict:
                years = [int(yr) for yr in values_dict.keys() if yr.isdigit()]
                if years:
                    latest_year = str(max(years))
                    val = values_dict[latest_year]
                    if val is not None:
                        return val
    except Exception:
        pass
    return None

def format_imf_indicator(base, quote, indicator):
    base_val = get_latest_imf_value(base, indicator)
    quote_val = get_latest_imf_value(quote, indicator)
    base_str = f"{base_val:.1f}%" if base_val is not None else "N/A"
    quote_str = f"{quote_val:.1f}%" if quote_val is not None else "N/A"
    return f"{base_str} / {quote_str}"



def get_latest_worldbank_trade_balance(country_code):
    try:
        df, _, _ = get_worldbank_data(country_code, "NE.RSB.GNFS.ZS")
        if df is not None and not df.empty:
            return df.iloc[-1]["value"]
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400) # 1 day
def get_oecd_cli_data():
    url = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/?format=csv"
    try:
        response = requests.get(url, timeout=20)
        if response.status_code == 200:
            df = pd.read_csv(io.StringIO(response.text))
            return df
    except Exception:
        pass
    return None

def get_latest_oecd_cli(curr):
    mapping = {
        "USD": "USA",
        "EUR": "EA20",
        "GBP": "GBR",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL",
        "JPY": "JPN"
    }
    country_code = mapping.get(curr)
    if not country_code:
        return None
        
    df = get_oecd_cli_data()
    if df is None or df.empty:
        return None
        
    try:
        df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == country_code)]
        if df_m.empty and curr == "EUR":
            df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == "EA19")]
            
        if df_m.empty:
            return None
            
        # Try indicators in order of preference: LI (CLI), BCICP (BCI proxy), CCICP (CCI proxy)
        for indicator in ["LI", "BCICP", "CCICP"]:
            df_ind = df_m[df_m["MEASURE"] == indicator]
            if not df_ind.empty:
                latest = df_ind.sort_values("TIME_PERIOD").iloc[-1]
                val = float(latest["OBS_VALUE"])
                if not pd.isna(val):
                    return val, latest["TIME_PERIOD"]
    except Exception:
        pass
    return None


# ----------------- 2. CACHED API LOADERS (Zero-Overlap & TTLs) -----------------
def check_demo_active():
    try:
        return st.session_state.get("demo_mode_chk", False)
    except Exception:
        return False

def is_data_valid(val, is_live):
    if val is None:
        return False
    if isinstance(val, pd.DataFrame) and val.empty:
        return False
    if not is_live and not check_demo_active():
        return False
    return True

# ----------------- 2. CACHED API LOADERS (Zero-Overlap & TTLs) -----------------
@st.cache_data(ttl=86400, show_spinner=False)
def get_ons_cpi_data():
    """
    Fetches the Consumer Price Index (CPI) 12-month rate (series D7G7) from the ONS timeseries API.
    Returns: (df, last_update_time, is_live)
             df has columns: 'date' (pd.Timestamp), 'value' (float) and 'release_date' (pd.Timestamp)
    """
    try:
        url = "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data"
        res = requests.get(url, timeout=15)
        if res.status_code != 200:
            raise ValueError(f"ONS HTTP Error {res.status_code}")
        
        data = res.json()
        months = data.get("months", [])
        if not months:
            raise ValueError("No monthly observations found in ONS response")
            
        records = []
        for m in months:
            # Parse observation date (e.g. '2026 JUL')
            try:
                obs_dt = pd.to_datetime(m["date"])
            except Exception:
                continue
                
            # Parse value
            try:
                val = float(m["value"])
            except ValueError:
                continue
                
            # Parse update/release date (e.g. '2026-08-18T23:00:00.000Z')
            release_dt = None
            is_pit_ltd = False
            if "updateDate" in m and m["updateDate"]:
                try:
                    raw_dt = pd.to_datetime(m["updateDate"]).tz_localize(None)
                    # 2015-10-12 is the global migration date
                    if raw_dt.strftime("%Y-%m-%d") == "2015-10-12":
                        release_dt = obs_dt + pd.offsets.MonthEnd(0) + pd.Timedelta(days=25)
                        is_pit_ltd = True
                    else:
                        release_dt = raw_dt
                except Exception:
                    pass
            
            # Fallback if release_date is missing
            if release_dt is None:
                release_dt = obs_dt + pd.offsets.MonthEnd(0) + pd.Timedelta(days=25)
                is_pit_ltd = True
                
            records.append({
                "date": obs_dt,
                "value": val,
                "release_date": release_dt,
                "is_pit_limited": is_pit_ltd
            })
            
        if not records:
            raise ValueError("No valid records parsed from ONS data")
            
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            # Generate clean mock data
            mock_records = []
            now = datetime.now()
            for i in range(24):
                dt = (now - timedelta(days=30 * (23 - i))).replace(day=1)
                mock_records.append({
                    "date": pd.to_datetime(dt.strftime("%Y-%m-%d")),
                    "value": 2.0 + 0.1 * i,
                    "release_date": pd.to_datetime((dt + timedelta(days=20)).strftime("%Y-%m-%d"))
                })
            df_mock = pd.DataFrame(mock_mock_records if 'mock_mock_records' in locals() else mock_records)
            return df_mock, datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=86400, show_spinner=False)
def get_statcan_cpi_data():
    """
    Fetches CPI index levels (series v41690973) from Statistics Canada WDS API.
    Returns: (df, last_update_time, is_live)
             df has columns: 'date' (pd.Timestamp), 'value' (float),
             'release_date' (pd.Timestamp) and 'is_pit_limited' (bool)
    """
    try:
        url = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"
        res = requests.post(url, json=[{"vectorId": 41690973, "latestN": 300}], timeout=15)
        if res.status_code != 200:
            raise ValueError(f"StatCan HTTP Error {res.status_code}")
        
        data = res.json()
        if not data or data[0].get("status") != "SUCCESS":
            raise ValueError(f"StatCan API status: {data[0].get('status') if data else 'Empty'}")
            
        vector_data = data[0].get("object", {}).get("vectorDataPoint", [])
        if not vector_data:
            raise ValueError("No vectorDataPoint found in StatCan response")
            
        records = []
        for dp in vector_data:
            try:
                obs_dt = pd.to_datetime(dp["refPer"])
                val = float(dp["value"])
                # StatCan releaseTime is e.g. '2026-08-17T08:30'
                release_dt = pd.to_datetime(dp["releaseTime"])
                if release_dt.tzinfo is None:
                    release_dt = release_dt.tz_localize("America/Toronto", ambiguous="raise", nonexistent="raise")
                release_dt = release_dt.tz_convert("UTC").tz_localize(None)
            except Exception:
                continue
                
            records.append({
                "date": obs_dt,
                "value": val,
                "release_date": release_dt,
                "is_pit_limited": False
            })
            
        if not records:
            raise ValueError("No valid records parsed from StatCan data")
            
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            mock_records = []
            now = datetime.now()
            for i in range(300):
                dt = (now - timedelta(days=30 * (299 - i))).replace(day=1)
                mock_records.append({
                    "date": pd.to_datetime(dt.strftime("%Y-%m-%d")),
                    "value": 100.0 + 0.25 * i,
                    "release_date": pd.to_datetime((dt + timedelta(days=20)).strftime("%Y-%m-%d")),
                    "is_pit_limited": False
                })
            df_mock = pd.DataFrame(mock_records)
            return df_mock, datetime.now(), False
        return None, datetime.now(), False


@st.cache_data(ttl=86400, show_spinner=False)
def get_abs_cpi_data():
    """
    Fetches Monthly Headline CPI YoY (3.10001.10.50.M) from the Australian Bureau of Statistics SDMX API.
    Returns: (df, last_update_time, is_live)
             df has columns: 'date' (pd.Timestamp), 'value' (float),
             'release_date' (pd.Timestamp) and 'is_pit_limited' (bool)
    """
    try:
        url = "https://data.api.abs.gov.au/rest/data/CPI/3.10001.10.50.M?lastNObservations=100"
        res = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
        if res.status_code != 200:
            raise ValueError(f"ABS HTTP Error {res.status_code}")
            
        data = res.json()
        datasets = data.get("dataSets", [])
        if not datasets or not datasets[0].get("series"):
            raise ValueError("No series data found in ABS response")
            
        series_data = next(iter(datasets[0].get("series", {}).values()))
        obs = series_data.get("observations", {})
        if not obs:
            raise ValueError("No observations found in ABS response")
            
        time_periods = data.get("structure", {}).get("dimensions", {}).get("observation", [])[0].get("values", [])
        
        records = []
        for t_idx, obs_val in obs.items():
            try:
                t_str = time_periods[int(t_idx)].get("id")
                # Parse period (e.g. '2026-06') to day-1 of that month
                obs_dt = pd.to_datetime(t_str + "-01")
                val = float(obs_val[0])
                # Conservative release date approximation: MonthEnd + 30 days
                release_dt = obs_dt + pd.offsets.MonthEnd(0) + pd.Timedelta(days=30)
            except Exception:
                continue
                
            records.append({
                "date": obs_dt,
                "value": val,
                "release_date": release_dt,
                "is_pit_limited": True
            })
            
        if not records:
            raise ValueError("No valid records parsed from ABS data")
            
        df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            mock_records = []
            now = datetime.now()
            for i in range(15):
                dt = (now - timedelta(days=30 * (14 - i))).replace(day=1)
                mock_records.append({
                    "date": pd.to_datetime(dt.strftime("%Y-%m-%d")),
                    "value": 2.5 + 0.1 * i,
                    "release_date": pd.to_datetime((dt + timedelta(days=30)).strftime("%Y-%m-%d")),
                    "is_pit_limited": True
                })
            df_mock = pd.DataFrame(mock_records)
            return df_mock, datetime.now(), False
        return None, datetime.now(), False


@st.cache_data(ttl=86400, show_spinner=False)
def get_estat_cpi_data():
    """
    Fetches official Headline CPI YoY and Index level from Statistics Bureau of Japan via e-Stat API.
    Primary preferred: StatsDataId 0004052037 (2025-Base Consumer Price Index Basic figures (Monthly) All Japan)
    Fallback: StatsDataId 0003427113 (2020-Base Consumer Price Index Basic figures (Monthly) All Japan)
    Category: 0001 (All items / 総合)
    Area: 00000 (All Japan / 全国)
    cdTab=3: Direct Official YoY % (DIRECT_OFFICIAL)
    cdTab=1: Index level for cross-validation
    """
    app_id = get_estat_app_id()
    if not app_id:
        return None
        
    for stats_id, base_year in [("0004052037", "2025"), ("0003427113", "2020")]:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            url_yoy = f"https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId={app_id}&statsDataId={stats_id}&cdCat01=0001&cdArea=00000&cdTab=3&limit=200"
            r_yoy = requests.get(url_yoy, headers=headers, timeout=15)
            if r_yoy.status_code != 200:
                continue
            data_yoy = r_yoy.json()
            values_yoy = data_yoy.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])

            url_idx = f"https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId={app_id}&statsDataId={stats_id}&cdCat01=0001&cdArea=00000&cdTab=1&limit=200"
            r_idx = requests.get(url_idx, headers=headers, timeout=15)
            if r_idx.status_code != 200:
                continue
            data_idx = r_idx.json()
            values_idx = data_idx.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])

            def parse_time(t_raw):
                if len(t_raw) == 10 and not t_raw.endswith("0000"):
                    return f"{t_raw[:4]}-{t_raw[6:8]}-01"
                return None

            records_yoy = {}
            for v in values_yoy:
                d_str = parse_time(v.get("@time", ""))
                val_str = v.get("$")
                if d_str and val_str is not None:
                    records_yoy[d_str] = float(val_str)

            records_idx = {}
            for v in values_idx:
                d_str = parse_time(v.get("@time", ""))
                val_str = v.get("$")
                if d_str and val_str is not None:
                    records_idx[d_str] = float(val_str)

            records = []
            all_dates = sorted(list(set(records_yoy.keys()) | set(records_idx.keys())))
            for d_str in all_dates:
                obs_dt = pd.to_datetime(d_str)
                direct_val = records_yoy.get(d_str)
                idx_val = records_idx.get(d_str)
                
                # Deterministic PIT release date rule: 3rd Friday of following month
                release_dt = obs_dt + pd.offsets.MonthEnd(0) + pd.offsets.Week(3, weekday=4)
                
                records.append({
                    "date": obs_dt,
                    "value": direct_val,
                    "index_level": idx_val,
                    "release_date": release_dt,
                    "base_year": base_year,
                    "stats_id": stats_id,
                    "is_pit_limited": True
                })

            if records:
                df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
                df["derived_yoy"] = (df["index_level"] / df["index_level"].shift(12) - 1.0) * 100.0
                return df, datetime.now(), True
        except Exception:
            continue
            
    if check_demo_active():
        mock_records = []
        now = datetime.now()
        for i in range(24):
            dt = (now - timedelta(days=30 * (23 - i))).replace(day=1)
            mock_records.append({
                "date": pd.to_datetime(dt.strftime("%Y-%m-%d")),
                "value": 1.5 + 0.05 * i,
                "index_level": 110.0 + 0.2 * i,
                "derived_yoy": 1.5 + 0.05 * i,
                "release_date": pd.to_datetime((dt + timedelta(days=21)).strftime("%Y-%m-%d")),
                "is_pit_limited": True
            })
        df_mock = pd.DataFrame(mock_records)
        return df_mock, datetime.now(), False
    return None, datetime.now(), False


def parse_statsnz_cpi_release(html, expected_period):
    """Read the published all-groups annual CPI, never an embedded local fixture."""
    import csv
    from bs4 import BeautifulSoup
    period = pd.Period(expected_period, freq="Q")
    expected_title = f"Consumers price index: {period.end_time.strftime('%B %Y')} quarter"
    for tag in BeautifulSoup(html, "html.parser").select("[data-value]"):
        try:
            payload = json.loads(tag["data-value"])
            if not isinstance(payload, dict) or payload.get("Title") != expected_title:
                continue
            release = pd.Timestamp(payload["DateTaxonomyTerm"]["PublicationDate"])
            # Stats NZ publishes in New Zealand local time; keep a UTC-naive timestamp.
            release = release.tz_localize("Pacific/Auckland").tz_convert("UTC").tz_localize(None)
            if release > pd.Timestamp.now(tz="UTC").tz_localize(None) or release < period.end_time.normalize():
                continue
            for series in payload.get("FeaturedMedia", {}).get("SeriesData", []):
                rows = list(csv.reader(io.StringIO(series.get("GraphCsvData", "").lstrip("\ufeff"))))
                if not rows or rows[0][0] != "Quarter":
                    continue
                for row in rows[1:]:
                    if not row or row[0] != "CPI all groups (annual)":
                        continue
                    for label, raw in zip(rows[0][1:], row[1:]):
                        observed = pd.to_datetime(label.replace("Sept-", "Sep-"), format="%b-%y").to_period("Q")
                        value = finite_number(raw)
                        if observed == period and value is not None and abs(value) <= 25:
                            return {"date": period.end_time.normalize(), "value": value,
                                    "index_level": None, "derived_yoy": None,
                                    "release_date": release, "is_pit_limited": True}
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def get_statsnz_cpi_data():
    """Fetch the latest published quarter from Stats NZ without an API key.

    Release title, quarter, annual all-groups series and publication timestamp
    must agree. A successful API heartbeat is never proof of economic data.
    Only the observed release is returned; historical vintages are not invented.
    """
    completed = pd.Timestamp.now(tz="UTC").tz_localize(None).to_period("Q") - 1
    for offset in range(3):
        period = completed - offset
        slug = period.end_time.strftime("%B-%Y").lower()
        url = f"https://www.stats.govt.nz/information-releases/consumers-price-index-{slug}-quarter/"
        try:
            response = requests.get(url, timeout=12)
            if response.status_code != 200:
                continue
            record = parse_statsnz_cpi_release(response.text, str(period))
            if record is not None:
                record["source_url"] = url
                return pd.DataFrame([record]), datetime.now(), True
        except requests.RequestException:
            continue
    return None, datetime.now(), False


@st.cache_data(ttl=86400, show_spinner=False)
def get_fred_data(series_id, key, units=None):
    if not key:
        if check_demo_active():
            return generate_mock_fred(series_id), datetime.now(), False
        return None, datetime.now(), False
    try:
        df = fetch_fred_live(series_id, key, units=units)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            return generate_mock_fred(series_id), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_av_data(from_symbol, to_symbol, key):
    if not key:
        if check_demo_active():
            return generate_mock_av(from_symbol, to_symbol), datetime.now(), False
        return None, datetime.now(), False
    try:
        df = fetch_av_live(from_symbol, to_symbol, key)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            return generate_mock_av(from_symbol, to_symbol), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=3600, show_spinner=False)
def get_benzinga_data(key):
    if not key:
        if check_demo_active():
            return generate_mock_benzinga(), datetime.now(), False
        return None, datetime.now(), False
    try:
        df = fetch_benzinga_live(key)
        if df.empty:
            raise ValueError("Empty response")
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            return generate_mock_benzinga(), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=21600, show_spinner=False)
def get_finnhub_data(pair, key):
    if "api_errors" not in st.session_state:
        st.session_state["api_errors"] = {}
    if not key:
        st.session_state["api_errors"]["Finnhub API"] = "API-Key nicht konfiguriert"
        if check_demo_active():
            return generate_mock_finnhub(pair), datetime.now(), False
        return None, datetime.now(), False
    try:
        data = fetch_finnhub_live(pair, key)
        st.session_state["api_errors"]["Finnhub API"] = None
        return data, datetime.now(), True
    except Exception as e:
        st.session_state["api_errors"]["Finnhub API"] = str(e)
        if check_demo_active():
            return generate_mock_finnhub(pair), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=60, show_spinner=False)
def get_itick_data(pair, key):
    if not key:
        if check_demo_active():
            return generate_mock_itick(pair), datetime.now(), False
        return None, datetime.now(), False
    try:
        data = fetch_itick_live(pair, key)
        return data, datetime.now(), True
    except Exception:
        if check_demo_active():
            return generate_mock_itick(pair), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_av_technical_data(pair, key):
    if not key:
        if check_demo_active():
            import random
            random.seed(hash(pair) % 15000)
            base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
            base = base_prices.get(pair, 1.0)
            return {
                "SMA_50": base * random.uniform(0.99, 1.01),
                "SMA_200": base * random.uniform(0.97, 0.99)
            }, datetime.now(), False
        return None, datetime.now(), False
    try:
        from_sym, to_sym = pair.split("/")
        df = fetch_av_live(from_sym, to_sym, key)
        if not df.empty and len(df) >= 50:
            df = calculate_smas(df)
            latest = df.iloc[-1]
            return {
                "SMA_50": float(latest["SMA_50"]) if "SMA_50" in latest else None,
                "SMA_200": float(latest["SMA_200"]) if "SMA_200" in latest else None
            }, datetime.now(), True
        else:
            raise ValueError("Insufficient data for SMA")
    except Exception:
        if check_demo_active():
            import random
            random.seed(hash(pair) % 15000)
            base_prices = {"EUR/USD": 1.0850, "GBP/USD": 1.2720, "USD/JPY": 158.50, "USD/CHF": 0.8910, "AUD/USD": 0.6650, "USD/CAD": 1.3680, "NZD/USD": 0.6120, "EUR/GBP": 0.8520}
            base = base_prices.get(pair, 1.0)
            return {
                "SMA_50": base * random.uniform(0.99, 1.01),
                "SMA_200": base * random.uniform(0.97, 0.99)
            }, datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_history_data(pair, key):
    if not key:
        if check_demo_active():
            from_sym, to_sym = pair.split("/")
            return generate_mock_fcs_history(from_sym, to_sym), datetime.now(), False
        return None, datetime.now(), False
    try:
        df = fetch_fcs_history_live(pair, key)
        return df, datetime.now(), True
    except Exception:
        if check_demo_active():
            from_sym, to_sym = pair.split("/")
            return generate_mock_fcs_history(from_sym, to_sym), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_correlation_data(key):
    if not key:
        if check_demo_active():
            return generate_mock_fcs_correlation(), datetime.now(), False
        return None, datetime.now(), False
    try:
        pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP"]
        prices = {}
        for p in pairs:
            df = fetch_fcs_history_live(p, key)
            if not df.empty:
                prices[p] = df.tail(30).set_index("date")["close"]
        if len(prices) == len(pairs):
            rdf = pd.DataFrame(prices).ffill().bfill()
            return rdf.corr(), datetime.now(), True
        else:
            raise ValueError("Failed to retrieve all pairs for correlation")
    except Exception:
        if check_demo_active():
            return generate_mock_fcs_correlation(), datetime.now(), False
        return None, datetime.now(), False

@st.cache_data(ttl=900, show_spinner=False)
def get_stockdata_sentiment(pair, key):
    if "api_errors" not in st.session_state:
        st.session_state["api_errors"] = {}
    if not key:
        status_msg = "🔴 StockData Sentiment: API-Key fehlt"
        st.session_state["api_errors"]["StockData Sentiment"] = "API-Key nicht in Streamlit Secrets konfiguriert"
        if check_demo_active():
            return 0.0, datetime.now(), False, status_msg
        return None, datetime.now(), False, status_msg
    try:
        val = fetch_stockdata_live(pair, key)
        status_msg = "🟢 StockData Sentiment: Aktiv (API-Verbindung erfolgreich)"
        st.session_state["api_errors"]["StockData Sentiment"] = None
        return val, datetime.now(), True, status_msg
    except Exception as e:
        err_str = str(e)
        if "ungültig" in err_str:
            status_msg = "🔴 StockData Sentiment: API-Key ungültig"
        elif "Rate Limit" in err_str:
            status_msg = "🟠 StockData Sentiment: API Rate Limit erreicht"
        elif "nicht erreichbar" in err_str:
            status_msg = "🟠 StockData Sentiment: API momentan nicht erreichbar"
        else:
            status_msg = f"🟠 StockData Sentiment: {err_str}"
            
        st.session_state["api_errors"]["StockData Sentiment"] = err_str
        if check_demo_active():
            return 0.0, datetime.now(), False, status_msg
        return None, datetime.now(), False, status_msg

@st.cache_data(ttl=604800, show_spinner=False)
def get_worldbank_data(country_code, indicator):
    try:
        df = fetch_worldbank_live(country_code, indicator)
        return df, datetime.now(), True
    except Exception:
        return generate_mock_worldbank(country_code, indicator), datetime.now(), False

def parse_worldbank_latest(wb_result):
    try:
        if wb_result is None:
            return None, None
        df, _, _ = wb_result
        if df is None or df.empty:
            return None, None
        latest_row = df.iloc[-1]
        val = latest_row["value"]
        dt = latest_row["date"]
        if hasattr(dt, "year"):
            year = str(dt.year)
        else:
            year = str(dt).split("-")[0]
        return val, year
    except Exception:
        return None, None

@st.cache_data(ttl=3600, show_spinner=False)
def get_ecb_rate_cached():
    url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?lastNObservations=2&format=jsondata"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
    r.raise_for_status()
    res = r.json()
    series = res["dataSets"][0]["series"]
    series_key = list(series.keys())[0]
    obs = series[series_key]["observations"]
    sorted_keys = sorted(obs.keys(), key=int)
    latest_val = float(obs[sorted_keys[-1]][0])
    prev_val = float(obs[sorted_keys[-2]][0]) if len(sorted_keys) > 1 else latest_val
    bps_change = int((latest_val - prev_val) * 100)
    return latest_val, bps_change

@st.cache_data(ttl=3600, show_spinner=False)
def get_snb_rate_cached():
    url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
    r = requests.get(url, timeout=8)
    r.raise_for_status()
    lines = r.text.split("\n")
    data_lines = []
    start_reading = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('"Date";'):
            start_reading = True
        if start_reading:
            data_lines.append(line)
    if not data_lines:
        raise ValueError("Could not find data in SNB CSV")
    
    df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep=";")
    df_lz = df[df["D0"] == "LZ"].copy()
    if df_lz.empty:
        raise ValueError("LZ key not found in SNB data")
    
    df_lz = df_lz.sort_values("Date")
    latest_val = float(df_lz.iloc[-1]["Value"])
    prev_val = float(df_lz.iloc[-2]["Value"]) if len(df_lz) > 1 else latest_val
    bps_change = int((latest_val - prev_val) * 100)
    return latest_val, bps_change



# ----------------- NEWS LOADER & FALLBACKS -----------------
@st.cache_data(ttl=60, show_spinner=False)
def get_news_data_search(query, newsdata_key, newsapi_key):
    debug_logs = []
    articles = []
    source = None
    success = False

    # 1. Test NewsData.io Key first with a simple check if present
    if newsdata_key:
        debug_logs.append("NewsData.io: API-Key vorhanden. Starte Verbindungstest...")
        try:
            url = "https://newsdata.io/api/1/latest"
            params = {
                "apikey": newsdata_key,
                "q": "forex",
                "size": 1
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"NewsData.io Test: HTTP Status {r.status_code}")
            
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "success":
                    debug_logs.append("NewsData.io: Verbindungstest erfolgreich.")
                    debug_logs.append(f"NewsData.io: Führe Suche für '{query}' aus...")
                    params_actual = {
                        "apikey": newsdata_key,
                        "q": query,
                        "language": "en,de"
                    }
                    r_actual = requests.get(url, params=params_actual, timeout=10)
                    debug_logs.append(f"NewsData.io Suche: HTTP Status {r_actual.status_code}")
                    if r_actual.status_code == 200:
                        res_actual = r_actual.json()
                        if res_actual.get("status") == "success" and res_actual.get("results"):
                            for a in res_actual["results"]:
                                articles.append({
                                    "title": a.get("title") or "Ohne Titel",
                                    "description": a.get("description") or "",
                                    "url": a.get("link") or "#",
                                    "source": a.get("source_id") or "NewsData",
                                    "publishedAt": a.get("pubDate") or "",
                                    "urlToImage": a.get("image_url"),
                                    "api": "NewsData.io"
                                })
                            debug_logs.append(f"NewsData.io: Suche erfolgreich, {len(articles)} Artikel gefunden.")
                            success = True
                            source = "NewsData.io"
                        else:
                            debug_logs.append("NewsData.io: Keine passenden Artikel für diese Suchanfrage gefunden.")
                    else:
                        debug_logs.append(f"NewsData.io Suche fehlgeschlagen: HTTP {r_actual.status_code}. Antwort: {r_actual.text[:150]}")
                else:
                    debug_logs.append(f"NewsData.io Verbindungstest meldete Fehler: {res.get('results') or res.get('error')}")
            else:
                debug_logs.append(f"NewsData.io Verbindungstest fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"NewsData.io: Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("NewsData.io: API-Key fehlt in .env.")

    time.sleep(0.5)

    # 2. Try NewsAPI.org
    if newsapi_key:
        debug_logs.append("NewsAPI.org: API-Key vorhanden. Starte Suche...")
        
        def query_newsapi(q_term):
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": q_term,
                "apiKey": newsapi_key,
                "sortBy": "publishedAt",
                "pageSize": 25,
                "language": "de,en"
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"NewsAPI.org Suche ({q_term[:30]}...): HTTP Status {r.status_code}")
            if r.status_code == 200:
                res = r.json()
                if res.get("status") == "ok" and res.get("articles"):
                    parsed_articles = []
                    for a in res["articles"]:
                        if a.get("title") and a.get("title") != "[Removed]":
                            parsed_articles.append({
                                "title": a.get("title"),
                                "description": a.get("description") or "",
                                "url": a.get("url") or "#",
                                "source": a.get("source", {}).get("name") or "NewsAPI",
                                "publishedAt": a.get("publishedAt") or "",
                                "urlToImage": a.get("urlToImage"),
                                "api": "NewsAPI.org"
                            })
                    return parsed_articles
                else:
                    debug_logs.append(f"NewsAPI.org ({q_term[:30]}...): Antwort enthielt keine Artikel.")
            else:
                debug_logs.append(f"NewsAPI.org fehlgeschlagen ({q_term[:30]}...): HTTP {r.status_code}. Antwort: {r.text[:100]}")
            return []

        na_articles = query_newsapi(query)
        if not na_articles:
            words = query.split()
            base_q = words[0] if len(words) >= 1 else "EUR"
            quote_q = words[1] if len(words) >= 2 else "USD"
            simple_q = f"{base_q} {quote_q} forex"
            debug_logs.append(f"NewsAPI.org: Erster Versuch leer. Weiche auf einfacheren Suchbegriff '{simple_q}' aus...")
            na_articles = query_newsapi(simple_q)
            
        if na_articles:
            debug_logs.append(f"NewsAPI.org: Suche erfolgreich, {len(na_articles)} Artikel gefunden.")
            if not articles:
                articles = na_articles
                source = "NewsAPI.org (Fallback)"
            else:
                existing_titles = {art["title"].lower()[:50] for art in articles}
                for a in na_articles:
                    title_prefix = a["title"].lower()[:50]
                    if title_prefix not in existing_titles:
                        articles.append(a)
                source = "Combined (NewsData & NewsAPI)"
            success = True
        else:
            debug_logs.append("NewsAPI.org: Beide Suchversuche lieferten keine Artikel.")
    else:
        debug_logs.append("NewsAPI.org: API-Key fehlt in .env.")

    if success and articles:
        debug_logs.append(f"Zusammenfassung: API {source} verwendet, insgesamt {len(articles)} Artikel geladen.")
        return articles[:25], source, True, datetime.now(), debug_logs

    # Both failed completely
    debug_logs.append("Zusammenfassung: Keine API lieferte Ergebnisse. News-APIs momentan nicht verfügbar. Weiche auf Mock-News aus.")
    mock_articles = []
    base_mock = generate_mock_news()
    for m in base_mock:
        m_copy = m.copy()
        m_copy["title"] = f"[{query}] " + m_copy["title"]
        m_copy["urlToImage"] = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
        m_copy["api"] = "MOCK-News Engine"
        mock_articles.append(m_copy)
    return mock_articles, "News-APIs momentan nicht verfügbar (Demo-Modus)", False, datetime.now(), debug_logs


# ----------------- HISTORICAL BACKTEST DATA HELPERS -----------------
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fred_history_full(series_id, target_date, key):
    if not key:
        return None
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_end={target_date_str}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            parsed = []
            for o in obs:
                if o["value"] != ".":
                    parsed.append({"date": pd.to_datetime(o["date"]), "value": float(o["value"])})
            if parsed:
                return pd.DataFrame(parsed)
    except Exception:
        pass
    # Fallback to full series fetching
    try:
        url_live = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json"
        r = requests.get(url_live, timeout=8)
        if r.status_code == 200:
            obs = r.json().get("observations", [])
            parsed = []
            for o in obs:
                if o["value"] != ".":
                    parsed.append({"date": pd.to_datetime(o["date"]), "value": float(o["value"])})
            if parsed:
                df = pd.DataFrame(parsed)
                target_dt = pd.to_datetime(target_date)
                return df[df["date"] <= target_dt]
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_fred_data_historical(series_id, target_date, fred_key=FRED_KEY):
    if not fred_key:
        return None, None, False

    try:
        df, _, is_live = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            target_dt = pd.to_datetime(target_date)
            # strictly point-in-time filter (no look-ahead)
            df_past = df[df["date"] <= target_dt]
            if not df_past.empty:
                closest_row = df_past.sort_values("date", ascending=False).iloc[0]
                return float(closest_row["value"]), closest_row["date"], is_live
    except Exception:
        pass

    return None, None, False


EODHD_BONDS_CACHE_FILE = ".eodhd_bonds_cache.json"
EODHD_STATUS_FILE = ".eodhd_status.json"
EODHD_DAILY_LIMIT = 20
EODHD_2Y_TICKERS = ["DE2Y.GBOND", "UK2Y.GBOND", "SW2Y.GBOND", "CA2Y.GBOND", "AU2Y.GBOND", "NZ2Y.GBOND", "JP2Y.GBOND"]
EODHD_5Y_TICKERS = ["DE5Y.GBOND", "UK5Y.GBOND", "CA5Y.GBOND", "AU5Y.GBOND", "NZ5Y.GBOND", "JP5Y.GBOND"]
_EODHD_STATUS_INFO = {"status": "⚪ EODHD NOT CHECKED", "code": None}


def _atomic_eodhd_json(path, data):
    import tempfile
    folder = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(prefix=".eodhd-", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _eodhd_utc_now():
    from datetime import timezone
    return datetime.now(timezone.utc)


def load_eodhd_status():
    try:
        with open(EODHD_STATUS_FILE, "r", encoding="utf-8") as handle:
            status = json.load(handle)
        return status if isinstance(status, dict) else {}
    except (OSError, ValueError):
        return {}


def save_eodhd_status(status_dict):
    _atomic_eodhd_json(EODHD_STATUS_FILE, status_dict)


def get_eodhd_status_label():
    persisted = load_eodhd_status()
    checked_day = persisted.get("day_utc", str(persisted.get("last_checked", ""))[:10])
    if checked_day and checked_day != _eodhd_utc_now().strftime("%Y-%m-%d"):
        return "🟡 EODHD CACHE – LAST CHECK " + checked_day
    return persisted.get("status", "⚪ EODHD NOT CHECKED")


def load_eodhd_bonds_cache():
    try:
        with open(EODHD_BONDS_CACHE_FILE, "r", encoding="utf-8") as handle:
            cache = json.load(handle)
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def save_eodhd_bonds_cache(ticker, parsed_data):
    cache = load_eodhd_bonds_cache()
    history = sorted(parsed_data, key=lambda row: row["date"])
    cache[ticker] = {
        "ticker": ticker,
        "observation_date": history[-1]["date"] if history else None,
        "value": history[-1]["value"] if history else None,
        "fetched_at": _eodhd_utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "EODHD GBOND",
        "history": history
    }
    _atomic_eodhd_json(EODHD_BONDS_CACHE_FILE, cache)


def _parse_eodhd_bond_entry(cached_entry):
    if isinstance(cached_entry, dict):
        return cached_entry.get("history")
    return cached_entry if isinstance(cached_entry, list) else None


def get_eodhd_bond_data(ticker, api_key):
    """Read stored genuine yields, including with no local API key. Never fetch."""
    cached = load_eodhd_bonds_cache().get(ticker)
    history = _parse_eodhd_bond_entry(cached)
    if not history:
        return None
    try:
        df = pd.DataFrame(history)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df[df["date"].notna() & np.isfinite(df["value"])].sort_values("date").reset_index(drop=True)
        if df.empty:
            return None
        df.attrs["source"] = "EODHD GBOND"
        df.attrs["fetched_at"] = cached.get("fetched_at") if isinstance(cached, dict) else None
        df.attrs["observation_date"] = df.iloc[-1]["date"].strftime("%Y-%m-%d")
        return df
    except (ValueError, TypeError, KeyError):
        return None


def _eodhd_daily_status(status, today):
    # Retain known legacy usage on the current day, never silently reset it.
    old_day = status.get("day_utc", str(status.get("last_checked", ""))[:10])
    if old_day != today:
        status = {}
    status.setdefault("calls", 0)
    status.setdefault("attempts", {})
    status["day_utc"] = today
    status["daily_limit"] = EODHD_DAILY_LIMIT
    return status


def _summarize_eodhd_status(status):
    attempts = status.get("attempts", {})
    results = [entry.get("result") for entry in attempts.values()]
    successes = sum(result == "SUCCESS" for result in results)
    failures = [result for result in results if result != "SUCCESS"]
    if "AUTH_FAILED" in results:
        label = "🔴 EODHD AUTH FAILED"
    elif "LIMIT_EXHAUSTED" in results or status.get("budget_exhausted"):
        label = "🟡 DAILY LIMIT EXHAUSTED"
    elif "NO_API_KEY" in results:
        label = "🔴 EODHD API KEY MISSING"
    elif failures:
        label = "🟡 EODHD DATA PARTIAL" if successes or "TRUNCATED" in results else "🔴 EODHD DATA UNAVAILABLE"
    elif len(results) == 14:
        label = "🟢 EODHD AVAILABLE"
    else:
        label = "⚪ EODHD COLLECTION INCOMPLETE"
    status["status"] = label
    status["success"] = len(results) == 14 and not failures and not status.get("budget_exhausted")
    status["collection_status"] = "SUCCESS" if status["success"] else ("PARTIAL" if successes or "TRUNCATED" in results else "FAILED")
    for stage, field in [("2y", "p1_2y_calls"), ("pmi", "p2_pmi_calls"), ("5y", "p3_5y_calls")]:
        status[field] = sum(entry.get("stage") == stage and entry.get("requested", False) for entry in attempts.values())
    return status


def prefetch_eodhd_production_data(api_key=EODHD_KEY):
    """Only EODHD writer: 7 true 2Y, one shared events call, then 6 true 5Y.

    The UTC daily ledger is reserved before each request (including timeouts).
    A same-day rerun reuses every recorded attempt and never retries it. The
    process lock covers both caches and ledger; Streamlit readers spend zero.
    """
    import fcntl
    import math
    resources = [(ticker, "2y") for ticker in EODHD_2Y_TICKERS]
    resources += [("economic-events", "pmi")]
    resources += [(ticker, "5y") for ticker in EODHD_5Y_TICKERS]
    with open(EODHD_STATUS_FILE + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = _eodhd_daily_status(load_eodhd_status(), _eodhd_utc_now().strftime("%Y-%m-%d"))
        for resource, stage in resources:
            status = _eodhd_daily_status(status, _eodhd_utc_now().strftime("%Y-%m-%d"))
            if resource in status["attempts"]:
                if status["attempts"][resource].get("requested"):
                    continue
                # Installing a missing key does not retry a spent provider call.
                del status["attempts"][resource]
            if any(entry.get("result") in ("AUTH_FAILED", "LIMIT_EXHAUSTED", "NO_API_KEY") for entry in status["attempts"].values()):
                break
            if status["calls"] >= EODHD_DAILY_LIMIT:
                status["budget_exhausted"] = True
                break
            now = _eodhd_utc_now()
            entry = {"stage": stage, "requested": False, "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            status["attempts"][resource] = entry
            if not api_key:
                entry["result"] = "NO_API_KEY"
                break
            # Persist the reservation first. A crash cannot spend this slot twice.
            entry.update({"requested": True, "result": "REQUEST_RESERVED"})
            status["calls"] += 1
            status["last_checked"] = entry["checked_at"]
            save_eodhd_status(_summarize_eodhd_status(status))
            try:
                if stage == "pmi":
                    start = (now - timedelta(days=120)).strftime("%Y-%m-%d")
                    end = now.strftime("%Y-%m-%d")
                    params = {"api_token": api_key, "from": start, "to": end, "limit": 1000}
                    response = requests.get("https://eodhd.com/api/economic-events", params=params, timeout=12)
                else:
                    params = {"api_token": api_key, "fmt": "json", "from": "2015-01-01"}
                    response = requests.get("https://eodhd.com/api/eod/" + resource, params=params, timeout=10)
                entry["http_status"] = response.status_code
                if response.status_code == 402:
                    entry["result"] = "LIMIT_EXHAUSTED"
                elif response.status_code in (401, 403):
                    entry["result"] = "AUTH_FAILED"
                elif response.status_code != 200:
                    entry["result"] = "SERVICE_ERROR"
                else:
                    data = response.json()
                    if not isinstance(data, list) or not data:
                        entry["result"] = "EMPTY_OR_INVALID_RESPONSE"
                    elif stage == "pmi":
                        events = [event for event in data if isinstance(event, dict) and event.get("date") and event.get("country") and event.get("name")]
                        if not events:
                            entry["result"] = "EMPTY_OR_INVALID_RESPONSE"
                        else:
                            save_eodhd_events_cache(events)
                            entry.update({"result": "TRUNCATED" if len(data) >= 1000 else "SUCCESS", "rows": len(events), "from": start, "to": end, "coverage_complete": len(data) < 1000})
                    else:
                        parsed = []
                        for row in data:
                            if not isinstance(row, dict) or not row.get("date") or row.get("close") is None:
                                continue
                            value = float(row["close"])
                            date = datetime.strptime(str(row["date"]), "%Y-%m-%d").strftime("%Y-%m-%d")
                            if math.isfinite(value) and date <= now.strftime("%Y-%m-%d"):
                                parsed.append({"date": date, "value": value})
                        if parsed:
                            save_eodhd_bonds_cache(resource, parsed)
                            latest_date = max(row["date"] for row in parsed)
                            age_days = (now.date() - datetime.strptime(latest_date, "%Y-%m-%d").date()).days
                            entry.update({"result": "SUCCESS" if age_days <= 15 else "STALE_DATA", "rows": len(parsed), "observation_date": latest_date})
                        else:
                            entry["result"] = "EMPTY_OR_INVALID_RESPONSE"
            except Exception as error:
                # Class names are diagnostic; URLs, tokens and payloads are not.
                entry.update({"result": "SERVICE_ERROR", "error_type": type(error).__name__})
            save_eodhd_status(_summarize_eodhd_status(status))
        status["last_checked"] = _eodhd_utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_eodhd_status(_summarize_eodhd_status(status))
        return status


def get_eodhd_bond_historical(ticker, target_date, api_key=EODHD_KEY):
    df = get_eodhd_bond_data(ticker, api_key)
    if df is not None and not df.empty:
        target_dt = pd.to_datetime(target_date)
        df_past = df[df["date"] <= target_dt]
        if not df_past.empty:
            closest_row = df_past.sort_values("date", ascending=False).iloc[0]
            return float(closest_row["value"]), closest_row["date"], True
    return None, None, False

@st.cache_data(ttl=3600, show_spinner=False)
def get_official_2y_data(curr, target_date):
    """Two keyless official benchmark series; no alternate maturity or curve proxy."""
    try:
        target = pd.Timestamp(target_date).normalize()
        end = min(target, pd.Timestamp(datetime.now().date()))
        start = end - pd.Timedelta(days=30)
        records = []
        if curr == "EUR":
            series = "BBSSY.D.REN.EUR.A610.000000WT0202.A"
            response = requests.get(
                "https://api.statistiken.bundesbank.de/rest/data/BBSSY/D.REN.EUR.A610.000000WT0202.A",
                params={"format": "csv", "lang": "en", "startPeriod": start.strftime("%Y-%m-%d"),
                        "endPeriod": end.strftime("%Y-%m-%d")}, timeout=12)
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.text.lstrip("\ufeff")), dtype=str)
            if len(frame.columns) != 3 or frame.columns[1] != series:
                return None
            metadata = dict(zip(frame.iloc[:, 0], frame.iloc[:, 1]))
            if metadata.get("unit") != "PROZENT" or metadata.get("unit multiplier") != "One":
                return None
            for row in frame.itertuples(index=False, name=None):
                if isinstance(row[0], str) and len(row[0]) == 10 and row[0][4] == "-" and row[0][7] == "-":
                    # Bundesbank flags such as provisional/estimated are not silently accepted.
                    if pd.notna(row[2]) and str(row[2]).strip():
                        continue
                    records.append({"date": row[0], "value": row[1]})
            source = f"Bundesbank (Germany 2Y benchmark; {series})"
        elif curr == "CAD":
            series = "BD.CDN.2YR.DQ.YLD"
            response = requests.get(f"https://www.bankofcanada.ca/valet/observations/{series}/json",
                                    params={"start_date": start.strftime("%Y-%m-%d"),
                                            "end_date": end.strftime("%Y-%m-%d")}, timeout=12)
            response.raise_for_status()
            payload = response.json()
            if payload.get("seriesDetail", {}).get(series, {}).get("label") != "Benchmark bond yield: 2 year":
                return None
            records = [{"date": row.get("d"), "value": row.get(series, {}).get("v")}
                       for row in payload.get("observations", [])]
            source = f"Bank of Canada (2Y benchmark; {series})"
        else:
            return None
        valid = []
        for row in records:
            value = finite_number(row.get("value"))
            date = pd.to_datetime(row.get("date"), errors="coerce")
            if value is not None and -5 <= value <= 30 and pd.notna(date) and date <= end:
                valid.append({"date": date, "value": value})
        if not valid:
            return None
        frame = pd.DataFrame(valid).sort_values("date").reset_index(drop=True)
        # Conflicting records for an observation date must not be arbitrarily resolved.
        if frame.groupby("date")["value"].nunique().gt(1).any():
            return None
        frame = frame.drop_duplicates("date")
        frame.attrs["source"] = source
        return frame
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        return None


def get_genuine_2y_yield_historical(curr, target_date, fred_key=FRED_KEY, eodhd_key=EODHD_KEY):
    if use_live_core_cache(target_date):
        detail = live_data.details(curr)
        observation = detail["_observations"].get("Geldpolitik", {})
        return (observation.get("yield_2y") if detail.get("Geldpolitik") is not None else None,
                observation.get("date"), observation.get("source", "UNAVAILABLE"))
    if curr in {"EUR", "CAD"}:
        official = get_official_2y_data(curr, target_date)
        if official is not None and not official.empty:
            row = official.iloc[-1]
            if observation_freshness(row["date"], target_date, 5, 15) in {"FRESH", "AGING"}:
                return float(row["value"]), row["date"], official.attrs["source"]
    if curr == "JPY" and pd.Timestamp(target_date).date() == datetime.now().date():
        from official_yields import fetch_japan_mof_2y
        observation = fetch_japan_mof_2y(target_date, client=requests)
        if observation and observation_freshness(observation["observation_date"], target_date, 5, 15) in {"FRESH", "AGING"}:
            return observation["value"], observation["observation_date"], observation["source"]
    if curr == "USD" and pd.Timestamp(target_date).date() == datetime.now().date():
        from official_yields import fetch_treasury_2y
        try:
            observation = fetch_treasury_2y(target_date, client=requests)
            if observation and observation_freshness(observation["observation_date"], target_date, 5, 15) in {"FRESH", "AGING"}:
                return observation["value"], observation["observation_date"], observation["source"]
        except requests.exceptions.RequestException:
            return None, None, "US Treasury: SOURCE_UNAVAILABLE"
        except Exception:
            return None, None, "US Treasury: SOURCE_CONFLICT"
        return None, None, "US Treasury: SOURCE_UNAVAILABLE"
    # Historical USD paths retain FRED DGS2.
    if curr == "USD":
        if fred_key:
            val, dt, is_live = get_fred_data_historical("DGS2", target_date, fred_key)
            if val is not None:
                return val, dt, "FRED"
        if eodhd_key or load_eodhd_bonds_cache():
            val, dt, is_live = get_eodhd_bond_historical("US2Y.GBOND", target_date, eodhd_key)
            if val is not None:
                return val, dt, "EODHD (US2Y.GBOND)"
                
    # EUR: EODHD DE2Y.GBOND (transparently Germany 2Y)
    elif curr == "EUR":
        if eodhd_key or load_eodhd_bonds_cache():
            val, dt, is_live = get_eodhd_bond_historical("DE2Y.GBOND", target_date, eodhd_key)
            if val is not None:
                return val, dt, "EODHD"
                
    # Other G8 currencies: genuine EODHD 2Y yields
    elif curr in ["GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]:
        ticker_map = {
            "GBP": "UK2Y.GBOND",
            "JPY": "JP2Y.GBOND",
            "CHF": "SW2Y.GBOND",
            "CAD": "CA2Y.GBOND",
            "AUD": "AU2Y.GBOND",
            "NZD": "NZ2Y.GBOND"
        }
        ticker = ticker_map.get(curr)
        if ticker:
            val, dt, is_live = get_eodhd_bond_historical(ticker, target_date, eodhd_key)
            if val is not None:
                return val, dt, "EODHD"
                
    if check_demo_active():
        mock_map = {
            "USD": 4.25, "EUR": 2.75, "GBP": 4.35, "JPY": 0.15,
            "CHF": 0.85, "CAD": 3.15, "AUD": 3.85, "NZD": 4.15
        }
        return mock_map.get(curr, 2.0), pd.to_datetime(target_date), "Demo Mock"
        
    return None, None, ""

def get_genuine_5y_yield_historical(curr, target_date, fred_key=FRED_KEY, eodhd_key=EODHD_KEY):
    """
    Fetches genuine 5Y government benchmark bond yields.
    Strictly forbids proxies, synthetic blends, or mock data.
    If unavailable (e.g. CHF), returns None, None, '5Y YIELD UNAVAILABLE'.
    """
    if curr == "USD":
        if fred_key:
            val, dt, is_live = get_fred_data_historical("DGS5", target_date, fred_key)
            if val is not None:
                return val, dt, "FRED (DGS5)"
        if eodhd_key or load_eodhd_bonds_cache():
            val, dt, is_live = get_eodhd_bond_historical("US5Y.GBOND", target_date, eodhd_key)
            if val is not None:
                return val, dt, "EODHD (US5Y.GBOND)"
                
    elif curr == "EUR":
        if eodhd_key or load_eodhd_bonds_cache():
            val, dt, is_live = get_eodhd_bond_historical("DE5Y.GBOND", target_date, eodhd_key)
            if val is not None:
                return val, dt, "EODHD (Germany 5Y Benchmark)"
                
    elif curr in ["GBP", "CAD", "AUD", "NZD", "JPY"]:
        ticker_map = {
            "GBP": ("UK5Y.GBOND", "EODHD (UK 5Y Gilt)"),
            "CAD": ("CA5Y.GBOND", "EODHD (Canada 5Y)"),
            "AUD": ("AU5Y.GBOND", "EODHD (Australia 5Y)"),
            "NZD": ("NZ5Y.GBOND", "EODHD (New Zealand 5Y)"),
            "JPY": ("JP5Y.GBOND", "EODHD (Japan 5Y JGB)")
        }
        entry = ticker_map.get(curr)
        if entry:
            ticker, src_label = entry
            val, dt, is_live = get_eodhd_bond_historical(ticker, target_date, eodhd_key)
            if val is not None:
                return val, dt, src_label
                
    elif curr == "CHF":
        return None, None, "5Y YIELD UNAVAILABLE"
        
    return None, None, "5Y YIELD UNAVAILABLE"


def get_yield_series(curr):
    if curr == "USD":
        df, _, is_live = get_fred_data("DGS2", FRED_KEY)
        return df
    else:
        ticker = YIELD_2Y_SERIES.get(curr)
        if ticker:
            return get_eodhd_bond_data(ticker, EODHD_KEY)
    return None


def get_yield_trends(curr, target_date=None):
    if target_date is None:
        target_date = datetime.now()
    target_dt = pd.to_datetime(target_date)
    
    df = get_yield_series(curr)
    if df is not None and not df.empty:
        df_filtered = df[df["date"] <= target_dt].sort_values("date")
        if len(df_filtered) >= 1:
            val_now = float(df_filtered.iloc[-1]["value"])
            dt_now = df_filtered.iloc[-1]["date"]
            
            val_1w = float(df_filtered.iloc[-6]["value"]) if len(df_filtered) >= 6 else None
            val_1m = float(df_filtered.iloc[-21]["value"]) if len(df_filtered) >= 21 else None
            val_3m = float(df_filtered.iloc[-61]["value"]) if len(df_filtered) >= 61 else None
            
            chg_1w = val_now - val_1w if val_1w is not None else None
            chg_1m = val_now - val_1m if val_1m is not None else None
            chg_3m = val_now - val_3m if val_3m is not None else None
            
            return {
                "val_now": val_now, "dt_now": dt_now,
                "val_1w": val_1w, "chg_1w": chg_1w,
                "val_1m": val_1m, "chg_1m": chg_1m,
                "val_3m": val_3m, "chg_3m": chg_3m
            }
    return {}


@st.cache_data(ttl=86400, show_spinner=False)
def get_ecb_rate_historical(target_date):
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.MRR_FR.LEV?startPeriod={target_date_str}&endPeriod={target_date_str}&format=jsondata"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                if obs:
                    val = float(list(obs.values())[0][0])
                    return val, pd.to_datetime(target_date_str)
    except Exception:
        pass

    # Fallback to querying series up to target date using endPeriod
    try:
        target_date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        url = f"https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.MRR_FR.LEV?endPeriod={target_date_str}&format=jsondata"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                
                dimensions = res["structure"]["dimensions"]["observation"]
                time_dim = next(dim for dim in dimensions if dim["id"] == "TIME_PERIOD")
                time_values = [v["id"] for v in time_dim["values"]]
                
                parsed = []
                for idx_str, val_list in obs.items():
                    idx = int(idx_str)
                    date_str = time_values[idx]
                    parsed.append((pd.to_datetime(date_str), float(val_list[0])))
                if parsed:
                    parsed.sort(key=lambda x: x[0])
                    return parsed[-1][1], parsed[-1][0]
    except Exception:
        pass
        
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_boc_rate_historical_cached():
    # Try OVERNIGHT_DAILY, V39079, V122514
    for series in ["OVERNIGHT_DAILY", "V39079", "V122514"]:
        try:
            url = f"https://www.bankofcanada.ca/valet/observations/{series}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                obs = data.get("observations", [])
                parsed = []
                for o in obs:
                    date_str = o.get("d")
                    series_data = o.get(series)
                    if series_data and "v" in series_data:
                        val = float(series_data["v"])
                        parsed.append((pd.to_datetime(date_str), val))
                if parsed:
                    df = pd.DataFrame(parsed, columns=["date", "value"]).sort_values("date").reset_index(drop=True)
                    return df
        except Exception:
            pass
    return None

def get_boc_rate_historical(target_date):
    df = get_boc_rate_historical_cached()
    if df is not None and not df.empty:
        target_dt = pd.to_datetime(target_date)
        df_past = df[df["date"] <= target_dt]
        if not df_past.empty:
            closest = df_past.sort_values("date", ascending=False).iloc[0]
            return float(closest["value"]), "Bank of Canada API"
            
    # Fallback to FRED IRSTCI01CAM156N
    val_fred, dt_fred, _ = get_fred_data_historical("IRSTCI01CAM156N", target_date)
    if val_fred is not None:
        return val_fred, "FRED (IRSTCI01CAM156N)"
        
    return None, "Keine Daten verfügbar"

@st.cache_data(ttl=86400, show_spinner=False)
def get_snb_rate_historical(target_date):
    try:
        url = "https://data.snb.ch/api/cube/snboffzisa/data/csv/en"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        lines = r.text.split("\n")
        data_lines = []
        start_reading = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith('"date";') or line.lower().startswith('date;'):
                start_reading = True
            if start_reading:
                data_lines.append(line)
        if not data_lines:
            raise ValueError("Could not find data in SNB CSV")
        
        df = pd.read_csv(io.StringIO("\n".join(data_lines)), sep=";")
        df_lz = df[df["D0"] == "LZ"].copy()
        if df_lz.empty:
            raise ValueError("LZ key not found in SNB data")
        
        df_lz["parsed_date"] = pd.to_datetime(df_lz["Date"], format="%Y-%m")
        df_lz = df_lz.dropna(subset=["Value"])
        df_lz = df_lz.sort_values("parsed_date")
        
        target_dt = pd.to_datetime(target_date)
        df_lz_past = df_lz[df_lz["parsed_date"] <= target_dt]
        if not df_lz_past.empty:
            closest = df_lz_past.sort_values("parsed_date", ascending=False).iloc[0]
            return float(closest["Value"]), closest["parsed_date"]
    except Exception:
        pass
    return None, None

@st.cache_data(ttl=86400, show_spinner=False)
def get_worldbank_data_historical_cached(country_code, indicator):
    if country_code == "EMU":
        country_code = "DEU"
    try:
        # Auto-translate ZG to ZS for unemployment
        ind_code = "SL.UEM.TOTL.ZS" if indicator == "SL.UEM.TOTL.ZG" else indicator
        url = f"https://api.worldbank.org/v2/country/{country_code}/indicator/{ind_code}?format=json&per_page=1000"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        res = r.json()
        if len(res) >= 2 and isinstance(res[1], list):
            parsed = []
            for item in res[1]:
                val = item.get("value")
                date_str = item.get("date")
                if val is not None:
                    parsed.append({"date": f"{date_str}-12-31", "value": float(val)})
            if parsed:
                df = pd.DataFrame(parsed)
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date").reset_index(drop=True), True
    except Exception:
        pass
    return None, False

def get_worldbank_data_historical(country_code, indicator, target_date):
    if country_code == "EMU":
        country_code = "DEU"
    df, is_live = get_worldbank_data_historical_cached(country_code, indicator)
    if df is not None and not df.empty:
        target_year = pd.to_datetime(target_date).year
        df = df.copy()
        df["year"] = df["date"].dt.year
        df_filtered = df[df["year"] <= target_year]
        if not df_filtered.empty:
            latest_row = df_filtered.sort_values("year").iloc[-1]
            return float(latest_row["value"]), latest_row["date"], is_live
    return None, None, False

def get_historical_oecd_cli(curr, target_date):
    mapping = {
        "USD": "USA",
        "EUR": "EA20",
        "GBP": "GBR",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL",
        "JPY": "JPN"
    }
    country_code = mapping.get(curr)
    if not country_code:
        return None
        
    df = get_oecd_cli_data()
    df_m = None
    if df is not None and not df.empty:
        try:
            df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == country_code)]
            if df_m.empty and curr == "EUR":
                df_m = df[(df["FREQ"] == "M") & (df["REF_AREA"] == "EA19")]
        except Exception:
            pass

    if df_m is not None and not df_m.empty:
        try:
            target_dt = pd.to_datetime(target_date)
            for indicator in ["LI", "BCICP", "CCICP"]:
                df_ind = df_m[df_m["MEASURE"] == indicator].copy()
                if not df_ind.empty:
                    df_ind["parsed_date"] = pd.to_datetime(df_ind["TIME_PERIOD"], errors="coerce")
                    df_ind = df_ind.dropna(subset=["parsed_date"])
                    df_filtered = df_ind[df_ind["parsed_date"] <= target_dt]
                    if not df_filtered.empty:
                        latest = df_filtered.sort_values("parsed_date").iloc[-1]
                        val = float(latest["OBS_VALUE"])
                        if not pd.isna(val):
                            return val, latest["TIME_PERIOD"]
        except Exception:
            pass

    return None

def get_country_rate_historical(country_code, target_date):
    map_code = {
        "USA": "USD", "USD": "USD", "US": "USD",
        "EMU": "EUR", "EUR": "EUR", "EU": "EUR",
        "CHE": "CHF", "CHF": "CHF", "CH": "CHF",
        "GBR": "GBP", "GBP": "GBP", "UK": "GBP",
        "JPN": "JPY", "JPY": "JPY", "JP": "JPY",
        "AUS": "AUD", "AUD": "AUD", "AU": "AUD",
        "CAN": "CAD", "CAD": "CAD", "CA": "CAD",
        "NZL": "NZD", "NZD": "NZD", "NZ": "NZD"
    }
    curr = map_code.get(country_code, country_code)
    
    if curr == "USD":
        val, dt, _ = get_fred_data_historical("FEDFUNDS", target_date)
        if val is not None:
            return val, "FRED (FEDFUNDS)"
            
    elif curr == "EUR":
        val, _ = get_ecb_rate_historical(target_date)
        if val is not None:
            return val, "ECB API"
            
    elif curr == "CHF":
        val, _ = get_snb_rate_historical(target_date)
        if val is not None:
            return val, "SNB API"
            
    elif curr == "GBP":
        val, dt, _ = get_fred_data_historical("IRSTCI01GBM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01GBM156N)"
            
    elif curr == "JPY":
        val, dt, _ = get_fred_data_historical("IRSTCI01JPM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01JPM156N)"
            
    elif curr == "AUD":
        val, dt, _ = get_fred_data_historical("IRSTCI01AUM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01AUM156N)"
            
    elif curr == "NZD":
        val, dt, _ = get_fred_data_historical("IRSTCI01NZM156N", target_date)
        if val is not None:
            return val, "FRED (IRSTCI01NZM156N)"
            
    elif curr == "CAD":
        val, src = get_boc_rate_historical(target_date)
        if val is not None:
            return val, src
            
    return None, "Keine Daten verfügbar"



@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_commodities(target_date, key=FRED_KEY):
    res = {}
    
    # 1. Gold and Silver from timeseries API
    try:
        target_dt = pd.to_datetime(target_date)
        start_dt = target_dt - timedelta(days=5)
        start_str = start_dt.strftime("%Y-%m-%d")
        
        url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
        params = {
            "start_date": start_str,
            "end_date": target_date,
            "base": "USD",
            "symbols": "XAU,XAG"
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            rates_dict = data.get("data", {}).get("rates", {})
            sorted_dates = sorted(rates_dict.keys())
            
            # Gold
            gold_vals = []
            for d in sorted_dates:
                val = rates_dict[d].get("XAU")
                if val and val > 0:
                    gold_vals.append((d, 1.0 / val))
            if gold_vals:
                res["gold"] = gold_vals[-1][1]
                if len(gold_vals) >= 2:
                    res["gold_chg"] = ((gold_vals[-1][1] - gold_vals[-2][1]) / gold_vals[-2][1]) * 100
                else:
                    res["gold_chg"] = 0.0
            else:
                res["gold"] = None
                res["gold_chg"] = 0.0
                
            # Silver
            silver_vals = []
            for d in sorted_dates:
                val = rates_dict[d].get("XAG")
                if val and val > 0:
                    silver_vals.append((d, 1.0 / val))
            if silver_vals:
                res["silver"] = silver_vals[-1][1]
                if len(silver_vals) >= 2:
                    res["silver_chg"] = ((silver_vals[-1][1] - silver_vals[-2][1]) / silver_vals[-2][1]) * 100
                else:
                    res["silver_chg"] = 0.0
            else:
                res["silver"] = None
                res["silver_chg"] = 0.0
    except Exception:
        res["gold"] = None
        res["gold_chg"] = 0.0
        res["silver"] = None
        res["silver_chg"] = 0.0

    # 2. WTI, Brent, VIX from FRED
    if key:
        series_map = {
            "wti": "DCOILWTICO",
            "brent": "DCOILBRENTEU",
            "vix": "VIXCLS"
        }
        for name, series_id in series_map.items():
            val, dt, _ = get_fred_data_historical(series_id, target_date, key)
            if val is not None:
                df = fetch_fred_history_full(series_id, target_date, key)
                chg_pct = 0.0
                if df is not None and len(df) >= 2:
                    df = df.sort_values("date")
                    last_val = df.iloc[-1]["value"]
                    prev_val = df.iloc[-2]["value"]
                    if prev_val != 0:
                        chg_pct = ((last_val - prev_val) / prev_val) * 100
                res[name] = val
                res[name + "_chg"] = chg_pct
            else:
                res[name] = None
                res[name + "_chg"] = 0.0
    else:
        res["wti"] = None
        res["wti_chg"] = 0.0
        res["brent"] = None
        res["brent_chg"] = 0.0
        res["vix"] = None
        res["vix_chg"] = 0.0
        
    return res

def get_historical_labor_data(target_date, key=FRED_KEY):
    if not key:
        return None
        
    series_map = {
        "nfp": "PAYEMS",
        "wage": "CES0500000003",
        "part": "CIVPART"
    }
    
    res = {}
    for name, series_id in series_map.items():
        val, dt, _ = get_fred_data_historical(series_id, target_date, key)
        if val is not None:
            df = fetch_fred_history_full(series_id, target_date, key)
            chg = 0.0
            if df is not None and len(df) >= 2:
                df = df.sort_values("date")
                last_val = df.iloc[-1]["value"]
                prev_val = df.iloc[-2]["value"]
                chg = last_val - prev_val
            res[name] = val
            res[name + "_chg"] = chg
        else:
            res[name] = None
            res[name + "_chg"] = None
            
    if all(res[name] is None for name in series_map.keys()):
        return None
    return res

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_news(pair, target_date, key=STOCKDATA_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    target_dt = pd.to_datetime(target_date)
    date_str = target_dt.strftime("%Y-%m-%d")
    url = f"https://api.stockdata.org/v1/news/all?language=en&symbols={symbol}&published_on={date_str}&api_token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res = r.json()
            articles = res.get("data", [])
            parsed = []
            for art in articles[:5]:
                parsed.append({
                    "title": art.get("title"),
                    "source": art.get("source"),
                    "publishedAt": art.get("published_at"),
                    "url": art.get("url"),
                    "description": art.get("description"),
                    "urlToImage": art.get("image_url") or "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80",
                    "api": "StockData API"
                })
            return parsed
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_recommendations(pair, target_date, key=FINNHUB_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    url = f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            trends = r.json()
            if isinstance(trends, list) and trends:
                target_dt = pd.to_datetime(target_date)
                valid = []
                for t in trends:
                    p_dt = pd.to_datetime(t.get("period"))
                    if p_dt <= target_dt:
                        valid.append((p_dt, t))
                if valid:
                    valid.sort(key=lambda x: x[0])
                    latest_trend = valid[-1][1]
                    
                    buy_cnt = latest_trend.get("buy", 0) + latest_trend.get("strongBuy", 0)
                    hold_cnt = latest_trend.get("hold", 0)
                    sell_cnt = latest_trend.get("sell", 0) + latest_trend.get("strongSell", 0)
                    
                    return {
                        "buy": buy_cnt,
                        "hold": hold_cnt,
                        "sell": sell_cnt,
                        "strongBuy": latest_trend.get("strongBuy", 0),
                        "strongSell": latest_trend.get("strongSell", 0),
                        "targetMean": None,
                        "targetHigh": None,
                        "targetLow": None,
                        "history": []
                    }
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_sentiment(pair, target_date, key=STOCKDATA_KEY):
    if not key:
        return None
    symbol = pair.replace("/", "")
    target_dt = pd.to_datetime(target_date)
    date_str = target_dt.strftime("%Y-%m-%d")
    url = f"https://api.stockdata.org/v1/news/all?language=en&symbols={symbol}&published_on={date_str}&api_token={key}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            res = r.json()
            articles = res.get("data", [])
            sentiments = []
            for art in articles:
                entities = art.get("entities", [])
                for ent in entities:
                    if ent.get("symbol") == symbol:
                        sent_score = ent.get("sentiment_score")
                        if sent_score is not None:
                            sentiments.append(float(sent_score))
            if sentiments:
                avg_sent = sum(sentiments) / len(sentiments)
                return avg_sent * 10.0
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def get_fcs_history_data_historical(pair, target_date, key=FCS_KEY):
    if not key:
        return None, False
    try:
        df = fetch_fcs_history_live(pair, key)
        if df is not None and not df.empty:
            target_dt = pd.to_datetime(target_date)
            df_filtered = df[df["date"] <= target_dt]
            if not df_filtered.empty:
                return df_filtered, True
    except Exception:
        pass
    return None, False

@st.cache_data(ttl=86400, show_spinner=False)
def get_historical_correlation_matrix(target_date):
    target_dt = pd.to_datetime(target_date)
    start_dt = target_dt - timedelta(days=30)
    
    limit_dt = datetime(2025, 12, 31)
    if start_dt > limit_dt:
        start_dt = limit_dt - timedelta(days=30)
        target_dt = limit_dt
        
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = target_dt.strftime("%Y-%m-%d")
    
    url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
    params = {
        "start_date": start_str,
        "end_date": end_str,
        "base": "USD",
        "symbols": "EUR,GBP,JPY,CHF,CAD,AUD,NZD"
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and "data" in data:
                rates_dict = data["data"].get("rates", {})
                
                daily_rates = []
                for date_str, val_dict in rates_dict.items():
                    row = {"date": pd.to_datetime(date_str)}
                    for sym, val in val_dict.items():
                        if val is not None:
                            row[sym] = float(val)
                    daily_rates.append(row)
                    
                df_raw = pd.DataFrame(daily_rates).sort_values("date").reset_index(drop=True)
                
                required = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]
                if not df_raw.empty and all(col in df_raw.columns for col in required):
                    df_raw = df_raw.dropna(subset=required)
                    
                    if len(df_raw) >= 5:
                        df_pairs = pd.DataFrame()
                        df_pairs["EUR/USD"] = 1.0 / df_raw["EUR"]
                        df_pairs["GBP/USD"] = 1.0 / df_raw["GBP"]
                        df_pairs["USD/JPY"] = df_raw["JPY"]
                        df_pairs["USD/CHF"] = df_raw["CHF"]
                        df_pairs["AUD/USD"] = 1.0 / df_raw["AUD"]
                        df_pairs["USD/CAD"] = df_raw["CAD"]
                        df_pairs["NZD/USD"] = 1.0 / df_raw["NZD"]
                        df_pairs["EUR/GBP"] = df_raw["GBP"] / df_raw["EUR"]
                        
                        corr = df_pairs.corr(method="pearson")
                        return corr, True
    except Exception:
        pass
    return generate_mock_fcs_correlation(), False



def get_historical_imf_value(curr, indicator, target_date):
    try:
        target_year = pd.to_datetime(target_date).year
        mapping = {
            "USD": ["USA"],
            "EUR": ["EUR", "EMU", "U2", "DEU"],
            "GBP": ["GBR"],
            "CHF": ["CHE"],
            "CAD": ["CAN"],
            "AUD": ["AUS"],
            "NZD": ["NZL"],
            "JPY": ["JPN"]
        }
        candidates = mapping.get(curr, [curr])
        data = get_imf_data(indicator)
        if not data:
            return None
        indicator_data = data.get("values", {}).get(indicator, {})
        for code in candidates:
            values_dict = indicator_data.get(code, {})
            if values_dict:
                years = [int(yr) for yr in values_dict.keys() if yr.isdigit()]
                if years:
                    valid_years = [y for y in years if y <= target_year]
                    if not valid_years:
                        best_year = min(years)
                    else:
                        best_year = max(valid_years)
                    val = values_dict[str(best_year)]
                    if val is not None:
                        return float(val)
    except Exception:
        pass
    return None

def get_yield_spread(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("T10Y2Y", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val / 2.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_ciss_index(target_date=None):
    try:
        if target_date is None:
            url = "https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SS_CI.IDX?lastNObservations=5&format=jsondata"
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            url = f"https://data-api.ecb.europa.eu/service/data/CISS/D.U2.Z0Z.4F.EC.SS_CI.IDX?endPeriod={dt_str}&lastNObservations=5&format=jsondata"
            
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
        if r.status_code == 200:
            res = r.json()
            series = res["dataSets"][0]["series"]
            if series:
                series_key = list(series.keys())[0]
                obs = series[series_key]["observations"]
                sorted_keys = sorted(obs.keys(), key=int)
                if sorted_keys:
                    latest_val = float(obs[sorted_keys[-1]][0])
                    if latest_val is None:
                        return 0.0
                    return float(np.clip((0.2 - latest_val) / 0.3 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0
    return 0.0

def get_house_price_index(target_date=None):
    try:
        url = "https://landregistry.data.gov.uk/landregistry/query"
        query = """
        prefix ukhpi: <http://landregistry.data.gov.uk/def/ukhpi/>
        select ?date ?hpi where {
          ?item ukhpi:refRegion <http://landregistry.data.gov.uk/id/region/united-kingdom> ;
                ukhpi:refMonth ?date ;
                ukhpi:housePriceIndex ?hpi .
        } order by desc(?date) limit 6
        """
        r = requests.post(url, data={"query": query}, headers={"Accept": "application/sparql-results+json"}, timeout=8)
        if r.status_code == 200:
            res = r.json()
            bindings = res.get("results", {}).get("bindings", [])
            data = []
            for b in bindings:
                d_str = b.get("date", {}).get("value")
                h_val = float(b.get("hpi", {}).get("value"))
                data.append({"date": d_str, "hpi": h_val})
            
            if data:
                data = sorted(data, key=lambda x: x["date"])
                if target_date is not None:
                    target_month = pd.to_datetime(target_date).strftime("%Y-%m")
                    valid_data = [x for x in data if x["date"] <= target_month]
                else:
                    valid_data = data
                    
                if len(valid_data) >= 2:
                    latest_hpi = valid_data[-1]["hpi"]
                    prev_hpi = valid_data[-2]["hpi"]
                    if latest_hpi is None or prev_hpi is None or prev_hpi == 0:
                        return 0.0
                    growth = (latest_hpi - prev_hpi) / prev_hpi * 100.0
                    return float(np.clip(growth / 0.5 * 2.0, -2.0, 2.0))
    except Exception:
        return 0.0
    return 0.0

def get_china_pmi_fred(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("CVPMA", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val - 50.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_oil_price(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        dt_3m_ago = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_now, _, _ = get_fred_data_historical("DCOILWTICO", dt_str, fred_key)
        val_3m, _, _ = get_fred_data_historical("DCOILWTICO", dt_3m_ago, fred_key)
        
        if val_now is None or val_3m is None or val_3m == 0:
            return 0.0
        chg = (val_now - val_3m) / val_3m * 100.0
        return float(np.clip(chg / 10.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

def get_milk_price(target_date=None):
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        dt_3m_ago = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_now, _, _ = get_fred_data_historical("PRAWINDEXM", dt_str, fred_key)
        val_3m, _, _ = get_fred_data_historical("PRAWINDEXM", dt_3m_ago, fred_key)
        
        if val_now is None or val_3m is None or val_3m == 0:
            return 0.0
        chg = (val_now - val_3m) / val_3m * 100.0
        return float(np.clip(chg / 10.0 * 3.0, -3.0, 3.0))
    except Exception:
        return 0.0

def get_trade_balance(target_date=None):
    try:
        if ESTAT_APP_ID:
            try:
                url = f"https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId={ESTAT_APP_ID}&statsDataId=0003444800&limit=10"
                r = requests.get(url, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    values = data.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])
                    if values:
                        latest_val = float(values[-1].get("$"))
                        if latest_val is not None:
                            return float(np.clip(latest_val / 1e12 * 5.0, -5.0, 5.0))
            except Exception:
                pass
                
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        val, _, _ = get_fred_data_historical("XTNTVA01JPM667S", dt_str, fred_key)
        if val is None:
            return 0.0
        return float(np.clip(val / 5000.0 * 5.0, -5.0, 5.0))
    except Exception:
        return 0.0

YIELD_SERIES = {
    "USD": "DGS2",
    "EUR": "IRLTLT01EZM156N",
    "GBP": "IRLTLT01GBM156N",
    "JPY": "IRLTLT01JPM156N",
    "CHF": "IRLTLT01CHM156N",
    "CAD": "IRLTLT01CAM156N",
    "AUD": "IRLTLT01AUM156N",
    "NZD": "IRLTLT01NZM156N"
}

YIELD_2Y_SERIES = {
    "USD": "DGS2",
    "EUR": "DE2Y.GBOND",
    "GBP": "UK2Y.GBOND",
    "JPY": "JP2Y.GBOND",
    "CHF": "SW2Y.GBOND",
    "CAD": "CA2Y.GBOND",
    "AUD": "AU2Y.GBOND",
    "NZD": "NZ2Y.GBOND"
}

YIELD_5Y_SERIES = {
    "USD": "DGS5",
    "EUR": "DE5Y.GBOND",
    "GBP": "UK5Y.GBOND",
    "JPY": "JP5Y.GBOND",
    "CHF": None,
    "CAD": "CA5Y.GBOND",
    "AUD": "AU5Y.GBOND",
    "NZD": "NZ5Y.GBOND"
}

YIELD_10Y_SERIES = {
    "USD": "DGS10",
    "EUR": "IRLTLT01EZM156N",
    "GBP": "IRLTLT01GBM156N",
    "JPY": "IRLTLT01JPM156N",
    "CHF": "IRLTLT01CHM156N",
    "CAD": "IRLTLT01CAM156N",
    "AUD": "IRLTLT01AUM156N",
    "NZD": "IRLTLT01NZM156N"
}

CPI_SERIES = {
    "USD": "CPIAUCNS",
    "EUR": "CP0000EZ19M086NEST",
    "GBP": "GBRCPIALLMINMEI",
    "JPY": "JPNCPIALLMINMEI",
    "CHF": "CP0000CHM086NEST",
    "CAD": "CPALTT01CAM657N",
    "AUD": "AUSCPIALLQINMEI",
    "NZD": "NZLCPIALLQINMEI"
}

OECD_INFLATION_EXP_SERIES = {
    "USD": "CSINFT02USM460S",
    "EUR": "CSINFT02EZM460S",
    "GBP": None,
    "JPY": None,
    "CHF": None,
    "CAD": None,
    "AUD": "CSINFT02AUM460S",
    "NZD": None
}

UNEMP_SERIES = {
    "USD": "UNRATE",
    "EUR": "LRUNTTTTEZM156S",
    "GBP": "LRUNTTTTGBM156S",
    "JPY": "LRUNTTTTJPM156S",
    "CHF": "LRUNTTTTCHM156S",
    "CAD": "LRUNTTTTCAM156S",
    "AUD": "LRUNTTTTAUM156S",
    "NZD": "LRUNTTTTNZM156S"
}

def get_unemp_rate_value(curr, target_date=None):
    """Use the published live observation; retain the historical reader for past dates."""
    if use_live_core_cache(target_date):
        return get_macro_observation_details(curr, "Arbeitsmarkt", target_date).get("value")
    try:
        series_id = UNEMP_SERIES.get(curr)
        if not series_id:
            return None
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        val, _, _ = get_fred_data_historical(series_id, target_date, FRED_KEY)
        if val is not None:
            return float(val)
    except Exception:
        pass
    return None


def get_live_labour_display_row(curr, info, target_date=None):
    observation = get_macro_observation_details(curr, "Arbeitsmarkt", target_date)
    value = finite_number(observation.get("value"))
    return {
        "Währung": f"{info['flag']} {curr}",
        "Arbeitslosenquote": f"{value:.2f}%" if value is not None else "N/A",
        "Quelle": observation.get("source") or "Nicht verfügbar",
        "Referenzperiode": observation.get("reference_period") or observation.get("date") or "Unbekannt",
        "Messzeitraum": observation.get("period_label") or observation.get("frequency") or "Unbekannt",
        "Aktualität": observation.get("freshness") or "UNAVAILABLE",
        "Status": "Nicht verfügbar" if value is None else "🟢 Normal" if value < 6.0 else "🟡 Erhöht",
    }


GDP_SERIES = {
    "USD": "GDPC1",
    "EUR": "CLVMEURSCAB1GQEZ",
    "GBP": "UKNGDPM",
    "JPY": "JPNGDPRQPSMEI",
    "CHF": "CHEGDPRQPSMEI",
    "CAD": "CANGDPRQPSMEI",
    "AUD": "AUSGDPRQPSMEI",
    "NZD": "NZLGDPRQPSMEI"
}

PMI_SERIES = {
    "USD": "MANEMP",
    "EUR": "BSPRTE01EZM661S",
    "GBP": "BSPRTE01GBM661S",
    "JPY": "BSPRTE01JPM661S",
    "CHF": "BSPRTE01CHM661S",
    "CAD": "BSPRTE01CAM661S",
    "AUD": "BSPRTE01AUM661S",
    "NZD": "BSPRTE01NZM661S"
}

def get_vix_value(target_date=None):
    """Live VIX requires a centrally validated index observation; history is unchanged."""
    if use_live_core_cache(target_date):
        # No VIX index is currently collected. An ETF price or constant cannot
        # certify today's volatility regime.
        return None
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
        
    try:
        val, _, _ = get_fred_data_historical("VIXCLS", target_date, FRED_KEY)
        if val is not None and val > 0:
            return float(val)
    except Exception:
        pass

    try:
        df_vix, _, _ = get_fred_data("VIXCLS", FRED_KEY)
        if df_vix is not None and not df_vix.empty:
            val = float(df_vix.iloc[-1]["value"])
            if val > 0:
                return val
    except Exception:
        pass

    try:
        apifreaks_data = get_apifreaks_prices(APIFREAKS_KEY)
        if apifreaks_data:
            rates = apifreaks_data.get("rates", {})
            vix_str = rates.get("VIX")
            if vix_str is not None:
                return float(vix_str)
    except Exception:
        pass

    try:
        if TIINGO_KEY:
            tiingo_res = get_tiingo_prices("VIXY", TIINGO_KEY)
            if tiingo_res and tiingo_res.get("close") is not None:
                return float(tiingo_res.get("close"))
    except Exception:
        pass

    return 15.0

def explain_currency_score_bullets(curr: str, target_date=None) -> list:
    bullets = []
    try:
        details = compute_currency_details(curr, target_date)
        gp = details.get("Geldpolitik", 0)
        inf = details.get("Inflation", 0)
        lab = details.get("Arbeitsmarkt", 0)
        pmi = details.get("PMI", 0)
        gdp = details.get("GDP", 0)
        
        if gp is not None and gp > 15:
            bullets.append("+ Hohe Renditen & steigende Zinserwartungen")
        elif gp is not None and gp < -15:
            bullets.append("- Niedrige Renditen & sinkende Zinserwartungen")
            
        if inf is not None and inf > 15:
            bullets.append("+ Erhöhter Inflationsdruck über dem Target")
        elif inf is not None and inf < -15:
            bullets.append("- Niedriger Inflationsdruck")
            
        if lab is not None and lab > 15:
            bullets.append("+ Starker & robuster Arbeitsmarkt")
        elif lab is not None and lab < -15:
            bullets.append("- Schwacher Arbeitsmarkt")
            
        if pmi is not None and pmi > 15:
            bullets.append("+ PMI-Frühindikatoren signalisieren Expansion")
        elif pmi is not None and pmi < -15:
            bullets.append("- PMI-Frühindikatoren signalisieren Kontraktion")
            
        if gdp is not None and gdp > 15:
            bullets.append("+ Robustes Wirtschaftswachstum (GDP)")
        elif gdp is not None and gdp < -15:
            bullets.append("- Schwaches Wirtschaftswachstum (GDP)")
    except Exception:
        pass
    if not bullets:
        bullets.append("⚪ Keine belastbare fundamentale Tendenz aus den verfügbaren Faktoren")
    return bullets

@st.cache_data(ttl=1800, show_spinner=False)
def get_current_official_cpi(curr):
    try:
        if curr == "CHF":
            from official_hicp import fetch_swiss_hicp
            return fetch_swiss_hicp(session=requests)
        if curr == "EUR":
            from official_hicp import fetch_hicp
            return fetch_hicp(session=requests, geo="EA21")
        if curr in ("JPY", "AUD"):
            from official_inflation import fetch_official_cpi
            diagnostics = {}
            result = fetch_official_cpi(curr, client=requests, estat_key=ESTAT_APP_ID, diagnostics=diagnostics)
            usage = getattr(requests, "usage", None)
            if isinstance(usage, dict):
                host = "api.e-stat.go.jp" if curr == "JPY" else "data.api.abs.gov.au"
                item = usage.setdefault(host, {"requests_this_run": 0, "status": "NOT_REQUESTED",
                    "remaining": None, "limit": None, "reset_at": None, "budget_evidence": "unknown"})
                item["data_status"] = diagnostics.get("code", "UNKNOWN")
                item["provider_response_status"] = diagnostics.get("provider_status")
            return result
    except requests.exceptions.JSONDecodeError:
        validation = "UNVERIFIED"
    except requests.exceptions.RequestException:
        validation = "SOURCE_UNAVAILABLE"
    except Exception:
        validation = "UNVERIFIED"
    else:
        return None
    return {"value": None, "date": None,
            "source": {"CHF": "BFS", "EUR": "Eurostat", "JPY": "Statistics Japan e-Stat", "AUD": "ABS"}.get(curr, "Amtliche Quelle"),
            "series_id": None, "_validation": validation,
            "_reason": "Amtlicher Inflations-Datenvertrag nicht bestätigt" if validation == "UNVERIFIED" else "Amtliche Inflationsquelle vorübergehend nicht erreichbar"}


def get_cpi_yoy_details(curr: str, target_date=None):
    if use_live_core_cache(target_date):
        detail = live_data.details(curr)
        observation = detail["_observations"].get("Inflation", {})
        return (observation.get("value") if detail.get("Inflation") is not None else None,
                observation.get("date"), "HICP_YOY" if curr in ("EUR", "CHF") else "CPI_YOY", observation.get("source", "UNAVAILABLE"),
                observation.get("series_id"), detail["_freshness"].get("Inflation", "UNAVAILABLE"))
    if curr in ("EUR", "CHF", "JPY", "AUD") and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        observation = get_current_official_cpi(curr)
        if not observation:
            return None, None, "CPI_YOY", "UNAVAILABLE", None, "UNAVAILABLE"
        status = observation_freshness(observation["date"], datetime.now(), 45, 90, monthly=True)
        return (observation["value"] if status in ("FRESH", "AGING") else None,
                observation["date"], "HICP_YOY" if curr in ("EUR", "CHF") else "CPI_YOY",
                observation["source"], observation["series_id"], status)
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        series_id = CPI_SERIES.get(curr)
        metric_type = "CPI_YOY"
        source = "FRED"
        
        # Switzerland uses Eurostat HICP CP0000CHM086NEST
        if curr == "CHF":
            series_id = "CP0000CHM086NEST"
            metric_type = "HICP_YOY"
            source = "Eurostat HICP"
            
        # GBP uses ONS API (D7G7 series)
        if curr == "GBP":
            metric_type = "CPI_YOY"
            source = "ONS"
            series_id = "D7G7"
            
            res_ons = get_ons_cpi_data()
            if res_ons is not None:
                df, _, is_live = res_ons
                if df is not None and not df.empty:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[(df["date"] <= target_dt) & (df["release_date"] <= target_dt)].sort_values("date")
                    if not df_filtered.empty:
                        obs_row = df_filtered.iloc[-1]
                        obs_date = obs_row["date"]
                        val = obs_row["value"]
                        
                        ref_end = obs_date + pd.offsets.MonthEnd(0)
                        days_diff = max(0, (target_dt - ref_end).days)
                        threshold = 90
                        if days_diff <= threshold / 2:
                            freshness = "🟢 FRESH"
                        elif days_diff <= threshold:
                            freshness = "🟡 AGING"
                        else:
                            freshness = "🔴 STALE"
                            
                        is_ltd = obs_row.get("is_pit_limited", False)
                        actual_source = "ONS (PIT_LIMITED)" if is_ltd else "ONS"
                        actual_freshness = f"{freshness} (PIT_LIMITED)" if is_ltd else freshness
                        
                        if days_diff > threshold:
                            return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                            
                        if val is not None and pd.notna(val):
                            if abs(val) <= 25.0:
                                return float(val), obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                            else:
                                return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, "🔴 DATA VALIDATION FAILED"
                        else:
                            return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
            return None, None, metric_type, "ONS", series_id, "🔴 UNAVAILABLE"
            
        # CAD uses Statistics Canada API
        if curr == "CAD":
            metric_type = "CPI_YOY"
            source = "Statistics Canada"
            series_id = "v41690973"
            
            res_statcan = get_statcan_cpi_data()
            if res_statcan is not None:
                df, _, is_live = res_statcan
                if df is not None and not df.empty:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[(df["date"] <= target_dt) & (df["release_date"] <= target_dt)].sort_values("date")
                    if not df_filtered.empty:
                        obs_row = df_filtered.iloc[-1]
                        obs_date = obs_row["date"]
                        
                        # Find row 12 months ago to calculate YoY
                        target_prev = obs_date - pd.DateOffset(months=12)
                        prev_rows = df_filtered[df_filtered["date"] == target_prev]
                        if not prev_rows.empty:
                            prev_row = prev_rows.iloc[0]
                            val = float((obs_row["value"] / prev_row["value"] - 1) * 100)
                            
                            ref_end = obs_date + pd.offsets.MonthEnd(0)
                            days_diff = max(0, (target_dt - ref_end).days)
                            threshold = 90
                            if days_diff <= threshold / 2:
                                freshness = "🟢 FRESH"
                            elif days_diff <= threshold:
                                freshness = "🟡 AGING"
                            else:
                                freshness = "🔴 STALE"
                                
                            is_ltd = obs_row.get("is_pit_limited", False)
                            actual_source = "Statistics Canada (PIT_LIMITED)" if is_ltd else "Statistics Canada"
                            actual_freshness = f"{freshness} (PIT_LIMITED)" if is_ltd else freshness
                            
                            if days_diff <= threshold:
                                if val is not None and pd.notna(val):
                                    if abs(val) <= 25.0:
                                        return val, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                                    else:
                                        return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, "🔴 DATA VALIDATION FAILED"
                                else:
                                    return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness

            # If StatCan failed or is empty, only allow historical fallback if target date is > 90 days ago
            target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
            is_historical = (datetime.now() - target_dt).days > 90
            if is_historical:
                series_id = "CPALTT01CAM657N"
                source = "FRED"
            else:
                return None, None, metric_type, "Statistics Canada", series_id, "🔴 UNAVAILABLE"

        # AUD uses ABS API
        if curr == "AUD":
            metric_type = "CPI_YOY"
            source = "ABS"
            series_id = "3.10001.10.50.M"
            
            res_abs = get_abs_cpi_data()
            if res_abs is not None:
                df, _, is_live = res_abs
                if df is not None and not df.empty:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[(df["date"] <= target_dt) & (df["release_date"] <= target_dt)].sort_values("date")
                    if not df_filtered.empty:
                        obs_row = df_filtered.iloc[-1]
                        obs_date = obs_row["date"]
                        val = obs_row["value"]
                        
                        ref_end = obs_date + pd.offsets.MonthEnd(0)
                        days_diff = max(0, (target_dt - ref_end).days)
                        threshold = 90
                        if days_diff <= threshold / 2:
                            freshness = "🟢 FRESH"
                        elif days_diff <= threshold:
                            freshness = "🟡 AGING"
                        else:
                            freshness = "🔴 STALE"
                            
                        is_ltd = obs_row.get("is_pit_limited", False)
                        actual_source = "ABS (PIT_LIMITED)" if is_ltd else "ABS"
                        actual_freshness = f"{freshness} (PIT_LIMITED)" if is_ltd else freshness
                        
                        if days_diff <= threshold:
                            if val is not None and pd.notna(val):
                                if abs(val) <= 25.0:
                                    return float(val), obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                                else:
                                    return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, "🔴 DATA VALIDATION FAILED"
                            else:
                                return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
            # If ABS failed or is empty, only allow historical fallback if target date is > 90 days ago
            target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
            is_historical = (datetime.now() - target_dt).days > 90
            if is_historical:
                series_id = "AUSCPIALLQINMEI"
                source = "FRED"
            else:
                return None, None, metric_type, "ABS", series_id, "🔴 UNAVAILABLE"

        # JPY uses Statistics Bureau of Japan e-Stat API
        if curr == "JPY":
            metric_type = "CPI_YOY"
            source = "e-Stat"
            series_id = "0001"
            
            res_estat = get_estat_cpi_data()
            if res_estat is not None:
                df, _, is_live = res_estat
                if df is not None and not df.empty:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[(df["date"] <= target_dt) & (df["release_date"] <= target_dt)].sort_values("date")
                    if not df_filtered.empty:
                        obs_row = df_filtered.iloc[-1]
                        obs_date = obs_row["date"]
                        val = obs_row["value"]
                        
                        ref_end = obs_date + pd.offsets.MonthEnd(0)
                        days_diff = max(0, (target_dt - ref_end).days)
                        threshold = 90
                        if days_diff <= threshold / 2:
                            freshness = "🟢 FRESH"
                        elif days_diff <= threshold:
                            freshness = "🟡 AGING"
                        else:
                            freshness = "🔴 STALE"
                            
                        is_ltd = obs_row.get("is_pit_limited", True)
                        actual_source = "e-Stat (PIT_LIMITED)" if is_ltd else "e-Stat"
                        actual_freshness = f"{freshness} (PIT_LIMITED)" if is_ltd else freshness
                        
                        if days_diff <= threshold:
                            if val is not None and pd.notna(val):
                                if abs(val) <= 25.0:
                                    return float(val), obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                                else:
                                    return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, "🔴 DATA VALIDATION FAILED"
                            else:
                                return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness

            # If e-Stat failed or is empty, only allow historical fallback if target date is > 90 days ago
            target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
            is_historical = (datetime.now() - target_dt).days > 90
            if is_historical:
                series_id = "JPNCPIALLMINMEI"
                source = "FRED"
            else:
                return None, None, metric_type, "e-Stat", series_id, "🔴 UNAVAILABLE"

        # NZD uses Stats NZ (Tatauranga Aotearoa) API
        if curr == "NZD":
            metric_type = "CPI_YOY"
            source = "Stats NZ"
            series_id = "CPIQ.SE9A"
            
            res_statsnz = get_statsnz_cpi_data()
            if res_statsnz is not None:
                df, _, is_live = res_statsnz
                if df is not None and not df.empty:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[(df["date"] <= target_dt) & (df["release_date"] <= target_dt)].sort_values("date")
                    if not df_filtered.empty:
                        obs_row = df_filtered.iloc[-1]
                        obs_date = obs_row["date"]
                        val = obs_row["value"]
                        
                        ref_end = obs_date + pd.offsets.QuarterEnd(0)
                        days_diff = max(0, (target_dt - ref_end).days)
                        threshold = 180
                        if days_diff <= threshold / 2:
                            freshness = "🟢 FRESH"
                        elif days_diff <= threshold:
                            freshness = "🟡 AGING"
                        else:
                            freshness = "🔴 STALE"
                            
                        is_ltd = obs_row.get("is_pit_limited", True)
                        actual_source = "Stats NZ (PIT_LIMITED)" if is_ltd else "Stats NZ"
                        actual_freshness = f"{freshness} (PIT_LIMITED)" if is_ltd else freshness
                        
                        if days_diff <= threshold:
                            if val is not None and pd.notna(val):
                                if abs(val) <= 25.0:
                                    return float(val), obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness
                                else:
                                    return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, "🔴 DATA VALIDATION FAILED"
                            else:
                                return None, obs_date.strftime("%Y-%m-%d"), metric_type, actual_source, series_id, actual_freshness

            # If Stats NZ failed or is empty, only allow historical fallback if target date is > 180 days ago
            target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
            is_historical = (datetime.now() - target_dt).days > 180
            if is_historical:
                series_id = "NZLCPIALLMINMEI"
                source = "FRED"
            else:
                return None, None, metric_type, "Stats NZ", series_id, "🔴 UNAVAILABLE"
            
        if series_id and fred_key:
            units_param = "pc1" if curr == "USD" else None
            df, _, is_live = get_fred_data(series_id, fred_key, units=units_param)
            if df is not None and not df.empty:
                if not is_live and not check_demo_active():
                    pass
                else:
                    target_dt = (pd.Timestamp.now(tz="UTC").tz_localize(None) if dt_str == datetime.now().strftime("%Y-%m-%d") else pd.to_datetime(dt_str))
                    df_filtered = df[df["date"] <= target_dt].sort_values("date")
                    
                    if not df_filtered.empty:
                        obs_date = df_filtered.iloc[-1]["date"]
                        is_quarterly = (curr in ["AUD", "NZD"] or series_id.endswith("Q") or "Q" in series_id)
                        threshold = 180 if is_quarterly else 90
                        
                        ref_end = obs_date if is_quarterly else (obs_date + pd.offsets.MonthEnd(0))
                        days_diff = max(0, (target_dt - ref_end).days)
                        
                        # Determine freshness
                        if days_diff <= threshold / 2:
                            freshness = "🟢 FRESH"
                        elif days_diff <= threshold:
                            freshness = "🟡 AGING"
                        else:
                            freshness = "🔴 STALE"
                            
                        # If stale, do NOT return value
                        if days_diff > threshold:
                            return None, obs_date.strftime("%Y-%m-%d"), metric_type, source, series_id, freshness
                            
                        val = None
                        if curr == "USD":
                            raw_pc1 = df_filtered.iloc[-1]["value"]
                            val = round(float(raw_pc1), 1)
                            source = "FRED / BLS"
                        elif series_id in ["CPALTT01CHM657N", "CPALTT01CAM657N"]:
                            # Compounding last 12 MoM rates
                            if len(df_filtered) >= 12:
                                last_12 = df_filtered.tail(12)["value"].astype(float).values
                                val = (np.prod(1.0 + last_12 / 100.0) - 1.0) * 100.0
                        else:
                            periods_offset = 4 if is_quarterly else 12
                            df_c = df.copy()
                            df_c["yoy"] = df_c["value"].pct_change(periods=periods_offset) * 100.0
                            df_f_yoy = df_c[df_c["date"] <= target_dt].sort_values("date")
                            val = df_f_yoy.iloc[-1]["yoy"] if not df_f_yoy.empty else None
                            
                        if val is not None and pd.notna(val):
                            if abs(val) <= 25.0:
                                return float(val), obs_date.strftime("%Y-%m-%d"), metric_type, source, series_id, freshness
                            else:
                                return None, obs_date.strftime("%Y-%m-%d"), metric_type, source, series_id, "🔴 DATA VALIDATION FAILED"
                        else:
                            return None, obs_date.strftime("%Y-%m-%d"), metric_type, source, series_id, freshness
    except Exception:
        pass
        
    try:
        # Fallback to World Bank only for historical research (>365 days ago)
        if target_date is not None:
            target_dt = pd.to_datetime(target_date)
            is_historical = (datetime.now() - target_dt).days > 365
            if is_historical:
                code = CURRENCIES[curr]["wb_code"]
                val, act_dt, is_live = get_worldbank_data_historical(code, "FP.CPI.TOTL.ZG", target_date)
                if val is not None:
                    if is_live or check_demo_active():
                        days_diff = (target_dt - pd.to_datetime(act_dt)).days
                        if days_diff <= 365 * 2:
                            if abs(val) <= 25.0:
                                return float(val), pd.to_datetime(act_dt).strftime("%Y-%m-%d"), "HISTORICAL_ANNUAL", "World Bank (Historical)", "FP.CPI.TOTL.ZG", "🟢 HISTORICAL"
    except Exception:
        pass
        
    if check_demo_active():
        return 2.0, datetime.now().strftime("%Y-%m-%d"), "CPI_YOY", "Demo", "DEMO", "🟢 FRESH"
        
    return None, None, "CPI_YOY", "UNAVAILABLE", "NONE", "🔴 UNAVAILABLE"

def get_cpi_yoy_value(curr: str, target_date=None):
    val, _, _, _, _, _ = get_cpi_yoy_details(curr, target_date)
    return val

def finite_number(value):
    """Return a finite numeric value, preserving unavailable data as None."""
    try:
        if isinstance(value, (bool, np.bool_)):
            return None
        number = float(value)
        return number if np.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalized_freshness(status):
    """Read freshness independently of decorative badges and PIT annotations."""
    text = str(status or "").upper()
    for flag in ("UNAVAILABLE", "FAILED", "STALE", "AGING", "FRESH"):
        if flag in text:
            return flag
    return "UNAVAILABLE"


def observation_freshness(reference, target_date, fresh_days, max_days, monthly=False):
    """Missing/future references cannot certify current data.

    Monthly period labels use period end for age, without allowing a future
    reference month. PMI uses the existing monthly labour limits: 45/90 days.
    These are data-eligibility limits, not changes to CORE scoring weights.
    """
    try:
        if reference is None or isinstance(reference, (int, float)):
            return "UNAVAILABLE"
        if isinstance(reference, str) and not reference.strip():
            return "UNAVAILABLE"
        if isinstance(reference, str):
            parsed = None
            for fmt in ("%b/%y", "%b/%Y", "%b %Y", "%Y-%m", "%m/%Y"):
                try:
                    parsed = datetime.strptime(reference.strip(), fmt)
                    break
                except ValueError:
                    continue
            reference = parsed if parsed is not None else reference
        observed = pd.to_datetime(reference)
        target = pd.to_datetime(target_date)
        if pd.isna(observed) or observed > target:
            return "UNAVAILABLE"
        age_reference = observed + pd.offsets.MonthEnd(0) if monthly else observed
        age = max(0, (target - age_reference).days)
        return "FRESH" if age <= fresh_days else "AGING" if age <= max_days else "STALE"
    except (TypeError, ValueError, OverflowError):
        return "UNAVAILABLE"


def get_macro_observation_details(curr, category, target_date=None):
    """Keep a labour/GDP value attached to the source and date used to judge it.

    Annual World Bank observations remain historical research only. They cannot
    stand in for the current monthly labour or quarterly GDP observation.
    """
    if use_live_core_cache(target_date):
        detail = live_data.details(curr)
        observation = dict(detail["_observations"].get(category, {}))
        observation["value"] = observation.get("value") if detail.get(category) is not None else None
        observation["freshness"] = detail["_freshness"].get(category, "UNAVAILABLE")
        observation.setdefault("date", None)
        observation.setdefault("source", "UNAVAILABLE")
        return observation
    target_dt = pd.to_datetime(target_date) if target_date is not None else pd.Timestamp(datetime.now().date())
    if (curr in ("AUD", "JPY", "CAD") or (curr in ("USD", "NZD") and category == "GDP")) and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        from official_macro import fetch_abs_observation, fetch_statcan_labour, fetch_statcan_gdp, fetch_japan_gdp, fetch_bea_gdp, fetch_nz_gdp
        from official_quarterly_labour import fetch_japan_labour
        validation = "SOURCE_UNAVAILABLE"
        try:
            result = (fetch_abs_observation(category, session=requests) if curr == "AUD"
                      else (fetch_statcan_labour if category == "Arbeitsmarkt" else fetch_statcan_gdp)(session=requests) if curr == "CAD"
                      else fetch_bea_gdp(session=requests) if curr == "USD"
                      else fetch_nz_gdp(session=requests) if curr == "NZD"
                      else fetch_japan_gdp(session=requests) if category == "GDP"
                      else fetch_japan_labour(session=requests))
            if result:
                result["freshness"] = observation_freshness(result["date"], target_dt,
                    45 if category == "Arbeitsmarkt" else 120,
                    90 if category == "Arbeitsmarkt" else 180, monthly=category == "Arbeitsmarkt")
                if result["freshness"] not in ("FRESH", "AGING"):
                    result["value"] = None
                return result
        except requests.exceptions.JSONDecodeError:
            validation = "UNVERIFIED"
        except requests.exceptions.RequestException:
            pass
        except Exception:
            validation = "UNVERIFIED"
        return {"value": None, "date": None, "source": {"NZD": "Stats NZ GDP expenditure", "USD": "BEA", "AUD": "ABS", "CAD": "Statistics Canada", "JPY": "Cabinet Office ESRI" if category == "GDP" else "Statistics Bureau of Japan"}[curr],
                "series_id": None, "frequency": "monthly" if category == "Arbeitsmarkt" else "quarterly",
                "freshness": "UNAVAILABLE", "_validation": validation,
                "_reason": "Amtlicher Datenvertrag oder Veröffentlichungsstand nicht bestätigt" if validation == "UNVERIFIED" else "Amtliche Quelle vorübergehend nicht erreichbar"}
    if curr == "GBP" and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        from official_ons import fetch_ons_gdp, fetch_ons_labour
        try:
            result = (fetch_ons_gdp if category == "GDP" else fetch_ons_labour)(session=requests)
            if result:
                result["freshness"] = observation_freshness(result["date"], target_dt, 45 if category == "Arbeitsmarkt" else 120,
                    90 if category == "Arbeitsmarkt" else 180, monthly=category == "Arbeitsmarkt")
                if category == "Arbeitsmarkt":
                    result["period_label"] = "Rollierende 3-Monats-Quote"
                if result["freshness"] not in ("FRESH", "AGING"):
                    result["value"] = None
                return result
        except Exception:
            pass
        return {"value": None, "date": None, "source": "ONS", "series_id": None,
                "frequency": "quarterly" if category == "GDP" else "rolling_three_month_monthly_release", "freshness": "UNAVAILABLE"}
    if (curr == "EUR" or (curr == "CHF" and category == "GDP")) and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        from official_macro import fetch_eurostat_observation
        validation = "SOURCE_UNAVAILABLE"
        try:
            result = fetch_eurostat_observation(category, geo="CH" if curr == "CHF" else "EA21", session=requests)
            if result:
                result["freshness"] = observation_freshness(result["date"], target_dt, 45 if category == "Arbeitsmarkt" else 120,
                    90 if category == "Arbeitsmarkt" else 180, monthly=category == "Arbeitsmarkt")
                if result["freshness"] not in ("FRESH", "AGING"):
                    result["value"] = None
                return result
        except requests.exceptions.JSONDecodeError:
            validation = "UNVERIFIED"
        except requests.exceptions.RequestException:
            pass
        except Exception:
            validation = "UNVERIFIED"
        return {"value": None, "date": None, "source": "Eurostat", "series_id": None,
                "frequency": "monthly" if category == "Arbeitsmarkt" else "quarterly", "freshness": "UNAVAILABLE", "_validation": validation,
                "_reason": "Amtlicher Datenvertrag oder Veröffentlichungsstand nicht bestätigt" if validation == "UNVERIFIED" else "Amtliche Quelle vorübergehend nicht erreichbar"}
    if category == "Arbeitsmarkt" and curr in ("CHF", "NZD") and (target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()):
        from official_quarterly_labour import fetch_quarterly_labour
        try:
            result = fetch_quarterly_labour(curr, session=requests)
            if result:
                result["freshness"] = observation_freshness(result["date"], target_dt, 120, 180, monthly=False)
                result["period_label"] = "Saisonbereinigte Quartalsquote"
                if result["freshness"] not in ("FRESH", "AGING"):
                    result["value"] = None
                return result
        except Exception:
            pass
        return {"value": None, "date": None, "source": "UNAVAILABLE", "series_id": None,
                "frequency": "quarterly", "freshness": "UNAVAILABLE"}
    series_id = (UNEMP_SERIES if category == "Arbeitsmarkt" else GDP_SERIES).get(curr)
    result = {"value": None, "date": None, "source": "UNAVAILABLE", "series_id": series_id,
              "freshness": "UNAVAILABLE", "frequency": "monthly" if category == "Arbeitsmarkt" else "quarterly"}
    try:
        df, _, is_live = get_fred_data(series_id, FRED_KEY)
        if df is not None and not df.empty and (is_live or check_demo_active()):
            frame = df.copy()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
            frame = frame[frame["date"] <= target_dt].sort_values("date").drop_duplicates("date", keep="last")
            if not frame.empty:
                observed = frame.iloc[-1]["date"]
                value = frame.iloc[-1]["value"]
                if category == "GDP" and series_id == "GDPC1":
                    # Match the same quarter a year earlier; missing quarters
                    # must not silently turn a four-row change into YoY.
                    previous = frame[frame["date"] == observed - pd.DateOffset(years=1)]
                    prior = finite_number(previous.iloc[-1]["value"]) if not previous.empty else None
                    value = (float(value) / prior - 1.0) * 100.0 if prior not in (None, 0.0) else None
                value = finite_number(value)
                is_live_date = target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()
                age_observed = observed + pd.offsets.QuarterEnd(0) if category == "GDP" and is_live_date else observed
                freshness = observation_freshness(age_observed, target_dt, 45 if category == "Arbeitsmarkt" else 120,
                                                  90 if category == "Arbeitsmarkt" else 180,
                                                  monthly=category == "Arbeitsmarkt")
                valid_range = value is not None and (0 <= value <= 25 if category == "Arbeitsmarkt" else abs(value) <= 25)
                result.update(date=age_observed.strftime("%Y-%m-%d"), source="FRED" if is_live else "Demo",
                              freshness=freshness if valid_range else "FAILED")
                if valid_range and freshness in ("FRESH", "AGING"):
                    result["value"] = value
                return result
    except Exception:
        result["freshness"] = "UNAVAILABLE"

    # Preserve the research fallback, explicitly identified as annual and never
    # eligible for the live CORE. Do not relabel it FRESH from an empty FRED fetch.
    if (pd.Timestamp(datetime.now().date()) - target_dt).days > 365:
        try:
            indicator = "SL.UEM.TOTL.ZS" if category == "Arbeitsmarkt" else "NY.GDP.MKTP.KD.ZG"
            value, observed, is_live = get_worldbank_data_historical(CURRENCIES[curr]["wb_code"], indicator, target_dt)
            value = finite_number(value)
            freshness = observation_freshness(observed, target_dt, 730, 730)
            if value is not None and abs(value) <= 25 and freshness == "FRESH" and (is_live or check_demo_active()):
                result.update(value=value, date=pd.to_datetime(observed).strftime("%Y-%m-%d"),
                              source="World Bank (Historical Annual)", series_id=indicator,
                              freshness="HISTORICAL_ANNUAL", frequency="annual")
        except Exception:
            pass
    return result


def get_unemployment_value(curr: str, target_date=None):
    return get_macro_observation_details(curr, "Arbeitsmarkt", target_date)["value"]


def get_gdp_yoy_value(curr: str, target_date=None):
    return get_macro_observation_details(curr, "GDP", target_date)["value"]


def get_composite_pmi_score(curr: str, target_date=None):
    """Calculates the composite PMI score (Mfg + Svc average) for a given currency."""
    try:
        all_pmi = get_all_pmi_data(FRED_KEY, EODHD_KEY, target_date)
        if all_pmi and curr in all_pmi:
            m_val = all_pmi[curr].get("m_last")
            s_val = all_pmi[curr].get("s_last")
            vals = [float(v) for v in [m_val, s_val] if v is not None]
            if vals:
                comp = sum(vals) / len(vals)
                return float(comp), m_val, s_val, all_pmi[curr].get("m_src", "TE")
    except Exception:
        pass
    return None, None, None, "N/A"

def get_series_trend_points(series_id: str, target_date=None, reverse=False) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            target_dt = datetime.now()
        else:
            target_dt = pd.to_datetime(target_date)
            
        df, _, _ = get_fred_data(series_id, fred_key)
        if df is not None and not df.empty:
            df_filtered = df[df["date"] <= target_dt].sort_values("date")
            if not df_filtered.empty:
                obs_date = df_filtered.iloc[-1]["date"]
                days_diff = (target_dt - obs_date).days
                is_q = ("Q" in series_id or "q" in series_id)
                threshold = 180 if is_q else 90
                if "IRLTLT" in series_id or "TB3MS" in series_id or "FEDFUNDS" in series_id:
                    threshold = 15
                if days_diff > threshold:
                    return 0.0
                    
                if len(df_filtered) >= 3:
                    v1 = float(df_filtered.iloc[-3]["value"])
                    v2 = float(df_filtered.iloc[-2]["value"])
                    v3 = float(df_filtered.iloc[-1]["value"])
                    
                    if v1 < v2 < v3:
                        return -15.0 if reverse else 15.0
                    elif v1 > v2 > v3:
                        return 15.0 if reverse else -15.0
    except Exception:
        pass
    return 0.0

def parse_numeric_calendar_value(val_str):
    if val_str is None:
        return None
    s = str(val_str).strip().upper()
    if s == "" or s == "-" or s == "N/A" or s == "NONE":
        return None
    multiplier = 1.0
    if "K" in s:
        multiplier = 1000.0
        s = s.replace("K", "")
    elif "M" in s:
        multiplier = 1000000.0
        s = s.replace("M", "")
    elif "B" in s:
        multiplier = 1000000000.0
        s = s.replace("B", "")
    elif "T" in s:
        multiplier = 1000000000000.0
        s = s.replace("T", "")
    s = s.replace("%", "").replace("$", "").replace(",", "").strip()
    try:
        return float(s) * multiplier
    except ValueError:
        return None

def get_surprise_points(curr: str, category: str, target_date=None) -> float:
    try:
        keywords = {
            "Geldpolitik": ["interest rate", "rate decision", "fomc", "policy rate", "discount rate"],
            "Inflation": ["cpi", "cpi yoy", "inflation", "consumer price index", "retail sales"],
            "Arbeitsmarkt": ["unemployment", "arbeitslosenquote", "nfp", "non-farm", "nonfarm payrolls", "employment change"],
            "Wachstum": ["pmi", "gdp", "bip", "gdp growth", "manufacturing pmi", "services pmi", "cli"]
        }
        
        kws = keywords.get(category, [])
        if not kws:
            return 0.0
            
        country_map = {
            "USD": ["USA", "US", "UNITED STATES"],
            "EUR": ["DEU", "FRA", "ITA", "ESP", "EMU", "EUROZONE", "EURO AREA"],
            "GBP": ["GBR", "UK", "UNITED KINGDOM"],
            "CHF": ["CHE", "CH", "SWITZERLAND"],
            "CAD": ["CAN", "CA", "CANADA"],
            "AUD": ["AUS", "AU", "AUSTRALIA"],
            "NZD": ["NZL", "NZ", "NEW ZEALAND"],
            "JPY": ["JPN", "JP", "JAPAN"]
        }
        
        allowed_countries = country_map.get(curr, [curr])
        
        global df_cal
        if df_cal is not None and not df_cal.empty:
            df_filtered = df_cal[df_cal["country"].str.upper().isin(allowed_countries)]
            if not df_filtered.empty:
                matches = []
                for idx, row in df_filtered.iterrows():
                    ev_name = str(row["event"]).lower()
                    if any(kw in ev_name for kw in kws):
                        act = parse_numeric_calendar_value(row["actual"])
                        cons = parse_numeric_calendar_value(row["consensus"])
                        if act is not None and cons is not None:
                            matches.append((row["time"], ev_name, act, cons))
                
                if matches:
                    matches = sorted(matches, key=lambda x: x[0], reverse=True)
                    latest_match = matches[0]
                    ev_name = latest_match[1]
                    act = latest_match[2]
                    cons = latest_match[3]
                    
                    surprise = act - cons
                    if "unemployment" in ev_name or "arbeitslosenquote" in ev_name:
                        surprise = cons - act
                        
                    if surprise > 0:
                        return 20.0
                    elif surprise < 0:
                        return -20.0
                    return 0.0
    except Exception:
        pass
        
    # No observed release means no surprise contribution; never fabricate data.
    return 0.0

def detect_market_regime(curr: str, target_date=None) -> str:
    try:
        vix = get_vix_value(target_date)
        if vix is None and use_live_core_cache(target_date):
            return "Unbekannt (VIX nicht geprüft)"
        if vix > 22.0:
            return "Risk-Off"
        elif vix < 14.0:
            return "Risk-On"
            
        cpi = get_cpi_yoy_value(curr, target_date)
        if cpi > 3.0:
            return "Inflation"
            
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        pmi_all = get_all_pmi_data(fred_key, EODHD_KEY, target_date=dt_str)
        pmi_data = pmi_all.get(curr, {})
        m_val = pmi_data.get("m_last")
        s_val = pmi_data.get("s_last")
        pmi_vals = [v for v in [m_val, s_val] if v is not None and v > 0]
        pmi_avg = np.mean(pmi_vals) if pmi_vals else 50.0
        
        gdp = get_gdp_yoy_value(curr, target_date)
        if pmi_avg < 50.0 and gdp < 1.0:
            return "Growth"
            
        yield_val, _, _ = get_fred_data_historical(YIELD_SERIES[curr], dt_str, fred_key)
        if yield_val is not None and yield_val > 4.0:
            return "Monetary Policy"
            
        unrate = get_unemployment_value(curr, target_date)
        if unrate < 4.0:
            return "Labour Market"
            
    except Exception:
        pass
    return "Normal"

def compute_macro_momentum(curr: str, target_date=None) -> float:
    try:
        fred_key = FRED_KEY
        if target_date is None:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        else:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
            
        pmi_all = get_all_pmi_data(fred_key, EODHD_KEY, target_date=dt_str)
        pmi_data = pmi_all.get(curr, {})
        m_val = pmi_data.get("m_last")
        m_prev = pmi_data.get("m_prev")
        
        pmi_change = (m_val - m_prev) if m_val is not None and m_prev is not None else 0.0
        
        unemp_trend = get_series_trend_points(UNEMP_SERIES.get(curr, "UNRATE"), dt_str, reverse=True)
        gdp_trend = get_series_trend_points(GDP_SERIES.get(curr, "GDPC1"), dt_str)
        
        momentum_score = 0.0
        if pmi_change > 0:
            momentum_score += 1.0
        elif pmi_change < 0:
            momentum_score -= 1.0
            
        if unemp_trend > 0:
            momentum_score += 1.0
        elif unemp_trend < 0:
            momentum_score -= 1.0
            
        if gdp_trend > 0:
            momentum_score += 1.0
        elif gdp_trend < 0:
            momentum_score -= 1.0
            
        return np.clip(momentum_score, -2.5, 2.5)
    except Exception:
        return 0.0

def compute_correction_score(curr: str, target_date=None) -> float:
    corr = 0.0
    dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d") if target_date else datetime.now().strftime("%Y-%m-%d")
    
    # 1. COT Percentile correction
    try:
        cot_val = get_latest_cot_percentile(curr, dt_str)
        if cot_val > 80.0:
            corr -= 2.0
        elif cot_val < 20.0:
            corr += 2.0
        elif cot_val > 60.0:
            corr += 1.5
        elif cot_val < 40.0:
            corr -= 1.5
    except Exception:
        pass
        
    # 2. Risk-On / Risk-Off correction
    try:
        vix = get_vix_value(dt_str)
        if vix is not None and vix > 22.0:
            if curr in ["USD", "CHF", "JPY"]:
                corr += 3.0
            else:
                corr -= 3.0
        elif vix is not None and vix < 14.0:
            if curr in ["USD", "CHF", "JPY"]:
                corr -= 2.0
            else:
                corr += 3.0
    except Exception:
        pass
        
    # 3. Commodity Score correction
    try:
        if curr == "CAD":
            oil_price = get_oil_price(dt_str)
            if oil_price > 75.0:
                corr += 2.0
            else:
                corr -= 2.0
        elif curr == "NZD":
            milk = get_milk_price(dt_str)
            if milk > 0.0:
                corr += 1.5
        elif curr == "AUD":
            cn_pmi = get_china_pmi_fred(dt_str)
            if cn_pmi > 50.0:
                corr += 2.0
            else:
                corr -= 2.0
    except Exception:
        pass
        
    # 4. Macro Momentum correction
    corr += compute_macro_momentum(curr, target_date)
    
    return np.clip(corr, -10.0, 10.0)

def get_bci_value(curr: str, target_date=None) -> dict:
    fred_key = FRED_KEY
    if target_date is None:
        dt_str = datetime.now().strftime("%Y-%m-%d")
    else:
        dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        
    bci_map_02 = {
        "USD": "BSCICP02USM460S",
        "EUR": "BSCICP02EZM460S",
        "GBP": "BSCICP02GBM460S",
        "CHF": "BSCICP02CHM460S"
    }
    bci_map_03 = {
        "JPY": "BSCICP03JPM665S",
        "AUD": "BSCICP03AUM665S",
        "NZD": "BSCICP03NZM665S"
    }
    
    val = None
    source = "FRED"
    ref_date = dt_str
    
    if curr in bci_map_02:
        series_id = bci_map_02[curr]
        val, dt, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        if val is not None:
            val = 50.0 + float(val)
            ref_date = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
            source = f"FRED ({series_id})"
    elif curr in bci_map_03:
        series_id = bci_map_03[curr]
        val, dt, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        if val is not None:
            val = 50.0 + (float(val) - 100.0) * 10.0
            ref_date = dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt) if dt else None
            source = f"FRED ({series_id})"
            
    if val is None:
        return None
    return {
        "value": val,
        "date": ref_date,
        "source": source
    }

CORE_FACTOR_WEIGHTS = {"Geldpolitik": 35.0, "Inflation": 20.0, "Arbeitsmarkt": 20.0, "PMI": 20.0, "GDP": 5.0}


def use_live_core_cache(target_date=None):
    if check_demo_active():
        return False
    live_date = target_date is None or pd.Timestamp(target_date).date() == datetime.now().date()
    return live_date and (os.environ.get("FX_COLLECTOR") != "1" or os.environ.get("FX_READ_CORE_CACHE") == "1")


def compute_currency_details(curr: str, target_date=None, include_context=True, factors_to_refresh=None) -> dict:
    """Evaluate requested factors; default callers retain all CORE calculations."""
    requested_factors = set(CORE_FACTOR_WEIGHTS if factors_to_refresh is None else factors_to_refresh)
    if use_live_core_cache(target_date):
        return live_data.details(curr)
    dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d") if target_date is not None else datetime.now().strftime("%Y-%m-%d")
    scores = {factor: None for factor in CORE_FACTOR_WEIGHTS}
    freshness = {factor: "UNAVAILABLE" for factor in CORE_FACTOR_WEIGHTS}
    observations = {}

    if 'Geldpolitik' in requested_factors:
        try:
            policy = get_verified_policy_rate(curr)
            policy_rate = finite_number(policy.get("rate")) if policy_rate_is_usable(policy) else None
            yield_2y, observed, source = get_genuine_2y_yield_historical(curr, dt_str, FRED_KEY, EODHD_KEY)
            yield_2y = finite_number(yield_2y)
            freshness["Geldpolitik"] = observation_freshness(observed, dt_str, 5, 15) if policy_rate is not None and yield_2y is not None else "UNAVAILABLE"
            observations["Geldpolitik"] = {"policy_rate": policy_rate, "yield_2y": yield_2y, "date": str(observed) if observed is not None else None, "source": source}
            if source == "US Treasury nominal 2Y constant maturity":
                observations["Geldpolitik"].update(series_id="BC_2YEAR (FRED equivalent DGS2)",
                    source_url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve")
            if freshness["Geldpolitik"] in ("FRESH", "AGING"):
                gp_nominal_score = (policy_rate - 3.0) / 3.0 * 100.0
                gp_market_score = (yield_2y - 3.0) / 3.0 * 100.0
                scores["Geldpolitik"] = float(np.clip(0.50 * gp_nominal_score + 0.50 * gp_market_score, -100.0, 100.0))
        except Exception:
            freshness["Geldpolitik"] = "UNAVAILABLE"

    if 'Inflation' in requested_factors:
        try:
            cpi, observed, metric_type, source, series_id, status = get_cpi_yoy_details(curr, dt_str)
            cpi = finite_number(cpi)
            freshness["Inflation"] = status
            observations["Inflation"] = {"value": cpi, "date": observed, "source": source, "series_id": series_id}
            if pd.Timestamp(dt_str).date() == datetime.now().date():
                observations["Inflation"].update({"frequency": "quarterly" if curr == "NZD" else "monthly",
                    "unit": "annual percent change", "seasonal_adjustment": "NSA"})
                if observed is not None:
                    period = pd.Timestamp(observed)
                    observations["Inflation"]["reference_period"] = f"{period.year}-Q{(period.month - 1) // 3 + 1}" if curr == "NZD" else period.strftime("%Y-%m")
            if curr in ("EUR", "CHF", "JPY", "AUD") and pd.Timestamp(dt_str).date() == datetime.now().date():
                official = get_current_official_cpi(curr)
                if official:
                    observations["Inflation"].update(official)
            if curr in ("NZD", "GBP", "CAD") and observed is not None:
                loader = {"NZD": get_statsnz_cpi_data, "GBP": get_ons_cpi_data, "CAD": get_statcan_cpi_data}[curr]
                release_frame, _, _ = loader()
                if release_frame is not None:
                    matching = release_frame[release_frame["date"] == pd.Timestamp(observed)]
                    if not matching.empty:
                        release = matching.iloc[-1]
                        if not release.get("is_pit_limited", True) and pd.notna(release.get("release_date")):
                            published = pd.Timestamp(release["release_date"])
                            published = published.tz_localize("UTC") if published.tzinfo is None else published.tz_convert("UTC")
                            observations["Inflation"]["published_at"] = published.isoformat()
            if cpi is not None and normalized_freshness(status) in ("FRESH", "AGING"):
                scores["Inflation"] = float(np.clip((cpi - 2.0) * 50.0, -100.0, 100.0))
        except Exception:
            freshness["Inflation"] = "UNAVAILABLE"

    if 'Arbeitsmarkt' in requested_factors:
        try:
            labour = get_macro_observation_details(curr, "Arbeitsmarkt", dt_str)
            observations["Arbeitsmarkt"] = labour
            freshness["Arbeitsmarkt"] = labour["freshness"]
            value = finite_number(labour.get("value"))
            if value is not None and normalized_freshness(labour["freshness"]) in ("FRESH", "AGING") and labour.get("frequency") != "annual":
                scores["Arbeitsmarkt"] = float(np.clip((5.0 - value) / 3.0 * 100.0, -100.0, 100.0))
        except Exception:
            freshness["Arbeitsmarkt"] = "UNAVAILABLE"

    if 'PMI' in requested_factors:
        try:
            pmi = (get_all_pmi_data(FRED_KEY, EODHD_KEY, target_date=dt_str) or {}).get(curr, {})
            eligible = []
            component_status = []
            observations["PMI"] = dict(pmi)
            for component in ("m", "s"):
                value = finite_number(pmi.get(f"{component}_last"))
                status = observation_freshness(pmi.get(f"{component}_ref"), dt_str, 45, 90, monthly=True)
                observations["PMI"][f"{component}_freshness"] = status
                if value is not None and 0 < value <= 100 and status in ("FRESH", "AGING"):
                    eligible.append(value)
                    component_status.append(status)
            observations["PMI"]["value"] = float(np.mean(eligible)) if eligible else None
            if eligible:
                freshness["PMI"] = "AGING" if "AGING" in component_status else "FRESH"
                scores["PMI"] = float(np.clip((observations["PMI"]["value"] - 50.0) / 10.0 * 100.0, -100.0, 100.0))
            else:
                freshness["PMI"] = "STALE" if any(observations["PMI"].get(f"{c}_freshness") == "STALE" for c in ("m", "s")) else "UNAVAILABLE"
        except Exception:
            freshness["PMI"] = "UNAVAILABLE"

    if 'GDP' in requested_factors:
        try:
            gdp = get_macro_observation_details(curr, "GDP", dt_str)
            observations["GDP"] = gdp
            freshness["GDP"] = gdp["freshness"]
            value = finite_number(gdp.get("value"))
            if value is not None and normalized_freshness(gdp["freshness"]) in ("FRESH", "AGING") and gdp.get("frequency") != "annual":
                scores["GDP"] = float(np.clip((value - 1.5) / 1.5 * 100.0, -100.0, 100.0))
        except Exception:
            freshness["GDP"] = "UNAVAILABLE"

    try:
        bci = get_bci_value(curr, dt_str) if include_context else None
        scores["BCI"] = finite_number(bci.get("value")) if bci else None
    except Exception:
        scores["BCI"] = None
    scores["_missing"] = [factor for factor in CORE_FACTOR_WEIGHTS if finite_number(scores[factor]) is None]
    scores["_completeness"] = sum(weight for factor, weight in CORE_FACTOR_WEIGHTS.items() if factor not in scores["_missing"])
    scores["_freshness"] = freshness
    scores["_observations"] = observations
    return scores


def compute_currency_professional_score_and_regime(curr: str, target_date=None):
    return compute_currency_professional_score_and_regime_custom(curr, None, target_date)


def compute_currency_professional_score_and_regime_custom(curr: str, weights: dict = None, target_date=None):
    # Signature retained for callers; frozen live weights cannot be promoted or
    # changed through session state, custom dictionaries or context parameters.
    weights = CORE_FACTOR_WEIGHTS
    scores = compute_currency_details(curr, target_date)
    missing = set(scores.get("_missing", []))
    for factor in CORE_FACTOR_WEIGHTS:
        if finite_number(scores.get(factor)) is None:
            scores[factor] = None
            missing.add(factor)
    scores["_missing"] = [factor for factor in CORE_FACTOR_WEIGHTS if factor in missing]
    scores["_completeness"] = sum(weight for factor, weight in CORE_FACTOR_WEIGHTS.items() if factor not in missing)
    # Context-only additions can never enter BASE CORE, even in custom research.
    active = {factor: (scores[factor], weights.get(factor, default) / 100.0)
              for factor, default in CORE_FACTOR_WEIGHTS.items() if factor not in missing}
    total_weight = sum(weight for value, weight in active.values())
    core_score = sum(value * weight for value, weight in active.values()) / total_weight if total_weight > 0 else None

    if use_live_core_cache(target_date):
        scores["_context_status"] = "NOT_COLLECTED"
        scores["_diagnostic_partial_score"] = core_score
        scores["_core_status"] = "VALID" if total_weight >= .5 else "INSUFFICIENT DATA"
        core_score = core_score if total_weight >= .5 else None
        return core_score, "Context nicht geprüft", core_score, 0.0, scores

    dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d") if target_date is not None else datetime.now().strftime("%Y-%m-%d")
    series = {"Geldpolitik": YIELD_2Y_SERIES.get(curr), "Inflation": CPI_SERIES.get(curr),
              "Arbeitsmarkt": UNEMP_SERIES.get(curr), "PMI": PMI_SERIES.get(curr), "GDP": GDP_SERIES.get(curr)}
    trends, surprises = {}, {}
    for factor in CORE_FACTOR_WEIGHTS:
        trends[factor] = 0.0
        surprises[factor] = 0.0
        if factor not in missing:
            try:
                trends[factor] = finite_number(get_series_trend_points(series[factor], dt_str, reverse=factor == "Arbeitsmarkt")) or 0.0
            except Exception:
                pass
            if factor != "GDP":
                try:
                    surprises[factor] = finite_number(get_surprise_points(curr, "Wachstum" if factor == "PMI" else factor, dt_str)) or 0.0
                except Exception:
                    pass
    trend_score = sum(trends[factor] * weight for factor, (_, weight) in active.items()) / total_weight if total_weight > 0 else 0.0
    surprise_score = sum(surprises[factor] * weight for factor, (_, weight) in active.items()) / total_weight if total_weight > 0 else 0.0
    try:
        corr_score = (finite_number(compute_correction_score(curr, target_date)) or 0.0) * weights.get("Correction", 100.0) / 100.0
    except Exception:
        corr_score = 0.0
    try:
        regime = detect_market_regime(curr, target_date)
    except Exception:
        regime = "Normal"

    scores["_diagnostic_partial_score"] = core_score
    if scores["_completeness"] < 50.0 or core_score is None:
        core_score = None
        final_score = None
        trend_score = surprise_score = 0.0
        scores["_core_status"] = "INSUFFICIENT DATA"
    else:
        final_score = float(np.clip(core_score + trend_score + surprise_score + corr_score, -100.0, 100.0))
        scores["_core_status"] = "VALID"
    scores["_trend_score"] = trend_score
    scores["_surprise_score"] = surprise_score
    scores["_trend_details"] = dict(zip(("gp_trend", "cpi_trend", "lab_trend", "pmi_trend", "gdp_trend"), trends.values()))
    scores["_surprise_details"] = dict(zip(("gp_surprise", "cpi_surprise", "lab_surprise", "pmi_surprise"), (surprises[k] for k in list(CORE_FACTOR_WEIGHTS)[:4])))
    # Missing factor scores stay None in UI, charts and persisted snapshots.
    return final_score, regime, core_score, corr_score, scores


def compute_currency_score_historical(curr: str, target_date) -> float:
    try:
        final_score, _, _, _, _ = compute_currency_professional_score_and_regime(curr, target_date)
        if final_score is None:
            return None
        mapped_score = (final_score + 100.0) / 2.0
        return float(mapped_score)
    except Exception:
        return None


def load_backtest_decisions():
    file_path = "backtest_decisions.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_backtest_decision(decision):
    file_path = "backtest_decisions.json"
    decisions = load_backtest_decisions()
    decisions.append(decision)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(decisions, f, indent=4, ensure_ascii=False)
    except Exception:
        pass
    return decisions


@st.cache_data(ttl=60, show_spinner=False)
def get_roro_index(fred_key, tiingo_key, apifreaks_key=None):
    debug_logs = []
    
    # 1. Check API Key presence
    if fred_key:
        debug_logs.append("FRED: API-Key in .env vorhanden.")
    else:
        debug_logs.append("FRED: API-Key fehlt in .env.")
        
    if apifreaks_key:
        debug_logs.append("APIFreaks: API-Key in .env vorhanden.")
    else:
        debug_logs.append("APIFreaks: API-Key fehlt in .env.")

    def query_fred(series_id, key):
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={key}&file_type=json&observation_start=2015-01-01"
        return requests.get(url, timeout=8)

    # 2. Test general FRED key validity with FEDFUNDS
    fred_works = False
    if fred_key:
        debug_logs.append("FRED: Teste API-Verbindung mit Indikator 'FEDFUNDS'...")
        try:
            r = query_fred("FEDFUNDS", fred_key)
            debug_logs.append(f"FRED (FEDFUNDS) Test: HTTP Status {r.status_code}")
            if r.status_code == 200:
                obs = r.json().get("observations", [])
                if obs:
                    fred_works = True
                    debug_logs.append("FRED: Verbindungstest erfolgreich. FEDFUNDS geladen.")
                else:
                    debug_logs.append("FRED: Antwort für FEDFUNDS war leer (keine observations).")
            else:
                debug_logs.append(f"FRED: Verbindungstest fehlgeschlagen mit HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"FRED: Netzwerkfehler bei Verbindungstest: {str(e)}")

    # 3. Attempt KCRORO
    if fred_works:
        debug_logs.append("FRED: Versuche primären RORO-Indikator 'KCRORO' zu laden...")
        try:
            r = query_fred("KCRORO", fred_key)
            debug_logs.append(f"FRED (KCRORO) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                obs = r.json().get("observations", [])
                parsed = []
                for o in obs:
                    if o["value"] != ".":
                        parsed.append({"date": o["date"], "value": float(o["value"])})
                if parsed:
                    val = float(parsed[-1]["value"])
                    dt = pd.to_datetime(parsed[-1]["date"])
                    debug_logs.append("FRED (KCRORO) erfolgreich geladen.")
                    return val, dt, "FRED Risk-On/Risk-Off (KCRORO)", debug_logs
                else:
                    debug_logs.append("FRED (KCRORO): Observations waren leer oder ungültig.")
            else:
                debug_logs.append(f"FRED (KCRORO) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"FRED (KCRORO): Netzwerkfehler: {str(e)}")

    # 4. Swap: Option A1: VIX via APIFreaks immediately after FRED KCRORO
    if apifreaks_key:
        debug_logs.append("Weiche auf Option A1 aus: APIFreaks VIX Index...")
        try:
            url = "https://api.apifreaks.com/v1.0/commodity/rates/latest"
            params = {
                "apiKey": apifreaks_key,
                "symbols": "VIX",
                "updates": "1m"
            }
            r = requests.get(url, params=params, timeout=10)
            debug_logs.append(f"APIFreaks (VIX) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if data and data.get("success") and "rates" in data:
                    rates = data["rates"]
                    vix_val = rates.get("VIX")
                    if vix_val is not None:
                        val = float(vix_val)
                        dt_str = data.get("date", "")
                        dt = pd.to_datetime(dt_str) if dt_str else datetime.now()
                        debug_logs.append(f"APIFreaks (VIX): Erfolgreich geladen (Wert: {val:.2f}).")
                        return val, dt, "APIFreaks VIX Volatilitätsindex", debug_logs
                    else:
                        debug_logs.append("APIFreaks (VIX): VIX-Wert nicht in Antwort gefunden.")
                else:
                    debug_logs.append("APIFreaks (VIX): Antwort war leer oder ungültig.")
            else:
                debug_logs.append(f"APIFreaks (VIX) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"APIFreaks (VIX): Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("APIFreaks: API-Key (APIFREAKS_API_KEY) fehlt in .env. Option A1 (VIX) übersprungen.")

    # 5. Swap: Option A2: VIX via Tiingo (VIXY)
    if tiingo_key:
        debug_logs.append("Weiche auf Option A2 aus: Tiingo VIXY Index...")
        try:
            url = "https://api.tiingo.com/tiingo/daily/VIXY/prices"
            headers = {"Authorization": f"Token {tiingo_key}"}
            r = requests.get(url, headers=headers, timeout=10)
            debug_logs.append(f"Tiingo (VIXY) Abfrage: HTTP Status {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    latest_vix = data[-1]
                    val = float(latest_vix["close"])
                    dt_str = latest_vix.get("date", "")
                    dt = pd.to_datetime(dt_str) if dt_str else datetime.now()
                    debug_logs.append(f"Tiingo (VIXY): Erfolgreich geladen (Schlusskurs: {val:.2f}).")
                    return val, dt, "Tiingo VIXY Volatilitätsindex", debug_logs
                else:
                    debug_logs.append("Tiingo (VIXY): Antwort war leer oder ungültig.")
            else:
                debug_logs.append(f"Tiingo (VIXY) fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
        except Exception as e:
            debug_logs.append(f"Tiingo (VIXY): Netzwerkfehler: {str(e)}")
    else:
        debug_logs.append("Tiingo: API-Key (TIINGO_API_KEY) fehlt in .env. Option A2 (VIX) übersprungen.")

    # 5. Option B: 10Y-2Y Spread over FRED
    if fred_works:
        debug_logs.append("Weiche auf Option B aus: FRED 10Y-2Y Spread (DGS10 - DGS2)...")
        try:
            r_10y = query_fred("DGS10", fred_key)
            r_2y = query_fred("DGS2", fred_key)
            debug_logs.append(f"FRED DGS10 Abfrage: HTTP Status {r_10y.status_code}")
            debug_logs.append(f"FRED DGS2 Abfrage: HTTP Status {r_2y.status_code}")
            if r_10y.status_code == 200 and r_2y.status_code == 200:
                obs_10y = r_10y.json().get("observations", [])
                obs_2y = r_2y.json().get("observations", [])
                parsed_10y = {o["date"]: float(o["value"]) for o in obs_10y if o["value"] != "."}
                parsed_2y = {o["date"]: float(o["value"]) for o in obs_2y if o["value"] != "."}
                
                common_dates = sorted(list(set(parsed_10y.keys()).intersection(set(parsed_2y.keys()))))
                if common_dates:
                    latest_date = common_dates[-1]
                    val = parsed_10y[latest_date] - parsed_2y[latest_date]
                    dt = pd.to_datetime(latest_date)
                    debug_logs.append(f"FRED (10Y-2Y): Spread erfolgreich berechnet ({val:+.4f}%).")
                    return val, dt, "FRED 10Y-2Y Spread (DGS10 - DGS2)", debug_logs
                else:
                    debug_logs.append("FRED (10Y-2Y): Keine gemeinsamen Datumsangaben gefunden.")
            else:
                debug_logs.append("FRED (10Y-2Y): Fehlerhafte Statuscodes bei DGS10 oder DGS2.")
        except Exception as e:
            debug_logs.append(f"FRED (10Y-2Y): Netzwerkfehler: {str(e)}")

    # 6. Option C: USD/JPY Daily Change Proxy
    debug_logs.append("Weiche auf Option C aus: USD/JPY Exchange Rate Proxy...")
    try:
        url = "https://currencyapi.vitalmedx.com/api/v1/timeseries"
        params = {
            "start_date": "2025-12-20",
            "end_date": "2025-12-31",
            "base": "USD",
            "symbols": "JPY"
        }
        r = requests.get(url, params=params, timeout=10)
        debug_logs.append(f"CurrencyArchiveAPI USD/JPY: HTTP Status {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and "data" in data:
                rates_dict = data["data"].get("rates", {})
                sorted_dates = sorted(rates_dict.keys())
                parsed = []
                for d in sorted_dates:
                    val = rates_dict[d].get("JPY")
                    if val is not None:
                        parsed.append((d, float(val)))
                if len(parsed) >= 2:
                    latest_close = parsed[-1][1]
                    prev_close = parsed[-2][1]
                    change = (latest_close - prev_close) / prev_close
                    dt = pd.to_datetime(parsed[-1][0])
                    debug_logs.append(f"CurrencyArchiveAPI (USD/JPY): Erfolgreich geladen (Änderung: {change:+.2%}).")
                    return change, dt, "USD/JPY Proxy (Tagesänderung)", debug_logs
                else:
                    debug_logs.append("CurrencyArchiveAPI USD/JPY: Weniger als 2 Kurse im Zeitraum gefunden.")
            else:
                debug_logs.append("CurrencyArchiveAPI USD/JPY: Fehlermeldung in JSON-Antwort.")
        else:
            debug_logs.append(f"CurrencyArchiveAPI USD/JPY fehlgeschlagen: HTTP {r.status_code}. Antwort: {r.text[:150]}")
    except Exception as e:
        debug_logs.append(f"CurrencyArchiveAPI USD/JPY: Netzwerkfehler: {str(e)}")

    debug_logs.append("FRED: Alle Indikatoren und alternative Fallbacks fehlgeschlagen.")
    return None, None, None, debug_logs





# ----------------- Helper Functions -----------------
def calculate_smas(df):
    if len(df) >= 50:
        df["SMA_50"] = df["close"].rolling(window=50).mean()
    else:
        df["SMA_50"] = np.nan
    if len(df) >= 200:
        df["SMA_200"] = df["close"].rolling(window=200).mean()
    else:
        df["SMA_200"] = np.nan
    return df

def format_freshness(timestamp):
    elapsed = datetime.now() - timestamp
    secs = int(elapsed.total_seconds())
    if secs < 60:
        return f"vor {secs}s"
    mins = secs // 60
    return f"vor {mins}m {secs % 60}s"

def get_default_query(base, quote):
    return f"{base} {quote} forex OR central bank OR interest OR inflation OR GDP"

def deduplicate_articles(articles):
    seen_urls = set()
    seen_titles = set()
    unique_articles = []
    for art in articles:
        url = art.get("url")
        if url:
            url_norm = url.replace("https://", "").replace("http://", "").rstrip("/")
        else:
            url_norm = ""
            
        title = art.get("title", "").strip().lower()
        for suffix in [" - reuters", " - bloomberg", " - cnbc", " - marketwatch", " | reuters", " | bloomberg", " | cnbc"]:
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
                
        title_clean = "".join(c for c in title if c.isalnum())
        title_trunc = title_clean[:100]
        
        if not title_trunc:
            continue
            
        if url_norm in seen_urls or title_trunc in seen_titles:
            continue
            
        if url_norm:
            seen_urls.add(url_norm)
        seen_titles.add(title_trunc)
        unique_articles.append(art)
    return unique_articles

def categorize_article(art):
    title_desc = f"{art.get('title', '')} {art.get('description', '')}".lower()
    trade_keywords = ["export", "import", "trade", "handel", "zoll", "tariffs", "lieferkette", "supply chain", "bilanz", "freihandel"]
    if any(kw in title_desc for kw in trade_keywords):
        return "🚢 Import & Export"
        
    rates_keywords = ["fed", "fomc", "leitzins", "zins", "interest", "ecb", "ezb", "rate", "central bank", "zentralbank", "powell", "lagarde", "geldpolitik"]
    if any(kw in title_desc for kw in rates_keywords):
        return "🏦 Geldpolitik & Zinsen"
        
    country_keywords = ["usa", "us-dollar", "america", "eurozone", "deutsch", "germany", "schweiz", "swiss", "kanada", "canada", "australi", "neuseeland", "new zealand", "japan", "england", "britain", "uk ", "gbp"]
    if any(kw in title_desc for kw in country_keywords):
        return "🌍 Länder-Analysen"
        
    return "📊 Sonstige Makro-News"

def get_country_rate(country_code, fred_key):
    # Mapping country codes to currencies
    map_code = {
        "USA": "USD",
        "EMU": "EUR",
        "GBR": "GBP",
        "JPN": "JPY",
        "CHE": "CHF",
        "AUS": "AUD",
        "CAN": "CAD",
        "NZL": "NZD"
    }
    curr = map_code.get(country_code, country_code)
    pol_obj = get_verified_policy_rate(curr)
    if not policy_rate_is_usable(pol_obj):
        return None, None, pol_obj.get("verification_status", "UNAVAILABLE")
    val = pol_obj.get("rate")
    prev_val = pol_obj.get("previous_rate", val)
    bps_change = round((val - prev_val) * 100) if (val is not None and prev_val is not None) else None
    return val, bps_change, pol_obj.get("primary_source", "Verified Central Bank")

# Compute economic score for one currency
def compute_currency_score(curr, fred_key):
    try:
        final_score, _, _, _, _ = compute_currency_professional_score_and_regime(curr, None)
        if final_score is None:
            return None
        mapped_score = (final_score + 100.0) / 2.0
        return float(mapped_score)
    except Exception:
        return None


COT_SYMBOLS = {
    "EUR": "098662",
    "GBP": "096742",
    "CHF": "092741",
    "CAD": "090741",
    "AUD": "232741",
    "NZD": "112741",
    "JPY": "097741"
}

@st.cache_data(ttl=86400, show_spinner=False)
def load_cot_year_cached(year):
    try:
        import cot_reports as cot
        df = cot.cot_year(year, cot_report_type='legacy_fut')
        if os.path.exists("annual.txt"):
            try:
                os.remove("annual.txt")
            except Exception:
                pass
        return df
    except Exception:
        return None

def get_cot_signal(symbol_code, target_date):
    try:
        target_dt = pd.to_datetime(target_date)
        y = target_dt.year
        
        # Load 3 years of COT data (current year + 3 previous years)
        dfs = []
        for offset in range(4):
            df_y = load_cot_year_cached(y - offset)
            if df_y is not None and not df_y.empty:
                dfs.append(df_y)
                
        if not dfs:
            return 50.0
            
        df = pd.concat(dfs, ignore_index=True)
        df.columns = df.columns.str.strip()
        
        code_col = "CFTC Contract Market Code" if "CFTC Contract Market Code" in df.columns else "CFTC_Contract_Market_Code"
        if code_col not in df.columns:
            return 50.0
            
        df[code_col] = df[code_col].astype(str).str.strip()
        df[code_col] = df[code_col].apply(lambda x: x.zfill(6) if x.isdigit() else x)
        
        symbol_code_std = str(symbol_code).strip().zfill(6)
        df_filtered = df[df[code_col] == symbol_code_std].copy()
        
        if df_filtered.empty:
            return 50.0
            
        date_col = "As of Date in Form YYYY-MM-DD" if "As of Date in Form YYYY-MM-DD" in df_filtered.columns else "As of Date in Form YYMMDD"
        if date_col == "As of Date in Form YYYY-MM-DD":
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], errors="coerce")
        else:
            df_filtered["parsed_date"] = pd.to_datetime(df_filtered[date_col], format="%y%m%d", errors="coerce")
            
        df_filtered = df_filtered.dropna(subset=["parsed_date"])
        df_filtered = df_filtered[df_filtered["parsed_date"] <= target_dt]
        if df_filtered.empty:
            return 50.0
            
        df_filtered = df_filtered.sort_values("parsed_date")
        # Keep last 156 observations (3 years of weekly reports)
        df_filtered = df_filtered.tail(156)
        if len(df_filtered) < 5:
            return 50.0
            
        long_col = "Noncommercial Positions-Long (All)"
        short_col = "Noncommercial Positions-Short (All)"
        
        df_filtered[long_col] = pd.to_numeric(df_filtered[long_col], errors="coerce").fillna(0.0)
        df_filtered[short_col] = pd.to_numeric(df_filtered[short_col], errors="coerce").fillna(0.0)
        
        df_filtered["net_pos"] = df_filtered[long_col] - df_filtered[short_col]
        
        net_positions = df_filtered["net_pos"].values
        current_net = net_positions[-1]
        
        # Calculate percentile rank
        count_less_or_equal = np.sum(net_positions <= current_net)
        percentile_rank = (count_less_or_equal / len(net_positions)) * 100.0
        
        return percentile_rank
    except Exception:
        return 50.0

def load_manual_cot():
    file_path = "manual_cot.json"
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_manual_cot_entry(curr, position, net_pos, percentile, date_str):
    if not operator_is_authorized():
        raise PermissionError("OPERATOR_ACCESS_REQUIRED")
    file_path = "manual_cot.json"
    cot_data = load_manual_cot()
    cot_data[curr] = {
        "position": position,
        "net_position": net_pos,
        "percentile": percentile,
        "date": date_str
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(cot_data, f, indent=4)

def get_latest_cot_percentile(curr, target_date=None):
    # Check manual COT input first
    manual_data = load_manual_cot()
    if curr in manual_data:
        entry = manual_data[curr]
        return float(entry.get("percentile", 50.0))
        
    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")
    code = COT_SYMBOLS.get(curr)
    if not code:
        return None
        
    val = get_cot_signal(code, target_date)
    if val == 50.0 and not check_demo_active():
        return None
    return val

# ----------------- UI RENDERERS -----------------
def render_bias_box(signal_val, base_curr, quote_curr, base_total_score, quote_total_score, sig):
    """Renders the Divergence Trading Bias banner with dynamic G8 quantitative signaling."""
    if sig == "INSUFFICIENT DATA" or signal_val is None:
        bg_color = "rgba(132, 142, 156, 0.05)"
        border_color = "#444c56"
        text_color = "#8b949e"
        title = f"INSUFFICIENT FUNDAMENTAL DATA ({base_curr}/{quote_curr})"
        desc = f"Paar-Signale erfordern 100% CORE-Abdeckung bei beiden Währungen ({base_curr} und {quote_curr}). Mindestens eine Seite ist unvollständig; das Signal bleibt gesperrt."
        badge = "INSUFFICIENT DATA"
    elif sig == "SB":
        bg_color = "rgba(16, 185, 129, 0.08)"
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER BUY-BIAS (STRONG BUY für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {base_curr} (Signal-Wert: {signal_val:+.1f}). Suche primär nach bullishen Einstiegen (SMC / FVG) im Chart."
        badge = "STRONG BUY"
    elif sig == "MB":
        bg_color = "rgba(226, 177, 60, 0.05)"
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER BUY-BIAS (MID BUY für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {base_curr} (Signal-Wert: {signal_val:+.1f}). Nutze charttechnische Bestätigung vor Einstiegen."
        badge = "MID BUY"
    elif sig == "NT":
        bg_color = "rgba(132, 142, 156, 0.05)"
        border_color = "#444c56"
        text_color = "#8b949e"
        title = f"NEUTRAL / NO TRADE ({base_curr}/{quote_curr})"
        desc = f"Keine signifikante fundamentale Divergenz zwischen {base_curr} und {quote_curr} (Signal-Wert: {signal_val:+.1f}). Seitwärtsbewegung wahrscheinlich. Neutraler Bias."
        badge = "NEUTRAL"
    elif sig == "MS":
        bg_color = "rgba(226, 177, 60, 0.05)"
        border_color = "#e2b13c"
        text_color = "#e2b13c"
        title = f"MITTLERER SELL-BIAS (MID SELL für {base_curr}/{quote_curr})"
        desc = f"Milder fundamentaler Vorteil für {quote_curr} (Signal-Wert: {signal_val:+.1f}). Suche nach charttechnischen Bestätigungen für Short-Setups."
        badge = "MID SELL"
    elif sig == "SS":
        bg_color = "rgba(16, 185, 129, 0.08)"
        border_color = "#10b981"
        text_color = "#10b981"
        title = f"STARKER SELL-BIAS (STRONG SELL für {base_curr}/{quote_curr})"
        desc = f"Die makroökonomische Divergenz spricht deutlich für den {quote_curr} (Signal-Wert: {signal_val:+.1f}). Suche primär nach bearishen Einstiegen im Chart."
        badge = "STRONG SELL"
    else:
        bg_color = "rgba(132, 142, 156, 0.05)"
        border_color = "#30363d"
        text_color = "#8b949e"
        title = "BERECHNUNGSFEHLER"
        desc = "Unzureichende Daten zur Bestimmung des Biases."
        badge = "ERR"

    sig_val_str = f"{signal_val:+.1f}" if signal_val is not None else "N/A"

    html_content = f"""
    <div style="
        background-color: {bg_color};
        border: 1px solid {border_color};
        border-radius: 6px;
        padding: 20px 24px;
        margin: 10px 0 0px 0;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <span style="
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1.2px;
                color: #8b949e;
            ">{base_curr}/{quote_curr} Fundamental-Signal: {sig_val_str}</span>
            <span style="
                background-color: {border_color}22;
                color: {text_color};
                border: 1px solid {border_color};
                font-weight: 700;
                font-size: 0.7rem;
                padding: 2px 10px;
                border-radius: 4px;
                text-transform: uppercase;
            ">{badge}</span>
        </div>
        <h2 style="
            color: {text_color};
            margin: 0 0 6px 0;
            font-size: 1.5rem;
            font-weight: 600;
            letter-spacing: -0.3px;
        ">{title}</h2>
        <p style="
            color: #8b949e;
            margin: 0;
            font-size: 0.95rem;
            line-height: 1.45;
        ">{desc}</p>
    </div>
    """
    st.markdown(html_content, unsafe_allow_html=True)

def format_score(value, digits=1):
    number = finite_number(value)
    return f"{number:+.{digits}f}" if number is not None else "N/A"


def render_metric_card(title, val_str, source_text, is_live):
    live_class = "source-tag-live" if is_live else ""
    card_html = f"""
    <div class="metric-card-custom">
        <span class="metric-label">{title}</span>
        <div class="metric-value">{val_str}</div>
        <div class="source-tag {live_class}">Quelle: {source_text}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

def render_articles_grid(articles_list):
    if not articles_list:
        st.info("Keine Artikel in dieser Kategorie vorhanden.")
        return
        
    cols = st.columns(3)
    for idx, art in enumerate(articles_list):
        col_idx = idx % 3
        with cols[col_idx]:
            # Prepare pubdate
            pub_date_str = ""
            if art['publishedAt']:
                try:
                    dt = pd.to_datetime(art['publishedAt'])
                    pub_date_str = dt.strftime('%d.%m.%Y %H:%M')
                except:
                    pub_date_str = str(art['publishedAt'])
            
            # Image tag
            fallback_img = "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=500&auto=format&fit=crop&q=80"
            img_html = ""
            if art.get('urlToImage'):
                img_html = f'<img src="{art["urlToImage"]}" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src=\'{fallback_img}\';" style="width:100%; height:130px; object-fit:cover; border-radius:6px; margin-bottom:10px; border: 1px solid #1f2026;">'
            else:
                img_html = f'<div style="width:100%; height:130px; background-color:#0c0c0e; border-radius:6px; margin-bottom:10px; display:flex; justify-content:center; align-items:center; border: 1px solid #1f2026;"><span style="font-size:2rem;">📊</span></div>'
                
            desc_str = art.get('description', '')
            if not desc_str:
                desc_str = "Keine Kurzbeschreibung verfügbar. Bitte folge dem Link, um den vollständigen Artikel zu lesen."
            if len(desc_str) > 200:
                desc_str = desc_str[:197] + "..."
                
            st.markdown(f"""
            <div class="news-card">
                <div>
                    {img_html}
                    <a class="news-title" href="{art['url']}" target="_blank">{art['title']}</a>
                    <div class="news-meta">Quelle: <strong>{art['source']}</strong> | {pub_date_str}</div>
                    <p class="news-desc">{desc_str}</p>
                </div>
                <div style="border-top:1px solid #1f2026; padding-top:8px; margin-top:8px; display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.68rem; color:#8b949e; background-color:#1f2026; padding:2px 6px; border-radius:3px;">{art.get('api', 'News')}</span>
                    <a href="{art['url']}" target="_blank" style="font-size:0.75rem; color:#e2b13c; text-decoration:none; font-weight:600;">Lesen ↗</a>
                </div>
            </div>
            """, unsafe_allow_html=True)


def pair_core_is_complete(details):
    """V2.8: five complete factors AND current collector validation."""
    return (isinstance(details, dict)
            and details.get("_live_checked") is True
            and finite_number(details.get("_completeness")) == 100.0
            and not details.get("_missing")
            and all(finite_number(details.get(factor)) is not None
                    for factor in ("Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP")))


def get_pair_signal_and_badge(base, quote, model_weights=None):
    """
    Canonical pair fundamental divergence function based strictly on BASE CORE.
    divergence = base_core_score - quote_core_score
    
    Thresholds:
    >= +50.0        => STRONG BUY (SB)
    +20.0 to <+50.0 => MID BUY (MB)
    >-20.0 to <+20.0 => NEUTRAL (NT)
    >-50.0 to <=-20.0 => MID SELL (MS)
    <= -50.0        => STRONG SELL (SS)
    """
    if model_weights is not None:
        _, _, b_core, _, b_details = compute_currency_professional_score_and_regime_custom(base, model_weights)
        _, _, q_core, _, q_details = compute_currency_professional_score_and_regime_custom(quote, model_weights)
    else:
        _, _, b_core, _, b_details = compute_currency_professional_score_and_regime(base)
        _, _, q_core, _, q_details = compute_currency_professional_score_and_regime(quote)
    
    if finite_number(b_core) is None or finite_number(q_core) is None or not pair_core_is_complete(b_details) or not pair_core_is_complete(q_details):
        return "INSUFFICIENT FUNDAMENTAL DATA", "#8b949e", None, "INSUFFICIENT DATA"
        
    divergence = float(b_core - q_core)
    
    if divergence >= 50.0:
        s = "SB"
        b = "STRONG BUY"
        c = "#10b981"
    elif 20.0 <= divergence < 50.0:
        s = "MB"
        b = "MID BUY"
        c = "#34d399"
    elif -20.0 < divergence < 20.0:
        s = "NT"
        b = "NEUTRAL"
        c = "#8b949e"
    elif -50.0 < divergence <= -20.0:
        s = "MS"
        b = "MID SELL"
        c = "#f87171"
    else:
        s = "SS"
        b = "STRONG SELL"
        c = "#ef4444"
        
    return b, c, divergence, s

def get_historical_indicator_values(series_id, dt_str, fred_key):
    try:
        val_now, _, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        dt_1m = (pd.to_datetime(dt_str) - timedelta(days=30)).strftime("%Y-%m-%d")
        val_1m, _, _ = get_fred_data_historical(series_id, dt_1m, fred_key)
        dt_3m = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
        val_3m, _, _ = get_fred_data_historical(series_id, dt_3m, fred_key)
        dt_6m = (pd.to_datetime(dt_str) - timedelta(days=180)).strftime("%Y-%m-%d")
        val_6m, _, _ = get_fred_data_historical(series_id, dt_6m, fred_key)
        return val_now, val_1m, val_3m, val_6m
    except Exception:
        return None, None, None, None

# ----------------- 3. SIDEBAR CONFIGURATION -----------------
invalid_pair = False
base_curr = "USD"
quote_curr = "EUR"
selected_pair = "USD/EUR"
latest_close = 0.0

if not getattr(st, "_mock_mode", False):
    with st.sidebar:
        st.title("⚙️ Dashboard-Einstellungen")
        demo_mode_chk = st.checkbox("🧪 Demo Mode (Mock-Daten aktiv)", value=False, key="demo_mode_chk")
        st.markdown("### 💱 Währungspaar wählen")
        base_curr = st.selectbox("Basiswährung (Base)", options=list(CURRENCIES.keys()), index=0)
        quote_curr = st.selectbox("Quote-Währung (Quote)", options=list(CURRENCIES.keys()), index=1)
        selected_pair = f"{base_curr}/{quote_curr}"
        
        invalid_pair = (base_curr == quote_curr)
        if invalid_pair:
            st.error("⚠️ Basis- und Quote-Währung dürfen nicht identisch sein.")
            
        show_all_pairs = st.checkbox("Alle Paare anzeigen (inkl. Neutral)", value=False, key="show_all_pairs_chk")
        with st.expander("🔒 Betreiberzugang", expanded=False):
            if load_api_key("DASHBOARD_OPERATOR_PASSWORD"):
                st.text_input("Betreiber-Passwort", type="password", key="operator_password")
            else:
                st.caption("Bearbeitungsfunktionen sind gesperrt. Betreiberzugang ist nicht eingerichtet.")
        st.button("🔄 System-Cache leeren", on_click=st.cache_data.clear, disabled=not operator_is_authorized())
        
        st.markdown("---")
        st.markdown("### 🏦 VERIFIED POLICY RATE CENTER")
        st.caption("Automatisch verifizierte Leitzinssätze (Official Central-Bank Sources):")
        
        rates_obj = get_all_verified_policy_rates()
        table_rows = []
        for c in ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]:
            pol = rates_obj.get(c, {})
            r_val = pol.get("rate")
            p_val = pol.get("previous_rate", r_val)
            stat = pol.get("verification_status", "🔴 OFFICIAL SOURCE UNAVAILABLE")
            table_rows.append({
                "Currency": c,
                "Central Bank": pol.get("central_bank", "Central Bank"),
                "Policy Instrument": pol.get("instrument", "Policy Rate"),
                "Rate (siehe Status)": f"{r_val:.2f}%" if r_val is not None else "N/A",
                "Previous Rate": f"{p_val:.2f}%" if p_val is not None else "N/A",
                "Rate Effective": pol.get("rate_effective_date", "N/A"),
                "Last Decision": pol.get("last_policy_decision_date", "N/A"),
                "Verification Status": stat,
                "Last Verified": pol.get("verified_at", "N/A"),
                "Source": pol.get("primary_source", "Central Bank")
            })
        st.dataframe(pd.DataFrame(table_rows), hide_index=True)

        if st.button("🔄 Offizielle Leitzinsen aktualisieren", disabled=not operator_is_authorized()) and operator_is_authorized():
            with st.spinner("Prüfe offizielle Notenbank-Quellen..."):
                checked_rates = refresh_all_verified_policy_rates(FRED_KEY)
            st.session_state["policy_refresh_summary"] = sum("🟢" in value.get("verification_status", "") for value in checked_rates.values())
            st.rerun()
        if "policy_refresh_summary" in st.session_state:
            count = st.session_state["policy_refresh_summary"]
            if count == 8:
                st.success("8 von 8 Leitzinsen mit offiziellen Quellen bestätigt.")
            else:
                st.warning(f"{count} von 8 Leitzinsen bestätigt. Bitte die Quellenstatus in der Tabelle beachten.")

        with st.expander("🚨 Advanced / Emergency Manual Override", expanded=False):
            st.caption("Standard: AUS. Bei Aktivierung überschreiben manuelle Werte die offiziellen Daten. Dies wird in den Snapshots dokumentiert.")
            emergency_on = st.checkbox("Manuelles Emergency-Override aktivieren", value=False, key="emergency_manual_rates_override", disabled=not operator_is_authorized())
            if emergency_on and operator_is_authorized():
                st.warning("🔴 MANUAL POLICY RATE OVERRIDE ACTIVE – CORE verwendet manuelle Eingaben!")
                st.number_input("European Central Bank (EUR) %", min_value=0.0, max_value=15.0, key="manual_rate_EUR", value=2.25, step=0.05)
                st.number_input("Federal Reserve (USD) %", min_value=0.0, max_value=15.0, key="manual_rate_USD", value=3.50, step=0.05)
                st.number_input("Bank of England (GBP) %", min_value=0.0, max_value=15.0, key="manual_rate_GBP", value=3.75, step=0.05)
                st.number_input("Bank of Japan (JPY) %", min_value=-5.0, max_value=15.0, key="manual_rate_JPY", value=1.00, step=0.05)
                st.number_input("Swiss National Bank (CHF) %", min_value=-5.0, max_value=15.0, key="manual_rate_CHF", value=0.00, step=0.05)
                st.number_input("Bank of Canada (CAD) %", min_value=0.0, max_value=15.0, key="manual_rate_CAD", value=2.25, step=0.05)
                st.number_input("Reserve Bank of Australia (AUD) %", min_value=0.0, max_value=15.0, key="manual_rate_AUD", value=4.35, step=0.05)
                st.number_input("Reserve Bank of New Zealand (NZD) %", min_value=0.0, max_value=15.0, key="manual_rate_NZD", value=2.50, step=0.05)
        
        def get_cot_data_status():
            try:
                now = datetime.now()
                y = now.year
                df_cot = load_cot_year_cached(y)
                if df_cot is None or df_cot.empty:
                    df_cot = load_cot_year_cached(y - 1)
                if df_cot is not None and not df_cot.empty:
                    date_col = "As of Date in Form YYYY-MM-DD" if "As of Date in Form YYYY-MM-DD" in df_cot.columns else "As of Date in Form YYMMDD"
                    dates = pd.to_datetime(df_cot[date_col])
                    latest_date = dates.max()
                    days_diff = (now - latest_date).days
                    
                    if days_diff <= 10:
                        status = "🟢 Aktuell"
                        weekday = now.weekday()
                        if weekday in [5, 6, 0]:
                            explanation = "Der Bericht spiegelt die Daten vom letzten Dienstag wider."
                        elif weekday in [1, 2, 3]:
                            explanation = "Daten vom Dienstag dieser Woche werden am Freitagabend veröffentlicht."
                        else:
                            explanation = "Neue Daten werden heute Abend (Freitag) veröffentlicht."
                    else:
                        status = "🟡 Veraltet"
                        explanation = f"Der letzte Bericht ist {days_diff} Tage alt. Bitte warten Sie auf das nächste Update am Freitag/Samstag."
                        
                    return latest_date.strftime("%d.%m.%Y"), status, explanation
            except Exception:
                pass
            return None, "🔴 Nicht verfügbar", "Es konnten keine COT-Daten geladen werden."
    
        with st.expander("📅 COT-Status", expanded=False):
            rep_date, status_val, explanation_val = get_cot_data_status()
            st.write(f"**Status:** {status_val}")
            if rep_date:
                st.write(f"**Bericht vom:** {rep_date}")
            st.caption(explanation_val)
            
            try:
                y = datetime.now().year
                df_cot = load_cot_year_cached(y)
                if df_cot is None or df_cot.empty:
                    df_cot = load_cot_year_cached(y - 1)
                if df_cot is not None and not df_cot.empty:
                    cot_rows = []
                    for curr, code in COT_SYMBOLS.items():
                        rank = get_cot_signal(code, datetime.now().strftime("%Y-%m-%d"))
                        cot_rows.append({"Währung": curr, "Perzentil": f"{rank:.1f}%"})
                    st.dataframe(pd.DataFrame(cot_rows), hide_index=True)
            except Exception as e:
                st.error(f"Fehler bei Tabelle: {e}")
    
        with st.expander("🔑 API Key Status", expanded=False):
            st.caption("Geladene Schlüssel (Env / Secrets):")
            st.write(f"FRED_API_KEY: {'🟢 Aktiv' if FRED_KEY else '🔴 Fehlt'}")
            st.write(f"NEWSDATA_API_KEY: {'🟢 Aktiv' if NEWSDATA_KEY else '🔴 Fehlt'}")
            st.write(f"NEWSAPI_KEY: {'🟢 Aktiv' if NEWSAPI_KEY else '🔴 Fehlt'}")
            st.write(f"APIFREAKS_API_KEY: {'🟢 Aktiv' if APIFREAKS_KEY else '🔴 Fehlt'}")
            st.write(f"EODHD_API_KEY: {get_eodhd_status_label()}")
            st.write(f"ESTAT_APP_ID: {'🟢 Aktiv' if ESTAT_APP_ID else '🔴 Fehlt'}")
    
        with st.expander("📝 Streamlit Secrets Anleitung", expanded=False):
            st.markdown("""
            Wenn die App auf Streamlit Cloud läuft, tragen Sie Keys im Dashboard unter **Settings -> Secrets** ein:
            ```toml
            APIFREAKS_API_KEY = "IhrKey"
            FRED_API_KEY = "IhrKey"
            EODHD_API_KEY = "IhrKey"
            # ...
            ```
            """)
            
        df_cal, t_cal, is_live_cal = None, None, False
        st.caption("News-Termine werden manuell geprüft; automatische News-Abfragen sind deaktiviert.")
    
# ----------------- 4. GLOBAL DATA INITIALIZATION & FRESHNESS -----------------
    if invalid_pair:
        base_score = 50.0
        quote_score = 50.0
        signal_value = 0.0
        sig = "NT"
        badge = "NEUTRAL"
        latest_close = 0.0
        t_itick = None
        is_live_itick = False
    else:
        with st.spinner("Initialisiere globale Marktdaten..."):
            m_weights = st.session_state.get("active_live_model_weights") if hasattr(st, "session_state") else None
            _, _, base_core, _, b_details = compute_currency_professional_score_and_regime_custom(base_curr, m_weights)
            _, _, quote_core, _, q_details = compute_currency_professional_score_and_regime_custom(quote_curr, m_weights)
            base_score = base_core
            quote_score = quote_core
            
            badge, _, raw_diff, sig = get_pair_signal_and_badge(base_curr, quote_curr, m_weights)
            signal_value = raw_diff
                
            itick_data, t_itick, is_live_itick = get_itick_data(selected_pair, ITICK_KEY)
            latest_close = itick_data["close"] if itick_data else 0.0
    
    # ----------------- 5. HEADER SECTION -----------------
    st.title("FX Fundamental Dashboard")
    if not check_demo_active():
        from run_data_collection import maybe_start_live_fallback, LIVE_FALLBACK_KEYS
        fallback_status = maybe_start_live_fallback({key: load_api_key(key) for key in LIVE_FALLBACK_KEYS})
        if fallback_status in ("started", "running"):
            st.info("Der zentrale Ausfallersatz prüft die Live-Daten. Gültige Werte bleiben sichtbar; die Ansicht aktualisiert sich automatisch.")
        elif fallback_status == "cooldown":
            st.caption("Ausfallersatz: nächster Versuch frühestens 30 Minuten nach dem letzten Start. Datenfreigaben werden nicht verlängert.")
        elif fallback_status in ("failed", "timeout"):
            st.warning("Der Ausfallersatz konnte den Lauf nicht abschließen. Nächster Versuch nach 30 Minuten; die Anfragenzählung kann unvollständig sein. Nicht bestätigte Daten bleiben gesperrt.")
    # A full rerun rechecks every badge/table against the current clock and cache.
    # The timestamp belongs to this render, so the initial fragment call cannot
    # create a rerun loop. No provider requests are made by the timer itself.
    @st.fragment(run_every=30)
    def revalidate_open_live_view(rendered_at):
        refresh_live_view_if_due(rendered_at)

    revalidate_open_live_view(time.monotonic())
    live_data.render_status(st, operator_is_authorized())
    if st.session_state.get("demo_mode_chk", False):
        st.warning("⚠️ **DEMO MODE ACTIVE – DATA IS NOT REAL (using mock data)**")
    
    if invalid_pair:
        st.error("⚠️ **Ungültiges Währungspaar ausgewählt:** Basis- und Kurswährung müssen unterschiedlich sein. Bitte wählen Sie zwei verschiedene G10-Währungen in der Sidebar aus (z. B. USD/EUR).")
    else:
        base_details_raw = compute_currency_details(base_curr, None)
        quote_details_raw = compute_currency_details(quote_curr, None)
        base_comp = base_details_raw.get("_completeness", 100.0)
        quote_comp = quote_details_raw.get("_completeness", 100.0)
        pair_completeness = (base_comp + quote_comp) / 2.0
        
        if pair_completeness < 100.0:
            st.warning(f"⚠️ **CORE-Datenabdeckung: {base_curr} {base_comp:.0f}% · {quote_curr} {quote_comp:.0f}%**. "
                       f"Fehlend bei {base_curr}: {', '.join(base_details_raw.get('_missing', [])) or 'keine'}; "
                       f"bei {quote_curr}: {', '.join(quote_details_raw.get('_missing', [])) or 'keine'}. "
                       "Abdeckung ist keine Trefferwahrscheinlichkeit.")
        st.caption(f"Modell {CURRENT_MODEL_VERSION}: Paar-Signale nur bei 100% CORE-Abdeckung beider Währungen. Einzelwährungsanalysen bleiben ab 50% sichtbar.")
    
# ----------------- 6. TABS MODULES -----------------

def get_historical_yield_trends(series_id, dt_str, fred_key):
    try:
        val_now, _, _ = get_fred_data_historical(series_id, dt_str, fred_key)
        dt_1w = (pd.to_datetime(dt_str) - timedelta(days=7)).strftime("%Y-%m-%d")
        val_1w, _, _ = get_fred_data_historical(series_id, dt_1w, fred_key)
        dt_1m = (pd.to_datetime(dt_str) - timedelta(days=30)).strftime("%Y-%m-%d")
        val_1m, _, _ = get_fred_data_historical(series_id, dt_1m, fred_key)
        return val_now, val_1w, val_1m
    except Exception:
        return None, None, None

def get_yield_details(curr, series_map=None, fred_key=None):
    if series_map is None:
        series_map = YIELD_2Y_SERIES
    if fred_key is None:
        fred_key = FRED_KEY
    is_2y = (series_map == YIELD_2Y_SERIES)
    is_5y = (series_map == YIELD_5Y_SERIES)
    
    val_now, val_1w, val_1m = None, None, None
    source = "FRED"
    series_id_used = series_map.get(curr, "")
    
    if is_2y and use_live_core_cache():
        detail = live_data.details(curr)
        observation = detail.get("_observations", {}).get("Geldpolitik", {})
        value = finite_number(observation.get("yield_2y"))
        if detail.get("Geldpolitik") is None or value is None:
            return None
        return {"value": value, "chg_1w": None, "chg_1m": None,
                "trend": "N/A", "series_id": observation.get("series_id"),
                "source": observation.get("source") or "UNAVAILABLE",
                "date": observation.get("date")}

    if is_2y:
        val_now, dt_now, src_now = get_genuine_2y_yield_historical(curr, datetime.now().strftime("%Y-%m-%d"), fred_key, EODHD_KEY)
        if val_now is not None:
            dt_1w = (pd.to_datetime(dt_now) - timedelta(days=7)).strftime("%Y-%m-%d")
            val_1w, _, _ = get_genuine_2y_yield_historical(curr, dt_1w, fred_key, EODHD_KEY)
            dt_1m = (pd.to_datetime(dt_now) - timedelta(days=30)).strftime("%Y-%m-%d")
            val_1m, _, _ = get_genuine_2y_yield_historical(curr, dt_1m, fred_key, EODHD_KEY)
            source = src_now
            series_id_used = "DGS2" if src_now == "FRED" else f"{curr}2Y.GBOND"
            if curr == "EUR":
                series_id_used = "DE2Y.GBOND"
            elif curr == "CHF":
                series_id_used = "SW2Y.GBOND"
            elif curr == "GBP":
                series_id_used = "UK2Y.GBOND"
            elif curr == "JPY":
                series_id_used = "JP2Y.GBOND"
            elif curr == "CAD":
                series_id_used = "CA2Y.GBOND"
            elif curr == "AUD":
                series_id_used = "AU2Y.GBOND"
            elif curr == "NZD":
                series_id_used = "NZ2Y.GBOND"
            elif curr == "USD" and src_now != "FRED":
                series_id_used = "US2Y.GBOND"
    elif is_5y:
        val_now, dt_now, src_now = get_genuine_5y_yield_historical(curr, datetime.now().strftime("%Y-%m-%d"), fred_key, EODHD_KEY)
        if val_now is not None:
            dt_1w = (pd.to_datetime(dt_now) - timedelta(days=7)).strftime("%Y-%m-%d")
            val_1w, _, _ = get_genuine_5y_yield_historical(curr, dt_1w, fred_key, EODHD_KEY)
            dt_1m = (pd.to_datetime(dt_now) - timedelta(days=30)).strftime("%Y-%m-%d")
            val_1m, _, _ = get_genuine_5y_yield_historical(curr, dt_1m, fred_key, EODHD_KEY)
            source = src_now
            series_id_used = "DGS5" if curr == "USD" and "FRED" in src_now else f"{curr}5Y.GBOND"
            if curr == "EUR":
                series_id_used = "DE5Y.GBOND"
            elif curr == "GBP":
                series_id_used = "UK5Y.GBOND"
            elif curr == "CAD":
                series_id_used = "CA5Y.GBOND"
            elif curr == "AUD":
                series_id_used = "AU5Y.GBOND"
            elif curr == "NZD":
                series_id_used = "NZ5Y.GBOND"
            elif curr == "JPY":
                series_id_used = "JP5Y.GBOND"
            elif curr == "USD" and "EODHD" in src_now:
                series_id_used = "US5Y.GBOND"
    else:
        if series_id_used and fred_key:
            v_now, v_1w, v_1m = get_historical_yield_trends(series_id_used, datetime.now().strftime("%Y-%m-%d"), fred_key)
            val_now, val_1w, val_1m = v_now, v_1w, v_1m
            
    if val_now is None:
        return None
        
    chg_1w = val_now - val_1w if val_1w is not None else 0.0
    chg_1m = val_now - val_1m if val_1m is not None else 0.0
    trend = "▲" if chg_1w > 0 else "▼" if chg_1w < 0 else "▬"
    
    src_label = source
    if is_2y and curr == "EUR":
        src_label = "EODHD (Germany 2Y Benchmark)"
    elif is_5y and curr == "EUR":
        src_label = "EODHD (Germany 5Y Benchmark)"
        
    return {
        "value": val_now,
        "chg_1w": chg_1w,
        "chg_1m": chg_1m,
        "trend": trend,
        "series_id": series_id_used,
        "source": src_label,
        "date": datetime.now().strftime("%Y-%m-%d")
    }

def get_forward_rates_data(curr, target_date=None):
    if target_date is None:
        dt_str = datetime.now().strftime("%Y-%m-%d")
    else:
        try:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        except Exception:
            dt_str = datetime.now().strftime("%Y-%m-%d")
        
    fred_key = FRED_KEY
    
    # Get Current Policy Rate
    info = CURRENCIES.get(curr, {})
    wb_code = info.get("wb_code", "USA")
    policy_rate, _, _ = get_country_rate(wb_code, fred_key)
    
    # Get 2Y Yield
    y2_val, _, _ = get_genuine_2y_yield_historical(curr, dt_str, fred_key, EODHD_KEY)
        
    # Get OIS / Swap Rate (or proxy)
    swap_series = {
        "USD": "ISASOFRRATE1Y",
        "EUR": "ISAEURIBOR1Y",
        "GBP": "ISAGBPLIBOR1Y"
    }
    ois_val = None
    swap_id = swap_series.get(curr)
    if swap_id and fred_key:
        ois_val, _, _ = get_fred_data_historical(swap_id, dt_str, fred_key)
        
    # Calculate implied forward rate: f_1_1
    implied_forward = None
    expected_change = None
    
    if y2_val is not None and policy_rate is not None:
        try:
            y1_dec = policy_rate / 100.0
            y2_dec = y2_val / 100.0
            # Formula: f = (1 + y2)^2 / (1 + y1) - 1
            f11_dec = ((1.0 + y2_dec) ** 2) / (1.0 + y1_dec) - 1.0
            implied_forward = float(f11_dec * 100.0)
            expected_change = implied_forward - policy_rate
        except Exception:
            pass
            
    return {
        "policy_rate": policy_rate,
        "y2_yield": y2_val,
        "ois_rate": ois_val,
        "implied_forward": implied_forward,
        "expected_change": expected_change,
        "date": dt_str,
        "source": "FRED / Yield Curve Implied"
    }

def get_forward_rate_signal(base, quote, target_date=None):
    fd_base = get_forward_rates_data(base, target_date)
    fd_quote = get_forward_rates_data(quote, target_date)
    
    chg_b = fd_base.get("expected_change")
    chg_q = fd_quote.get("expected_change")
    
    if chg_b is None or chg_q is None:
        return "Neutral 🟡", 0.0, "N/A"
        
    expect_diff = chg_b - chg_q
    
    if expect_diff >= 1.5:
        sig = "Strong Bullish 🟢🟢"
    elif 0.5 <= expect_diff < 1.5:
        sig = "Bullish 🟢"
    elif -0.5 < expect_diff < 0.5:
        sig = "Neutral 🟡"
    elif -1.5 < expect_diff <= -0.5:
        sig = "Bearish 🔴"
    else:
        sig = "Strong Bearish 🔴🔴"
        
    return sig, expect_diff, f"{chg_b:+.2f}% vs {chg_q:+.2f}%"

def get_historical_forward_rates(curr, days=180, step=30):
    series_data = []
    end_date = datetime.now()
    for d in range(days, -1, -step):
        t_date = end_date - timedelta(days=d)
        t_date_str = t_date.strftime("%Y-%m-%d")
        try:
            fd = get_forward_rates_data(curr, t_date_str)
            if fd["implied_forward"] is not None:
                series_data.append({
                    "Datum": t_date,
                    "2Y Yield": fd["y2_yield"],
                    "Implied Forward": fd["implied_forward"],
                    "Policy Rate": fd["policy_rate"]
                })
        except Exception:
            pass
    return pd.DataFrame(series_data)

def get_inflation_expectations_data(curr, target_date=None):
    if target_date is None:
        dt_str = datetime.now().strftime("%Y-%m-%d")
    else:
        try:
            dt_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
        except Exception:
            dt_str = datetime.now().strftime("%Y-%m-%d")
            
    cpi_val, obs_date_cur, metric_type, source, series_id, freshness = get_cpi_yoy_details(curr, dt_str)
    
    cpi_trend = None
    if cpi_val is not None:
        lookback_days = 95 if curr in ["NZD"] else 35
        dt_prev = (pd.to_datetime(dt_str) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        cpi_prev, obs_date_prev, _, _, _, _ = get_cpi_yoy_details(curr, dt_prev)
        if (cpi_prev is None or obs_date_prev == obs_date_cur) and lookback_days < 95:
            dt_prev = (pd.to_datetime(dt_str) - timedelta(days=90)).strftime("%Y-%m-%d")
            cpi_prev, obs_date_prev, _, _, _, _ = get_cpi_yoy_details(curr, dt_prev)
            
        # Only compute trend if obs dates are different (prevents false 0.00% trends on same data)
        if cpi_prev is not None and obs_date_prev != obs_date_cur:
            cpi_trend = round(cpi_val - cpi_prev, 2)
            
    fred_key = FRED_KEY
    expect_id = OECD_INFLATION_EXP_SERIES.get(curr)
    expect_val = None
    if expect_id and fred_key:
        try:
            expect_val, _, _ = get_fred_data_historical(expect_id, dt_str, fred_key)
        except Exception:
            pass
            
    breakeven_val = None
    if curr == "USD" and fred_key:
        try:
            breakeven_val, _, _ = get_fred_data_historical("T10YIE", dt_str, fred_key)
        except Exception:
            pass
            
    return {
        "actual_cpi": cpi_val,
        "cpi_trend": cpi_trend,
        "oecd_expectation": expect_val,
        "market_breakeven": breakeven_val,
        "date": dt_str,
        "source": source
    }

def get_inflation_expectation_signal(base, quote, target_date=None):
    ed_base = get_inflation_expectations_data(base, target_date)
    ed_quote = get_inflation_expectations_data(quote, target_date)
    
    exp_b = ed_base.get("oecd_expectation")
    exp_q = ed_quote.get("oecd_expectation")
    
    if exp_b is None or exp_q is None:
        return "Neutral 🟡", 0.0, "N/A"
        
    diff = exp_b - exp_q
    
    if diff >= 1.0:
        sig = "Strong Inflationary 🔴🔴 (Base expects higher inflation)"
    elif 0.3 <= diff < 1.0:
        sig = "Inflationary 🔴"
    elif -0.3 < diff < 0.3:
        sig = "Neutral 🟡"
    elif -1.0 < diff <= -0.3:
        sig = "Disinflationary 🟢"
    else:
        sig = "Strong Disinflationary 🟢🟢 (Base expects lower inflation)"
        
    return sig, diff, f"{exp_b:.2f} vs {exp_q:.2f}"

def get_historical_inflation_expectations(curr, days=365, step=30):
    series_data = []
    end_date = datetime.now()
    for d in range(days, -1, -step):
        t_date = end_date - timedelta(days=d)
        t_date_str = t_date.strftime("%Y-%m-%d")
        try:
            ed = get_inflation_expectations_data(curr, t_date_str)
            if ed["oecd_expectation"] is not None:
                series_data.append({
                    "Datum": t_date,
                    "CPI": ed["actual_cpi"],
                    "Expectation": ed["oecd_expectation"],
                    "Breakeven": ed["market_breakeven"]
                })
        except Exception:
            pass
    return pd.DataFrame(series_data)

REAL_HISTORICAL_SURPRISES = [
    {"date": "2026-07-08", "country": "USA", "event": "Non-Farm Payrolls (NFP)", "actual": 206.0, "consensus": 190.0, "unit": "K", "importance": "High"},
    {"date": "2026-07-08", "country": "USA", "event": "Unemployment Rate", "actual": 4.1, "consensus": 4.0, "unit": "%", "importance": "High"},
    {"date": "2026-07-11", "country": "USA", "event": "CPI YoY", "actual": 3.0, "consensus": 3.1, "unit": "%", "importance": "High"},
    {"date": "2026-07-11", "country": "USA", "event": "Core CPI YoY", "actual": 3.3, "consensus": 3.4, "unit": "%", "importance": "High"},
    {"date": "2026-07-02", "country": "EUR", "event": "Eurozone CPI YoY", "actual": 2.5, "consensus": 2.5, "unit": "%", "importance": "High"},
    {"date": "2026-07-17", "country": "GBR", "event": "UK CPI YoY", "actual": 2.0, "consensus": 2.0, "unit": "%", "importance": "High"},
    {"date": "2026-07-18", "country": "JPN", "event": "Japan CPI YoY", "actual": 2.8, "consensus": 2.9, "unit": "%", "importance": "High"},
    {"date": "2026-07-04", "country": "CAN", "event": "Canada Unemployment Rate", "actual": 6.4, "consensus": 6.3, "unit": "%", "importance": "High"},
    {"date": "2026-07-11", "country": "AUS", "event": "Australia Unemployment Rate", "actual": 4.1, "consensus": 4.0, "unit": "%", "importance": "High"}
]

INDICATOR_SDEVS = {
    "cpi": 0.15,
    "pmi": 1.0,
    "gdp": 0.2,
    "unemployment": 0.1,
    "nfp": 30.0
}

def fetch_benzinga_history(key, date_from, date_to):
    try:
        url = f"https://api.benzinga.com/api/v2.1/calendar/economics?token={key}&parameters[date_from]={date_from}&parameters[date_to]={date_to}"
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=8)
        r.raise_for_status()
        res = r.json()
        calendar = res.get("economics", [])
        parsed = []
        for item in calendar:
            dt = item.get("date") or ""
            tm = item.get("time") or ""
            combined_time = f"{dt} {tm}".strip()
            
            act_val = item.get("actual")
            cons_val = item.get("consensus")
            prior_val = item.get("prior")
            
            parsed.append({
                "time": combined_time,
                "date": dt,
                "country": item.get("country") or "",
                "event": item.get("event_name") or "",
                "consensus": cons_val,
                "actual": act_val,
                "prior": prior_val,
                "importance": item.get("importance")
            })
        return pd.DataFrame(parsed)
    except Exception:
        return pd.DataFrame()

def parse_numeric_calendar_value(val_str):
    if val_str is None:
        return None
    val_cleaned = "".join([c for c in str(val_str) if c.isdigit() or c == "." or c == "-"])
    try:
        return float(val_cleaned)
    except ValueError:
        return None

def compute_currency_surprise_score(curr, halflife=5, target_date=None):
    if target_date is None:
        target_dt = datetime.now()
    else:
        try:
            target_dt = pd.to_datetime(target_date)
        except Exception:
            target_dt = datetime.now()
        
    releases = []
    curr_countries = {
        "USD": "USA",
        "EUR": "EUR",
        "GBP": "GBR",
        "JPY": "JPN",
        "CHF": "CHE",
        "CAD": "CAN",
        "AUD": "AUS",
        "NZD": "NZL"
    }
    country_code = curr_countries.get(curr, "USA")
    
    has_live = False
    if BENZINGA_KEY:
        try:
            date_from = (target_dt - timedelta(days=30)).strftime("%Y-%m-%d")
            date_to = target_dt.strftime("%Y-%m-%d")
            df_bz = fetch_benzinga_history(BENZINGA_KEY, date_from, date_to)
            if not df_bz.empty:
                has_live = True
                df_bz_curr = df_bz[df_bz["country"] == country_code]
                for _, row in df_bz_curr.iterrows():
                    act = parse_numeric_calendar_value(row["actual"])
                    cons = parse_numeric_calendar_value(row["consensus"])
                    if act is not None and cons is not None:
                        releases.append({
                            "date": row["date"],
                            "event": row["event"],
                            "actual": act,
                            "consensus": cons
                        })
        except Exception:
            pass
            
    if not has_live:
        for item in REAL_HISTORICAL_SURPRISES:
            if item["country"] == country_code:
                item_dt = pd.to_datetime(item["date"])
                if 0 <= (target_dt - item_dt).days <= 30:
                    releases.append({
                        "date": item["date"],
                        "event": item["event"],
                        "actual": item["actual"],
                        "consensus": item["consensus"]
                    })
                    
    if not releases:
        return 0.0, []
        
    weighted_scores = []
    for rel in releases:
        event_name = rel["event"].lower()
        actual = rel["actual"]
        consensus = rel["consensus"]
        rel_date = pd.to_datetime(rel["date"])
        age_days = (target_dt - rel_date).days
        if age_days < 0:
            continue
            
        sd = 1.0
        multiplier = 1.0
        
        if "cpi" in event_name or "inflation" in event_name:
            sd = INDICATOR_SDEVS["cpi"]
            multiplier = 1.0 
        elif "pmi" in event_name:
            sd = INDICATOR_SDEVS["pmi"]
            multiplier = 1.0
        elif "gdp" in event_name:
            sd = INDICATOR_SDEVS["gdp"]
            multiplier = 1.0
        elif "unemployment" in event_name or "arbeitslos" in event_name:
            sd = INDICATOR_SDEVS["unemployment"]
            multiplier = -1.0
        elif "payrolls" in event_name or "nfp" in event_name or "employment" in event_name:
            sd = INDICATOR_SDEVS["nfp"]
            multiplier = 1.0
            
        surprise = actual - consensus
        z_score = (surprise / sd) * multiplier
        
        decay_weight = np.exp(-np.log(2) * age_days / max(1, halflife))
        weighted_z = z_score * decay_weight
        
        weighted_scores.append({
            "event": rel["event"],
            "date": rel["date"],
            "actual": actual,
            "consensus": consensus,
            "surprise": surprise,
            "z_score": z_score,
            "weight": decay_weight,
            "weighted_z": weighted_z,
            "age": age_days
        })
        
    if not weighted_scores:
        return 0.0, []
        
    total_score = sum(item["weighted_z"] for item in weighted_scores)
    capped_score = float(np.clip(total_score * 2.0, -10.0, 10.0))
    
    return capped_score, weighted_scores

def load_live_signals():
    """Read stored evidence verbatim; a damaged file must never become an empty log."""
    file_path = "live_signals.json"
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        signals = json.load(f)
    if not isinstance(signals, dict):
        raise ValueError("INVALID_LIVE_SIGNALS_DOCUMENT")
    return signals


def save_live_signals(signals):
    """Atomically replace the log and surface write failures to the collector."""
    import tempfile
    file_path = "live_signals.json"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=".",
                                         prefix=".live_signals_", suffix=".tmp",
                                         delete=False) as f:
            temp_path = f.name
            json.dump(signals, f, indent=4, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _live_snapshot_weights():
    # Stored provenance must match the frozen BASE calculation, regardless of UI state.
    return {"Geldpolitik": 35.0, "Inflation": 20.0, "Arbeitsmarkt": 20.0,
            "PMI": 20.0, "GDP": 5.0, "ForwardRates": 0.0,
            "InflationExpectations": 0.0, "EconomicSurprises": 0.0,
            "BCI": 0.0, "Correction": 100.0}


def _live_run_summary():
    return {"status": "SUCCESS", "attempted": 0, "written": 0, "updated": 0,
            "skipped": 0, "errors": 0, "issues": []}


def _finish_live_summary(summary):
    if (summary["errors"] and summary["errors"] == summary["attempted"]
            and not summary["written"] and not summary["updated"]):
        summary["status"] = "FAILED"
    elif any(issue.get("severity", "warning") != "info" for issue in summary["issues"]):
        summary["status"] = "PARTIAL"
    return summary


def _live_positive_price(value):
    try:
        return not isinstance(value, bool) and bool(np.isfinite(float(value))) and float(value) > 0.0
    except (TypeError, ValueError, OverflowError):
        return False


def _live_price_history(df, today_str):
    """Keep observed daily bars in order without filling gaps or inventing prices."""
    if df is None or df.empty or not {"date", "close"}.issubset(df.columns):
        raise ValueError("PRICE_HISTORY_UNAVAILABLE")
    history = df.copy()
    history["date"] = pd.to_datetime(history["date"], errors="coerce", utc=True)
    if history["date"].isna().any():
        raise ValueError("INVALID_PRICE_DATE")
    history["date_str"] = history["date"].dt.strftime("%Y-%m-%d")
    history = history[(history["date_str"] <= today_str) & (history["date"].dt.dayofweek < 5)]
    if history["date_str"].duplicated().any():
        raise ValueError("DUPLICATE_DAILY_PRICE_BAR")
    return history.sort_values("date").reset_index(drop=True)


def compute_checklist_snapshot(model_weights):
    checklist = []
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY"]
    for pair in pairs:
        base, quote = pair.split("/")
        try:
            badge, _, divergence, code = get_pair_signal_and_badge(base, quote, model_weights)
            _, b_reg, _, _, b_details = compute_currency_professional_score_and_regime_custom(base, model_weights)
            _, _, _, _, q_details = compute_currency_professional_score_and_regime_custom(quote, model_weights)
            
            b_comp = b_details.get("_completeness", 0.0) if b_details else 0.0
            q_comp = q_details.get("_completeness", 0.0) if q_details else 0.0
            dq = (b_comp + q_comp) / 2.0
            
            if divergence is None:
                sig_text = "INSUFFICIENT DATA"
                sig_strength = "N/A"
                diff_val = None
                conf = None
            else:
                sig_text = badge
                sig_strength = "STARK" if abs(divergence) >= 50.0 else "MITTEL" if abs(divergence) >= 20.0 else "SCHWACH"
                diff_val = round(divergence, 1)
                conf = None  # Magnitude is not a calibrated success probability.
                
            checklist.append({
                "pair": pair,
                "signal": sig_text,
                "signal_strength": sig_strength,
                "divergence": diff_val,
                "confidence": conf,
                "data_quality": dq,
                "regime": b_reg
            })
        except Exception:
            pass
    return checklist

def save_live_signal_snapshot(selected_pair, base_curr, quote_curr, base_score, quote_score, signal_value, badge, latest_close, entry_price_date=None, snapshot_date=None):
    model_name = "CORE v1 - Baseline"
    model_weights = _live_snapshot_weights()

    today_str = snapshot_date or datetime.now().strftime("%Y-%m-%d")
    signals = load_live_signals()
    snapshot_id = f"PAIR_{selected_pair.replace('/', '')}_{today_str}_{CURRENT_MODEL_VERSION}"
    # A rerun must never rewrite the signal, entry, or completed outcomes of this day.
    if snapshot_id in signals:
        return False
    for s_data in signals.values():
        if not isinstance(s_data, dict):
            continue
        meta = s_data.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        if (meta.get("pair") == selected_pair and meta.get("date") == today_str
                and meta.get("model_version") == CURRENT_MODEL_VERSION):
            return False
    if not _live_positive_price(latest_close):
        raise ValueError("INVALID_ENTRY_PRICE")
    if entry_price_date != today_str:
        raise ValueError("ENTRY_PRICE_DATE_MISMATCH")
    if any(finite_number(v) is None for v in [base_score, quote_score, signal_value]):
        raise ValueError("INSUFFICIENT_CORE_DATA")

    # Both currencies and their scores must describe one checked collector run.
    dataset = live_data.load()
    checked_now = live_data.now_utc()
    base_details_raw = live_data.details(base_curr, now=checked_now, data=dataset)
    quote_details_raw = live_data.details(quote_curr, now=checked_now, data=dataset)
    if not pair_core_is_complete(base_details_raw) or not pair_core_is_complete(quote_details_raw):
        raise ValueError("PAIR_REQUIRES_COMPLETE_CORE")
    factors = ("Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP")
    total_weight = sum(model_weights[factor] / 100.0 for factor in factors)
    checked_scores = [sum(details[factor] * (model_weights[factor] / 100.0) for factor in factors) / total_weight
                      for details in (base_details_raw, quote_details_raw)]
    checked_divergence = checked_scores[0] - checked_scores[1]
    checked_badge = ("STRONG BUY" if checked_divergence >= 50.0 else
                     "MID BUY" if checked_divergence >= 20.0 else
                     "NEUTRAL" if checked_divergence > -20.0 else
                     "MID SELL" if checked_divergence > -50.0 else "STRONG SELL")
    if (any(not np.isclose(float(given), actual, rtol=0.0, atol=1e-9)
            for given, actual in zip((base_score, quote_score, signal_value), (*checked_scores, checked_divergence)))
            or badge != checked_badge):
        raise ValueError("PAIR_SNAPSHOT_SIGNAL_MISMATCH")
    base_score, quote_score = checked_scores
    signal_value, badge = checked_divergence, checked_badge
    checklist_copy = compute_checklist_snapshot(model_weights)
    
    def get_effective_weights(details, w):
        av_factors = {}
        for k in ["Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP"]:
            if details.get(k) is not None:
                av_factors[k] = w.get(k, 0.0)
        if w.get("BCI", 0.0) > 0.0 and details.get("BCI") is not None:
            av_factors["BCI"] = w.get("BCI", 0.0)
        total_w = sum(av_factors.values())
        eff = {}
        for k, v in av_factors.items():
            eff[k] = (v / total_w * 100.0) if total_w > 0 else 0.0
        return eff
        
    base_eff_weights = get_effective_weights(base_details_raw, model_weights)
    quote_eff_weights = get_effective_weights(quote_details_raw, model_weights)
    
    vix_val = None
    try:
        vix_val = get_vix_value(today_str)
    except Exception:
        pass
        
    oil_val = None
    try:
        oil_val = get_oil_price(today_str)
    except Exception:
        pass
        
    milk_val = None
    try:
        milk_val = get_milk_price(today_str)
    except Exception:
        pass
        
    snapshot = {
        "metadata": {
            "snapshot_id": snapshot_id,
            "date": today_str,
            "entry_price_date": entry_price_date,
            "time": datetime.now().strftime("%H:%M:%S"),
            "timezone": str(datetime.now().astimezone().tzinfo),
            "pair": selected_pair,
            "base_currency": base_curr,
            "quote_currency": quote_curr,
            "core_model_name": model_name,
            "core_model_weights": model_weights,
            "model_version": CURRENT_MODEL_VERSION,
            "app_version": "v1.0"
        },
        "pair_signal": {
            "base_core": float(base_score),
            "quote_core": float(quote_score),
            "divergence": float(signal_value),
            "final_score": float(signal_value),
            "signal": badge,
            "signal_strength": "STARK" if abs(signal_value) >= 50.0 else "MITTEL" if abs(signal_value) >= 20.0 else "SCHWACH",
            "confidence": None,  # Score magnitude is not a calibrated success probability.
            "regime": base_details_raw.get("regime", "Normal"),
            "risk_on_off": "Unbekannt (VIX nicht geprüft)" if vix_val is None else "Risk-Off" if vix_val > 22.0 else "Risk-On",
            "data_quality": (base_details_raw.get("_completeness", 100.0) + quote_details_raw.get("_completeness", 100.0)) / 2.0
        },
        "base_currency_details": {
            "total_core_score": float(base_score),
            "factor_scores": {k: float(v) if v is not None else None for k, v in base_details_raw.items() if not k.startswith("_")},
            "original_weights": model_weights,
            "effective_weights": base_eff_weights,
            "data_quality": base_details_raw.get("_completeness", 100.0),
            "missing_factors": base_details_raw.get("_missing", []),
            "data_quality_status": "🟢 VALID" if base_details_raw.get("_completeness", 100.0) == 100.0 else "🟡 PARTIAL"
        },
        "quote_currency_details": {
            "total_core_score": float(quote_score),
            "factor_scores": {k: float(v) if v is not None else None for k, v in quote_details_raw.items() if not k.startswith("_")},
            "original_weights": model_weights,
            "effective_weights": quote_eff_weights,
            "data_quality": quote_details_raw.get("_completeness", 100.0),
            "missing_factors": quote_details_raw.get("_missing", []),
            "data_quality_status": "🟢 VALID" if quote_details_raw.get("_completeness", 100.0) == 100.0 else "🟡 PARTIAL"
        },
        "market_context": {
            "vix": vix_val,
            "oil": oil_val,
            "milk": milk_val
        },
        "checklist_snapshot": checklist_copy,
        "outcome_status": "OPEN",
        "entry_price": float(latest_close),
        "outcomes": {
            str(n): {
                "exit_price": None,
                "exit_date": None,
                "return_pct": None,
                "directional_return_pct": None,
                "status": None,
                "mfe": None,
                "mae": None
            } for n in [1, 3, 5, 10, 15, 20]
        }
    }
    
    signals[snapshot_id] = snapshot
    save_live_signals(signals)
    return True

def update_open_outcomes():
    """Append observed outcomes only; never repair historical signals or entry prices."""
    signals = load_live_signals()
    summary = _live_run_summary()
    changed = False
    today_str = datetime.now().strftime("%Y-%m-%d")
    histories = {}
    for s_id, s_data in signals.items():
        if isinstance(s_data, dict) and (s_id.startswith("CURR_") or s_data.get("type") == "CURRENCY"):
            continue
        if isinstance(s_data, dict) and s_data.get("outcome_status", "OPEN") != "OPEN":
            continue
        summary["attempted"] += 1
        try:
            if not isinstance(s_data, dict):
                raise ValueError("INVALID_SNAPSHOT")
            meta = s_data.get("metadata") or {}
            if meta.get("type") == "currency":
                summary["attempted"] -= 1
                continue
            entry_price = s_data.get("entry_price")
            if not _live_positive_price(entry_price):
                s_data["outcome_status"] = "INVALID"
                s_data["outcome_error"] = "INVALID_ENTRY_PRICE"
                changed = True
                summary["errors"] += 1
                summary["issues"].append({"item": s_id, "reason": "INVALID_ENTRY_PRICE", "severity": "error"})
                continue
            entry_price = float(entry_price)
            pair = meta.get("pair")
            if not pair:
                raise ValueError("MISSING_PAIR")
            if pair not in histories:
                try:
                    df, _, observed = get_fcs_history_data(pair, FCS_KEY)
                    if not observed:
                        raise ValueError("PRICE_HISTORY_UNAVAILABLE")
                    histories[pair] = _live_price_history(df, today_str)
                except Exception:
                    histories[pair] = None
            df = histories[pair]
            if df is None or df.empty:
                summary["skipped"] += 1
                summary["issues"].append({"item": s_id, "reason": "PRICE_HISTORY_UNAVAILABLE", "severity": "warning"})
                continue
            entry_date = meta.get("entry_price_date") or meta.get("date")
            matches = df.index[df["date_str"] == entry_date]
            if not len(matches):
                # No next-date fallback: that would silently move the original entry.
                summary["skipped"] += 1
                summary["issues"].append({"item": s_id, "reason": "ENTRY_PRICE_DATE_UNAVAILABLE", "severity": "warning"})
                continue
            idx = int(matches[0])
            badge = str(s_data.get("pair_signal", {}).get("signal", ""))
            direction = "LONG" if "BUY" in badge else "SHORT" if "SELL" in badge else None
            outcomes = s_data.get("outcomes")
            if not isinstance(outcomes, dict) or not outcomes:
                raise ValueError("INVALID_OUTCOMES")
            row_updated = False
            row_errors = False
            for n_str, out_data in outcomes.items():
                try:
                    n = int(n_str)
                    if n not in [1, 3, 5, 10, 15, 20] or not isinstance(out_data, dict):
                        raise ValueError("INVALID_OUTCOME_HORIZON")
                    if out_data.get("exit_price") is not None:
                        continue
                    target_idx = idx + n
                    if target_idx >= len(df):
                        continue
                    exit_row = df.iloc[target_idx]
                    exit_price = exit_row["close"]
                    if not _live_positive_price(exit_price):
                        raise ValueError("INVALID_EXIT_PRICE")
                    exit_price = float(exit_price)
                    raw_ret = (exit_price - entry_price) / entry_price * 100.0
                    if not np.isfinite(raw_ret):
                        raise ValueError("INVALID_OUTCOME_RETURN")
                    dir_ret = raw_ret if direction == "LONG" else -raw_ret if direction == "SHORT" else None
                    max_fav = max_adv = None
                    if direction:
                        max_fav = max_adv = 0.0
                        for _, bar in df.iloc[idx + 1:target_idx + 1].iterrows():
                            high, low = bar.get("high"), bar.get("low")
                            close = bar.get("close")
                            if not all(_live_positive_price(v) for v in (high, low, close)):
                                raise ValueError("INVALID_OUTCOME_PRICE_BAR")
                            high, low, close = float(high), float(low), float(close)
                            if not low <= close <= high:
                                raise ValueError("INVALID_OUTCOME_PRICE_BAR")
                            fav = (high - entry_price) / entry_price * 100.0 if direction == "LONG" else (entry_price - low) / entry_price * 100.0
                            adv = (low - entry_price) / entry_price * 100.0 if direction == "LONG" else (entry_price - high) / entry_price * 100.0
                            if not np.isfinite(fav) or not np.isfinite(adv):
                                raise ValueError("INVALID_OUTCOME_RETURN")
                            max_fav, max_adv = max(max_fav, fav), min(max_adv, adv)
                    out_data.update({
                        "exit_price": exit_price, "exit_date": exit_row["date_str"],
                        "return_pct": round(raw_ret, 3),
                        "directional_return_pct": round(dir_ret, 3) if dir_ret is not None else None,
                        "status": ("CORRECT" if dir_ret > 0 else "WRONG" if dir_ret < 0 else "NEUTRAL") if dir_ret is not None else "NO_TRADE",
                        "mfe": round(max_fav, 3) if max_fav is not None else None,
                        "mae": round(max_adv, 3) if max_adv is not None else None,
                    })
                    row_updated = changed = True
                except Exception:
                    row_errors = True
                    summary["issues"].append({"item": s_id, "reason": "INVALID_OUTCOME_DATA", "horizon": str(n_str), "severity": "error"})
            if row_errors:
                summary["errors"] += 1
            if all(isinstance(o, dict) and o.get("exit_price") is not None for o in outcomes.values()) and not row_errors:
                s_data["outcome_status"] = "COMPLETED"
                row_updated = changed = True
            if row_updated:
                summary["updated"] += 1
            elif not row_errors:
                summary["skipped"] += 1
        except Exception:
            summary["errors"] += 1
            summary["issues"].append({"item": s_id, "reason": "INVALID_SNAPSHOT", "severity": "error"})
    if changed:
        save_live_signals(signals)
    return _finish_live_summary(summary)


def save_currency_snapshot(curr, total_score, core_score, corr_score, regime, details, model_weights, today_str):
    signals = load_live_signals()
    snap_id = f"CURR_{curr}_{today_str}_{CURRENT_MODEL_VERSION}"
    if snap_id in signals:
        return False
    details = details or {}
    model_weights = _live_snapshot_weights()
    
    eff_weights = {}
    if details and "_completeness" in details:
        active_factors = [k for k in ["Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP"] if k not in details.get("_missing", [])]
        tot_w = sum(model_weights.get(k, 0.0) for k in active_factors)
        if tot_w > 0:
            eff_weights = {k: round(model_weights.get(k, 0.0) / tot_w * 100.0, 1) for k in active_factors}
            
    cpi_val, obs_date, metric_type, source, series_id, freshness = get_cpi_yoy_details(curr, today_str)
    inf_data = get_inflation_expectations_data(curr, today_str)
    c_trend = inf_data.get("cpi_trend")
    c_trend_str = "↑ RISING" if (c_trend is not None and c_trend > 0.05) else "↓ FALLING" if (c_trend is not None and c_trend < -0.05) else "→ STABLE" if c_trend is not None else "N/A"

    # Dynamic CPI release date, dataset, metric calculation, and PIT status resolution
    cpi_dataset_val = series_id
    cpi_release_date_val = obs_date
    if curr == "JPY":
        res_j = get_estat_cpi_data()
        stats_id_used = "0004052037"
        if res_j is not None and res_j[0] is not None and not res_j[0].empty:
            stats_id_used = res_j[0].iloc[-1].get("stats_id", "0004052037")
            rel_dt = res_j[0].iloc[-1].get("release_date")
            if rel_dt is not None:
                cpi_release_date_val = pd.to_datetime(rel_dt).strftime("%Y-%m-%d")
        cpi_dataset_val = stats_id_used
    elif curr == "NZD":
        cpi_dataset_val = "CS_ECONOMY / CAT_PRICE_INDEXES"
        res_n = get_statsnz_cpi_data()
        if res_n is not None and res_n[0] is not None and not res_n[0].empty:
            rel_dt = res_n[0].iloc[-1].get("release_date")
            if rel_dt is not None:
                cpi_release_date_val = pd.to_datetime(rel_dt).strftime("%Y-%m-%d")
    elif curr == "CAD":
        cpi_dataset_val = "v41690973"
    elif curr == "AUD":
        cpi_dataset_val = "3.10001.10.50.M"
    elif curr == "GBP":
        cpi_dataset_val = "D7G7"
    elif curr == "USD":
        cpi_dataset_val = "CPIAUCNS"
        
    if curr == "USD":
        cpi_method_val = "DIRECT_FRED_PC1"
    elif curr in ["GBP", "AUD", "JPY", "NZD"]:
        cpi_method_val = "DIRECT_OFFICIAL"
    else:
        cpi_method_val = "DERIVED_FROM_INDEX"
        
    cpi_pit_status_val = "PIT_STRICT" if "PIT_STRICT" in str(freshness) else "PIT_LIMITED"

    # Get raw values for saving
    pol_meta = get_verified_policy_rate(curr)
    raw_values = {
        "policy_rate": pol_meta.get("rate"),
        "policy_rate_previous": pol_meta.get("previous_rate"),
        "policy_rate_instrument": pol_meta.get("instrument"),
        "policy_rate_effective_date": pol_meta.get("rate_effective_date"),
        "policy_rate_last_decision_date": pol_meta.get("last_policy_decision_date"),
        "policy_rate_verified_at": pol_meta.get("verified_at"),
        "policy_rate_primary_source": pol_meta.get("primary_source"),
        "policy_rate_secondary_source": pol_meta.get("secondary_source"),
        "policy_rate_verification_status": pol_meta.get("verification_status", pol_meta.get("status")),
        "policy_rate_verification_evidence": pol_meta.get("verification_evidence", []),
        "yield_2y": float(get_genuine_2y_yield_historical(curr, today_str)[0]) if get_genuine_2y_yield_historical(curr, today_str)[0] is not None else None,
        "yield_5y": float(get_genuine_5y_yield_historical(curr, today_str)[0]) if get_genuine_5y_yield_historical(curr, today_str)[0] is not None else None,
        "cpi_yoy": cpi_val,
        "unrate": get_unemployment_value(curr, today_str),
        "gdp_yoy": get_gdp_yoy_value(curr, today_str),
        
        # CPI Snapshot metadata
        "cpi_value": cpi_val,
        "cpi_metric_type": metric_type,
        "cpi_source": source,
        "cpi_dataset": cpi_dataset_val,
        "cpi_series": series_id,
        "cpi_geography": "00000" if curr == "JPY" else "National" if curr in ["NZD", "AUD", "CAD", "GBP"] else curr,
        "cpi_observation_date": obs_date,
        "cpi_release_date": cpi_release_date_val,
        "cpi_frequency": "quarterly" if curr == "NZD" else "monthly",
        "cpi_freshness": freshness,
        "cpi_change_pp": c_trend,
        "cpi_trend": c_trend_str,
        "cpi_pit_status": cpi_pit_status_val,
        "metric_calculation": cpi_method_val
    }

    snapshot = {
        "type": "CURRENCY",
        "currency": curr,
        "date": today_str,
        "time": datetime.now().strftime("%H:%M:%S"),
        "model_version": CURRENT_MODEL_VERSION,
        "schema_version": "2.0",
        "total_score": float(total_score) if total_score is not None else None,
        "core_score": float(core_score) if core_score is not None else None,
        "core_status": "INSUFFICIENT DATA" if (core_score is None or details.get("_completeness", 100.0) < 50.0) else "VALID",
        "diagnostic_partial_score": float(details.get("_diagnostic_partial_score", 0.0)) if (details and "_diagnostic_partial_score" in details and details["_diagnostic_partial_score"] is not None) else None,
        "correction_score": float(corr_score) if corr_score is not None else None,
        "trend_score": float(details.get("_trend_score", 0.0)) if details.get("_trend_score", 0.0) is not None else None,
        "surprise_score": float(details.get("_surprise_score", 0.0)) if details.get("_surprise_score", 0.0) is not None else None,
        "regime": regime,
        "factor_scores": {k: float(v) if v is not None else None for k, v in details.items() if not k.startswith("_")},
        "original_weights": model_weights,
        "effective_weights": eff_weights,
        "data_quality": details.get("_completeness", 100.0) if details else 100.0,
        "missing_factors": details.get("_missing", []) if details else [],
        "freshness": details.get("_freshness", {}) if details else {},
        "raw_values": raw_values,
        "trend_details": details.get("_trend_details", {}) if details else {},
        "surprise_details": details.get("_surprise_details", {}) if details else {},
        
        # Root level V2.2 snapshot keys
        "cpi_value": cpi_val,
        "cpi_metric_type": metric_type,
        "cpi_source": source,
        "cpi_series": series_id,
        "cpi_observation_date": obs_date,
        "cpi_release_date": cpi_release_date_val,
        "cpi_freshness": freshness,
        "cpi_change_pp": c_trend,
        "cpi_trend": c_trend_str
    }
    signals[snap_id] = snapshot
    save_live_signals(signals)
    return True

def save_all_g10_live_snapshots():
    model_weights = _live_snapshot_weights()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    summary = _live_run_summary()
    summary["currencies"] = _live_run_summary()
    summary["pairs"] = _live_run_summary()
    if getattr(st, "_mock_mode", False) or check_demo_active():
        summary["issues"].append({"item": "collector", "reason": "LIVE_COLLECTION_DISABLED", "severity": "warning"})
        return _finish_live_summary(summary)

    for curr in CURRENCIES.keys():
        child = summary["currencies"]
        child["attempted"] += 1
        try:
            c_score, c_reg, c_core, c_corr, c_details = compute_currency_professional_score_and_regime_custom(curr, model_weights)
            written = save_currency_snapshot(curr, c_score, c_core, c_corr, c_reg, c_details, model_weights, today_str)
            child["written" if written else "skipped"] += 1
        except Exception:
            child["errors"] += 1
            child["issues"].append({"item": curr, "reason": "CURRENCY_SNAPSHOT_FAILED", "severity": "error"})

    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY"]
    for pair in pairs:
        child = summary["pairs"]
        child["attempted"] += 1
        try:
            snapshot_id = f"PAIR_{pair.replace('/', '')}_{today_str}_{CURRENT_MODEL_VERSION}"
            if snapshot_id in load_live_signals() or pd.Timestamp(today_str).dayofweek >= 5:
                child["skipped"] += 1
                continue
            base, quote = pair.split("/")
            _, _, b_core, _, _ = compute_currency_professional_score_and_regime_custom(base, model_weights)
            _, _, q_core, _, _ = compute_currency_professional_score_and_regime_custom(quote, model_weights)
            badge, _, divergence, _ = get_pair_signal_and_badge(base, quote, model_weights)
            if divergence is None or b_core is None or q_core is None:
                child["skipped"] += 1
                continue
            df, _, observed = get_fcs_history_data(pair, FCS_KEY)
            if not observed or df is None or df.empty:
                child["skipped"] += 1
                child["issues"].append({"item": pair, "reason": "ENTRY_PRICE_UNAVAILABLE", "severity": "warning"})
                continue
            history = _live_price_history(df, today_str)
            if history.empty or history.iloc[-1]["date_str"] != today_str:
                child["skipped"] += 1
                child["issues"].append({"item": pair, "reason": "ENTRY_PRICE_STALE", "severity": "warning"})
                continue
            latest = history.iloc[-1]
            written = save_live_signal_snapshot(pair, base, quote, b_core, q_core, divergence, badge,
                                                latest["close"], entry_price_date=latest["date_str"], snapshot_date=today_str)
            child["written" if written else "skipped"] += 1
        except Exception:
            child["errors"] += 1
            child["issues"].append({"item": pair, "reason": "PAIR_SNAPSHOT_FAILED", "severity": "error"})
    for name in ("currencies", "pairs"):
        child = _finish_live_summary(summary[name])
        for key in ("attempted", "written", "updated", "skipped", "errors"):
            summary[key] += child[key]
        summary["issues"].extend(child["issues"])
    return _finish_live_summary(summary)

# Daily G10 snapshots & outcome updates are automatically executed by the GitHub Actions workflow scheduler

if not getattr(st, "_mock_mode", False):
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11, tab12, tab13 = st.tabs([
        "🏆 Currency Strength Overview",
        "📊 Fundamental Score",
        "🏦 Interest Rates & 2Y Yields",
        "📈 Inflation / CPI",
        "👷 Labour Market",
        "📊 PMI (Mfg & Svc)",
        "📈 GDP Growth",
        "🌎 Market Regime & Macro Factors",
        "📅 Manual News Check",
        "💱 FX Pair Divergence Analyzer",
        "📍 Positioning (COT)",
        "📈 Live Signal History & Outcomes",
        "📊 Backtesting & Model Lab"
    ])
    
    # ----------------- TAB 1: CURRENCY STRENGTH OVERVIEW -----------------
    with tab1:
        st.header("🏆 Currency Strength & Fundamental Overview")
        st.caption("Primäre fundamentale Stärkeanalyse der 8 G8-Währungen (Strongest ➔ Weakest). Identifizieren Sie divergierende Währungen für die anschließende Paarbildung.")
        
        st.info("💡 **3-Schritte Fundamentalanalyse Workflow:**\n"
                "1. **Einzelwährungen analysieren:** Stärkste Währung (🟢 Bullish) und schwächste Währung (🔴 Bearish) im Ranking identifizieren.\n"
                "2. **Währungspaar selbst auswählen:** Starke Basis gegen schwache Quote kombinieren (z. B. USD stark vs. JPY schwach ➔ Long USD/JPY).\n"
                "3. **Manuelle News-Prüfung:** Vor Trade-Einstieg manuelle Prüfung wichtiger Wirtschaftsdaten auf Forex Factory im Tab *📅 Manual News Check*.")
                
        # Compute scores for all 8 currencies
        g8_data = {}
        for curr in CURRENCIES.keys():
            f_score, regime, core_score, corr_score, cat_scores = compute_currency_professional_score_and_regime(curr)
            details = cat_scores
            g8_data[curr] = {
                "score": f_score,
                "core": core_score,
                "corr": corr_score,
                "regime": regime,
                "categories": cat_scores,
                "details": details
            }
            
        sorted_curr_keys = sorted(CURRENCIES.keys(), key=lambda k: g8_data[k]["core"] if g8_data[k]["core"] is not None else -999.0, reverse=True)
        
        valid_scores = [k for k in CURRENCIES.keys() if g8_data[k]["core"] is not None]
        if valid_scores:
            sorted_valid = sorted(valid_scores, key=lambda k: g8_data[k]["core"], reverse=True)
            strongest_c = sorted_valid[0]
            weakest_c = sorted_valid[-1]
        else:
            strongest_c = "N/A"
            weakest_c = "N/A"
            
        vix = get_vix_value()
        cpi_us = get_cpi_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        gdp_us = get_gdp_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        
        if vix is None:
            global_regime = "Unbekannt · VIX nicht geprüft"
        elif vix > 22.0:
            global_regime = "Risk-Off 🛡️"
        elif vix < 14.0 and (gdp_us is not None and gdp_us > 1.5):
            global_regime = "Risk-On / Growth 🚀"
        elif (cpi_us is not None and cpi_us > 3.0) and (gdp_us is not None and gdp_us < 1.0):
            global_regime = "Stagflation ⚠️"
        else:
            global_regime = "Normales Marktregime 🟡"
            
        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        with m_col1:
            if strongest_c != "N/A":
                st.metric("🟢 Stärkste Währung (BASE CORE)", f"{CURRENCIES[strongest_c]['flag']} {strongest_c} ({g8_data[strongest_c]['core']:+.1f})")
            else:
                st.metric("🟢 Stärkste Währung (BASE CORE)", "N/A (Insufficient Data)")
        with m_col2:
            if weakest_c != "N/A":
                st.metric("🔴 Schwächste Währung (BASE CORE)", f"{CURRENCIES[weakest_c]['flag']} {weakest_c} ({g8_data[weakest_c]['core']:+.1f})")
            else:
                st.metric("🔴 Schwächste Währung (BASE CORE)", "N/A (Insufficient Data)")
        with m_col3:
            st.metric("Globale Marktphase", f"{global_regime} (VIX: {vix:.1f})" if vix is not None else global_regime)
        with m_col4:
            st.metric("Modell-Baseline", "35/20/20/20/5 (Yield/CPI/Lab/PMI/GDP)")
            
        st.write("")
        
        # Visual horizontal bar chart based purely on BASE CORE
        plot_keys = [k for k in sorted_curr_keys if g8_data[k]["core"] is not None]
        if plot_keys:
            fig_rank = go.Figure(go.Bar(
                x=[g8_data[k]["core"] for k in reversed(plot_keys)],
                y=[f"{CURRENCIES[k]['flag']} {k}" for k in reversed(plot_keys)],
                orientation='h',
                marker=dict(
                    color=['#10b981' if g8_data[k]["core"] >= 20.0 else '#f87171' if g8_data[k]["core"] <= -20.0 else '#e2b13c' for k in reversed(plot_keys)]
                ),
                text=[f"{g8_data[k]['core']:+.1f}" for k in reversed(plot_keys)],
                textposition='outside'
            ))
            fig_rank.update_layout(
                title="<b>BASE CORE Ranking (Strongest ➔ Weakest)</b>",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font=dict(color="#d1d5db", size=12),
                xaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.1)', zeroline=True, zerolinecolor='#4b5563'),
                yaxis=dict(showgrid=False),
                height=340,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_rank, use_container_width=True)
        else:
            st.warning("⚠️ Keine ausreichenden Daten zur Erstellung des Rankings vorhanden (Coverage < 50%).")
            
        st.subheader("📋 Einzelwährungs-Fundamentaltabelle (G8)")
        table_rows = []
        for rank_idx, curr in enumerate(sorted_curr_keys, 1):
            d = g8_data[curr]
            det = d["details"]
            cats = d["categories"]
            
            comp = det.get("_completeness", 100.0)
            missing = det.get("_missing", [])
            
            if d["core"] is None:
                badge_str = "⚪ INSUFFICIENT DATA"
            elif d["core"] >= 50.0:
                badge_str = "🟢 STRONG BULLISH"
            elif d["core"] >= 20.0:
                badge_str = "🟢 BULLISH"
            elif d["core"] > -20.0:
                badge_str = "🟡 NEUTRAL"
            elif d["core"] > -50.0:
                badge_str = "🔴 BEARISH"
            else:
                badge_str = "🔴 STRONG BEARISH"
                
            status_dq = "🟢 100%" if comp == 100.0 else f"🟡 {comp:.0f}% ({', '.join(missing)})"
            
            table_rows.append({
                "Rang": f"#{rank_idx}" if d["core"] is not None else "—",
                "Währung": f"{CURRENCIES[curr]['flag']} {curr}",
                "CORE Score": f"{d['core']:+.1f}" if d['core'] is not None else "N/A",
                "CORE-Abdeckung": f"{comp:.0f}%",
                "Gesamt-Score": f"{d['score']:+.1f}" if d['score'] is not None else "N/A",
                "Signal / Tendenz": badge_str,
                "Geldpolitik (35%)": f"{cats.get('Geldpolitik', 0.0):+.1f}" if cats.get('Geldpolitik') is not None else "N/A",
                "Inflation (20%)": f"{cats.get('Inflation', 0.0):+.1f}" if cats.get('Inflation') is not None else "N/A",
                "Arbeitsmarkt (20%)": f"{cats.get('Arbeitsmarkt', 0.0):+.1f}" if cats.get('Arbeitsmarkt') is not None else "N/A",
                "PMI (20%)": f"{cats.get('PMI', 0.0):+.1f}" if cats.get('PMI') is not None else "N/A",
                "GDP (5%)": f"{cats.get('GDP', 0.0):+.1f}" if cats.get('GDP') is not None else "N/A",
                "Korrektur": f"{d['corr']:+.1f}" if d['corr'] is not None else "N/A",
                "Regime": d["regime"],
                "Datenqualität": status_dq
            })
            
        df_curr_summary = pd.DataFrame(table_rows)
        st.dataframe(df_curr_summary, hide_index=True, use_container_width=True)
        
        st.write("")
        st.markdown("---")
    
    # ----------------- TAB 2: FUNDAMENTAL SCORE & DEEP-DIVE -----------------
    with tab2:
        st.header("📊 Fundamental Score: Detailanalyse pro Einzelwährung")
        st.caption("Detaillierte Aufschlüsselung der makroökonomischen Faktoren, Berechnungsformeln und Zeitreihen für jede der 8 G8-Währungen.")
        
        sel_curr_fund = st.selectbox("Wähle eine Währung zur Tiefenanalyse:", list(CURRENCIES.keys()), index=0, key="deepdive_curr_sel")
        f_score_d, reg_d, core_d, corr_d, cats_d = compute_currency_professional_score_and_regime(sel_curr_fund)
        details_f = cats_d
        
        st.write(f"### Detaillierte Kennzahlen für {CURRENCIES[sel_curr_fund]['flag']} {sel_curr_fund} ({CURRENCIES[sel_curr_fund]['name']})")
        
        col_fd1, col_fd2, col_fd3, col_fd4, col_fd5 = st.columns(5)
        with col_fd1:
            st.metric("Geldpolitik (35%)", f"{format_score(cats_d.get('Geldpolitik', 0.0))}")
        with col_fd2:
            st.metric("Inflation (20%)", f"{format_score(cats_d.get('Inflation', 0.0))}")
        with col_fd3:
            st.metric("Arbeitsmarkt (20%)", f"{format_score(cats_d.get('Arbeitsmarkt', 0.0))}")
        with col_fd4:
            st.metric("PMI (20%)", f"{format_score(cats_d.get('PMI', 0.0))}")
        with col_fd5:
            st.metric("GDP (5%)", f"{format_score(cats_d.get('GDP', 0.0))}")
            
        st.write("")
        
        col_deep1, col_deep2 = st.columns([1.2, 1])
        with col_deep1:
            st.subheader("📋 BASE CORE: Rohdaten & Indikatoren")
            observations = details_f.get("_observations", {})
            rate_val = observations.get("Geldpolitik", {}).get("policy_rate")
            y2_val = observations.get("Geldpolitik", {}).get("yield_2y")
            cpi_val = observations.get("Inflation", {}).get("value")
            unemp_val = observations.get("Arbeitsmarkt", {}).get("value")
            pmi_val = observations.get("PMI", {}).get("value")
            gdp_val = observations.get("GDP", {}).get("value")

            raw_metrics = [
                {"Kategorie": "Zentralbank Leitzins", "Wert": f"{rate_val:.2f}%" if rate_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('Geldpolitik', 0.0))} (Blended)"},
                {"Kategorie": "2Y Sovereign Yield", "Wert": f"{y2_val:.3f}%" if y2_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('Geldpolitik', 0.0))} (Blended)"},
                {"Kategorie": "Verbraucherpreise (CPI YoY)", "Wert": f"{cpi_val:.2f}%" if cpi_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('Inflation', 0.0))}"},
                {"Kategorie": "Arbeitslosenquote", "Wert": f"{unemp_val:.2f}%" if unemp_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('Arbeitsmarkt', 0.0))}"},
                {"Kategorie": "PMI Einkaufsmanagerindex", "Wert": f"{pmi_val:.1f}" if pmi_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('PMI', 0.0))}"},
                {"Kategorie": "Reales BIP-Wachstum (YoY)", "Wert": f"{gdp_val:.2f}%" if gdp_val is not None else "N/A", "Modell-Score": f"{format_score(cats_d.get('GDP', 0.0))}"}
            ]
            for row, factor in zip(raw_metrics, ("Geldpolitik", "Geldpolitik", "Inflation", "Arbeitsmarkt", "PMI", "GDP")):
                observation = observations.get(factor, {})
                row["Quelle"] = observation.get("source") or "N/A"
                row["Bezugsdatum"] = str(observation.get("date") or "N/A")
                if factor == "PMI":
                    row["Quelle"] = " / ".join(str(observation.get(f"{c}_src") or "N/A") for c in ("m", "s"))
                    row["Bezugsdatum"] = " / ".join(str(observation.get(f"{c}_ref") or "N/A") for c in ("m", "s"))
            # The monetary factor stores the 2Y observation; do not attribute
            # that source/date to the independently verified policy rate.
            raw_metrics[0]["Quelle"] = "Verified Policy Rate Center"
            raw_metrics[0]["Bezugsdatum"] = "Siehe Policy Rate Center"
            st.dataframe(pd.DataFrame(raw_metrics), hide_index=True, use_container_width=True)
    
            st.subheader("📈 TREND & MOMENTUM CONTEXT")
            y_trends = get_yield_trends(sel_curr_fund)
            cpi_trend_pts = get_series_trend_points(CPI_SERIES.get(sel_curr_fund, "CPIAUCSL"), datetime.now().strftime("%Y-%m-%d"))
            unemp_trend_pts = get_series_trend_points(UNEMP_SERIES.get(sel_curr_fund, "UNRATE"), datetime.now().strftime("%Y-%m-%d"), reverse=True)
            pmi_trend_pts = get_series_trend_points(PMI_SERIES.get(sel_curr_fund, "MANEMP") if sel_curr_fund in PMI_SERIES else "USISMT", datetime.now().strftime("%Y-%m-%d"))
            gdp_trend_pts = get_series_trend_points(GDP_SERIES.get(sel_curr_fund, "GDPC1"), datetime.now().strftime("%Y-%m-%d"))
    
            trend_metrics = [
                {"Faktor": "2Y Sovereign Yield (1W Change)", "Wert": f"{y_trends.get('chg_1w', 0.0):+.3f}%" if y_trends.get('chg_1w') is not None else "N/A"},
                {"Faktor": "2Y Sovereign Yield (1M Change)", "Wert": f"{y_trends.get('chg_1m', 0.0):+.3f}%" if y_trends.get('chg_1m') is not None else "N/A"},
                {"Faktor": "2Y Sovereign Yield (3M Change)", "Wert": f"{y_trends.get('chg_3m', 0.0):+.3f}%" if y_trends.get('chg_3m') is not None else "N/A"},
                {"Faktor": "Inflation (CPI Trend Points)", "Wert": f"{cpi_trend_pts:+.1f} pts"},
                {"Faktor": "Arbeitsmarkt (Unemp Trend Points)", "Wert": f"{unemp_trend_pts:+.1f} pts"},
                {"Faktor": "PMI Trend Points", "Wert": f"{pmi_trend_pts:+.1f} pts"},
                {"Faktor": "GDP Trend Points", "Wert": f"{gdp_trend_pts:+.1f} pts"},
                {"Faktor": "Gesamte Trend-Korrektur (Trend Score)", "Wert": f"{details_f.get('_trend_score', 0.0):+.2f}"}
            ]
            st.dataframe(pd.DataFrame(trend_metrics), hide_index=True, use_container_width=True)
            
        with col_deep2:
            st.subheader("⚖️ Core Weights & Model Isolation")
            f_score_str = f"{f_score_d:+.1f}" if f_score_d is not None else "N/A"
            core_str = f"{core_d:+.1f}" if core_d is not None else "N/A"
            st.write(f"- **Gesamt-Score (Final Score):** `{f_score_str}`")
            st.write(f"- **BASE CORE Score:** `{core_str}`")
            st.write(f"- **Trend-Faktoren (Trend Score):** `{details_f.get('_trend_score', 0.0):+.1f}`" if details_f.get('_trend_score') is not None else "- **Trend-Faktoren (Trend Score):** `N/A`")
            st.write(f"- **Surprise-Faktoren (Surprise Score):** `{details_f.get('_surprise_score', 0.0):+.1f}`" if details_f.get('_surprise_score') is not None else "- **Surprise-Faktoren (Surprise Score):** `N/A`")
            st.write(f"- **Positionierungs- & Context-Score:** `{corr_d:+.1f}`" if corr_d is not None else "- **Positionierungs- & Context-Score:** `N/A`")
            st.write(f"- **Markt-Regime:** `{reg_d}`")
            st.write(f"- **Model Version:** `{CURRENT_MODEL_VERSION}` (Schema: `2.0`)")
            
            st.subheader("🟢 Freshness & Data Quality Indicators")
            st.write(f"- **Datenvollständigkeit:** `{details_f.get('_completeness', 100.0):.0f}%`")
            freshness_map = details_f.get("_freshness", {})
            for factor, status in freshness_map.items():
                badge_color = "🟢" if normalized_freshness(status) == "FRESH" else "🟡" if normalized_freshness(status) == "AGING" else "🔴"
                st.write(f"- **{factor} Freshness:** {badge_color} `{status}`")
                
            if details_f.get("_missing"):
                st.warning(f"⚠️ Fehlende/Stale Faktoren: {', '.join(details_f.get('_missing'))}")
            elif any(normalized_freshness(status) == "AGING" for status in freshness_map.values()):
                st.warning("🟡 Alle CORE-Faktoren verfügbar; einzelne Beobachtungen sind AGING. Die Datumsangaben oben beachten.")
            else:
                st.success("🟢 Alle CORE-Faktoren vollständig und innerhalb ihrer Aktualitätsgrenzen.")
    
            st.subheader("🌎 MARKET CONTEXT & RESEARCH FACTORS")
            y5_det = get_yield_details(sel_curr_fund, YIELD_5Y_SERIES, FRED_KEY)
            y5_val = y5_det.get("value") if y5_det else None
            oecd_exp = get_inflation_expectations_data(sel_curr_fund)
            oecd_val = oecd_exp.get("oecd_expectation") if oecd_exp else None
            
            st.write(f"- **5Y Sovereign Bond Yield:** `{y5_val:.3f}%`" if y5_val is not None else "- **5Y Sovereign Bond Yield:** `5Y YIELD UNAVAILABLE`")
            st.write(f"- **OECD Inflation Expectations:** `{oecd_val:.2f}%`" if oecd_val is not None else "- **OECD Inflation Expectations:** `OECD Expectations unavailable`")
    
    # ----------------- TAB 3: INTEREST RATES & 2Y YIELDS -----------------
    with tab3:
        st.header("🏦 Interest Rates & 2Y Government Bond Yields")
        st.caption("Vergleichende Analyse von Zentralbank-Leitzinsen, 2Y-Benchmark-Renditen und Zinskurven für alle 8 G8-Währungen.")
        
        rates_data = {}
        for curr, info in CURRENCIES.items():
            r_val, bps_chg, src = get_country_rate(info["wb_code"], FRED_KEY)
            rates_data[curr] = {
                "rate": r_val,
                "bps_change": bps_chg,
                "source": src
            }
            
        df_rates_plot = pd.DataFrame([
            {"Zentralbank": f"{curr} ({CURRENCIES[curr]['name']})", "Zinssatz": data["rate"], "Change": data["bps_change"]}
            for curr, data in rates_data.items()
        ])
        
        fig_rates_g8 = go.Figure()
        fig_rates_g8.add_trace(go.Bar(
            x=df_rates_plot["Zentralbank"],
            y=df_rates_plot["Zinssatz"],
            marker_color=['#7d7d8a' if finite_number(r) is None else '#10b981' if r > 4.0 else '#e2b13c' if r > 1.5 else '#ef4444' for r in df_rates_plot["Zinssatz"]],
            text=[f"{r:.2f}%" if finite_number(r) is not None else "N/A" for r in df_rates_plot["Zinssatz"]],
            textposition='auto',
            name="Zinssatz"
        ))
        fig_rates_g8.update_layout(
            title="<b>Zentralbank-Leitzinsen der G8</b>",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(color="#d1d5db", size=10),
            xaxis=dict(showgrid=False, linecolor="#1f2026"),
            yaxis=dict(showgrid=True, gridcolor='rgba(128,128,128,0.05)', linecolor="#1f2026"),
            height=300,
            margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_rates_g8, use_container_width=True)
        if df_rates_plot["Zinssatz"].isna().any():
            st.caption("Für nicht bestätigte Leitzinsen wird kein Balken angezeigt. Details stehen im Policy Rate Center.")
        
        # Bond Market & Yield Curve
        st.subheader("🏦 Bond Market & 2Y Benchmark Yields")
        bond_rows = []
        for curr, info in CURRENCIES.items():
            y2_det = get_yield_details(curr, YIELD_2Y_SERIES, FRED_KEY)
            y5_det = get_yield_details(curr, YIELD_5Y_SERIES, FRED_KEY)
            y10_det = get_yield_details(curr, YIELD_10Y_SERIES, FRED_KEY)
            
            status_2y = "🟢 REAL 2Y YIELD" if (y2_det and y2_det.get("source") != "Demo Mock" and y2_det.get("value") is not None) else "🟡 2Y YIELD UNAVAILABLE"
            status_5y = "🟢 REAL 5Y YIELD" if (y5_det and y5_det.get("source") != "Demo Mock" and y5_det.get("value") is not None) else "🟡 5Y YIELD UNAVAILABLE"
            
            y2_str = f"{y2_det['value']:.2f}%" if (y2_det and y2_det.get('value') is not None) else "N/A"
            y5_str = f"{y5_det['value']:.2f}%" if (y5_det and y5_det.get('value') is not None) else "5Y YIELD UNAVAILABLE"
            y10_str = f"{y10_det['value']:.2f}%" if (y10_det and y10_det.get('value') is not None) else "N/A"
            
            spread_str = f"{(y10_det['value'] - y2_det['value']):+.2f}%" if (y2_det and y10_det and y2_det.get('value') is not None and y10_det.get('value') is not None) else "N/A"
            chg_1w = f"{y2_det['chg_1w']:+.2f}%" if (y2_det and y2_det.get('chg_1w') is not None) else "N/A"
            chg_1m = f"{y2_det['chg_1m']:+.2f}%" if (y2_det and y2_det.get('chg_1m') is not None) else "N/A"
            trend_str = y2_det["trend"] if y2_det else "▬"
            src_str = y2_det["source"] if y2_det else "N/A"
            src_5y_str = y5_det["source"] if y5_det else "5Y YIELD UNAVAILABLE"
            
            bond_rows.append({
                "Währung": f"{info['flag']} {curr}",
                "Status (2Y)": status_2y,
                "Status (5Y)": status_5y,
                "2Y Rendite": y2_str,
                "5Y Rendite": y5_str,
                "10Y Rendite": y10_str,
                "2Y-10Y Spread": spread_str,
                "Veränderung 1W (2Y)": chg_1w,
                "Veränderung 1M (2Y)": chg_1m,
                "Trend (2Y)": trend_str,
                "Quelle 2Y": src_str,
                "Quelle 5Y": src_5y_str
            })
            
        df_bonds = pd.DataFrame(bond_rows)
        st.dataframe(df_bonds, hide_index=True, use_container_width=True)
    
    # ----------------- TAB 4: INFLATION / CPI -----------------
    with tab4:
        st.header("📈 Inflation & CPI Hub")
        st.caption("Verbraucherpreisindizes (CPI YoY), Inflationstrends und OECD Consumer Inflation Expectations für alle 8 G8-Währungen.")
        
        today_s = datetime.now().strftime("%Y-%m-%d")
        cpi_rows = []
        for curr, info in CURRENCIES.items():
            c_val, obs_date, metric_type, source, series_id, freshness = get_cpi_yoy_details(curr, today_s)
            inf_data = get_inflation_expectations_data(curr, today_s)
            c_trend = inf_data.get("cpi_trend")
            oecd_val = inf_data.get("oecd_expectation")
            
            # Display label for Swiss HICP
            metric_label = "HICP YoY" if curr in ("CHF", "EUR") else "CPI YoY"
            inflation_yoy_str = f"{c_val:.2f}% ({metric_label})" if c_val is not None else "N/A"
            
            cpi_change_str = f"{c_trend:+.2f} pp" if c_trend is not None else "N/A"
            cpi_trend_str = "↑ RISING" if (c_trend is not None and c_trend > 0.05) else "↓ FALLING" if (c_trend is not None and c_trend < -0.05) else "→ STABLE" if c_trend is not None else "N/A"
            oecd_str = f"{oecd_val:.1f} (% Saldo)" if oecd_val is not None else "N/A"
            
            cpi_rows.append({
                "Währung": f"{info['flag']} {curr}",
                "Inflation YoY": inflation_yoy_str,
                "Change": cpi_change_str,
                "Trend": cpi_trend_str,
                "Freshness": freshness,
                "Source": source,
                "OECD Expectations": oecd_str
            })
            
        st.dataframe(pd.DataFrame(cpi_rows), hide_index=True, use_container_width=True)
    
    # ----------------- TAB 5: LABOUR MARKET -----------------
    with tab5:
        st.header("👷 Labour Market Hub")
        st.caption("Arbeitslosenquoten und Beschäftigungsdynamik der 8 G8-Währungen.")
        
        today_s = datetime.now().strftime("%Y-%m-%d")
        unemp_rows = []
        for curr, info in CURRENCIES.items():
            unemp_rows.append(get_live_labour_display_row(curr, info, today_s))
            
        st.dataframe(pd.DataFrame(unemp_rows), hide_index=True, use_container_width=True)
    
    # ----------------- TAB 6: PMI (MANUFACTURING & SERVICES) -----------------
    with tab6:
        st.header("📊 PMI Frühindikatoren (Manufacturing & Services)")
        st.caption("Einkaufsmanagerindizes zur Messung der konjunkturellen Dynamik (Expansion > 50 / Kontraktion < 50).")
        
        pmi_data = get_all_pmi_data(FRED_KEY, EODHD_KEY)
        if pmi_data:
            rows = []
            for code, data in pmi_data.items():
                m_val = data.get("m_last")
                s_val = data.get("s_last")
                m_str = f"{m_val:.1f} ({'Expansion' if m_val >= 50 else 'Kontraktion'})" if m_val is not None else "N/A"
                s_str = f"{s_val:.1f} ({'Expansion' if s_val >= 50 else 'Kontraktion'})" if s_val is not None else "N/A"
                
                rows.append({
                    "Währung": f"{CURRENCIES.get(code, {}).get('flag', '')} {code}",
                    "Manufacturing PMI": m_str,
                    "Services PMI": s_str,
                    "Datenquelle": "S&P Global / ISM"
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.info("PMI Daten zur Zeit nicht geladen.")
    
    # ----------------- TAB 7: GDP GROWTH -----------------
    with tab7:
        st.header("📈 GDP Growth (Reales BIP-Wachstum)")
        st.caption("Reale Wirtschaftswachstumsraten (YoY) im internationalen G8-Vergleich.")
        
        today_s = datetime.now().strftime("%Y-%m-%d")
        gdp_rows = []
        for curr, info in CURRENCIES.items():
            g_val = get_gdp_yoy_value(curr, today_s)
            gdp_rows.append({
                "Währung": f"{info['flag']} {curr}",
                "Reales BIP-Wachstum (YoY)": f"{g_val:.2f}%" if g_val is not None else "N/A",
                "Klassifikation": "🟢 Starkes Wachstum" if (g_val and g_val > 2.0) else "🟡 Moderat" if (g_val and g_val > 0.5) else "🔴 Schwäche",
                "Quelle": "FRED / World Bank"
            })
        st.dataframe(pd.DataFrame(gdp_rows), hide_index=True, use_container_width=True)
    
    # ----------------- TAB 8: MARKET REGIME & MACRO FACTORS -----------------
    with tab8:
        st.header("🌎 Market Regime & Macro Correction Factors")
        st.caption("Globales Marktregime, Volatilität (VIX), Rohstoffeinflüsse und strukturelle Makrokorrekturen (Leistungsbilanz & Staatsverschuldung).")
        
        vix = get_vix_value()
        cpi_us = get_cpi_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        gdp_us = get_gdp_yoy_value("USD", datetime.now().strftime("%Y-%m-%d"))
        
        if vix is None:
            current_regime = "Unbekannt · VIX nicht geprüft"
            regime_desc = "Für den VIX liegt kein zentral geprüfter Live-Wert vor. Eine Risikoeinstufung wird deshalb nicht angezeigt."
        elif vix > 22.0:
            current_regime = "Risk-Off 🛡️"
            regime_desc = "Erhöhte Volatilität und Risikoaversion. Sichere Häfen (USD, CHF, JPY) tendieren zur Stärke."
        elif vix < 14.0 and (gdp_us is not None and gdp_us > 1.5):
            current_regime = "Risk-On / Inflationary Growth 🚀"
            regime_desc = "Risikobereitschaft am Markt ist hoch. Wachstums- und Rohstoffwährungen (AUD, NZD, CAD) sind gefragt."
        elif (cpi_us is not None and cpi_us > 3.0) and (gdp_us is not None and gdp_us < 1.0):
            current_regime = "Stagflation ⚠️"
            regime_desc = "Hohe Inflation bei stagnierendem Wirtschaftswachstum. Schwieriges Umfeld für Risikoanlagen."
        else:
            current_regime = "Normales Marktregime 🟡"
            regime_desc = "Standard-Marktumfeld ohne extreme Risikoverteilungen."
            
        col_reg1, col_reg2 = st.columns([1, 2])
        with col_reg1:
            st.metric("Aktueller VIX Index", f"{vix:.2f}" if vix is not None else "Nicht verfügbar")
            st.metric("Regime-Einstufung", current_regime)
        with col_reg2:
            st.markdown("### Regime-Interpretation")
            st.write(regime_desc)
            
        st.write("")
        st.subheader("🛍️ Rohstoff-Sensitivität (AUD, CAD, NZD)")
        st.caption("Korrekturfaktoren für rohstoffgebundene G8-Währungen (Öl, Kupfer, Milch/Agrar).")
        
        com_rows = []
        for c in ["AUD", "CAD", "NZD", "USD", "EUR", "GBP", "JPY", "CHF"]:
            c_score, _, _, corr_val, _ = compute_currency_professional_score_and_regime(c)
            com_rows.append({
                "Währung": f"{CURRENCIES[c]['flag']} {c}",
                "Struktur-Korrekturfaktor": f"{corr_val:+.1f}",
                "Typ": "Rohstoffwährung" if c in ["AUD", "CAD", "NZD"] else "Sicherer Hafen" if c in ["USD", "CHF", "JPY"] else "Standard"
            })
        st.dataframe(pd.DataFrame(com_rows), hide_index=True, use_container_width=True)
    
    # ----------------- TAB 9: MANUAL NEWS CHECK -----------------
    with tab9:
        st.header("📅 Manual News Check (Forex Factory)")
        st.caption("Manuelle Vor-Trade-Prüfung wichtiger Marktereignisse. Automatische News-APIs fließen nicht in die Signalberechnung ein.")
        
        st.warning("⚠️ **Wichtiger Hinweis:** News-APIs wurden vollständig aus der Signal- und CORE-Berechnung entfernt (0% Einfluss). Bitte prüfen Sie anstehende High-Impact-Termine vor jedem Trade-Einstieg manuell auf **Forex Factory**.")
        
        st.markdown("""
        ### 📋 Pre-Trade Checkliste für Forex Factory:
        
        Vor der Ausführung eines Trades auf Basis der Fundamentaldaten sollten folgende Schritte manuell auf [Forex Factory](https://www.forexfactory.com/calendar) geprüft werden:
        
        1. 🔴 **Red-Folder Events (High Impact):**
           - Steht in den nächsten 24 Stunden eine Zinsentscheidung (FOMC, EZB, BoE, BoJ, SNB, BoC, RBA, RBNZ) für die beteiligten Währungen an?
           - Werden heute wichtige Inflationsdaten (CPI, PPI, PCE) veröffentlicht?
           - Stehen wichtige Arbeitsmarktdaten (z. B. US Non-Farm Payrolls, Arbeitslosenquote) an?
        2. 🗣️ **Zentralbank-Reden & Pressekonferenzen:**
           - Gibt es Reden von Zentralbank-Präsidenten (Powell, Lagarde, Bailey, Ueda)?
        3. ⚡ **Ungeplante geopolitische / globale Risiken:**
           - Gibt es plötzliche Krisen oder Marktverwerfungen, die das globale Sentiment dominieren?
           
        > *„Fundamental stark vs. schwach gibt die fundamentale Richtung vor – der Wirtschaftskalender liefert das Timing und schützt vor Slippage bei News-Events.“*
        """)
        
        st.info("🔗 **Direktlink zum Kalender:** [Forex Factory Economic Calendar](https://www.forexfactory.com/calendar)")
    
    # ----------------- TAB 10: FX PAIR DIVERGENCE ANALYZER -----------------
    with tab10:
        st.header("💱 FX Pair Divergence Analyzer")
        st.caption("Paar-Signale erfordern 100% CORE-Abdeckung beider Währungen. Vollständigkeit ist keine Garantie für Datenrichtigkeit oder Handelserfolg.")
        
        col_pa1, col_pa2 = st.columns(2)
        with col_pa1:
            base_sel = st.selectbox("Basis-Währung (Base)", list(CURRENCIES.keys()), index=0, key="pair_div_base")
        with col_pa2:
            quote_sel = st.selectbox("Quote-Währung (Quote)", list(CURRENCIES.keys()), index=1, key="pair_div_quote")
            
        if base_sel == quote_sel:
            st.warning("⚠️ Bitte wählen Sie zwei unterschiedliche Währungen aus.")
        else:
            b_score, b_reg, b_core, b_corr, b_details = compute_currency_professional_score_and_regime(base_sel)
            q_score, q_reg, q_core, q_corr, q_details = compute_currency_professional_score_and_regime(quote_sel)
            
            b_score_str = f"{b_score:+.1f}" if b_score is not None else "N/A"
            q_score_str = f"{q_score:+.1f}" if q_score is not None else "N/A"
            if b_score is None or q_score is None:
                print(f"[DEBUG] Tab 10 import: base={base_sel} score={b_score}, quote={quote_sel} score={q_score}")
                
            badge_name, badge_color, sig_val, s_code = get_pair_signal_and_badge(base_sel, quote_sel)
            
            st.write(f"### Paar-Divergenz: {CURRENCIES[base_sel]['flag']} {base_sel} vs {CURRENCIES[quote_sel]['flag']} {quote_sel}")
            render_bias_box(sig_val, base_sel, quote_sel, b_core, q_core, s_code)
            
            col_pb1, col_pb2 = st.columns(2)
            with col_pb1:
                st.subheader(f"{CURRENCIES[base_sel]['flag']} {base_sel} Faktoren")
                st.metric("BASE CORE Score", f"{b_core:+.1f}" if b_core is not None else "N/A", delta=f"Regime: {b_reg}")
                st.write(f"- Gesamt/Kontext-Score: `{b_score_str}`")
                st.write(f"- Geldpolitik: `{format_score(b_details.get('Geldpolitik', 0.0))}`")
                st.write(f"- Inflation: `{format_score(b_details.get('Inflation', 0.0))}`")
                st.write(f"- Arbeitsmarkt: `{format_score(b_details.get('Arbeitsmarkt', 0.0))}`")
                st.write(f"- PMI: `{format_score(b_details.get('PMI', 0.0))}`")
                st.write(f"- GDP: `{format_score(b_details.get('GDP', 0.0))}`")
            with col_pb2:
                st.subheader(f"{CURRENCIES[quote_sel]['flag']} {quote_sel} Faktoren")
                st.metric("BASE CORE Score", f"{q_core:+.1f}" if q_core is not None else "N/A", delta=f"Regime: {q_reg}")
                st.write(f"- Gesamt/Kontext-Score: `{q_score_str}`")
                st.write(f"- Geldpolitik: `{format_score(q_details.get('Geldpolitik', 0.0))}`")
                st.write(f"- Inflation: `{format_score(q_details.get('Inflation', 0.0))}`")
                st.write(f"- Arbeitsmarkt: `{format_score(q_details.get('Arbeitsmarkt', 0.0))}`")
                st.write(f"- PMI: `{format_score(q_details.get('PMI', 0.0))}`")
                st.write(f"- GDP: `{format_score(q_details.get('GDP', 0.0))}`")
    
    # ----------------- TAB 11: POSITIONING (COT) -----------------
    with tab11:
        st.header("📍 Positioning (COT Report)")
        st.caption("Netto-Spekulanten-Positionierung der G8-Währungen aus dem Commitment of Traders Report.")
        
        st.info("ℹ️ **TradingView Notice:** COT is externally monitored via TradingView. Manuelle Korrekturen sind ausschließlich im geschützten Betreiberzugang möglich.")
        
        with st.expander("📝 Manuelle COT-Daten eingeben / aktualisieren"):
            m_curr = st.selectbox("Währung:", list(CURRENCIES.keys()), key="cot_m_curr_new")
            m_pos = st.selectbox("Positionierung:", ["Bullish", "Bearish", "Neutral"], key="cot_m_pos_new")
            m_net = st.number_input("Netto-Kontrakte:", value=0, key="cot_m_net_new")
            m_perc = st.slider("Percentile (0-100%):", 0.0, 100.0, 50.0, step=1.0, key="cot_m_perc_new")
            m_date = st.date_input("Berichtsdatum:", key="cot_m_date_new")
            
            if st.button("💾 Manuellen COT-Eintrag speichern", key="save_m_cot_btn_new", disabled=not operator_is_authorized()) and operator_is_authorized():
                save_manual_cot_entry(m_curr, m_pos, m_net, m_perc, m_date.strftime("%Y-%m-%d"))
                st.success(f"COT-Daten für {m_curr} gespeichert!")
                st.rerun()
    
        st.subheader("🛍️ COT Netto-Positionierung (Percentile)")
        cot_rows = []
        for curr in CURRENCIES.keys():
            try:
                percentile = get_latest_cot_percentile(curr)
                if percentile is None:
                    cot_rows.append({
                        "Währung": f"{CURRENCIES[curr]['flag']} {curr}",
                        "COT Rollierendes Percentil (3Y)": "DATA UNAVAILABLE 🔴",
                        "Status / Warnung": "Keine aktuellen Daten verfügbar"
                    })
                else:
                    warning_str = "⚠️ Extrem bullish (Überkauft)" if percentile > 80.0 else "⚠️ Extrem bearish (Überverkauft)" if percentile < 20.0 else "Gesund"
                    cot_rows.append({
                        "Währung": f"{CURRENCIES[curr]['flag']} {curr}",
                        "COT Rollierendes Percentil (3Y)": f"{percentile:.1f}%",
                        "Status / Warnung": warning_str
                    })
            except Exception:
                pass
                
        if cot_rows:
            st.dataframe(pd.DataFrame(cot_rows), hide_index=True, use_container_width=True)
    
    # ----------------- TAB 12: LIVE SIGNAL HISTORY & OUTCOMES -----------------
    with tab12:
        st.header("📈 Live Signal History & Outcomes")
        st.caption("Dauerhafte Aufzeichnung und Analyse von echten Live-Snapshots (Einzelwährungen & Währungspaare) zur empirischen Evaluierung.")
        
        st.warning("⚠️ **Wichtiger Hinweis:** Die Live-Datensammlung dient Beobachtungszwecken. Statistische Ergebnisse beweisen keine Kausalität und Modelle werden nicht automatisch optimiert.")
        
        signals_data = load_live_signals()
        
        if not signals_data:
            st.info("Bisher wurden keine Live-Signal-Snapshots aufgezeichnet. Die automatische Erfassung startet bei täglicher Verwendung oder via GitHub Actions.")
        else:
            # Separate Currency Snapshots vs Pair Snapshots
            curr_snapshots = {k: v for k, v in signals_data.items() if k.startswith("CURR_") or v.get("metadata", {}).get("type") == "currency"}
            pair_snapshots = {k: v for k, v in signals_data.items() if not (k.startswith("CURR_") or v.get("metadata", {}).get("type") == "currency")}
            
            num_pairs = len(pair_snapshots)
            completed_outcomes = sum(1 for s in pair_snapshots.values() if s.get("outcome_status") == "COMPLETED")
            open_outcomes = sum(1 for snapshot in pair_snapshots.values() if snapshot.get("outcome_status", "OPEN") == "OPEN")
            invalid_outcomes = sum(1 for snapshot in pair_snapshots.values() if snapshot.get("outcome_status") == "INVALID")

            col_st1, col_st2, col_st3, col_st4, col_st5 = st.columns(5)
            with col_st1:
                st.metric("Einzelwährungs-Snapshots", f"{len(curr_snapshots)}")
            with col_st2:
                st.metric("Paar-Snapshots", f"{num_pairs}")
            with col_st3:
                st.metric("Abgeschlossene Outcomes", f"{completed_outcomes}")
            with col_st4:
                st.metric("Laufende Outcomes", f"{open_outcomes}")
            with col_st5:
                st.metric("Ungültige Outcomes", f"{invalid_outcomes}")
                
            st.write("")
            hist_sub1, hist_sub2, hist_sub3 = st.tabs([
                "🏆 Einzelwährungs-Historie (G8)",
                "💱 Währungspaar-Outcomes & Performance",
                "📥 Daten-Export"
            ])
            
            with hist_sub1:
                st.subheader("🏆 Protokollierte Einzelwährungs-Snapshots")
                if curr_snapshots:
                    c_rows = []
                    for s_id, s in curr_snapshots.items():
                        meta = s.get("metadata", {}) if isinstance(s.get("metadata"), dict) else {}
                        factors = s.get("factors", {}) if isinstance(s.get("factors"), dict) else {}
                        
                        s_date = meta.get("date") if meta.get("date") else s.get("date", "N/A")
                        s_curr = meta.get("currency") if meta.get("currency") else s.get("currency", "N/A")
                        
                        # Preserve explicit missing CORE; never substitute a context total.
                        score_val = s.get("core_score") if "core_score" in s else s.get("score")
                            
                        s_factors = factors if factors else s.get("factor_scores", {})
                        
                        c_rows.append({
                            "Snapshot ID": s_id,
                            "Datum": s_date,
                            "Währung": s_curr,
                            "BASE CORE": format_score(score_val),
                            "Regime": s.get("regime", "Neutral"),
                            "Geldpolitik (35%)": f"{s_factors.get('Geldpolitik', 0.0):+.1f}" if s_factors.get('Geldpolitik') is not None else "N/A",
                            "Inflation (20%)": f"{s_factors.get('Inflation', 0.0):+.1f}" if s_factors.get('Inflation') is not None else "N/A",
                            "Arbeitsmarkt (20%)": f"{s_factors.get('Arbeitsmarkt', 0.0):+.1f}" if s_factors.get('Arbeitsmarkt') is not None else "N/A",
                            "PMI (20%)": f"{s_factors.get('PMI', 0.0):+.1f}" if s_factors.get('PMI') is not None else "N/A",
                            "GDP (5%)": f"{s_factors.get('GDP', 0.0):+.1f}" if s_factors.get('GDP') is not None else "N/A"
                        })
                    df_c_hist = pd.DataFrame(c_rows).sort_values("Datum", ascending=False)
                    st.dataframe(df_c_hist, hide_index=True, use_container_width=True)
                else:
                    st.info("Noch keine Einzelwährungs-Snapshots vorhanden.")
                    
            with hist_sub2:
                st.subheader("💱 Währungspaar-Performance & Tracking")
                if pair_snapshots:
                    eval_rows = []
                    for s_id, s in pair_snapshots.items():
                        p_sig = s.get("pair_signal", {})
                        outcomes = s.get("outcomes", {})
                        ret5 = outcomes.get("5", {}).get("directional_return_pct")
                        ret10 = outcomes.get("10", {}).get("directional_return_pct")
                        ret20 = outcomes.get("20", {}).get("directional_return_pct")
                        eval_rows.append({
                            "Snapshot ID": s_id,
                            "Datum": s.get("metadata", {}).get("date"),
                            "FX-Paar": s.get("metadata", {}).get("pair"),
                            "Signal": p_sig.get("signal"),
                            "Divergenz": format_score(p_sig.get("divergence")),
                            "Entry": s.get("entry_price"),
                            "Status": s.get("outcome_status", "OPEN"),
                            "Fehler / Hinweis": s.get("outcome_error", ""),
                            "Return 5D": f"{ret5:+.2f}%" if ret5 is not None else "N/A" if s.get("outcome_status") == "INVALID" else "Pending",
                            "Return 10D": f"{ret10:+.2f}%" if ret10 is not None else "N/A" if s.get("outcome_status") == "INVALID" else "Pending",
                            "Return 20D": f"{ret20:+.2f}%" if ret20 is not None else "N/A" if s.get("outcome_status") == "INVALID" else "Pending"
                        })
                    df_eval = pd.DataFrame(eval_rows).sort_values("Datum", ascending=False)
                    st.dataframe(df_eval, hide_index=True, use_container_width=True)
                else:
                    st.info("Noch keine Paar-Snapshots vorhanden.")
                    
            with hist_sub3:
                st.subheader("📥 Export der Live-Datensätze")
                json_str = json.dumps(signals_data, indent=4, ensure_ascii=False)
                st.download_button(
                    label="📥 Vollständigen JSON Datensatz exportieren",
                    data=json_str,
                    file_name=f"live_signals_full_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json",
                    key="btn_export_json_history"
                )
    
    # ----------------- TAB 13: BACKTESTING & MODEL LAB -----------------
    with tab13:
        st.header("📊 Backtesting, Model Lab & Quant Research")
        st.caption("CORE-Baseline und Datenstatus. Die angekündigten Research-Funktionen sind noch nicht implementiert.")

        lab1, lab2, lab3, lab4, lab5, lab6 = st.tabs([
            "📊 Fundamental Backtest",
            "🧪 Model Lab & Custom Weights",
            "🔬 Historical & Quant Research",
            "🚀 Forward Testing",
            "📝 Research Journal",
            "🛠 API & Data Status"
        ])
        
        with lab1:
            st.subheader("📊 Fundamental FX Backtest")
            st.warning("Nicht verfügbar: Es ist keine validierte Backtest-Engine angeschlossen. Es werden keine Trades, Renditen oder Erfolgsquoten berechnet.")
            st.button("Backtest starten – noch nicht verfügbar", disabled=True, key="btn_run_bt_lab")

        with lab2:
            st.subheader("🧪 CORE-Baseline")
            st.caption("Die produktive Methodik ist eingefroren. Eigene Modelle können hier noch nicht erstellt oder aktiviert werden.")
            for factor, weight in CORE_FACTOR_WEIGHTS.items():
                st.write(f"- **{factor}:** {weight:.1f}%")
            st.write(f"Modellversion: `{CURRENT_MODEL_VERSION}`")

        with lab3:
            st.subheader("🔬 Historical & Quant Research")
            st.info("Nicht verfügbar: Historische Score-Rekonstruktion und eine Prüfung der zum damaligen Zeitpunkt verfügbaren Daten sind noch nicht implementiert.")

        with lab4:
            st.subheader("🚀 Forward Testing & Paper Trading")
            st.info("Nicht verfügbar: Es läuft hier kein eigenes Forward-Testing oder Paper Trading. Vorhandene Live-Snapshots finden Sie im Tab Live Signal History & Outcomes.")

        with lab5:
            st.subheader("📝 Research Journal")
            st.info("Nicht verfügbar: Das Journal ist noch nicht angebunden; Eingaben und Experimente werden hier nicht aufgezeichnet.")

        with lab6:
            st.subheader("🛠 Technical API & Data Status")
            st.caption("Konfiguration der Datenquellen. Ein vorhandener API-Schlüssel bestätigt keine erfolgreiche Datenabfrage.")
            
            api_health = [
                {"API / Datenquelle": "FRED API (St. Louis Fed)", "Status": "Schlüssel vorhanden (Verbindung ungeprüft)" if FRED_KEY else "Inaktiv 🔴 (API-Key fehlt)"},
                {"API / Datenquelle": "EODHD Macro / Bonds API", "Status": get_eodhd_status_label()},
                {"API / Datenquelle": "FCS Price Data API", "Status": "Schlüssel vorhanden (Verbindung ungeprüft)" if FCS_KEY else "Inaktiv 🔴 (API-Key fehlt)"},
                {"API / Datenquelle": "Tiingo Commodity API", "Status": "Schlüssel vorhanden (Verbindung ungeprüft)" if TIINGO_KEY else "Inaktiv 🔴 (API-Key fehlt)"},
                {"API / Datenquelle": "World Bank Indicator API", "Status": "Öffentliche Quelle (Verbindung ungeprüft)"},
                {"API / Datenquelle": "OECD Consumer Expectations", "Status": "Öffentliche Quelle (Verbindung ungeprüft)"}
            ]
            st.dataframe(pd.DataFrame(api_health), hide_index=True, use_container_width=True)
            st.caption("🛡️ News-APIs (Finnhub, NewsAPI, StockData, Benzinga News) sind dauerhaft deaktiviert (0% Einfluss auf Fundamentalanalyse).")
