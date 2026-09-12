"""Synthetic research snapshots; no trade accuracy or historical-release claims."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import research_vintages as journal
from test_research_panel import artifact as bis_fixture
from test_ec_industry_research import fixture as ec_fixture, saved_artifact

BASE = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


def bis(value=2, older=1, offset=0):
    data = bis_fixture()
    data['retrieved_at'] = (BASE + timedelta(seconds=offset)).isoformat()
    data['value'] = value
    data['observations'] = [dict(observation_date='2026-09-03', value=older),
                            dict(observation_date='2026-09-04', value=value)]
    data['missing_observation_dates'] = ['2026-09-05']
    return data


def ec(value=-2, older=-1, offset=0):
    source = ec_fixture()
    source['value'] = {'0': older, '1': value}
    return saved_artifact(source, now=BASE + timedelta(seconds=offset))


class VintageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'bis_nz_policy_vintages.json'
        self.clock = patch('research_vintages._now', return_value=BASE + timedelta(days=20))
        self.clock.start(); self.addCleanup(self.clock.stop)

    def append(self, data=None, offset=1, anchor=None):
        return journal.append_vintage(self.path, 'bis_nz_policy', data or bis(), BASE+timedelta(seconds=offset), anchor)

    def load(self):
        return json.loads(self.path.read_text())

    def anchor(self, head):
        return {key: head[key] for key in ('sequence', 'event_hash')}

    def test_genesis_post_fetch_time_and_allowlist(self):
        self.assertEqual(journal.inspect_vintage(self.path, 'bis_nz_policy')['event_count'], 0)
        data = bis(); data['private_token'] = 'DO_NOT_PUBLISH'; data['source'] = 'untrusted secret'
        head = self.append(data)
        event = self.load()['events'][0]
        self.assertEqual(event['first_observed_at'], (BASE+timedelta(seconds=1)).isoformat())
        self.assertEqual(event['request_started_at'], BASE.isoformat())
        self.assertIsNone(event['published_at'])
        self.assertNotIn('DO_NOT_PUBLISH', self.path.read_text())
        self.assertNotIn('untrusted secret', self.path.read_text())
        self.assertEqual(head['sequence'], 1)
        self.assertEqual(event['snapshot']['observations'][-1], {'period': '2026-09-05', 'status': 'M', 'value': None})

    def test_equivalent_numbers_order_and_provenance_do_not_append(self):
        self.append()
        original = self.path.read_bytes()
        data = bis(value=2.0, older=1.0, offset=2)
        data['observations'].reverse()
        data['payload_sha256'] = 'b'*64
        data['observation_age_days'] = 999
        head = self.append(data, offset=3)
        self.assertEqual(head['event_count'], 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(journal._number(-0.0, 'bis_nz_policy'), 0)

    def test_reversion_and_older_revision_preserved(self):
        self.append(bis())
        self.append(bis(value=3, offset=2), offset=3)
        self.append(bis(offset=4), offset=5)
        self.append(bis(older=1.5, offset=6), offset=7)
        events = self.load()['events']
        self.assertEqual([e['sequence'] for e in events], [1,2,3,4])
        self.assertEqual(events[0]['state_sha256'], events[2]['state_sha256'])
        self.assertNotEqual(events[2]['state_sha256'], events[3]['state_sha256'])
        self.assertEqual(journal.inspect_vintage(self.path, 'bis_nz_policy')['event_count'], 4)

    def test_anchor_missing_truncation_and_valid_orphan_recovery(self):
        first = self.append()
        first_bytes = self.path.read_bytes()
        second = self.append(bis(value=3, offset=2), offset=3, anchor=self.anchor(first))
        head = journal.inspect_vintage(self.path, 'bis_nz_policy', self.anchor(first))
        self.assertEqual(head['event_count'], 2)
        self.assertEqual(self.append(bis(value=3, offset=4), offset=5, anchor=self.anchor(first))['event_count'], 2)
        self.path.write_bytes(first_bytes)
        with self.assertRaises(ValueError): journal.inspect_vintage(self.path, 'bis_nz_policy', self.anchor(second))
        self.path.unlink()
        with self.assertRaises(ValueError): journal.inspect_vintage(self.path, 'bis_nz_policy', self.anchor(first))

    def test_corrupt_unknown_fields_hash_edits_preserve_bytes(self):
        self.append()
        original = self.path.read_text()
        changes = [lambda j: j.update(secret='x'),
                   lambda j: j['events'][0].update(response_sha256='f'*64),
                   lambda j: j['events'][0].update(state_sha256='f'*64),
                   lambda j: j['events'][0].update(previous_event_hash='f'*64),
                   lambda j: j['events'][0]['snapshot']['observations'][0].update(value=True),
                   lambda j: j.update(events=[])]
        corruptions = ['', '{', original.replace('"schema":', '"schema":"duplicate","schema":', 1)]
        for change in changes:
            data = json.loads(original); change(data); corruptions.append(json.dumps(data))
        for bad in corruptions:
            self.path.write_text(bad); prior = self.path.read_bytes()
            with self.assertRaises((ValueError, TypeError, KeyError)):
                self.append(bis(offset=2), offset=3)
            self.assertEqual(prior, self.path.read_bytes())

    def test_rehashed_unknown_snapshot_field_rejected(self):
        self.append()
        data = self.load(); event = data['events'][0]
        event['snapshot']['observations'][0]['unexpected'] = 'secret'
        event['state_sha256'] = journal._digest(event['snapshot'])
        event['event_hash'] = journal._digest({k:v for k,v in event.items() if k != 'event_hash'})
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): journal.inspect_vintage(self.path, 'bis_nz_policy')

    def test_invalid_artifact_dates_definitions_and_numbers(self):
        for field, value in [('unit','basis_points'), ('core_eligible',True), ('value',True),
                             ('value',float('nan')), ('value',float('inf')),
                             ('missing_observation_dates',['2026-09-13']),
                             ('missing_observation_dates',['2026-09-03'])]:
            data=bis(); data[field]=value
            with self.assertRaises((ValueError,TypeError,KeyError)): self.append(data)
            self.assertFalse(self.path.exists())

    def test_chronology_and_atomic_failure_preserve_bytes(self):
        self.append()
        original=self.path.read_bytes()
        for observed in [BASE.replace(tzinfo=None), BASE-timedelta(seconds=1), BASE+timedelta(days=30)]:
            with self.assertRaises(ValueError): journal.append_vintage(self.path,'bis_nz_policy',bis(value=3),observed)
        with self.assertRaises(ValueError): self.append(bis(value=3),offset=1)
        with patch('research_vintages.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.append(bis(value=3,offset=2),offset=3)
        self.assertEqual(original,self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_ec_older_revision_update_nochange_and_missing_periods(self):
        path = self.path.with_name('ec_industry_vintages.json')
        data=ec()
        journal.append_vintage(path,'ec_industry',data,BASE+timedelta(seconds=1))
        changed=ec(offset=2)
        changed['dataset_updated_at']='2026-09-01T00:00:00+00:00'
        changed['source_snapshot']['updated']=changed['dataset_updated_at']
        head=journal.append_vintage(path,'ec_industry',changed,BASE+timedelta(seconds=3))
        self.assertEqual(head['event_count'],1)
        head=journal.append_vintage(path,'ec_industry',ec(older=-1.5,offset=4),BASE+timedelta(seconds=5))
        self.assertEqual(head['event_count'],2)
        source=ec_fixture(); source['value']['1']=None
        missing=saved_artifact(source,now=BASE+timedelta(seconds=6))
        journal.append_vintage(path,'ec_industry',missing,BASE+timedelta(seconds=7))
        last=json.loads(path.read_text())['events'][-1]
        self.assertIsNone(last['snapshot']['observations'][-1]['value'])
        source['dimension']['time']['category']['index']={'2026-06':0,'2026-08':1}
        absent=saved_artifact(source,now=BASE+timedelta(seconds=8))
        journal.append_vintage(path,'ec_industry',absent,BASE+timedelta(seconds=9))
        self.assertEqual(json.loads(path.read_text())['events'][-1]['snapshot']['absent_calendar_periods'],['2026-07'])

    def test_wrong_source_and_fixed_path_rejected(self):
        self.append()
        with self.assertRaises(ValueError): journal.inspect_vintage(self.path,'ec_industry')
        with self.assertRaises(ValueError): journal.append_vintage(self.path.with_name('arbitrary.json'),'bis_nz_policy',bis(),BASE)


if __name__ == '__main__': unittest.main()
