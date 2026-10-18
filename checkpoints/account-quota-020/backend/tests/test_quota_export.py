"""Pure quota normalization and atomic local publication; no live accounts."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_quota_snapshots import snapshot

try:
    export = importlib.import_module('pocket.quota_export')
except ModuleNotFoundError as error:
    if error.name != 'pocket.quota_export': raise
    export = None


class NormalizerTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(export, 'pure numeric quota exporter is missing')

    def test_codex_weekly_only_primary_preserves_bucket_and_real_duration(self):
        raw = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 3,
            'windowDurationMins': 10080, 'resetsAt': 2000}, 'secondary': None}},
            'rateLimits': {'primary': {'usedPercent': 99}},
            'planType': 'synthetic-private', 'credits': {'balance': 'unrelated'}}
        before = deepcopy(raw)
        self.assertEqual(export.codex_snapshot(raw, fetched_at=1000), snapshot(metrics=[
            {'window': 'codex:primary', 'used_percent': 3, 'resets_at': 2000, 'window_minutes': 10080}]))
        self.assertEqual(raw, before)

    def test_codex_legacy_independent_windows_unknown_and_empty_map_preferred(self):
        result = export.codex_snapshot({'rateLimits': {'primary': {'usedPercent': None},
            'secondary': {'usedPercent': 0, 'resetsAt': 2100, 'windowDurationMins': 10080}}}, fetched_at=1000)
        self.assertEqual(result['metrics'], [
            {'window': 'codex:primary', 'used_percent': None, 'resets_at': None},
            {'window': 'codex:secondary', 'used_percent': 0, 'resets_at': 2100, 'window_minutes': 10080}])
        result = export.codex_snapshot({'rateLimitsByLimitId': {}, 'rateLimits': {
            'primary': {'usedPercent': 88}}}, fetched_at=1000)
        self.assertEqual(result['metrics'], [])

    def test_codex_multiple_buckets_are_never_merged(self):
        result = export.codex_snapshot({'rateLimitsByLimitId': {
            'codex': {'primary': {'usedPercent': 4}},
            'model': {'secondary': {'usedPercent': 6}}}}, fetched_at=1000)
        self.assertEqual([(m['window'], m['used_percent']) for m in result['metrics']],
                         [('codex:primary', 4), ('model:secondary', 6)])

    def test_claude_five_hour_seven_day_and_context_are_separate(self):
        raw = {'rate_limits': {'five_hour': {'used_percentage': 12.5, 'resets_at': 1100},
            'seven_day': {'used_percentage': 40, 'resets_at': 2000}},
            'context_window': {'used_percentage': 99}, 'session_id': 'synthetic-private'}
        self.assertEqual(export.claude_snapshot(raw, fetched_at=1000), snapshot('claude', metrics=[
            {'window': 'five_hour', 'used_percent': 12.5, 'resets_at': 1100, 'window_minutes': 300},
            {'window': 'seven_day', 'used_percent': 40, 'resets_at': 2000, 'window_minutes': 10080}]))

    def test_absent_quota_does_not_invent_windows_or_zero(self):
        for raw in ({}, {'context_window': {'used_percentage': 5}}, {'rate_limits': None}):
            self.assertEqual(export.claude_snapshot(raw, fetched_at=1000)['metrics'], [])
        self.assertEqual(export.codex_snapshot({}, fetched_at=1000)['metrics'], [])
        self.assertEqual(export.claude_snapshot({'rate_limits': {'seven_day': {}}}, fetched_at=1000)['metrics'],
            [{'window': 'seven_day', 'used_percent': None, 'resets_at': None, 'window_minutes': 10080}])

    def test_invalid_supported_fields_do_not_become_unknown_or_partially_publish(self):
        for value in (True, -1, 101, '4', float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.codex_snapshot({'rateLimits': {'primary': {'usedPercent': value}}}, fetched_at=1000)
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.claude_snapshot({'rate_limits': {'five_hour': {'used_percentage': value}}}, fetched_at=1000)
        for duration in (True, 0, 2.5, 4294967296):
            with self.assertRaises(ValueError):
                export.codex_snapshot({'rateLimits': {'primary': {'windowDurationMins': duration}}}, fetched_at=1000)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(export, 'pure numeric quota exporter is missing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / '.tasks.sqlite.account-quota-v1'
        self.folder.mkdir(mode=0o700)
        self.path = self.folder / 'codex.json'

    def test_atomic_publish_writes_only_validated_snapshot_and_private_file(self):
        written = export.publish_snapshot(snapshot(), self.path)
        self.assertEqual(written, snapshot())
        self.assertEqual(json.loads(self.path.read_bytes()), snapshot())
        self.assertEqual([p.name for p in self.folder.iterdir()], ['codex.json'])
        if os.name == 'posix': self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_cached_identical_payload_preserves_observation_but_authoritative_refresh_advances(self):
        export.publish_snapshot(snapshot(), self.path)
        cached = export.publish_snapshot(snapshot(fetched_at=1100), self.path, cached_event=True)
        self.assertEqual(cached['fetched_at'], 1000)
        fresh = export.publish_snapshot(snapshot(fetched_at=1100), self.path)
        self.assertEqual(fresh['fetched_at'], 1100)
        self.assertEqual(json.loads(self.path.read_bytes())['fetched_at'], 1100)

    def test_changed_cached_payload_may_advance_but_old_and_same_time_conflict_never_replace(self):
        export.publish_snapshot(snapshot(), self.path)
        for data in (snapshot(fetched_at=999), snapshot(metrics=[])):
            with self.assertRaises(ValueError): export.publish_snapshot(data, self.path)
            self.assertEqual(json.loads(self.path.read_bytes()), snapshot())
        changed = snapshot(fetched_at=1010, metrics=[])
        self.assertEqual(export.publish_snapshot(changed, self.path, cached_event=True), changed)

    def test_empty_or_bad_existing_output_is_not_silently_overwritten(self):
        self.path.write_bytes(b'synthetic-private-error'); self.path.chmod(0o600)
        with self.assertRaises(ValueError): export.publish_snapshot(snapshot(), self.path)
        self.assertEqual(self.path.read_bytes(), b'synthetic-private-error')

    def test_invalid_snapshot_and_output_path_are_rejected_without_writes(self):
        data = snapshot(); data['secret'] = 'synthetic-private'
        with self.assertRaises(ValueError): export.publish_snapshot(data, self.path)
        for path in (self.root / 'codex.json', self.folder / 'claude.json',
                     self.folder / 'other.json', self.root / 'missing' / 'codex.json'):
            with self.assertRaises((ValueError, OSError)): export.publish_snapshot(snapshot(), path)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_replace_failure_preserves_previous_snapshot_and_cleans_temporary(self):
        export.publish_snapshot(snapshot(), self.path)
        with patch.object(export.os, 'replace', side_effect=OSError('synthetic-private-error')):
            with self.assertRaises((ValueError, OSError)):
                export.publish_snapshot(snapshot(fetched_at=1010, metrics=[]), self.path)
        self.assertEqual(json.loads(self.path.read_bytes()), snapshot())
        self.assertEqual([p.name for p in self.folder.iterdir()], ['codex.json'])

    def test_replacement_after_previous_read_cannot_adopt_unrelated_file_revision(self):
        export.publish_snapshot(snapshot(), self.path)
        original = export.read_private; changed = False
        def replace_after_read(*args, **kwargs):
            nonlocal changed
            previous = original(*args, **kwargs)
            if not changed:
                changed = True
                replacement = self.folder / 'synthetic-replacement'
                replacement.write_text(json.dumps(snapshot(fetched_at=1100)))
                replacement.chmod(0o600); os.replace(replacement, self.path)
            return previous
        with patch.object(export, 'read_private', side_effect=replace_after_read):
            with self.assertRaises(ValueError):
                export.publish_snapshot(snapshot(fetched_at=1050), self.path)
        self.assertEqual(json.loads(self.path.read_bytes())['fetched_at'], 1100)

    def test_competing_publishers_never_overwrite_a_successful_newer_observation(self):
        export.publish_snapshot(snapshot(), self.path)
        reached = threading.Event(); release = threading.Event(); errors = []
        original = export.os.replace
        def gated_replace(*args, **kwargs):
            if threading.current_thread().name == 'quota-first':
                reached.set()
                if not release.wait(3): raise AssertionError('publication gate timed out')
            return original(*args, **kwargs)
        def first():
            try: export.publish_snapshot(snapshot(fetched_at=1010), self.path)
            except ValueError: pass  # A newer committed observation may reject this writer.
            except BaseException as error: errors.append(error)
        with patch.object(export.os, 'replace', side_effect=gated_replace):
            thread = threading.Thread(target=first, name='quota-first'); thread.start()
            try:
                self.assertTrue(reached.wait(2), 'first publication did not reach atomic replacement')
                try:
                    export.publish_snapshot(snapshot(fetched_at=1020), self.path)
                    second_succeeded = True
                except ValueError:
                    second_succeeded = False  # Busy publication may fail safely and be retried.
            finally: release.set(); thread.join(4)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(json.loads(self.path.read_bytes())['fetched_at'],
                         1020 if second_succeeded else 1010)

    def test_hardlink_and_directory_outputs_are_rejected(self):
        other = self.root / 'synthetic'; other.write_text(json.dumps(snapshot())); other.chmod(0o600)
        os.link(other, self.path)
        with self.assertRaises((ValueError, OSError)): export.publish_snapshot(snapshot(), self.path)
        self.path.unlink(); self.path.mkdir()
        with self.assertRaises((ValueError, OSError)): export.publish_snapshot(snapshot(), self.path)

    @unittest.skipUnless(os.name == 'posix', 'private POSIX permissions and links require Linux gate')
    def test_unsafe_public_parent_file_symlink_and_fifo_are_rejected(self):
        self.folder.chmod(0o755)
        with self.assertRaises(ValueError): export.publish_snapshot(snapshot(), self.path)
        self.folder.chmod(0o700)
        self.path.write_text(json.dumps(snapshot())); self.path.chmod(0o644)
        with self.assertRaises(ValueError): export.publish_snapshot(snapshot(), self.path)
        self.path.unlink(); other = self.root / 'other'; other.write_text('synthetic-private')
        self.path.symlink_to(other)
        with self.assertRaises((ValueError, OSError)): export.publish_snapshot(snapshot(), self.path)
        self.path.unlink(); os.mkfifo(self.path, 0o600)
        with self.assertRaises(ValueError): export.publish_snapshot(snapshot(), self.path)

    def cli(self, raw, *extra):
        return subprocess.run([sys.executable, '-B', '-m', 'pocket.quota_export',
            '--provider', 'codex', '--source', 'codex_app', '--fetched-at', '1000',
            '--output', str(self.path), *extra], input=raw, capture_output=True, timeout=5)

    def test_cli_bounded_stdin_strips_unrelated_fields_and_has_no_response_text(self):
        raw = b'{"rateLimits":{"primary":{"usedPercent":8}},"token":"synthetic-private"}'
        result = self.cli(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'synthetic-private', self.path.read_bytes())
        self.assertEqual(json.loads(self.path.read_bytes())['metrics'], [
            {'window': 'codex:primary', 'used_percent': 8, 'resets_at': None}])

    def test_cli_rejects_oversize_duplicates_nan_invalid_utf8_and_deep_json_safely(self):
        for raw in (b' ' * 65537, b'{"rateLimits":{},"rateLimits":{}}',
                    b'{"rateLimits":{"primary":{"usedPercent":NaN}}}', b'\xff',
                    b'{"unrelated":1e999}',
                    b'[' * 2000 + b']' * 2000, b'{"synthetic-private-error":'):
            result = self.cli(raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertEqual(result.stderr.strip(), b'quota_export: invalid_input')
            self.assertFalse(self.path.exists())

    def test_cli_accepts_exact_65536_limit_and_strict_canonical_handoff(self):
        raw = b'{}'; result = self.cli(raw + b' ' * (65536 - len(raw)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.path.unlink()
        result = self.cli(json.dumps(snapshot()).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_bytes()), snapshot())

    def test_cli_source_provider_mismatch_and_parse_errors_only_emit_safe_code(self):
        for extra in (('--source', 'claude_code'), ('--fetched-at', 'synthetic-private'),
                      ('--source', 'synthetic-private')):
            result = self.cli(b'{}', *extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), b'quota_export: invalid_input')


class FuturePublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / '.tasks.sqlite.account-quota-v1'
        self.folder.mkdir(mode=0o700)
        self.path = self.folder / 'codex.json'

    def raw_write(self, data):
        self.path.write_text(json.dumps(data)); self.path.chmod(0o600)

    @patch('time.time', return_value=1000)
    def test_excessively_future_candidate_never_accesses_or_changes_good_output(self, clock):
        export.publish_snapshot(snapshot(), self.path)
        previous = self.path.read_bytes()
        for cached in (False, True):
            with self.subTest(cached_event=cached):
                try:
                    with patch.object(export, 'PrivateDirectory', side_effect=AssertionError('future input accessed output')):
                        with self.assertRaises(ValueError):
                            export.publish_snapshot(snapshot(fetched_at=87400), self.path, cached_event=cached)
                except AssertionError as error:
                    self.fail(str(error))
                self.assertEqual(self.path.read_bytes(), previous)
                self.assertEqual([p.name for p in self.folder.iterdir()], ['codex.json'])
        clock.return_value = 1015
        self.assertEqual(export.publish_snapshot(snapshot(fetched_at=1015), self.path)['fetched_at'], 1015)

    @patch('time.time', return_value=1000)
    def test_initial_future_candidate_is_rejected_without_creating_file(self, clock):
        with self.assertRaises(ValueError):
            export.publish_snapshot(snapshot(fetched_at=1031), self.path)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_injected_clock_accepts_30_second_boundary_and_rejects_invalid_clocks(self):
        try:
            current = export.publish_snapshot(snapshot(fetched_at=1030), self.path, clock=lambda: 1000)
        except TypeError:
            self.fail('publisher must accept an explicit observation clock')
        self.assertEqual(current['fetched_at'], 1030)
        previous = self.path.read_bytes()
        with self.assertRaises(ValueError):
            export.publish_snapshot(snapshot(fetched_at=1031), self.path, clock=lambda: 1000)
        for now in (True, float('nan'), float('inf'), -1, 2**53):
            with self.subTest(clock=now), self.assertRaises(ValueError):
                export.publish_snapshot(snapshot(fetched_at=1030), self.path, clock=lambda: now)
        self.assertEqual(self.path.read_bytes(), previous)

    @patch('time.time', return_value=1015)
    def test_fresh_authoritative_observation_recovers_already_poisoned_future_file(self, clock):
        self.raw_write(snapshot(fetched_at=87400))
        current = snapshot(fetched_at=1015, metrics=[
            {'window': 'codex:primary', 'used_percent': 20, 'resets_at': 2000}])
        try:
            written = export.publish_snapshot(current, self.path)
        except ValueError:
            self.fail('fresh authoritative data must be able to replace an excessive-future poison file')
        self.assertEqual(written, current)
        self.assertEqual(json.loads(self.path.read_bytes()), current)
        with self.assertRaises(ValueError):
            export.publish_snapshot(snapshot(fetched_at=1014), self.path)
        with self.assertRaises(ValueError):
            export.publish_snapshot(snapshot(fetched_at=1015), self.path)
        self.assertEqual(json.loads(self.path.read_bytes()), current)

    @patch('time.time', return_value=1000)
    def test_poison_recovery_requires_fresh_nonfuture_authoritative_data(self, clock):
        self.raw_write(snapshot(fetched_at=87400)); previous = self.path.read_bytes()
        for stamp, cached in ((700, False), (699, False), (1001, False), (1000, True)):
            with self.subTest(fetched_at=stamp, cached_event=cached), self.assertRaises(ValueError):
                export.publish_snapshot(snapshot(fetched_at=stamp), self.path, cached_event=cached)
            self.assertEqual(self.path.read_bytes(), previous)
        try:
            written = export.publish_snapshot(snapshot(fetched_at=701), self.path)
        except ValueError:
            self.fail('a nonfuture authoritative observation younger than the 300-second TTL may recover poison')
        self.assertEqual(written['fetched_at'], 701)

    @patch('time.time', return_value=1000)
    def test_valid_recent_and_near_future_snapshots_keep_replay_and_equal_conflict_guards(self, clock):
        for previous_stamp in (995, 1030):
            self.raw_write(snapshot(fetched_at=previous_stamp)); previous = self.path.read_bytes()
            for current in (snapshot(fetched_at=previous_stamp-1), snapshot(fetched_at=previous_stamp, metrics=[])):
                with self.subTest(previous=previous_stamp, current=current), self.assertRaises(ValueError):
                    export.publish_snapshot(current, self.path)
                self.assertEqual(self.path.read_bytes(), previous)

    def test_cli_uses_actual_time_and_preserves_good_file_after_future_rejection(self):
        now = int(time.time())
        def run(stamp):
            return subprocess.run([sys.executable, '-B', '-m', 'pocket.quota_export',
                '--provider', 'codex', '--source', 'codex_app', '--fetched-at', str(stamp),
                '--output', str(self.path)], input=json.dumps(snapshot(fetched_at=stamp)).encode(),
                capture_output=True, timeout=5)
        first = run(now)
        self.assertEqual(first.returncode, 0, first.stderr)
        previous = self.path.read_bytes()
        future = run(now + 86400)
        self.assertNotEqual(future.returncode, 0)
        self.assertEqual(future.stderr.strip(), b'quota_export: invalid_input')
        self.assertEqual(future.stdout, b'')
        self.assertEqual(self.path.read_bytes(), previous)
