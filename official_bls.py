"""Official BLS publication checks for the existing FRED USD CORE series.

The scheduled successor is a deadline, never proof of publication. The BLS
release hosted by the US Department of Labor is primary proof. Its verified
BLS Employment Situation PDF is a bounded alternate transport. After
unreachable PDF transports and a 24-hour delay, the official API v1 may prove
the exact month if its raw observation matches FRED. FRED supplies the CORE score.
"""
import re
from io import BytesIO
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup


EASTERN = ZoneInfo("America/New_York")
REPORTS = {
    "Arbeitsmarkt": {
        "schedule": "https://www.bls.gov/schedule/news_release/empsit.htm",
        "schedule_title": "Schedule of Releases for the Employment Situation",
        "release_title": "THE EMPLOYMENT SITUATION",
        "series_id": "UNRATE",
        "api_series": "LNS14000000",
        "archive_prefix": "empsit",
    },
    "Inflation": {
        "schedule": "https://www.bls.gov/schedule/news_release/cpi.htm",
        "schedule_title": "Schedule of Releases for the Consumer Price Index",
        "release_title": "CONSUMER PRICE INDEX",
        "series_id": "CPIAUCNS",
        "api_series": "CUUR0000SA0",
        "archive_prefix": "cpi",
    },
}
# Verified BLS 2026 schedule rows. These are deadlines, not observations or
# evidence that an edition has appeared. No extrapolation beyond these rows.
# https://www.bls.gov/schedule/news_release/empsit.htm
# https://www.bls.gov/schedule/news_release/cpi.htm
PINNED_DUES = {
    "Arbeitsmarkt": {
        "2026-07": "2026-08-07T12:30:00+00:00",
        "2026-08": "2026-09-04T12:30:00+00:00",
        "2026-09": "2026-10-02T12:30:00+00:00",
        "2026-10": "2026-11-06T13:30:00+00:00",
        "2026-11": "2026-12-04T13:30:00+00:00",
    },
    "Inflation": {
        "2026-07": "2026-08-12T12:30:00+00:00",
        "2026-08": "2026-09-11T12:30:00+00:00",
        "2026-09": "2026-10-14T12:30:00+00:00",
        "2026-10": "2026-11-10T13:30:00+00:00",
        "2026-11": "2026-12-10T13:30:00+00:00",
    },
}
USER_AGENT = "fx-dashboard-source-verification/1.0 (+https://github.com/quillsy/forex-dashboard)"
# This exact link is published by BLS on the Employment Situation release page.
# Verified 2026-10-02: byte-identical to the dated DOL bulletin. No other report
# or hostname is enabled by inference. The latest-PDF URL still needs the full
# period, embargo and successor checks on every newly fetched edition.
BLS_PDF_URLS = {"Arbeitsmarkt": "https://www.bls.gov/news.release/pdf/empsit.pdf"}
API_BASE = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_ROW = re.compile(
    rf"{_MONTH}\s+(\d{{4}})\s+([A-Z][a-z]{{2,8}})\.?\s+(\d{{1,2}}),\s+"
    r"(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)\b"
)
_EMBARGO = re.compile(
    rf"Transmission of material in this (?:news )?release is embargoed until\s+"
    rf"(?:USDL-\d{{2}}-\d{{1,6}}\s+)?"
    rf"(\d{{1,2}}):(\d{{2}})\s*(a\.m\.|p\.m\.)\s*\(ET\)\s+"
    rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+{_MONTH}\s+"
    r"(\d{1,2}),\s+(\d{4})",
    re.I,
)


class BlsInvalid(ValueError):
    """A BLS document does not prove the expected release contract."""


def _text(html):
    if not isinstance(html, str):
        raise BlsInvalid("BLS_HTML_INVALID")
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return soup.get_text(" ", strip=True)


def _period(month, year):
    try:
        return datetime.strptime(f"{month} {year}", "%B %Y").strftime("%Y-%m")
    except ValueError as exc:
        raise BlsInvalid("BLS_PERIOD_INVALID") from exc


