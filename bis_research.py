"""Isolated BIS research adapter: never releases a production CORE signal."""
import csv
import hashlib
import io
import math
from datetime import date, datetime, timedelta, timezone

import requests

ENDPOINT = 'https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.NZ'
DOC = 'https://www.bis.org/statistics/cbpol/cbpol_doc.pdf'
LEGAL = 'https://data.bis.org/help/legal'
REQUIRED = {'FREQ','REF_AREA','UNIT_MEASURE','UNIT_MULT','COMPILATION','SOURCE_REF','TIME_PERIOD','OBS_VALUE','OBS_STATUS','OBS_CONF'}


def parse_csv(text, *, retrieved_at):
    if retrieved_at.tzinfo is None:
        raise ValueError('Retrieval time must be timezone-aware')
    reader = csv.DictReader(io.StringIO(text))
    if (not REQUIRED.issubset(set(reader.fieldnames or []))
            or len(reader.fieldnames) != len(set(reader.fieldnames))):
        raise ValueError('BIS schema mismatch')
    observations, missing, seen = [], [], set()
    for row in reader:
        if None in row or any(row.get(k) is None for k in REQUIRED):
            raise ValueError('Malformed CSV row')
        if any(row[k] != v for k,v in {'FREQ':'D','REF_AREA':'NZ','UNIT_MEASURE':'368','UNIT_MULT':'0','SOURCE_REF':'Reserve Bank of New Zealand','OBS_CONF':'F'}.items()):
            raise ValueError('Unexpected series definition, units or confidentiality')
        if 'from 17 mar 1999 onwards:' not in row['COMPILATION'].lower() or 'official cash rate' not in row['COMPILATION'].lower():
            raise ValueError('Instrument definition not confirmed')
        period = date.fromisoformat(row['TIME_PERIOD'])
        if period.isoformat() != row['TIME_PERIOD'] or period < date(1999,3,17) or period > retrieved_at.astimezone(timezone.utc).date():
            raise ValueError('Unsupported or future observation date')
        if period in seen:
            raise ValueError('Duplicate observation date')
        seen.add(period)
        if row['OBS_STATUS'] == 'M':
            if row['OBS_VALUE'].strip().lower() not in ('nan', ''):
                raise ValueError('Missing flag conflicts with numeric value')
            missing.append(period.isoformat())
            continue
        if row['OBS_STATUS'] != 'A':
            raise ValueError('Unreviewed observation status')
        value = float(row['OBS_VALUE'])
        if not math.isfinite(value) or not -10 <= value <= 100:
            raise ValueError('Invalid policy rate')
        observations.append({'observation_date':period.isoformat(),'value':value})
    if not observations:
        raise ValueError('No usable BIS observation')
    observations.sort(key=lambda x:x['observation_date'])
    latest = observations[-1]
    age = (retrieved_at.astimezone(timezone.utc).date()-date.fromisoformat(latest['observation_date'])).days
    return {
        'schema':'bis-nz-policy-research-v1','mode':'research_only','core_eligible':False,
        'core_block_reason':'Weekly BIS dissemination does not establish hourly RBNZ freshness or current decision/effective-date verification',
        'currency':'NZD','series':'BIS:WS_CBPOL(1.0):D.NZ','instrument':'Official Cash Rate',
        'unit':'percent_per_year','seasonal_adjustment':'not_seasonally_adjusted',
        'observation_frequency':'daily','dissemination_cadence':'weekly, around mid-week',
        'value':latest['value'],'observation_date':latest['observation_date'],
        'retrieved_at':retrieved_at.astimezone(timezone.utc).isoformat(),
        'published_at':None,'next_publication_at':None,'rate_effective_date':None,
        'observation_age_days':age,
        'research_status':'historical_observation_stale' if age > 14 else 'weekly_backup_observation',
        'research_stale_threshold_days':14,
        'status_note':'14-day threshold is a research display warning, not a provider SLA or CORE age limit',
        'source':'Bank for International Settlements; underlying national source: Reserve Bank of New Zealand',
        'source_url':ENDPOINT,'methodology_url':DOC,'terms_url':LEGAL,
        'attribution':'Source: BIS, Central bank policy rates; Reserve Bank of New Zealand.',
        'translation_notice':'German descriptions, if displayed, are not an official BIS translation.',
        'payload_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'missing_observation_dates':missing,'observations':observations,
    }


def fetch(*, session=None, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Collection time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    session = session or requests.Session()
    response = session.get(ENDPOINT, params={'startPeriod':(now.date()-timedelta(days=45)).isoformat(),'format':'csv'}, timeout=(10, 40), allow_redirects=False)
    if response.status_code != 200:
        raise requests.HTTPError("Unexpected BIS HTTP status")
    response.raise_for_status()
    return parse_csv(response.text,retrieved_at=now)
