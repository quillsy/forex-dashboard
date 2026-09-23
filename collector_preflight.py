"""Throttle scheduled GitHub collector attempts before installing dependencies."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


MIN_INTERVAL = timedelta(minutes=30)
DAILY_SCHEDULE = "0 22 * * *"
HOURLY_MINUTES = {7, 17, 27, 37, 47, 57}
WATCHDOG_GRACE = timedelta(minutes=10)
MAX_WATCHDOG_HOURLY_DELAY = timedelta(minutes=90)
MAX_WATCHDOG_DAILY_DELAY = timedelta(minutes=90)


def _timestamp(path, field):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get(field) if isinstance(payload, dict) else None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _recent_attempt(root, now):
    # This marker is committed before any provider request. It covers runs
    # whose later CORE/status commit fails or whose job is interrupted.
    marker = root / "daily_collection_status.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return True
        raw = payload.get("last_provider_attempt_at")
    except FileNotFoundError:
        raw = None  # Legacy checkout before the attempt marker was introduced.
    except (OSError, ValueError, AttributeError, TypeError):
        return True
    if raw is not None:
        try:
            reserved = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if reserved.tzinfo is None:
                return True
            reserved = reserved.astimezone(timezone.utc)
        except (ValueError, AttributeError, TypeError):
            return True
        # A future reservation is a clock/provenance anomaly. Do not risk
        # duplicate requests until the marker is inspected and repaired.
        if reserved > now or now - reserved < MIN_INTERVAL:
            return True
    timestamps = [
        _timestamp(root / "live_core_data.json", "completed_at"),
        _timestamp(root / "data_collection_status.json", "last_run_timestamp"),
    ]
    if any(value is not None and value > now for value in timestamps):
        return False
    latest = max((value for value in timestamps if value is not None), default=None)
    return latest is not None and now - latest < MIN_INTERVAL


def _daily_slot(now):
    slot = now.replace(hour=22, minute=0, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def _daily_attempted(root, slot, now):
    marker = root / "daily_collection_status.json"
    attempt = _timestamp(marker, "last_daily_attempt_at")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        expected = slot.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if isinstance(payload, dict) and payload.get("last_daily_attempt_slot_utc") == expected:
            return attempt is not None and slot <= attempt <= now
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    # A pre-marker daily run can still be recognized during rollout.
    path = root / "data_collection_status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        previous = _timestamp(path, "last_run_timestamp")
        return status.get("mode") == "daily" and previous is not None and slot <= previous <= now
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _watchdog_slot(raw, now):
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z", raw):
        return None
    try:
        slot = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    daily = slot.hour == 22 and slot.minute == 0
    if not daily and slot.minute not in HOURLY_MINUTES:
        return None
    age = now - slot
    max_delay = MAX_WATCHDOG_DAILY_DELAY if daily else MAX_WATCHDOG_HOURLY_DELAY
    return (slot, daily) if WATCHDOG_GRACE <= age <= max_delay else None


def preflight_decision(event, schedule, watchdog_slot="", root=Path("."), now=None,
                       dispatch_mode=""):
    """Return (run_collector, mode); invalid watchdog inputs fail closed."""
    now = now or datetime.now(timezone.utc)
    if event == "workflow_dispatch":
        if dispatch_mode == "manual" and not watchdog_slot:
            return True, "live"
        if dispatch_mode != "watchdog" or not watchdog_slot:
            return False, "live"
        parsed = _watchdog_slot(watchdog_slot, now)
        if parsed is None:
            return False, "live"
        slot, daily = parsed
        if daily:
            # A watchdog rescue is optional: defer it if a live run recently
            # reserved provider calls. Recheck at the next watchdog tick; the
            # original 22:00 schedule retains its separate Daily priority.
            return not _daily_attempted(root, slot, now) and not _recent_attempt(root, now), "daily"
        return not _recent_attempt(root, now), "live"
    if event != "schedule":
        return True, "live"
    if schedule == DAILY_SCHEDULE:
        slot = _daily_slot(now)
        # Snapshots are dated by the runner's current UTC day. A much-delayed
        # 22:00 event must not claim to have recorded the previous day.
        if now - slot > MAX_WATCHDOG_DAILY_DELAY:
            return not _recent_attempt(root, now), "live"
        return not _daily_attempted(root, slot, now), "daily"
    return not _recent_attempt(root, now), "live"


def should_collect(event, schedule, root=Path("."), now=None, watchdog_slot="",
                   dispatch_mode=""):
    """Compatibility wrapper for the scheduled collector tests."""
    return preflight_decision(event, schedule, watchdog_slot, root, now, dispatch_mode)[0]


def main():
    collect, mode = preflight_decision(os.environ.get("FX_WORKFLOW_EVENT"),
                                       os.environ.get("FX_WORKFLOW_SCHEDULE"),
                                       os.environ.get("FX_WATCHDOG_SLOT", ""),
                                       dispatch_mode=os.environ.get("FX_DISPATCH_MODE", ""))
    output = "run_collector=" + ("true" if collect else "false") + "\n"
    output += "collector_mode=" + mode + "\n"
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(output)
    print(output.strip())


if __name__ == "__main__":
    main()