def _successor(period):
    year, month = map(int, period.split("-"))
    return f"{year + (month == 12):04d}-{month % 12 + 1:02d}"


def _eastern(year, month, day, hour, minute):
    try:
        value = datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=EASTERN)
    except ValueError as exc:
        raise BlsInvalid("BLS_TIME_INVALID") from exc
    return value.astimezone(timezone.utc)


def _parse_aware(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def parse_schedule(html, factor):
    """Return exact reference-month deadlines from one BLS report schedule."""
    report = REPORTS[factor]
    text = _text(html)
    title = report["schedule_title"]
    if text.count(title) != 1:
        raise BlsInvalid("BLS_SCHEDULE_IDENTITY_INVALID")
    section = text.split(title, 1)[1].split("Subscribe to the BLS Online Calendar", 1)[0]
    if "Reference Month Release Date Release Time" not in section:
        raise BlsInvalid("BLS_SCHEDULE_SCHEMA_INVALID")
    rows = {}
    for match in _ROW.finditer(section):
        ref_month, ref_year, release_month, day, year, hour, minute, meridiem = match.groups()
        period = _period(ref_month, ref_year)
        try:
            release_number = datetime.strptime(release_month[:3], "%b").month
        except ValueError as exc:
            raise BlsInvalid("BLS_TIME_INVALID") from exc
        hour = int(hour) % 12 + (12 if meridiem.upper() == "PM" else 0)
        due = _eastern(year, release_number, day, hour, minute)
        if period in rows or due <= datetime.strptime(period + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc):
            raise BlsInvalid("BLS_SCHEDULE_CONFLICT")
        rows[period] = due
    if not rows:
        raise BlsInvalid("BLS_SCHEDULE_EMPTY")
    return rows


def parse_pdf_release(text, factor, now):
    """Bind a DOL-hosted BLS bulletin to its heading, embargo and next month."""
    report = REPORTS[factor]
    clean = " ".join(text.split())
    heading = re.findall(rf"\b{re.escape(report['release_title'])}\s*[–—-]\s*{_MONTH}\s+(\d{{4}})\b", clean, re.I)
    embargo = _EMBARGO.findall(clean)
    name = "The Employment Situation" if factor == "Arbeitsmarkt" else "The Consumer Price Index news release"
    successor = re.findall(
        rf"{re.escape(name)}\s+for\s+{_MONTH}\s+(\d{{4}})\s+is scheduled to be published on\s+"
        rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+{_MONTH}\s+(\d{{1,2}}),\s+"
        r"(\d{4}),\s+at\s+(\d{1,2}):(\d{2})\s+a\.m\.\s*\(ET\)", clean, re.I)
    if len(heading) != 1 or len(embargo) != 1 or len(successor) != 1:
        raise BlsInvalid("BLS_PDF_RELEASE_SCHEMA_INVALID")
    month, year = heading[0]
    period = _period(month, year)
    hour, minute, meridiem, pub_month, day, pub_year = embargo[0]
    hour = int(hour) % 12 + (12 if meridiem.lower().startswith("p") else 0)
    published = _eastern(pub_year, datetime.strptime(pub_month, "%B").month, day, hour, minute)
    next_month, next_year, due_month, due_day, due_year, due_hour, due_minute = successor[0]
    if _period(next_month, next_year) != _successor(period):
        raise BlsInvalid("BLS_PDF_SUCCESSOR_PERIOD_INVALID")
    due = _eastern(due_year, datetime.strptime(due_month, "%B").month,
                   due_day, due_hour, due_minute)
    if not published <= now < due or not published < due:
        raise BlsInvalid("BLS_PDF_RELEASE_NOT_CURRENT")
    return {"period": period, "embargo_ends_at": published.isoformat(),
            "next_due_at": due.isoformat()}


def _pdf_text(content):
    if not isinstance(content, bytes) or not content.startswith(b"%PDF") or len(content) > 3_000_000:
        raise BlsInvalid("BLS_PDF_INVALID")
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content), strict=True)
        if len(reader.pages) < 3 or len(reader.pages) > 60:
            raise BlsInvalid("BLS_PDF_PAGES_INVALID")
        return " ".join((reader.pages[i].extract_text() or "") for i in range(min(5, len(reader.pages))))
    except Exception as exc:
        raise BlsInvalid("BLS_PDF_PARSE_INVALID") from exc


