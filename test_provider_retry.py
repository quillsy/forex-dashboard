import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from provider_transport import CollectorTransport


class ProviderRetryTests(unittest.TestCase):
    def test_statcan_html_outage_is_labelled_without_persisting_body(self):
        from official_macro import fetch_statcan_labour
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'text/html'
        response._content = b"<title>Statistics Canada - We're sorry! The website is currently unavailable</title>private-detail"
        self.client.post.return_value = response
        transport = self.transport()
        with self.assertRaisesRegex(requests.RequestException, 'STATCAN_OFFICIAL_OUTAGE'):
            fetch_statcan_labour(session=transport)
        usage = transport.usage['www150.statcan.gc.ca']
        self.assertEqual(usage['requests_this_run'], 1)
        self.assertEqual(usage['outcomes_this_run'], {'SUCCESS': 1})  # HTTP transport did succeed.
        self.assertEqual(usage['status'], 'SOURCE_UNAVAILABLE')
        self.assertEqual(usage['data_status'], 'OFFICIAL_OUTAGE_PAGE')
        self.assertEqual(usage['last_failure_at'], self.now.isoformat())
        self.assertNotIn('private-detail', json.dumps(usage))

    def test_ons_fallback_through_actual_collector_transport(self):
        from official_ons import fetch_ons_gdp, PN2_FALLBACK_URL
        from test_official_ons import fixture, NOW
        for failure in (requests.exceptions.Timeout('private-key'), requests.exceptions.SSLError('private-key')):
            with self.subTest(failure=type(failure)):
                responses = []
                for family in ('PN2', 'QNA'):
                    response = requests.Response()
                    response.status_code = 200
                    response._content = json.dumps(fixture(family)).encode()
                    responses.append(response)
                self.client.get.reset_mock()
                self.client.get.side_effect = [failure, *responses]
                transport = self.transport()
                if isinstance(failure, requests.exceptions.SSLError):
                    with self.assertRaises(requests.RequestException):
                        fetch_ons_gdp(now=NOW, session=transport)
                    self.assertEqual(self.client.get.call_count, 1)
                else:
                    result = fetch_ons_gdp(now=NOW, session=transport)
                    self.assertEqual(result['source_url'], PN2_FALLBACK_URL)
                    self.assertEqual(self.client.get.call_count, 3)
                    self.assertEqual(result['value'], 1.2)

    def test_transient_categories_sanitized_but_tls_not_fallback_eligible(self):
        for source, expected in [(requests.exceptions.Timeout, requests.exceptions.Timeout),
                                 (requests.exceptions.ConnectionError, requests.exceptions.ConnectionError),
                                 (requests.exceptions.SSLError, requests.RequestException)]:
            with self.subTest(source=source):
                self.client.get.side_effect = source('private-url-key')
                transport = self.transport()
                with self.assertRaises(expected) as caught:
                    transport.get('https://official.example/data')
                self.assertIs(type(caught.exception), expected)
                self.assertEqual(str(caught.exception), 'PROVIDER_REQUEST_FAILED')
                self.assertIsNone(caught.exception.request)
                self.assertIsNone(caught.exception.response)
                self.assertNotIn('private', json.dumps(transport.usage))

    def test_success_does_not_erase_prior_failure_and_dedup_does_not_count(self):
        ok = requests.Response(); ok.status_code = 200; ok._content = b'{}'
        self.client.get.side_effect = [requests.exceptions.Timeout('private-key'), ok]
        transport = self.transport()
        with self.assertRaises(requests.exceptions.Timeout):
            transport.get('https://official.example/cpi?key=private-key')
        failed_at = self.now.isoformat()
        self.now += timedelta(seconds=5)
        transport.get('https://official.example/labour')
        transport.get('https://official.example/labour')
        usage = transport.usage['official.example']
        self.assertEqual(usage['status'], 'SUCCESS')
        self.assertEqual(usage['outcomes_this_run'], {'TIMEOUT': 1, 'SUCCESS': 1})
        self.assertEqual(usage['requests_this_run'], 2)
        self.assertEqual(usage['last_failure_at'], failed_at)
        self.assertEqual(usage['last_attempt_at'], self.now.isoformat())
        self.assertNotIn('private', json.dumps(usage))

    def test_outcome_counts_reset_on_restart_and_cooldown_does_not_count(self):
        self.response(429, '7200'); first = self.transport()
        first.get('https://official.example/data')
        self.assertEqual(first.usage['official.example']['outcomes_this_run'], {'HTTP_429': 1})
        self.path.write_text(json.dumps({'providers': first.usage}))
        second = self.transport()
        with self.assertRaises(requests.RequestException):
            second.get('https://official.example/data')
        self.assertEqual(second.usage['official.example']['outcomes_this_run'], {})
        self.assertNotIn('last_attempt_at', second.usage['official.example'])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "status.json"
        self.now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
        self.client = Mock()
        self.env = patch.dict("os.environ", {"FX_COLLECTOR": "1"})
        self.env.start(); self.addCleanup(self.env.stop)

    def transport(self, durable=False):
        return CollectorTransport(self.client, status_path=self.path, clock=lambda: self.now,
                                  durable=durable)

    def persisted(self):
        return json.loads(self.path.read_text())["providers"]["official.example"]

    def test_default_transport_does_not_write_even_in_collector_environment(self):
        self.response(429, "7200")
        self.transport().get("https://official.example/data")
        self.assertFalse(self.path.exists())

    def test_reservation_precedes_io_without_fabricating_outcome_or_consumption(self):
        self.path.write_text(json.dumps({"last_run_status": "SUCCESS", "components": {"prior": "kept"}}))
        def interrupt(url, **kwargs):
            saved = self.persisted()
            self.assertEqual(saved["requests_reserved_utc_day"], 1)
            self.assertEqual(saved["requests_uncertain_utc_day"], 1)
            self.assertEqual(saved["requests_observed_utc_day"], 0)
            self.assertEqual(saved["outcomes_this_run"], {})
            self.assertEqual(saved["status"], "REQUEST_RESERVED")
            self.assertFalse(saved["usage_complete"])
            self.assertNotIn("last_checked_at", saved)
            raise KeyboardInterrupt()
        self.client.get.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.transport(durable=True).get("https://official.example/data?key=private-key")
        status = json.loads(self.path.read_text())
        self.assertEqual(status["last_run_status"], "SUCCESS")
        self.assertEqual(status["components"], {"prior": "kept"})
        self.assertNotIn("private", self.path.read_text())
        # A later returned request cannot silently turn the open request into
        # a confirmed response or known consumed provider quota.
        self.client.get.side_effect = None
        self.response(200, "")
        self.now += timedelta(minutes=31)
        self.transport(durable=True).get("https://official.example/other")
        saved = self.persisted()
        self.assertEqual(saved["requests_observed_utc_day"], 1)
        self.assertEqual(saved["requests_reserved_utc_day"], 2)
        self.assertEqual(saved["requests_uncertain_utc_day"], 1)
        self.assertFalse(saved["usage_complete"])
        self.assertIsNone(saved["remaining"])

    def test_durable_retry_after_survives_without_final_collector_save(self):
        for status, header in ((429, "7200"), (503, "Mon, 07 Sep 2026 14:00:00 GMT")):
            with self.subTest(status=status):
                self.path.unlink(missing_ok=True)
                self.now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
                self.response(status, header)
                first = self.transport(durable=True)
                first.get("https://official.example/data?key=private-key")
                saved = self.persisted()
                self.assertEqual(saved["requests_observed_utc_day"], 1)
                self.assertEqual(saved["requests_reserved_utc_day"], 1)
                self.assertEqual(saved["requests_uncertain_utc_day"], 0)
                self.assertTrue(saved["usage_complete"])
                self.assertEqual(saved["retry_after_at"], "2026-09-07T14:00:00+00:00")
                self.client.get.reset_mock()
                self.now += timedelta(minutes=31)
                second = self.transport(durable=True)
                with self.assertRaisesRegex(requests.RequestException, "PROVIDER_COOLDOWN"):
                    second.get("https://official.example/other")
                self.client.get.assert_not_called()
                self.assertEqual(self.persisted(), saved)

    def test_durable_dedup_and_guard_do_not_reserve_or_call_callbacks(self):
        self.response(200, "")
        before, after = Mock(), Mock()
        transport = self.transport(durable=True)
        for _ in range(2):
            transport.get("https://official.example/data", before_request=before, after_request=after)
        self.client.get.assert_called_once_with("https://official.example/data")
        before.assert_called_once_with(self.now)
        after.assert_called_once_with(self.now)
        self.assertEqual(self.persisted()["requests_observed_utc_day"], 1)
        self.assertEqual(self.persisted()["requests_reserved_utc_day"], 1)
        previous = self.path.read_text()
        with patch.dict("os.environ", {"FX_RBNZ_AUTOMATION_APPROVED": "0"}):
            with self.assertRaisesRegex(requests.RequestException, "PROVIDER_AUTOMATION_PERMISSION_REQUIRED"):
                transport.get("https://rbnz.govt.nz/data", before_request=before, after_request=after)
        self.assertEqual(self.path.read_text(), previous)
        self.assertEqual(before.call_count, 1)
        self.assertEqual(after.call_count, 1)

    def test_unwritable_or_corrupt_durable_status_prevents_io(self):
        for content in ("invalid json", '{"providers":null}'):
            with self.subTest(content=content):
                self.path.write_text(content)
                with self.assertRaisesRegex(requests.RequestException, "PROVIDER_ATTEMPT_PERSISTENCE_FAILED"):
                    self.transport(durable=True).get("https://official.example/data")
                self.client.get.assert_not_called()
        self.path.unlink()
        with patch("provider_transport.os.replace", side_effect=OSError("private-path")):
            with self.assertRaisesRegex(requests.RequestException, "PROVIDER_ATTEMPT_PERSISTENCE_FAILED"):
                self.transport(durable=True).get("https://official.example/data")
        self.client.get.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_durable_counters_roll_over_on_request_utc_day_without_double_counting(self):
        self.now = self.now.replace(hour=23, minute=59)
        self.response(200, "")
        transport = self.transport(durable=True)
        transport.get("https://official.example/first")
        transport.get("https://official.example/first")
        self.assertEqual(self.persisted()["requests_observed_utc_day"], 1)
        self.now += timedelta(minutes=2)
        transport.get("https://official.example/second")
        saved = self.persisted()
        self.assertEqual(saved["counted_day_utc"], "2026-09-08")
        self.assertEqual(saved["requests_observed_utc_day"], 1)
        self.assertEqual(saved["requests_reserved_utc_day"], 1)
        self.assertEqual(saved["requests_uncertain_utc_day"], 0)

    def test_activation_reloads_confirmed_cooldown_and_never_shortens_known_deadline(self):
        transport = self.transport()
        deadline = self.now + timedelta(hours=2)
        self.path.write_text(json.dumps({"providers": {"official.example": {
            "retry_after_at": deadline.isoformat()}}}))
        transport.enable_durable_usage()
        self.path.write_text(json.dumps({"providers": {"official.example": {
            "retry_after_at": (self.now + timedelta(hours=1)).isoformat()}}}))
        with self.assertRaisesRegex(requests.RequestException, "PROVIDER_COOLDOWN"):
            transport.get("https://official.example/data")
        self.client.get.assert_not_called()
        self.assertEqual(transport.retry_after["official.example"], deadline)

    def test_unknown_old_day_usage_is_not_labeled_complete_after_midnight(self):
        self.client.get.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.transport(durable=True).get("https://official.example/first")
        self.now += timedelta(days=1)
        self.client.get.side_effect = None; self.response(200, "")
        self.transport(durable=True).get("https://official.example/second")
        saved = self.persisted()
        self.assertEqual(saved["counted_day_utc"], self.now.date().isoformat())
        self.assertEqual(saved["requests_observed_utc_day"], 1)
        self.assertEqual(saved["requests_reserved_utc_day"], 1)
        self.assertEqual(saved["requests_uncertain_utc_day"], 0)
        self.assertTrue(saved["prior_usage_uncertain"])
        self.assertFalse(saved["usage_complete"])
        self.assertIsNone(saved["remaining"])

    def test_corrupt_day_count_remains_unknown_instead_of_becoming_known_zero(self):
        self.path.write_text(json.dumps({"providers": {"official.example": {
            "counted_day_utc": self.now.date().isoformat(), "requests_observed_utc_day": "corrupt"}}}))
        self.response(200, "")
        self.transport(durable=True).get("https://official.example/data")
        saved = self.persisted()
        self.assertIsNone(saved["requests_observed_utc_day"])
        self.assertIsNone(saved["requests_reserved_utc_day"])
        self.assertFalse(saved["usage_complete"])

    def response(self, status, header):
        result = requests.Response(); result.status_code = status
        result.headers["Retry-After"] = header
        result._content = b"private-response-secret"
        self.client.get.return_value = result

    def test_seconds_survive_restart_without_network_or_counter(self):
        self.response(429, "7200"); first = self.transport()
        clean = first.get("https://official.example/data?key=private-key")
        self.assertEqual(clean.content, b"")
        self.path.write_text(json.dumps({"providers": first.usage}))
        self.client.get.reset_mock(); self.now += timedelta(minutes=30)
        second = self.transport()
        with self.assertRaises(requests.RequestException): second.get("https://official.example/other")
        self.client.get.assert_not_called()
        self.assertEqual(second.usage["official.example"]["requests_this_run"], 0)
        self.assertEqual(second.usage["official.example"]["retry_after_at"], "2026-09-07T14:00:00+00:00")
        self.assertNotIn("private", json.dumps(second.usage))
        self.assertIsNone(second.usage["official.example"]["remaining"])

    def test_after_deadline_restart_resumes(self):
        self.path.write_text(json.dumps({"providers": {"official.example": {"retry_after_at": "2026-09-07T13:00:00Z"}}}))
        self.now += timedelta(hours=2); self.response(200, "")
        transport = self.transport(); transport.get("https://official.example/data")
        self.client.get.assert_called_once()
        self.assertNotIn("retry_after_at", transport.usage["official.example"])

    def test_http_date_503_and_expiry_same_process(self):
        self.response(503, "Mon, 07 Sep 2026 13:00:00 GMT")
        transport = self.transport(); transport.get("https://official.example/data")
        self.assertEqual(transport.usage["official.example"]["retry_after_at"], "2026-09-07T13:00:00+00:00")
        self.now += timedelta(hours=2); self.response(200, "")
        transport.get("https://official.example/other")
        self.assertNotIn("retry_after_at", transport.usage["official.example"])

    def test_invalid_headers_are_not_persisted_or_exposed(self):
        for header in ["private-key=secret", "-1", "999999999999999999999999", "Wed, 01 Jan 2020 12:00:00 GMT"]:
            with self.subTest(header=header):
                self.response(429, header); transport = self.transport()
                transport.get("https://official.example/data")
                self.assertNotIn("retry_after_at", transport.usage["official.example"])
                self.assertNotIn("secret", json.dumps(transport.usage))
                with self.assertRaises(requests.RequestException): transport.get("https://official.example/other")

    def test_corrupt_persisted_state_does_not_break_collection(self):
        for text in ["not json", '{"providers": null}', '{"providers":{"official.example":{"retry_after_at":"secret"}}}']:
            self.path.write_text(text); self.response(200, "")
            self.assertEqual(self.transport().get("https://official.example/data").status_code, 200)


if __name__ == "__main__": unittest.main()
