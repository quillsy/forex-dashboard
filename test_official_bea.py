import unittest
from io import BytesIO
from unittest.mock import Mock
import openpyxl
import requests
from official_macro import *

NOW = '2026-09-12T12:00:00+00:00'
RELEASE = '''<p>EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Wednesday, August 26, 2026</p>
<p>Real gross domestic product (GDP) increased at an annual rate of 1.5 percent in the second quarter of 2026, according to the second estimate</p>
<p>Next release: September 30, 2026, at 8:30 a.m. EDT</p>'''


def workbook():
    w = openpyxl.Workbook()
    s = w.active
    s.title = 'T10106-Q'
    for row in [
        ['Table 1.1.6. Real Gross Domestic Product, Chained Dollars'],
        ['[Millions of chained (2017) dollars] Seasonally adjusted at annual rates'],
        ['Quarterly data from 2025Q1 to 2026Q2'], ['Bureau of Economic Analysis'],
        ['Data published August 26, 2026'], ['File created Aug 25 2026 11:05AM'], [],
        ['Line', None, None, '2025Q1', '2025Q2', '2025Q3', '2025Q4', '2026Q1', '2026Q2'],
        ['1', '    Gross domestic product', 'A191RX', 23548210, 23770976, 24026834, 24055749, 24180419, 24269613],
    ]:
        s.append(row)
    return w


def content(w):
    f = BytesIO(); w.save(f); return f.getvalue()


class BEATests(unittest.TestCase):
    def test_actual_levels_yoy_and_release(self):
        r = parse_bea_gdp(content(workbook()), parse_bea_gdp_release(RELEASE, now=NOW), now=NOW)
        self.assertAlmostEqual(r['value'], 2.097671547016011)
        self.assertEqual(r['next_due_at'], '2026-09-30T12:30:00+00:00')
        self.assertEqual(r['published_at'], '2026-08-26T12:30:00+00:00')
        self.assertTrue(r['needs_hourly_check'])
        self.assertEqual(r['release_stage'], 'second')
        self.assertTrue(r['is_estimate'])

    def test_unit_vintage_missing_quarter_boolean_and_wrong_series(self):
        for cell, val in [('A2', 'Current dollars'), ('A5', 'Data published July 30, 2026'),
                          ('F8', '2025Q2'), ('I8', '2026Q3'), ('E9', None), ('I9', True), ('C9', 'OTHER')]:
            with self.subTest(cell=cell, val=val):
                w = workbook(); w.active[cell] = val
                with self.assertRaises(ValueError):
                    parse_bea_gdp(content(w), parse_bea_gdp_release(RELEASE, now=NOW), now=NOW)

    def test_headline_conflict_same_period_blocks(self):
        release = parse_bea_gdp_release(RELEASE.replace('1.5 percent', '2.0 percent'), now=NOW)
        with self.assertRaisesRegex(ValueError, 'HEADLINE_CONFLICT'):
            parse_bea_gdp(content(workbook()), release, now=NOW)

    def test_future_overdue_ambiguous_or_timezone_bad(self):
        for html, now in [(RELEASE, '2026-08-25T12:00:00Z'), (RELEASE, '2026-09-30T12:30:00Z'),
                          (RELEASE.replace('EDT', 'EST'), NOW), (RELEASE + RELEASE, NOW),
                          (RELEASE.replace('second quarter', 'fourth quarter'), NOW)]:
            with self.assertRaises(ValueError):
                parse_bea_gdp_release(html, now=now)

    def test_release_boundary_exact_and_revised_vintage(self):
        release = parse_bea_gdp_release(RELEASE, now=NOW)
        self.assertEqual(parse_bea_gdp(content(workbook()), release, now=release['published_at'])['date'], '2026-06-30')
        with self.assertRaisesRegex(ValueError, 'NOT_CURRENT'):
            parse_bea_gdp(content(workbook()), release, now=release['next_due_at'])
        w = workbook(); w.active['A5'] = 'Data published August 27, 2026'
        with self.assertRaisesRegex(ValueError, 'VINTAGE_CONFLICT'):
            parse_bea_gdp(content(w), release, now=NOW)

    def test_duplicate_series_missing_prior_year_and_wrong_frequency(self):
        release = parse_bea_gdp_release(RELEASE, now=NOW)
        w = workbook(); w.active.append([1, 'Gross domestic product', 'A191RX', 1])
        with self.assertRaisesRegex(ValueError, 'SERIES_INVALID'):
            parse_bea_gdp(content(w), release, now=NOW)
        w = workbook(); w.active.delete_cols(4, 2)
        with self.assertRaisesRegex(ValueError, 'LEVEL_INVALID'):
            parse_bea_gdp(content(w), release, now=NOW)
        w = workbook(); w.active.title = 'T10106-A'
        with self.assertRaisesRegex(ValueError, 'SCHEMA_INVALID'):
            parse_bea_gdp(content(w), release, now=NOW)

    def test_release_stage_conflict(self):
        with self.assertRaisesRegex(ValueError, 'STAGE_CONFLICT'):
            parse_bea_gdp_release(RELEASE + '<p>according to the third estimate</p>', now=NOW)

    def test_discovery_and_transport(self):
        url = 'https://www.bea.gov/news/2026/gdp-second-estimate-and-corporate-profits-2nd-quarter-2026'
        landing = '<a href="'+url+'">Current Release</a><a href="#collapseCurrent">Current Release</a>'
        self.assertEqual(discover_bea_gdp_release(landing), url)
        with self.assertRaises(ValueError):
            discover_bea_gdp_release(landing.replace('www.bea.gov', 'evil.test'))
        session = Mock()
        responses = [Mock(text=landing), Mock(text=RELEASE), Mock(content=content(workbook()))]
        session.get.side_effect = responses
        self.assertEqual(fetch_bea_gdp(now=NOW, session=session)['date'], '2026-06-30')
        self.assertEqual(session.get.call_count, 3)
        session.get.side_effect = requests.Timeout('offline')
        with self.assertRaises(requests.Timeout):
            fetch_bea_gdp(now=NOW, session=session)

if __name__ == '__main__':
    unittest.main()
