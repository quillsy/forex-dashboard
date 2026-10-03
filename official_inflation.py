"""Validated official annual headline CPI; publication time is never guessed."""
from datetime import datetime, timezone
import math
import re
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ESTAT_TABLE = "0004052037"
ESTAT_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
ABS_URL = "https://data.api.abs.gov.au/rest/data/CPI/3.10001.10.50.M"
ABS_RELEASES_URL = "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia"


def _list(value):
    return value if isinstance(value, list) else [value]


def _now(now):
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _put(records, period, value, now):
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", period):
        raise ValueError("CPI_PERIOD_INVALID")
    if period >= now.strftime("%Y-%m"):
        return
    if isinstance(value, bool):
        raise ValueError("CPI_VALUE_INVALID")
    numeric = float(value)
    if not math.isfinite(numeric) or not -25 <= numeric <= 25:
        raise ValueError("CPI_VALUE_INVALID")
    if period in records and records[period] != numeric:
        raise ValueError("CPI_CONFLICT")
    records[period] = numeric


def _result(records, now, source, series):
    if not records:
        return None
    period = max(records)
    return {"value": records[period], "date": period + "-01", "refperiod": period, "reference_period": period,
            "unit": "percent_yoy", "frequency": "monthly", "seasonal_adjustment": "unadjusted",
            "source": source, "series_id": series, "checked_at": now.isoformat(), "published_at": None}


def parse_estat_cpi(payload, now=None):
    now = _now(now)
    root = payload["GET_STATS_DATA"]
    if str(root["RESULT"]["STATUS"]) != "0":
        raise ValueError("ESTAT_FAILURE")
    data = root["STATISTICAL_DATA"]
    # A successful HTTP/API status does not establish a complete result.
    # e-Stat documents NEXT_KEY as the continuation row for truncated data.
    next_key = data.get("RESULT_INF", {}).get("NEXT_KEY")
    if next_key not in (None, "", 0, "0"):
        raise ValueError("ESTAT_INCOMPLETE_RESPONSE")
    table = data["TABLE_INF"]
    if table["@id"] != ESTAT_TABLE or table["STAT_NAME"]["@code"] != "00200573":
        raise ValueError("ESTAT_TABLE_INVALID")
    classes = {}
    for entry in _list(data["CLASS_INF"]["CLASS_OBJ"]):
        identity = entry["@id"]
        if not isinstance(identity, str) or identity in classes:
            raise ValueError("ESTAT_IDENTITY_INVALID")
        entries = _list(entry["CLASS"])
        codes = [item["@code"] for item in entries]
        if any(not isinstance(code, str) for code in codes) or len(codes) != len(set(codes)):
            raise ValueError("ESTAT_IDENTITY_INVALID")
        classes[identity] = entries
    time_codes = {item["@code"] for item in classes["time"]}
    if not time_codes or any(not re.fullmatch(r"\d{4}00(0[1-9]|1[0-2])\1", code) for code in time_codes):
        raise ValueError("ESTAT_TIME_INVALID")
    expected = {"tab": ("3", "前年同月比"), "cat01": ("0001", "0001 総合"), "area": ("00000", "全国")}
    for dimension, (code, name) in expected.items():
        entries = classes[dimension]
        if len(entries) != 1 or entries[0].get("@code") != code or entries[0].get("@name") != name:
            raise ValueError("ESTAT_IDENTITY_INVALID")
    if classes["tab"][0].get("@unit") != "%":
        raise ValueError("ESTAT_UNIT_INVALID")
    records = {}
    seen_times = set()
    missing_months = set()
    for item in _list(data["DATA_INF"].get("VALUE", [])):
        if any(item.get(key) != value for key, value in
               {"@tab": "3", "@cat01": "0001", "@area": "00000", "@unit": "%"}.items()):
            raise ValueError("ESTAT_OBSERVATION_IDENTITY_INVALID")
        code = item.get("@time", "")
        if not isinstance(code, str) or code not in time_codes or code in seen_times:
            raise ValueError("ESTAT_TIME_INVALID")
        seen_times.add(code)
        match = re.fullmatch(r"(\d{4})00(0[1-9]|1[0-2])\2", code)
        if not match:
            raise ValueError("ESTAT_TIME_INVALID")
        if item.get("$") in {"***", "-", "", None}:
            missing_months.add(f"{match[1]}-{match[2]}")
            continue
        # Validate numerical schema even when the reference month is future.
        if isinstance(item["$"], bool) or not isinstance(item["$"], (str, int, float)):
            raise ValueError("CPI_VALUE_INVALID")
        numeric = float(item["$"])
        if not math.isfinite(numeric) or not -25 <= numeric <= 25:
            raise ValueError("CPI_VALUE_INVALID")
        _put(records, f"{match[1]}-{match[2]}", numeric, now)
    if records and any(max(records) <= period < now.strftime("%Y-%m") for period in missing_months):
        raise ValueError("ESTAT_LATEST_VALUE_MISSING")
    return _result(records, now, "Statistics Japan e-Stat 2025-base headline CPI YoY", ESTAT_TABLE)



