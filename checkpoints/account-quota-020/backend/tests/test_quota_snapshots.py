"""Private numeric quota sources; all files and observations are synthetic."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from pocket.store import Store
from pocket.usage import UsageCache

try:
    quota = importlib.import_module('pocket.quota_snapshots')
except ModuleNotFoundError as error:
    if error.name != 'pocket.quota_snapshots': raise
    quota = None


def snapshot(provider='codex', fetched_at=1000, metrics=None):
    return {'version': 1, 'scope': 'account', 'provider': provider,
            'source': {'codex': 'codex_app', 'claude': 'claude_code'}[provider],
            'fetched_at': fetched_at, 'metrics': metrics if metrics is not None else [
                {'window': 'codex:primary', 'used_percent': 25, 'resets_at': 2000,
                 'window_minutes': 10080}]}


class SnapshotValidationTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(quota, 'bounded private account snapshot feature is missing')

    def test_exact_envelope_and_numeric_copy(self):
        data = snapshot()
        result = quota.validate_snapshot(data, provider='codex')
        self.assertEqual(result, {'version': 1, 'scope': 'account', 'provider': 'codex',
            'source': 'codex_app', 'fetched_at': 1000, 'metrics': [
                {'window': 'codex:primary', 'used_percent': 25, 'resets_at': 2000,
                 'window_minutes': 10080}]})
        data['metrics'][0]['used_percent'] = 99
        self.assertEqual(result['metrics'][0]['used_percent'], 25)

    def test_unexpected_or_missing_keys_and_provider_mismatch_are_rejected(self):
        bad = []
        for field in ('version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'):
            data = snapshot(); del data[field]; bad.append(data)
        for field, value in (('token', 'private'), ('version', True), ('version', 2),
                             ('scope', 'context'), ('provider', 'ollama_cloud'),
                             ('source', 'claude_code'), ('fetched_at', True),
                             ('fetched_at', 1.5), ('fetched_at', -1),
                             ('fetched_at', 2**53), ('metrics', {})):
            data = snapshot(); data[field] = value; bad.append(data)
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                quota.validate_snapshot(data, provider='codex')
        with self.assertRaises(ValueError): quota.validate_snapshot(snapshot(), provider='claude')

    def test_metric_schema_percent_duration_and_window_bounds(self):
        bad = []
        for field in ('window', 'used_percent', 'resets_at'):
            metric = snapshot()['metrics'][0]; del metric[field]; bad.append(metric)
        for field, value in (('secret', 'private'), ('used_percent', True),
                ('used_percent', float('nan')), ('used_percent', float('inf')),
                ('used_percent', -1), ('used_percent', 101), ('used_percent', '1'),
                ('window_minutes', True), ('window_minutes', 0),
                ('window_minutes', 1.5), ('window_minutes', 4294967296),
                ('window_minutes', None), ('resets_at', True), ('resets_at', -1),
                ('window', ''), ('window', 'x' * 49), ('window', 'bad\n'),
                ('window', '\u00e9')):
            metric = snapshot()['metrics'][0]; metric[field] = value; bad.append(metric)
        for metric in bad:
            with self.subTest(metric=metric), self.assertRaises(ValueError):
                quota.validate_snapshot(snapshot(metrics=[metric]))
        valid = {'window': '~' * 48, 'used_percent': None, 'resets_at': None,
                 'window_minutes': 4294967295}
        self.assertEqual(quota.validate_snapshot(snapshot(metrics=[valid]))['metrics'], [valid])

    def test_metric_count_and_duplicate_ids(self):
        metrics = [{'window': str(i), 'used_percent': 0, 'resets_at': None} for i in range(64)]
        self.assertEqual(len(quota.validate_snapshot(snapshot(metrics=metrics))['metrics']), 64)
        for bad in (metrics + [{'window': 'extra', 'used_percent': 1, 'resets_at': None}],
                    [metrics[0], metrics[0]]):
            with self.assertRaises(ValueError): quota.validate_snapshot(snapshot(metrics=bad))

    def test_strict_json_duplicate_nan_utf8_depth_and_16384_byte_bound(self):
        raw = json.dumps(snapshot()).encode()
        padded = raw + b' ' * (16384 - len(raw))
        self.assertEqual(quota.loads_snapshot(padded)['fetched_at'], 1000)
        invalid = (padded + b' ', b'\xff', b'{"version":1,"version":1}',
                   raw.replace(b'25', b'NaN'), b'[' * 2000 + b']' * 2000,
                   raw.replace(b'25', b'1' * 5000))
        for data in invalid:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                quota.loads_snapshot(data)

    def test_iso_reset_uses_existing_epoch_semantics(self):
        data = snapshot(metrics=[{'window': 'weekly', 'used_percent': 100,
                                 'resets_at': '2030-01-01T00:00:00Z'}])
        self.assertEqual(quota.validate_snapshot(data)['metrics'][0]['resets_at'], 1893456000)


class QuotaReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(quota, 'bounded private account snapshot feature is missing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'tasks.sqlite'); self.addCleanup(self.store.close)
        self.cache = UsageCache(); self.now = 1000
        self.source = quota.QuotaSnapshots('linux', self.store, self.cache, clock=lambda: self.now)
        self.folder = self.root / '.tasks.sqlite.account-quota-v1'

    def write(self, data=None, raw=None, provider='codex'):
        self.folder.mkdir(mode=0o700, exist_ok=True)
        path = self.folder / (provider + '.json')
        path.write_bytes(raw if raw is not None else json.dumps(data or snapshot(provider)).encode())
        path.chmod(0o600)
        return path

    def report(self, provider='codex'):
        self.source.refresh()
        return self.cache.report('linux', provider, self.now)

    def test_missing_and_memory_sources_stay_unconfigured_without_creating_files(self):
        self.assertEqual(self.report()['availability'], 'not_configured')
        self.assertFalse(self.folder.exists())
        memory = Store(':memory:'); self.addCleanup(memory.close)
        quota.QuotaSnapshots('memory', memory, self.cache, clock=lambda: self.now).refresh()
        self.assertEqual(self.cache.report('memory', 'claude', self.now)['availability'], 'not_configured')

    def test_only_database_filename_metadata_is_queried_without_store_lock(self):
        self.store.db.set_authorizer(lambda action, a, b, db, context:
            __import__('sqlite3').SQLITE_OK if action == __import__('sqlite3').SQLITE_PRAGMA
            and a == 'database_list' else __import__('sqlite3').SQLITE_DENY)
        class ForbiddenLock:
            def __enter__(self): raise AssertionError('quota reader used task-store lock')
        self.store.lock = ForbiddenLock()
        source = quota.QuotaSnapshots('other', self.store, self.cache, clock=lambda: self.now)
        self.write(); source.refresh()
        self.assertEqual(self.cache.report('other', 'codex', self.now)['metrics'][0]['value'], 75)

    def test_remaining_and_actual_weekly_primary_duration_are_preserved(self):
        self.write()
        report = self.report(); metric = report['metrics'][0]
        self.assertEqual((metric['value'], metric['unit'], metric['window'], metric['window_minutes']),
                         (75, 'percent_remaining', 'codex:primary', 10080))
        self.assertEqual((report['source'], report['fetched_at']), ('Codex app account limits', 1000))
        self.assertEqual((metric['resets_at'], metric['expires_at']), (2000, 1300))

    def test_independent_resets_expire_before_ttl_and_unknown_is_not_full(self):
        self.write(snapshot(metrics=[
            {'window': 'primary', 'used_percent': 0, 'resets_at': 1010},
            {'window': 'secondary', 'used_percent': 100, 'resets_at': 2000},
            {'window': 'unknown', 'used_percent': None, 'resets_at': None}]))
        self.report(); self.now = 1010
        rows = self.report()['metrics']
        self.assertEqual([m['value'] for m in rows], [100, 0, None])
        self.assertEqual([m['availability'] for m in rows], ['stale', 'available', 'unavailable'])
        self.now = 1300
        self.assertEqual([m['availability'] for m in self.report()['metrics']], ['stale', 'stale', 'unavailable'])

    def test_refresh_is_at_most_once_per_15_seconds(self):
        self.write(); self.report()
        self.write(snapshot(fetched_at=1010, metrics=[]))
        self.now = 1014
        self.assertEqual(self.report()['metrics'][0]['value'], 75)
        self.now = 1015
        self.assertEqual((self.report()['availability'], self.report()['fetched_at']), ('unavailable', 1010))

    def test_identical_file_reread_does_not_refresh_observation_time(self):
        self.write(); self.report(); self.now = 1310
        path = self.folder / 'codex.json'; os.utime(path, None)
        report = self.report()
        self.assertEqual((report['fetched_at'], report['availability']), (1000, 'stale'))

    def test_older_and_same_time_conflicting_payloads_preserve_newest_and_fail(self):
        self.write(); self.report()
        for stamp in (999, 1000):
            self.now += 15
            self.write(snapshot(fetched_at=stamp, metrics=[]))
            report = self.report()
            self.assertEqual((report['fetched_at'], report['metrics'][0]['value']), (1000, 75))
            self.assertEqual((report['availability'], report['metrics'][0]['availability']), ('failed', 'stale'))

    def test_future_data_is_stale_until_observed_and_beyond_30s_is_rejected(self):
        self.write(snapshot(fetched_at=1030)); report = self.report()
        self.assertEqual(report['metrics'][0]['availability'], 'stale')
        self.now = 1030
        self.assertEqual(self.report()['metrics'][0]['availability'], 'available')
        self.write(snapshot(fetched_at=1076, metrics=[])); self.now = 1045
        report = self.report()
        self.assertEqual((report['fetched_at'], report['metrics'][0]['value']), (1030, 75))
        self.assertEqual(report['availability'], 'failed')

    def test_initial_bad_or_wrong_provider_is_failed_without_any_value(self):
        for raw in (b'synthetic-private-source-error', json.dumps(snapshot('claude')).encode(),
                    b' ' * 16385):
            self.write(raw=raw); self.now += 15
            report = self.report()
            self.assertEqual((report['availability'], report['metrics']), ('failed', []))
            self.assertNotIn('synthetic-private', json.dumps(report))

    def test_deletion_parse_and_permission_failure_preserve_success_and_recovery(self):
        path = self.write(); self.report(); path.unlink(); self.now += 15
        self.assertEqual(self.report()['metrics'][0]['availability'], 'stale')
        self.write(raw=b'bad'); self.now += 15
        self.assertEqual(self.report()['fetched_at'], 1000)
        self.write(); self.now += 15
        with patch.object(quota.os, 'open', side_effect=PermissionError('synthetic-private')):
            self.assertEqual(self.report()['availability'], 'failed')
        self.now += 15
        self.assertEqual(self.report()['availability'], 'available')

    def test_64_windows_use_existing_omitted_metrics_and_two_provider_scope(self):
        self.write(snapshot(metrics=[{'window': f'bucket:{i}', 'used_percent': i,
                                     'resets_at': None} for i in range(64)]))
        self.write(snapshot('claude', metrics=[]), provider='claude')
        report = self.report()
        self.assertEqual((len(report['metrics']), report['omitted_metrics'], report['availability']), (4, 60, 'partial'))
        self.assertEqual(self.report('claude')['availability'], 'unavailable')
        self.assertEqual(self.cache.report('other', 'codex', self.now)['availability'], 'not_configured')
        self.assertEqual(self.cache.report('linux', 'ollama_cloud', self.now)['availability'], 'not_configured')

    def test_directory_and_hardlinked_file_are_rejected_before_open(self):
        path = self.write(); path.unlink(); path.mkdir()
        self.assertEqual(self.report()['availability'], 'failed')
        path.rmdir(); outside = self.root / 'synthetic.json'; outside.write_text(json.dumps(snapshot()))
        outside.chmod(0o600); os.link(outside, path); self.now += 15
        self.assertEqual(self.report()['availability'], 'failed')

    @unittest.skipUnless(os.name == 'posix', 'POSIX private mode/owner checks require Linux gate')
    def test_public_modes_wrong_owner_and_fifo_are_rejected_without_open(self):
        path = self.write()
        for mode in (0o644, 0o666):
            path.chmod(mode); self.now += 15
            self.assertEqual(self.report()['availability'], 'failed')
        path.chmod(0o600); self.folder.chmod(0o755); self.now += 15
        self.assertEqual(self.report()['availability'], 'failed')
        self.folder.chmod(0o700); path.chmod(0o600); self.now += 15
        with patch.object(quota.os, 'geteuid', return_value=path.stat().st_uid + 1):
            self.assertEqual(self.report()['availability'], 'failed')
        self.folder.chmod(0o700); path.unlink(); os.mkfifo(path, 0o600); self.now += 15
        with patch.object(quota.PrivateDirectory, 'open', side_effect=AssertionError('FIFO source file opened')):
            self.assertEqual(self.report()['availability'], 'failed')

    @unittest.skipUnless(os.name == 'posix', 'symlink and dirfd race checks require Linux gate')
    def test_symlink_file_and_ancestor_are_rejected(self):
        path = self.write(); raw = path.read_bytes(); path.unlink()
        other = self.root / 'private.json'; other.write_bytes(raw); other.chmod(0o600)
        path.symlink_to(other); self.assertEqual(self.report()['availability'], 'failed')
        path.unlink(); self.folder.rmdir()
        other_dir = self.root / 'private'; other_dir.mkdir(mode=0o700)
        (other_dir / 'codex.json').write_bytes(raw); (other_dir / 'codex.json').chmod(0o600)
        self.folder.symlink_to(other_dir, target_is_directory=True); self.now += 15
        self.assertEqual(self.report()['availability'], 'failed')

    def test_file_replacement_between_stat_and_open_is_rejected_before_read(self):
        path = self.write(); original = os.open; swapped = False
        def replace_before_open(name, flags, *args, **kwargs):
            nonlocal swapped
            if Path(name).name == 'codex.json' and not swapped:
                swapped = True; replacement = self.folder / 'replacement'
                replacement.write_bytes(b'synthetic-private'); replacement.chmod(0o600)
                os.replace(replacement, path)
            return original(name, flags, *args, **kwargs)
        with patch.object(quota.os, 'open', side_effect=replace_before_open):
            report = self.report()
        self.assertTrue(swapped)
        self.assertEqual((report['availability'], report['metrics']), ('failed', []))

    def test_file_mutation_during_read_is_rejected(self):
        path = self.write(); original = os.read; changed = False
        def mutate(fd, count):
            nonlocal changed
            raw = original(fd, count)
            if not changed:
                changed = True
                with path.open('ab') as stream: stream.write(b' ')
            return raw
        with patch.object(quota.os, 'read', side_effect=mutate):
            self.assertEqual(self.report()['availability'], 'failed')
