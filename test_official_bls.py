"""Offline checks for USD BLS bulletin proof and FRED period gating."""
import tempfile
import unittest
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests
import pandas as pd

import live_data
from provider_transport import CollectorTransport
from official_bls import (BlsInvalid, PINNED_DUES, _api_url, _pdf_url, fetch_release_state,
                          parse_api_latest, parse_pdf_release, parse_schedule)

NOW = datetime(2026, 9, 27, 19, tzinfo=timezone.utc)


def states():
    return {
        "Arbeitsmarkt": {"period": "2026-08", "embargo_ends_at": "2026-09-04T12:30:00+00:00",
                         "next_due_at": "2026-10-02T12:30:00+00:00",
                         "release_url": "https://www.dol.gov/newsroom/economicdata/empsit_09042026.pdf"},
        "Inflation": {"period": "2026-08", "embargo_ends_at": "2026-09-11T12:30:00+00:00",
                      "next_due_at": "2026-10-14T12:30:00+00:00",
                      "release_url": "https://www.dol.gov/newsroom/economicdata/cpi_09112026.pdf"},
    }


def bulletin(factor, *, period="August 2026", embargo="September 4, 2026",
             successor="September 2026", due="October 2, 2026"):
    title, name = (("THE EMPLOYMENT SITUATION", "The Employment Situation")
                   if factor == "Arbeitsmarkt" else
                   ("CONSUMER PRICE INDEX", "The Consumer Price Index news release"))
    embargo_day = datetime.strptime(embargo, "%B %d, %Y").strftime("%A")
    due_day = datetime.strptime(due, "%B %d, %Y").strftime("%A")
    return ("Transmission of material in this news release is embargoed until USDL-26-1435 "
            f"8:30 a.m. (ET) {embargo_day}, {embargo} NEWS RELEASE {title} — {period} "
            f"{name} for {successor} is scheduled to be published on {due_day}, "
            f"{due}, at 8:30 a.m. (ET).")


def pdf_response(status=200):
    response = Mock()
    response.status_code = status
    response.headers = {"Content-Type": "application/pdf"}
    response.content = b"%PDF-1.7 fixture"
    if status != 200:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    return response


def api_payload(factor, *, period="M08", value=None, year="2026"):
    # Synthetic values exercise equality checks, not a claim about the live index.
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [{
        "seriesID": "LNS14000000" if factor == "Arbeitsmarkt" else "CUUR0000SA0",
        "data": [{"year": year, "period": period, "periodName": "August",
                  "latest": "true", "footnotes": [{}],
                  "value": value or ("4.1" if factor == "Arbeitsmarkt" else "321.123")}] }]}}


def api_response(factor, **kwargs):
    response = Mock()
    response.status_code = 200
    response.json.return_value = api_payload(factor, **kwargs)
    return response


def api_state(factor):
    prior = states()[factor]
    return {**prior, "release_url": _api_url(factor), "proof_source": "BLS_API_V1",
            "first_observed_at": NOW.isoformat(),
            "raw_value": "4.1" if factor == "Arbeitsmarkt" else "321.123"}