ESTAT_RELEASE_URL = "https://www.stat.go.jp/data/cpi/sokuhou/tsuki/index-z.html"
ESTAT_CALENDAR_URL = "https://www.stat.go.jp/english/data/cpi/1582.html"


def parse_japan_cpi_release(html, now=None):
    """National headline only; the source supplies a date, not a publication time."""
    page = BeautifulSoup(html, "html.parser")
    titles = page.select("#section h1")
    if len(titles) != 1:
        raise ValueError("ESTAT_RELEASE_INVALID")
    title = re.sub(r"\s+", "", titles[0].get_text())
    match = re.fullmatch(r"2025年基準消費者物価指数全国(\d{4})年（令和(\d+)年）(\d{1,2})月分（(\d{4})年(\d{1,2})月(\d{1,2})日公表）", title)
    if not match:
        raise ValueError("ESTAT_RELEASE_INVALID")
    try:
        year, era, month, ry, rm, rd = map(int, match.groups())
        if year < 2019 or era != year - 2018:
            raise ValueError("ESTAT_RELEASE_INVALID")
        period = datetime(year, month, 1).strftime("%Y-%m")
        released = datetime(ry, rm, rd).date()
    except ValueError as exc:
        raise ValueError("ESTAT_RELEASE_INVALID") from exc
    if released > _now(now).astimezone(ZoneInfo("Asia/Tokyo")).date() or released < datetime(year + (month == 12), month % 12 + 1, 1).date():
        raise ValueError("ESTAT_RELEASE_INVALID")
    labels = [node for node in page.select("#section p strong") if node.get_text(strip=True) == "総合指数"]
    if len(labels) != 1:
        raise ValueError("ESTAT_RELEASE_INVALID")
    fragments = []
    for node in labels[0].next_siblings:
        if getattr(node, "name", None) == "strong":
            break
        fragments.append(node.get_text() if hasattr(node, "get_text") else str(node))
    text = re.sub(r"\s+", "", "".join(fragments))
    rates = re.findall(r"前年同月比(?:は)?([0-9]+(?:\.[0-9]+)?)[%％](?:の)?(上昇|下落|低下|横ばい)?", text)
    if len(rates) != 1:
        raise ValueError("ESTAT_RELEASE_INVALID")
    magnitude, direction = rates[0]
    value = float(magnitude)
    if value and direction not in ("上昇", "下落", "低下") or direction == "横ばい" and value != 0:
        raise ValueError("ESTAT_RELEASE_INVALID")
    if direction in ("下落", "低下"):
        value = -value
    if not -25 <= value <= 25:
        raise ValueError("ESTAT_RELEASE_INVALID")
    return {"latest_period": period, "value": value, "release_date_known": released.isoformat()}


