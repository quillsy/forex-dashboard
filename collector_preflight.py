"""Throttle scheduled GitHub collector attempts before installing dependencies."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


MIN_INTERVAL = timedelta(minutes=30)
DAILY_SCHEDULE = "0 22 * * *"


def _timestamp(path, field):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get(field) if isinstance(payload, dict) else None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def should_collect(event, schedule, root=Path("."), now=None):
    """Manual/push and daily runs always proceed; other cron runs respect cooldown."""
    if event != "schedule" or schedule == DAILY_SCHEDULE:
        return True
    now = now or datetime.now(timezone.utc)
    timestamps = [
        _timestamp(root / "live_core_data.json", "completed_at"),
        _timestamp(root / "data_collection_status.json", "last_run_timestamp"),
    ]
    if any(value is not None and value > now for value in timestamps):
        return True
    latest = max((value for value in timestamps if value is not None), default=None)
    return latest is None or now - latest >= MIN_INTERVAL


def main():
    collect = should_collect(os.environ.get("FX_WORKFLOW_EVENT"),
                             os.environ.get("FX_WORKFLOW_SCHEDULE"))
    output = "run_collector=" + ("true" if collect else "false") + "\n"
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(output)
    print(output.strip())


if __name__ == "__main__":
    main()
