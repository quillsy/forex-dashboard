import csv
import html
import io
import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from official_macro import parse_nz_gdp, discover_nz_gdp_release, fetch_nz_gdp, NZ_GDP_HEADER as HEADER

NOW = datetime(2026,9,12,tzinfo=timezone.utc)
PAGE = {'Title': 'Gross domestic product: March 2026 quarter',
        'DateTaxonomyTerm': {'PublicationDate': '2026-06-18 10:45:00'},
        'Body': 'Gross domestic product: June 2026 quarter will be released on 17 September 2026',
        'Downloads': [{'FileName': 'gross-domestic-product-march-2026-quarter.csv',
                       'DocumentExtension': 'csv',
                       'DocumentLink': '/assets/Uploads/Gross-domestic-product/Gross-domestic-product-March-2026-quarter/Download-data/gross-domestic-product-march-2026-quarter.csv'}]}
ROWS = []
# Small deterministic same-vintage fixture; no downloaded provider file dependency.
for period, value in [('2025.03',72159),('2025.06',71907),('2025.09',72694),('2025.12',72963),('2026.03',73699)]:
    row = dict.fromkeys(HEADER, '')
    row.update(Series_reference='SNEQ.SG02RSC00B15', Period=period, Data_value=str(value),
               STATUS='FINAL' if period=='2026.03' else 'REVISED', UNITS='Dollars', MAGNITUDE='6',
               Subject='National Accounts - SNA 2008 - SNE',
               Group='Series, GDP(E), Chain volume, Seasonally adjusted, Total',
               Series_title_1='Gross Domestic Product - expenditure measure')
    ROWS.append(row)
def page(p): return '<div data-value="'+html.escape(json.dumps(p),quote=True)+'"></div>'
def csv_text(rows):
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=HEADER);w.writeheader();w.writerows(rows);return out.getvalue()
def parse(rows=None,p=None,now=NOW):
    return parse_nz_gdp(csv_text(ROWS if rows is None else rows),page(PAGE if p is None else p),year=2026,month=3,now=now)

class ProposalTests(unittest.TestCase):
    def test_real_excerpt(self):
        r=parse();self.assertAlmostEqual(r['value'],2.134175917072012);self.assertEqual(r['comparison_period_status'],'REVISED')
        self.assertEqual(r['next_due_at'],'2026-09-16T12:00:00+00:00');self.assertEqual(r['published_at'],'2026-06-17T22:45:00+00:00')
    def test_wrong_definition(self):
        for key,value in [('UNITS','Percent'),('MAGNITUDE','0'),('Series_title_1','Gross Domestic Product - production measure'),('Group','per capita'),('STATUS','PROVISIONAL'),('Data_value','nan'),('Data_value','0')]:
            with self.subTest(key=key,value=value):
                rows=deepcopy(ROWS);rows[-1][key]=value
                with self.assertRaises(ValueError):parse(rows)
    def test_missing_quarter(self):
        for i in range(5):
            with self.subTest(i=i),self.assertRaises(ValueError):parse(ROWS[:i]+ROWS[i+1:])
    def test_duplicate_quarter(self):
        with self.assertRaises(ValueError):parse(ROWS+[ROWS[-1]])
    def test_future_csv(self):
        rows=deepcopy(ROWS);rows[-1]['Period']='2026.06'
        with self.assertRaises(ValueError):parse(rows)
    def test_other_series_not_proxy(self):
        rows=deepcopy(ROWS)
        for row in rows:row['Series_reference']='SNEQ.SG01RSC00B15'
        with self.assertRaises(ValueError):parse(rows)
    def test_future_publication(self):
        with self.assertRaises(ValueError):parse(now=datetime(2026,6,17,tzinfo=timezone.utc))
    def test_due_release_rejects_old(self):
        with self.assertRaisesRegex(ValueError,'NEW_RELEASE_DUE'):parse(now=datetime(2026,9,16,12,tzinfo=timezone.utc))
    def test_wrong_release(self):
        p=deepcopy(PAGE);p['Title']='Gross domestic product: June 2026 quarter'
        with self.assertRaises(ValueError):parse(p=p)
    def test_no_calendar_hourly_required(self):
        def replace(x):
            if isinstance(x,dict):return {k:replace(v) for k,v in x.items()}
            if isinstance(x,list):return [replace(v) for v in x]
            if isinstance(x,str):return x.replace('will be released on','calendar absent')
            return x
        r=parse(p=replace(PAGE));self.assertIsNone(r['next_due_at']);self.assertTrue(r['needs_hourly_check'])
    def test_dynamic_topic(self):
        y,m,url=discover_nz_gdp_release(page({'Title': PAGE['Title'], 'PageLink': '/information-releases/gross-domestic-product-march-2026-quarter/'}));self.assertEqual((y,m),(2026,3));self.assertTrue(url.endswith('march-2026-quarter/'))
    def test_external_topic_link_rejected(self):
        p={'Title':'Gross domestic product: March 2026 quarter','Link':'https://example.org/'}
        with self.assertRaises(ValueError):discover_nz_gdp_release(page(p))
    def test_three_requests(self):
        class Response:
            def __init__(self,text):self.text=text
            def raise_for_status(self):pass
        class Session:
            def __init__(self):self.calls=[]
            def get(self,url,timeout):
                self.calls.append(url)
                return Response([page({'Title': PAGE['Title'], 'PageLink': '/information-releases/gross-domestic-product-march-2026-quarter/'}),page(PAGE),csv_text(ROWS)][len(self.calls)-1])
        s=Session();self.assertAlmostEqual(fetch_nz_gdp(now=NOW,session=s)['value'],parse()['value']);self.assertEqual(len(s.calls),3)

    def test_public_projection_and_date_only_label(self):
        import live_data
        from unittest.mock import MagicMock, patch
        observation=parse()
        public=live_data.public_observation(observation)
        self.assertEqual(public['source_url'], observation['source_url'])
        self.assertEqual(public['comparison_period_status'],'REVISED')
        self.assertEqual(public['license'],'CC BY 4.0; Stats NZ')
        for suffix in ('?key=secret', '.evil.org', '/extra'):
            self.assertNotIn('source_url', live_data.public_observation({'source_url':observation['source_url']+suffix}))
        row=live_data.build_record('GDP', 10, observation, 'AGING', NOW.isoformat())
        self.assertTrue(live_data.record_not_due(row,'NZD','GDP',NOW))
        self.assertFalse(live_data.eligible(row,datetime(2026,9,16,12,tzinfo=timezone.utc),factor='GDP',currency='NZD')[0])
        st=MagicMock()
        with patch.object(live_data,'load',return_value={'currencies':{'NZD':{'GDP':row}}}):
            live_data.render_status(st)
        rows=[x for call in st.dataframe.call_args_list for x in call.args[0] if isinstance(x,dict)]
        target=next(x for x in rows if x.get('Währung')=='NZD' and x.get('Faktor')=='GDP')
        self.assertIn('Neuseeland',target['Nächste Fälligkeit'])
        self.assertIn('unbekannt',target['Nächste Fälligkeit'])

if __name__=='__main__': unittest.main()
