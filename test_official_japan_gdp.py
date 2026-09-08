"""Official Japan GDP regression tests; fixture from ESRI second Q2 release2026."""
import csv
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
import requests
from official_macro import (fetch_japan_gdp, discover_japan_gdp_menu,
    parse_japan_gdp_menu, parse_japan_gdp_release, parse_japan_gdp_csv,
    JP_GDP_LATEST)

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
MENU = 'https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/qe262_2/gdemenuea.html'
CSV = 'https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/qe262_2/tables/gaku-jk2622.csv'
ARCHIVE = 'https://www.esri.cao.go.jp/en/sna/data/sokuhou/files/2026/toukei_2026.html'


def index(menu=MENU):
    return f'<h1>Quarterly Estimates of GDP</h1><a href="{menu}">Time series table</a>'.encode()


def menu():
    return f'<h1>Apr.-Jun.2026 (The 2nd preliminary)</h1><a href="{CSV}">Real, Seasonally Adjusted Series (csv:42KB)</a>'.encode()


def archive():
    return f'<h1>Quarterly Estimates of GDP - Release Archive - 2026</h1><table><tr><td>Sep 8, 2026</td><td><a href="{MENU}">Q2 release</a></td></tr></table>'.encode()


def csv_rows():
    rows = [[''] * 33 for _ in range(7)]
    rows[0][0] = '実質季節調整系列'
    rows[1][0] = 'Real, Seasonally Adjusted Series'
    rows[1][29] = '(Billions of Chained (2020) Yen)'
    rows[2][1] = '国内総生産(支出側)'
    rows[5][1] = 'GDP(Expenditure Approach)'
    for quarter, level in [('2025/ 1- 3.', '593,896.1 '), ('4- 6.', '594,629.6 '),
                           ('7- 9.', '592,407.5 '), ('10-12.', '593,959.0 '),
                           ('2026/ 1- 3.', '596,799.3 '), ('4- 6.', '598,950.5 ')]:
        rows.append([quarter, level] + [''] * 31)
    rows.append(['＊年率で表示している。'] + [''] * 32)
    return rows


def content(rows=None):
    stream = io.StringIO(); csv.writer(stream).writerows(csv_rows() if rows is None else rows)
    return stream.getvalue().encode('cp932')


def metadata():
    out = parse_japan_gdp_menu(menu(), MENU)
    out.update(parse_japan_gdp_release(archive(), out, now=NOW))
    return out


