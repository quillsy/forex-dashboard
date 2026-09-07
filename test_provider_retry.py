import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from provider_transport import CollectorTransport


class ProviderRetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "status.json"
        self.now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
        self.client = Mock()
        self.env = patch.dict("os.environ", {"FX_COLLECTOR": "1"})
        self.env.start(); self.addCleanup(self.env.stop)

    def transport(self):
        return CollectorTransport(self.client, status_path=self.path, clock=lambda: self.now)

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
