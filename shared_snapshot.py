"""Keyless, commit-pinned public snapshots; never collects provider data."""
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import certifi
import requests

REF_URL = 'https://api.github.com/repos/quillsy/forex-dashboard/git/ref/heads/main'
RAW_BASE = 'https://raw.githubusercontent.com/quillsy/forex-dashboard/'
FILES = ('live_core_data.json', 'data_collection_status.json', '.policy_rates_cache.json')
MAX_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 300
SHA = re.compile(r'[0-9a-f]{40}')
RECORD = set('factor score validation reason freshness checked_at last_attempt_at published_at next_due_at expires_at observation last_error'.split())
POLICY = set('currency rate previous_rate upper_bound instrument central_bank rate_effective_date last_policy_decision_date verified_at verification_timestamp last_verified_at primary_source secondary_source verification_evidence verification_status last_attempt_at last_attempt_error candidate_rate'.split())
EVIDENCE = set('currency instrument source_url rate retrieved_at rate_effective_date previous_rate observation_date upper_bound last_policy_decision_date decision_source effective_date_source valid_until announced_rate announced_effective_date deadline_basis superseded_terms_source superseded_terms_rate superseded_terms_revision_date'.split())
PROVIDER = set('requests_this_run status remaining limit reset_at budget_evidence outcomes_this_run last_attempt_at last_checked_at requests_observed_utc_day counted_day_utc requests_reserved_utc_day requests_uncertain_utc_day prior_usage_uncertain usage_complete last_failure_at data_status provider_response_status retry_after_at'.split())
STATES = {'PARTIAL', 'SUCCESS', 'FAILED'}


class Invalid(ValueError):
    pass


def _time(value):
    if not isinstance(value, str):
        raise Invalid()
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise Invalid()
    return dt.astimezone(timezone.utc)


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    return _time(value.isoformat() if isinstance(value, datetime) else value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid()
        result[key] = value
    return result


def _decode(raw):
    if len(raw) > MAX_BYTES:
        raise Invalid()
    def reject(value):
        raise Invalid()
    result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject)
    if not isinstance(result, dict):
        raise Invalid()
    _bounded(result)
    return result


