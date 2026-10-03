"""Release-boundary checks for the original CAD monthly CPI index."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

import live_data
from official_macro import statcan_cpi_next_due, validate_statcan_cpi
import test_official_macro


class StatCanCpiCalendarTests(unittest.TestCase):
    def test_official_dates_resolve_eastern_time_in_both_seasons(self):
        self.assertEqual(statcan_cpi_next_due('2026-08'),
                         datetime(2026, 10, 19, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(statcan_cpi_next_due('2026-09'),
                         datetime(2026, 11, 16, 13, 30, tzinfo=timezone.utc))
        self.assertIsNone(statcan_cpi_next_due('2027-02'))

    def test_stale_month_blocks_exactly_at_the_scheduled_release(self):
        payloads = test_official_macro.StatCanCpiContractTests.fixture()
        original = copy.deepcopy(payloads)
        due = statcan_cpi_next_due('2026-08')
        points = validate_statcan_cpi(*payloads, now=due - timedelta(microseconds=1))
        self.assertEqual(points[-1]['next_due_at'], due.isoformat())
        self.assertTrue(points[-1]['needs_hourly_check'])
        self.assertEqual(points[-1]['next_due_precision'], 'timestamp')
        for checked in (due, due + timedelta(seconds=1)):
            with self.subTest(checked=checked), self.assertRaisesRegex(ValueError, 'RELEASE_OVERDUE'):
                validate_statcan_cpi(*payloads, now=checked)
        self.assertEqual(payloads, original)

    def test_new_api_timestamp_does_not_refresh_an_overdue_reference_month(self):
        payloads = test_official_macro.StatCanCpiContractTests.fixture()
        for point in payloads[1][0]['object']['vectorDataPoint']:
            point['releaseTime'] = '2026-10-19T08:30'
        payloads[2][0]['object']['releaseTime'] = '2026-10-19T08:30'
        with self.assertRaisesRegex(ValueError, 'RELEASE_OVERDUE'):
            validate_statcan_cpi(*payloads, now=statcan_cpi_next_due('2026-08'))

    def test_confirmed_new_month_receives_the_following_deadline(self):
        payloads = test_official_macro.StatCanCpiContractTests.fixture()
        points = payloads[1][0]['object']['vectorDataPoint']
        next_point = dict(points[-1], refPer='2026-09-01', refPerRaw='2026-09-01')
        points.append(next_point)
        del points[0]
        for point in points:
            point['releaseTime'] = '2026-10-19T08:30'
        payloads[2][0]['object'].update(cubeEndDate='2026-09-01', releaseTime='2026-10-19T08:30')
        checked = statcan_cpi_next_due('2026-08')
        result = validate_statcan_cpi(*payloads, now=checked)
        self.assertEqual(result[-1]['refPer'], '2026-09-01')
        self.assertEqual(result[-1]['next_due_at'], statcan_cpi_next_due('2026-09').isoformat())
        self.assertEqual(len(result), 13)

    def test_known_deadline_does_not_extend_the_hourly_check(self):
        checked = datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc)
        observation = dict(value=3.033980582524265, date='2026-08-01',
                           source='Statistics Canada', series_id='v41690973',
                           reference_period='2026-08', unit='annual percent change',
                           frequency='monthly', seasonal_adjustment='NSA',
                           next_due_at=statcan_cpi_next_due('2026-08').isoformat(),
                           next_due_precision='timestamp', needs_hourly_check=True)
        record = live_data.build_record('Inflation', 51.69902912621325, observation,
                                        'FRESH', checked.isoformat())
        self.assertTrue(live_data.eligible(record, checked + timedelta(minutes=59),
                                          factor='Inflation', currency='CAD')[0])
        self.assertFalse(live_data.eligible(record, checked + timedelta(hours=1),
                                           factor='Inflation', currency='CAD')[0])

    def test_period_outside_the_calendar_remains_hourly(self):
        payloads = test_official_macro.StatCanCpiContractTests.fixture()
        points = payloads[1][0]['object']['vectorDataPoint']
        end = 2027 * 12 + 1
        for point, serial in zip(points, range(end - 12, end + 1)):
            year, month = divmod(serial, 12)
            point.update(refPer=f'{year}-{month + 1:02d}-01',
                         refPerRaw=f'{year}-{month + 1:02d}-01',
                         releaseTime='2027-03-15T08:30')
        payloads[2][0]['object'].update(cubeEndDate='2027-02-01', releaseTime='2027-03-15T08:30')
        result = validate_statcan_cpi(*payloads, now=datetime(2027, 3, 15, 12, 30, tzinfo=timezone.utc))
        self.assertIsNone(result[-1]['next_due_at'])
        self.assertIsNone(result[-1]['next_due_precision'])
        self.assertTrue(result[-1]['needs_hourly_check'])


if __name__ == '__main__':
    unittest.main()
