"""Synthetic fixtures only: not economic measurements."""
import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from ec_industry_research import fetch, validate_artifact, _snapshot, render_ec_industry
from datetime import datetime, timezone
from ec_industry_research import DIMENSIONS, PARAMS, parse_json, parse_response

NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


def fixture():
    dimensions = {name: {'category': {'index': {PARAMS[name]: 0}}} for name in DIMENSIONS[:5]}
    dimensions['time'] = {'category': {'index': {'2026-07': 0, '2026-08': 1}}}
    return dict(version='2.0', **{'class': 'dataset'}, source='ESTAT', id=DIMENSIONS.copy(),
                size=[1, 1, 1, 1, 1, 2], dimension=dimensions,
                extension=dict(id='EI_BSIN_M_R2', agencyId='ESTAT'),
                updated='2026-08-28T11:00:00+0200', value={'0': -1, '1': -2})


def parse(data):
    return parse_response(data, retrieved_at=NOW, now=NOW)


class AdapterTests(unittest.TestCase):
    def test_exact_identity_and_metadata(self):
        result = parse(fixture())
        self.assertEqual(result['value'], -2)
        self.assertEqual(result['reference_period_age_days'], 12)
        self.assertIsNone(result['published_at'])
        self.assertEqual(result['dataset_updated_at'], '2026-08-28T09:00:00+00:00')
        self.assertIs(result['core_eligible'], False)
        self.assertIsNone(result['signal'])
        self.assertEqual(result['unit'], 'balance_points')

    def test_missing_latest_not_imputed(self):
        data = fixture(); del data['value']['1']
        result = parse(data)
        self.assertEqual(result['value'], -1)
        self.assertTrue(result['latest_indexed_period_missing'])
        self.assertEqual(result['missing_periods'], ['2026-08'])
        self.assertIsNone(result['observations'][-1]['value'])
        data['value'] = {}
        self.assertIsNone(parse(data)['value'])

    def test_absent_calendar_month_retained(self):
        data = fixture(); data['dimension']['time']['category']['index'] = {'2026-06': 0, '2026-08': 1}
        self.assertEqual(parse(data)['absent_calendar_periods'], ['2026-07'])

    def test_identity_and_dimension_layouts_rejected(self):
        for field, value in [('source', 'UNKNOWN'), ('id', list(reversed(DIMENSIONS))),
                             ('size', [True, 1, 1, 1, 1, 2]), ('extension', {})]:
            data = fixture(); data[field] = value
            with self.assertRaises(ValueError): parse(data)
        for name, wrong in [('geo', 'EA20'), ('unit', 'IX'), ('indic', 'PMI'), ('s_adj', 'NSA')]:
            data = fixture(); data['dimension'][name]['category']['index'] = {wrong: 0}
            with self.assertRaises(ValueError): parse(data)

    def test_time_index_future_malformed_duplicate_positions_rejected(self):
        for index in [{'2026-13': 0, '2026-08': 1}, {'2026-07': 0, '2026-10': 1},
                      {'2026-07': 0, '2026-08': 0}, {'2026-07': False, '2026-08': 1},
                      {'2026-07': 1, '2026-08': 0}, {'2026-7': 0, '2026-08': 1}]:
            data = fixture(); data['dimension']['time']['category']['index'] = index
            with self.assertRaises(ValueError): parse(data)

    def test_invalid_values_and_indices_rejected(self):
        for value in [True, float('nan'), float('inf'), '-2', -101]:
            data = fixture(); data['value']['1'] = value
            with self.assertRaises(ValueError): parse(data)
        for key in ['2', '-1', '01', 'x']:
            data = fixture(); data['value'][key] = 1
            with self.assertRaises(ValueError): parse(data)

    def test_duplicate_json_keys_and_nonfinite_constants_rejected(self):
        raw = json.dumps(fixture())
        for invalid in [raw.replace('"source": "ESTAT"', '"source": "ESTAT", "source": "ESTAT"'),
                        raw.replace('"1": -2', '"1": NaN')]:
            with self.assertRaises(ValueError): parse_json(invalid, retrieved_at=NOW, now=NOW)
        self.assertEqual(len(parse_json(raw, retrieved_at=NOW, now=NOW)['payload_sha256']), 64)

    def test_future_updated_and_naive_retrieval_rejected(self):
        data = fixture(); data['updated'] = '2026-09-13T00:00:00Z'
        with self.assertRaises(ValueError): parse(data)
        with self.assertRaises(ValueError): parse_response(fixture(), retrieved_at=NOW.replace(tzinfo=None), now=NOW)

    def test_missing_status_conflict_rejected(self):
        data = fixture(); data['status'] = {'1': ':'}
        with self.assertRaises(ValueError): parse(data)
        data['value']['1'] = None
        self.assertTrue(parse(data)['latest_indexed_period_missing'])

    def test_unreviewed_flags_cannot_qualify_numeric_or_missing_values(self):
        for flag in ('p', 'e', 'c', 'unknown', '', ' '):
            for value in (-2, None):
                with self.subTest(flag=flag, value=value):
                    data = fixture()
                    data['status'] = {'1': flag}
                    data['value']['1'] = value
                    with self.assertRaises(ValueError):
                        parse(data)