def _pdf_url(factor, published):
    local_date = published.astimezone(EASTERN).strftime("%m%d%Y")
    return f"https://www.dol.gov/newsroom/economicdata/{REPORTS[factor]['archive_prefix']}_{local_date}.pdf"


def _bls_pdf_url(factor):
    return BLS_PDF_URLS.get(factor)


def _host_blocked(session, host, now):
    cooldown = getattr(session, "cooldown", None)
    if isinstance(cooldown, (set, frozenset, tuple, list)) and host in cooldown:
        return True
    retry_after = getattr(session, "retry_after", None)
    deadline = retry_after.get(host) if isinstance(retry_after, dict) else None
    return (isinstance(deadline, datetime) and deadline.tzinfo is not None
            and deadline > now)


def _validate_pdf_origin(response, url):
    """An HTTP denial must have the same safe origin as a returned PDF."""
    status = getattr(response, "status_code", None)
    if type(status) is not int or not 100 <= status < 600:
        raise BlsInvalid("BLS_PDF_HTTP_STATUS_INVALID")
    if 300 <= status < 400 or getattr(response, "history", None):
        raise BlsInvalid("BLS_PDF_REDIRECT_OR_URL_INVALID")
    final_url = getattr(response, "url", None)
    # CollectorTransport deliberately reduces HTTP error URLs to the host.
    # Never treat a denial from another scheme/host as a permitted outage.
    if isinstance(final_url, str):
        target = urlsplit(final_url)
        expected = urlsplit(url)
        if (target.scheme, target.netloc) != (expected.scheme, expected.netloc):
            raise BlsInvalid("BLS_PDF_REDIRECT_OR_URL_INVALID")


def _request_pdf(session, url, now, diagnostics, status_key):
    """One direct GET, retaining the transport's locks and attempt accounting."""
    host = urlsplit(url).hostname
    if _host_blocked(session, host, now):
        if isinstance(diagnostics, dict):
            diagnostics[status_key] = "PROVIDER_COOLDOWN"
        raise requests.RequestException("PROVIDER_COOLDOWN")
    usage = getattr(session, "usage", None)
    before = (usage.get(host, {}).get("requests_this_run", 0)
              if isinstance(usage, dict) else None)
    try:
        # An explicit empty Cookie header prevents a supplied requests.Session
        # from attaching its cookie jar. No cookies or redirect destinations
        # are used to obtain a document after a blocked request.
        response = session.get(url, headers={"User-Agent": USER_AGENT, "Cookie": ""},
                               cookies={}, allow_redirects=False, timeout=20)
    except requests.exceptions.RequestException as error:
        if isinstance(diagnostics, dict):
            diagnostics[status_key] = ("PROVIDER_COOLDOWN" if error.args == ("PROVIDER_COOLDOWN",)
                                       else "TRANSPORT_ERROR")
        error_response = getattr(error, "response", None)
        if error_response is not None:
            _validate_pdf_origin(error_response, url)
        raise
    finally:
        after = (usage.get(host, {}).get("requests_this_run", 0)
                 if isinstance(usage, dict) else None)
        attempted = after > before if isinstance(before, int) and isinstance(after, int) else True
        if attempted and isinstance(diagnostics, dict):
            diagnostics["pdf_attempted"] = True
            if status_key == "bls_pdf_status":
                diagnostics["bls_pdf_attempted"] = True
    if isinstance(diagnostics, dict):
        status = getattr(response, "status_code", None)
        diagnostics[status_key] = "HTTP_" + str(status) if type(status) is int else "UNKNOWN"
    _validate_pdf_origin(response, url)
    response.raise_for_status()
    return response


