"""Validated official annual headline CPI; publication time is never guessed."""
from datetime import datetime, timezone
import math
import re

import requests

ESTAT_TABLE = "0004052037"
ESTAT_URL = "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData"
ABS_URL = "https://data.api.abs.gov.au/rest/data/CPI/3.10001.10.50.M"


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
    return {"value": records[period], "date": period + "-01", "refperiod": period,
            "unit": "percent_yoy", "frequency": "monthly", "seasonal_adjustment": "unadjusted",
            "source": source, "series_id": series, "checked_at": now.isoformat(), "published_at": None}


def parse_estat_cpi(payload, now=None):
    now = _now(now)
    root = payload["GET_STATS_DATA"]
    if str(root["RESULT"]["STATUS"]) != "0":
        raise ValueError("ESTAT_FAILURE")
    data = root["STATISTICAL_DATA"]
    table = data["TABLE_INF"]
    if table["@id"] != ESTAT_TABLE or table["STAT_NAME"]["@code"] != "00200573":
        raise ValueError("ESTAT_TABLE_INVALID")
    classes = {entry["@id"]: _list(entry["CLASS"]) for entry in data["CLASS_INF"]["CLASS_OBJ"]}
    expected = {"tab": ("3", "前年同月比"), "cat01": ("0001", "0001 総合"), "area": ("00000", "全国")}
    for dimension, (code, name) in expected.items():
        entries = classes[dimension]
        if len(entries) != 1 or entries[0].get("@code") != code or entries[0].get("@name") != name:
            raise ValueError("ESTAT_IDENTITY_INVALID")
    if classes["tab"][0].get("@unit") != "%":
        raise ValueError("ESTAT_UNIT_INVALID")
    records = {}
    for item in _list(data["DATA_INF"].get("VALUE", [])):
        if any(item.get(key) != value for key, value in
               {"@tab": "3", "@cat01": "0001", "@area": "00000", "@unit": "%"}.items()):
            raise ValueError("ESTAT_OBSERVATION_IDENTITY_INVALID")
        match = re.fullmatch(r"(\d{4})00(0[1-9]|1[0-2])\2", item.get("@time", ""))
        if not match:
            continue  # Annual and quarterly records are not monthly CPI.
        if item.get("$") in {"***", "-", "", None}:
            continue
        _put(records, f"{match[1]}-{match[2]}", item["$"], now)
    return _result(records, now, "Statistics Japan e-Stat 2025-base headline CPI YoY", ESTAT_TABLE)


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
    attrs = structure["attributes"]["series"]
    unit_positions = [i for i, attr in enumerate(attrs) if attr["id"] == "UNIT_MEASURE"]
    if len(unit_positions) != 1:
        raise ValueError("ABS_UNIT_INVALID")
    position = unit_positions[0]
    unit = attrs[position]["values"][series["attributes"][position]]
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


def fetch_official_cpi(currency, *, client=None, estat_key=None, now=None):
    """Return validated observation or None, using injected budget-aware transport."""
    client = client or requests
    try:
        if currency == "JPY":
            if not estat_key:
                return None
            response = client.get(ESTAT_URL, params={"appId": estat_key, "statsDataId": ESTAT_TABLE,
                "cdCat01": "0001", "cdArea": "00000", "cdTab": "3", "limit": 24}, timeout=15)
            parser = parse_estat_cpi
        elif currency == "AUD":
            response = client.get(ABS_URL, params={"lastNObservations": 24},
                                  headers={"Accept": "application/json"}, timeout=15)
            parser = parse_abs_cpi
        else:
            return None
        response.raise_for_status()
        return parser(response.json(), now=now)
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None
