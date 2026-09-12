import csv
import io
import unittest
from datetime import datetime, timezone
from bis_research import parse_csv

NOW=datetime(2026,9,12,tzinfo=timezone.utc)
ROW={'FREQ':'D','REF_AREA':'NZ','UNIT_MEASURE':'368','UNIT_MULT':'0','COMPILATION':'From 17 Mar 1999 onwards: Official cash rate;','SOURCE_REF':'Reserve Bank of New Zealand','TIME_PERIOD':'2026-09-10','OBS_VALUE':'2.5','OBS_STATUS':'A','OBS_CONF':'F'}
def payload(rows):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=list(ROW));w.writeheader();w.writerows(rows);return s.getvalue()
class TestBIS(unittest.TestCase):
    def test_valid_never_core(self):
        r=parse_csv(payload([ROW]),retrieved_at=NOW)
        self.assertEqual(r['value'],2.5);self.assertFalse(r['core_eligible']);self.assertIsNone(r['published_at']);self.assertIsNone(r['rate_effective_date'])
    def test_wrong_contract(self):
        for k,v in [('FREQ','M'),('REF_AREA','AU'),('UNIT_MEASURE','USD'),('UNIT_MULT','2'),('SOURCE_REF','Other'),('COMPILATION','overnight interbank rate'),('OBS_CONF','C')]:
            with self.subTest(k=k),self.assertRaises(ValueError):parse_csv(payload([ROW|{k:v}]),retrieved_at=NOW)
    def test_bad_dates(self):
        for d in ('2026-09-13','1998-01-01','not-a-date'):
            with self.subTest(d=d),self.assertRaises(ValueError):parse_csv(payload([ROW|{'TIME_PERIOD':d}]),retrieved_at=NOW)
    def test_bad_values(self):
        for v in ('NaN','inf','garbage','1000'):
            with self.subTest(v=v),self.assertRaises(ValueError):parse_csv(payload([ROW|{'OBS_VALUE':v}]),retrieved_at=NOW)
    def test_missing_not_filled(self):
        r=parse_csv(payload([ROW,ROW|{'TIME_PERIOD':'2026-09-11','OBS_VALUE':'NaN','OBS_STATUS':'M'}]),retrieved_at=NOW)
        self.assertEqual(r['observation_date'],'2026-09-10');self.assertEqual(len(r['observations']),1)
    def test_no_usable_or_conflicting_missing(self):
        for v in ('NaN','2.5'):
            with self.subTest(v=v),self.assertRaises(ValueError):parse_csv(payload([ROW|{'OBS_VALUE':v,'OBS_STATUS':'M'}]),retrieved_at=NOW)
    def test_schema_duplicate_status(self):
        for text in ('<html>Error</html>',payload([ROW,ROW]),payload([ROW|{'OBS_STATUS':'E'}]),payload([])):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_csv(text,retrieved_at=NOW)
    def test_old_data_warned(self):
        r=parse_csv(payload([ROW|{'TIME_PERIOD':'2026-08-01'}]),retrieved_at=NOW)
        self.assertEqual(r['research_status'],'historical_observation_stale');self.assertFalse(r['core_eligible'])
    def test_naive_timestamp(self):
        with self.assertRaises(ValueError):parse_csv(payload([ROW]),retrieved_at=NOW.replace(tzinfo=None))
if __name__=='__main__':unittest.main()
