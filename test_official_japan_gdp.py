"""Official Japan GDP regression tests for suffixed and suffixless ESRI releases."""
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
Q3_NOW = datetime(2026, 11, 19, 13, tzinfo=timezone.utc)
Q3_MENU_HREF = '/en/sna/data/sokuhou/files/2026/qe263/gdemenuea.html'
Q3_CSV_HREF = '/jp/sna/data/data_list/sokuhou/files/2026/qe263/tables/gaku-jk2631.csv'
Q3_MENU = 'https://www.esri.cao.go.jp' + Q3_MENU_HREF
Q3_CSV = 'https://www.esri.cao.go.jp' + Q3_CSV_HREF
Q3_ARCHIVE_LABEL = 'Quarterly Estimates of GDP for Jul.-Sep.2026 (The First preliminary Estimates)'


def index(menu=MENU):
    return f'<h1>Quarterly Estimates of GDP</h1><a href="{menu}">Time series table</a>'.encode()


def menu():
    return f'<h1>Apr.-Jun.2026 (The 2nd preliminary)</h1><a href="{CSV}">Real, Seasonally Adjusted Series (csv:42KB)</a>'.encode()


def archive():
    return f'<h1>Quarterly Estimates of GDP - Release Archive - 2026</h1><table><tr><td>Sep 8, 2026</td><td><a href="{MENU}">Q2 release</a></td></tr></table>'.encode()


def q3_menu():
    # Minimal HTML of the visible labels and relative hrefs verified 2026-11-19;
    # the similarly labelled household series is deliberately present as well.
    household = Q3_CSV_HREF.replace('/gaku-', '/kgaku-')
    return (f'<h1>Jul.-Sep.2026 (The 1st preliminary)</h1>'
            f'<h3>GDP (Expenditure Approach) and Its Components</h3>'
            f'<a href="{Q3_CSV_HREF}">Real, Seasonally Adjusted Series (csv:42KB)</a>'
            f'<h3>Household Consumption</h3>'
            f'<a href="{household}">Real, Seasonally Adjusted Series (csv:25KB)</a>').encode()


def q3_archive_row(date, href, period, ordinal):
    return (f'<tr><td>{date}</td><td><a href="{href}">Quarterly Estimates of GDP for '
            f'{period}2026 (The {ordinal} preliminary Estimates)</a> (Benchmark year=2020)</td></tr>')


