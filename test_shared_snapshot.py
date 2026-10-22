"""Offline transport/atomicity tests; synthetic completed batches, no provider calls."""
import copy
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import live_data
import shared_snapshot as shared

NOW = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
COMMIT = 'a' * 40


def fixture(completed=NOW - timedelta(minutes=1)):
    at = completed.isoformat()
    policies = {c: {'currency': c, 'rate': None, 'verification_status': 'UNAVAILABLE', 'verification_evidence': []} for c in live_data.CURRENCIES}
    core = {'model_version': live_data.MODEL, 'last_attempt_at': at, 'completed_at': at, 'status': 'FAILED', 'eligible_factors': 0,
            'currencies': {c: {f: {'score': None, 'validation': 'UNVERIFIED', 'last_attempt_at': at, 'observation': {}} for f in live_data.FACTORS} for c in live_data.CURRENCIES}}
    status = {'last_run_timestamp': at, 'last_run_status': 'FAILED', 'mode': 'live', 'components': {
        'live_core': {'status': 'FAILED', 'eligible_factors': 0, 'total_factors': 40, 'completed_at': at},
        'policy_rates': {'status': 'FAILED', 'currencies': {c: 'UNAVAILABLE' for c in live_data.CURRENCIES}}}}
    return dict(zip(shared.FILES, [core, status, policies]))


def frontier(currency='AUD', factor='Arbeitsmarkt'):
    source, series_id = shared.FRONTIER_SERIES[(currency, factor)]
    if (currency, factor) == ('EUR', 'GDP'):
        return {'source': source, 'series_id': series_id, 'date': '2025-12-31',
                'latest_date': '2025-12-31', 'next_due_at': None, 'next_due_precision': None}
    return {'source': source, 'series_id': series_id, 'date': '2026-01-01',
            'latest_date': '2026-01-01', 'next_due_at': '2026-01-09T00:00:00+00:00',
            'next_due_precision': 'official_scheduled_time'}


class Response:
    def __init__(self, payload=b'', status=200, headers=None):
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
    def iter_content(self, chunk_size):
        for index in range(0, len(self.raw), chunk_size):
            yield self.raw[index:index + chunk_size]
    def close(self):
        self.closed = True


