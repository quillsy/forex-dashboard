"""Offline tests of USD BLS publication proof and read-time CORE gating."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import live_data
from official_bls import BlsInvalid, fetch_release_state, parse_api_latest, parse_pdf_release, parse_schedule
from provider_transport import CollectorTransport

NOW = datetime(2026, 9, 27, 19, tzinfo=timezone.utc)


def states():
    return {
        "Arbeitsmarkt": {"period": "2026-08", "published_at": "2026-09-04T12:30:00+00:00",
                         "next_due_at": "2026-10-02T12:30:00+00:00",
                         "release_url": "https://www.dol.gov/newsroom/economicdata/empsit_09042026.pdf"},
        "Inflation": {"period": "2026-08", "published_at": "2026-09-11T12:30:00+00:00",
                      "next_due_at": "2026-10-14T12:30:00+00:00",
                      "release_url": "https://www.dol.gov/newsroom/economicdata/cpi_09112026.pdf"},
    }


def bulletin(factor, *, period="AUGUST 2026", embargo="September 4, 2026",
             successor="September 2026", due="October 2, 2026"):
    if factor == "Inflation":
        title, name = "CONSUMER PRICE INDEX", "The Consumer Price Index news release"
    else:
        title, name = "THE EMPLOYMENT SITUATION", "The Employment Situation"
    return (f"Transmission of material in this news release is embargoed until USDL-26-1435 "
            f"8:30 a.m. (ET) Friday, {embargo} NEWS RELEASE {title} — {period} "
            f"{name} for {successor} is scheduled to be published on Friday, "
            f"{due}, at 8:30 a.m. (ET).")


def api(factor, period="M08", year="2026"):
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [{
        "seriesID": "LNS14000000" if factor == "Arbeitsmarkt" else "CUUR0000SA0",
        "data": [{"year": year, "period": period, "periodName": "August", "latest": "true",
                  "value": "4.1" if factor == "Arbeitsmarkt" else "334.980"}]}]}}


class BlsSourceTests(unittest.TestCase):
    def test_pdf_heading_embargo_and_successor_are_distinct(self):
        state = parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt", NOW)
        self.assertEqual(state["period"], "2026-08")
        self.assertEqual(state["next_due_at"], "2026-10-02T12:30:00+00:00")
        with self.assertRaises(BlsInvalid):
            parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt",
                              datetime(2026, 9, 4, 12, 29, tzinfo=timezone.utc))
        with self.assertRaises(BlsInvalid):
            parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt",
                              datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc))

    def test_cpi_heading_and_successor_date(self):
        text = bulletin("Inflation", embargo="September 11, 2026", due="October 14, 2026")
        state = parse_pdf_release(text, "Inflation", NOW)
        self.assertEqual(state["period"], "2026-08")
        self.assertEqual(state["next_due_at"], "2026-10-14T12:30:00+00:00")

    def test_missing_duplicate_or_wrong_successor_fails_closed(self):
        text = bulletin("Arbeitsmarkt")
        for broken in (text + text, text.replace("AUGUST 2026", "JULY 2026"),
                       text.replace("September 2026 is scheduled", "October 2026 is scheduled"),
                       text.replace("THE EMPLOYMENT SITUATION", "ECONOMIC OUTLOOK")):
            with self.subTest(broken=broken[:50]), self.assertRaises(BlsInvalid):
                parse_pdf_release(broken, "Arbeitsmarkt", NOW)

    def test_api_requires_exact_latest_series_and_month(self):
        self.assertEqual(parse_api_latest(api("Arbeitsmarkt"), "Arbeitsmarkt"), "2026-08")
        for mutate in (lambda p: p["Results"]["series"][0].update(seriesID="LNS14000001"),
                       lambda p: p["Results"]["series"][0]["data"][0].update(period="M13"),
                       lambda p: p["Results"]["series"][0]["data"][0].update(latest="false")):
            payload = api("Arbeitsmarkt")
            mutate(payload)
            with self.assertRaises(BlsInvalid):
                parse_api_latest(payload, "Arbeitsmarkt")

    def test_official_provider_quota_has_fixed_error_without_raw_message(self):
        payload = {"status": "REQUEST_NOT_PROCESSED", "message": [
            "Request could not be serviced, as the daily threshold for total number of "
            "requests allocated to the user with registration key  has been reached."]}
        with self.assertRaisesRegex(BlsInvalid, "^BLS_PROVIDER_LIMIT$"):
            parse_api_latest(payload, "Arbeitsmarkt")
        payload["message"] = ["unrecognized provider response with private content"]
        with self.assertRaisesRegex(BlsInvalid, "^BLS_API_STATUS_INVALID$"):
            parse_api_latest(payload, "Arbeitsmarkt")

    def test_cache_reused_before_due_without_api_and_cooldown_after_due(self):
        prior = states()["Arbeitsmarkt"]
        client = Mock()
        self.assertEqual(fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                         confirmed_period="2026-08", previous_state=prior,
                         last_api_attempt=NOW - timedelta(minutes=30)), prior)
        client.get.assert_not_called()
        client.get.return_value = Mock(json=Mock(return_value=api("Arbeitsmarkt")), raise_for_status=Mock())
        diagnostics = {}
        self.assertEqual(fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                         confirmed_period="2026-08", previous_state=prior,
                         last_api_attempt=NOW - timedelta(hours=25), diagnostics=diagnostics), prior)
        self.assertTrue(diagnostics["api_attempted"])
        self.assertEqual(client.get.call_count, 1)
        client.reset_mock()
        with self.assertRaises(BlsInvalid):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=datetime(2026, 10, 2, 13, tzinfo=timezone.utc),
                                last_api_attempt=datetime(2026, 10, 2, 12, 35, tzinfo=timezone.utc),
                                confirmed_period="2026-08", previous_state=prior)
        client.get.assert_not_called()

    def test_release_window_budget_and_half_hour_retry(self):
        prior = states()["Arbeitsmarkt"]
        due = datetime.fromisoformat(prior["next_due_at"])
        client = Mock()
        with self.assertRaises(BlsInvalid):
            fetch_release_state("Arbeitsmarkt", session=client, now=due,
                                confirmed_period="2026-08", previous_state=prior,
                                rolling_attempts=20)
        with self.assertRaisesRegex(BlsInvalid, "BLS_RELEASE_WINDOW_INVALID"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due,
                                confirmed_period="2026-08", previous_state=prior,
                                release_attempts=-1)
        client.get.assert_not_called()
        client.get.return_value = Mock(json=Mock(return_value=api("Arbeitsmarkt")), raise_for_status=Mock())
        diagnostic = {}
        with self.assertRaisesRegex(BlsInvalid, "BLS_NEW_REFERENCE_MONTH_NOT_CONFIRMED"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due,
                                confirmed_period="2026-08", previous_state=prior,
                                diagnostics=diagnostic)
        self.assertEqual(diagnostic["release_window_due"], prior["next_due_at"])
        self.assertEqual(client.get.call_count, 1)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_RECHECK_COOLDOWN"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=due + timedelta(minutes=29),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=due)
        self.assertEqual(client.get.call_count, 1)

    def test_twelve_fast_attempts_continue_hourly_while_rolling_cap_allows(self):
        prior = states()["Arbeitsmarkt"]
        last = datetime(2026, 10, 2, 18, 30, tzinfo=timezone.utc)
        client = Mock()
        client.get.return_value = Mock(json=Mock(return_value=api("Arbeitsmarkt")),
                                       raise_for_status=Mock())
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_RECHECK_COOLDOWN"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=last + timedelta(minutes=59),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=last, release_attempts=12,
                                rolling_attempts=12)
        client.get.assert_not_called()
        with self.assertRaisesRegex(BlsInvalid, "BLS_NEW_REFERENCE_MONTH_NOT_CONFIRMED"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=last + timedelta(hours=1),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=last, release_attempts=12,
                                rolling_attempts=12)
        self.assertEqual(client.get.call_count, 1)
        with self.assertRaisesRegex(BlsInvalid, "BLS_LOCAL_API_BUDGET_EXHAUSTED"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=last + timedelta(hours=2),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=last, release_attempts=13,
                                rolling_attempts=20)
        self.assertEqual(client.get.call_count, 1)

    def test_late_release_rechecks_hourly_on_following_day(self):
        prior = states()["Arbeitsmarkt"]
        client = Mock()
        client.get.return_value = Mock(json=Mock(return_value=api("Arbeitsmarkt")),
                                       raise_for_status=Mock())
        last_attempt = datetime(2026, 10, 3, 12, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_RECHECK_COOLDOWN"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=last_attempt + timedelta(minutes=59),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=last_attempt)
        client.get.assert_not_called()
        with self.assertRaisesRegex(BlsInvalid, "BLS_NEW_REFERENCE_MONTH_NOT_CONFIRMED"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=last_attempt + timedelta(hours=1),
                                confirmed_period="2026-08", previous_state=prior,
                                last_api_attempt=last_attempt)
        self.assertEqual(client.get.call_count, 1)

    def test_api_pdf_conflict_and_transport_failure_block(self):
        client = Mock()
        client.get.side_effect = [Mock(json=Mock(return_value=api("Arbeitsmarkt")), raise_for_status=Mock()),
                                  requests.exceptions.Timeout()]
        with self.assertRaises(requests.exceptions.Timeout):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                                previous_state={"period": None, "published_at": None,
                                                "next_due_at": None, "release_url": None})
        self.assertEqual(client.get.call_count, 2)

    def test_official_schedule_dst_and_duplicate_detection(self):
        head = ("<h1>Schedule of Releases for the Employment Situation</h1>"
                "<table><tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>")
        row = "<tr><td>October 2026</td><td>Nov. 06, 2026</td><td>08:30 AM</td></tr>"
        tail = "</table><p>Subscribe to the BLS Online Calendar</p>"
        self.assertEqual(parse_schedule(head + row + tail, "Arbeitsmarkt")["2026-10"].isoformat(),
                         "2026-11-06T13:30:00+00:00")
        with self.assertRaises(BlsInvalid):
            parse_schedule(head + row + row + tail, "Arbeitsmarkt")


class BlsCollectorTests(unittest.TestCase):
    def record(self, factor, checked, due=None):
        state = states()[factor]
        return live_data.build_record(factor, 70 if factor == "Inflation" else 30, {
            "value": 3.4 if factor == "Inflation" else 4.1,
            "date": "2026-08-01", "reference_period": "2026-08",
            "series_id": "CPIAUCNS" if factor == "Inflation" else "UNRATE",
            "source": "FRED / BLS" if factor == "Inflation" else "FRED",
            "bls_release_period": "2026-08" if due else None,
            "bls_release_url": state["release_url"] if due else None,
            "published_at": state["published_at"] if due else None,
            "next_due_at": due,
        }, "FRESH", checked.isoformat())

    def run_collector(self, reports, observations, previous=None, at=NOW,
                      previous_data=None, attempted=()):
        app = Mock(FRED_KEY="test")
        self.source_calls = {}
        app.compute_currency_details.return_value = {
            **{factor: 70 if factor == "Inflation" else 30 for factor in reports},
            "_freshness": {factor: "FRESH" for factor in reports},
            "_observations": observations,
        }
        def source(factor, **kwargs):
            self.source_calls[factor] = kwargs
            if factor in attempted:
                kwargs["diagnostics"]["api_attempted"] = True
            value = reports[factor]
            if isinstance(value, Exception):
                raise value
            return value
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            if previous_data:
                live_data.save(previous_data, path)
            elif previous:
                live_data.save({"model_version": live_data.MODEL,
                                "currencies": {"USD": previous}}, path)
            with patch.object(live_data, "CURRENCIES", ("USD",)), \
                 patch.object(live_data, "FACTORS", {"Inflation": 20, "Arbeitsmarkt": 20}), \
                 patch.object(live_data, "now_utc", return_value=at), \
                 patch("official_bls.fetch_release_state", side_effect=source), \
                 patch("source_contracts.validate_fred_metadata", return_value=True):
                live_data.collect(app, path)
            self.last_dataset = live_data.load(path)
            return self.last_dataset["currencies"]["USD"], app

    def test_current_bls_period_qualifies_and_due_blocks_after_restart(self):
        observations = {
            "Inflation": {"value": 3.4, "date": "2026-08-01", "series_id": "CPIAUCNS", "source": "FRED / BLS"},
            "Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01", "series_id": "UNRATE", "source": "FRED"},
        }
        rows, _ = self.run_collector(states(), observations)
        for factor, row in rows.items():
            self.assertEqual(row["validation"], "VALID")
            self.assertTrue(live_data.eligible(row, NOW, factor=factor, currency="USD")[0])
            due = datetime.fromisoformat(row["next_due_at"])
            self.assertTrue(live_data.eligible(row, due - timedelta(seconds=1), factor=factor, currency="USD")[0])
            self.assertFalse(live_data.eligible(row, due, factor=factor, currency="USD")[0])

    def test_old_null_due_and_fred_lag_cannot_requalify(self):
        old = self.record("Arbeitsmarkt", NOW, due=None)
        self.assertFalse(live_data.eligible(old, NOW, factor="Arbeitsmarkt", currency="USD")[0])
        reports = states()
        reports["Arbeitsmarkt"] = {**reports["Arbeitsmarkt"], "period": "2026-09",
                                   "published_at": "2026-10-02T12:30:00+00:00",
                                   "next_due_at": "2026-11-06T13:30:00+00:00"}
        observations = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                          "series_id": "UNRATE", "source": "FRED"}}
        rows, _ = self.run_collector(reports, observations, previous={"Arbeitsmarkt": old},
                                     at=datetime(2026, 10, 2, 13, tzinfo=timezone.utc))
        self.assertEqual(rows["Arbeitsmarkt"]["validation"], "UNVERIFIED")
        self.assertIsNone(rows["Arbeitsmarkt"]["score"])
        self.assertEqual(self.last_dataset["bls_release_states"]["Arbeitsmarkt"]["period"], "2026-09")

    def test_outage_preserves_verified_cache_only_until_due(self):
        old = self.record("Arbeitsmarkt", NOW, states()["Arbeitsmarkt"]["next_due_at"])
        observations = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                          "series_id": "UNRATE", "source": "FRED"}}
        reports = {**states(), "Arbeitsmarkt": requests.exceptions.Timeout()}
        rows, _ = self.run_collector(reports, observations, previous={"Arbeitsmarkt": old})
        self.assertEqual(rows["Arbeitsmarkt"]["validation"], "VALID")
        self.assertTrue(live_data.eligible(rows["Arbeitsmarkt"], NOW,
                                           factor="Arbeitsmarkt", currency="USD")[0])
        due = datetime.fromisoformat(old["next_due_at"])
        self.assertFalse(live_data.eligible(rows["Arbeitsmarkt"], due,
                                            factor="Arbeitsmarkt", currency="USD")[0])

    def test_provider_daily_limit_preserves_only_pre_due_proof(self):
        old = self.record("Arbeitsmarkt", NOW, states()["Arbeitsmarkt"]["next_due_at"])
        observations = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                          "series_id": "UNRATE", "source": "FRED"}}
        reports = {**states(), "Arbeitsmarkt": BlsInvalid("BLS_PROVIDER_LIMIT")}
        before, _ = self.run_collector(reports, observations, previous={"Arbeitsmarkt": old})
        self.assertEqual(before["Arbeitsmarkt"]["validation"], "VALID")
        due = datetime.fromisoformat(old["next_due_at"])
        after, _ = self.run_collector(reports, observations,
                                      previous={"Arbeitsmarkt": old}, at=due)
        self.assertEqual(after["Arbeitsmarkt"]["validation"], "UNVERIFIED")
        self.assertIsNone(after["Arbeitsmarkt"]["score"])
        self.assertIn("BLS-Tageslimit", after["Arbeitsmarkt"]["reason"])

    def test_local_api_count_persists_across_collector_restart(self):
        observations = {"Inflation": {"value": 3.4, "date": "2026-08-01",
                                      "series_id": "CPIAUCNS", "source": "FRED / BLS"},
                        "Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                          "series_id": "UNRATE", "source": "FRED"}}
        self.run_collector(states(), observations, attempted=("Inflation",))
        self.assertEqual(self.last_dataset["bls_api_budget"]["local_attempts"], 1)
        first = self.last_dataset
        self.run_collector(states(), observations, previous_data=first,
                           at=NOW + timedelta(minutes=30), attempted=("Arbeitsmarkt",))
        self.assertEqual(self.last_dataset["bls_api_budget"]["local_attempts"], 2)
        self.assertEqual(self.last_dataset["bls_api_budget"]["basis"], "local_estimate")

    def test_exhausted_release_window_resets_next_utc_day(self):
        labor = states()["Arbeitsmarkt"]
        previous_data = {
            "model_version": live_data.MODEL,
            "currencies": {"USD": {"Arbeitsmarkt": self.record("Arbeitsmarkt", NOW,
                                                                  labor["next_due_at"]) }},
            "bls_release_states": {"Arbeitsmarkt": labor},
            "bls_api_attempts": {"Arbeitsmarkt": "2026-10-02T21:00:00+00:00"},
            "bls_api_budget": {"utc_day": "2026-10-02", "local_attempts": 20,
                               "attempted_at": ["2026-10-02T00:00:00+00:00"] * 20},
            "bls_release_windows": {"Arbeitsmarkt": {"utc_day": "2026-10-02",
                                                       "due_at": labor["next_due_at"], "attempts": 12}},
        }
        self.run_collector({"Arbeitsmarkt": BlsInvalid("BLS_NEW_REFERENCE_MONTH_NOT_CONFIRMED"),
                            "Inflation": states()["Inflation"]},
                           {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                              "series_id": "UNRATE", "source": "FRED"}},
                           previous_data=previous_data,
                           at=datetime(2026, 10, 3, 13, tzinfo=timezone.utc),
                           attempted=("Arbeitsmarkt",))
        self.assertEqual(self.source_calls["Arbeitsmarkt"]["rolling_attempts"], 0)
        self.assertEqual(self.source_calls["Arbeitsmarkt"]["release_attempts"], 0)
        self.assertEqual(self.last_dataset["bls_api_budget"]["local_attempts"], 1)

    def test_rolling_api_budget_does_not_reset_at_midnight(self):
        labor = states()["Arbeitsmarkt"]
        previous_data = {
            "model_version": live_data.MODEL,
            "currencies": {"USD": {"Arbeitsmarkt": self.record("Arbeitsmarkt", NOW,
                                                                  labor["next_due_at"])}},
            "bls_release_states": {"Arbeitsmarkt": labor},
            "bls_api_budget": {"utc_day": "2026-10-02", "local_attempts": 20,
                               "attempted_at": ["2026-10-02T22:00:00+00:00"] * 20},
            "bls_release_windows": {"Arbeitsmarkt": {"utc_day": "2026-10-02",
                                                       "due_at": labor["next_due_at"], "attempts": 12}},
        }
        self.run_collector({"Arbeitsmarkt": BlsInvalid("BLS_LOCAL_API_BUDGET_EXHAUSTED"),
                            "Inflation": states()["Inflation"]},
                           {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                              "series_id": "UNRATE", "source": "FRED"}},
                           previous_data=previous_data,
                           at=datetime(2026, 10, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(self.source_calls["Arbeitsmarkt"]["rolling_attempts"], 20)
        self.assertEqual(self.source_calls["Arbeitsmarkt"]["release_attempts"], 0)
        self.assertEqual(self.last_dataset["bls_api_budget"]["local_attempts"], 0)
        self.assertEqual(self.last_dataset["bls_api_budget"]["rolling_24h_attempts"], 20)

    def test_retry_after_blocks_without_count_then_recovers_on_real_request(self):
        def response(status, body=b"", headers=None):
            item = requests.Response()
            item.status_code = status
            item._content = body
            item.headers.update(headers or {})
            item.url = "https://api.bls.gov/publicAPI/v1/timeseries/data/LNS14000000"
            return item

        client = Mock()
        bls_responses = iter([
            response(429, headers={"Retry-After": "7200"}),
            response(200, json.dumps(api("Arbeitsmarkt")).encode(),
                     {"Content-Type": "application/json"}),
            response(200, b"%PDF-1.7 test", {"Content-Type": "application/pdf"}),
        ])
        client.get.side_effect = lambda url, **kwargs: (
            next(bls_responses) if url.startswith(("https://api.bls.gov/", "https://www.dol.gov/"))
            else response(200, b"{}", {"Content-Type": "application/json"}))
        current = [NOW]
        app = Mock(FRED_KEY="test")
        app.compute_currency_details.return_value = {
            "Arbeitsmarkt": 30, "_freshness": {"Arbeitsmarkt": "FRESH"},
            "_observations": {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                               "series_id": "UNRATE", "source": "FRED"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            live_path = Path(directory) / "live.json"
            status_path = Path(directory) / "status.json"
            with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), \
                 patch.object(live_data, "CURRENCIES", ("USD",)), \
                 patch.object(live_data, "FACTORS", {"Arbeitsmarkt": 20}), \
                 patch.object(live_data, "now_utc", side_effect=lambda: current[0]), \
                 patch("source_contracts.validate_fred_metadata", return_value=True), \
                 patch("official_bls._pdf_text", return_value=bulletin("Arbeitsmarkt")):
                for instant, expected_api_calls, expected_attempts in (
                    (NOW, 1, 1),
                    (NOW + timedelta(hours=1, minutes=1), 1, 1),
                    (NOW + timedelta(hours=1, minutes=31), 1, 1),
                    (NOW + timedelta(hours=2, minutes=1), 2, 2),
                ):
                    current[0] = instant
                    app.requests = CollectorTransport(client=client, status_path=status_path,
                                                      clock=lambda: current[0])
                    live_data.collect(app, live_path)
                    status_path.write_text(json.dumps({"providers": app.requests.usage}))
                    dataset = live_data.load(live_path)
                    api_calls = sum(call.args[0].startswith("https://api.bls.gov/")
                                    for call in client.get.call_args_list)
                    self.assertEqual(api_calls, expected_api_calls,
                                     (instant.isoformat(), app.requests.usage))
                    self.assertEqual(dataset["bls_api_budget"]["local_attempts"], expected_attempts)
                    self.assertEqual(dataset["bls_api_budget"]["rolling_24h_attempts"], expected_attempts)
                    if expected_api_calls == 1:
                        self.assertEqual(dataset["bls_api_attempts"]["Arbeitsmarkt"], NOW.isoformat())
                record = dataset["currencies"]["USD"]["Arbeitsmarkt"]
                self.assertEqual(record["validation"], "VALID")
                self.assertTrue(live_data.eligible(record, current[0],
                                                   factor="Arbeitsmarkt", currency="USD")[0])


if __name__ == "__main__":
    unittest.main()
