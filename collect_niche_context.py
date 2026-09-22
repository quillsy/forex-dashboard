"""Once-daily, independent context collection with prospective revision history."""
import argparse
import base64
import binascii
import fcntl
import hashlib
import json
import os
import tempfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from collect_research import atomic_json, read_archive_status
from niche_context import fetch, validate_artifact

DATA_NAME = 'niche_context.json'
ECB_CSV_NAME = 'niche_ecb_est.csv'
JOURNAL_NAME = 'niche_context_vintages.json'
STATUS_NAME = 'niche_context_status.json'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _pack_snapshot(artifact):
    raw = json.dumps(artifact, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    if len(raw) > 100_000:
        raise ValueError('Oversize research snapshot')
    return 'zlib-base64-v1:' + base64.b64encode(zlib.compress(raw, level=9)).decode('ascii')


def _unpack_snapshot(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.startswith('zlib-base64-v1:') or len(value) > 100_000:
        raise ValueError('Invalid snapshot encoding')
    try:
        compressed = base64.b64decode(value.removeprefix('zlib-base64-v1:'), validate=True)
        inflater = zlib.decompressobj()
        raw = inflater.decompress(compressed, max_length=100_001)
        if len(raw) > 100_000 or not inflater.eof or inflater.unconsumed_tail or inflater.unused_data:
            raise ValueError('Invalid compressed snapshot')
        def unique_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError('Duplicate snapshot field')
                result[key] = item
            return result
        return json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))
    except (binascii.Error, UnicodeDecodeError, zlib.error) as error:
        raise ValueError('Corrupt compressed snapshot') from error


def _atomic_csv(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _history(path, anchor, *, now=None, allow_tail=False):
    now = now or datetime.now(timezone.utc)
    if not path.exists():
        if anchor is not None:
            raise ValueError('Anchored history missing')
        return []
    journal = read_archive_status(path)
    if journal.get('schema') != 'fx-niche-vintages-v1' or not isinstance(journal.get('events'), list):
        raise ValueError('Invalid history')
    events = journal['events']
    previous = None
    previous_observed = None
    anchor_seen = anchor is None
    for sequence, event in enumerate(events, 1):
        snapshot = _unpack_snapshot(event.get('snapshot'))
        if (event.get('sequence') != sequence or event.get('previous_hash') != previous
                or event.get('mode') != 'research_only' or event.get('core_eligible') is not False
                or event.get('signal') is not None or event.get('published_at') is not None
                or event.get('state_hash') != _digest(snapshot)):
            raise ValueError('Invalid history event')
        validate_artifact(snapshot, now=now)
        observed = datetime.fromisoformat(event['first_observed_at'])
        retrieved = datetime.fromisoformat(snapshot['retrieved_at'])
        if (observed.tzinfo is None or observed > now or observed < retrieved
                or previous_observed and observed <= previous_observed):
            raise ValueError('Invalid prospective chronology')
        if event.get('event_hash') != _digest({key: value for key, value in event.items()
                                                if key != 'event_hash'}):
            raise ValueError('Broken history hash')
        previous = event['event_hash']
        previous_observed = observed
        if previous == anchor:
            anchor_seen = True
    if not anchor_seen or (not allow_tail and anchor != previous):
        raise ValueError('History anchor mismatch')
    return events


def collect(output_dir, *, now=None, session=None):
    injected_clock = now is not None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Naive collection clock')
    now = now.astimezone(timezone.utc)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.niche_context.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running'}
        status_path = directory / STATUS_NAME
        try:
            previous_status = read_archive_status(status_path)
            if previous_status and (previous_status.get('schema') != 'fx-niche-collection-v1'
                                    or previous_status.get('mode') != 'research_only'
                                    or previous_status.get('core_eligible') is not False
                                    or previous_status.get('signal') is not None):
                raise ValueError('Invalid previous status')
            anchor = previous_status.get('archive_head')
            events = _history(directory / JOURNAL_NAME, anchor, now=now, allow_tail=True)
            tail_found = bool(events and anchor != events[-1]['event_hash'])
            if events:
                anchor = events[-1]['event_hash']
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return {'status': 'failed', 'error_code': 'NICHE_ARCHIVE_INVALID'}
        attempted = previous_status.get('last_attempt_at')
        if attempted:
            try:
                last = datetime.fromisoformat(attempted)
                if last.tzinfo is None or last > now:
                    raise ValueError('Invalid collection clock')
                if (not tail_found and last <= now < last + timedelta(days=1)):
                    return {**previous_status, 'collection_action': 'not_due'}
            except ValueError:
                return {'status': 'failed', 'error_code': 'NICHE_STATUS_INVALID'}
        state = {'schema': 'fx-niche-collection-v1', 'mode': 'research_only',
                 'core_eligible': False, 'signal': None, 'status': 'attempt_started',
                 'last_attempt_at': now.isoformat(),
                 'last_success_at': previous_status.get('last_success_at'),
                 'archive_head': anchor, 'archive_event_count': len(events), 'error_code': None}
        atomic_json(status_path, state)
        try:
            artifact = fetch(session=session, now=now)
            validate_artifact(artifact, now=now)
        except requests.RequestException:
            state.update(status='failed', error_code='NICHE_REQUEST_FAILED')
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, UnicodeError):
            state.update(status='failed', error_code='NICHE_RESPONSE_INVALID')
        else:
            try:
                state_hash = _digest(artifact)
                if not events or events[-1]['state_hash'] != state_hash:
                    event = {'sequence': len(events) + 1, 'previous_hash': anchor,
                             'first_observed_at': (now if injected_clock else max(
                                 now, datetime.now(timezone.utc))).isoformat(),
                             'published_at': None, 'mode': 'research_only',
                             'core_eligible': False, 'signal': None,
                             'state_hash': state_hash, 'snapshot': _pack_snapshot(artifact)}
                    event['event_hash'] = _digest(event)
                    events.append(event)
                    atomic_json(directory / JOURNAL_NAME,
                                {'schema': 'fx-niche-vintages-v1', 'events': events})
                    state['archive_head'] = event['event_hash']
                    state['archive_event_count'] = len(events)
                _atomic_csv(directory / ECB_CSV_NAME, artifact['ecb_raw_csv'].encode('utf-8'))
                atomic_json(directory / DATA_NAME, artifact)
                state.update(status='success', last_success_at=now.isoformat())
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                state.update(status='failed', error_code='NICHE_ARCHIVE_INVALID')
        atomic_json(status_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'research_data')
    args = parser.parse_args()
    result = collect(args.output_dir)
    print(json.dumps({key: result.get(key) for key in ('status', 'collection_action',
                                                       'last_attempt_at', 'error_code')}))
    return int(result['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