def q3_archive():
    # The 2026 archive contains suffixless first and suffixed second estimates.
    prefix = '/en/sna/data/sokuhou/files/2026/'
    rows = [
        q3_archive_row('Nov  16, 2026', Q3_MENU_HREF, 'Jul.-Sep.', 'First'),
        q3_archive_row('Sep 8, 2026', prefix + 'qe262_2/gdemenuea.html', 'Apr.-Jun.', 'Second'),
        q3_archive_row('Aug  17, 2026', prefix + 'qe262/gdemenuea.html', 'Apr.-Jun.', 'First'),
        q3_archive_row('Jun 8, 2026', prefix + 'qe261_2/gdemenuea.html', 'Jan.-Mar.', 'Second'),
        q3_archive_row('May  19, 2026', prefix + 'qe261/gdemenuea.html', 'Jan.-Mar.', 'First'),
    ]
    return ('<h1>Quarterly Estimates of GDP - Release Archive - 2026</h1><table>'
            + ''.join(rows) + '</table>').encode()


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

    def test_suffixless_first_preliminary_uses_verified_index_menu_archive_and_csv_link(self):
        # Only the link metadata is from the current official release. The
        # added Q3 level below is synthetic, not a downloaded GDP value.
        rows = csv_rows()
        rows.insert(-1, ['7- 9.', '600,000.0 '] + [''] * 31)
        client = Mock()
        client.get.side_effect = [Mock(content=body, url=url) for body, url in
                                  [(index(Q3_MENU_HREF), JP_GDP_LATEST), (q3_menu(), Q3_MENU),
                                   (q3_archive(), ARCHIVE), (content(rows), Q3_CSV)]]
        result = fetch_japan_gdp(now=Q3_NOW, session=client)
        self.assertEqual([call.args[0] for call in client.get.call_args_list],
                         [JP_GDP_LATEST, Q3_MENU, ARCHIVE, Q3_CSV])
        self.assertTrue(all(call.kwargs == {'timeout': 20} for call in client.get.call_args_list))
        self.assertEqual(result['reference_period'], '2026-Q3')
        self.assertEqual(result['release_stage'], '1st')
        self.assertEqual(result['release_date_known'], '2026-11-16')
        self.assertEqual(result['date'], '2026-09-30')
        self.assertEqual(result['source_url'], Q3_CSV)
        self.assertAlmostEqual(result['value'], 100 * (600000.0 / 592407.5 - 1))
        self.assertIsNone(result['published_at'])
        self.assertIsNone(result['next_due_at'])
        self.assertTrue(result['needs_hourly_check'])
        self.assertTrue(result['is_estimate'])

    def test_suffixless_menu_requires_matching_year_quarter_and_first_stage(self):
        for url in (Q3_MENU.replace('/2026/', '/2027/'), Q3_MENU.replace('qe263', 'qe2640'),
                    Q3_MENU.replace('qe263', 'qe269'), Q3_MENU.replace('qe263', 'qe263_3')):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'MENU_PATH_INVALID'):
                discover_japan_gdp_menu(index(url))
        for title in (b'Jul.-Sep.2027 (The 1st preliminary)',
                      b'Apr.-Jun.2026 (The 1st preliminary)', b'Jul.-Sep.2026 (The 2nd preliminary)'):
            with self.subTest(title=title), self.assertRaisesRegex(ValueError, 'MENU_IDENTITY_INVALID'):
                parse_japan_gdp_menu(q3_menu().replace(b'Jul.-Sep.2026 (The 1st preliminary)', title), Q3_MENU)
        self.assertEqual(parse_japan_gdp_menu(q3_menu(), Q3_MENU)['source_url'], Q3_CSV)

    def test_suffixless_menu_cannot_substitute_csv_directory_series_quarter_year_or_stage(self):
        valid = Q3_CSV_HREF.encode()
        for invalid in (Q3_CSV_HREF.replace('/qe263/', '/qe263_1/'),
                        Q3_CSV_HREF.replace('/2026/', '/2027/'),
                        Q3_CSV_HREF.replace('qe263', 'qe262'),
                        Q3_CSV_HREF.replace('gaku-jk2631', 'kgaku-jk2631'),
                        Q3_CSV_HREF.replace('gaku-jk2631', 'gaku-jk2632'),
                        Q3_CSV_HREF.replace('gaku-jk2631', 'gaku-jk2621')):
            page = q3_menu().replace(valid, invalid.encode())
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'LEVEL_CSV_AMBIGUOUS'):
                parse_japan_gdp_menu(page, Q3_MENU)
        duplicate = q3_menu() + f'<a href="{Q3_CSV_HREF}">Real, Seasonally Adjusted Series (csv:42KB)</a>'.encode()
        with self.assertRaisesRegex(ValueError, 'LEVEL_CSV_AMBIGUOUS'):
            parse_japan_gdp_menu(duplicate, Q3_MENU)

    def test_suffixless_archive_requires_consistent_stated_release_identity(self):
        meta = parse_japan_gdp_menu(q3_menu(), Q3_MENU)
        label = Q3_ARCHIVE_LABEL.encode()
        for invalid in (label.replace(b'First', b'Second'), label.replace(b'2026', b'2027'),
                        label.replace(b'Jul.-Sep.', b'Apr.-Jun.'), b'Q3 release'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'ARCHIVE_RELEASE_IDENTITY_INVALID'):
                parse_japan_gdp_release(q3_archive().replace(label, invalid), meta, now=Q3_NOW)
        for field, invalid in (('reference_period', '2026-Q2'), ('release_stage', '2nd'),
                               ('archive_url', ARCHIVE.replace('2026', '2027'))):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'ARCHIVE_IDENTITY_INVALID'):
                parse_japan_gdp_release(q3_archive(), {**meta, field: invalid}, now=Q3_NOW)

    def test_archive_rejects_duplicate_match_same_day_rival_and_later_suffixless_release(self):
        meta = parse_japan_gdp_menu(q3_menu(), Q3_MENU)
        entries = (
            q3_archive_row('Nov  16, 2026', Q3_MENU_HREF, 'Jul.-Sep.', 'First'),
            q3_archive_row('Nov  16, 2026', Q3_MENU_HREF.replace('qe263', 'qe263_1'), 'Jul.-Sep.', 'First'),
            q3_archive_row('Nov  17, 2026', Q3_MENU_HREF.replace('qe263', 'qe264'), 'Oct.-Dec.', 'First'),
        )
        for entry in entries:
            page = q3_archive().replace(b'</table>', entry.encode() + b'</table>')
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, 'RELEASE_NOT_LATEST'):
                parse_japan_gdp_release(page, meta, now=Q3_NOW)

    def test_archive_rejects_unrecognized_official_release_path_instead_of_hiding_it(self):
        meta = parse_japan_gdp_menu(q3_menu(), Q3_MENU)
        other_material = q3_archive_row('Nov  17, 2026', Q3_MENU_HREF.replace('gdemenuea.html', 'summary.pdf'),
                                       'Jul.-Sep.', 'First')
        # A release-like title alone is not evidence that a PDF is a menu.
        page = q3_archive().replace(b'</table>', other_material.encode() + b'</table>')
        self.assertEqual(parse_japan_gdp_release(page, meta, now=Q3_NOW)['release_date_known'], '2026-11-16')
        entry = q3_archive_row('Nov  17, 2026', Q3_MENU_HREF.replace('qe263', 'qe264_3'), 'Oct.-Dec.', 'First')
        with self.assertRaisesRegex(ValueError, 'MENU_PATH_INVALID'):
            parse_japan_gdp_release(q3_archive().replace(b'</table>', entry.encode() + b'</table>'), meta, now=Q3_NOW)

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