def parse_japan_cpi_calendar(html, latest_period):
    """Read only the Japan columns; carry years from explicit source anchors."""
    page = BeautifulSoup(html, "html.parser")
    tables = page.select("#section table.datatable")
    if len(tables) != 1:
        raise ValueError("ESTAT_CALENDAR_INVALID")
    rows = tables[0].select("tr")
    if len(rows) < 3:
        raise ValueError("ESTAT_CALENDAR_INVALID")
    headers = rows[0].find_all("th", recursive=False)
    if len(headers) != 3 or headers[0].get_text(" ", strip=True) != "Japan" or headers[0].get("colspan") != "2" or "Ku-area of Tokyo" not in headers[1].get_text(" ", strip=True):
        raise ValueError("ESTAT_CALENDAR_INVALID")
    if [h.get_text(" ", strip=True) for h in rows[1].find_all("th", recursive=False)] != ["Survey month", "Date of release"] * 2:
        raise ValueError("ESTAT_CALENDAR_INVALID")
    months = {datetime(2000, m, 1).strftime("%B"): m for m in range(1, 13)}
    previous_period = previous_due = None
    entries = {}
    for row in rows[2:]:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 5:
            raise ValueError("ESTAT_CALENDAR_INVALID")
        a = re.fullmatch(r"([A-Za-z]+)(?:,? (\d{4}))?", " ".join(cells[0].get_text().split()))
        b = re.fullmatch(r"([A-Za-z]+) (\d{1,2})(?:,? (\d{4}))?", " ".join(cells[1].get_text().split()))
        if not a or not b or a[1] not in months or b[1] not in months:
            raise ValueError("ESTAT_CALENDAR_INVALID")
        try:
            pm, dm = months[a[1]], months[b[1]]
            py = int(a[2]) if a[2] else previous_period.year + (pm < previous_period.month)
            dy = int(b[3]) if b[3] else previous_due.year + (dm < previous_due.month)
            period, due = datetime(py, pm, 1), datetime(dy, dm, int(b[2]))
        except (ValueError, AttributeError) as exc:
            raise ValueError("ESTAT_CALENDAR_INVALID") from exc
        if due < datetime(py + (pm == 12), pm % 12 + 1, 1) or (previous_period and (period.year * 12 + period.month != previous_period.year * 12 + previous_period.month + 1 or due <= previous_due)):
            raise ValueError("ESTAT_CALENDAR_INVALID")
        entries[period.strftime("%Y-%m")] = due.date().isoformat()
        previous_period, previous_due = period, due
    year, month = map(int, latest_period.split("-"))
    following = f"{year + (month == 12):04d}-{month % 12 + 1:02d}"
    if latest_period not in entries:
        raise ValueError("ESTAT_CALENDAR_INVALID")
    return {"current_release_date": entries[latest_period], "next_due_date": entries.get(following)}