class BlsBulletinTests(unittest.TestCase):
    def test_heading_embargo_and_successor_are_distinct(self):
        state = parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt", NOW)
        self.assertEqual(state["period"], "2026-08")
        self.assertEqual(state["embargo_ends_at"], "2026-09-04T12:30:00+00:00")
        self.assertEqual(state["next_due_at"], "2026-10-02T12:30:00+00:00")
        with self.assertRaises(BlsInvalid):
            parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt",
                              datetime(2026, 9, 4, 12, 29, tzinfo=timezone.utc))
        with self.assertRaises(BlsInvalid):
            parse_pdf_release(bulletin("Arbeitsmarkt"), "Arbeitsmarkt",
                              datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc))

    def test_cpi_bulletin_and_malformed_editions(self):
        text = bulletin("Inflation", embargo="September 11, 2026", due="October 14, 2026")
        state = parse_pdf_release(text, "Inflation", NOW)
        self.assertEqual(state["period"], "2026-08")
        self.assertEqual(state["next_due_at"], "2026-10-14T12:30:00+00:00")
        labor = bulletin("Arbeitsmarkt")
        for broken in (labor + labor, labor.replace("August 2026", "July 2026"),
                       labor.replace("September 2026 is scheduled", "October 2026 is scheduled"),
                       labor.replace("THE EMPLOYMENT SITUATION", "ECONOMIC OUTLOOK")):
            with self.subTest(broken=broken[:45]), self.assertRaises(BlsInvalid):
                parse_pdf_release(broken, "Arbeitsmarkt", NOW)

    def test_schedule_dst_and_duplicate_detection(self):
        head = ("<h1>Schedule of Releases for the Employment Situation</h1>"
                "<table><tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr>")
        row = "<tr><td>October 2026</td><td>Nov. 06, 2026</td><td>08:30 AM</td></tr>"
        tail = "</table><p>Subscribe to the BLS Online Calendar</p>"
        self.assertEqual(parse_schedule(head + row + tail, "Arbeitsmarkt")["2026-10"].isoformat(),
                         "2026-11-06T13:30:00+00:00")
        with self.assertRaises(BlsInvalid):
            parse_schedule(head + row + row + tail, "Arbeitsmarkt")

    def test_august_cold_boot_reads_only_official_pdf_not_bls_api(self):
        for factor, text in (("Arbeitsmarkt", bulletin("Arbeitsmarkt")),
                             ("Inflation", bulletin("Inflation", embargo="September 11, 2026",
                                                   due="October 14, 2026"))):
            with self.subTest(factor=factor):
                client = Mock()
                client.get.return_value = pdf_response()
                with patch("official_bls._pdf_text", return_value=text):
                    state = fetch_release_state(factor, session=client, now=NOW)
                self.assertEqual(state["period"], "2026-08")
                self.assertEqual(state["release_url"], states()[factor]["release_url"])
                self.assertEqual(client.get.call_args.args[0], state["release_url"])
                self.assertEqual(client.get.call_count, 1)

    def test_bulletin_conflict_with_pinned_successor_date_blocks(self):
        client = Mock()
        client.get.return_value = pdf_response()
        changed = bulletin("Arbeitsmarkt", due="October 3, 2026")
        with patch("official_bls._pdf_text", return_value=changed), \
             self.assertRaisesRegex(BlsInvalid, "BLS_PDF_SCHEDULE_CONFLICT"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW)

    def test_due_rechecks_new_bulletin_without_api_and_reuses_cache_before_due(self):
        for factor, due, embargo, next_due in (
            ("Arbeitsmarkt", "2026-10-02T12:30:00+00:00", "October 2, 2026", "November 6, 2026"),
            ("Inflation", "2026-10-14T12:30:00+00:00", "October 14, 2026", "November 10, 2026"),
        ):
            with self.subTest(factor=factor):
                client = Mock()
                client.get.return_value = pdf_response(403)
                old = states()[factor]
                self.assertEqual(fetch_release_state(factor, session=client,
                                 now=datetime.fromisoformat(due) - timedelta(seconds=1),
                                 previous_state=old)["period"], "2026-08")
                client.get.assert_not_called()
                client.get.return_value = pdf_response()
                text = bulletin(factor, period="September 2026", embargo=embargo,
                                successor="October 2026", due=next_due)
                with patch("official_bls._pdf_text", return_value=text):
                    state = fetch_release_state(factor, session=client,
                                                now=datetime.fromisoformat(due),
                                                previous_state=old)
                self.assertEqual(state["period"], "2026-09")
                self.assertEqual(state["embargo_ends_at"], due)
                self.assertEqual(client.get.call_args.args[0], _pdf_url(factor, datetime.fromisoformat(due)))

    def test_pdf_403_and_retry_cooldown_fail_closed_after_due(self):
        due = datetime.fromisoformat(states()["Arbeitsmarkt"]["next_due_at"])
        client = Mock()
        client.get.return_value = pdf_response(403)
        diagnostic = {}
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due,
                                previous_state=states()["Arbeitsmarkt"], diagnostics=diagnostic)
        self.assertTrue(diagnostic["pdf_attempted"])
        with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_RECHECK_COOLDOWN"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due + timedelta(minutes=30),
                                previous_state=states()["Arbeitsmarkt"], last_pdf_attempt=due)
        self.assertEqual(client.get.call_count, 1)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due + timedelta(hours=1),
                                previous_state=states()["Arbeitsmarkt"], last_pdf_attempt=due)
        self.assertEqual(client.get.call_count, 2)

    def test_bls_api_exact_latest_month_and_m13_rejected(self):
        for factor in ("Arbeitsmarkt", "Inflation"):
            with self.subTest(factor=factor):
                self.assertEqual(parse_api_latest(api_payload(factor), factor, "2026-08"),
                                 "4.1" if factor == "Arbeitsmarkt" else "321.123")
                for changes in ({"period": "M13"}, {"period": "M07"}, {"latest": "false"}):
                    payload = api_payload(factor)
                    payload["Results"]["series"][0]["data"][0].update(changes)
                    with self.assertRaises(BlsInvalid):
                        parse_api_latest(payload, factor, "2026-08")
                payload = api_payload(factor)
                payload["Results"]["series"][0]["data"].append(
                    {"year": "2026", "period": "M13", "periodName": "Annual", "value": "3"})
                self.assertEqual(parse_api_latest(payload, factor, "2026-08"),
                                 "4.1" if factor == "Arbeitsmarkt" else "321.123")
                payload["Results"]["series"][0]["data"].append(
                    {"year": "2026", "period": "M09", "periodName": "September", "value": "3"})
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_PERIOD_MISMATCH"):
                    parse_api_latest(payload, factor, "2026-08")
                payload = api_payload(factor)
                payload["Results"]["series"][0]["data"].append(
                    dict(payload["Results"]["series"][0]["data"][0]))
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_DUPLICATE_PERIOD"):
                    parse_api_latest(payload, factor, "2026-08")
                payload = api_payload(factor)
                payload["Results"] = [payload["Results"]]
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_RESULTS_INVALID"):
                    parse_api_latest(payload, factor, "2026-08")
                payload = api_payload(factor)
                payload["status"] = "REQUEST_NOT_PROCESSED"
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_STATUS_INVALID"):
                    parse_api_latest(payload, factor, "2026-08")

    def test_dol_403_uses_api_only_after_24h_with_pinned_next_due(self):
        for factor in ("Arbeitsmarkt", "Inflation"):
            with self.subTest(factor=factor):
                client = Mock()
                client.get.side_effect = [pdf_response(403), api_response(factor)]
                state = fetch_release_state(factor, session=client, now=NOW)
                self.assertEqual(state["period"], "2026-08")
                self.assertEqual(state["proof_source"], "BLS_API_V1")
                self.assertEqual(state["first_observed_at"], NOW.isoformat())
                self.assertEqual(state["next_due_at"], PINNED_DUES[factor]["2026-09"])
                self.assertEqual(client.get.call_args_list[1].args[0], _api_url(factor))

    def test_dol_timeout_can_use_api_but_pdf_schema_error_cannot(self):
        client = Mock()
        client.get.side_effect = [requests.exceptions.Timeout(), api_response("Arbeitsmarkt")]
        state = fetch_release_state("Arbeitsmarkt", session=client, now=NOW)
        self.assertEqual(state["proof_source"], "BLS_API_V1")
        self.assertEqual(client.get.call_count, 2)

    def test_api_daily_cap_and_unknown_schedule_fail_closed(self):
        client = Mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_DAILY_LIMIT"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                                last_api_attempt=NOW - timedelta(hours=1))
        self.assertEqual(client.get.call_count, 1)
        client.reset_mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_ATTEMPT_STATE_INVALID"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW, api_budget_blocked=True)
        client.reset_mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_SCHEDULE_UNKNOWN"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=datetime(2026, 12, 5, 15, tzinfo=timezone.utc))
        self.assertEqual(client.get.call_count, 1)

    def test_api_is_not_used_for_unverified_pdf_content_or_unauthorized_http(self):
        client = Mock()
        client.get.return_value = pdf_response(401)
        with self.assertRaises(requests.exceptions.HTTPError):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW)
        self.assertEqual(client.get.call_count, 1)
        client.reset_mock()
        malformed = pdf_response()
        malformed.headers = {"Content-Type": "text/html"}
        client.get.return_value = malformed
        with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_CONTENT_TYPE_INVALID"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW)
        self.assertEqual(client.get.call_count, 1)

    def test_api_error_consumes_attempt_and_old_proof_does_not_cross_due(self):
        due = datetime.fromisoformat(states()["Arbeitsmarkt"]["next_due_at"])
        now = due + timedelta(days=1)
        client = Mock()
        bad = api_response("Arbeitsmarkt")
        bad.json.return_value["status"] = "REQUEST_NOT_PROCESSED"
        client.get.side_effect = [pdf_response(403), bad]
        diagnostic = {}
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_STATUS_INVALID"):
            fetch_release_state("Arbeitsmarkt", session=client, now=now,
                                previous_state=states()["Arbeitsmarkt"], diagnostics=diagnostic)
        self.assertTrue(diagnostic["api_attempted"])
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_DAILY_LIMIT"):
            fetch_release_state("Arbeitsmarkt", session=Mock(get=Mock(return_value=pdf_response(403))),
                                now=now + timedelta(hours=1),
                                previous_state=states()["Arbeitsmarkt"], last_api_attempt=now)

    def test_provider_cooldown_without_network_does_not_advance_pdf_retry(self):
        client = Mock()
        client.usage = {"www.dol.gov": {"requests_this_run": 0}}
        client.get.side_effect = requests.RequestException("PROVIDER_COOLDOWN")
        diagnostics = {}
        with self.assertRaises(requests.RequestException):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                                diagnostics=diagnostics)
        self.assertNotIn("pdf_attempted", diagnostics)

    def test_prior_bulletin_can_derive_unpinned_successor_after_last_known_schedule(self):
        prior = {"period": "2026-11", "embargo_ends_at": PINNED_DUES["Arbeitsmarkt"]["2026-11"],
                 "next_due_at": "2027-01-08T13:30:00+00:00",
                 "release_url": "https://www.dol.gov/newsroom/economicdata/empsit_12042026.pdf"}
        now = datetime(2027, 1, 8, 14, tzinfo=timezone.utc)
        client = Mock()
        client.get.return_value = pdf_response()
        text = bulletin("Arbeitsmarkt", period="December 2026", embargo="January 8, 2027",
                        successor="January 2027", due="February 5, 2027")
        with patch("official_bls._pdf_text", return_value=text):
            state = fetch_release_state("Arbeitsmarkt", session=client, now=now, previous_state=prior)
        self.assertEqual(state["period"], "2026-12")
        self.assertEqual(client.get.call_args.args[0],
                         "https://www.dol.gov/newsroom/economicdata/empsit_01082027.pdf")
        old_bulletin = bulletin("Arbeitsmarkt", period="November 2026",
                                embargo="December 4, 2026", successor="December 2026",
                                due="January 8, 2027")
        cold_client = Mock()
        cold_client.get.return_value = pdf_response()
        with patch("official_bls._pdf_text", return_value=old_bulletin), \
             self.assertRaisesRegex(BlsInvalid, "BLS_PDF_RELEASE_NOT_CURRENT"):
            fetch_release_state("Arbeitsmarkt", session=cold_client,
                                now=now, previous_state=None)

    def test_legacy_verified_state_is_reused_before_due(self):
        old = {**states()["Arbeitsmarkt"]}
        old["published_at"] = old.pop("embargo_ends_at")
        client = Mock()
        state = fetch_release_state("Arbeitsmarkt", session=client, now=NOW, previous_state=old)
        self.assertEqual(state["embargo_ends_at"], old["published_at"])
        client.get.assert_not_called()

    def test_legacy_bls_publication_is_labeled_as_embargo_not_upload(self):
        state = states()["Arbeitsmarkt"]
        record = live_data.build_record("Arbeitsmarkt", 30, {
            "value": 4.1, "date": "2026-08-01", "series_id": "UNRATE",
            "bls_release_period": state["period"],
            "bls_release_url": state["release_url"],
            "published_at": state["embargo_ends_at"],
            "next_due_at": state["next_due_at"],
        }, "FRESH", NOW.isoformat())
        st = MagicMock()
        data = {"completed_at": NOW.isoformat(), "currencies": {"USD": {"Arbeitsmarkt": record}}}
        with patch.object(live_data, "load", return_value=data), \
             patch.object(live_data, "now_utc", return_value=NOW):
            live_data.render_status(st)
        rows = st.dataframe.call_args_list[1].args[0]
        labor = next(row for row in rows if row["Währung"] == "USD" and row["Faktor"] == "Arbeitsmarkt")
        self.assertEqual(labor["Veröffentlicht"],
                         state["embargo_ends_at"] + " (Embargo-Ende; Uploadzeit unbekannt)")


