"""Independent, once-daily research collection; never imports CORE or app code."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile

import requests
from bis_research import fetch

DATA_NAME = 'bis_nz_policy.json'
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


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def collect(output_dir, *, now=None, session=None):
    now = now or datetime.now(timezone.utc)
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
        previous = read_json(status_path)
        attempted = timestamp(previous.get('last_attempt_at'))
        due = timestamp(previous.get('next_attempt_at'))
        # A saved attempt (including failure) prevents duplicate requests after restart.
        if (previous.get('schema') == 'bis-research-collection-v1' and attempted and due
                and attempted <= now < due <= attempted + timedelta(days=1)):
            return {**previous, 'collection_action': 'not_due'}
        previous_success = timestamp(previous.get('last_success_at'))
        state = {
            'schema': 'bis-research-collection-v1', 'mode': 'research_only',
            'core_eligible': False, 'status': 'attempt_started',
            'last_attempt_at': now.isoformat(),
            'next_attempt_at': (now + timedelta(days=1)).isoformat(),
            'last_success_at': previous_success.isoformat() if previous_success and previous_success <= now else None,
            'error_code': None,
        }
        # Persist before the network call: crashes must not produce an unbounded retry loop.
        atomic_json(status_path, state)
        try:
            result = fetch(session=session, now=now)
        except requests.RequestException:
            state.update(status='failed', error_code='BIS_REQUEST_FAILED')
        except (ValueError, KeyError, TypeError, OverflowError):
            state.update(status='failed', error_code='BIS_RESPONSE_INVALID')
        else:
            # Only the strict adapter result reaches public data. Failure retains previous bytes.
            atomic_json(output_dir / DATA_NAME, result)
            state.update(status='success', last_success_at=now.isoformat())
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