def parse_abs_cpi(payload, now=None):
    now = _now(now)
    structure = payload["structure"]
    dimensions = structure["dimensions"]["series"]
    expected = {"MEASURE": ("3", "Percentage change from previous year"),
                "INDEX": ("10001", "All groups CPI"), "TSEST": ("10", "Original"),
                "REGION": ("50", "Australia"), "FREQ": ("M", "Monthly")}
    if len(dimensions) != 5 or {d["id"] for d in dimensions} != set(expected):
        raise ValueError("ABS_DIMENSIONS_INVALID")
    datasets = payload["dataSets"]
    if len(datasets) != 1 or len(datasets[0]["series"]) != 1:
        raise ValueError("ABS_SERIES_COUNT_INVALID")
    key, series = next(iter(datasets[0]["series"].items()))
    indices = key.split(":")
    if len(indices) != 5:
        raise ValueError("ABS_SERIES_KEY_INVALID")
    for dimension, index in zip(dimensions, indices):
        value = dimension["values"][int(index)]
        if (value["id"], value["name"]) != expected[dimension["id"]]:
            raise ValueError("ABS_SERIES_IDENTITY_INVALID")
    attribute_groups = structure["attributes"]
    # The current CPI contract supplies unscaled percentage points. UNIT_MULT
    # is not defined by its DSD; do not silently interpret an added multiplier
    # at any attachment level as the same percentage observation. Its sole
    # UNIT_MEASURE is series-attached; an additional unit is not this contract.
    if not isinstance(attribute_groups, dict) or any(
            not isinstance(group, list) or any(
                not isinstance(attr, dict) or attr.get("id") == "UNIT_MULT" or
                (level != "series" and attr.get("id") == "UNIT_MEASURE") for attr in group)
            for level, group in attribute_groups.items()):
        raise ValueError("ABS_UNIT_INVALID")
    attrs = attribute_groups["series"]
    for level, container in (("dataSet", datasets[0]), ("series", series)):
        references = container.get("attributes", [])
        descriptors = attribute_groups.get(level, [])
        if not isinstance(references, list) or len(references) > len(descriptors):
            raise ValueError("ABS_UNIT_INVALID")
        for i, reference in enumerate(references):
            if reference is None:
                continue  # Optional metadata is allowed to have no value.
            values = descriptors[i]["values"]
            if (not isinstance(values, list) or type(reference) is not int or
                    not 0 <= reference < len(values)):
                raise ValueError("ABS_UNIT_INVALID")
    unit_positions = [i for i, attr in enumerate(attrs) if attr["id"] == "UNIT_MEASURE"]
    if len(unit_positions) != 1:
        raise ValueError("ABS_UNIT_INVALID")
    position = unit_positions[0]
    if len(series.get("attributes", [])) <= position:
        raise ValueError("ABS_UNIT_INVALID")
    unit_reference = series["attributes"][position]
    unit_values = attrs[position]["values"]
    if type(unit_reference) is not int or not 0 <= unit_reference < len(unit_values):
        raise ValueError("ABS_UNIT_INVALID")
    unit = unit_values[unit_reference]
    if unit["id"] != "PCT" or unit["name"] != "Percent":
        raise ValueError("ABS_UNIT_INVALID")
    times = structure["dimensions"]["observation"]
    if len(times) != 1 or times[0]["id"] != "TIME_PERIOD":
        raise ValueError("ABS_TIME_DIMENSION_INVALID")
    records = {}
    for index, observation in series["observations"].items():
        if observation[0] is None:
            continue
        _put(records, times[0]["values"][int(index)]["id"], observation[0], now)
    return _result(records, now, "Australian Bureau of Statistics headline CPI YoY", "CPI/3.10001.10.50.M")