class Client:
    def __init__(self, docs=None, commit=COMMIT):
        self.responses = [Response({'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': commit}})]
        self.responses += [Response((docs or fixture())[name]) for name in shared.FILES]
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SharedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN'))
        self.guard.start()
    def tearDown(self):
        self.guard.stop(); self.temp.cleanup()
    def refresh(self, client=None, now=NOW):
        return shared.refresh_shared_snapshot(now=now, root=self.root, client=client or Client())
    def test_eurostat_period_only_frontier_is_strictly_bound_and_has_no_invented_due(self):
        docs=fixture()
        row=docs[shared.FILES[0]]['currencies']['EUR']['GDP']
        row['release_frontier']=frontier('EUR','GDP')
        self.assertEqual(shared._validate(docs,NOW),shared._time(docs[shared.FILES[0]]['completed_at']))
        changes=({'date':'2025-12-30'}, {'latest_date':'2026-01-01'},
                 {'next_due_precision':'official_scheduled_time'}, {'series_id':'other'},
                 {'next_due_at':'2026-02-01T00:00:00+00:00'},
                 {'next_due_at':'2026-02-01T00:00:00+00:00','next_due_precision':'official_scheduled_time'},
                 {'next_due_at':'invalid'}, {'private_key':'secret'})
        for change in changes:
            with self.subTest(change=change):
                bad=copy.deepcopy(docs)
                bad[shared.FILES[0]]['currencies']['EUR']['GDP']['release_frontier'].update(change)
                with self.assertRaises((shared.Invalid,ValueError)): shared._validate(bad,NOW)
        for currency,factor in (('AUD','GDP'),('NZD','GDP')):
            bad=fixture(); f=frontier(currency,factor); f.update(next_due_at=None,next_due_precision=None)
            bad[shared.FILES[0]]['currencies'][currency][factor]['release_frontier']=f
            with self.assertRaises((shared.Invalid,ValueError)): shared._validate(bad,NOW)

    def test_eurostat_record_and_observation_reject_invented_calendar_in_both_transports(self):
        for layer,fields in (('record',{'next_due_at':'2026-02-01T00:00:00+00:00'}),
                ('observation',{'next_due_at':'2026-02-01T00:00:00+00:00'}),
                ('observation',{'next_due_precision':'official_scheduled_time'}),
                ('observation',{'next_due_at':'2026-02-01T00:00:00+00:00',
                                'next_due_precision':'official_scheduled_time'})):
            with self.subTest(layer=layer,fields=fields):
                docs=fixture(); row=docs[shared.FILES[0]]['currencies']['EUR']['GDP']
                (row if layer=='record' else row['observation']).update(fields)
                # No valid observation is needed to reject invented public metadata.
                with self.assertRaises(shared.Invalid): shared._validate(docs,NOW)
                with tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp)
                    for name,document in docs.items():
                        (root/name).write_text(json.dumps(document))
                    self.assertIsNone(shared.bundled_snapshot_directory(root,root/'runtime'))
                with tempfile.TemporaryDirectory() as tmp:
                    result=shared.refresh_shared_snapshot(now=NOW,root=Path(tmp),client=Client(docs))
                    self.assertNotEqual(result['outcome'],'UPDATED')

    def test_release_frontiers_survive_public_and_bundled_invalid_records(self):
        docs = fixture()
        for currency, factor in shared.FRONTIER_SERIES:
            docs[shared.FILES[0]]['currencies'][currency][factor]['release_frontier'] = frontier(currency, factor)
        self.assertEqual(self.refresh(Client(docs))['outcome'], 'UPDATED')
        public = shared.current_shared_directory(self.root)
        self.assertEqual(json.loads((public / shared.FILES[0]).read_text()), docs[shared.FILES[0]])
        source = self.root / 'source'; source.mkdir()
        for name, value in docs.items(): (source / name).write_text(json.dumps(value))
        with patch.object(shared.requests, 'Session', side_effect=AssertionError('NO_HTTP')):
            bundled = shared.bundled_snapshot_directory(source, self.root)
        self.assertIsNotNone(bundled)
        self.assertEqual(json.loads((bundled / shared.FILES[0]).read_text()), docs[shared.FILES[0]])

    def test_release_frontier_blocks_old_observation_even_without_other_due(self):
        docs = fixture()
        core, status = docs[shared.FILES[0]], docs[shared.FILES[1]]
        at = core['completed_at']
        row = {'factor': 'Arbeitsmarkt', 'score': 20, 'validation': 'VALID',
               'freshness': 'FRESH', 'checked_at': at, 'last_attempt_at': at,
               'published_at': None, 'next_due_at': None,
               'expires_at': '2026-04-01T00:00:00+00:00',
               'observation': {'date': '2026-01-01', 'value': 4.0,
                               'unit': 'percent of labour force', 'source': 'ABS',
                               'series_id': 'LF:M13.3.1599.20.AUS.M'},
               'release_frontier': frontier()}
        core['currencies']['AUD']['Arbeitsmarkt'] = row
        self.assertFalse(live_data.eligible(row, shared._time(at), 'Arbeitsmarkt', 'AUD')[0])
        self.assertEqual(shared._validate(docs, NOW), shared._time(at))
        row['observation']['date'] = '2026-01-02'
        row['release_frontier']['latest_date'] = '2026-01-02'
        self.assertTrue(live_data.eligible(row, shared._time(at), 'Arbeitsmarkt', 'AUD')[0])
        core.update(status='PARTIAL', eligible_factors=1)
        status['last_run_status'] = 'PARTIAL'
        status['components']['live_core'].update(status='PARTIAL', eligible_factors=1)
        self.assertEqual(shared._validate(docs, NOW), shared._time(at))

    def test_release_frontier_exact_public_schema_and_identity(self):
        docs = fixture()
        core = docs[shared.FILES[0]]
        core['currencies']['AUD']['Arbeitsmarkt']['release_frontier'] = frontier()
        self.assertEqual(shared._validate(docs, NOW), shared._time(core['completed_at']))
        changes = {
            'private field': lambda d: d['release_frontier'].update(private_key='secret'),
            'nested field': lambda d: d['release_frontier'].update(source={'private': 'secret'}),
            'wrong source': lambda d: d['release_frontier'].update(source='Other'),
            'wrong series': lambda d: d['release_frontier'].update(series_id='OTHER'),
            'missing latest': lambda d: d['release_frontier'].pop('latest_date'),
            'malformed reference': lambda d: d['release_frontier'].update(date='2026-01-99'),
            'non-ISO reference': lambda d: d['release_frontier'].update(date='2026-1-1'),
            'future reference': lambda d: d['release_frontier'].update(date='2026-01-11', latest_date='2026-01-11'),
            'future latest': lambda d: d['release_frontier'].update(latest_date='2026-01-11'),
            'latest before reference': lambda d: d['release_frontier'].update(date='2026-01-02'),
            'naive due': lambda d: d['release_frontier'].update(next_due_at='2026-01-09T00:00:00'),
            'non-UTC due': lambda d: d['release_frontier'].update(next_due_at='2026-01-09T01:00:00+01:00'),
            'unknown precision': lambda d: d['release_frontier'].update(next_due_precision='secret'),
            'wrong currency': lambda d: d['currencies']['NZD'].__setitem__('Arbeitsmarkt', d['currencies']['AUD']['Arbeitsmarkt']),
            'wrong factor': lambda d: d['currencies']['AUD'].__setitem__('GDP', d['currencies']['AUD']['Arbeitsmarkt']),
        }
        for label, mutation in changes.items():
            with self.subTest(label=label):
                bad = copy.deepcopy(docs)
                mutation(bad[shared.FILES[0]] if label in {'wrong currency', 'wrong factor'} else bad[shared.FILES[0]]['currencies']['AUD']['Arbeitsmarkt'])
                with self.assertRaises((shared.Invalid, ValueError)):
                    shared._validate(bad, NOW)
        bad = copy.deepcopy(docs)
        bad[shared.FILES[0]]['currencies']['AUD']['Arbeitsmarkt']['release_frontier']['private_key'] = 'secret'
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(shared.refresh_shared_snapshot(NOW, root, Client(bad)), {'outcome': 'UNAVAILABLE'})
            self.assertIsNone(shared.current_shared_directory(root))
    def test_exact_pinned_urls_transport_and_unchanged_bytes(self):
        docs = fixture(); client = Client(docs)
        self.assertEqual(self.refresh(client)['outcome'], 'UPDATED')
        path = shared.current_shared_directory(self.root)
        self.assertEqual(path.name, COMMIT)
        for name in shared.FILES:
            self.assertEqual(json.loads((path / name).read_text()), docs[name])
        self.assertEqual([u for u, _ in client.calls], [shared.REF_URL] + [shared.RAW_BASE + COMMIT + '/' + n for n in shared.FILES])
        for _, kw in client.calls:
            self.assertFalse(kw['allow_redirects']); self.assertTrue(kw['stream']); self.assertTrue(kw['verify'])
    def test_persistent_poll_and_sha_change_rollback(self):
        self.assertEqual(self.refresh()['outcome'], 'UPDATED')
        client = Client(); self.assertEqual(self.refresh(client, NOW + timedelta(seconds=299))['outcome'], 'THROTTLED'); self.assertEqual(client.calls, [])
        client = Client(); self.assertEqual(self.refresh(client, NOW + timedelta(minutes=5))['outcome'], 'UNCHANGED'); self.assertEqual(len(client.calls), 1)
        client = Client(commit='b' * 40); self.assertEqual(self.refresh(client, NOW + timedelta(minutes=10))['outcome'], 'OLDER')
        newer = fixture(NOW + timedelta(minutes=14)); self.assertEqual(self.refresh(Client(newer, 'c' * 40), NOW + timedelta(minutes=15))['outcome'], 'UPDATED')
        self.assertEqual(shared.current_shared_directory(self.root).name, 'c' * 40)
    def test_schema_mutations_fail_closed(self):
        mutations = [lambda d: d[shared.FILES[0]].update(model_version='OTHER'),
                     lambda d: d[shared.FILES[0]].update(completed_at=(NOW + timedelta(days=1)).isoformat()),
                     lambda d: d[shared.FILES[0]]['currencies']['JPY'].pop('PMI'),
                     lambda d: d[shared.FILES[0]].update(private_key='secret'),
                     lambda d: d[shared.FILES[1]]['components']['live_core'].update(eligible_factors=1),
                     lambda d: d[shared.FILES[2]]['JPY'].update(private_key='secret')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                docs = fixture(); mutation(docs)
                with tempfile.TemporaryDirectory() as root:
                    result = shared.refresh_shared_snapshot(NOW, root, Client(docs))
                    self.assertEqual(result, {'outcome': 'UNAVAILABLE'})
                    self.assertIsNone(shared.current_shared_directory(root))
    def test_duplicate_json_nan_oversize_redirect_auth(self):
        for response in [Response(b'{"x":1,"x":2}'), Response(b'{"x":NaN}'), Response(b'x' * (shared.MAX_BYTES + 1)), Response(status=302), Response(status=401)]:
            with tempfile.TemporaryDirectory() as root:
                client = Client(); client.responses[0] = response
                self.assertEqual(shared.refresh_shared_snapshot(NOW, root, client)['outcome'], 'UNAVAILABLE')
                self.assertEqual(len(client.calls), 1)
                self.assertTrue(response.closed)
    def test_rate_limits_persist_header_deadline(self):
        for status in (403, 429):
            with tempfile.TemporaryDirectory() as root:
                client = Client(); client.responses[0] = Response(status=status, headers={'Retry-After': '900', 'X-RateLimit-Reset': str(int(NOW.timestamp()) + 1200)})
                self.assertEqual(shared.refresh_shared_snapshot(NOW, root, client)['outcome'], 'RATE_LIMITED')
                other = Client(); self.assertEqual(shared.refresh_shared_snapshot(NOW + timedelta(minutes=19), root, other)['outcome'], 'THROTTLED'); self.assertFalse(other.calls)
                state = json.loads((Path(root) / '.shared-poll.json').read_text()); self.assertEqual(state['next_poll_at'], (NOW + timedelta(minutes=20)).isoformat())
    def test_corrupt_and_future_markers_never_call(self):
        for raw in ('bad', json.dumps({'attempted_at': (NOW + timedelta(minutes=1)).isoformat(), 'next_poll_at': (NOW + timedelta(minutes=6)).isoformat()})):
            (self.root / '.shared-poll.json').write_text(raw)
            client = Client(); self.assertEqual(self.refresh(client)['outcome'], 'INVALID_STATE'); self.assertFalse(client.calls)
    def test_failed_download_preserves_old_generation(self):
        self.refresh(); original = (self.root / '.shared-current.json').read_bytes()
        for index in range(4):
            client = Client(fixture(NOW + timedelta(minutes=4)), 'b' * 40); client.responses[index] = Response(status=500)
            self.refresh(client, NOW + timedelta(minutes=5 * (index + 1)))
            self.assertEqual((self.root / '.shared-current.json').read_bytes(), original)
    def test_hash_tampering_and_path_traversal_fail(self):
        self.refresh(); path = shared.current_shared_directory(self.root)
        (path / shared.FILES[0]).write_text('{}'); self.assertIsNone(shared.current_shared_directory(self.root))
        (self.root / '.shared-current.json').write_text(json.dumps({'commit': '../outside', 'completed_at': NOW.isoformat()}))
        self.assertIsNone(shared.current_shared_directory(self.root))
    def test_poll_reservation_precedes_get_and_concurrent_call(self):
        entered = threading.Event(); proceed = threading.Event(); client = Client(); original = client.get
        def get(url, **kw):
            self.assertTrue((self.root / '.shared-poll.json').exists())
            entered.set(); proceed.wait(3)
            return original(url, **kw)
        client.get = get
        result = []; thread = threading.Thread(target=lambda: result.append(self.refresh(client))); thread.start()
        self.assertTrue(entered.wait(3)); second = Client(); self.assertEqual(self.refresh(second)['outcome'], 'BUSY'); self.assertFalse(second.calls)
        proceed.set(); thread.join(3); self.assertEqual(result[0]['outcome'], 'UPDATED')
    def test_crash_before_pointer_recovers_same_immutable_generation(self):
        real = shared._atomic
        def crash(path, value):
            if path.name == '.shared-current.json':
                raise OSError('simulated')
            real(path, value)
        with patch.object(shared, '_atomic', side_effect=crash):
            self.assertEqual(self.refresh()['outcome'], 'UNAVAILABLE')
        self.assertIsNone(shared.current_shared_directory(self.root))
        self.assertEqual(self.refresh(now=NOW + timedelta(minutes=5))['outcome'], 'UPDATED')
    def test_bound_daily_summaries_and_unknown_nested_fields(self):
        docs = fixture(); status = docs[shared.FILES[1]]
        status['mode'] = 'daily'
        status['last_run_status'] = 'PARTIAL'
        status['components']['snapshots'] = {'status': 'SUCCESS', 'attempted': 0, 'written': 0, 'updated': 0, 'skipped': 0, 'errors': 0, 'issues': [], 'currencies': {'status': 'SUCCESS'}, 'pairs': {'status': 'SUCCESS'}}
        status['components']['outcomes'] = {'status': 'SUCCESS', 'issues': []}
        status['providers'] = {'api.bls.gov': {'outcomes_this_run': {'TLS_ERROR': 1, 'HTTP_418': 1}, 'retry_after_at': (NOW + timedelta(hours=1)).isoformat()}}
        self.assertEqual(self.refresh(Client(docs))['outcome'], 'UPDATED')
        for mutate in [lambda d: d[shared.FILES[1]]['components']['live_core'].pop('completed_at'),
                       lambda d: d[shared.FILES[1]]['components']['live_core'].update(completed_at=NOW.isoformat()),
                       lambda d: d[shared.FILES[2]]['JPY'].update(instrument={'secret': 'x'}),
                       lambda d: d[shared.FILES[0]].update(bls_provider_status={'Inflation': {'proof': {'private': 'x'}}})]:
            bad = copy.deepcopy(docs); mutate(bad)
            with self.assertRaises((shared.Invalid, ValueError)):
                shared._validate(bad, NOW)

    def test_valid_policy_rate_and_unit_consistency(self):
        docs = fixture(); core, status, policies = [docs[n] for n in shared.FILES]
        at = core['completed_at']
        row = {'factor': 'Geldpolitik', 'score': 0.0, 'validation': 'VALID', 'freshness': 'FRESH', 'checked_at': at,
               'last_attempt_at': at, 'published_at': None, 'next_due_at': None, 'expires_at': '2026-01-20T00:00:00+00:00',
               'observation': {'date': '2026-01-09', 'policy_rate': 3.0, 'yield_2y': 3.0, 'unit': 'percent per annum'}}
        core['currencies']['USD']['Geldpolitik'] = row
        core.update(status='PARTIAL', eligible_factors=1)
        status['last_run_status'] = 'PARTIAL'
        status['components']['live_core'].update(status='PARTIAL', eligible_factors=1)
        policies['USD'].update(rate=3.0, verified_at='2026-01-09T00:00:00+00:00')
        policies['USD']['verification_evidence'] = [{'retrieved_at': '2026-01-09T00:00:00+00:00', 'valid_until': '2026-01-20T00:00:00+00:00', 'announced_rate': 4.0, 'announced_effective_date': '2026-01-20', 'deadline_basis': 'date-only effective day'}]
        self.assertEqual(shared._validate(docs, NOW), shared._time(at))
        row['observation']['unit'] = 'Thousands'
        with self.assertRaises(shared.Invalid): shared._validate(docs, NOW)
        row['observation']['unit'] = 'percent per annum'; policies['USD']['rate'] = 4.0
        with self.assertRaises(shared.Invalid): shared._validate(docs, NOW)

    def test_failed_manifest_or_rename_keeps_previous_pointer(self):
        self.refresh(); original = (self.root / '.shared-current.json').read_bytes()
        docs = fixture(NOW + timedelta(minutes=4))
        real = shared._atomic
        def failure(path, value):
            if path.name == 'manifest.json': raise OSError('simulated')
            return real(path, value)
        with patch.object(shared, '_atomic', side_effect=failure):
            self.assertEqual(self.refresh(Client(docs, 'b' * 40), NOW + timedelta(minutes=5))['outcome'], 'UNAVAILABLE')
        self.assertEqual((self.root / '.shared-current.json').read_bytes(), original)
        with patch.object(shared.os, 'rename', side_effect=OSError('simulated')):
            self.assertEqual(self.refresh(Client(docs, 'b' * 40), NOW + timedelta(minutes=10))['outcome'], 'UNAVAILABLE')
        self.assertEqual((self.root / '.shared-current.json').read_bytes(), original)
        self.assertEqual(shared.current_shared_directory(self.root).name, COMMIT)

    def test_session_is_isolated_and_retry_free(self):
        session = Client(); session.headers = {'Authorization': 'not-retained'}
        session.mount = lambda scheme, adapter: self.assertEqual(adapter.max_retries.total, 0)
        session.close = lambda: None
        with patch.object(shared.requests, 'Session', return_value=session):
            self.assertEqual(shared.refresh_shared_snapshot(NOW, self.root)['outcome'], 'UPDATED')
        self.assertFalse(session.trust_env); self.assertIsNone(session.auth)
        self.assertNotIn('Authorization', session.headers)

    def test_bundled_freeze_reuse_and_input_mutation_isolation(self):
        source = self.root / 'source'; source.mkdir()
        docs = fixture()
        for name, value in docs.items(): (source / name).write_text(json.dumps(value))
        with patch.object(shared.requests, 'Session', side_effect=AssertionError('NO_HTTP')):
            frozen = shared.bundled_snapshot_directory(source, self.root)
            self.assertIsNotNone(frozen)
            self.assertEqual(frozen, shared.bundled_snapshot_directory(source, self.root))
            before = {name: (frozen / name).read_bytes() for name in shared.FILES}
            (source / shared.FILES[0]).write_text('{}')
            self.assertIsNone(shared.bundled_snapshot_directory(source, self.root))
            self.assertEqual(before, {name: (frozen / name).read_bytes() for name in shared.FILES})
            manifest = json.loads((frozen / 'manifest.json').read_text())
            self.assertEqual(manifest['identity_algorithm'], 'sha256')
            self.assertNotIn('commit', manifest)
            self.assertEqual(len(frozen.name), 64)

    def test_bundled_rejects_mixed_status_policy_and_tampered_generation(self):
        source = self.root / 'source'; source.mkdir()
        for mutation in ('binding', 'policy', 'complete'):
            docs = fixture()
            if mutation == 'binding': docs[shared.FILES[1]]['components']['live_core']['completed_at'] = NOW.isoformat()
            if mutation == 'policy': docs[shared.FILES[2]]['USD']['verification_status'] = 'OTHER'
            for name, value in docs.items(): (source / name).write_text(json.dumps(value))
            result = shared.bundled_snapshot_directory(source, self.root)
            if mutation != 'complete': self.assertIsNone(result)
            else:
                self.assertIsNotNone(result)
                (result / shared.FILES[1]).write_text('{}')
                self.assertIsNone(shared.bundled_snapshot_directory(source, self.root))

    def test_official_outage_timestamp_survives_public_and_bundled_snapshots(self):
        docs = fixture()
        at = docs[shared.FILES[0]]['completed_at']
        provider = {'status': 'SOURCE_UNAVAILABLE', 'data_status': 'OFFICIAL_OUTAGE_PAGE',
                    'data_status_at': at, 'last_failure_at': at,
                    'outcomes_this_run': {'SUCCESS': 1}}
        docs[shared.FILES[1]]['providers'] = {'www150.statcan.gc.ca': provider}
        self.assertEqual(shared._validate(docs, NOW), shared._time(at))
        self.assertEqual(self.refresh(Client(docs))['outcome'], 'UPDATED')
        public = shared.current_shared_directory(self.root)
        published = json.loads((public / shared.FILES[1]).read_text())
        self.assertEqual(published['providers']['www150.statcan.gc.ca'], provider)
        self.assertEqual(published['components']['live_core']['eligible_factors'], 0)
        source = self.root / 'source'; source.mkdir()
        for name, value in docs.items():
            (source / name).write_text(json.dumps(value))
        with patch.object(shared.requests, 'Session', side_effect=AssertionError('NO_HTTP')):
            bundled = shared.bundled_snapshot_directory(source, self.root)
        self.assertIsNotNone(bundled)
        self.assertEqual(json.loads((bundled / shared.FILES[1]).read_text()), published)

    def test_outage_timestamp_is_strict_and_unknown_provider_fields_stay_blocked(self):
        docs = fixture()
        at = docs[shared.FILES[0]]['completed_at']
        docs[shared.FILES[1]]['providers'] = {'www150.statcan.gc.ca': {
            'data_status': 'OFFICIAL_OUTAGE_PAGE', 'data_status_at': at}}
        invalid = (None, True, {}, [], 'not-a-time', '2026-01-10T11:59:00',
                   '2026-01-10T12:59:00+01:00', NOW.isoformat())
        for value in invalid:
            with self.subTest(value=value):
                bad = copy.deepcopy(docs)
                bad[shared.FILES[1]]['providers']['www150.statcan.gc.ca']['data_status_at'] = value
                with self.assertRaises((shared.Invalid, ValueError)):
                    shared._validate(bad, NOW)
        bad = copy.deepcopy(docs)
        bad[shared.FILES[1]]['providers']['www150.statcan.gc.ca']['private_key'] = 'synthetic'
        with self.assertRaises(shared.Invalid):
            shared._validate(bad, NOW)

    def test_local_public_fixture_matches_contract(self):
        docs = {name: shared._decode(Path(name).read_bytes()) for name in shared.FILES}
        completed = shared._time(docs[shared.FILES[0]]['completed_at'])
        # Establish the new binding independently of the current checked-in vintage.
        docs[shared.FILES[1]]['components']['live_core']['completed_at'] = docs[shared.FILES[0]]['completed_at']
        self.assertEqual(shared._validate(docs, completed), completed)
        legacy = copy.deepcopy(docs)
        legacy[shared.FILES[1]]['components']['live_core'].pop('completed_at')
        with self.assertRaises(shared.Invalid):
            shared._validate(legacy, completed)


if __name__ == '__main__':
    unittest.main()
