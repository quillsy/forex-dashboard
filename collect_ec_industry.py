"""Independent hourly EC industry research collection; never grants CORE eligibility."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path

import requests
from collect_research import atomic_json, read_json, timestamp
from ec_industry_research import fetch

DATA_NAME = 'ec_industry.json'
STATUS_NAME = 'ec_industry_status.json'
LOCK_NAME = '.ec_industry_collection.lock'


def collect(output_dir, *, now=None, session=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('A timezone-aware collection time is required')
    now = now.astimezone(timezone.utc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / LOCK_NAME).open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running', 'mode': 'research_only', 'core_eligible': False}
        status_path = output_dir / STATUS_NAME
        previous = read_json(status_path)
        attempted = timestamp(previous.get('last_attempt_at'))
        due = timestamp(previous.get('next_attempt_at'))
        # A saved attempt (including failure) prevents duplicate requests after restart.
        if (previous.get('schema') == 'ec-industry-research-collection-v1' and attempted and due
                and attempted <= now < due <= attempted + timedelta(hours=1)):
            return {**previous, 'collection_action': 'not_due'}
        previous_success = timestamp(previous.get('last_success_at'))
        state = {
            'schema': 'ec-industry-research-collection-v1', 'mode': 'research_only',
            'core_eligible': False, 'status': 'attempt_started',
            'last_attempt_at': now.isoformat(),
            'next_attempt_at': (now + timedelta(hours=1)).isoformat(),
            'last_success_at': previous_success.isoformat() if previous_success and previous_success <= now else None,
            'error_code': None,
        }
        # Persist before the network call: crashes must not produce an unbounded retry loop.
        atomic_json(status_path, state)
        try:
            result = fetch(session=session, now=now)
            if (not isinstance(result, dict) or result.get('mode') != 'research_only'
                    or result.get('core_eligible') is not False):
                raise ValueError('Invalid research envelope')
            # Validate JSON before replacing a previously usable public artifact.
            json.dumps(result, allow_nan=False)
        except requests.RequestException:
            state.update(status='failed', error_code='EC_INDUSTRY_REQUEST_FAILED')
        except (ValueError, KeyError, TypeError, OverflowError):
            state.update(status='failed', error_code='EC_INDUSTRY_RESPONSE_INVALID')
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
