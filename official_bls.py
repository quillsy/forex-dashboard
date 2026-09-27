"""Official BLS publication checks for the existing FRED USD CORE series.

The scheduled successor is a deadline, never proof of publication. The BLS
release hosted by the US Department of Labor supplies that proof; FRED still
supplies the CORE observation and score.
"""
import re
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


EASTERN = ZoneInfo("America/New_York")
REPORTS = {
    "Arbeitsmarkt": {
        "schedule": "https://www.bls.gov/schedule/news_release/empsit.htm",
        "schedule_title": "Schedule of Releases for the Employment Situation",
        "release_title": "THE EMPLOYMENT SITUATION",
        "series_id": "UNRATE",
        "archive_prefix": "empsit",
    },
    "Inflation": {
        "schedule": "https://www.bls.gov/schedule/news_release/cpi.htm",
        "schedule_title": "Schedule of Releases for the Consumer Price Index",
        "release_title": "CONSUMER PRICE INDEX",
        "series_id": "CPIAUCNS",
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
USER_AGENT = "Mozilla/5.0 fx-dashboard-source-verification/1.0"
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


def _verified_previous(factor, state):
    """Accept only a coherent, already confirmed bulletin, never a date alone."""
    if not isinstance(state, dict) or not isinstance(state.get("period"), str):
        return None
    period = state["period"]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        return None
    published = _parse_aware(state.get("embargo_ends_at") or state.get("published_at"))
    due = _parse_aware(state.get("next_due_at"))
    if (published is None or due is None or published >= due
            or state.get("release_url") != _pdf_url(factor, published)):
        return None
    pinned = PINNED_DUES[factor]
    if ((period in pinned and pinned[period] != published.isoformat())
            or (_successor(period) in pinned and pinned[_successor(period)] != due.isoformat())):
        return None
    return period, published, due


def fetch_release_state(factor, *, session=requests, now=None, previous_state=None,
                        last_pdf_attempt=None, diagnostics=None):
    """Confirm the current report from its official DOL-hosted BLS bulletin.

    Pinned schedule dates and a validated prior bulletin select a PDF URL only.
    Neither a planned date nor an old FRED observation proves publication.
    """
    report = REPORTS[factor]
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise BlsInvalid("BLS_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    previous = _verified_previous(factor, previous_state)
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
    usage = getattr(session, "usage", None)
    before = (usage.get("www.dol.gov", {}).get("requests_this_run", 0)
              if isinstance(usage, dict) else None)
    try:
        response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    finally:
        after = (usage.get("www.dol.gov", {}).get("requests_this_run", 0)
                 if isinstance(usage, dict) else None)
        attempted = after > before if isinstance(before, int) and isinstance(after, int) else True
        if attempted and isinstance(diagnostics, dict):
            diagnostics["pdf_attempted"] = True
    response.raise_for_status()
    if "pdf" not in response.headers.get("Content-Type", "").lower():
        raise BlsInvalid("BLS_PDF_CONTENT_TYPE_INVALID")
    state = parse_pdf_release(_pdf_text(response.content), factor, now)
    if state["period"] != period or state["embargo_ends_at"] != published.isoformat():
        raise BlsInvalid("BLS_PDF_RELEASE_CONFLICT")
    pinned_next = PINNED_DUES[factor].get(_successor(period))
    if pinned_next is not None and state["next_due_at"] != pinned_next:
        raise BlsInvalid("BLS_PDF_SCHEDULE_CONFLICT")
    return {**state, "release_url": url, "schedule_url": report["schedule"]}
