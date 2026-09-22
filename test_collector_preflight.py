import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector_preflight import should_collect


class CollectorPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def write_time(self, name, field, age_minutes):
        timestamp = self.now - timedelta(minutes=age_minutes)
        (self.root / name).write_text(json.dumps({field: timestamp.isoformat()}))

    def decision(self, event="schedule", schedule="7,17,27,37,47,57 * * * *"):
        return should_collect(event, schedule, self.root, self.now)

    def test_recent_scheduled_run_skips_but_thirty_minutes_is_due(self):
        for elapsed, expected in ((29, False), (30, True), (31, True)):
            with self.subTest(elapsed=elapsed):
                self.write_time("live_core_data.json", "completed_at", elapsed)
                self.assertIs(self.decision(), expected)

    def test_latest_attempt_limits_retries_after_partial_failure(self):
        self.write_time("live_core_data.json", "completed_at", 80)
        self.write_time("data_collection_status.json", "last_run_timestamp", 8)
        self.assertFalse(self.decision())
        self.write_time("data_collection_status.json", "last_run_timestamp", 30)
        self.assertTrue(self.decision())

    def test_missing_malformed_and_future_timestamps_trigger_collection(self):
        self.assertTrue(self.decision())
        (self.root / "live_core_data.json").write_text('{"completed_at":"invalid"}')
        self.assertTrue(self.decision())
        self.write_time("live_core_data.json", "completed_at", -1)
        self.assertTrue(self.decision())

    def test_manual_push_and_daily_run_bypass_throttle(self):
        self.write_time("live_core_data.json", "completed_at", 1)
        self.assertTrue(self.decision("workflow_dispatch"))
        self.assertTrue(self.decision("push"))
        self.assertTrue(self.decision("schedule", "0 22 * * *"))


if __name__ == "__main__":
    unittest.main()
