import copy
import csv
import html
import io
import json
import math
import unittest
from datetime import datetime, timezone

from official_quarterly_labour import (
    CH_RIGHTS, CH_TITLE, fetch_ch_labour, fetch_nz_labour, fetch_quarterly_labour,
    parse_ch_labour, parse_nz_labour, select_ch_labour_resources,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
URL = 'https://dam-api.bfs.admin.ch/hub/api/dam/assets/123456/master'
ISSUED = '2026-08-18T06:30:00+00:00'


def nz_document():
    return {'Title': 'Labour market statistics: June 2026 quarter',
            'DateTaxonomyTerm': {'PublicationDate': '2026-08-05 10:45:00'},
            'FeaturedMedia': {'GraphHeading': 'Unemployment rate by sex, seasonally adjusted, June 2012–June 2026 quarters',
                'SeriesData': [{'GraphCsvData': 'Quarter,Sept-25,Mar-26,Jun-26\nMen,5.2,5.4,5.7\nWomen,5.1,5.3,5.5\nTotal,5.2,5.4,5.6\n'}]},
            'Blocks': [{'HTML': '<p>Labour market statistics: September 2026 quarter</p> will be released on <b>4 November 2026</b>.'}]}


def page(d):
    return '<div data-value="' + html.escape(json.dumps(d), quote=True) + '"></div>'


def ch_csv(rows=None):
    fields = ['INDICATORS_HRCHY', 'INDICATORS_DE', 'SEASON_D', 'DETAILS_DE', 'PERIOD', 'FREQ', 'MEASURE_DE', 'VALUE', 'STATUS']
    row = ['P', 'TOTAL', 'saisonbereinigte', 'Total', '2026-Q2', 'Q', 'Durchschnittliche Quartalswerte', '5.11906217648145', 'A']
    out = io.StringIO(); writer = csv.writer(out); writer.writerow(fields)
    writer.writerows(rows if rows is not None else [row])
    return out.getvalue().encode('utf-8-sig')


def catalog():
    return {'success': True, 'result': {'count': 1, 'results': [{
        'name': 'ilo-quarterly-labour', 'title': {'de': CH_TITLE}, 'organization': {'name': 'bundesamt-fur-statistik-bfs'},
        'resources': [{'format': 'CSV', 'url': URL, 'rights': CH_RIGHTS, 'issued': ISSUED}]}]}}


class Response:
    def __init__(self, text='', content=b'', payload=None, status=200):
        self.text, self.content, self.payload, self.status_code = text, content, payload, status
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('HTTP_FAILED')
    def json(self): return self.payload


class Session:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs)); return self.responses.pop(0)


