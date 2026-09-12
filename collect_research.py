"""Independent, once-daily research collection; never imports CORE or app code."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

import requests
from research_vintages import inspect_vintage, append_vintage
from bis_research import fetch

DATA_NAME = 'bis_nz_policy.json'
SOURCE_ID = 'bis_nz_policy'
ARCHIVE_NAME = 'bis_nz_policy_vintages.json'
STATUS_NAME = 'status.json'


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_archive_status(path):
    """Missing is bootstrap; corrupt persisted status must not erase an archive anchor."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate status field')
            result[key] = value
        return result
    try:
        raw = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    value = json.loads(raw, object_pairs_hook=unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid status constant')))
    if not isinstance(value, dict):
        raise ValueError('Invalid status envelope')
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def collect(output_dir, *, now=None, session=None, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError('A timezone-aware collection time is required')
    now = now.astimezone(timezone.utc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / '.collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running', 'mode': 'research_only', 'core_eligible': False}
        status_path = output_dir / STATUS_NAME
        try:
            previous = read_archive_status(status_path)
        except (OSError, ValueError, TypeError):
            # Preserve malformed bytes: overwriting could destroy a recoverable head.
            return {'status': 'failed', 'mode': 'research_only', 'core_eligible': False,
                    'error_code': 'RESEARCH_ARCHIVE_INVALID'}
        attempted = timestamp(previous.get('last_attempt_at'))
        due = timestamp(previous.get('next_attempt_at'))
        previous_success = timestamp(previous.get('last_success_at'))
        state = {
            'schema': 'bis-research-collection-v1', 'mode': 'research_only',
            'core_eligible': False, 'status': 'attempt_started',
            'last_attempt_at': now.isoformat(),
            'next_attempt_at': (now + timedelta(days=1)).isoformat(),
            'last_success_at': previous_success.isoformat() if previous_success and previous_success <= now else None,
            'error_code': None,
        }
        # Preserve the independently published anchor even during interrupted attempts.
        if 'archive_head' in previous:
            state['archive_head'] = previous['archive_head']
        if 'archive_event_count' in previous:
            state['archive_event_count'] = previous['archive_event_count']
        archive_path = output_dir / ARCHIVE_NAME
        anchor = previous.get('archive_head')
        try:
            if previous.get('archive_event_count', 0) and anchor is None:
                raise ValueError('Recorded archive is missing its anchor')
            inspect_vintage(archive_path, SOURCE_ID, anchor=anchor)
        except (ValueError, OSError, TypeError, KeyError, OverflowError):
            state.update(status='failed', error_code='RESEARCH_ARCHIVE_INVALID')
            atomic_json(status_path, state)
            return state
        # A saved attempt (including failure) prevents duplicate requests after restart.
        if (previous.get('schema') == 'bis-research-collection-v1' and attempted and due
                and attempted <= now < due <= attempted + timedelta(days=1)):
            return {**previous, 'collection_action': 'not_due'}
        # Persist before the network call: crashes must not produce an unbounded retry loop.
        atomic_json(status_path, state)
        try:
            result = fetch(session=session, now=now)
        except requests.RequestException:
            state.update(status='failed', error_code='BIS_REQUEST_FAILED')
        except (ValueError, KeyError, TypeError, OverflowError):
            state.update(status='failed', error_code='BIS_RESPONSE_INVALID')
        else:
            try:
                # First availability is sampled only after successful fetch/validation.
                observed_at = clock()
                if (not isinstance(observed_at, datetime) or observed_at.tzinfo is None
                        or observed_at < now):
                    raise ValueError('Invalid post-fetch observation time')
                head = append_vintage(archive_path, SOURCE_ID, result, observed_at, anchor=anchor)
            except (ValueError, OSError, TypeError, KeyError, OverflowError):
                state.update(status='failed', error_code='RESEARCH_ARCHIVE_INVALID')
            else:
                # Journal first, latest artifact second, final anchored status last.
                # A crash here leaves a validated journal prefix for the next attempt.
                atomic_json(output_dir / DATA_NAME, result)
                state.update(status='success', last_success_at=now.isoformat(),
                             archive_head={key: head[key] for key in ('sequence', 'event_hash')},
                             archive_event_count=head['event_count'])
        atomic_json(status_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'research_data')
    args = parser.parse_args()
    result = collect(args.output_dir)
    print(json.dumps({key: result.get(key) for key in ('status', 'collection_action', 'last_attempt_at', 'last_success_at', 'error_code')}))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
