import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from research_panel import SOURCE_URL, render_research_panel, validate_research_artifact, render_archive_summary


def artifact():
    return dict(schema='bis-nz-policy-research-v1', mode='research_only', core_eligible=False,
                currency='NZD', series='BIS:WS_CBPOL(1.0):D.NZ', instrument='Official Cash Rate',
                unit='percent_per_year', seasonal_adjustment='not_seasonally_adjusted',
                observation_frequency='daily', source_url=SOURCE_URL, published_at=None,
                next_publication_at=None, rate_effective_date=None, payload_sha256='a'*64,
                retrieved_at='2026-09-12T11:00:00+00:00', observation_date='2026-09-04',
                value=2.75, observations=[dict(observation_date='2026-09-04', value=2.75)],
                observation_age_days=0, research_status='fresh')


class FakeStreamlit:
    def __init__(self):
        self.messages = []
        self.metrics = []
    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.messages.append((name, args))
            if name == 'metric':
                self.metrics.append(args)
        return record


class ResearchPanelTests(unittest.TestCase):
    def test_archive_summary_rejects_missing_anchor_and_duplicate_status_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            for content in ('{"archive_event_count":2,"archive_head":null}',
                            '{"archive_event_count":2,"archive_event_count":0}'):
                Path(tmp, 'status.json').write_text(content)
                st = FakeStreamlit()
                render_archive_summary(st, tmp, 'bis_nz_policy')
                self.assertTrue(any(name == 'warning' for name, _ in st.messages))
                self.assertNotIn('noch kein tatsächlich', str(st.messages))

    def test_archive_summary_reports_actual_history_and_sanitizes_corruption(self):
        from research_vintages import append_vintage
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bis_nz_policy_vintages.json'
            sample = dict(artifact(), missing_observation_dates=[])
            append_vintage(path, 'bis_nz_policy', sample, datetime.now(timezone.utc))
            st = FakeStreamlit()
            render_archive_summary(st, tmp, 'bis_nz_policy')
            self.assertIn('1 erfasste Datenstände', str(st.messages))
            path.write_text('INVALID_PRIVATE_EXCEPTION_TEXT')
            st = FakeStreamlit()
            render_archive_summary(st, tmp, 'bis_nz_policy')
            self.assertNotIn('INVALID_PRIVATE_EXCEPTION_TEXT', str(st.messages))
            self.assertTrue(any(name == 'warning' for name, _ in st.messages))

    def setUp(self):
        self.now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)

    def test_read_time_age_ignores_cached_freshness(self):
        result = validate_research_artifact(artifact(), now=self.now)
        self.assertEqual(result['observation_age_days'], 8)
        self.assertEqual(result['retrieval_age_hours'], 1)
        later = validate_research_artifact(artifact(), now=datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertTrue(later['stale'])
        self.assertFalse(later['core_eligible'])
        self.assertEqual(later['value'], 2.75)

    def test_real_streamlit_panel_renders_populated_and_invalid_artifact(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'research.json'
            sample = artifact()
            sample['retrieved_at'] = datetime.now(timezone.utc).isoformat()
            path.write_text(json.dumps(sample))
            code = ('import streamlit as st\n'
                    'from research_panel import render_research_panel\n'
                    f'render_research_panel(st, {str(path)!r})\n')
            app = AppTest.from_string(code).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[0].value, '2.75 %')
            self.assertTrue(any('Beobachtungsdatum: 2026-09-04' in m.value for m in app.markdown))
            path.write_text(json.dumps(dict(sample, core_eligible=True)))
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 0)

    def test_identity_and_invalid_rates_rejected(self):
        cases = [('core_eligible', True), ('core_eligible', 0), ('mode', 'live'),
                 ('unit', 'basis_points'), ('currency', 'AUD'), ('value', True),
                 ('value', float('nan')), ('value', float('inf')), ('value', '2.75'),
                 ('published_at', '2026-09-12'), ('source_url', 'https://example.com'),
                 ('payload_sha256', '')]
        for key, value in cases:
            with self.subTest(key=key):
                data = artifact(); data[key] = value
                with self.assertRaises(ValueError):
                    validate_research_artifact(data, now=self.now)

    def test_future_and_naive_timestamps_rejected(self):
        for timestamp in ['2026-09-12T13:00:00+00:00', '2026-09-12T11:00:00']:
            data = artifact(); data['retrieved_at'] = timestamp
            with self.assertRaises(ValueError):
                validate_research_artifact(data, now=self.now)
        data = artifact(); data['observation_date'] = '2026-09-13'
        with self.assertRaises(ValueError):
            validate_research_artifact(data, now=self.now)

    def test_history_conflicts_rejected(self):
        for history in [[], [dict(observation_date='2026-09-04', value=3)],
                        artifact()['observations'] * 2,
                        [dict(observation_date='2026-09-05', value=2.75)]]:
            data = artifact(); data['observations'] = history
            with self.assertRaises(ValueError):
                validate_research_artifact(data, now=self.now)

    def test_missing_corrupt_and_invalid_files_never_show_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'research.json'
            for content in [None, '{', '[]', json.dumps(dict(artifact(), core_eligible=True))]:
                if content is not None:
                    path.write_text(content)
                st = FakeStreamlit()
                render_research_panel(st, path)
                self.assertEqual(st.metrics, [])
                self.assertTrue(any(name == 'warning' for name, _ in st.messages))

    def test_collection_failure_is_sanitized_and_does_not_hide_historical_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'research.json'
            path.write_text(json.dumps(artifact()))
            status = dict(schema='bis-research-collection-v1', mode='research_only',
                          core_eligible=False, status='failed',
                          last_attempt_at='2026-09-12T00:00:00+00:00',
                          error_code='SECRET_FROM_EXCEPTION')
            path.with_name('status.json').write_text(json.dumps(status))
            st = FakeStreamlit()
            result = validate_research_artifact(artifact(), now=self.now)
            with patch('research_panel.load_research_artifact', return_value=result):
                render_research_panel(st, path)
            self.assertEqual(len(st.metrics), 1)
            self.assertIn('fehlgeschlagen', str(st.messages))
            self.assertNotIn('SECRET_FROM_EXCEPTION', str(st.messages))

    def test_stale_render_warns_and_keeps_historical_label(self):
        result = validate_research_artifact(artifact(), now=datetime(2026, 9, 20, tzinfo=timezone.utc))
        st = FakeStreamlit()
        with patch('research_panel.load_research_artifact', return_value=result):
            render_research_panel(st, 'unused')
        self.assertIn('Historischer', st.metrics[0][0])
        text = str(st.messages)
        self.assertIn('älter als 14 Tage', text)
        self.assertIn('Wirksamkeitsdatum: unbekannt', text)
        self.assertIn('2026-09-04', text)


if __name__ == '__main__':
    unittest.main()
