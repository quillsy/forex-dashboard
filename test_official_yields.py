"""Offline checks for Japan MOF identity, date and failure handling."""

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

import requests

from official_yields import JAPAN_MOF_CURRENT_URL, fetch_japan_mof_2y, parse_japan_mof_2y


FIXTURE = """Interest Rate (September 2026),,,(Unit : %)
Date,1Y,2Y,3Y
2026/9/3,1.563,1.85,1.994
2026/9/4,1.546,1.83,1.955
,,,
"If you cannot download the latest csv data, clear the browser cache.",,,
"""
NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


class JapanMofYieldTests(unittest.TestCase):
    def parse(self, text=FIXTURE, target="2026-09-07", now=NOW):
        return parse_japan_mof_2y(text, target, now=now)

    def test_exact_series_value_and_provenance(self):
        result = self.parse()
        self.assertEqual(result["value"], 1.83)
        self.assertEqual(result["observation_date"], "2026-09-04")
        self.assertEqual(result["source"], "Japan MOF 2Y constant maturity")
        self.assertEqual(result["source_url"], JAPAN_MOF_CURRENT_URL)
        self.assertNotIn("publication_date", result)

    def test_target_and_now_both_bound_selection(self):
        self.assertEqual(self.parse(target="2026-09-03")["value"], 1.85)
        self.assertEqual(self.parse(target="2099-01-01", now="2026-09-03")["value"], 1.85)
        self.assertIsNone(self.parse(target="2026-08-31"))

    def test_units_and_identity_are_required(self):
        for text in (FIXTURE.replace("Unit : %", "Unit : basis points"),
                     FIXTURE.replace("Date,1Y,2Y,3Y", "Date,1Y,20Y,3Y"),
                     FIXTURE.replace("Date,1Y,2Y,3Y", "Date,2Y,2Y,3Y")):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                self.parse(text)

    def test_nonfinite_and_implausible_values_rejected(self):
        for value in ("NaN", "inf", "-inf", "30.01", "-5.01", "oops"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(FIXTURE.replace(",1.83,", f",{value},"))

    def test_zero_and_negative_yields_are_valid(self):
        for value in (0, -0.25, -5, 30):
            with self.subTest(value=value):
                self.assertEqual(self.parse(FIXTURE.replace(",1.83,", f",{value},"))["value"], value)

    def test_conflicts_rejected_identical_duplicates_allowed(self):
        self.assertEqual(self.parse(FIXTURE + "2026/9/4,1.546,1.83,1.955\n")["value"], 1.83)
        for value in ("1.84", "-"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(FIXTURE + f"2026/9/4,1.546,{value},1.955\n")

    def test_missing_observation_is_not_zero(self):
        self.assertEqual(self.parse(FIXTURE.replace(",1.83,", ",-,"))["observation_date"], "2026-09-03")

    def test_injected_transport_and_failure(self):
        client = Mock()
        client.get.return_value.text = FIXTURE
        self.assertEqual(fetch_japan_mof_2y("2026-09-07", client=client, now=NOW)["value"], 1.83)
        client.get.assert_called_once_with(JAPAN_MOF_CURRENT_URL, timeout=15)
        client.get.return_value.raise_for_status.side_effect = requests.HTTPError("private details")
        self.assertIsNone(fetch_japan_mof_2y("2026-09-07", client=client, now=NOW))




from official_yields import parse_treasury_2y, fetch_treasury_2y


def treasury_fixture(rows=(('2026-09-03','4.34'),('2026-09-04','4.37'))):
    entries=''.join(f'''<entry><category term="TreasuryDataWarehouseModel.DailyTreasuryYieldCurveRateDatum"/><content><m:properties>
    <d:NEW_DATE m:type="Edm.DateTime">{day}T00:00:00</d:NEW_DATE><d:BC_2YEAR m:type="Edm.Double">{value}</d:BC_2YEAR>
    </m:properties></content></entry>''' for day,value in rows)
    return f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"><title>DailyTreasuryYieldCurveRateData</title>{entries}</feed>'''


class TreasuryYieldTests(unittest.TestCase):
    def test_current_nominal_cmt_identity(self):
        result=parse_treasury_2y(treasury_fixture(),'2026-09-07',now=NOW)
        self.assertEqual((result['value'],result['observation_date']), (4.37,'2026-09-04'))
        self.assertEqual(result['equivalent_series_id'],'DGS2')
        self.assertEqual(result['unit'],'percent_per_annum')
        self.assertIsNone(result['published_at'])

    def test_wrong_real_feed_or_wrong_maturity_rejected(self):
        for old,new in [('DailyTreasuryYieldCurveRateData','DailyTreasuryRealYieldCurveRateData'),('BC_2YEAR','BC_20YEAR'),('Edm.Double','Edm.String')]:
            with self.assertRaises(ValueError):parse_treasury_2y(treasury_fixture().replace(old,new),'2026-09-07',now=NOW)

    def test_latest_bad_value_and_null_never_fall_back(self):
        for value in ('','NaN','Infinity','oops','-0.1','31'):
            with self.assertRaises(ValueError):parse_treasury_2y(treasury_fixture((('2026-09-03','4.34'),('2026-09-04',value))),'2026-09-07',now=NOW)
        text=treasury_fixture().replace('<d:BC_2YEAR m:type="Edm.Double">4.37','<d:BC_2YEAR m:type="Edm.Double" m:null="true">4.37')
        with self.assertRaises(ValueError):parse_treasury_2y(text,'2026-09-07',now=NOW)

    def test_duplicates_future_and_entity_rejected(self):
        for text in [treasury_fixture((('2026-09-04','4.37'),('2026-09-04','4.37'))),treasury_fixture((('2026-09-08','4.37'),)), '<!DOCTYPE feed>'+treasury_fixture()]:
            with self.assertRaises(ValueError):parse_treasury_2y(text,'2026-09-07',now=NOW)

    def test_month_boundary_previous_month_only_when_empty(self):
        client=Mock();client.get.side_effect=[Mock(text=treasury_fixture(())),Mock(text=treasury_fixture((('2026-08-31','4.34'),)))]
        result=fetch_treasury_2y('2026-09-01',client=client,now=NOW)
        self.assertEqual(result['observation_date'],'2026-08-31')
        self.assertEqual([c.kwargs['params']['field_tdr_date_value_month'] for c in client.get.call_args_list],['202609','202608'])
        for call in client.get.call_args_list:self.assertEqual(call.kwargs['timeout'],20)

    def test_invalid_feed_no_backup_and_transport_error(self):
        client=Mock();client.get.return_value.text=treasury_fixture((('2026-09-04','NaN'),))
        with self.assertRaises(ValueError):fetch_treasury_2y('2026-09-07',client=client,now=NOW)
        client.get.assert_called_once()
        client=Mock();client.get.return_value.raise_for_status.side_effect=requests.HTTPError('error')
        with self.assertRaises(requests.HTTPError):fetch_treasury_2y('2026-09-07',client=client,now=NOW)


if __name__ == '__main__':unittest.main()