def _bounded(value, depth=0):
    if depth > 14:
        raise Invalid()
    if isinstance(value, dict):
        if len(value) > 200:
            raise Invalid()
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 200:
                raise Invalid()
            _bounded(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 200:
            raise Invalid()
        for item in value:
            _bounded(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 8192:
            raise Invalid()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise Invalid()
    elif value is not None and not isinstance(value, bool):
        raise Invalid()


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise Invalid()


def _scalars(value, except_fields=()):
    if any(isinstance(v, (list, dict)) for k, v in value.items() if k not in except_fields):
        raise Invalid()


def _summary(value, nested=False):
    fields = {'status', 'attempted', 'written', 'updated', 'skipped', 'errors', 'issues'}
    _keys(value, fields | ({'currencies', 'pairs'} if not nested else set()), {'status'})
    _scalars(value, {'issues', 'currencies', 'pairs'})
    if value['status'] not in STATES:
        raise Invalid()
    for issue in value.get('issues', []):
        _keys(issue, {'item', 'reason', 'severity', 'horizon'})
        _scalars(issue)
    for key in ('currencies', 'pairs'):
        if key in value:
            _summary(value[key], True)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Invalid()
    return value


def _validate(documents, now):
    import live_data
    core, status, policies = [documents[name] for name in FILES]
    _keys(core, {'model_version', 'last_attempt_at', 'currencies', 'eligible_factors', 'status', 'completed_at',
                 'bls_pdf_attempts', 'bls_pdf_reservations', 'bls_api_attempts', 'bls_api_reservations',
                 'bls_provider_status', 'bls_release_states'},
          {'model_version', 'last_attempt_at', 'currencies', 'eligible_factors', 'status', 'completed_at'})
    _scalars(core, {'currencies', 'bls_pdf_attempts', 'bls_pdf_reservations', 'bls_api_attempts', 'bls_api_reservations', 'bls_provider_status', 'bls_release_states'})
    completed = _time(core['completed_at'])
    start = _time(core['last_attempt_at'])
    if core['model_version'] != live_data.MODEL or not now - timedelta(days=3650) <= start <= completed <= now or completed - start > timedelta(hours=24):
        raise Invalid()
    if core['status'] not in STATES:
        raise Invalid()
    _keys(core['currencies'], live_data.CURRENCIES, live_data.CURRENCIES)
    units = {'Geldpolitik': {'percent per annum'}, 'Inflation': {'annual percent change', 'percent_yoy', 'percent YoY'},
             'Arbeitsmarkt': {'PC_ACT', 'percent labour force age 16+', 'percent of labour force', 'percent of labour force age 15+'},
             'GDP': {'CLV_PCH_SM', 'percent YoY', 'percent year-on-year', 'real GDP YoY percent'}, 'PMI': {'index'}}
    for currency, records in core['currencies'].items():
        _keys(records, live_data.FACTORS, live_data.FACTORS)
        for factor, record in records.items():
            _keys(record, RECORD, {'score', 'validation', 'observation', 'last_attempt_at'})
            if record['validation'] not in {'VALID', 'UNVERIFIED', 'SOURCE_UNAVAILABLE', 'FAILED'}:
                raise Invalid()
            _scalars(record, {'observation'})
            observation = record['observation']
            _keys(observation, live_data.OBS_FIELDS)
            if any(isinstance(v, (dict, list)) for v in observation.values()):
                raise Invalid()
            for field in ('checked_at', 'last_attempt_at', 'published_at'):
                if record.get(field) is not None and _time(record[field]) > completed:
                    raise Invalid()
            if record['score'] is not None and not -100 <= _number(record['score']) <= 100:
                raise Invalid()
            if record['validation'] == 'VALID':
                if record.get('factor') != factor or record['score'] is None or observation.get('unit') not in units[factor]:
                    raise Invalid()
                for field in ('checked_at', 'expires_at'):
                    _time(record.get(field))
                date = datetime.strptime(observation.get('date', ''), '%Y-%m-%d').replace(tzinfo=timezone.utc)
                if date > completed:
                    raise Invalid()
                for field in (('policy_rate', 'yield_2y') if factor == 'Geldpolitik' else ('value',)):
                    _number(observation.get(field))
    for field in live_data.BLS_ATTEMPT_FIELDS:
        values = core.get(field, {})
        _keys(values, {'Inflation', 'Arbeitsmarkt'})
        for value in values.values():
            if _time(value) > completed:
                raise Invalid()
    for value in core.get('bls_provider_status', {}).values():
        _keys(value, {'dol', 'bls_pdf', 'api_v1', 'proof'})
        _scalars(value)
    _keys(core.get('bls_provider_status', {}), {'Inflation', 'Arbeitsmarkt'})
    _keys(core.get('bls_release_states', {}), {'Inflation', 'Arbeitsmarkt'})
    for value in core.get('bls_release_states', {}).values():
        _keys(value, set('period embargo_ends_at next_due_at release_url schedule_url first_observed_at raw_value proof_source fred_raw_value'.split()))
    for value in core.get('bls_release_states', {}).values():
        _scalars(value)
    count = sum(live_data.eligible(record, completed, factor, currency)[0]
                for currency, records in core['currencies'].items() for factor, record in records.items())
    expected = 'SUCCESS' if count == 40 else 'PARTIAL' if count else 'FAILED'
    if type(core['eligible_factors']) is not int or core['eligible_factors'] != count or core['status'] != expected:
        raise Invalid()
    _keys(status, set('last_run_timestamp last_run_status last_run_error total_successful_runs total_partial_runs total_failed_runs history components mode providers'.split()),
          {'last_run_timestamp', 'last_run_status', 'components', 'mode'})
    _scalars(status, {'components', 'history', 'providers'})
    began = _time(status['last_run_timestamp'])
    if not start - timedelta(hours=24) <= began <= start or status['last_run_status'] not in STATES or status['mode'] not in ('live', 'daily'):
        raise Invalid()
    _keys(status['components'], {'policy_rates', 'live_core', 'snapshots', 'outcomes'}, {'live_core', 'policy_rates'})
    for name in ('snapshots', 'outcomes'):
        if name in status['components']:
            _summary(status['components'][name])
    summary = status['components']['live_core']
    _keys(summary, {'status', 'eligible_factors', 'total_factors', 'completed_at'}, {'status', 'eligible_factors', 'total_factors', 'completed_at'})
    if type(summary['eligible_factors']) is not int or type(summary['total_factors']) is not int:
        raise Invalid()
    if summary != {'status': core['status'], 'eligible_factors': count, 'total_factors': 40, 'completed_at': core['completed_at']}:
        raise Invalid()
    policy_status = status['components']['policy_rates']
    _keys(policy_status, {'status', 'currencies'}, {'status', 'currencies'})
    _keys(policy_status['currencies'], live_data.CURRENCIES, live_data.CURRENCIES)
    _scalars(policy_status['currencies'])
    if policy_status['status'] not in STATES:
        raise Invalid()
    states = [component['status'] for component in status['components'].values()]
    overall = 'SUCCESS' if all(state == 'SUCCESS' for state in states) else 'FAILED' if all(state == 'FAILED' for state in states) else 'PARTIAL'
    if status['last_run_status'] != overall:
        raise Invalid()
    for row in status.get('history', []):
        _keys(row, {'timestamp', 'status', 'error'}, {'timestamp', 'status'})
        _scalars(row)
        if _time(row['timestamp']) > completed or row['status'] not in STATES:
            raise Invalid()
    for field in ('total_successful_runs', 'total_partial_runs', 'total_failed_runs'):
        if field in status and (type(status[field]) is not int or status[field] < 0):
            raise Invalid()
    for host, row in status.get('providers', {}).items():
        if not re.fullmatch(r'[a-z0-9.-]+', host):
            raise Invalid()
        _keys(row, PROVIDER)
        outcomes = row.get('outcomes_this_run', {})
        _scalars(row, {'outcomes_this_run'})
        if not isinstance(outcomes, dict) or any(key not in {'SUCCESS', 'TIMEOUT', 'TLS_ERROR', 'CONNECTION_ERROR', 'NETWORK_ERROR'} and not re.fullmatch(r'HTTP_[1-5][0-9]{2}', key) for key in outcomes):
            raise Invalid()
        if any(type(v) is not int or v < 0 for v in outcomes.values()):
            raise Invalid()
    _keys(policies, live_data.CURRENCIES, live_data.CURRENCIES)
    for currency, policy in policies.items():
        _keys(policy, POLICY, {'currency', 'rate', 'verification_status', 'verification_evidence'})
        _scalars(policy, {'verification_evidence'})
        if policy['currency'] != currency or policy_status['currencies'][currency] != policy['verification_status']:
            raise Invalid()
        for field in ('verified_at', 'verification_timestamp', 'last_verified_at', 'last_attempt_at'):
            if policy.get(field) is not None and _time(policy[field]) > completed:
                raise Invalid()
        if not isinstance(policy['verification_evidence'], list):
            raise Invalid()
        for evidence in policy['verification_evidence']:
            _keys(evidence, EVIDENCE)
            _scalars(evidence)
            if evidence.get('valid_until') is not None:
                _time(evidence['valid_until'])  # A known future expiry is legal metadata.
            if evidence.get('retrieved_at') is not None and _time(evidence['retrieved_at']) > completed:
                raise Invalid()
        record = core['currencies'][currency]['Geldpolitik']
        if record['validation'] == 'VALID' and _number(policy['rate']) != record['observation']['policy_rate']:
            raise Invalid()
    return completed


def _atomic(path, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    fd, name = tempfile.mkstemp(prefix='.shared-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(raw); file.flush(); os.fsync(file.fileno())
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _generation(root, commit):
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise Invalid()
    base = root / 'shared-snapshots'
    path = base / commit
    if base.is_symlink() or path.is_symlink() or path.resolve().parent != base.resolve():
        raise Invalid()
    return path


def current_shared_directory(runtime_root):
    """Return a verified existing generation; remote fields never select a path."""
    try:
        root = Path(runtime_root)
        if (root / '.shared-current.json').is_symlink():
            raise Invalid()
        pointer = _decode((root / '.shared-current.json').read_bytes())
        _keys(pointer, {'commit', 'completed_at'}, {'commit', 'completed_at'})
        path = _generation(root, pointer['commit'])
        if (path / 'manifest.json').is_symlink():
            raise Invalid()
        manifest = _decode((path / 'manifest.json').read_bytes())
        _keys(manifest, {'commit', 'completed_at', 'hashes'}, {'commit', 'completed_at', 'hashes'})
        if manifest['commit'] != pointer['commit'] or manifest['completed_at'] != pointer['completed_at']:
            raise Invalid()
        _keys(manifest['hashes'], FILES, FILES)
        docs = {}
        for name in FILES:
            file = path / name
            if file.is_symlink():
                raise Invalid()
            if file.stat().st_size > MAX_BYTES:
                raise Invalid()
            raw = file.read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest['hashes'][name]:
                raise Invalid()
            docs[name] = _decode(raw)
        completed = _validate(docs, datetime.now(timezone.utc))
        if completed != _time(pointer['completed_at']):
            raise Invalid()
        return path
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return None


class Limited(Exception):
    def __init__(self, until):
        self.until = until


def _get(client, url, now, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Invalid()
    response = client.get(url, timeout=(min(3, remaining), min(8, remaining)), allow_redirects=False, stream=True, verify=certifi.where())
    try:
        if response.status_code in (403, 429):
            until = now + timedelta(seconds=POLL_SECONDS)
            retry = response.headers.get('Retry-After')
            reset = response.headers.get('X-RateLimit-Reset')
            try:
                if retry:
                    due = now + timedelta(seconds=int(retry)) if str(retry).isdigit() else parsedate_to_datetime(retry)
                    until = max(until, due.astimezone(timezone.utc))
                if reset and str(reset).isdigit():
                    until = max(until, datetime.fromtimestamp(int(reset), timezone.utc))
            except (ValueError, TypeError, OverflowError):
                until = max(until, now + timedelta(hours=1))
            raise Limited(until)
        if response.status_code != 200:
            raise Invalid()
        length = response.headers.get('Content-Length')
        if length is not None and (not str(length).isdigit() or int(length) > MAX_BYTES):
            raise Invalid()
        data = bytearray(); began = time.monotonic()
        for chunk in response.iter_content(chunk_size=1):
            if time.monotonic() >= deadline or time.monotonic() - began > 15 or len(data) + len(chunk) > MAX_BYTES:
                raise Invalid()
            data.extend(chunk)
        return bytes(data)
    finally:
        response.close()


def refresh_shared_snapshot(now=None, root=None, client=None):
    """Poll at most once per five minutes, including failures and process restarts."""
    from live_data import runtime_directory
    try:
        checked = _now(now)
        root = Path(root) if root is not None else runtime_directory()
        root.mkdir(parents=True, exist_ok=True)
        with (root / '.shared-poll.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'outcome': 'BUSY'}
            marker = root / '.shared-poll.json'
            if marker.exists():
                state = _decode(marker.read_bytes())
                _keys(state, {'attempted_at', 'next_poll_at'}, {'attempted_at', 'next_poll_at'})
                attempted, following = _time(state['attempted_at']), _time(state['next_poll_at'])
                if attempted > checked or following < attempted + timedelta(seconds=POLL_SECONDS):
                    return {'outcome': 'INVALID_STATE'}
                if checked < following:
                    return {'outcome': 'THROTTLED'}
            _atomic(marker, {'attempted_at': checked.isoformat(), 'next_poll_at': (checked + timedelta(seconds=POLL_SECONDS)).isoformat()})
            own = client is None
            if own:
                client = requests.Session()
                client.trust_env = False
                client.auth = None
                client.headers.clear()
                client.headers.update({'Accept': 'application/json', 'User-Agent': 'fx-public-snapshot'})
                client.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
            staging = None
            try:
                deadline = time.monotonic() + 50
                ref = _decode(_get(client, REF_URL, checked, deadline))
                _keys(ref, {'ref', 'node_id', 'url', 'object'}, {'ref', 'object'})
                _keys(ref['object'], {'type', 'sha', 'url'}, {'type', 'sha'})
                obj = ref.get('object', {})
                commit = obj.get('sha')
                if ref.get('ref') != 'refs/heads/main' or obj.get('type') != 'commit' or not isinstance(commit, str) or not SHA.fullmatch(commit):
                    raise Invalid()
                old = current_shared_directory(root)
                if (root / '.shared-current.json').exists() and old is None:
                    raise Invalid()
                if old and old.name == commit:
                    return {'outcome': 'UNCHANGED', 'commit': commit}
                raw = {name: _get(client, RAW_BASE + commit + '/' + name, checked, deadline) for name in FILES}
                docs = {name: _decode(value) for name, value in raw.items()}
                completed = _validate(docs, checked)
                if old:
                    prior = _decode((old / 'manifest.json').read_bytes())
                    if completed <= _time(prior['completed_at']):
                        return {'outcome': 'OLDER'}
                target = _generation(root, commit)
                target.parent.mkdir(exist_ok=True)
                if target.exists():
                    # A crash may have published an immutable generation but not its pointer.
                    manifest = _decode((target / 'manifest.json').read_bytes())
                    expected_manifest = {'commit': commit, 'completed_at': docs[FILES[0]]['completed_at'],
                                         'hashes': {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}}
                    if manifest != expected_manifest or any((target / name).is_symlink() or (target / name).read_bytes() != raw[name] for name in FILES):
                        raise Invalid()
                    _atomic(root / '.shared-current.json', {'commit': commit, 'completed_at': docs[FILES[0]]['completed_at']})
                    return {'outcome': 'UPDATED', 'commit': commit}
                staging = Path(tempfile.mkdtemp(prefix='.incoming-', dir=target.parent))
                for name, value in raw.items():
                    with (staging / name).open('wb') as file:
                        file.write(value); file.flush(); os.fsync(file.fileno())
                _atomic(staging / 'manifest.json', {'commit': commit, 'completed_at': docs[FILES[0]]['completed_at'],
                                                   'hashes': {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}})
                os.rename(staging, target)
                staging = None
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                _atomic(root / '.shared-current.json', {'commit': commit, 'completed_at': docs[FILES[0]]['completed_at']})
                return {'outcome': 'UPDATED', 'commit': commit}
            except Limited as error:
                _atomic(marker, {'attempted_at': checked.isoformat(), 'next_poll_at': error.until.isoformat()})
                return {'outcome': 'RATE_LIMITED'}
            except (requests.RequestException, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
                return {'outcome': 'UNAVAILABLE'}
            finally:
                if staging is not None:
                    shutil.rmtree(staging, ignore_errors=True)
                if own:
                    client.close()
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return {'outcome': 'INVALID_STATE'}


def bundled_snapshot_directory(source, runtime_root):
    """Freeze a validated checkout bundle before rendering; no Git identity or HTTP."""
    staging = None
    try:
        source, root = Path(source), Path(runtime_root)
        raw = {}
        for name in FILES:
            path = source / name
            if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                raise Invalid()
            with path.open('rb') as file:
                value = file.read(MAX_BYTES + 1)
            if len(value) > MAX_BYTES:
                raise Invalid()
            raw[name] = value
        documents = {name: _decode(value) for name, value in raw.items()}
        _validate(documents, datetime.now(timezone.utc))
        hashes = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
        identity = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        base = root / 'bundled-snapshots'
        target = base / identity
        if base.is_symlink() or target.is_symlink():
            raise Invalid()
        manifest = {'identity_algorithm': 'sha256', 'identity': identity,
                    'completed_at': documents[FILES[0]]['completed_at'], 'hashes': hashes}
        base.mkdir(parents=True, exist_ok=True)
        with (base / '.freeze.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists():
                if (target / 'manifest.json').is_symlink() or _decode((target / 'manifest.json').read_bytes()) != manifest:
                    raise Invalid()
                for name in FILES:
                    path = target / name
                    if path.is_symlink() or path.stat().st_size > MAX_BYTES or path.read_bytes() != raw[name]:
                        raise Invalid()
                return target
            staging = Path(tempfile.mkdtemp(prefix='.incoming-', dir=base))
            for name, value in raw.items():
                with (staging / name).open('wb') as file:
                    file.write(value); file.flush(); os.fsync(file.fileno())
            _atomic(staging / 'manifest.json', manifest)
            os.rename(staging, target)
            staging = None
            descriptor = os.open(base, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return target
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