def _pdf_unreachable(error, session, host, diagnostics, status_key):
    if isinstance(error, requests.exceptions.SSLError):
        return False
    usage = getattr(session, "usage", None)
    cooldown = getattr(session, "cooldown", None)
    prior_403 = (error.args == ("PROVIDER_COOLDOWN",)
                 and isinstance(usage, dict)
                 and usage.get(host, {}).get("outcomes_this_run", {}).get("HTTP_403", 0) >= 1
                 and isinstance(cooldown, (set, frozenset, tuple, list)) and host in cooldown)
    if prior_403 and isinstance(diagnostics, dict):
        diagnostics[status_key] = "PRIOR_HTTP_403_COOLDOWN"
    status = getattr(getattr(error, "response", None), "status_code", None)
    return isinstance(error, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)) or prior_403 or (
                type(status) is int and (status in (403, 408, 425, 429) or 500 <= status < 600))


def _pdf_state(response, factor, now, period, published, url):
    """Both PDF transports must prove the same unchanged release contract."""
    if (300 <= response.status_code < 400 or response.history
            or response.url != url):
        raise BlsInvalid("BLS_PDF_REDIRECT_OR_URL_INVALID")
    if response.status_code != 200:
        raise BlsInvalid("BLS_PDF_HTTP_STATUS_INVALID")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/pdf":
        raise BlsInvalid("BLS_PDF_CONTENT_TYPE_INVALID")
    state = parse_pdf_release(_pdf_text(response.content), factor, now)
    if state["period"] != period or state["embargo_ends_at"] != published.isoformat():
        raise BlsInvalid("BLS_PDF_RELEASE_CONFLICT")
    pinned_next = PINNED_DUES[factor].get(_successor(period))
    if pinned_next is not None and state["next_due_at"] != pinned_next:
        raise BlsInvalid("BLS_PDF_SCHEDULE_CONFLICT")
    return {**state, "release_url": url, "schedule_url": REPORTS[factor]["schedule"]}


def _api_url(factor):
    return API_BASE + REPORTS[factor]["api_series"]


def _raw_decimal(value):
    if isinstance(value, (bool, type(None))) or not isinstance(value, (str, int, float, Decimal)):
        raise BlsInvalid("BLS_API_VALUE_INVALID")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise BlsInvalid("BLS_API_VALUE_INVALID") from exc
    if not result.is_finite():
        raise BlsInvalid("BLS_API_VALUE_INVALID")
    return result


def parse_api_latest(payload, factor, expected_period):
    """Accept the expected month only when v1 has no newer monthly row."""
    try:
        if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED" or payload.get("message"):
            raise BlsInvalid("BLS_API_STATUS_INVALID")
        result_group = payload["Results"]
        if not isinstance(result_group, dict) or set(result_group) != {"series"}:
            raise BlsInvalid("BLS_API_RESULTS_INVALID")
        series = result_group["series"]
        if not isinstance(series, list) or len(series) != 1 or series[0].get("seriesID") != REPORTS[factor]["api_series"]:
            raise BlsInvalid("BLS_API_SERIES_INVALID")
        rows = series[0]["data"]
        if not isinstance(rows, list) or not rows:
            raise BlsInvalid("BLS_API_DATA_INVALID")
        monthly = {}
        for row in rows:
            if not isinstance(row, dict):
                raise BlsInvalid("BLS_API_SCHEMA_INVALID")
            period_code = row["period"]
            if period_code == "M13":
                continue  # Annual average is never a monthly CPI or labor observation.
            if not isinstance(period_code, str) or re.fullmatch(r"M(0[1-9]|1[0-2])", period_code) is None:
                raise BlsInvalid("BLS_API_PERIOD_INVALID")
            year = row["year"]
            if not isinstance(year, str) or re.fullmatch(r"\d{4}", year) is None:
                raise BlsInvalid("BLS_API_PERIOD_INVALID")
            period = f"{year}-{period_code[1:]}"
            if period in monthly:
                raise BlsInvalid("BLS_API_DUPLICATE_PERIOD")
            monthly[period] = row
        if expected_period not in monthly or max(monthly, default="") != expected_period:
            raise BlsInvalid("BLS_API_PERIOD_MISMATCH")
        row = monthly[expected_period]
        if row.get("periodName") != datetime.strptime(expected_period, "%Y-%m").strftime("%B"):
            raise BlsInvalid("BLS_API_PERIOD_MISMATCH")
        if row.get("latest") not in (True, "true"):
            raise BlsInvalid("BLS_API_LATEST_INVALID")
        value = _raw_decimal(row["value"])
        if not (Decimal("0") <= value <= Decimal("25") if factor == "Arbeitsmarkt" else Decimal("0") < value < Decimal("1000")):
            raise BlsInvalid("BLS_API_VALUE_INVALID")
        return str(value)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, BlsInvalid):
            raise
        raise BlsInvalid("BLS_API_SCHEMA_INVALID") from exc


