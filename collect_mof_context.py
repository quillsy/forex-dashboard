"""Collect and archive the MoF weekly series as research-only context."""
import argparse
import fcntl
import json
import re
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from collect_niche_context import _atomic_csv, _digest, _pack_snapshot, _unpack_snapshot
from collect_research import atomic_json, read_archive_status
from mof_context import _time, fetch, validate_artifact

DATA_NAME = 'mof_context.json'
RAW_NAME = 'mof_week.csv'
JOURNAL_NAME = 'mof_context_vintages.json'
STATUS_NAME = 'mof_context_status.json'


class PersistenceError(RuntimeError):
    """A durable Git transition was rejected or could not be confirmed."""


class GitPublisher:
    """Publish one reservation and completion without force or conflict retries."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.repository = self.directory.parent
        self.parent = None
        self.reservation = None
        if self.directory.name != 'research_data':
            raise PersistenceError('Publishing requires repository/research_data')

    def _git(self, *args):
        try:
            return subprocess.run(['git', *args], cwd=self.repository, check=True,
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise PersistenceError('MoF Git persistence failed') from error

    def refresh(self):
        if (Path(self._git('rev-parse', '--show-toplevel')).resolve() != self.repository
                or self._git('symbolic-ref', '--short', 'HEAD') != 'main'
                or self._git('status', '--porcelain', '--untracked-files=all')):
            raise PersistenceError('Publishing requires a clean main checkout')
        self._git('fetch', '--no-tags', 'origin', 'refs/heads/main')
        self._git('merge', '--ff-only', 'FETCH_HEAD')
        self.parent = self._git('rev-parse', 'HEAD')
        if self.parent != self._git('rev-parse', 'FETCH_HEAD'):
            raise PersistenceError('Local main contains unpublished commits')

    def _publish(self, names, message):
        if self.parent is None or self._git('rev-parse', 'HEAD') != self.parent:
            raise PersistenceError('MoF local parent changed')
        paths = [f'research_data/{name}' for name in names
                 if (self.directory / name).exists()]
        self._git('add', '--', *paths)
        staged = {path for path in self._git('diff', '--cached', '--name-only', '-z').split('\0')
                  if path}
        if not staged or not staged <= set(paths):
            raise PersistenceError('Unexpected MoF publication paths')
        self._git('commit', '-m', message)
        commit = self._git('rev-parse', 'HEAD')
        self._git('push', 'origin', f'{commit}:refs/heads/main')
        self.parent = commit

    def reserve(self, state):
        self._publish([STATUS_NAME], 'Reserve MoF research attempt [skip ci]')
        self.reservation = dict(state)

    def finish(self, state):
        persisted = json.loads(self._git('show', f'{self.parent}:research_data/{STATUS_NAME}'))
        if (self.reservation is None or persisted != self.reservation
                or state.get('attempt_id') != self.reservation['attempt_id']):
            raise PersistenceError('MoF reservation changed')
        self._publish([RAW_NAME, DATA_NAME, JOURNAL_NAME, STATUS_NAME],
                      'Complete MoF research attempt [skip ci]')


def validate_status(status, *, now):
    required_fields = {'schema', 'mode', 'core_eligible', 'signal', 'status', 'last_attempt_at',
                       'next_allowed_at', 'last_success_at', 'archive_head', 'archive_event_count',
                       'source_sha256', 'error_code'}
    if (not isinstance(status, dict)
            or set(status) not in (required_fields,
                                   required_fields | {'attempt_id', 'attempt_completed_at'})
            or status.get('schema') != 'fx-mof-collection-v1'
            or status.get('mode') != 'research_only' or status.get('core_eligible') is not False
            or status.get('signal') is not None
            or status.get('status') not in ('success', 'failed', 'attempt_started')):
        raise ValueError('Invalid MoF status')
    count = status['archive_event_count']
    anchor = status['archive_head']
    source_hash = status['source_sha256']
    if (type(count) is not int or count < 0
            or (count == 0 and anchor is not None)
            or (count > 0 and (not isinstance(anchor, str) or not re.fullmatch('[0-9a-f]{64}', anchor)))
            or (source_hash is not None and (count == 0 or not isinstance(source_hash, str)
                                            or not re.fullmatch('[0-9a-f]{64}', source_hash)))):
        raise ValueError('Invalid MoF status archive metadata')
    attempted = _time(status['last_attempt_at'])
    deadline = _time(status['next_allowed_at'])
    successful = _time(status['last_success_at']) if status['last_success_at'] is not None else None
    if (attempted > now or deadline < attempted + timedelta(hours=20)
            or successful is not None and successful > now):
        raise ValueError('Invalid MoF status chronology')
    if 'attempt_id' in status:
        completed = (_time(status['attempt_completed_at'])
                     if status['attempt_completed_at'] is not None else None)
        if (not isinstance(status['attempt_id'], str)
                or not re.fullmatch('[0-9a-f]{32}', status['attempt_id'])
                or status['status'] == 'attempt_started' and completed is not None
                or status['status'] != 'attempt_started' and completed is None
                or completed is not None and (completed < attempted or completed > now
                                               or deadline < completed + timedelta(hours=20)
                                               or successful is not None and successful > completed)):
            raise ValueError('Invalid MoF attempt metadata')
    if status['status'] == 'success':
        if (successful is None or successful < attempted
                or deadline < successful + timedelta(hours=20)
                or count == 0 or source_hash is None):
            raise ValueError('Invalid MoF success status')
    elif successful is not None and successful > attempted:
        raise ValueError('Invalid MoF previous success time')
    if status['status'] == 'failed':
        if status['error_code'] not in ('MOF_REQUEST_FAILED', 'MOF_RESPONSE_OR_ARCHIVE_INVALID'):
            raise ValueError('Invalid MoF failure code')
    elif status['error_code'] is not None:
        raise ValueError('Unexpected MoF failure code')
    return attempted, deadline


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
    if (date.fromisoformat(current['observations'][-1]['week_end'])
            < date.fromisoformat(previous['observations'][-1]['week_end'])):
        raise ValueError('MoF source period moved backwards')
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


def collect(output_dir, *, now=None, session=None, publisher=None):
    """Only the process that confirms a new reservation may perform its GET."""
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
        if publisher is not None:
            try:
                publisher.refresh()
            except PersistenceError:
                return {'status': 'failed', 'error_code': 'MOF_PERSISTENCE_FAILED'}
        status_path = directory / STATUS_NAME
        try:
            previous_status = read_archive_status(status_path)
            attempted, deadline = (validate_status(previous_status, now=now)
                                   if previous_status or status_path.exists() else (None, None))
            events = _history(directory / JOURNAL_NAME, previous_status.get('archive_head'),
                              now=now, allow_tail=True)
            anchor = events[-1]['event_hash'] if events else None
            if previous_status.get('status') == 'attempt_started':
                return {**previous_status, 'status': 'failed',
                        'error_code': 'MOF_PRIOR_ATTEMPT_INCOMPLETE',
                        'collection_action': 'blocked_incomplete'}
            if attempted is not None and now < deadline:
                return {**previous_status, 'collection_action': 'not_due'}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return {'status': 'failed', 'error_code': 'MOF_ARCHIVE_INVALID'}
        state = {'schema': 'fx-mof-collection-v1', 'mode': 'research_only',
                 'core_eligible': False, 'signal': None, 'status': 'attempt_started',
                 'attempt_id': uuid.uuid4().hex, 'attempt_completed_at': None,
                 'last_attempt_at': now.isoformat(),
                 'next_allowed_at': (now + timedelta(hours=20)).isoformat(),
                 'last_success_at': previous_status.get('last_success_at'),
                 'archive_head': anchor, 'archive_event_count': len(events),
                 'source_sha256': previous_status.get('source_sha256'), 'error_code': None}
        atomic_json(status_path, state)
        reservation = dict(state)
        if publisher is not None:
            try:
                publisher.reserve(state)
            except PersistenceError:
                return {**state, 'status': 'failed', 'error_code': 'MOF_PERSISTENCE_FAILED'}
        retry_after = None
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
                         source_sha256=artifact['source_sha256'])
        except requests.RequestException as error:
            state.update(status='failed', error_code='MOF_REQUEST_FAILED')
            failed_at = now if injected_clock else max(now, datetime.now(timezone.utc))
            retry_after = _retry_after_at(error, failed_at)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, UnicodeError):
            state.update(status='failed', error_code='MOF_RESPONSE_OR_ARCHIVE_INVALID')
        completed = now if injected_clock else max(now, datetime.now(timezone.utc))
        state['attempt_completed_at'] = completed.isoformat()
        state['next_allowed_at'] = max(
            _time(reservation['next_allowed_at']),
            completed + timedelta(hours=20), retry_after or completed).isoformat()
        validate_status(state, now=completed)
        atomic_json(status_path, state)
        if publisher is not None:
            try:
                publisher.finish(state)
            except PersistenceError:
                atomic_json(status_path, reservation)
                return {**reservation, 'status': 'failed',
                        'error_code': 'MOF_PERSISTENCE_FAILED'}
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'research_data')
    parser.add_argument('--publish', action='store_true',
                        help='Confirm reservation on origin/main before GET, then publish completion')
    args = parser.parse_args()
    publisher = GitPublisher(args.output_dir) if args.publish else None
    result = collect(args.output_dir, publisher=publisher)
    print(json.dumps({key: result.get(key) for key in ('status', 'collection_action',
                                                       'last_attempt_at', 'error_code')}))
    return int(result['status'] == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
