"""Collect and archive the MoF weekly series as research-only context."""
import argparse
import fcntl
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from collect_niche_context import _atomic_csv, _digest, _pack_snapshot, _unpack_snapshot
from collect_research import atomic_json, read_archive_status
from mof_context import fetch, validate_artifact

DATA_NAME = 'mof_context.json'
RAW_NAME = 'mof_week.csv'
JOURNAL_NAME = 'mof_context_vintages.json'
STATUS_NAME = 'mof_context_status.json'


def _retry_after_at(error, now):
    response = getattr(error, 'response', None)
    if response is None or getattr(response, 'status_code', None) != 429:
        return None
    header = response.headers.get('Retry-After', '').strip()
    try:
        if header.isdecimal():
            remaining = int((datetime.max.replace(tzinfo=timezone.utc) - now).total_seconds())
            return now + timedelta(seconds=min(int(header), remaining))
        parsed = parsedate_to_datetime(header)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        pass
    return now + timedelta(days=1)


def _changes(previous, current):
    if previous is None:
        return [], [item['week_end'] for item in current['observations']]
    old = {item['week_end']: item['values'] for item in previous['observations']}
    new = {item['week_end']: item['values'] for item in current['observations']}
    revisions = sorted(week for week in old.keys() & new.keys() if old[week] != new[week])
    additions = sorted(new.keys() - old.keys())
    return revisions, additions


def _history(path, anchor, *, now, allow_tail=False):
    if not path.exists():
        if anchor is not None:
            raise ValueError('Anchored MoF history missing')
        return []
    journal = read_archive_status(path)
    if journal.get('schema') != 'fx-mof-vintages-v1' or not isinstance(journal.get('events'), list):
        raise ValueError('Invalid MoF history')
    events = journal['events']
    previous_hash = None
    previous_artifact = None
    previous_observed = None
    anchor_seen = anchor is None
    for sequence, event in enumerate(events, 1):
        if (not isinstance(event, dict) or set(event) !=
                {'sequence', 'previous_hash', 'first_observed_at', 'published_at',
                 'mode', 'core_eligible', 'signal', 'state_hash', 'snapshot',
                 'revision_week_ends', 'added_week_ends', 'event_hash'}):
            raise ValueError('MoF history event schema changed')
        artifact = _unpack_snapshot(event['snapshot'])
        validate_artifact(artifact, now=now)
        observed = datetime.fromisoformat(artifact['first_observed_at'])
        revisions, additions = _changes(previous_artifact, artifact)
        if (event.get('sequence') != sequence or event.get('previous_hash') != previous_hash
                or event.get('mode') != 'research_only' or event.get('core_eligible') is not False
                or event.get('signal') is not None or event.get('published_at') is not None
                or event.get('first_observed_at') != artifact['first_observed_at']
                or event.get('state_hash') != _digest(artifact)
                or event.get('revision_week_ends') != revisions
                or event.get('added_week_ends') != additions
                or previous_artifact and previous_artifact['source_sha256'] == artifact['source_sha256']
                or previous_artifact and previous_artifact['source_update_date'] > artifact['source_update_date']
                or previous_observed and observed <= previous_observed
                or event.get('event_hash') != _digest({key: value for key, value in event.items()
                                                       if key != 'event_hash'})):
            raise ValueError('Invalid MoF history event')
        previous_hash = event['event_hash']
        previous_artifact = artifact
        previous_observed = observed
        if previous_hash == anchor:
            anchor_seen = True
    if not anchor_seen or not allow_tail and anchor != previous_hash:
        raise ValueError('MoF history anchor mismatch')
    return events


