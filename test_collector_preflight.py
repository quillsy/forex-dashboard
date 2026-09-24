import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector_preflight import preflight_decision, should_collect
from run_data_collection import update_daily_markers


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

    def test_durable_provider_reservation_throttles_uncommitted_collector(self):
        marker = self.root / "daily_collection_status.json"
        for elapsed, expected in ((0, False), (29, False), (30, True), (31, True)):
            with self.subTest(elapsed=elapsed):
                reserved = (self.now - timedelta(minutes=elapsed)).strftime("%Y-%m-%dT%H:%M:%SZ")
                update_daily_markers(reserved, {}, True, marker, reserve_provider_attempt=True)
                self.assertIs(self.decision(), expected)
        self.assertIsNone(json.loads(marker.read_text())["last_daily_attempt_at"])

    def test_daily_reservation_also_throttles_hourly_recovery(self):
        marker = self.root / "daily_collection_status.json"
        reserved = datetime(2026, 9, 23, 22, 12, tzinfo=timezone.utc)
        update_daily_markers(reserved.strftime("%Y-%m-%dT%H:%M:%SZ"), {}, False, marker,
                             reserve_provider_attempt=True)
        now = reserved + timedelta(minutes=7)
        self.assertFalse(should_collect("schedule", "7,17,27,37,47,57 * * * *", self.root, now))
        self.assertEqual(json.loads(marker.read_text())["last_daily_attempt_slot_utc"],
                         "2026-09-23T22:00:00.000Z")

    def test_hourly_schedule_recovers_missing_daily_slot_after_cooldown(self):
        slot = "7,17,27,37,47,57 * * * *"
        marker = self.root / "daily_collection_status.json"
        update_daily_markers("2026-09-23T22:07:00Z", {}, True, marker,
                             reserve_provider_attempt=True)
        self.assertEqual(preflight_decision("schedule", slot, "", self.root,
                                            datetime(2026, 9, 23, 22, 17, tzinfo=timezone.utc)),
                         (False, "daily"))
        self.assertEqual(preflight_decision("schedule", slot, "", self.root,
                                            datetime(2026, 9, 23, 22, 37, tzinfo=timezone.utc)),
                         (True, "daily"))
        update_daily_markers("2026-09-23T22:37:00Z", {}, False, marker,
                             reserve_provider_attempt=True)
        self.assertEqual(preflight_decision("schedule", slot, "", self.root,
                                            datetime(2026, 9, 23, 23, 7, tzinfo=timezone.utc)),
                         (True, "live"))
        self.assertEqual(preflight_decision("schedule", "0 22 * * *", "", self.root,
                                            datetime(2026, 9, 23, 23, 7, tzinfo=timezone.utc)),
                         (False, "daily"))

    def test_hourly_schedule_does_not_backdate_late_daily_snapshot(self):
        slot = "7,17,27,37,47,57 * * * *"
        self.assertEqual(preflight_decision("schedule", slot, "", self.root,
                                            datetime(2026, 9, 23, 23, 37, tzinfo=timezone.utc)),
                         (True, "daily"))
        self.assertEqual(preflight_decision("schedule", slot, "", self.root,
                                            datetime(2026, 9, 23, 23, 45, tzinfo=timezone.utc)),
                         (True, "live"))

    def test_collector_marker_update_preserves_reserved_attempt(self):
        marker = self.root / "daily_collection_status.json"
        update_daily_markers("2026-09-23T22:12:00Z", {}, False, marker,
                             reserve_provider_attempt=True)
        update_daily_markers("2026-09-23T22:18:00Z", {}, False, marker)
        state = json.loads(marker.read_text())
        self.assertEqual(state["last_provider_attempt_at"], "2026-09-23T22:12:00Z")
        update_daily_markers("2026-09-23T22:21:00Z", {}, True, marker)
        self.assertEqual(json.loads(marker.read_text()), state)

    def test_future_or_invalid_provider_reservation_fails_closed(self):
        marker = self.root / "daily_collection_status.json"
        for value in ("2026-09-23T12:01:00Z", "nonsense", 17, "2026-09-23T11:59:00"):
            with self.subTest(value=value):
                marker.write_text(json.dumps({"last_provider_attempt_at": value}))
                self.assertFalse(self.decision())
        marker.write_text("invalid json")
        self.assertFalse(self.decision())

    def test_missing_malformed_and_future_timestamps_trigger_collection(self):
        self.assertTrue(self.decision())
        (self.root / "live_core_data.json").write_text('{"completed_at":"invalid"}')
        self.assertTrue(self.decision())
        self.write_time("live_core_data.json", "completed_at", -1)
        self.assertTrue(self.decision())

    def test_manual_and_primary_daily_run_bypass_provider_cooldown(self):
        self.write_time("live_core_data.json", "completed_at", 1)
        self.assertTrue(should_collect("workflow_dispatch", "", self.root, self.now,
                                       dispatch_mode="manual"))
        self.assertFalse(self.decision("push"))
        self.assertTrue(should_collect("schedule", "0 22 * * *", self.root,
            datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)))

    def test_push_obeys_cooldown_and_can_recover_daily_slot(self):
        now = datetime(2026, 9, 23, 23, 7, tzinfo=timezone.utc)
        marker = self.root / "daily_collection_status.json"
        update_daily_markers("2026-09-23T22:58:00Z", {}, True, marker,
                             reserve_provider_attempt=True)
        self.assertEqual(preflight_decision("push", "", "", self.root, now),
                         (False, "daily"))
        after_cooldown = datetime(2026, 9, 23, 23, 37, tzinfo=timezone.utc)
        self.assertEqual(preflight_decision("push", "", "", self.root, after_cooldown),
                         (True, "daily"))
        update_daily_markers("2026-09-23T23:37:00Z", {}, False, marker,
                             reserve_provider_attempt=True)
        self.assertEqual(preflight_decision("push", "", "", self.root,
                                            datetime(2026, 9, 23, 23, 44, tzinfo=timezone.utc)),
                         (False, "live"))

    def test_watchdog_hourly_slot_is_validated_and_respects_cooldown(self):
        slot = "2026-09-23T11:47:00.000Z"
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root, self.now, "watchdog"),
                         (True, "live"))
        self.write_time("data_collection_status.json", "last_run_timestamp", 8)
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root, self.now, "watchdog"),
                         (False, "live"))
        for bad in ("2026-09-23T11:50:00.000Z", "2026-09-23T12:17:00.000Z",
                    "2026-09-23T10:17:00.000Z", "2026-09-23T11:47:00Z", "invalid"):
            with self.subTest(slot=bad):
                self.assertEqual(preflight_decision("workflow_dispatch", "", bad, self.root, self.now, "watchdog"),
                                 (False, "live"))
        self.assertEqual(preflight_decision("workflow_dispatch", "", "", self.root, self.now,
                                            "watchdog"), (False, "live"))
        self.assertEqual(preflight_decision("workflow_dispatch", "", "", self.root, self.now),
                         (False, "live"))
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root, self.now,
                                            "manual"), (False, "live"))

    def test_daily_watchdog_waits_for_live_cooldown_without_losing_daily_slot(self):
        now = datetime(2026, 9, 23, 22, 15, tzinfo=timezone.utc)
        slot = "2026-09-23T22:00:00.000Z"
        (self.root / "data_collection_status.json").write_text(json.dumps({
            "last_run_timestamp": "2026-09-23T22:07:00Z", "mode": "live"}))
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root, now, "watchdog"),
                         (False, "daily"))
        after_cooldown = datetime(2026, 9, 23, 22, 37, tzinfo=timezone.utc)
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root,
                                            after_cooldown, "watchdog"), (True, "daily"))
        update_daily_markers("2026-09-23T22:13:00Z", {
            "live_core": {"status": "PARTIAL"},
            "snapshots": {"status": "SUCCESS"}, "outcomes": {"status": "SUCCESS"}},
            False, self.root / "daily_collection_status.json", "2026-09-23T22:14:00Z")
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root,
                                            after_cooldown, "watchdog"),
                         (False, "daily"))
        self.assertEqual(preflight_decision("schedule", "0 22 * * *", "", self.root, after_cooldown),
                         (False, "daily"))

    def test_daily_watchdog_uses_pre_provider_reservation_when_completion_is_missing(self):
        slot = "2026-09-23T22:00:00.000Z"
        marker = self.root / "daily_collection_status.json"
        # An hourly run may have started after the Worker checked GitHub but
        # before the watchdog dispatch reaches repository preflight.
        update_daily_markers("2026-09-23T22:11:00Z", {}, True, marker,
                             reserve_provider_attempt=True)
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root,
                                            datetime(2026, 9, 23, 22, 12, tzinfo=timezone.utc),
                                            "watchdog"), (False, "daily"))
        self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root,
                                            datetime(2026, 9, 23, 22, 41, tzinfo=timezone.utc),
                                            "watchdog"), (True, "daily"))

    def test_daily_watchdog_invalid_reservation_fails_closed_but_primary_schedule_is_unchanged(self):
        slot = "2026-09-23T22:00:00.000Z"
        now = datetime(2026, 9, 23, 22, 15, tzinfo=timezone.utc)
        marker = self.root / "daily_collection_status.json"
        for value in ("2026-09-23T22:16:00Z", "invalid"):
            with self.subTest(value=value):
                marker.write_text(json.dumps({"last_provider_attempt_at": value}))
                self.assertEqual(preflight_decision("workflow_dispatch", "", slot, self.root,
                                                    now, "watchdog"), (False, "daily"))
                self.assertEqual(preflight_decision("schedule", "0 22 * * *", "", self.root,
                                                    now), (True, "daily"))

    def test_failed_daily_attempt_is_not_success_but_prevents_duplicate(self):
        marker = self.root / "daily_collection_status.json"
        update_daily_markers("2026-09-23T22:12:00Z", {
            "live_core": {"status": "PARTIAL"},
            "snapshots": {"status": "FAILED"}, "outcomes": {"status": "SUCCESS"}}, False, marker)
        state = json.loads(marker.read_text())
        self.assertEqual(state["last_daily_attempt_slot_utc"], "2026-09-23T22:00:00.000Z")
        self.assertIsNone(state["last_daily_completed_at"])
        now = datetime(2026, 9, 23, 22, 15, tzinfo=timezone.utc)
        self.assertEqual(preflight_decision("workflow_dispatch", "",
            "2026-09-23T22:00:00.000Z", self.root, now, "watchdog"), (False, "daily"))
        update_daily_markers("2026-09-23T22:16:00Z", {}, True, marker)
        self.assertEqual(json.loads(marker.read_text()), state)
        update_daily_markers("2026-09-23T22:18:00Z", {
            "live_core": {"status": "FAILED"},
            "snapshots": {"status": "SUCCESS"}, "outcomes": {"status": "SUCCESS"}},
            False, marker)
        self.assertIsNone(json.loads(marker.read_text())["last_daily_completed_at"])

    def test_future_daily_attempt_does_not_suppress_recovery(self):
        marker = self.root / "daily_collection_status.json"
        update_daily_markers("2026-09-23T22:20:00Z", {}, False, marker)
        now = datetime(2026, 9, 23, 22, 15, tzinfo=timezone.utc)
        self.assertEqual(preflight_decision("workflow_dispatch", "",
            "2026-09-23T22:00:00.000Z", self.root, now, "watchdog"), (True, "daily"))

    def test_late_daily_run_cannot_label_next_calendar_day(self):
        next_day = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(preflight_decision("workflow_dispatch", "",
            "2026-09-23T22:00:00.000Z", self.root, next_day, "watchdog"),
                         (False, "live"))
        self.assertEqual(preflight_decision("schedule", "0 22 * * *", "", self.root, next_day),
                         (True, "live"))


if __name__ == "__main__":
    unittest.main()