def saved_artifact(data=None, now=NOW):
    source = data or fixture()
    result = parse_json(json.dumps(source), retrieved_at=now, now=now)
    result['source_snapshot'] = _snapshot(source)
    return result


class ProductionTests(unittest.TestCase):
    def test_fetch_is_bounded_and_no_redirect(self):
        response = Mock(status_code=200, text=json.dumps(fixture()))
        session = Mock(); session.get.return_value = response
        result = fetch(session=session, now=NOW)
        self.assertEqual(validate_artifact(result, now=NOW)['value'], -2)
        args = session.get.call_args.kwargs
        self.assertEqual(args['params']['lastTimePeriod'], 13)
        self.assertEqual(args['timeout'], 40)
        self.assertIs(args['allow_redirects'], False)
        response.status_code = 302
        with self.assertRaises(ValueError): fetch(session=session, now=NOW)

    def test_artifact_rejects_core_and_tampering(self):
        for field, value in [('core_eligible', 0), ('mode', 'live'), ('value', -3),
                             ('published_at', '2026-08-28'), ('payload_sha256', ''),
                             ('unit', 'index'), ('signal', 'BUY')]:
            data = saved_artifact(); data[field] = value
            with self.assertRaises(ValueError): validate_artifact(data, now=NOW)

    def test_due_date_guard_and_unknown_calendar_hourly(self):
        data = saved_artifact()
        self.assertEqual(validate_artifact(data, now=NOW)['freshness_state'], 'verified_calendar_window')
        due = datetime(2026, 9, 29, tzinfo=timezone.utc)
        self.assertEqual(validate_artifact(data, now=due)['freshness_state'], 'release_due_unconfirmed')
        source = fixture()
        source['dimension']['time']['category']['index'] = {'2026-08': 0, '2026-09': 1}
        data = saved_artifact(source, now=due)
        self.assertEqual(validate_artifact(data, now=due)['freshness_state'], 'calendar_unknown_recent_check')
        later = datetime(2026, 9, 29, 2, tzinfo=timezone.utc)
        self.assertEqual(validate_artifact(data, now=later)['freshness_state'], 'calendar_unknown_check_overdue')

    def test_real_streamlit_display_and_missing_file(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ec_industry.json'
            path.write_text(json.dumps(saved_artifact(now=datetime(2026, 9, 11, tzinfo=timezone.utc))))
            script = "import streamlit as st\nfrom ec_industry_research import render_ec_industry\nrender_ec_industry(st, " + repr(str(path)) + ')'
            app = AppTest.from_string(script).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[0].value, '-2.0')
            self.assertIn('Saldopunkte', app.metric[0].label)
            path.write_text('{')
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 0)
            self.assertTrue(any('Keine gültigen' in str(w.value) for w in app.warning))

    def test_app_status_error_is_sanitized(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ec_industry.json'
            path.write_text(json.dumps(saved_artifact(now=datetime(2026, 9, 11, tzinfo=timezone.utc))))
            path.with_name('ec_industry_status.json').write_text(json.dumps(dict(
                schema='ec-industry-research-collection-v1', mode='research_only',
                core_eligible=False, status='failed', last_attempt_at='2026-09-01T00:00:00Z',
                error_code='secret_token_never_render')))
            script = "import streamlit as st\nfrom ec_industry_research import render_ec_industry\nrender_ec_industry(st, " + repr(str(path)) + ')'
            app = AppTest.from_string(script).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any('fehlgeschlagen' in w.value for w in app.warning))
            self.assertNotIn('secret_token_never_render', str(app))


if __name__ == '__main__': unittest.main()