def collect(output_dir, *, now=None, session=None):
    injected_clock = now is not None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Naive collection clock')
    now = now.astimezone(timezone.utc)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.mof_context.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running'}
        status_path = directory / STATUS_NAME
        try:
            previous_status = read_archive_status(status_path)
            if previous_status and (previous_status.get('schema') != 'fx-mof-collection-v1'
                                    or previous_status.get('mode') != 'research_only'
                                    or previous_status.get('core_eligible') is not False
                                    or previous_status.get('signal') is not None
                                    or previous_status.get('status') not in
                                    ('success', 'failed', 'attempt_started')):
                raise ValueError('Invalid previous MoF status')
            events = _history(directory / JOURNAL_NAME, previous_status.get('archive_head'),
                              now=now, allow_tail=True)
            anchor = events[-1]['event_hash'] if events else None
            last_attempt = previous_status.get('last_attempt_at')
            if last_attempt:
                attempted = datetime.fromisoformat(last_attempt)
                if attempted.tzinfo is None or attempted > now:
                    raise ValueError('Invalid MoF attempt time')
                deadline_text = previous_status.get('next_allowed_at')
                deadline = (datetime.fromisoformat(deadline_text) if deadline_text
                            else attempted + timedelta(hours=20))
                if deadline.tzinfo is None or deadline < attempted:
                    raise ValueError('Invalid MoF cooldown')
                if now < deadline:
                    if previous_status.get('status') == 'attempt_started':
                        return {**previous_status, 'status': 'failed',
                                'error_code': 'MOF_PRIOR_ATTEMPT_INCOMPLETE',
                                'collection_action': 'not_due'}
                    return {**previous_status, 'collection_action': 'not_due'}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return {'status': 'failed', 'error_code': 'MOF_ARCHIVE_INVALID'}
        state = {'schema': 'fx-mof-collection-v1', 'mode': 'research_only',
                 'core_eligible': False, 'signal': None, 'status': 'attempt_started',
                 'last_attempt_at': now.isoformat(),
                 'next_allowed_at': (now + timedelta(hours=20)).isoformat(),
                 'last_success_at': previous_status.get('last_success_at'),
                 'archive_head': anchor, 'archive_event_count': len(events),
                 'source_sha256': previous_status.get('source_sha256'), 'error_code': None}
        atomic_json(status_path, state)
        try:
            artifact, raw = fetch(session=session, now=now)
            observed = now if injected_clock else max(now, datetime.now(timezone.utc))
            artifact['first_observed_at'] = observed.isoformat()
            validate_artifact(artifact, now=observed)
            previous = _unpack_snapshot(events[-1]['snapshot']) if events else None
            if previous is not None and previous['source_sha256'] == artifact['source_sha256']:
                artifact = previous
            else:
                if (previous is not None
                        and artifact['source_update_date'] < previous['source_update_date']):
                    raise ValueError('MoF source update moved backwards')
                revisions, additions = _changes(previous, artifact)
                event = {'sequence': len(events) + 1, 'previous_hash': anchor,
                         'first_observed_at': artifact['first_observed_at'],
                         'published_at': None, 'mode': 'research_only',
                         'core_eligible': False, 'signal': None,
                         'state_hash': _digest(artifact), 'snapshot': _pack_snapshot(artifact),
                         'revision_week_ends': revisions, 'added_week_ends': additions}
                event['event_hash'] = _digest(event)
                events.append(event)
                atomic_json(directory / JOURNAL_NAME,
                            {'schema': 'fx-mof-vintages-v1', 'events': events})
                state['archive_head'] = event['event_hash']
                state['archive_event_count'] = len(events)
            _atomic_csv(directory / RAW_NAME, raw)
            atomic_json(directory / DATA_NAME, artifact)
            state.update(status='success', last_success_at=observed.isoformat(),
                         next_allowed_at=(observed + timedelta(hours=20)).isoformat(),
                         source_sha256=artifact['source_sha256'])
        except requests.RequestException as error:
            state.update(status='failed', error_code='MOF_REQUEST_FAILED')
            failed_at = now if injected_clock else max(now, datetime.now(timezone.utc))
            retry_after = _retry_after_at(error, failed_at)
            state['next_allowed_at'] = max(
                failed_at + timedelta(hours=20), retry_after or failed_at).isoformat()
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, UnicodeError):
            state.update(status='failed', error_code='MOF_RESPONSE_OR_ARCHIVE_INVALID')
            failed_at = now if injected_clock else max(now, datetime.now(timezone.utc))
            state['next_allowed_at'] = (failed_at + timedelta(hours=20)).isoformat()
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