def _verified_previous(factor, state):
    """Accept only coherent PDF proof or matched API/FRED proof."""
    if not isinstance(state, dict) or not isinstance(state.get("period"), str):
        return None
    period = state["period"]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        return None
    published = _parse_aware(state.get("embargo_ends_at") or state.get("published_at"))
    due = _parse_aware(state.get("next_due_at"))
    if published is None or due is None or published >= due:
        return None
    pinned = PINNED_DUES[factor]
    if ((period in pinned and pinned[period] != published.isoformat())
            or (_successor(period) in pinned and pinned[_successor(period)] != due.isoformat())):
        return None
    if state.get("proof_source") == "BLS_API_V1":
        first = _parse_aware(state.get("first_observed_at"))
        if (period not in pinned or _successor(period) not in pinned
                or state.get("release_url") != _api_url(factor)
                or first is None or first < published + timedelta(hours=24) or first >= due):
            return None
        try:
            if _raw_decimal(state.get("raw_value")) != _raw_decimal(state.get("fred_raw_value")):
                return None
        except BlsInvalid:
            return None
    elif state.get("proof_source") == "BLS_PDF":
        if (factor != "Arbeitsmarkt" or period not in pinned or _successor(period) not in pinned
                or state.get("release_url") != _bls_pdf_url(factor)):
            return None
    elif (state.get("proof_source") not in (None, "DOL_PDF")
          or state.get("release_url") != _pdf_url(factor, published)):
        return None
    return period, published, due


