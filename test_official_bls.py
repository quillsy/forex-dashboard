"""Offline checks for USD BLS bulletin proof and FRED period gating."""
import tempfile
import unittest
import os
import calendar
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import requests
import pandas as pd

import live_data
from provider_transport import CollectorTransport
from official_bls import (BlsInvalid, PINNED_DUES, USER_AGENT, _api_url, _bls_pdf_url,
                          _pdf_url, _verified_previous, fetch_release_state,
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


def pdf_response(status=200, *, url=None):
    response = Mock()
    response.status_code = status
    response.headers = {"Content-Type": "application/pdf"}
    response.content = b"%PDF-1.7 fixture"
    response.url = url if status >= 400 else url or states()["Arbeitsmarkt"]["release_url"]
    response.history = []
    if status >= 400:
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
    return response


def api_payload(factor, *, period="M08", value=None, year="2026"):
    # Synthetic values exercise equality checks, not a claim about the live index.
    month = int(period[1:])
    period_name = calendar.month_name[month] if 1 <= month <= 12 else "Annual"
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [{
        "seriesID": "LNS14000000" if factor == "Arbeitsmarkt" else "CUUR0000SA0",
        "data": [{"year": year, "period": period, "periodName": period_name,
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
                client.get.return_value = pdf_response(url=states()[factor]["release_url"])
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
                client.get.return_value = pdf_response(url=_pdf_url(factor, datetime.fromisoformat(due)))
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
        self.assertEqual(client.get.call_count, 2)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
            fetch_release_state("Arbeitsmarkt", session=client, now=due + timedelta(hours=1),
                                previous_state=states()["Arbeitsmarkt"], last_pdf_attempt=due)
        self.assertEqual(client.get.call_count, 4)

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
                client.get.side_effect = ([pdf_response(403)]
                                         + ([pdf_response(403)] if factor == "Arbeitsmarkt" else [])
                                         + [api_response(factor)])
                state = fetch_release_state(factor, session=client, now=NOW)
                self.assertEqual(state["period"], "2026-08")
                self.assertEqual(state["proof_source"], "BLS_API_V1")
                self.assertEqual(state["first_observed_at"], NOW.isoformat())
                self.assertEqual(state["next_due_at"], PINNED_DUES[factor]["2026-09"])
                self.assertEqual(client.get.call_args_list[-1].args[0], _api_url(factor))

    def test_dol_timeout_can_use_api_but_pdf_schema_error_cannot(self):
        client = Mock()
        client.get.side_effect = [requests.exceptions.Timeout(), pdf_response(403), api_response("Arbeitsmarkt")]
        state = fetch_release_state("Arbeitsmarkt", session=client, now=NOW)
        self.assertEqual(state["proof_source"], "BLS_API_V1")
        self.assertEqual(client.get.call_count, 3)

    def test_api_daily_cap_and_unknown_schedule_fail_closed(self):
        client = Mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_DAILY_LIMIT"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW,
                                last_api_attempt=NOW - timedelta(hours=1))
        self.assertEqual(client.get.call_count, 2)
        client.reset_mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_ATTEMPT_STATE_INVALID"):
            fetch_release_state("Arbeitsmarkt", session=client, now=NOW, api_budget_blocked=True)
        client.reset_mock()
        client.get.return_value = pdf_response(403)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_SCHEDULE_UNKNOWN"):
            fetch_release_state("Arbeitsmarkt", session=client,
                                now=datetime(2026, 12, 5, 15, tzinfo=timezone.utc))
        self.assertEqual(client.get.call_count, 2)

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
        client.get.side_effect = [pdf_response(403), pdf_response(403), bad]
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
        client.get.return_value = pdf_response(url="https://www.dol.gov/newsroom/economicdata/empsit_01082027.pdf")
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
        cold_client.get.return_value = pdf_response(url=prior["release_url"])
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


class BlsPdfFallbackTests(unittest.TestCase):
    DUE = datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)
    CURRENT = DUE + timedelta(hours=1)
    URL = "https://www.bls.gov/news.release/pdf/empsit.pdf"
    TEXT = bulletin("Arbeitsmarkt", period="September 2026", embargo="October 2, 2026",
                    successor="October 2026", due="November 6, 2026")

    def test_verified_bls_pdf_after_dol_outage_keeps_exact_release_contract(self):
        for failure in [pdf_response(status) for status in (403, 408, 425, 429, 500, 503)] + [
                requests.exceptions.Timeout(), requests.exceptions.ConnectionError()]:
            with self.subTest(failure=type(failure).__name__):
                client = Mock()
                client.get.side_effect = [failure, pdf_response(url=self.URL)]
                diagnostics = {}
                with patch("official_bls._pdf_text", return_value=self.TEXT):
                    state = fetch_release_state("Arbeitsmarkt", session=client,
                                                now=self.CURRENT, diagnostics=diagnostics)
                self.assertEqual(state["proof_source"], "BLS_PDF")
                self.assertEqual(state["release_url"], self.URL)
                self.assertEqual(state["period"], "2026-09")
                self.assertEqual(state["embargo_ends_at"], self.DUE.isoformat())
                self.assertEqual(state["next_due_at"], "2026-11-06T13:30:00+00:00")
                self.assertNotIn("first_observed_at", state)
                self.assertTrue(diagnostics["pdf_attempted"])
                self.assertTrue(diagnostics["bls_pdf_attempted"])
                self.assertEqual(diagnostics["bls_pdf_status"], "HTTP_200")
                self.assertEqual([call.args[0] for call in client.get.call_args_list],
                                 [_pdf_url("Arbeitsmarkt", self.DUE), self.URL])
                for call in client.get.call_args_list:
                    self.assertFalse(call.kwargs["allow_redirects"])
                    self.assertEqual(call.kwargs["cookies"], {})
                    self.assertEqual(call.kwargs["headers"]["Cookie"], "")
                    self.assertEqual(call.kwargs["headers"]["User-Agent"], USER_AGENT)

    def test_identified_bot_never_sends_a_supplied_session_cookie_or_follows_redirects(self):
        session = requests.Session()
        session.cookies.set("fixture_cookie", "fixture_value", domain=".bls.gov")
        sent = []
        def send(prepared, **kwargs):
            sent.append((prepared, kwargs))
            response = requests.Response()
            response.url = prepared.url
            response.status_code = 403 if prepared.url.startswith("https://www.dol.gov/") else 200
            response.headers = {"Content-Type": "application/pdf"}
            response._content = b"%PDF-1.7 fixture"
            return response
        with patch.object(session, "send", side_effect=send), \
             patch("official_bls._pdf_text", return_value=self.TEXT):
            fetch_release_state("Arbeitsmarkt", session=session, now=self.CURRENT)
        self.assertEqual(len(sent), 2)
        self.assertIn("https://github.com/quillsy/forex-dashboard", USER_AGENT)
        self.assertNotIn("Mozilla", USER_AGENT)
        for prepared, options in sent:
            self.assertEqual(prepared.headers.get("Cookie"), "")
            self.assertEqual(prepared.headers["User-Agent"], USER_AGENT)
            self.assertFalse(options["allow_redirects"])

    def test_primary_security_and_unapproved_http_errors_never_open_another_transport(self):
        for error in (requests.exceptions.SSLError(), pdf_response(401), pdf_response(404),
                      requests.RequestException("PROVIDER_COOLDOWN")):
            with self.subTest(error=type(error).__name__):
                client = Mock()
                client.get.side_effect = [error, pdf_response(url=self.URL)]
                with self.assertRaises(requests.RequestException):
                    fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT)
                self.assertEqual(client.get.call_count, 1)

    def test_primary_invalid_content_and_redirects_are_never_hidden_by_bls(self):
        for invalid in ("type", "redirect", "url", "bytes", "contract"):
            with self.subTest(invalid=invalid):
                response = pdf_response(url=_pdf_url("Arbeitsmarkt", self.DUE))
                if invalid == "type":
                    response.headers = {"Content-Type": "text/html"}
                elif invalid == "redirect":
                    response.status_code = 302
                    response.headers["Location"] = self.URL
                elif invalid == "url":
                    response.url = self.URL
                client = Mock()
                client.get.return_value = response
                parse_error = BlsInvalid("BLS_PDF_INVALID" if invalid == "bytes" else "BLS_PDF_RELEASE_SCHEMA_INVALID")
                with patch("official_bls._pdf_text", side_effect=parse_error), self.assertRaises(BlsInvalid):
                    fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT)
                self.assertEqual(client.get.call_count, 1)

    def test_bls_wrong_stale_future_and_calendar_conflicting_editions_never_use_api(self):
        for text in (
                bulletin("Arbeitsmarkt"),
                bulletin("Arbeitsmarkt", period="October 2026", embargo="November 6, 2026",
                         successor="November 2026", due="December 4, 2026"),
                bulletin("Arbeitsmarkt", period="September 2026", embargo="October 3, 2026",
                         successor="October 2026", due="November 6, 2026"),
                bulletin("Arbeitsmarkt", period="September 2026", embargo="October 1, 2026",
                         successor="October 2026", due="November 6, 2026"),
                bulletin("Arbeitsmarkt", period="September 2026", embargo="October 2, 2026",
                         successor="October 2026", due="November 7, 2026"),
                self.TEXT.replace("THE EMPLOYMENT SITUATION", "CONSUMER PRICE INDEX"),
                self.TEXT + self.TEXT):
            with self.subTest(text=text[:75]):
                client = Mock()
                client.get.side_effect = [pdf_response(403), pdf_response(url=self.URL)]
                with patch("official_bls._pdf_text", return_value=text), self.assertRaises(BlsInvalid):
                    fetch_release_state("Arbeitsmarkt", session=client,
                                        now=self.DUE + timedelta(days=1))
                self.assertEqual(client.get.call_count, 2)

    def test_bls_redirect_or_unapproved_final_url_never_followed_or_used_as_proof(self):
        for status, target, history in (
                (301, self.URL, []), (302, self.URL, []), (307, self.URL, []), (308, self.URL, []),
                (200, "https://example.invalid/empsit.pdf", []),
                (200, "https://data.bls.gov/empsit.pdf", []),
                (200, self.URL + "?edition=202609", []),
                (200, self.URL, [Mock(status_code=302)])):
            with self.subTest(status=status, target=target):
                alternate = pdf_response(status, url=target)
                alternate.history = history
                alternate.headers["Location"] = "https://example.invalid/target.pdf"
                client = Mock()
                client.get.side_effect = [pdf_response(403), alternate]
                with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_REDIRECT_OR_URL_INVALID"):
                    fetch_release_state("Arbeitsmarkt", session=client,
                                        now=self.DUE + timedelta(days=1))
                self.assertEqual(client.get.call_count, 2)
                self.assertEqual(client.get.call_args.args[0], self.URL)
                self.assertFalse(client.get.call_args.kwargs["allow_redirects"])

    def test_bls_content_type_bytes_and_tls_fail_closed_without_api(self):
        for invalid in ("type", "bytes", "tls"):
            with self.subTest(invalid=invalid):
                alternate = pdf_response(url=self.URL)
                if invalid == "type":
                    alternate.headers = {"Content-Type": "text/html; application/pdf"}
                elif invalid == "bytes":
                    alternate.content = b"<html>not a bulletin</html>"
                else:
                    alternate = requests.exceptions.SSLError()
                client = Mock()
                client.get.side_effect = [pdf_response(403), alternate]
                with self.assertRaises((BlsInvalid, requests.RequestException)):
                    fetch_release_state("Arbeitsmarkt", session=client,
                                        now=self.DUE + timedelta(days=1))
                self.assertEqual(client.get.call_count, 2)

    def test_error_response_from_redirect_or_another_target_does_not_open_more_sources(self):
        for origin in ("dol", "bls"):
            for invalid in ("redirect", "target"):
                with self.subTest(origin=origin, invalid=invalid):
                    denied = pdf_response(403, url=(self.URL if origin == "bls"
                                                   else _pdf_url("Arbeitsmarkt", self.DUE)))
                    if invalid == "redirect":
                        denied.history = [Mock(status_code=302)]
                    else:
                        denied.url = "https://example.invalid/denied.pdf"
                    client = Mock()
                    client.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [denied]
                    with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_REDIRECT_OR_URL_INVALID"):
                        fetch_release_state("Arbeitsmarkt", session=client,
                                            now=self.DUE + timedelta(days=1))
                    self.assertEqual(client.get.call_count, 2 if origin == "bls" else 1)

    def test_tls_with_attached_http_denial_never_opens_another_source(self):
        for origin in ("dol", "bls"):
            for status in (403, 429, 503):
                with self.subTest(origin=origin, status=status):
                    denied = pdf_response(status, url=(self.URL if origin == "bls"
                                                       else _pdf_url("Arbeitsmarkt", self.DUE)))
                    error = requests.exceptions.SSLError("TLS_FAILURE", response=denied)
                    client = Mock()
                    client.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                        error, api_response("Arbeitsmarkt", period="M09") if origin == "bls"
                        else pdf_response(url=self.URL)]
                    diagnostics = {}
                    with patch("official_bls._pdf_text", return_value=self.TEXT), \
                         self.assertRaises(requests.exceptions.SSLError):
                        fetch_release_state("Arbeitsmarkt", session=client,
                                            now=self.DUE + timedelta(days=1), diagnostics=diagnostics)
                    self.assertEqual(client.get.call_count, 2 if origin == "bls" else 1)
                    self.assertTrue(diagnostics["pdf_attempted"])
                    self.assertEqual(diagnostics.get("bls_pdf_attempted", False), origin == "bls")
                    self.assertNotIn("api_attempted", diagnostics)

    def test_raised_http_denial_with_unsafe_metadata_never_opens_another_source(self):
        for origin in ("dol", "bls"):
            for invalid in ("redirect_history", "redirect_status", "scheme", "host", "credentials"):
                with self.subTest(origin=origin, invalid=invalid):
                    url = self.URL if origin == "bls" else _pdf_url("Arbeitsmarkt", self.DUE)
                    denied = pdf_response(403, url=url)
                    if invalid == "redirect_history":
                        denied.history = [Mock(status_code=302)]
                    elif invalid == "redirect_status":
                        denied.status_code = 302
                    elif invalid == "scheme":
                        denied.url = url.replace("https:", "http:")
                    elif invalid == "host":
                        denied.url = "https://example.invalid/denied.pdf"
                    else:
                        denied.url = url.replace("https://", "https://user:private@")
                    error = requests.exceptions.HTTPError(response=denied)
                    client = Mock()
                    client.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                        error, api_response("Arbeitsmarkt", period="M09") if origin == "bls"
                        else pdf_response(url=self.URL)]
                    diagnostics = {}
                    with patch("official_bls._pdf_text", return_value=self.TEXT), \
                         self.assertRaisesRegex(BlsInvalid, "BLS_PDF_REDIRECT_OR_URL_INVALID"):
                        fetch_release_state("Arbeitsmarkt", session=client,
                                            now=self.DUE + timedelta(days=1), diagnostics=diagnostics)
                    self.assertEqual(client.get.call_count, 2 if origin == "bls" else 1)
                    self.assertTrue(diagnostics["pdf_attempted"])
                    self.assertNotIn("api_attempted", diagnostics)

    def test_raised_denial_with_invalid_http_status_is_not_a_transport_outage(self):
        for origin in ("dol", "bls"):
            for status in (None, "403", True):
                with self.subTest(origin=origin, status=status):
                    denied = pdf_response(403, url=(self.URL if origin == "bls"
                                                   else _pdf_url("Arbeitsmarkt", self.DUE)))
                    denied.status_code = status
                    client = Mock()
                    client.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                        requests.exceptions.HTTPError(response=denied), pdf_response(url=self.URL)]
                    with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_HTTP_STATUS_INVALID"):
                        fetch_release_state("Arbeitsmarkt", session=client,
                                            now=self.DUE + timedelta(days=1))
                    self.assertEqual(client.get.call_count, 2 if origin == "bls" else 1)

    def test_safe_raised_http_denial_preserves_bounded_fallback_and_attempts(self):
        for origin in ("dol", "bls"):
            with self.subTest(origin=origin):
                denied = pdf_response(403, url=(self.URL if origin == "bls"
                                               else _pdf_url("Arbeitsmarkt", self.DUE)))
                client = Mock()
                client.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                    requests.exceptions.HTTPError(response=denied),
                    api_response("Arbeitsmarkt", period="M09") if origin == "bls"
                    else pdf_response(url=self.URL)]
                diagnostics = {}
                with patch("official_bls._pdf_text", return_value=self.TEXT):
                    state = fetch_release_state("Arbeitsmarkt", session=client,
                                                now=self.DUE + timedelta(days=1), diagnostics=diagnostics)
                self.assertEqual(state["proof_source"], "BLS_API_V1" if origin == "bls" else "BLS_PDF")
                self.assertEqual(client.get.call_count, 3 if origin == "bls" else 2)
                self.assertTrue(diagnostics["pdf_attempted"])
                self.assertTrue(diagnostics["bls_pdf_attempted"])
                self.assertEqual(diagnostics.get("api_attempted", False), origin == "bls")

    def test_collector_tls_failure_keeps_attempt_accounting_and_never_falls_back(self):
        for origin in ("dol", "bls"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as directory:
                host = "www.bls.gov" if origin == "bls" else "www.dol.gov"
                denied = pdf_response(403, url=(self.URL if origin == "bls"
                                               else _pdf_url("Arbeitsmarkt", self.DUE)))
                underlying = Mock()
                underlying.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                    requests.exceptions.SSLError("TLS_FAILURE", response=denied)]
                transport = CollectorTransport(client=underlying, clock=lambda: self.CURRENT,
                                               status_path=Path(directory) / "status.json")
                diagnostics = {}
                with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), self.assertRaises(requests.RequestException):
                    fetch_release_state("Arbeitsmarkt", session=transport,
                                        now=self.CURRENT, diagnostics=diagnostics)
                self.assertEqual(underlying.get.call_count, 2 if origin == "bls" else 1)
                self.assertEqual(transport.usage[host]["requests_this_run"], 1)
                self.assertEqual(transport.usage[host]["outcomes_this_run"]["TLS_ERROR"], 1)
                self.assertTrue(diagnostics["pdf_attempted"])
                self.assertEqual(diagnostics.get("bls_pdf_attempted", False), origin == "bls")
                self.assertNotIn("api_attempted", diagnostics)

    def test_tls_failure_cannot_masquerade_as_a_confirmed_provider_cooldown(self):
        for origin in ("dol", "bls"):
            with self.subTest(origin=origin):
                host = "www.bls.gov" if origin == "bls" else "www.dol.gov"
                denied = pdf_response(403, url=(self.URL if origin == "bls"
                                               else _pdf_url("Arbeitsmarkt", self.DUE)))
                client = Mock(cooldown=set(), usage={})
                def response(url, **kwargs):
                    target = "www.bls.gov" if url == self.URL else "www.dol.gov"
                    client.usage[target] = {"requests_this_run": 1,
                                            "outcomes_this_run": {"HTTP_403": 1}}
                    if target == host:
                        client.cooldown.add(host)
                        raise requests.exceptions.SSLError("PROVIDER_COOLDOWN", response=denied)
                    return pdf_response(403, url=url)
                client.get.side_effect = response
                with self.assertRaises(requests.exceptions.SSLError):
                    fetch_release_state("Arbeitsmarkt", session=client,
                                        now=self.DUE + timedelta(days=1))
                self.assertEqual(client.get.call_count, 2 if origin == "bls" else 1)

    def test_collector_transient_failures_preserve_bounded_fallback_and_attempts(self):
        for origin in ("dol", "bls"):
            for transient in (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                with self.subTest(origin=origin, transient=transient.__name__), \
                     tempfile.TemporaryDirectory() as directory:
                    at = self.DUE + timedelta(days=1)
                    underlying = Mock()
                    underlying.get.side_effect = ([pdf_response(403)] if origin == "bls" else []) + [
                        transient(), api_response("Arbeitsmarkt", period="M09") if origin == "bls"
                        else pdf_response(url=self.URL)]
                    transport = CollectorTransport(client=underlying, clock=lambda: at,
                                                   status_path=Path(directory) / "status.json")
                    diagnostics = {}
                    with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), \
                         patch("official_bls._pdf_text", return_value=self.TEXT):
                        state = fetch_release_state("Arbeitsmarkt", session=transport,
                                                    now=at, diagnostics=diagnostics)
                    self.assertEqual(state["proof_source"], "BLS_API_V1" if origin == "bls" else "BLS_PDF")
                    self.assertEqual(underlying.get.call_count, 3 if origin == "bls" else 2)
                    self.assertEqual(transport.usage["www.dol.gov"]["requests_this_run"], 1)
                    self.assertEqual(transport.usage["www.bls.gov"]["requests_this_run"], 1)
                    self.assertTrue(diagnostics["pdf_attempted"])
                    self.assertTrue(diagnostics["bls_pdf_attempted"])
                    self.assertEqual(diagnostics.get("api_attempted", False), origin == "bls")

    def test_two_pdf_denials_keep_exact_api_24h_daily_and_budget_boundaries(self):
        for status in (403, 429):
            for elapsed, expected in ((timedelta(hours=24, microseconds=-1), "BLS_API_WAIT_24H"),
                                      (timedelta(hours=24), None)):
                with self.subTest(status=status, elapsed=elapsed):
                    client = Mock()
                    client.get.side_effect = [pdf_response(status), pdf_response(status),
                                             api_response("Arbeitsmarkt", period="M09", value="4.2")]
                    at = self.DUE + elapsed
                    if expected:
                        with self.assertRaisesRegex(BlsInvalid, expected):
                            fetch_release_state("Arbeitsmarkt", session=client, now=at)
                        self.assertEqual(client.get.call_count, 2)
                    else:
                        state = fetch_release_state("Arbeitsmarkt", session=client, now=at)
                        self.assertEqual(state["proof_source"], "BLS_API_V1")
                        self.assertEqual(state["first_observed_at"], at.isoformat())
                        self.assertEqual(client.get.call_count, 3)
                        self.assertEqual(client.get.call_args.args[0], _api_url("Arbeitsmarkt"))
        for options, expected in (({"last_api_attempt": self.DUE + timedelta(days=1)}, "BLS_API_DAILY_LIMIT"),
                                  ({"api_budget_blocked": True}, "BLS_API_ATTEMPT_STATE_INVALID")):
            client = Mock(get=Mock(return_value=pdf_response(403)))
            with self.assertRaisesRegex(BlsInvalid, expected):
                fetch_release_state("Arbeitsmarkt", session=client,
                                    now=self.DUE + timedelta(days=1, hours=1), **options)
            self.assertEqual(client.get.call_count, 2)

    def test_outer_hourly_pdf_cooldown_covers_the_alternate_attempt(self):
        client = Mock(get=Mock(return_value=pdf_response(403)))
        diagnostics = {}
        with self.assertRaisesRegex(BlsInvalid, "BLS_PDF_RECHECK_COOLDOWN"):
            fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT,
                                last_pdf_attempt=self.CURRENT - timedelta(hours=1, microseconds=-1),
                                diagnostics=diagnostics)
        client.get.assert_not_called()
        self.assertNotIn("pdf_attempted", diagnostics)
        with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
            fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT,
                                last_pdf_attempt=self.CURRENT - timedelta(hours=1))
        self.assertEqual(client.get.call_count, 2)

    def test_verified_alternate_cache_has_no_redundant_get_and_expires_at_next_release(self):
        previous = {"period": "2026-09", "embargo_ends_at": self.DUE.isoformat(),
                    "next_due_at": "2026-11-06T13:30:00+00:00", "release_url": self.URL,
                    "proof_source": "BLS_PDF"}
        client = Mock()
        self.assertEqual(fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT,
                                            previous_state=previous), previous)
        client.get.assert_not_called()
        next_due = datetime.fromisoformat(previous["next_due_at"])
        client.get.return_value = pdf_response(url=_pdf_url("Arbeitsmarkt", next_due))
        current = bulletin("Arbeitsmarkt", period="October 2026", embargo="November 6, 2026",
                           successor="November 2026", due="December 4, 2026")
        with patch("official_bls._pdf_text", return_value=current):
            state = fetch_release_state("Arbeitsmarkt", session=client, now=next_due,
                                        previous_state=previous)
        self.assertEqual(state["period"], "2026-10")
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.args[0], _pdf_url("Arbeitsmarkt", next_due))

    def test_unknown_factor_url_or_proof_does_not_enable_a_source(self):
        previous = {**states()["Arbeitsmarkt"], "release_url": self.URL, "proof_source": "BLS_PDF"}
        self.assertIsNotNone(_verified_previous("Arbeitsmarkt", previous))
        for changes in ({"release_url": self.URL + "?v=1"}, {"release_url": _api_url("Arbeitsmarkt")},
                        {"proof_source": "NEW_PDF"}, {"proof_source": None}):
            self.assertIsNone(_verified_previous("Arbeitsmarkt", {**previous, **changes}))
        self.assertIsNone(_verified_previous("Inflation", {**states()["Inflation"],
                                                          "release_url": self.URL, "proof_source": "BLS_PDF"}))
        self.assertIsNone(_bls_pdf_url("Inflation"))
        self.assertIsNone(_bls_pdf_url("GDP"))

    def test_bls_cached_proof_requires_both_pinned_release_dates(self):
        for previous in (
                {"period": "2026-05", "embargo_ends_at": "2026-06-05T12:30:00+00:00",
                 "next_due_at": "2099-01-01T00:00:00+00:00"},
                {"period": "2026-11", "embargo_ends_at": "2026-12-04T13:30:00+00:00",
                 "next_due_at": "2099-01-01T00:00:00+00:00"},
                {"period": "2030-01", "embargo_ends_at": "2030-02-01T13:30:00+00:00",
                 "next_due_at": "2030-03-01T13:30:00+00:00"}):
            with self.subTest(period=previous["period"]):
                self.assertIsNone(_verified_previous("Arbeitsmarkt", {
                    **previous, "release_url": self.URL, "proof_source": "BLS_PDF"}))

    def test_unknown_bls_cache_cannot_suppress_primary_reconfirmation(self):
        previous = {"period": "2026-05", "embargo_ends_at": "2026-06-05T12:30:00+00:00",
                    "next_due_at": "2099-01-01T00:00:00+00:00", "release_url": self.URL,
                    "proof_source": "BLS_PDF"}
        client = Mock(get=Mock(return_value=pdf_response(url=_pdf_url("Arbeitsmarkt", self.DUE))))
        with patch("official_bls._pdf_text", return_value=self.TEXT):
            state = fetch_release_state("Arbeitsmarkt", session=client, now=self.CURRENT,
                                        previous_state=previous)
        self.assertEqual(state["period"], "2026-09")
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.args[0], _pdf_url("Arbeitsmarkt", self.DUE))

    def test_collector_host_cooldown_and_retry_after_skip_bls_without_resetting_limits(self):
        for blocked_by in ("cooldown", "retry_after"):
            with self.subTest(blocked_by=blocked_by), tempfile.TemporaryDirectory() as directory:
                at = self.DUE + timedelta(days=1)
                underlying = Mock()
                underlying.get.side_effect = [pdf_response(403),
                                              api_response("Arbeitsmarkt", period="M09", value="4.2")]
                transport = CollectorTransport(client=underlying, clock=lambda: at,
                                               status_path=Path(directory) / "status.json")
                if blocked_by == "cooldown":
                    transport.cooldown.add("www.bls.gov")
                else:
                    transport.retry_after["www.bls.gov"] = at + timedelta(hours=2)
                diagnostics = {}
                with patch.dict(os.environ, {"FX_COLLECTOR": "1"}):
                    state = fetch_release_state("Arbeitsmarkt", session=transport,
                                                now=at, diagnostics=diagnostics)
                self.assertEqual(state["proof_source"], "BLS_API_V1")
                self.assertEqual(diagnostics["bls_pdf_status"], "PROVIDER_COOLDOWN")
                self.assertNotIn("bls_pdf_attempted", diagnostics)
                self.assertEqual([call.args[0] for call in underlying.get.call_args_list],
                                 [_pdf_url("Arbeitsmarkt", self.DUE), _api_url("Arbeitsmarkt")])
                self.assertIn("www.dol.gov", transport.cooldown)
                if blocked_by == "cooldown":
                    self.assertIn("www.bls.gov", transport.cooldown)
                else:
                    self.assertEqual(transport.retry_after["www.bls.gov"], at + timedelta(hours=2))

    def test_dol_retry_after_without_a_confirmed_outage_never_opens_another_source(self):
        with tempfile.TemporaryDirectory() as directory:
            underlying = Mock()
            transport = CollectorTransport(client=underlying, clock=lambda: self.CURRENT,
                                           status_path=Path(directory) / "status.json")
            transport.retry_after["www.dol.gov"] = self.CURRENT + timedelta(hours=1)
            diagnostics = {}
            with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), self.assertRaises(requests.RequestException):
                fetch_release_state("Arbeitsmarkt", session=transport,
                                    now=self.CURRENT, diagnostics=diagnostics)
            underlying.get.assert_not_called()
            self.assertNotIn("pdf_attempted", diagnostics)

    def test_collector_deduplicates_both_pdf_transports_without_repeating_403(self):
        with tempfile.TemporaryDirectory() as directory:
            underlying = Mock()
            underlying.get.side_effect = lambda url, **kwargs: pdf_response(
                403 if url.startswith("https://www.dol.gov/") else 200, url=url)
            transport = CollectorTransport(client=underlying, clock=lambda: self.CURRENT,
                                           status_path=Path(directory) / "status.json")
            with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), \
                 patch("official_bls._pdf_text", return_value=self.TEXT):
                first = fetch_release_state("Arbeitsmarkt", session=transport, now=self.CURRENT)
                diagnostics = {}
                second = fetch_release_state("Arbeitsmarkt", session=transport,
                                             now=self.CURRENT, diagnostics=diagnostics)
            self.assertEqual(first, second)
            self.assertEqual(underlying.get.call_count, 2)
            self.assertEqual(transport.usage["www.dol.gov"]["requests_this_run"], 1)
            self.assertEqual(transport.usage["www.bls.gov"]["requests_this_run"], 1)
            self.assertNotIn("pdf_attempted", diagnostics)

    def test_collector_bls_429_retry_after_is_not_retried_even_after_pdf_hour_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            current = [self.CURRENT]
            underlying = Mock()
            retry = pdf_response(429, url=self.URL)
            retry.headers["Retry-After"] = "7200"
            underlying.get.side_effect = [pdf_response(403), retry]
            transport = CollectorTransport(client=underlying, clock=lambda: current[0],
                                           status_path=Path(directory) / "status.json")
            with patch.dict(os.environ, {"FX_COLLECTOR": "1"}):
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
                    fetch_release_state("Arbeitsmarkt", session=transport, now=current[0])
                current[0] += timedelta(hours=1)
                diagnostics = {}
                with self.assertRaisesRegex(BlsInvalid, "BLS_API_WAIT_24H"):
                    fetch_release_state("Arbeitsmarkt", session=transport, now=current[0],
                                        last_pdf_attempt=self.CURRENT, diagnostics=diagnostics)
            self.assertEqual(underlying.get.call_count, 2)
            self.assertNotIn("bls_pdf_attempted", diagnostics)
            self.assertEqual(transport.retry_after["www.bls.gov"], self.CURRENT + timedelta(hours=2))


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
            if url == _bls_pdf_url("Arbeitsmarkt"):
                return pdf_response(403, url=url)
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
        self.assertIn("abgeschlossene API-Versuche: 1 Faktor", captions)
        self.assertIn("offene Reservierungen heute: Unbekannt", captions)
        self.assertIn("GitHub-Prozessabbruch", captions)