class QuarterlyLabourTests(unittest.TestCase):
    def test_common_entrypoint_and_currency_guard(self):
        session = Session([Response(text=page(nz_document()))])
        row = fetch_quarterly_labour("NZD", now=NOW, session=session)
        self.assertEqual(row["period_label"], "Saisonbereinigte Quartalsquote")
        with self.assertRaises(ValueError): fetch_quarterly_labour("USD", now=NOW, session=session)

    def test_nz_total_actual_publication_and_conservative_due(self):
        row = parse_nz_labour(page(nz_document()), year=2026, quarter=2, now=NOW)
        self.assertEqual(row['value'], 5.6)
        self.assertEqual(row['date'], '2026-06-30')
        self.assertEqual(row['frequency'], 'quarterly')
        self.assertEqual(row['published_at'], '2026-08-04T22:45:00+00:00')
        self.assertEqual(row['next_due_at'], '2026-11-03T11:00:00+00:00')

    def test_nz_rejects_wrong_survey_missing_total_future_and_conflict(self):
        cases = []
        d = nz_document(); d['FeaturedMedia']['GraphHeading'] = 'Underutilisation rate'; cases.append(d)
        d = nz_document(); d['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = 'Quarter,Jun-26\nMen,5.7'; cases.append(d)
        d = nz_document(); d['DateTaxonomyTerm']['PublicationDate'] = '2026-10-05 10:45:00'; cases.append(d)
        d = nz_document(); d['FeaturedMedia']['SeriesData'].append({'GraphCsvData': 'Quarter,Jun-26\nTotal,9.9'}); cases.append(d)
        d = nz_document(); d['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = 'Quarter,Jun-26,Sep-26\nTotal,5.6,5.7'; cases.append(d)
        d = nz_document(); d['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = 'Quarter,Jun-26\nTotal,nan'; cases.append(d)
        for d in cases:
            with self.subTest(d=d), self.assertRaises(ValueError):
                parse_nz_labour(page(d), year=2026, quarter=2, now=NOW)

    def test_nz_rejects_duplicate_period_and_conflicting_calendar(self):
        d = nz_document(); d['FeaturedMedia']['SeriesData'][0]['GraphCsvData'] = 'Quarter,Jun-26,Jun-26\nTotal,5.6,5.6'
        with self.assertRaises(ValueError): parse_nz_labour(page(d), year=2026, quarter=2, now=NOW)
        d = nz_document(); d['Blocks'].append('Labour market statistics: September 2026 quarter will be released on 5 November 2026')
        with self.assertRaises(ValueError): parse_nz_labour(page(d), year=2026, quarter=2, now=NOW)

    def test_nz_unknown_next_date_remains_unknown(self):
        d = nz_document(); d.pop('Blocks')
        self.assertIsNone(parse_nz_labour(page(d), year=2026, quarter=2, now=NOW)['next_due_at'])

    def test_nz_discovery_only_404_permits_previous_quarter(self):
        session = Session([Response(status=404), Response(text=page(nz_document()))])
        row = fetch_nz_labour(now=datetime(2026, 10, 7, tzinfo=timezone.utc), session=session)
        self.assertEqual(row['reference_period'], '2026-Q2')
        self.assertIn('september-2026', session.calls[0][0]); self.assertIn('june-2026', session.calls[1][0])
        session = Session([Response(status=503), Response(text=page(nz_document()))])
        with self.assertRaises(RuntimeError): fetch_nz_labour(now=NOW, session=session)
        self.assertEqual(len(session.calls), 1)

    def test_ch_selects_latest_official_published_resource(self):
        d = catalog(); resources = d['result']['results'][0]['resources']
        resources.append(dict(resources[0], url=URL.replace('123456', '999999'), issued='2026-05-18T06:30:00Z'))
        resources.append(dict(resources[0], url=URL.replace('123456', '888888'), issued='2026-11-18T06:30:00Z'))
        self.assertEqual(select_ch_labour_resources(d, now=NOW), [{'issued': ISSUED, 'url': URL, 'source_url': 'https://opendata.swiss/dataset/ilo-quarterly-labour'}])

    def test_ch_rejects_wrong_rights_origin_and_truncated_catalog(self):
        for field, value in [('rights', 'https://opendata.swiss/terms-of-use#terms_by_ask'), ('url', 'https://example.com/data.csv')]:
            d = catalog(); d['result']['results'][0]['resources'][0][field] = value
            with self.assertRaises(ValueError): select_ch_labour_resources(d, now=NOW)
        d = catalog(); d['result']['count'] = 101
        with self.assertRaises(ValueError): select_ch_labour_resources(d, now=NOW)
        d = catalog(); d['result']['results'][0]['organization']['name'] = 'other'
        with self.assertRaises(ValueError): select_ch_labour_resources(d, now=NOW)

    def test_ch_total_sa_value(self):
        row = parse_ch_labour(ch_csv(), published_at=ISSUED, source_url=URL, now=NOW)
        self.assertEqual(row['reference_period'], '2026-Q2'); self.assertAlmostEqual(row['value'], 5.11906217648145)
        self.assertEqual(row['published_at'], ISSUED); self.assertIsNone(row['next_due_at'])

    def test_ch_rejects_bad_metadata_future_nan_conflicts(self):
        original = ['P', 'TOTAL', 'saisonbereinigte', 'Total', '2026-Q2', 'Q', 'Durchschnittliche Quartalswerte', '5.1', 'A']
        for position, value in [(1, 'OTHER'), (3, 'Men'), (4, '2026-Q4'), (5, 'M'), (6, 'Monatswerte'), (7, 'nan'), (8, 'O')]:
            row = original[:]; row[position] = value
            with self.subTest(position=position), self.assertRaises(ValueError):
                parse_ch_labour(ch_csv([row]), published_at=ISSUED, source_url=URL, now=NOW)
        other = original[:]; other[7] = '5.2'
        with self.assertRaises(ValueError): parse_ch_labour(ch_csv([original, other]), published_at=ISSUED, source_url=URL, now=NOW)

    def test_ch_fetch_dynamic_url_and_conflicting_releases(self):
        session = Session([Response(payload=catalog()), Response(content=ch_csv())])
        self.assertEqual(fetch_ch_labour(now=NOW, session=session)['source_url'], 'https://opendata.swiss/dataset/ilo-quarterly-labour')
        self.assertEqual(session.calls[1][0], URL)
        d = catalog(); d['result']['results'][0]['resources'].append(dict(d['result']['results'][0]['resources'][0], url=URL.replace('123456', '777777')))
        session = Session([Response(payload=d), Response(content=ch_csv()), Response(content=ch_csv().replace(b'5.11906217648145', b'8.8'))])
        with self.assertRaises(ValueError): fetch_ch_labour(now=NOW, session=session)




class JapanLabourTests(unittest.TestCase):
    CURRENT_NOW = datetime(2026, 10, 2, 13, tzinfo=timezone.utc)

    def release_pages(self):
        return ('<table><tr><td>Monthly</td><td>- July 2026 - (Released on August 28, 2026) Main results</td><td>-</td></tr></table>',
                '<table><tr><td>2026 July</td><td>August 28</td><td></td><td></td></tr>'
                '<tr><td>August</td><td>October 2</td><td></td><td></td></tr></table>')

    def metadata(self, changes=None, download=None):
        # Relevant original e-Stat detail-table markup, checked 2026-10-02.
        # The separate empty modal row is also present on the real page.
        fields = {
            'Statistics name': 'Labour Force Survey',
            'Statistics code': '00200531',
            'Dataset category0': 'Labour force survey (Public documents, Historical data)',
            'Dataset category1': 'Historical data',
            'Dataset category2': 'Basic tabulation',
            'Table number': '1-a-1',
            'Table category1': '[Monthly figures - Results of whole Japan] Seasonally adjusted series and Original series',
            'Statistical table name': 'Major items (Labour force, Employed person, Employee, Unemployed person, Not in labour force, Unemployment rate)',
            'Publisher': 'Ministry of Internal Affairs and Communications',
            'Survey date': '2026 Aug.',
            'Published date and time': '2026-10-02 08:30',
            'Tabulation area': 'Nationwide',
        }
        fields.update(changes or {})
        rows = ''.join('<tr><th class="stat-resource_item">' + html.escape(key)
                       + '</th><td class="stat-resource_item"><a>\n '
                       + html.escape(value) + ' \n</a></td><td class="stat-resource_item stat-resource_exp-item"></td></tr>'
                       for key, value in fields.items())
        link = download or '/en/stat-search/file-download?statInfId=000031831358&fileKind=0'
        return ('<a class="stat-dl_icon stat-icon_0 stat-icon_format js-dl stat-download_icon_top" href="'
                + html.escape(link, quote=True) + '"><span class="stat-dl_text">EXCEL</span></a>'
                + '<table>' + rows + '</table><table><tr><th>Statistics name</th>'
                '<td class="js-modal_toukei_name"></td></tr></table>')

    def current_calendar(self):
        return ('<table><tr><th>Reference month</th><th>Date of release</th><th>Reference month</th><th>Date of release</th></tr>'
                '<tr><td>2026 July</td><td>August 28</td><td></td><td></td></tr>'
                '<tr><td>August</td><td>October 2</td><td></td><td></td></tr>'
                '<tr><td>September,<br>July - September average</td><td>October 30</td><td>July - September average</td><td>November 10</td></tr>'
                '<tr><td>October</td><td>December 1</td><td></td><td></td></tr></table>')

    def book(self, mutate=None):
        from openpyxl import Workbook
        w = Workbook(); s = w.active; s.title = '季節調整値'
        title = ('Historical data 1 a-1 Major items (Labour force, Employed person, '
                 'Employee, Unemployed person, Not in labour force, Unemployment rate) '
                 '- Whole Japan, Monthly Data')
        for column in (5, 14):
            s.cell(2, column, title)
            s.cell(5, column, '季節調整値 Seasonally adjusted series')
        s.cell(7, 20, 'Unemployment rate  (percent)'); s.cell(7, 22, '')
        s.cell(9, 20, 'Both sexes')
        for month in range(1, 13):
            row = month + 10
            if month == 1: s.cell(row, 1, '令和 8年')
            if month == 2: s.cell(row, 1, 2026)
            s.cell(row, 2, f'{month}月')
            if month <= 7: s.cell(row, 20, 2.4 if month == 7 else 2.5)
        if mutate: mutate(s)
        out = io.BytesIO(); w.save(out); return out.getvalue()

    def test_actual_layout_july_and_blank_future_months(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_release
        release = parse_japan_labour_release(*self.release_pages(), now=NOW)
        result = parse_japan_labour(self.book(), release, now=NOW)
        self.assertEqual((result['value'], result['reference_period']), (2.4, '2026-07'))
        self.assertIsNone(result['published_at'])
        self.assertEqual(result['release_date_known'], '2026-08-28')
        self.assertEqual(result['next_due_at'], '2026-10-01T15:00:00+00:00')

    def test_wrong_sex_adjustment_year_and_missing_latest_rejected(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_release
        release = parse_japan_labour_release(*self.release_pages(), now=NOW)
        mutations = [lambda s: setattr(s.cell(9,20), 'value', 'Male'),
                     lambda s: setattr(s.cell(5,5), 'value', 'Original series'),
                     lambda s: setattr(s.cell(12,1), 'value', 2025),
                     lambda s: setattr(s.cell(17,20), 'value', None),
                     lambda s: setattr(s.cell(18,20), 'value', 2.3),
                     lambda s: setattr(s.cell(17,20), 'value', True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parse_japan_labour(self.book(mutation), release, now=NOW)

    def test_release_calendar_mismatch_and_due_release_fail(self):
        from official_quarterly_labour import parse_japan_labour_release
        results, schedule = self.release_pages()
        with self.assertRaisesRegex(ValueError, 'CONFLICT'):
            parse_japan_labour_release(results, schedule.replace('August 28', 'August 29'), now=NOW)
        with self.assertRaisesRegex(ValueError, 'NEW_RELEASE_DUE'):
            parse_japan_labour_release(results, schedule, now=datetime(2026,10,1,15,tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, 'FUTURE_RELEASE'):
            parse_japan_labour_release(results, schedule, now=datetime(2026,8,27,tzinfo=timezone.utc))

    def test_fetch_rejects_failed_calendar_before_downloading_workbook(self):
        from official_quarterly_labour import fetch_japan_labour
        from unittest.mock import Mock
        import requests
        session = Mock(); _, calendar_page = self.release_pages()
        first = Mock(text=self.metadata({'Survey date': '2026 Jul.', 'Published date and time': '2026-08-28 08:30'}))
        second = Mock(text=calendar_page)
        second.raise_for_status.side_effect = requests.HTTPError()
        session.get.side_effect = [first, second]
        with self.assertRaises(requests.HTTPError): fetch_japan_labour(now=NOW, session=session)
        self.assertEqual(session.get.call_count, 2)

    def test_current_estat_metadata_and_august_workbook_without_overview_date(self):
        from official_quarterly_labour import (
            JP_LABOUR_CALENDAR, JP_LABOUR_FILE, JP_LABOUR_METADATA, JP_LABOUR_RESULTS,
            fetch_japan_labour,
        )
        from live_data import public_observation
        workbook = self.book(lambda s: setattr(s.cell(18, 20), 'value', 2.5))
        session = Session([Response(text=self.metadata()), Response(text=self.current_calendar()),
                           Response(content=workbook)])
        observation = fetch_japan_labour(now=self.CURRENT_NOW, session=session)
        self.assertEqual([call[0] for call in session.calls],
                         [JP_LABOUR_METADATA, JP_LABOUR_CALENDAR, JP_LABOUR_FILE])
        self.assertNotIn(JP_LABOUR_RESULTS, [call[0] for call in session.calls])
        self.assertEqual((observation['value'], observation['reference_period'], observation['date']),
                         (2.5, '2026-08', '2026-08-31'))
        self.assertEqual(observation['series_id'], 'Historical1-a-1:unemployment_rate:BothSexes:SA:M')
        self.assertEqual(observation['seasonal_adjustment'], 'SA')
        self.assertEqual(observation['source_url'], JP_LABOUR_FILE)
        self.assertIn('e-Stat-Metadaten derselben Datei', observation['publication_basis'])
        public = public_observation(observation)
        self.assertEqual(public['publication_basis'], observation['publication_basis'])
        self.assertEqual(public['source_url'], JP_LABOUR_FILE)
        self.assertEqual(observation['release_date_known'], '2026-10-02')
        self.assertIsNone(observation['published_at'])
        self.assertEqual(observation['next_due_at'], '2026-10-29T15:00:00+00:00')
        self.assertEqual(observation['next_due_precision'], 'date_only_start_of_JP_day')

    def test_metadata_rejects_wrong_statistics_table_series_area_and_publisher(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        for key, value in [
            ('Statistics code', '00200573'), ('Statistics name', 'Consumer Price Index'),
            ('Dataset category1', 'Monthly results'), ('Dataset category2', 'Detailed tabulation'),
            ('Table number', '1-a-9'), ('Table category1', '[Monthly figures - Regional results] Seasonally adjusted series and Original series'),
            ('Table category1', '[Monthly figures - Results of whole Japan] Original series'),
            ('Statistical table name', 'Employed person [by age group]'),
            ('Tabulation area', 'Tokyo'), ('Publisher', 'Other publisher'),
        ]:
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'IDENTITY_INVALID'):
                parse_japan_labour_metadata(self.metadata({key: value}), self.current_calendar(), now=self.CURRENT_NOW)

    def test_metadata_rejects_wrong_file_id_kind_host_and_ambiguous_download(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        links = [
            '/en/stat-search/file-download?statInfId=000031831359&fileKind=0',
            '/en/stat-search/file-download?statInfId=000031831358&fileKind=2',
            'https://example.test/en/stat-search/file-download?statInfId=000031831358&fileKind=0',
            '/en/stat-search/file-download?statInfId=000031831358&fileKind=0&fileKind=2',
            '/en/stat-search/file-download?statInfId=000031831358&fileKind=0#other',
        ]
        for link in links:
            with self.subTest(link=link), self.assertRaisesRegex(ValueError, 'DOWNLOAD_MISMATCH'):
                parse_japan_labour_metadata(self.metadata(download=link), self.current_calendar(), now=self.CURRENT_NOW)
        extra = '<a class="stat-download_icon_top" href="/en/stat-search/file-download?statInfId=000031831359&amp;fileKind=0">EXCEL</a>'
        with self.assertRaisesRegex(ValueError, 'DOWNLOAD_AMBIGUOUS'):
            parse_japan_labour_metadata(self.metadata() + extra, self.current_calendar(), now=self.CURRENT_NOW)

    def test_metadata_rejects_duplicate_cards_and_fields_instead_of_first_match(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        data = self.metadata()
        duplicate = '<tr><th class="stat-resource_item">Survey date</th><td>2026 Jul.</td></tr>'
        for page in [data + data, data.replace('</table>', duplicate + '</table>', 1), '<html></html>']:
            with self.subTest(page=page[:80]), self.assertRaisesRegex(ValueError, 'METADATA_AMBIGUOUS'):
                parse_japan_labour_metadata(page, self.current_calendar(), now=self.CURRENT_NOW)

    def test_metadata_rejects_future_invalid_and_overdue_monthly_release(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        for changes, code in [
            ({'Published date and time': '2026-10-03 08:30'}, 'FUTURE_RELEASE'),
            ({'Published date and time': '2026-10-32 08:30'}, 'PUBLICATION_INVALID'),
            ({'Published date and time': '2026-08-31 08:30'}, 'PUBLICATION_INVALID'),
            ({'Survey date': '2026 Q2'}, 'PERIOD_INVALID'),
            ({'Survey date': '2026 Xxx.'}, 'PERIOD_INVALID'),
            ({'Survey date': '2026 Jul.', 'Published date and time': '2026-08-28 08:30'}, 'NEW_RELEASE_DUE'),
        ]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, code):
                parse_japan_labour_metadata(self.metadata(changes), self.current_calendar(), now=self.CURRENT_NOW)

    def test_metadata_calendar_conflicts_and_next_date_boundary(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        with self.assertRaisesRegex(ValueError, 'RELEASE_CALENDAR_CONFLICT'):
            parse_japan_labour_metadata(self.metadata(), self.current_calendar().replace('October 2</td>', 'October 3</td>'), now=self.CURRENT_NOW)
        before = datetime(2026, 10, 29, 14, 59, 59, tzinfo=timezone.utc)
        result = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=before)
        self.assertEqual(result['next_due_at'], '2026-10-29T15:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'NEW_RELEASE_DUE'):
            parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=datetime(2026, 10, 29, 15, tzinfo=timezone.utc))

    def test_current_metadata_cannot_qualify_missing_or_unconfirmed_workbook_month(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        with self.assertRaisesRegex(ValueError, 'LATEST_PERIOD_MISSING'):
            parse_japan_labour(self.book(), release, now=self.CURRENT_NOW)
        def future(s):
            s.cell(18, 20, 2.6)
            s.cell(19, 20, 2.7)
        with self.assertRaisesRegex(ValueError, 'UNCONFIRMED_PERIOD'):
            parse_japan_labour(self.book(future), release, now=self.CURRENT_NOW)

    def test_metadata_calendar_or_workbook_outage_never_returns_observation(self):
        from official_quarterly_labour import fetch_japan_labour
        from unittest.mock import Mock
        import requests
        for failed in range(3):
            for error in [requests.Timeout('source unavailable'), requests.HTTPError('source unavailable')]:
                session = Mock()
                responses = [Mock(text=self.metadata()), Mock(text=self.current_calendar())]
                session.get.side_effect = responses[:failed] + [error]
                with self.subTest(failed=failed, error=type(error).__name__), self.assertRaises(type(error)):
                    fetch_japan_labour(now=self.CURRENT_NOW, session=session)
                self.assertEqual(session.get.call_count, failed + 1)

    def test_invalid_metadata_never_falls_back_or_downloads_workbook(self):
        from official_quarterly_labour import fetch_japan_labour
        session = Session([Response(text=self.metadata({'Table number': '1-a-9'})),
                           Response(text=self.current_calendar())])
        with self.assertRaisesRegex(ValueError, 'IDENTITY_INVALID'):
            fetch_japan_labour(now=self.CURRENT_NOW, session=session)
        self.assertEqual(len(session.calls), 2)

    def test_workbook_title_and_sa_identity_are_exact_at_both_header_locations(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        def valid(s): s.cell(18, 20, 2.5)
        for column in (5, 14):
            for kind in ('table', 'frequency', 'nsa_title', 'sa'):
                def corrupt(s, column=column, kind=kind):
                    valid(s)
                    if kind == 'sa':
                        s.cell(5, column, 'Not Seasonally adjusted series: Original series')
                    else:
                        title = s.cell(2, column).value
                        title = (title.replace('1 a-1', '1 a-10') if kind == 'table' else
                                 title.replace('Monthly Data', 'Quarterly Data') if kind == 'frequency' else
                                 title + ' - Original series only')
                        s.cell(2, column, title)
                with self.subTest(column=column, kind=kind), self.assertRaisesRegex(ValueError, 'SERIES_IDENTITY_INVALID'):
                    parse_japan_labour(self.book(corrupt), release, now=self.CURRENT_NOW)
        def whitespace(s):
            valid(s)
            for column in (5, 14):
                s.cell(2, column, '\n  ' + s.cell(2, column).value.replace(' ', '  ') + '\n')
                s.cell(5, column, '季節調整値 \n Seasonally adjusted series')
        self.assertEqual(parse_japan_labour(self.book(whitespace), release, now=self.CURRENT_NOW)['value'], 2.5)

    def test_unrecognized_data_period_never_hides_duplicate_or_future_rows(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        for year, label in ((2026, '8月*'), (2027, '1月*'), (2026, 'August'), (2026, '')):
            for column in (5, 14, 20, 21, 22):
                for value in (9.9, '9.9', '9.9*', '9,9', '***', float('nan'), 0.0, True, False):
                    # XLSX cannot retain a NaN numeric cell; use string NaN
                    # to verify a non-finite observation is not skipped.
                    stored = 'NaN' if isinstance(value, float) and math.isnan(value) else value
                    def corrupt(s, year=year, label=label, column=column, stored=stored):
                        s.cell(18, 20, 2.5); s.cell(23, 1, year)
                        s.cell(23, 2, label); s.cell(23, column, stored)
                    with self.subTest(year=year, label=label, column=column, value=stored), self.assertRaisesRegex(ValueError, 'UNRECOGNIZED_DATA_PERIOD'):
                        parse_japan_labour(self.book(corrupt), release, now=self.CURRENT_NOW)

    def test_original_note_rows_and_blank_future_months_are_preserved(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        def notes(s):
            s.cell(18, 20, 2.5)
            for column in (5, 14):
                s.cell(23, column, '「※注_Notes」シートを参照')
                s.cell(24, column, 'Please refer to "※注_Notes" the sheet. ')
        observation = parse_japan_labour(self.book(notes), release, now=self.CURRENT_NOW)
        self.assertEqual((observation['value'], observation['reference_period']), (2.5, '2026-08'))
        self.assertIsNone(observation['published_at'])

    def test_only_exact_original_note_row_structure_is_ignored(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        for column, value in ((1, 2026), (2, '8月*'), (20, '***'), (20, False),
                              (5, 'Unknown note'), (14, 'Contradictory note')):
            def corrupt(s, column=column, value=value):
                s.cell(18, 20, 2.5)
                for note_column in (5, 14):
                    s.cell(23, note_column, '「※注_Notes」シートを参照')
                s.cell(23, column, value)
            with self.subTest(column=column, value=value), self.assertRaisesRegex(ValueError, 'UNRECOGNIZED_DATA_PERIOD'):
                parse_japan_labour(self.book(corrupt), release, now=self.CURRENT_NOW)

    def test_calendar_nonmonotonic_dates_and_any_newer_due_period_are_rejected(self):
        from official_quarterly_labour import parse_japan_labour_metadata
        calendar = self.current_calendar().replace('October 30</td>', 'November 5</td>').replace('December 1</td>', 'November 1</td>')
        # Even before either new date arrives, the inverted release order is
        # a contract conflict, not permission to extend August's deadline.
        for now in (self.CURRENT_NOW, datetime(2026, 11, 2, 12, tzinfo=timezone.utc)):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, 'CALENDAR_CONFLICT'):
                parse_japan_labour_metadata(self.metadata(), calendar, now=now)

    def test_current_revision_is_allowed_but_missing_duplicate_future_months_are_not(self):
        from official_quarterly_labour import parse_japan_labour, parse_japan_labour_metadata
        release = parse_japan_labour_metadata(self.metadata(), self.current_calendar(), now=self.CURRENT_NOW)
        revised = parse_japan_labour(self.book(lambda s: s.cell(18, 20, 2.6)), release, now=self.CURRENT_NOW)
        self.assertEqual((revised['value'], revised['reference_period']), (2.6, '2026-08'))
        self.assertIsNone(revised['published_at'])
        def missing(s): s.cell(18, 20, None)
        def duplicate(s): s.cell(18, 20, 2.5); s.cell(19, 2, '8月')
        def future(s): s.cell(18, 20, 2.5); s.cell(19, 20, 2.7)
        for mutate in (missing, duplicate, future):
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                parse_japan_labour(self.book(mutate), release, now=self.CURRENT_NOW)


if __name__ == '__main__': unittest.main()