def fetch_release_state(factor, *, session=requests, now=None, previous_state=None,
                        last_pdf_attempt=None, last_api_attempt=None, api_budget_blocked=False,
                        diagnostics=None):
    """Confirm the report from DOL, its verified BLS PDF or bounded API v1.

    A DOL PDF attempt always comes first. Only its genuine transport failure
    allows one direct, verified BLS PDF transport. Content or security failures
    never allow another source. API v1 retains the pinned release plus 24 hours.
    Persisted attempt times limit completed collector runs to one API request
    per factor and UTC day.
    Neither a planned date nor old FRED data proves publication.
    """
    report = REPORTS[factor]
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise BlsInvalid("BLS_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    previous = _verified_previous(factor, previous_state)
    if (previous is not None and previous_state.get("proof_source") == "BLS_API_V1"
            and _parse_aware(previous_state.get("first_observed_at")) > now):
        previous = None
    if previous is not None and previous[1] <= now < previous[2]:
        return {**previous_state, "embargo_ends_at": previous[1].isoformat()}

    candidates = [(published, period) for period, value in PINNED_DUES[factor].items()
                  if (published := _parse_aware(value)) is not None and published <= now]
    if previous is not None and previous[2] <= now:
        successor = _successor(previous[0])
        if (successor in PINNED_DUES[factor]
                and PINNED_DUES[factor][successor] != previous[2].isoformat()):
            raise BlsInvalid("BLS_SCHEDULE_CONFLICT")
        candidates.append((previous[2], successor))
    if not candidates:
        raise BlsInvalid("BLS_PDF_RELEASE_DATE_UNKNOWN")
    published, period = max(candidates)
    url = _pdf_url(factor, published)
    if (last_pdf_attempt is not None and
            timedelta(0) <= now - last_pdf_attempt < timedelta(hours=1)):
        raise BlsInvalid("BLS_PDF_RECHECK_COOLDOWN")
    try:
        response = _request_pdf(session, url, now, diagnostics, "pdf_status")
    except requests.exceptions.RequestException as error:
        if not _pdf_unreachable(error, session, "www.dol.gov", diagnostics, "pdf_status"):
            raise
        alternate_url = _bls_pdf_url(factor)
        if alternate_url is not None:
            try:
                alternate = _request_pdf(session, alternate_url, now, diagnostics, "bls_pdf_status")
            except requests.exceptions.RequestException as alternate_error:
                if isinstance(alternate_error, requests.exceptions.SSLError):
                    raise
                if not (_pdf_unreachable(alternate_error, session, "www.bls.gov", diagnostics, "bls_pdf_status")
                        or (alternate_error.args == ("PROVIDER_COOLDOWN",)
                            and _host_blocked(session, "www.bls.gov", now))):
                    raise
            else:
                state = _pdf_state(alternate, factor, now, period, published, alternate_url)
                return {**state, "proof_source": "BLS_PDF"}
        if (period not in PINNED_DUES[factor]
                or _successor(period) not in PINNED_DUES[factor]
                or now >= _parse_aware(PINNED_DUES[factor][_successor(period)])):
            raise BlsInvalid("BLS_API_SCHEDULE_UNKNOWN") from error
        if now < published + timedelta(hours=24):
            raise BlsInvalid("BLS_API_WAIT_24H") from error
        if api_budget_blocked:
            raise BlsInvalid("BLS_API_ATTEMPT_STATE_INVALID") from error
        if last_api_attempt is not None and last_api_attempt.date() == now.date():
            raise BlsInvalid("BLS_API_DAILY_LIMIT") from error
        api_url = _api_url(factor)
        usage = getattr(session, "usage", None)
        before = (usage.get("api.bls.gov", {}).get("requests_this_run", 0)
                  if isinstance(usage, dict) else None)
        try:
            api_response = session.get(api_url, headers={"User-Agent": USER_AGENT}, timeout=15)
            if isinstance(diagnostics, dict):
                status_code = getattr(api_response, "status_code", None)
                diagnostics["api_status"] = "HTTP_" + str(status_code) if type(status_code) is int else "UNKNOWN"
        except requests.exceptions.RequestException:
            if isinstance(diagnostics, dict):
                diagnostics["api_status"] = "TRANSPORT_ERROR"
            raise
        finally:
            after = (usage.get("api.bls.gov", {}).get("requests_this_run", 0)
                     if isinstance(usage, dict) else None)
            attempted = after > before if isinstance(before, int) and isinstance(after, int) else True
            if attempted and isinstance(diagnostics, dict):
                diagnostics["api_attempted"] = True
        api_response.raise_for_status()
        raw_value = parse_api_latest(api_response.json(), factor, period)
        observed = (_parse_aware(usage.get("api.bls.gov", {}).get("last_checked_at"))
                    if isinstance(usage, dict) else None) or now
        next_due = _parse_aware(PINNED_DUES[factor][_successor(period)])
        if not published + timedelta(hours=24) <= observed < next_due:
            raise BlsInvalid("BLS_API_OBSERVATION_TIME_INVALID")
        return {"period": period, "embargo_ends_at": published.isoformat(),
                "next_due_at": next_due.isoformat(),
                "first_observed_at": observed.isoformat(), "raw_value": raw_value,
                "proof_source": "BLS_API_V1", "release_url": api_url,
                "schedule_url": report["schedule"]}
    return _pdf_state(response, factor, now, period, published, url)
