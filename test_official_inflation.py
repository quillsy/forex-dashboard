import unittest
from unittest.mock import Mock

import requests

from official_inflation import fetch_official_cpi, parse_abs_cpi, parse_abs_release_index, parse_estat_cpi, parse_japan_cpi_release, parse_japan_cpi_calendar

NOW = "2026-09-07T12:00:00+00:00"


def estat():
    classes = [("tab", "3", "前年同月比"), ("cat01", "0001", "0001 総合"), ("area", "00000", "全国"), ("time", "2026000707", "2026年7月")]
    return {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {
        "TABLE_INF": {"@id": "0004052037", "STAT_NAME": {"@code": "00200573"}},
        "CLASS_INF": {"CLASS_OBJ": [{"@id": id_, "CLASS": {"@code": code, "@name": name, "@unit": "%"}}
                                   for id_, code, name in classes]},
        "DATA_INF": {"VALUE": [{"@tab": "3", "@cat01": "0001", "@area": "00000",
                                "@unit": "%", "@time": "2026000707", "$": "1.9"}]}}}}



def japan_release(month=7, value="1.9", direction="上昇", date="2026年8月21日"):
    # Documentation-derived synthetic fixtures, not captured provider JSON.
    return f'<section id="section"><h1>2025年基準 消費者物価指数 全国 2026年（令和8年）{month}月分（{date}公表）</h1><p><strong>総合指数</strong>は2025年を100として102.2<br>前年同月比は{value}%の{direction}<br><strong>生鮮食品を除く総合指数</strong>前年同月比は9.9%の上昇</p></section>'


def japan_calendar(future=True):
    return '<section id="section"><table class="datatable"><tr><th colspan="2">Japan</th><th colspan="2">Ku-area of Tokyo (preliminary)</th><th>Remarks</th></tr><tr><th>Survey month</th><th>Date of release</th><th>Survey month</th><th>Date of release</th></tr><tr><td>July, 2026</td><td>August 21, 2026</td><td>August</td><td>August 28</td><td></td></tr>' + ('<tr><td>August</td><td>September 18</td><td>September</td><td>October 2</td><td></td></tr>' if future else '') + '</table></section>'


def japan_client(payload=None):
    client = Mock()
    api = Mock(); api.json.return_value = estat() if payload is None else payload
    release = Mock(); release.text = japan_release()
    calendar = Mock(); calendar.text = japan_calendar()
    client.get.side_effect = [api, release, calendar]
    return client

def abs_data():
    dims = [("MEASURE", "3", "Percentage change from previous year"), ("INDEX", "10001", "All groups CPI"),
            ("TSEST", "10", "Original"), ("REGION", "50", "Australia"), ("FREQ", "M", "Monthly")]
    return {"dataSets": [{"series": {"0:0:0:0:0": {"attributes": [0], "observations": {"0": [3.5], "1": [3.8]}}}}],
        "structure": {"dimensions": {"series": [{"id": id_, "values": [{"id": code, "name": name}]}
                                                for id_, code, name in dims],
            "observation": [{"id": "TIME_PERIOD", "values": [{"id": "2026-07"}, {"id": "2026-06"}]}]},
            "attributes": {"series": [{"id": "UNIT_MEASURE", "values": [{"id": "PCT", "name": "Percent"}]}]}}}


def abs_release_index(latest="July", next_month="August", due="2026-09-30T01:30:00Z", future=True):
    future_row = (f'<div class="views-row">Consumer Price Index, Australia, {next_month} 2026'
                  f'<span>Release date</span><time datetime="{due}">11:30am AEST</time></div>') if future else ''
    return f'''<div id="block-views-block-topic-releases-listing-topic-latest-release-block">
      <div class="views-row"><a>Consumer Price Index, Australia, {latest} 2026</a></div></div>
      <div id="block-views-block-topic-releases-listing-future-releases-block">
      {future_row}</div>'''


def abs_client(page):
    client = Mock()
    api = Mock(); api.json.return_value = abs_data()
    release = Mock(); release.text = page
    client.get.side_effect = [api, release]
    return client


