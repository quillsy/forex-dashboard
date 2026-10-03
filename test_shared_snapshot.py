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