def parse_abs_release_index(html):
    """Read the current monthly CPI period and next UTC deadline from ABS."""
    page = BeautifulSoup(html, "html.parser")
    latest = page.select_one("#block-views-block-topic-releases-listing-topic-latest-release-block")
    future = page.select_one("#block-views-block-topic-releases-listing-future-releases-block")
    if latest is None or future is None:
        raise ValueError("ABS_RELEASE_INDEX_INVALID")
    links = latest.select(".views-row a")
    rows = future.select(".views-row")
    if len(links) != 1:
        raise ValueError("ABS_RELEASE_INDEX_INVALID")

    def period(label):
        match = re.fullmatch(r"Consumer Price Index, Australia, ([A-Z][a-z]+) (\d{4})", label)
        if not match:
            raise ValueError("ABS_RELEASE_INDEX_INVALID")
        try:
            return datetime.strptime(f"{match[1]} {match[2]}", "%B %Y").strftime("%Y-%m")
        except ValueError as exc:
            raise ValueError("ABS_RELEASE_INDEX_INVALID") from exc

    current_period = period(links[0].get_text(" ", strip=True))
    if not rows:
        # ABS may not have announced a later date yet. Recheck the index and
        # data hourly instead of inventing a monthly publication deadline.
        return {"latest_period": current_period, "next_due_at": None}
    upcoming = rows[0]
    next_period = period(upcoming.get_text(" ", strip=True).split("Release date", 1)[0].strip())
    year, month = map(int, current_period.split("-"))
    expected_next = f"{year + (month == 12):04d}-{month % 12 + 1:02d}"
    if next_period != expected_next:
        raise ValueError("ABS_RELEASE_INDEX_INVALID")
    times = upcoming.select("time[datetime]")
    if len(times) != 1:
        raise ValueError("ABS_RELEASE_INDEX_INVALID")
    try:
        due = datetime.fromisoformat(times[0]["datetime"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ABS_RELEASE_INDEX_INVALID") from exc
    if due.tzinfo is None:
        raise ValueError("ABS_RELEASE_INDEX_INVALID")
    return {"latest_period": current_period, "next_due_at": due.astimezone(timezone.utc).isoformat()}


_DIAGNOSTIC_ERRORS = frozenset({
    "ESTAT_TIME_INVALID", "ESTAT_LATEST_VALUE_MISSING", "ESTAT_RELEASE_INVALID", "ESTAT_CALENDAR_INVALID",
    "ESTAT_RELEASE_CONFLICT", "ESTAT_API_RELEASE_LAG", "ESTAT_DUE_UNCONFIRMED",
    "ESTAT_INCOMPLETE_RESPONSE", "ESTAT_FAILURE", "ESTAT_TABLE_INVALID", "ESTAT_IDENTITY_INVALID", "ESTAT_UNIT_INVALID",
    "ESTAT_OBSERVATION_IDENTITY_INVALID", "ABS_DIMENSIONS_INVALID", "ABS_SERIES_COUNT_INVALID",
    "ABS_SERIES_KEY_INVALID", "ABS_SERIES_IDENTITY_INVALID", "ABS_UNIT_INVALID",
    "ABS_TIME_DIMENSION_INVALID", "CPI_PERIOD_INVALID", "CPI_VALUE_INVALID", "CPI_CONFLICT",
    "ABS_RELEASE_INDEX_INVALID", "ABS_API_RELEASE_LAG", "ABS_RELEASE_CONFLICT", "ABS_RELEASE_DUE_UNCONFIRMED",
})


def fetch_official_cpi(currency, *, client=None, estat_key=None, now=None, diagnostics=None):
    """Return observation or None; optional diagnostics contains allowlisted fields.

    Never copy exception messages, response bodies, URLs or credentials to the
    diagnostics. provider_status is exclusively e-Stat's bounded numeric STATUS.
    """
    client = client or requests
    diagnostic = diagnostics if isinstance(diagnostics, dict) else {}
    diagnostic.clear()
    diagnostic["code"] = "SOURCE_UNAVAILABLE"
    phase = "transport"
    try:
        if currency == "JPY":
            if not estat_key:
                diagnostic["code"] = "KEY_MISSING"
                return None
            # limit is a first-page size, not "latest N observations". Select
            # the last 24 completed months explicitly in one provider request.
            # https://www.e-stat.go.jp/api/api-info/e-stat-manual3-0
            checked = _now(now)
            serial = checked.year * 12 + checked.month - 1
            periods = []
            for offset in range(1, 25):
                year, zero_month = divmod(serial - offset, 12)
                month = zero_month + 1
                periods.append(f"{year}00{month:02d}{month:02d}")
            response = client.get(ESTAT_URL, params={"appId": estat_key, "statsDataId": ESTAT_TABLE,
                "cdCat01": "0001", "cdArea": "00000", "cdTab": "3",
                "cdTime": ",".join(periods), "limit": 24}, timeout=15)
            parser = parse_estat_cpi
        elif currency == "AUD":
            response = client.get(ABS_URL, params={"lastNObservations": 24},
                                  headers={"Accept": "application/json"}, timeout=15)
            parser = parse_abs_cpi
        else:
            diagnostic["code"] = "UNSUPPORTED_CURRENCY"
            return None
        response.raise_for_status()
        phase = "json"
        payload = response.json()
        phase = "schema"
        if currency == "JPY":
            status = payload.get("GET_STATS_DATA", {}).get("RESULT", {}).get("STATUS")
            if type(status) is int and 0 <= status <= 9999:
                diagnostic["provider_status"] = status
            elif isinstance(status, str) and re.fullmatch(r"[0-9]{1,4}", status):
                diagnostic["provider_status"] = int(status)
        result = parser(payload, now=now)
        if result is not None and currency == "AUD":
            result["source_url"] = ABS_URL
            phase = "release_index"
            release = client.get(ABS_RELEASES_URL, timeout=15)
            release.raise_for_status()
            calendar = parse_abs_release_index(release.text)
            result["next_due_at"] = calendar["next_due_at"]
            if calendar["next_due_at"] is None:
                result["needs_hourly_check"] = True
            checked = _now(now)
            if result["reference_period"] < calendar["latest_period"]:
                diagnostic["code"] = "ABS_API_RELEASE_LAG"
                result.update(_validation="UNVERIFIED", _reason="ABS-CPI-API liefert eine ältere Referenzperiode als die amtliche Veröffentlichung")
            elif result["reference_period"] > calendar["latest_period"]:
                diagnostic["code"] = "ABS_RELEASE_CONFLICT"
                result.update(_validation="UNVERIFIED", _reason="ABS-CPI-API und amtliche Veröffentlichungsseite widersprechen sich")
            elif calendar["next_due_at"] is not None and checked >= _now(calendar["next_due_at"]):
                diagnostic["code"] = "ABS_RELEASE_DUE_UNCONFIRMED"
                result.update(_validation="UNVERIFIED", _reason="Neue ABS-CPI-Veröffentlichung fällig; aktuelle Referenzperiode unbestätigt")
        if result is not None and currency == "JPY":
            phase = "estat_release"
            release_response = client.get(ESTAT_RELEASE_URL, timeout=15)
            release_response.raise_for_status()
            release = parse_japan_cpi_release(release_response.text, now)
            phase = "estat_calendar"
            calendar_response = client.get(ESTAT_CALENDAR_URL, timeout=15)
            calendar_response.raise_for_status()
            calendar = parse_japan_cpi_calendar(calendar_response.text, release["latest_period"])
            result["release_date_known"] = release["release_date_known"]
            result["next_due_date"] = calendar["next_due_date"]
            result["next_due_precision"] = "date_only_start_of_JP_day"
            result["published_at"] = None
            due = calendar["next_due_date"]
            result["next_due_at"] = (datetime.fromisoformat(due).replace(tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(timezone.utc).isoformat() if due else None)
            if due is None:
                result["needs_hourly_check"] = True
            code = None
            if result["reference_period"] < release["latest_period"]:
                code = "ESTAT_API_RELEASE_LAG"
            elif result["reference_period"] != release["latest_period"] or result["value"] != release["value"] or calendar["current_release_date"] not in (None, release["release_date_known"]):
                code = "ESTAT_RELEASE_CONFLICT"
            elif due and _now(now).astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat() >= due:
                code = "ESTAT_DUE_UNCONFIRMED"
            if code:
                diagnostic["code"] = code
                result.update(_validation="UNVERIFIED", _reason={
                    "ESTAT_API_RELEASE_LAG": "e-Stat-CPI liefert eine ältere Referenzperiode als die nationale Veröffentlichung",
                    "ESTAT_RELEASE_CONFLICT": "e-Stat-CPI und nationale Veröffentlichung oder Veröffentlichungskalender widersprechen sich",
                    "ESTAT_DUE_UNCONFIRMED": "Neue nationale CPI-Veröffentlichung fällig; neue Referenzperiode noch unbestätigt",
                }[code])
            result["source_url"] = "https://www.e-stat.go.jp/en/stat-search/database?layout=dataset&statdisp_id=0004052037"
        if diagnostic["code"] == "SOURCE_UNAVAILABLE":
            diagnostic["code"] = "OK" if result is not None else "NO_ELIGIBLE_OBSERVATION"
        return result
    except requests.RequestException as error:
        if currency == "JPY":
            from live_data import temporary_source_outage
            if phase == "json":
                diagnostic["code"] = "INVALID_JSON"
            elif not temporary_source_outage(error):
                diagnostic["code"] = "ESTAT_HTTP_INVALID"
            else:
                diagnostic["code"] = ("ESTAT_RELEASE_UNAVAILABLE" if phase == "estat_release" else
                                      "ESTAT_CALENDAR_UNAVAILABLE" if phase == "estat_calendar" else "HTTP_ERROR")
            return None
        diagnostic["code"] = "ESTAT_RELEASE_UNAVAILABLE" if phase == "estat_release" else "ESTAT_CALENDAR_UNAVAILABLE" if phase == "estat_calendar" else "ABS_RELEASE_INDEX_UNAVAILABLE" if phase == "release_index" else "HTTP_ERROR" if phase == "transport" else "INVALID_JSON"
        return None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        token = exc.args[0] if len(exc.args) == 1 else None
        diagnostic["code"] = token if isinstance(token, str) and token in _DIAGNOSTIC_ERRORS else (
            "INVALID_JSON" if phase == "json" else "SCHEMA_INVALID")
        return None