class OperatorRequestStateDisplayTests(unittest.TestCase):
    AT = datetime(2026, 10, 2, 16, 48, tzinfo=timezone.utc)
    DAILY_COLUMNS = {
        "requests_observed_utc_day": "Heute lokal abgeschlossen (UTC)",
        "requests_reserved_utc_day": "Heute lokal reserviert (UTC)",
        "requests_uncertain_utc_day": "Heute unbestätigt (UTC)",
    }

    def dataset(self, **fields):
        return {"model_version": live_data.MODEL, "completed_at": self.AT.isoformat(),
                "currencies": {}, "bls_api_attempts": {}, "bls_api_reservations": {}, **fields}

    def provider(self, **fields):
        return {"status": "HTTP_403", "requests_this_run": 1,
                "counted_day_utc": self.AT.date().isoformat(),
                "requests_observed_utc_day": 1, "requests_reserved_utc_day": 1,
                "requests_uncertain_utc_day": 0, "usage_complete": True,
                "prior_usage_uncertain": False, **fields}

    def render(self, provider=None, data=None, authorized=True):
        st = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "data_collection_status.json").write_text(json.dumps({
                "providers": {"api.bls.gov": self.provider() if provider is None else provider}}))
            with patch.object(live_data, "load", return_value=self.dataset() if data is None else data), \
                 patch.object(live_data, "now_utc", return_value=self.AT), \
                 patch.object(live_data, "selected_live_directory", return_value=folder):
                live_data.render_status(st, authorized=authorized)
        return st

    def row(self, **fields):
        return self.render(provider=self.provider(**fields)).dataframe.call_args_list[-1].args[0][0]

    def captions(self, **kwargs):
        return " ".join(str(call.args[0]) for call in self.render(**kwargs).caption.call_args_list)

    def test_returned_http_error_is_local_completion_without_quota_confirmation(self):
        st = self.render()
        row = st.dataframe.call_args_list[-1].args[0][0]
        self.assertEqual(row["Letzter Abrufstatus"], "HTTP_403")
        self.assertEqual(row["Heute lokal abgeschlossen (UTC)"], 1)
        self.assertEqual(row["Heute lokal reserviert (UTC)"], 1)
        self.assertEqual(row["Heute unbestätigt (UTC)"], 0)
        self.assertEqual(row["Lokale Zählervollständigkeit"], "Vollständig (lokal)")
        self.assertEqual(row["Restkontingent"], "Unbekannt")
        self.assertEqual(row["Rücksetzung"], "Unbekannt")
        self.assertEqual(row["Limit"], "25 Anfragen/Tag (BLS API v1 ohne Registrierung)")
        captions = " ".join(str(call.args[0]) for call in st.caption.call_args_list)
        self.assertIn("kumulativ", captions)
        self.assertIn("gespeichertes Ende eines Abrufversuchs, auch bei Fehlern", captions)
        self.assertIn("kein erfolgreicher Datenabruf und kein bestätigter Verbrauch beim Anbieter", captions)

    def test_interrupted_reservation_remains_visible_and_incomplete(self):
        row = self.row(requests_observed_utc_day=0, requests_reserved_utc_day=1,
                       requests_uncertain_utc_day=1, usage_complete=False)
        self.assertEqual(row["Heute lokal abgeschlossen (UTC)"], 0)
        self.assertEqual(row["Heute lokal reserviert (UTC)"], 1)
        self.assertEqual(row["Heute unbestätigt (UTC)"], 1)
        self.assertEqual(row["Lokale Zählervollständigkeit"], "Unvollständig")
        self.assertEqual(row["Restkontingent"], "Unbekannt")

    def test_valid_current_zero_is_distinct_from_unknown(self):
        row = self.row(requests_observed_utc_day=0, requests_reserved_utc_day=0)
        for column in self.DAILY_COLUMNS.values():
            self.assertEqual(row[column], 0)
        self.assertEqual(row["Lokale Zählervollständigkeit"], "Vollständig (lokal)")

    def test_missing_malformed_old_or_future_day_never_becomes_current_zero(self):
        days = (None, "not-a-day", "2026-02-30",
                (self.AT - timedelta(days=1)).date().isoformat(),
                (self.AT + timedelta(days=1)).date().isoformat())
        for day in days:
            with self.subTest(day=day):
                row = self.row(counted_day_utc=day, requests_observed_utc_day=0,
                               requests_reserved_utc_day=0)
                for column in self.DAILY_COLUMNS.values():
                    self.assertEqual(row[column], "Unbekannt")
                self.assertEqual(row["Lokale Zählervollständigkeit"], "Unbekannt")
                self.assertEqual(row["Frühere Nutzung unbestätigt"], "Unbekannt")

    def test_daily_counts_require_nonnegative_integers(self):
        for field, column in self.DAILY_COLUMNS.items():
            for value in (None, "0", 0.0, False, -1):
                with self.subTest(field=field, value=value):
                    row = self.row(**{field: value})
                    self.assertEqual(row[column], "Unbekannt")
                    self.assertEqual(row["Lokale Zählervollständigkeit"], "Unbekannt")

    def test_legacy_missing_fields_and_nonboolean_flags_do_not_claim_complete(self):
        legacy = self.provider()
        for field in ("requests_reserved_utc_day", "requests_uncertain_utc_day", "usage_complete", "prior_usage_uncertain"):
            legacy.pop(field)
        row = self.render(provider=legacy).dataframe.call_args_list[-1].args[0][0]
        self.assertEqual(row["Heute lokal abgeschlossen (UTC)"], 1)
        self.assertEqual(row["Heute lokal reserviert (UTC)"], "Unbekannt")
        self.assertEqual(row["Heute unbestätigt (UTC)"], "Unbekannt")
        self.assertEqual(row["Lokale Zählervollständigkeit"], "Unbekannt")
        for field in ("usage_complete", "prior_usage_uncertain"):
            for value in (None, "true", 1):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.row(**{field: value})["Lokale Zählervollständigkeit"], "Unbekannt")

    def test_malformed_provider_entry_has_unknown_daily_counts(self):
        for entry in ("INVALID", [], 1):
            with self.subTest(entry=entry):
                row = self.render(provider=entry).dataframe.call_args_list[-1].args[0][0]
                for column in self.DAILY_COLUMNS.values():
                    self.assertEqual(row[column], "Unbekannt")
                self.assertEqual(row["Lokale Zählervollständigkeit"], "Unbekannt")

    def test_explicit_complete_cannot_override_inconsistent_counters(self):
        conflicts = ({"requests_observed_utc_day": 2, "requests_reserved_utc_day": 1},
                     {"requests_observed_utc_day": 0, "requests_reserved_utc_day": 1, "requests_uncertain_utc_day": 2},
                     {"requests_reserved_utc_day": 3},
                     {"prior_usage_uncertain": True})
        for fields in conflicts:
            with self.subTest(fields=fields):
                self.assertEqual(self.row(**fields)["Lokale Zählervollständigkeit"], "Unvollständig")

    def test_prior_uncertain_usage_survives_current_known_zero(self):
        row = self.row(requests_observed_utc_day=0, requests_reserved_utc_day=0,
                       prior_usage_uncertain=True, usage_complete=False)
        self.assertEqual(row["Heute unbestätigt (UTC)"], 0)
        self.assertEqual(row["Frühere Nutzung unbestätigt"], "Ja")
        self.assertEqual(row["Lokale Zählervollständigkeit"], "Unvollständig")

    def test_bls_union_counts_same_factor_once_and_keeps_old_reservations_visible(self):
        captions = self.captions(data=self.dataset(
            bls_api_attempts={"Arbeitsmarkt": self.AT.isoformat()},
            bls_api_reservations={"Arbeitsmarkt": self.AT.isoformat(),
                                  "Inflation": (self.AT - timedelta(days=1)).isoformat()}))
        self.assertIn("abgeschlossene API-Versuche: 1 Faktor", captions)
        self.assertIn("offene Reservierungen heute: 1 Faktor", captions)
        self.assertIn("Faktoren mit Versuch oder Reservierung: 1/2 Faktoren (jeder Faktor einmal)", captions)
        self.assertIn("Ältere offene Reservierungen: 1 Faktor (Inflation)", captions)
        self.assertIn("insgesamt höchstens zwei", captions)
        self.assertIn("unabhängig vom Abschluss eines Collector-Laufs", captions)
        self.assertIn("keine Übernahme nach einem GitHub-Prozessabbruch", captions)

    def test_bls_invalid_and_future_marks_are_unknown_not_current_attempts(self):
        captions = self.captions(data=self.dataset(
            bls_api_attempts={"Arbeitsmarkt": self.AT.isoformat(), "Inflation": "INVALID"},
            bls_api_reservations={"Inflation": (self.AT + timedelta(minutes=1)).isoformat()}))
        self.assertIn("abgeschlossene API-Versuche: Unbekannt (1 Faktor bestätigt)", captions)
        self.assertIn("offene Reservierungen heute: Unbekannt", captions)
        self.assertIn("Faktoren mit Versuch oder Reservierung: Unbekannt (1/2 Faktoren bestätigt)", captions)
        self.assertIn("Inflation (Versuch: ungültig)", captions)
        self.assertIn("Inflation (Reservierung: zukünftig)", captions)
        captions = self.captions(data=self.dataset(
            bls_api_attempts={"Inflation": "0001-01-01T00:00:00+14:00"}))
        self.assertIn("abgeschlossene API-Versuche: Unbekannt", captions)
        self.assertIn("Inflation (Versuch: ungültig)", captions)

    def test_bls_missing_or_malformed_fields_never_claim_known_zero(self):
        for field in ("bls_api_attempts", "bls_api_reservations"):
            for value in (None, "INVALID", []):
                with self.subTest(field=field, value=value):
                    captions = self.captions(data=self.dataset(**{field: value}))
                    self.assertIn("Faktoren mit Versuch oder Reservierung: Unbekannt", captions)
                    self.assertIn("Feld fehlt oder ist ungültig", captions)
        captions = self.captions(data={"completed_at": self.AT.isoformat(), "currencies": {}})
        self.assertIn("abgeschlossene API-Versuche: Unbekannt", captions)
        self.assertIn("offene Reservierungen heute: Unbekannt", captions)

    def test_bls_timestamp_uses_utc_day_and_preserves_known_empty_state(self):
        local = self.AT.astimezone(timezone(timedelta(hours=12))).isoformat()
        captions = self.captions(data=self.dataset(bls_api_attempts={"Inflation": local}))
        self.assertIn("abgeschlossene API-Versuche: 1 Faktor", captions)
        self.assertIn("offene Reservierungen heute: 0 Faktoren", captions)
        self.assertIn("Faktoren mit Versuch oder Reservierung: 1/2 Faktoren", captions)
        self.assertIn("Unklare Marken: Keine dokumentiert", captions)

    def test_operator_request_details_remain_authorized_only(self):
        st = self.render(authorized=False)
        expanders = [call.args[0] for call in st.expander.call_args_list]
        self.assertNotIn("Anbieter und Anfragebudget", expanders)
        captions = " ".join(str(call.args[0]) for call in st.caption.call_args_list)
        self.assertNotIn("Anbieter-Reservierungen", captions)
        self.assertNotIn("GitHub-Prozessabbruch", captions)


class DurableBlsCollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "live.json"
        self.status_path = Path(self.directory.name) / "status.json"
        self.at = NOW
        checked = (NOW - timedelta(hours=2)).isoformat()
        self.previous = {"model_version": live_data.MODEL, "completed_at": checked,
                         "last_attempt_at": checked, "currencies": {"USD": {
                             "Arbeitsmarkt": {"factor": "Arbeitsmarkt", "validation": "VALID", "score": 0,
                                 "checked_at": checked, "observation": {"value": 4.1, "date": "2026-08-01"}}}}}
        live_data.save(self.previous, self.path)
        self.client = Mock()
        self.client.get.side_effect = self.response
        self.abort_at = None
        self.inspect_api = None

    def response(self, url, **kwargs):
        if self.abort_at == "pdf" and url.startswith("https://www.dol.gov/"):
            self.assert_preserved_batch()
            saved = live_data.load(self.path)
            self.assertEqual(saved["bls_pdf_reservations"]["Arbeitsmarkt"], self.at.isoformat())
            self.assertNotIn("Arbeitsmarkt", saved["bls_pdf_attempts"])
            raise KeyboardInterrupt()
        if url.startswith("https://www.dol.gov/") or url == _bls_pdf_url("Arbeitsmarkt"):
            return pdf_response(403, url=url)
        if url == _api_url("Arbeitsmarkt"):
            self.assert_preserved_batch()
            saved = live_data.load(self.path)
            self.assertEqual(saved["bls_api_reservations"]["Arbeitsmarkt"], self.at.isoformat())
            self.assertNotEqual(saved["bls_api_attempts"].get("Arbeitsmarkt"), self.at.isoformat())
            self.assertEqual(saved["bls_pdf_attempts"]["Arbeitsmarkt"], self.at.isoformat())
            if self.inspect_api:
                self.inspect_api(saved)
            if self.abort_at == "api":
                raise KeyboardInterrupt()
            return api_response("Arbeitsmarkt")
        response = Mock(status_code=200)
        response.json.return_value = {}
        return response

    def assert_preserved_batch(self):
        saved = live_data.load(self.path)
        for field, value in self.previous.items():
            self.assertEqual(saved[field], value)
        self.assertNotIn("bls_release_states", saved)

    def collect(self, *, abort_after_reports=False, valid=False, durable=True):
        transport = CollectorTransport(self.client, status_path=self.status_path,
                                       clock=lambda: self.at, durable=durable)
        app = Mock(FRED_KEY="test-only", requests=transport)
        if abort_after_reports:
            app.compute_currency_details.side_effect = KeyboardInterrupt()
        elif valid:
            app.compute_currency_details.return_value = {
                "Arbeitsmarkt": 0, "_freshness": {"Arbeitsmarkt": "FRESH"},
                "_observations": {"Arbeitsmarkt": {"value": 4.1, "date": "2026-08-01",
                    "series_id": "UNRATE", "source": "FRED", "frequency": "monthly"}}}
        else:
            app.compute_currency_details.return_value = {}
        with patch.dict(os.environ, {"FX_COLLECTOR": "1"}), \
             patch.object(live_data, "CURRENCIES", ("USD",)), \
             patch.object(live_data, "FACTORS", {"Arbeitsmarkt": 20}), \
             patch.object(live_data, "now_utc", side_effect=lambda: self.at), \
             patch("source_contracts.validate_fred_metadata", return_value=True):
            return live_data.collect(app, self.path)

    def test_pdf_kill_retains_open_reservation_and_hourly_gate(self):
        self.abort_at = "pdf"
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assert_preserved_batch()
        saved = live_data.load(self.path)
        self.assertEqual(saved["bls_pdf_reservations"]["Arbeitsmarkt"], NOW.isoformat())
        usage = json.loads(self.status_path.read_text())["providers"]["www.dol.gov"]
        self.assertEqual(usage["requests_uncertain_utc_day"], 1)
        self.assertEqual(usage["requests_observed_utc_day"], 0)
        self.client.get.reset_mock(); self.abort_at = None
        self.at += timedelta(minutes=31)
        self.collect()
        self.client.get.assert_not_called()
        saved = live_data.load(self.path)
        self.assertEqual(saved["bls_pdf_reservations"]["Arbeitsmarkt"], NOW.isoformat())

    def test_api_kill_and_post_report_abort_both_retain_daily_budget(self):
        for inside_api in (True, False):
            with self.subTest(inside_api=inside_api):
                self.at = NOW; self.abort_at = "api" if inside_api else None
                self.status_path.unlink(missing_ok=True)
                live_data.save(self.previous, self.path)
                with self.assertRaises(KeyboardInterrupt):
                    self.collect(abort_after_reports=not inside_api)
                self.assert_preserved_batch()
                saved = live_data.load(self.path)
                field = "bls_api_reservations" if inside_api else "bls_api_attempts"
                self.assertEqual(saved[field]["Arbeitsmarkt"], NOW.isoformat())
                other = "bls_api_attempts" if inside_api else "bls_api_reservations"
                self.assertNotIn("Arbeitsmarkt", saved[other])
                # A full hour permits PDF checking, but neither path can spend
                # another API request for this factor on the same UTC day.
                self.client.get.reset_mock(); self.abort_at = None
                self.at = NOW + timedelta(minutes=61)
                self.collect()
                self.assertEqual(self.client.get.call_count, 2)
                self.assertFalse(any(call.args[0] == _api_url("Arbeitsmarkt")
                                     for call in self.client.get.call_args_list))
                self.assertEqual(live_data.load(self.path)[field]["Arbeitsmarkt"], NOW.isoformat())

    def test_successful_durable_api_proof_preserves_zero_and_requires_fred_match(self):
        result = self.collect(valid=True)
        saved = live_data.load(self.path)
        row = saved["currencies"]["USD"]["Arbeitsmarkt"]
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(row["score"], 0)
        self.assertEqual(row["observation"]["value"], 4.1)
        self.assertEqual(saved["bls_release_states"]["Arbeitsmarkt"]["fred_raw_value"], "4.1")
        self.assertEqual(saved["bls_api_attempts"]["Arbeitsmarkt"], NOW.isoformat())
        self.assertEqual(saved["bls_api_reservations"], {})
        self.assertEqual(saved["bls_pdf_reservations"], {})
        self.assertTrue(live_data.eligible(row, NOW, factor="Arbeitsmarkt", currency="USD")[0])
        self.client.get.reset_mock()
        self.collect(valid=True)
        self.client.get.assert_not_called()

    def test_hour_boundary_and_next_utc_day_allow_only_the_existing_budgets(self):
        self.abort_at = "pdf"
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.abort_at = None; self.client.get.reset_mock()
        self.at = NOW + timedelta(hours=1)
        with self.assertRaises(KeyboardInterrupt):
            self.collect(abort_after_reports=True)
        self.assertEqual(self.client.get.call_count, 3)
        self.assertEqual(live_data.load(self.path)["bls_api_attempts"]["Arbeitsmarkt"], self.at.isoformat())
        # The pending/observed previous day's API budget does not consume the
        # next UTC day's single allowance. Publication's 24-hour rule remains.
        self.client.get.reset_mock(); self.at = NOW + timedelta(days=1)
        with self.assertRaises(KeyboardInterrupt):
            self.collect(abort_after_reports=True)
        self.assertEqual(self.client.get.call_count, 3)
        saved = live_data.load(self.path)
        self.assertEqual(saved["bls_api_attempts"]["Arbeitsmarkt"], self.at.isoformat())
        self.assertEqual(saved["bls_api_reservations"], {})

    def test_24_hour_wait_does_not_reserve_an_api_request(self):
        self.at = datetime.fromisoformat(PINNED_DUES["Arbeitsmarkt"]["2026-09"]) + timedelta(hours=1)
        self.collect()
        self.assertEqual(self.client.get.call_count, 2)
        saved = live_data.load(self.path)
        self.assertEqual(saved["bls_api_attempts"], {})
        self.assertEqual(saved["bls_api_reservations"], {})
        self.assertEqual(saved["bls_provider_status"]["Arbeitsmarkt"]["proof"], "BLS_API_WAIT_24H")

    def test_failed_bls_reservation_save_prevents_http_and_leaves_budget_unused(self):
        original = live_data.save
        count = [0]
        def save(data, path=live_data.PATH):
            count[0] += 1
            if count[0] == 1:
                raise OSError("private-path")
            return original(data, path)
        with patch.object(live_data, "save", side_effect=save):
            self.collect()
        self.client.get.assert_not_called()
        self.assertFalse(self.status_path.exists())
        saved = live_data.load(self.path)
        for field in live_data.BLS_ATTEMPT_FIELDS:
            self.assertEqual(saved[field], {})
        self.assertEqual(saved["currencies"]["USD"]["Arbeitsmarkt"]["validation"], "UNVERIFIED")

    def test_newer_repository_seed_cannot_erase_real_daily_or_open_pdf_attempt(self):
        self.abort_at = "api"
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        from run_data_collection import _seed_fallback_live
        remote = copy.deepcopy(self.previous)
        remote["completed_at"] = (NOW + timedelta(minutes=5)).isoformat()
        remote["currencies"]["USD"]["Arbeitsmarkt"]["score"] = 90
        remote["bls_pdf_attempts"] = {"Arbeitsmarkt": (NOW - timedelta(minutes=50)).isoformat()}
        source = Path(self.directory.name) / "repository.json"
        live_data.save(remote, source)
        _seed_fallback_live(source, self.path)
        seeded = live_data.load(self.path)
        self.assertEqual(seeded["currencies"], remote["currencies"])
        self.assertEqual(seeded["completed_at"], remote["completed_at"])
        self.assertEqual(seeded["bls_pdf_attempts"]["Arbeitsmarkt"], NOW.isoformat())
        self.assertEqual(seeded["bls_api_reservations"]["Arbeitsmarkt"], NOW.isoformat())
        self.at += timedelta(minutes=36); self.abort_at = None; self.client.get.reset_mock()
        self.collect()
        self.client.get.assert_not_called()

    def test_invalid_or_future_pdf_reservation_never_grants_an_attempt(self):
        for value in ("invalid", None, (NOW + timedelta(days=1)).isoformat()):
            with self.subTest(value=value):
                previous = copy.deepcopy(self.previous)
                previous["bls_pdf_reservations"] = {"Arbeitsmarkt": value}
                live_data.save(previous, self.path)
                self.client.get.reset_mock()
                self.collect()
                self.client.get.assert_not_called()
                saved = live_data.load(self.path)
                self.assertEqual(saved["bls_provider_status"]["Arbeitsmarkt"]["proof"],
                                 "BLS_PDF_ATTEMPT_STATE_INVALID")

    def test_non_durable_offline_transport_does_not_write_operational_file(self):
        # The ordinary test/default transport is still read-only. A final live
        # cache save here would also be inside the explicit temporary path.
        self.client.get.side_effect = lambda url, **kwargs: (
            api_response("Arbeitsmarkt") if url == _api_url("Arbeitsmarkt") else pdf_response(403, url=url))
        with self.assertRaises(KeyboardInterrupt):
            self.collect(abort_after_reports=True, durable=False)
        self.assertEqual(live_data.load(self.path), self.previous)
        self.assertFalse(self.status_path.exists())


if __name__ == "__main__":
    unittest.main()