class JapanGDPProposalTests(unittest.TestCase):
    def test_current_vintage_levels_produce_yoy_not_annualized_qoq(self):
        result = parse_japan_gdp_csv(content(), metadata(), now=NOW)
        self.assertAlmostEqual(result['value'], 0.7266540380768127)
        self.assertNotAlmostEqual(result['value'], 100 * ((598950.5 / 596799.3) ** 4 - 1))
        self.assertEqual(result['date'], '2026-06-30')
        self.assertEqual(result['frequency'], 'quarterly')
        self.assertEqual(result['seasonal_adjustment'], 'SA')
        self.assertIsNone(result['published_at'])
        self.assertIsNone(result['next_due_at'])
        self.assertTrue(result['needs_hourly_check'])
        self.assertEqual(result['provider_status'], 'p')
        self.assertTrue(result['is_estimate'])

    def test_verified_official_csv_matches_expected_current_yoy(self):
        from pathlib import Path
        raw = Path(__file__).with_name('testdata_jpy_gdp.csv').read_bytes()
        result = parse_japan_gdp_csv(raw, metadata(), now=NOW)
        self.assertAlmostEqual(result['value'], 0.7266540380768127)
        self.assertEqual(result['reference_period'], '2026-Q2')

    def test_all_four_requests_are_keyless_discovered_and_bounded(self):
        client = Mock()
        client.get.side_effect = [Mock(content=body, url=url) for body, url in
                                 [(index(), JP_GDP_LATEST), (menu(), MENU), (archive(), ARCHIVE), (content(), CSV)]]
        self.assertAlmostEqual(fetch_japan_gdp(now=NOW, session=client)['value'], 0.7266540380768127)
        self.assertEqual([call.args[0] for call in client.get.call_args_list], [JP_GDP_LATEST, MENU, ARCHIVE, CSV])
        self.assertTrue(all(call.kwargs == {'timeout': 20} for call in client.get.call_args_list))

    def test_dynamic_menu_works_in_later_year(self):
        later = MENU.replace('2026', '2027').replace('qe262_2', 'qe271_1')
        found = discover_japan_gdp_menu(index(later))
        page = menu().replace(b'Apr.-Jun.2026 (The 2nd', b'Jan.-Mar.2027 (The 1st').replace(b'2026', b'2027').replace(b'qe262_2', b'qe271_1').replace(b'gaku-jk2622', b'gaku-jk2711')
        self.assertEqual(parse_japan_gdp_menu(page, found)['reference_period'], '2027-Q1')

    def test_foreign_host_and_credential_queries_are_rejected(self):
        for url in (MENU.replace('www.esri.cao.go.jp', 'evil.example'), MENU + '?token=private', MENU.replace('https:', 'http:')):
            with self.subTest(url=url), self.assertRaises(ValueError):
                discover_japan_gdp_menu(index(url))

    def test_ambiguous_or_wrong_identity_menu_rejected(self):
        for page in (index() + index(), index().replace(b'Quarterly Estimates', b'Other Estimates')):
            with self.assertRaises(ValueError): discover_japan_gdp_menu(page)
        with self.assertRaises(ValueError): parse_japan_gdp_menu(menu().replace(b'2nd', b'1st'), MENU)

    def test_wrong_metric_and_price_base_are_rejected(self):
        for row, col, wrong in [(1, 0, 'Real, Original Series'), (1, 29, '(Billions of Chained (2015) Yen)'),
                                (5, 1, 'GNI'), (1, 0, 'Real, Seasonally Adjusted Series (Quarter-to-Quarter, Annualized)')]:
            rows = csv_rows(); rows[row][col] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                parse_japan_gdp_csv(content(rows), metadata(), now=NOW)

    def test_missing_or_duplicate_quarter_is_rejected(self):
        for mutation in ('missing', 'duplicate'):
            rows = csv_rows()
            if mutation == 'missing': rows.pop(8)
            else: rows.insert(9, rows[8][:])
            with self.assertRaises(ValueError): parse_japan_gdp_csv(content(rows), metadata(), now=NOW)

    def test_missing_prior_year_quarter_cannot_be_replaced_by_row_offset(self):
        rows = csv_rows(); rows = rows[:7] + rows[11:]
        with self.assertRaisesRegex(ValueError, 'PRIOR_QUARTER_MISSING'):
            parse_japan_gdp_csv(content(rows), metadata(), now=NOW)

    def test_bad_latest_level_never_falls_back(self):
        for value in ('', 'NaN', 'inf', '0', '-1', '1,2', '***'):
            rows = csv_rows(); rows[-2][1] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_japan_gdp_csv(content(rows), metadata(), now=NOW)

    def test_csv_must_end_at_current_menu_period(self):
        rows = csv_rows(); rows.pop(-2)
        with self.assertRaisesRegex(ValueError, 'PERIOD_CONFLICT'):
            parse_japan_gdp_csv(content(rows), metadata(), now=NOW)

    def test_archive_must_confirm_newest_matching_release(self):
        meta = parse_japan_gdp_menu(menu(), MENU)
        newer = '<tr><td>Sep 9, 2026</td><td><a href="' + MENU.replace('qe262_2', 'qe263_1') + '">newer</a></td></tr>'
        for page in (archive().replace(b'</table>', newer.encode() + b'</table>'), archive().replace(b'Sep 8', b'Sep 9')):
            with self.assertRaises(ValueError): parse_japan_gdp_release(page, meta, now=NOW)

    def test_changed_source_redirect_is_rejected(self):
        client = Mock(); client.get.return_value = Mock(content=index(), url='https://other.example/')
        with self.assertRaisesRegex(ValueError, 'REDIRECT'):
            fetch_japan_gdp(now=NOW, session=client)

    def test_network_failure_propagates_distinct_from_contract_error(self):
        client = Mock(); client.get.side_effect = requests.RequestException('offline')
        with self.assertRaises(requests.RequestException): fetch_japan_gdp(now=NOW, session=client)
        self.assertEqual(client.get.call_count, 1)


if __name__ == '__main__': unittest.main()