class BlsCollectorTests(unittest.TestCase):
    def test_fred_comparison_accepts_timestamp_date_but_rejects_bad_period(self):
        observation = {"date": pd.Timestamp("2026-08-01"), "source": "FRED", "value": 4.1}
        self.assertEqual(live_data._fred_raw_for_bls(Mock(), "Arbeitsmarkt", observation, "2026-08"), "4.1")
        with self.assertRaisesRegex(BlsInvalid, "BLS_FRED_PERIOD_MISMATCH"):
            live_data._fred_raw_for_bls(Mock(), "Arbeitsmarkt", observation, "2026-09")

    def record(self, factor, checked, due=None):
        state = states()[factor]
        return live_data.build_record(factor, 70 if factor == "Inflation" else 30, {
            "value": 3.4 if factor == "Inflation" else 4.1,
            "date": "2026-08-01", "reference_period": "2026-08",
            "series_id": "CPIAUCNS" if factor == "Inflation" else "UNRATE",
            "source": "FRED / BLS" if factor == "Inflation" else "FRED",
            "bls_release_period": "2026-08" if due else None,
            "bls_release_url": state["release_url"] if due else None,
            "bls_embargo_ends_at": state["embargo_ends_at"] if due else None,
            "published_at": state["embargo_ends_at"] if due else None,
            "next_due_at": due,
        }, "FRESH", checked.isoformat())

    def run_collector(self, reports, observations, previous=None, at=NOW,
                      previous_data=None, attempted=(), api_attempted=(), cpi_raw="321.123"):
        app = Mock(FRED_KEY="test")
        app.get_fred_data.return_value = (pd.DataFrame({"date": pd.to_datetime(["2026-08-01"]),
                                                         "value": [float(cpi_raw)]}), None, True)
        self.source_calls = {}
        app.compute_currency_details.return_value = {
            **{factor: 70 if factor == "Inflation" else 30 for factor in reports},
            "_freshness": {factor: "FRESH" for factor in reports},
            "_observations": observations,
        }
        def source(factor, **kwargs):
            self.source_calls[factor] = kwargs
            if factor in attempted:
                kwargs["diagnostics"]["pdf_attempted"] = True
            if factor in api_attempted:
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
            return self.last_dataset["currencies"]["USD"]

    def test_current_period_qualifies_with_explicit_metadata_and_due_blocks_read_time(self):
        observations = {
            "Inflation": {"value": 3.4, "date": "2026-08-01", "series_id": "CPIAUCNS", "source": "FRED / BLS"},
            "Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01", "series_id": "UNRATE", "source": "FRED"},
        }
        rows = self.run_collector(states(), observations)
        for factor, row in rows.items():
            self.assertEqual(row["validation"], "VALID")
            self.assertTrue(live_data.eligible(row, NOW, factor=factor, currency="USD")[0])
            self.assertIn("Embargo-Ende", row["observation"]["publication_basis"])
            self.assertEqual(row["observation"]["bls_embargo_ends_at"], states()[factor]["embargo_ends_at"])
            self.assertEqual(live_data.public_observation(row["observation"])["bls_release_url"],
                             states()[factor]["release_url"])
            due = datetime.fromisoformat(row["next_due_at"])
            self.assertTrue(live_data.eligible(row, due - timedelta(seconds=1),
                                               factor=factor, currency="USD")[0])
            self.assertFalse(live_data.eligible(row, due, factor=factor, currency="USD")[0])

    def test_null_due_and_fred_lag_cannot_requalify(self):
        old = self.record("Arbeitsmarkt", NOW, due=None)
        self.assertFalse(live_data.eligible(old, NOW, factor="Arbeitsmarkt", currency="USD")[0])
        labor = {**states()["Arbeitsmarkt"], "period": "2026-09",
                 "embargo_ends_at": "2026-10-02T12:30:00+00:00",
                 "next_due_at": "2026-11-06T13:30:00+00:00",
                 "release_url": "https://www.dol.gov/newsroom/economicdata/empsit_10022026.pdf"}
        rows = self.run_collector({**states(), "Arbeitsmarkt": labor},
                                  {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                                     "series_id": "UNRATE", "source": "FRED"}},
                                  previous={"Arbeitsmarkt": old},
                                  at=datetime(2026, 10, 2, 13, tzinfo=timezone.utc))
        self.assertEqual(rows["Arbeitsmarkt"]["validation"], "UNVERIFIED")
        self.assertIsNone(rows["Arbeitsmarkt"]["score"])
        self.assertEqual(self.last_dataset["bls_release_states"]["Arbeitsmarkt"]["period"], "2026-09")
        prior_dataset = self.last_dataset
        caught_up = self.run_collector({**states(), "Arbeitsmarkt": labor},
                                       {"Arbeitsmarkt": {"value": 4.2, "date": "2026-09-01",
                                                          "series_id": "UNRATE", "source": "FRED"}},
                                       previous_data=prior_dataset,
                                       at=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))
        self.assertTrue(live_data.eligible(caught_up["Arbeitsmarkt"],
                                           datetime(2026, 10, 2, 14, tzinfo=timezone.utc),
                                           factor="Arbeitsmarkt", currency="USD")[0])

    def test_outage_keeps_valid_cache_before_due_but_not_after(self):
        old = self.record("Arbeitsmarkt", NOW, states()["Arbeitsmarkt"]["next_due_at"])
        observations = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                          "series_id": "UNRATE", "source": "FRED"}}
        reports = {**states(), "Arbeitsmarkt": requests.exceptions.Timeout()}
        rows = self.run_collector(reports, observations, previous={"Arbeitsmarkt": old})
        self.assertEqual(rows["Arbeitsmarkt"]["validation"], "VALID")
        due = datetime.fromisoformat(old["next_due_at"])
        self.assertFalse(live_data.eligible(rows["Arbeitsmarkt"], due,
                                            factor="Arbeitsmarkt", currency="USD")[0])
        later = self.run_collector(reports, observations, previous={"Arbeitsmarkt": old}, at=due)
        self.assertNotEqual(later["Arbeitsmarkt"]["validation"], "VALID")
        forbidden = requests.exceptions.HTTPError(response=Mock(status_code=403))
        later = self.run_collector({**states(), "Arbeitsmarkt": forbidden}, observations,
                                   previous={"Arbeitsmarkt": old}, at=due)
        self.assertEqual(later["Arbeitsmarkt"]["validation"], "UNVERIFIED")

    def test_pdf_attempt_timestamp_persists_without_api_quota_state(self):
        observation = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                       "series_id": "UNRATE", "source": "FRED"}}
        self.run_collector(states(), observation, attempted=("Arbeitsmarkt",))
        first = self.last_dataset
        self.assertEqual(first["bls_pdf_attempts"]["Arbeitsmarkt"], NOW.isoformat())
        self.assertNotIn("bls_api_budget", first)
        self.run_collector(states(), observation, previous_data=first, at=NOW + timedelta(hours=1))
        self.assertEqual(self.source_calls["Arbeitsmarkt"]["last_pdf_attempt"], NOW)

    def test_api_raw_match_keeps_fred_scores_and_persists_restart_proof(self):
        observations = {
            "Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01", "series_id": "UNRATE", "source": "FRED"},
            "Inflation": {"value": 3.4, "date": "2026-08-01", "series_id": "CPIAUCNS", "source": "FRED / BLS"},
        }
        rows = self.run_collector({factor: api_state(factor) for factor in observations}, observations,
                                  api_attempted=tuple(observations))
        for factor, score in (("Arbeitsmarkt", 30), ("Inflation", 70)):
            self.assertEqual(rows[factor]["validation"], "VALID")
            self.assertEqual(rows[factor]["score"], score)
            self.assertEqual(rows[factor]["observation"]["bls_proof_source"], "BLS_API_V1")
            self.assertEqual(rows[factor]["published_at"], NOW.isoformat())
            self.assertTrue(live_data.eligible(rows[factor], NOW, factor=factor, currency="USD")[0])
            due = datetime.fromisoformat(rows[factor]["next_due_at"])
            self.assertFalse(live_data.eligible(rows[factor], due, factor=factor, currency="USD")[0])
            self.assertEqual(self.last_dataset["bls_api_attempts"][factor], NOW.isoformat())
            self.assertEqual(self.last_dataset["bls_release_states"][factor]["fred_raw_value"],
                             rows[factor]["observation"]["bls_fred_raw_value"])
        self.assertEqual(rows["Inflation"]["observation"]["value"], 3.4)
        tampered = dict(rows["Inflation"])
        tampered["observation"] = dict(rows["Inflation"]["observation"], bls_fred_raw_value="0")
        self.assertFalse(live_data.eligible(tampered, NOW, factor="Inflation", currency="USD")[0])
        st = MagicMock()
        with patch.object(live_data, "load", return_value=self.last_dataset), \
             patch.object(live_data, "now_utc", return_value=NOW):
            live_data.render_status(st)
        captions = " ".join(str(call.args[0]) for call in st.caption.call_args_list)
        self.assertIn("BLS.gov cannot vouch for the data", captions)
        self.assertIn("BLS API Terms of Service", captions)
        prior = self.last_dataset
        rows = self.run_collector({factor: prior["bls_release_states"][factor] for factor in observations},
                                  observations, previous_data=prior, at=NOW + timedelta(hours=1))
        self.assertEqual(rows["Inflation"]["validation"], "VALID")
        self.assertEqual(self.source_calls["Inflation"]["last_api_attempt"], NOW)

    def test_api_raw_mismatch_and_missing_cpi_raw_fail_closed(self):
        observation = {"Inflation": {"value": 3.4, "date": "2026-08-01",
                                     "series_id": "CPIAUCNS", "source": "FRED / BLS"}}
        rows = self.run_collector({"Inflation": api_state("Inflation")}, observation, cpi_raw="321.122")
        self.assertEqual(rows["Inflation"]["validation"], "UNVERIFIED")
        self.assertIsNone(rows["Inflation"]["score"])
        self.assertNotIn("Inflation", self.last_dataset["bls_release_states"])
        row = self.record("Arbeitsmarkt", NOW, states()["Arbeitsmarkt"]["next_due_at"])
        row["next_due_at"] = None
        error = requests.exceptions.HTTPError(response=Mock(status_code=403))
        rows = self.run_collector({"Arbeitsmarkt": error},
                                  {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                                     "series_id": "UNRATE", "source": "FRED"}},
                                  previous={"Arbeitsmarkt": row})
        self.assertEqual(rows["Arbeitsmarkt"]["reason"], "BLS-Termin/Beleg unbekannt")

    def test_corrupt_api_attempt_marker_survives_restart_and_blocks_fallback(self):
        observation = {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                                       "series_id": "UNRATE", "source": "FRED"}}
        previous = {"model_version": live_data.MODEL, "currencies": {"USD": {}},
                    "bls_api_attempts": {"Arbeitsmarkt": "corrupt"}}
        self.run_collector({"Arbeitsmarkt": BlsInvalid("BLS_API_ATTEMPT_STATE_INVALID")},
                           observation, previous_data=previous)
        first = self.last_dataset
        self.assertEqual(first["bls_api_attempts"]["Arbeitsmarkt"], "INVALID")
        self.run_collector({"Arbeitsmarkt": BlsInvalid("BLS_API_ATTEMPT_STATE_INVALID")},
                           observation, previous_data=first, at=NOW + timedelta(hours=1))
        self.assertTrue(self.source_calls["Arbeitsmarkt"]["api_budget_blocked"])

    def test_one_dol_403_cooldown_allows_both_factor_api_proofs(self):
        observations = {
            "Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01", "series_id": "UNRATE", "source": "FRED"},
            "Inflation": {"value": 3.4, "date": "2026-08-01", "series_id": "CPIAUCNS", "source": "FRED / BLS"},
        }
        underlying = Mock()
        def response(url, **kwargs):
            if url.startswith("https://www.dol.gov/"):
                return pdf_response(403)
            if url == _api_url("Arbeitsmarkt"):
                return api_response("Arbeitsmarkt")
            if url == _api_url("Inflation"):
                return api_response("Inflation")
            fred = Mock(status_code=200)
            fred.json.return_value = {}
            return fred
        underlying.get.side_effect = response
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            transport = CollectorTransport(client=underlying,
                                           status_path=Path(directory) / "status.json", clock=lambda: NOW)
            app = Mock(FRED_KEY="test", requests=transport)
            app.compute_currency_details.return_value = {
                "Arbeitsmarkt": 30, "Inflation": 70,
                "_freshness": {factor: "FRESH" for factor in observations},
                "_observations": observations}
            app.get_fred_data.return_value = (pd.DataFrame({"date": pd.to_datetime(["2026-08-01"]),
                                                              "value": [321.123]}), None, True)
            with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), \
                 patch.object(live_data, "CURRENCIES", ("USD",)), \
                 patch.object(live_data, "FACTORS", {"Inflation": 20, "Arbeitsmarkt": 20}), \
                 patch.object(live_data, "now_utc", return_value=NOW), \
                 patch("source_contracts.validate_fred_metadata", return_value=True):
                live_data.collect(app, path)
            data = live_data.load(path)
        self.assertEqual(transport.usage["www.dol.gov"]["requests_this_run"], 1)
        self.assertEqual(transport.usage["api.bls.gov"]["requests_this_run"], 2)
        for factor in observations:
            self.assertEqual(data["currencies"]["USD"][factor]["validation"], "VALID")
            self.assertEqual(data["bls_api_attempts"][factor], NOW.isoformat())
        self.assertEqual(data["bls_provider_status"]["Inflation"]["dol"],
                         "PRIOR_HTTP_403_COOLDOWN")

    def test_operator_quota_separates_bls_official_limit_from_local_attempts(self):
        st = MagicMock()
        data = {"completed_at": NOW.isoformat(), "currencies": {},
                "bls_api_attempts": {"Arbeitsmarkt": NOW.isoformat(),
                                     "Inflation": (NOW - timedelta(days=1)).isoformat()}}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "data_collection_status.json").write_text(
                '{"providers":{"api.bls.gov":{"status":"SUCCESS","requests_this_run":1}}}')
            with patch.object(live_data, "load", return_value=data), \
                 patch.object(live_data, "now_utc", return_value=NOW), \
                 patch.object(live_data, "selected_live_directory", return_value=folder):
                live_data.render_status(st, authorized=True)
        provider_rows = st.dataframe.call_args_list[-1].args[0]
        self.assertEqual(provider_rows[0]["Limit"], "25 Anfragen/Tag (BLS API v1 ohne Registrierung)")
        self.assertEqual(provider_rows[0]["Restkontingent"], "Unbekannt")
        captions = " ".join(str(call.args[0]) for call in st.caption.call_args_list)
        self.assertIn("1/2 lokal gespeicherte Faktorversuche", captions)
        self.assertIn("Prozessabbruch", captions)


if __name__ == "__main__":
    unittest.main()