class OfficialInflationTests(unittest.TestCase):
    def test_abs_official_release_deadline_and_current_api_period(self):
        calendar = parse_abs_release_index(abs_release_index())
        self.assertEqual(calendar, {"latest_period": "2026-07", "next_due_at": "2026-09-30T01:30:00+00:00"})
        client = abs_client(abs_release_index()); diagnostics = {}
        row = fetch_official_cpi("AUD", client=client, now="2026-09-27T12:00:00+00:00", diagnostics=diagnostics)
        self.assertEqual(row["value"], 3.5)
        self.assertEqual(row["next_due_at"], "2026-09-30T01:30:00+00:00")
        self.assertNotIn("_validation", row)
        self.assertEqual(diagnostics, {"code": "OK"})
        self.assertEqual(client.get.call_count, 2)

    def test_abs_old_api_period_blocked_after_release(self):
        client = abs_client(abs_release_index("August", "September", "2026-10-28T00:30:00Z"))
        diagnostics = {}
        row = fetch_official_cpi("AUD", client=client, now="2026-10-01T12:00:00+00:00", diagnostics=diagnostics)
        self.assertEqual(row["reference_period"], "2026-07")  # show the observed value with its true period
        self.assertEqual(row["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostics, {"code": "ABS_API_RELEASE_LAG"})
        due_client = abs_client(abs_release_index())
        due = fetch_official_cpi("AUD", client=due_client, now="2026-09-30T01:30:00+00:00", diagnostics=diagnostics)
        self.assertEqual(due["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostics, {"code": "ABS_RELEASE_DUE_UNCONFIRMED"})

    def test_abs_calendar_conflict_or_invalid_page_never_qualifies(self):
        client = abs_client(abs_release_index("June", "July", "2026-08-26T01:30:00Z")); diagnostics = {}
        row = fetch_official_cpi("AUD", client=client, now="2026-09-27T12:00:00+00:00", diagnostics=diagnostics)
        self.assertEqual(row["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostics, {"code": "ABS_RELEASE_CONFLICT"})
        for page in ("<html></html>", abs_release_index(next_month="September"),
                     abs_release_index(due="unknown")):
            with self.subTest(page=page[:30]), self.assertRaisesRegex(ValueError, "ABS_RELEASE_INDEX_INVALID"):
                parse_abs_release_index(page)

    def test_abs_release_page_outage_cannot_be_reported_as_fresh(self):
        client = abs_client(abs_release_index()); diagnostics = {}
        responses = list(client.get.side_effect)
        responses[1].raise_for_status.side_effect = requests.Timeout("private-response")
        client.get.side_effect = responses
        self.assertIsNone(fetch_official_cpi("AUD", client=client, now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "ABS_RELEASE_INDEX_UNAVAILABLE"})

    def test_abs_unannounced_next_release_requires_hourly_check(self):
        client = abs_client(abs_release_index(future=False))
        row = fetch_official_cpi("AUD", client=client, now=NOW)
        self.assertIsNone(row["next_due_at"])
        self.assertTrue(row["needs_hourly_check"])

    def test_correct_latest_and_unknown_publication(self):
        for parse, fixture, value in [(parse_estat_cpi, estat, 1.9), (parse_abs_cpi, abs_data, 3.5)]:
            row = parse(fixture(), NOW)
            self.assertEqual(row["value"], value)
            self.assertEqual(row["refperiod"], "2026-07")
            self.assertIsNone(row["published_at"])

    def test_estat_rejects_old_base_wrong_unit_and_area(self):
        for path, value in [("table", "0003427113"), ("unit", "index"), ("area", "13100")]:
            data = estat(); root = data["GET_STATS_DATA"]["STATISTICAL_DATA"]
            if path == "table": root["TABLE_INF"]["@id"] = value
            if path == "unit": root["CLASS_INF"]["CLASS_OBJ"][0]["CLASS"]["@unit"] = value
            if path == "area": root["DATA_INF"]["VALUE"][0]["@area"] = value
            with self.subTest(path=path), self.assertRaises(ValueError): parse_estat_cpi(data, NOW)

    def test_estat_quarterly_and_future_are_not_monthly(self):
        for period in ["2026000709", "2026000000", "2026000909", "2026001010"]:
            data = estat(); data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["@time"] = period
            with self.assertRaisesRegex(ValueError, "ESTAT_TIME_INVALID"):
                parse_estat_cpi(data, NOW)

    def test_japan_release_sign_and_identity(self):
        for value, direction, expected in [("1.9", "上昇", 1.9), ("0", "横ばい", 0), ("0.5", "下落", -0.5)]:
            self.assertEqual(parse_japan_cpi_release(japan_release(value=value, direction=direction), NOW)["value"], expected)
        for html in [japan_release().replace("全国", "東京都区部"), japan_release(date="2026年10月1日"), japan_release().replace("2025年基準", "2020年基準")]:
            with self.assertRaises(ValueError): parse_japan_cpi_release(html, NOW)

    def test_japan_calendar_date_only_and_unknown_next(self):
        self.assertEqual(parse_japan_cpi_calendar(japan_calendar(), "2026-07")["next_due_date"], "2026-09-18")
        row = fetch_official_cpi("JPY", client=japan_client(), estat_key="test-only", now=NOW)
        self.assertEqual(row["next_due_at"], "2026-09-17T15:00:00+00:00")
        self.assertIsNone(row["published_at"])
        client = japan_client(); responses = list(client.get.side_effect); responses[2].text = japan_calendar(False); client.get.side_effect = responses
        row = fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW)
        self.assertTrue(row["needs_hourly_check"])

    def test_japan_due_conflict_and_parser_short_circuit(self):
        row = fetch_official_cpi("JPY", client=japan_client(), estat_key="test-only", now="2026-09-17T15:00:00Z")
        self.assertEqual(row["_validation"], "UNVERIFIED")
        payload = estat(); payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["$"] = "2.5"
        diagnostic = {}; row = fetch_official_cpi("JPY", client=japan_client(payload), estat_key="test-only", now=NOW, diagnostics=diagnostic)
        self.assertEqual(diagnostic["code"], "ESTAT_RELEASE_CONFLICT")
        for mutation in ("dimension", "duplicate", "membership"):
            payload = estat(); data = payload["GET_STATS_DATA"]["STATISTICAL_DATA"]
            if mutation == "dimension": data["CLASS_INF"]["CLASS_OBJ"].append(data["CLASS_INF"]["CLASS_OBJ"][0])
            if mutation == "duplicate": data["DATA_INF"]["VALUE"].append(data["DATA_INF"]["VALUE"][0].copy())
            if mutation == "membership": data["DATA_INF"]["VALUE"][0]["@time"] = "2026000606"
            client = japan_client(payload)
            self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW))
            self.assertEqual(client.get.call_count, 1)

    def test_japan_missing_latest_lag_and_safe_transport(self):
        payload = estat(); data = payload["GET_STATS_DATA"]["STATISTICAL_DATA"]
        data["CLASS_INF"]["CLASS_OBJ"][-1]["CLASS"] = [{"@code": "2026000606"}, {"@code": "2026000707"}]
        latest = data["DATA_INF"]["VALUE"][0]
        data["DATA_INF"]["VALUE"].insert(0, {**latest, "@time": "2026000606"})
        latest["$"] = "***"
        client = japan_client(payload); diagnostic = {}
        row = fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostic)
        self.assertEqual(row["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostic["code"], "ESTAT_API_RELEASE_LAG")
        self.assertEqual(client.get.call_count, 3)
        data["DATA_INF"]["VALUE"].pop()
        row = fetch_official_cpi("JPY", client=japan_client(payload), estat_key="test-only", now=NOW, diagnostics=diagnostic)
        self.assertEqual(diagnostic["code"], "ESTAT_API_RELEASE_LAG")
        self.assertEqual(row["_validation"], "UNVERIFIED")
        for index, token in [(1, "ESTAT_RELEASE_UNAVAILABLE"), (2, "ESTAT_CALENDAR_UNAVAILABLE")]:
            client = japan_client(); responses = list(client.get.side_effect)
            responses[index].raise_for_status.side_effect = requests.Timeout("secret-test-only")
            client.get.side_effect = responses
            self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostic))
            self.assertEqual(diagnostic, {"code": token, "provider_status": 0})

    def test_unpublished_placeholder_does_not_replace_national_release_gate(self):
        payload = estat(); data = payload["GET_STATS_DATA"]["STATISTICAL_DATA"]
        data["CLASS_INF"]["CLASS_OBJ"][-1]["CLASS"] = [{"@code": "2026000707"}, {"@code": "2026000808"}]
        data["DATA_INF"]["VALUE"].append({**data["DATA_INF"]["VALUE"][0], "@time": "2026000808", "$": "***"})
        diagnostic = {}
        client = japan_client(payload)
        row = fetch_official_cpi("JPY", client=client, estat_key="fixture-only", now=NOW, diagnostics=diagnostic)
        self.assertEqual(row["reference_period"], "2026-07")
        self.assertNotIn("_validation", row)
        self.assertEqual(diagnostic["code"], "OK")
        self.assertEqual(client.get.call_count, 3)
        client = japan_client(payload)
        row = fetch_official_cpi("JPY", client=client, estat_key="fixture-only", now="2026-09-18T00:00:00+00:00", diagnostics=diagnostic)
        self.assertEqual(row["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostic["code"], "ESTAT_DUE_UNCONFIRMED")
        client = japan_client(payload); responses = list(client.get.side_effect)
        responses[1].text = japan_release(month=8, date="2026年9月18日")
        client.get.side_effect = responses
        row = fetch_official_cpi("JPY", client=client, estat_key="fixture-only", now="2026-09-20T00:00:00+00:00", diagnostics=diagnostic)
        self.assertEqual(row["_validation"], "UNVERIFIED")
        self.assertEqual(diagnostic["code"], "ESTAT_API_RELEASE_LAG")

    def test_japan_calendar_malformed_and_year_rollover(self):
        for html in [japan_calendar().replace("September 18", "September 99"), japan_calendar().replace("<td>August</td><td>September 18", "<td>October</td><td>September 18"), japan_calendar().replace("Japan</th>", "Other</th>")]:
            with self.assertRaises(ValueError): parse_japan_cpi_calendar(html, "2026-07")
        html = japan_calendar().replace("July, 2026", "December, 2026").replace("August 21, 2026", "January 22, 2027").replace("<td>August</td><td>September 18", "<td>January, 2027</td><td>February 19")
        self.assertEqual(parse_japan_cpi_calendar(html, "2026-12")["next_due_date"], "2027-02-19")

    def test_japan_transport_classification_at_each_phase(self):
        for phase in range(3):
            for status in (401, 403, 404, 429, 503):
                client = japan_client(); responses = list(client.get.side_effect)
                response = requests.Response(); response.status_code = status
                responses[phase].raise_for_status.side_effect = requests.HTTPError("private-body", response=response)
                client.get.side_effect = responses; diagnostic = {}
                self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostic))
                expected = ["HTTP_ERROR", "ESTAT_RELEASE_UNAVAILABLE", "ESTAT_CALENDAR_UNAVAILABLE"][phase] if status in (429, 503) else "ESTAT_HTTP_INVALID"
                self.assertEqual(diagnostic["code"], expected)
                self.assertNotIn("private", str(diagnostic))

    def test_japan_incomplete_calendar_and_future_numeric_schema(self):
        with self.assertRaisesRegex(ValueError, "ESTAT_CALENDAR_INVALID"):
            parse_japan_cpi_calendar(japan_calendar(), "2026-06")
        for value in (True, "NaN", "99", {}):
            payload = estat(); data = payload["GET_STATS_DATA"]["STATISTICAL_DATA"]
            data["CLASS_INF"]["CLASS_OBJ"][-1]["CLASS"]["@code"] = "2026001010"
            data["DATA_INF"]["VALUE"][0].update({"@time": "2026001010", "$": value})
            self.assertIsNone(fetch_official_cpi("JPY", client=japan_client(payload), estat_key="test-only", now=NOW))

    def test_japan_era_and_completed_month_required(self):
        for html in [japan_release().replace("令和8年", "令和7年"),
                     japan_release().replace("2026年（令和8年）", "2018年（令和0年）"),
                     japan_release(date="2026年7月31日")]:
            with self.assertRaisesRegex(ValueError, "ESTAT_RELEASE_INVALID"):
                parse_japan_cpi_release(html, NOW)
        short = japan_calendar(False).replace("August 21, 2026", "July 31, 2026")
        with self.assertRaisesRegex(ValueError, "ESTAT_CALENDAR_INVALID"):
            parse_japan_cpi_calendar(short, "2026-07")
        self.assertEqual(parse_japan_cpi_release(japan_release(date="2026年8月1日"), NOW)["release_date_known"], "2026-08-01")

    def test_abs_identity_unit_and_quarterly_rejected(self):
        for index in range(5):
            data = abs_data(); data["structure"]["dimensions"]["series"][index]["values"][0]["id"] = "wrong"
            with self.subTest(index=index), self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
        data = abs_data(); data["structure"]["attributes"]["series"][0]["values"][0]["id"] = "IX"
        with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
        data = abs_data(); data["structure"]["dimensions"]["observation"][0]["values"][0]["id"] = "2026-Q2"
        with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)

    def test_abs_future_ignored(self):
        data = abs_data(); data["structure"]["dimensions"]["observation"][0]["values"][0]["id"] = "2026-10"
        self.assertEqual(parse_abs_cpi(data, NOW)["refperiod"], "2026-06")

    def test_abs_multiplier_at_any_attachment_level_cannot_be_a_percent_rate(self):
        for level in ("dataSet", "series", "observation"):
            for multiplier in ("0", "1", "3"):
                with self.subTest(level=level, multiplier=multiplier):
                    data = abs_data()
                    attrs = data["structure"]["attributes"].setdefault(level, [])
                    attrs.append({"id": "UNIT_MULT", "values": [{"id": multiplier}]})
                    # A missing reference still cannot authorize a field outside
                    # the CPI contract. Both bound and unbound forms must fail.
                    for reference in (0, None):
                        series = data["dataSets"][0]["series"]["0:0:0:0:0"]
                        if level == "series": series["attributes"] = [0, reference]
                        elif level == "dataSet": data["dataSets"][0]["attributes"] = [reference]
                        else: series["observations"]["0"] = [3.5, reference]
                        with self.assertRaisesRegex(ValueError, "ABS_UNIT_INVALID"):
                            parse_abs_cpi(data, NOW)

    def test_abs_unit_reference_must_select_one_nonnegative_integer_position(self):
        for reference in (-1, 1, True, False, None, "0", 0.0):
            with self.subTest(reference=reference):
                data = abs_data()
                data["dataSets"][0]["series"]["0:0:0:0:0"]["attributes"] = [reference]
                with self.assertRaisesRegex(ValueError, "ABS_UNIT_INVALID"):
                    parse_abs_cpi(data, NOW)

    def test_abs_additional_unit_on_other_attachment_level_is_not_ignored(self):
        for level in ("dataSet", "observation"):
            for code, name in (("IX", "Index"), ("PCT", "Percent")):
                with self.subTest(level=level, code=code):
                    data = abs_data()
                    data["structure"]["attributes"][level] = [
                        {"id": "UNIT_MEASURE", "values": [{"id": code, "name": name}]}]
                    if level == "dataSet": data["dataSets"][0]["attributes"] = [0]
                    else: data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = [3.5, 0]
                    with self.assertRaisesRegex(ValueError, "ABS_UNIT_INVALID"):
                        parse_abs_cpi(data, NOW)

    def test_abs_unscaled_rates_including_zero_and_deflation_are_preserved(self):
        for rate in (3.5, 0, -1.2):
            data = abs_data()
            data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = [rate]
            self.assertEqual(parse_abs_cpi(data, NOW)["value"], rate)

    def test_abs_unbound_or_malformed_metadata_references_are_rejected(self):
        for level in ("dataSet", "series"):
            for references in ([0, 0], [-1], [True], [1], ["0"], "0"):
                with self.subTest(level=level, references=references):
                    data = abs_data()
                    if level == "dataSet":
                        data['structure']['attributes']['dataSet'] = [
                            {'id': 'BASE_PERIOD', 'values': [{'id': '25'}]}]
                    target = data['dataSets'][0] if level == 'dataSet' else data['dataSets'][0]['series']['0:0:0:0:0']
                    target['attributes'] = references
                    with self.assertRaisesRegex(ValueError, 'ABS_UNIT_INVALID'):
                        parse_abs_cpi(data, NOW)
        data = abs_data()
        data['dataSets'][0]['series']['0:0:0:0:0']['attributes'] = []
        with self.assertRaisesRegex(ValueError, 'ABS_UNIT_INVALID'):
            parse_abs_cpi(data, NOW)

    def test_abs_optional_metadata_can_be_absent_or_unassigned(self):
        for references in ([], [None], [0]):
            data = abs_data()
            data['structure']['attributes']['dataSet'] = [
                {'id': 'BASE_PERIOD', 'values': [{'id': '25'}]}]
            data['dataSets'][0]['attributes'] = references
            self.assertEqual(parse_abs_cpi(data, NOW)['value'], 3.5)

    def test_abs_invalid_unit_stops_before_calendar_and_has_safe_diagnostic(self):
        data = abs_data()
        data["structure"]["attributes"]["observation"] = [
            {"id": "UNIT_MULT", "values": [{"id": "3", "name": "private-provider-text"}]}]
        client = Mock(); client.get.return_value.json.return_value = data
        diagnostic = {}
        self.assertIsNone(fetch_official_cpi("AUD", client=client, now=NOW, diagnostics=diagnostic))
        self.assertEqual(diagnostic, {"code": "ABS_UNIT_INVALID"})
        self.assertEqual(client.get.call_count, 1)

    def test_nonfinite_and_conflicting_values_fail(self):
        for value in [float("nan"), float("inf"), 26]:
            data = abs_data(); data["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = [value]
            with self.assertRaises(ValueError): parse_abs_cpi(data, NOW)
            data = estat(); data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["$"] = value
            with self.assertRaises(ValueError): parse_estat_cpi(data, NOW)
        data = estat(); values = data["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
        values.append({**values[0], "$": "2.1"})
        with self.assertRaises(ValueError): parse_estat_cpi(data, NOW)

    def test_transport_injection_and_no_legacy_fallback(self):
        client = Mock(); client.get.return_value.json.return_value = estat()
        api = client.get.return_value
        release = Mock(); release.text = japan_release()
        calendar = Mock(); calendar.text = japan_calendar()
        client.get.side_effect = lambda url, **kwargs: release if url.endswith("index-z.html") else calendar if url.endswith("1582.html") else api
        result = fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW)
        self.assertEqual(result["value"], 1.9)
        self.assertEqual(result["source_url"],
                         "https://www.e-stat.go.jp/en/stat-search/database?layout=dataset&statdisp_id=0004052037")
        self.assertEqual(client.get.call_args_list[0].kwargs["params"]["statsDataId"], "0004052037")
        client.get.reset_mock(); client.get.return_value.raise_for_status.side_effect = requests.HTTPError()
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW))
        self.assertEqual(client.get.call_count, 1)
        client.get.reset_mock()
        self.assertIsNone(fetch_official_cpi("JPY", client=client, now=NOW)); client.get.assert_not_called()

    def test_abs_validated_result_carries_keyless_source_endpoint(self):
        client = abs_client(abs_release_index())
        result = fetch_official_cpi("AUD", client=client, now=NOW)
        self.assertEqual(result['source_url'], 'https://data.api.abs.gov.au/rest/data/CPI/3.10001.10.50.M')
        self.assertEqual(result['series_id'], 'CPI/3.10001.10.50.M')

    def test_safe_diagnostics_success_api_rejection_and_schema(self):
        client = Mock(); client.get.return_value.json.return_value = estat()
        api = client.get.return_value
        release = Mock(); release.text = japan_release()
        calendar = Mock(); calendar.text = japan_calendar()
        client.get.side_effect = lambda url, **kwargs: release if url.endswith("index-z.html") else calendar if url.endswith("1582.html") else api
        diagnostics = {"old_field": "old"}
        result = fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics)
        self.assertEqual(result["reference_period"], "2026-07")
        self.assertEqual(diagnostics, {"code": "OK", "provider_status": 0})
        payload = estat(); payload["GET_STATS_DATA"]["RESULT"] = {"STATUS": "100", "ERROR_MSG": "private-key-value"}
        client.get.return_value.json.return_value = payload
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "ESTAT_FAILURE", "provider_status": 100})
        payload = estat(); payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["TABLE_INF"]["@id"] = "other"
        client.get.return_value.json.return_value = payload
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "ESTAT_TABLE_INVALID", "provider_status": 0})

    def test_diagnostics_never_include_provider_messages_or_unbounded_status(self):
        client = Mock(); diagnostics = {}
        for status in ["private-key-value", -1, 10000, True, "12345", {"secret": "private-key-value"}]:
            payload = estat(); payload["GET_STATS_DATA"]["RESULT"]["STATUS"] = status
            client.get.return_value.json.return_value = payload
            self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
            self.assertEqual(diagnostics, {"code": "ESTAT_FAILURE"})
        client.get.return_value.json.side_effect = ValueError("https://example.test/?key=private-key-value")
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "INVALID_JSON"})
        client.get.return_value.json.side_effect = None
        client.get.return_value.raise_for_status.side_effect = requests.HTTPError("private-key-value")
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "ESTAT_HTTP_INVALID"})

    def test_estat_request_selects_completed_months_instead_of_first_page(self):
        client = Mock(); client.get.return_value.json.return_value = estat()
        api = client.get.return_value
        release = Mock(); release.text = japan_release()
        calendar = Mock(); calendar.text = japan_calendar()
        client.get.side_effect = lambda url, **kwargs: release if url.endswith("index-z.html") else calendar if url.endswith("1582.html") else api
        fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW)
        params = client.get.call_args_list[0].kwargs["params"]
        periods = params["cdTime"].split(",")
        self.assertEqual(len(periods), 24)
        self.assertEqual(len(set(periods)), 24)
        self.assertEqual(periods[0], "2026000808")
        self.assertEqual(periods[-1], "2024000909")
        self.assertEqual(client.get.call_count, 3)

    def test_estat_truncated_success_cannot_confirm_freshness(self):
        payload = estat()
        payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["RESULT_INF"] = {"NEXT_KEY": 25}
        with self.assertRaisesRegex(ValueError, "ESTAT_INCOMPLETE_RESPONSE"):
            parse_estat_cpi(payload, NOW)
        client = Mock(); client.get.return_value.json.return_value = payload
        diagnostic = {}
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only",
                                             now=NOW, diagnostics=diagnostic))
        self.assertEqual(diagnostic["code"], "ESTAT_INCOMPLETE_RESPONSE")

    def test_boolean_is_not_a_cpi_rate(self):
        payload = estat()
        payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"][0]["$"] = True
        with self.assertRaisesRegex(ValueError, "CPI_VALUE_INVALID"):
            parse_estat_cpi(payload, NOW)
        payload = abs_data()
        payload["dataSets"][0]["series"]["0:0:0:0:0"]["observations"]["0"] = [False]
        with self.assertRaisesRegex(ValueError, "CPI_VALUE_INVALID"):
            parse_abs_cpi(payload, NOW)

    def test_diagnostics_missing_key_and_empty_observations(self):
        diagnostics = {}
        self.assertIsNone(fetch_official_cpi("JPY", diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "KEY_MISSING"})
        client = Mock(); payload = estat()
        payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["DATA_INF"]["VALUE"] = []
        client.get.return_value.json.return_value = payload
        self.assertIsNone(fetch_official_cpi("JPY", client=client, estat_key="test-only", now=NOW, diagnostics=diagnostics))
        self.assertEqual(diagnostics, {"code": "NO_ELIGIBLE_OBSERVATION", "provider_status": 0})


if __name__ == "__main__": unittest.main()
