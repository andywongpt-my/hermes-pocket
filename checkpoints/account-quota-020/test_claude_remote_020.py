"""Synthetic receiver tests. No account, service, SSH or network operations.

Native qualification can point CLAUDE_RECEIVER_TEST_FIXTURES at a directory
containing pocket/{usage,quota_snapshots,quota_export}.py and the two frozen
Codex files quota_remote_020.py and quotaops-acceptance.json.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
try:
    import claude_remote_020 as remote
except ModuleNotFoundError:
    remote = None

NOW = 1_800_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def snapshot(fetched=NOW, used=37):
    return {'version': 1, 'scope': 'account', 'provider': 'claude',
            'source': 'claude_code', 'fetched_at': fetched,
            'metrics': [{'window': '5h', 'used_percent': used,
                         'resets_at': NOW + 1000, 'window_minutes': 300},
                        {'window': '7d', 'used_percent': None,
                         'resets_at': None, 'window_minutes': 10080}]}


def usage_rows():
    rows = []
    for provider in ('codex', 'claude', 'ollama_cloud'):
        rows.append({'provider': provider, 'availability': 'not_configured',
                     'value': None, 'metrics': [], 'local_usage': {
                         'scope': 'pocket_runs', 'period': 'all_recorded',
                         'availability': 'available'}})
    for row, window, minutes, source in (
            (rows[0], 'codex:primary', 300, 'Codex app account limits'),
            (rows[1], '5h', 300, 'Claude Code account limits')):
        row['availability'] = 'available'
        row['metrics'] = [{'provider': row['provider'], 'category': 'subscription',
            'window': window, 'window_minutes': minutes, 'source': source,
            'unit': 'percent_remaining', 'value': 63, 'fetched_at': NOW,
            'resets_at': NOW + 1000, 'availability': 'available'}]
    return rows


class DeliveryExists(unittest.TestCase):
    def test_receiver_is_implemented(self):
        self.assertIsNotNone(remote, 'The additive receiver has not been implemented')


@unittest.skipIf(remote is None, 'receiver not implemented yet')
class ValidationTests(unittest.TestCase):
    def test_exact_snapshot_returns_independent_numeric_copy(self):
        value = snapshot()
        result = remote.validate_snapshot(canonical(value), NOW)
        self.assertEqual(value, result)
        result['metrics'][0]['used_percent'] = 99
        self.assertEqual(37, value['metrics'][0]['used_percent'])

    def test_freshness_boundary(self):
        self.assertEqual(NOW - 299, remote.validate_snapshot(canonical(snapshot(NOW - 299)), NOW)['fetched_at'])
        for fetched in (NOW - 300, NOW + 1, True, 1.5, -1):
            with self.subTest(fetched=fetched), self.assertRaises(remote.ReceiverError):
                remote.validate_snapshot(canonical(snapshot(fetched)), NOW)

    def test_rejects_unknown_envelope_and_wrong_owner(self):
        for key, value in (('raw_identity', 'PRIVATE-VENDOR'), ('version', True),
                           ('provider', 'codex'), ('source', 'other'), ('scope', 'session')):
            candidate = snapshot(); candidate[key] = value
            with self.subTest(key=key), self.assertRaises(remote.ReceiverError):
                remote.validate_snapshot(canonical(candidate), NOW)

    def test_rejects_bad_metrics_and_requires_real_quota(self):
        for mutate in (
            lambda v: v.update(metrics=[]),
            lambda v: v['metrics'].append(copy.deepcopy(v['metrics'][0])),
            lambda v: v['metrics'][0].update(window='five_hour'),
            lambda v: v['metrics'][0].update(window_minutes=True),
            lambda v: v['metrics'][0].update(window_minutes=10080),
            lambda v: v['metrics'][0].update(used_percent=True),
            lambda v: v['metrics'][0].update(used_percent=100.1),
            lambda v: v['metrics'][0].update(used_percent=-1),
            lambda v: v['metrics'][0].update(used_percent=None),
            lambda v: v['metrics'][0].update(resets_at=NOW),
            lambda v: v['metrics'][0].update(resets_at=True),
            lambda v: v['metrics'][0].update(extra='PRIVATE-VENDOR')):
            value = snapshot(); mutate(value)
            with self.subTest(value=value), self.assertRaises(remote.ReceiverError):
                remote.validate_snapshot(canonical(value), NOW)

    def test_rejects_duplicate_nonfinite_oversize_and_wrong_input(self):
        for raw in (b'{"version":1,"version":1}', b'{"n":NaN}',
                    b'{"n":1e9999}', b'x' * 16385, snapshot(), b'\xff'):
            with self.subTest(raw_type=type(raw).__name__), self.assertRaises(remote.ReceiverError):
                remote.validate_snapshot(raw, NOW)

    def test_combined_profile_accepts_fresh_claude_and_codex(self):
        self.assertTrue(remote.combined_usage_ready(usage_rows(), NOW, require_claude=True))
        rows = usage_rows(); rows[1].update(availability='not_configured', metrics=[])
        self.assertTrue(remote.combined_usage_ready(rows, NOW))
        self.assertFalse(remote.combined_usage_ready(rows, NOW, require_claude=True))

    def test_combined_profile_preserves_codex_and_other_provider_semantics(self):
        for mutate in (
            lambda r: r[0]['metrics'][0].update(fetched_at=NOW - 300),
            lambda r: r[0]['metrics'][0].update(value=True),
            lambda r: r[0]['metrics'][0].update(window='5h'),
            lambda r: r[1]['metrics'][0].update(window='five_hour'),
            lambda r: r[1]['metrics'][0].update(window_minutes=10080),
            lambda r: r[1]['metrics'][0].update(fetched_at=NOW + 1),
            lambda r: r[1]['metrics'][0].update(resets_at=NOW),
            lambda r: r[2].update(availability='available'),
            lambda r: r[2].update(provider='unknown'),
            lambda r: r[2].update(metrics=[{'availability': 'stale', 'fetched_at': NOW + 1}]),
            lambda r: r[1].update(value=5)):
            rows = usage_rows(); mutate(rows)
            with self.subTest(rows=rows):
                self.assertFalse(remote.combined_usage_ready(rows, NOW, require_claude=True))

    def test_combined_profile_requires_local_history_and_unique_providers(self):
        rows = usage_rows()
        for row in rows: row['local_usage']['availability'] = 'unavailable'
        self.assertFalse(remote.combined_usage_ready(rows, NOW))
        self.assertFalse(remote.combined_usage_ready([usage_rows()[0]] * 3, NOW))


class FixtureOps:
    """Read-only service/acceptance boundary; filesystem checks remain real."""
    def __init__(self, paths, manifest):
        self.paths = paths; self.manifest = manifest; self.calls = []; self.live = False

    def validate_bundle(self, stage):
        if stage != self.paths.codex_stage: raise AssertionError('wrong accepted stage')
        self.calls.append('bundle')
        return {'manifest': self.manifest, 'acceptance_sha256': remote.CODEX_ACCEPTANCE_SHA256}

    def validate_remote_stage(self, bundle):
        self.calls.append('stage')

    def check_hashes(self, manifest, deployed):
        if deployed is not True: raise AssertionError('must retain deployed backend')
        for name, expected in manifest['candidate_hashes'].items():
            if hashlib.sha256((self.paths.release / name).read_bytes()).hexdigest() != expected:
                raise ValueError('PRIVATE-VENDOR backend path')
        self.calls.append('hashes')

    def gateway_invariants(self):
        self.calls.append('gateway')

    def read_channel(self, bundle, *, allow_pending):
        value = json.loads((self.paths.codex_stage / 'codex-channel.json').read_bytes())
        if allow_pending or value['state'] != 'active': raise ValueError('channel')
        return value

    def supervisor_identity(self, phase, accepted):
        return {'operator_pid': 123456, 'receipts': {'result':
            json.loads((self.paths.codex_stage / (phase + '-result.json')).read_bytes())}}

    def operator_process_exists(self, pid):
        return self.live

    def operation_lock(self):
        raise AssertionError('Claude must never acquire the old Codex operation lock')


@unittest.skipIf(remote is None, 'receiver not implemented yet')
class FilesystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        explicit = os.environ.get('CLAUDE_RECEIVER_TEST_FIXTURES')
        cls.source = Path(explicit) if explicit else HERE.parent
        cls.pocket = cls.source / 'pocket' if explicit else cls.source / 'frozen-R3' / 'pocket'
        cls.old_stage = cls.source if explicit else cls.source / 'codex-only-activation-020-r3'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='receiver-fixture-', dir=HERE)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.paths = remote.PathSet(stage=root / 'stage', codex_stage=root / 'codex-stage',
            release=root / 'release', numeric=root / '.numeric.account-quota-v1',
            claude_staging=root / '.claude.account-quota-v1', claude_lock=root / '.claude-lock')
        for path in vars(self.paths).values():
            path.mkdir(mode=0o700)
        (self.paths.release / 'pocket').mkdir(mode=0o700)
        hashes = {}
        for name in ('usage.py', 'quota_snapshots.py', 'quota_export.py'):
            raw = (self.pocket / name).read_bytes()
            self.write(self.paths.release / 'pocket' / name, raw)
            hashes['pocket/' + name] = hashlib.sha256(raw).hexdigest()
        for name in ('quota_remote_020.py', 'quotaops-acceptance.json'):
            self.write(self.paths.codex_stage / name, (self.old_stage / name).read_bytes())
        self.write(self.paths.stage / 'claude_remote_020.py', Path(remote.__file__).read_bytes())
        self.config = {'version': 1, 'profile': remote.PROFILE, 'enabled': True,
            'codex_acceptance_sha256': remote.CODEX_ACCEPTANCE_SHA256,
            'codex_ops_sha256': remote.CODEX_OPS_SHA256,
            'source_pins': {'claude_remote_020.py': hashlib.sha256(
                (self.paths.stage / 'claude_remote_020.py').read_bytes()).hexdigest()},
            'aw_source_pins': {'claude_passive_aw_020.py': 'a' * 64}}
        self.write(self.paths.stage / 'combined-profile.json', canonical(self.config))
        self.profile_digest = hashlib.sha256(canonical(self.config)).hexdigest()
        self.write(self.paths.codex_stage / 'codex-channel.json', canonical({
            'version': 1, 'profile': 'codex_aw_numeric_v1', 'enabled': True,
            'state': 'active', 'acceptance_sha256': remote.CODEX_ACCEPTANCE_SHA256,
            'not_before': NOW - 1000, 'expires_at': NOW - 700, 'previous_revision': None}))
        for phase in ('deployment', 'activation'):
            self.write(self.paths.codex_stage / (phase + '-result.json'), canonical({
                'ok': True, 'acceptance_sha256': remote.CODEX_ACCEPTANCE_SHA256,
                'backend_deployed': True, 'quota_activated': phase == 'activation'}))
        self.ops = FixtureOps(self.paths, {'candidate_hashes': hashes})
        self.backend = remote._load_backend(self.paths, self.ops.manifest)
        self.codex = b'{"codex":"synthetic immutable sentinel"}\n'
        self.write(self.paths.numeric / 'codex.json', self.codex)
        self.codex_before = (self.paths.numeric / 'codex.json').stat()

    @staticmethod
    def write(path, raw):
        path.write_bytes(raw)
        path.chmod(0o600)

    def call(self, value=None, **kwargs):
        kwargs.setdefault('profile_sha256', self.profile_digest)
        return remote.receive(canonical(snapshot() if value is None else value), self.config,
            now=NOW, paths=self.paths, ops=self.ops, backend=self.backend, **kwargs)

    def assert_codex_unchanged(self):
        path = self.paths.numeric / 'codex.json'
        self.assertEqual(self.codex, path.read_bytes())
        after = path.stat()
        self.assertEqual((self.codex_before.st_ino, self.codex_before.st_mtime_ns),
                         (after.st_ino, after.st_mtime_ns))

    def test_atomic_publish_returns_only_exact_ack_and_preserves_codex(self):
        seen = []
        def checkpoint(event):
            seen.append((event, set(p.name for p in self.paths.numeric.iterdir())))
        result = self.call(checkpoint=checkpoint)
        self.assertEqual({'ok': True, 'profile': remote.PROFILE, 'provider': 'claude',
            'fetched_at': NOW, 'snapshot_sha256': hashlib.sha256(canonical(snapshot())).hexdigest()}, result)
        self.assertEqual(snapshot(), json.loads((self.paths.numeric / 'claude.json').read_bytes()))
        self.assertTrue(all(names <= {'codex.json', 'claude.json'} for _, names in seen))
        self.assertEqual([], list(self.paths.claude_staging.iterdir()))
        self.assert_codex_unchanged()
        self.assertEqual(3, self.ops.calls.count('gateway'))

    def test_codex_numeric_publisher_lock_does_not_block_claude(self):
        with self.backend.PrivateDirectory(self.paths.numeric) as directory:
            with self.backend._publication_lock(directory):
                self.assertTrue(self.call()['ok'])
        self.assert_codex_unchanged()

    def test_existing_claude_needs_strictly_new_observation(self):
        self.call()
        for fetched in (NOW, NOW - 1):
            with self.subTest(fetched=fetched), self.assertRaises(remote.ReceiverError):
                self.call(snapshot(fetched))
        self.assertTrue(self.call(snapshot(NOW + 1), clock=lambda: NOW + 1)['ok'])
        self.assert_codex_unchanged()

    def test_prior_future_snapshot_is_not_recovered_or_overwritten(self):
        raw = canonical(snapshot(NOW + 10))
        self.write(self.paths.numeric / 'claude.json', raw)
        with self.assertRaises(remote.ReceiverError): self.call()
        self.assertEqual(raw, (self.paths.numeric / 'claude.json').read_bytes())

    def test_same_provider_concurrency_fails_closed(self):
        results = []
        def checkpoint(event):
            if event != 'staged': return
            def concurrent():
                try: self.call()
                except remote.ReceiverError as error: results.append(str(error))
            worker = threading.Thread(target=concurrent)
            worker.start(); worker.join(10)
            self.assertFalse(worker.is_alive())
        self.call(checkpoint=checkpoint)
        self.assertEqual(['receiver_rejected'], results)
        self.assert_codex_unchanged()

    def test_source_ops_acceptance_and_profile_drift_fail_before_write(self):
        targets = (self.paths.stage / 'claude_remote_020.py',
            self.paths.codex_stage / 'quota_remote_020.py',
            self.paths.codex_stage / 'quotaops-acceptance.json',
            self.paths.stage / 'combined-profile.json')
        for target in targets:
            original = target.read_bytes()
            self.write(target, original + b'\n')
            try:
                with self.subTest(target=target.name), self.assertRaises(remote.ReceiverError): self.call()
                self.assertFalse((self.paths.numeric / 'claude.json').exists())
            finally: self.write(target, original)
        self.assert_codex_unchanged()

    def test_profile_requires_root_exact_byte_hash(self):
        with self.assertRaises(remote.ReceiverError): self.call(profile_sha256='0' * 64)
        digest = hashlib.sha256((self.paths.stage / 'combined-profile.json').read_bytes()).hexdigest()
        self.assertTrue(self.call(profile_sha256=digest)['ok'])

    def test_rollback_or_live_operator_blocks_publication(self):
        marker = self.paths.codex_stage / 'rollback-dispatched.json'
        self.write(marker, b'{}')
        with self.assertRaises(remote.ReceiverError): self.call()
        marker.unlink(); self.ops.live = True
        with self.assertRaises(remote.ReceiverError): self.call()
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_channel_pending_or_failed_activation_blocks_publication(self):
        for name, mutate in (('codex-channel.json', lambda v: v.update(state='pending')),
                ('activation-result.json', lambda v: v.update(ok=False))):
            path = self.paths.codex_stage / name; raw = path.read_bytes()
            value = json.loads(raw); mutate(value); self.write(path, canonical(value))
            try:
                with self.subTest(name=name), self.assertRaises(remote.ReceiverError): self.call()
            finally: self.write(path, raw)

    def test_final_destination_revision_guard_rejects_competing_write(self):
        competitor = canonical(snapshot(NOW - 1, 88))
        def checkpoint(event):
            if event == 'before_replace': self.write(self.paths.numeric / 'claude.json', competitor)
        with self.assertRaises(remote.ReceiverError): self.call(checkpoint=checkpoint)
        self.assertEqual(competitor, (self.paths.numeric / 'claude.json').read_bytes())
        self.assert_codex_unchanged()

    def test_policy_and_config_rechecked_before_rename(self):
        def checkpoint(event):
            if event == 'before_replace':
                self.write(self.paths.codex_stage / 'rollback-dispatched.json', b'{}')
        with self.assertRaises(remote.ReceiverError): self.call(checkpoint=checkpoint)
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_snapshot_that_expires_during_gates_is_not_published(self):
        moments = iter((NOW, NOW + 300))
        with self.assertRaises(remote.ReceiverError): self.call(clock=lambda: next(moments))
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_pending_recovery_blocks_even_if_receipt_claims_ok(self):
        path = self.paths.codex_stage / 'activation-result.json'
        value = json.loads(path.read_bytes()); value['recovery_pending'] = True
        self.write(path, canonical(value))
        with self.assertRaises(remote.ReceiverError): self.call()
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_policy_change_after_rename_is_uncertain_and_never_restored(self):
        def checkpoint(event):
            if event == 'replaced':
                self.write(self.paths.codex_stage / 'rollback-dispatched.json', b'{}')
        with self.assertRaises(remote.PublicationUncertain): self.call(checkpoint=checkpoint)
        self.assertEqual(snapshot(), json.loads((self.paths.numeric / 'claude.json').read_bytes()))
        self.assert_codex_unchanged()

    def test_config_change_after_staging_rejected_before_publication(self):
        def checkpoint(event):
            if event == 'before_replace':
                path = self.paths.stage / 'combined-profile.json'
                self.write(path, path.read_bytes() + b'\n')
        with self.assertRaises(remote.ReceiverError): self.call(checkpoint=checkpoint)
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_no_numeric_temp_entry_even_during_real_publisher_temp_write(self):
        original = self.backend.PrivateDirectory.open
        seen = []
        def checked(directory, name, *args, **kwargs):
            if name.startswith('.quota-'):
                seen.append(directory.path)
                self.assertEqual(self.paths.claude_staging, directory.path)
                self.assertEqual({'codex.json'}, set(p.name for p in self.paths.numeric.iterdir()))
            return original(directory, name, *args, **kwargs)
        with patch.object(self.backend.PrivateDirectory, 'open', checked): self.call()
        self.assertEqual([self.paths.claude_staging], seen)

    def test_final_replace_uses_anchored_cross_directory_fds(self):
        original = os.replace; calls = []
        def replace(src, dst, *args, **kwargs):
            calls.append((src, dst, kwargs))
            return original(src, dst, *args, **kwargs)
        with patch.object(os, 'replace', replace): self.call()
        final = calls[-1]
        if os.name == 'posix':
            self.assertEqual(('claude.json', 'claude.json'), final[:2])
            self.assertIsInstance(final[2]['src_dir_fd'], int)
            self.assertIsInstance(final[2]['dst_dir_fd'], int)
            self.assertNotEqual(final[2]['src_dir_fd'], final[2]['dst_dir_fd'])
        self.assertEqual(2, len(calls))

    def test_final_replace_uncertain_does_not_retry_or_restore(self):
        original = os.replace; final_calls = []
        def replace(src, dst, *args, **kwargs):
            is_final = str(src) == 'claude.json' or Path(src) == self.paths.claude_staging / 'claude.json'
            result = original(src, dst, *args, **kwargs)
            if is_final:
                final_calls.append(1)
                raise OSError('PRIVATE-VENDOR filesystem path')
            return result
        with patch.object(os, 'replace', replace):
            with self.assertRaises(remote.PublicationUncertain): self.call()
        self.assertEqual([1], final_calls)
        self.assertEqual(snapshot(), json.loads((self.paths.numeric / 'claude.json').read_bytes()))
        with self.assertRaises(remote.ReceiverError): self.call()
        self.assert_codex_unchanged()

    def test_unknown_numeric_or_staging_entries_fail_closed(self):
        for directory in (self.paths.numeric, self.paths.claude_staging, self.paths.claude_lock):
            path = directory / 'unexpected'; self.write(path, b'private')
            try:
                with self.subTest(directory=directory.name), self.assertRaises(remote.ReceiverError): self.call()
            finally: path.unlink()

    def test_backend_source_drift_rejected(self):
        self.write(self.paths.release / 'pocket' / 'quota_export.py', b'bad source')
        with self.assertRaises(remote.ReceiverError): self.call()
        self.assertFalse((self.paths.numeric / 'claude.json').exists())

    def test_ops_loader_hashes_bytes_before_compile_and_no_pyc(self):
        loaded = remote._load_ops(self.paths, self.config)
        self.assertTrue(callable(loaded.validate_bundle))
        self.assertFalse(list(self.paths.codex_stage.rglob('*.pyc')))
        self.write(self.paths.codex_stage / 'quota_remote_020.py', b'raise RuntimeError("PRIVATE-VENDOR")')
        with self.assertRaises(remote.ReceiverError): remote._load_ops(self.paths, self.config)

    def test_config_rejects_identity_credentials_traversal_and_wrong_pins(self):
        for mutate in (lambda c: c.update(token='PRIVATE-VENDOR'),
                       lambda c: c.update(enabled=False),
                       lambda c: c.update(version=True),
                       lambda c: c['source_pins'].update({'../outside.py': 'a' * 64}),
                       lambda c: c['source_pins'].update({'combined-profile.json': 'a' * 64}),
                       lambda c: c.update(codex_ops_sha256='0' * 64)):
            candidate = copy.deepcopy(self.config); mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(remote.ReceiverError):
                remote.validate_config(candidate)

    def test_cli_exact_ack_and_redacted_failures(self):
        def run(args, raw):
            output = io.StringIO()
            with patch.object(remote.sys, 'platform', 'linux'), \
                 patch.object(remote, 'STAGE', self.paths.stage), \
                 patch.object(remote, 'PATHS', self.paths), \
                 patch.object(remote, '__file__', str(self.paths.stage / 'claude_remote_020.py')), \
                 patch.object(remote, '_load_ops', return_value=self.ops), \
                 patch.object(remote.time, 'time', return_value=NOW), \
                 patch.object(remote.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(raw))), \
                 patch.object(remote.sys, 'stdout', output):
                code = remote.main(args)
            return code, output.getvalue()
        args = ['receive', '--profile-sha256', self.profile_digest]
        code, output = run(args, canonical(snapshot()))
        self.assertEqual(0, code)
        self.assertEqual({'ok', 'profile', 'provider', 'fetched_at', 'snapshot_sha256'}, set(json.loads(output)))
        code, output = run(['receive', '--profile-sha256', '0' * 64], b'PRIVATE-VENDOR')
        self.assertEqual(1, code)
        self.assertEqual({'ok': False, 'category': 'receiver_rejected'}, json.loads(output))
        self.assertNotIn('PRIVATE-VENDOR', output)
        self.assertNotIn(str(self.paths.stage), output)

    def test_cli_uncertainty_has_fixed_safe_category(self):
        output = io.StringIO()
        with patch.object(remote.sys, 'platform', 'linux'), \
             patch.object(remote, 'STAGE', self.paths.stage), \
             patch.object(remote, '__file__', str(self.paths.stage / 'claude_remote_020.py')), \
             patch.object(remote.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(canonical(snapshot())))), \
             patch.object(remote.sys, 'stdout', output), \
             patch.object(remote, 'receive', side_effect=remote.PublicationUncertain()):
            code = remote.main(['receive', '--profile-sha256', self.profile_digest])
        self.assertEqual(2, code)
        self.assertEqual({'ok': False, 'category': 'publication_uncertain', 'outcome_uncertain': True},
                         json.loads(output.getvalue()))

    @unittest.skipUnless(os.name == 'posix', 'POSIX ownership and dirfd guard')
    def test_posix_private_modes_symlink_and_hardlink_rejected(self):
        self.paths.claude_staging.chmod(0o755)
        with self.assertRaises(remote.ReceiverError): self.call()
        self.paths.claude_staging.chmod(0o700)
        outside = self.paths.stage / 'outside'; self.write(outside, canonical(snapshot(NOW - 1)))
        destination = self.paths.numeric / 'claude.json'
        destination.symlink_to(outside)
        with self.assertRaises(remote.ReceiverError): self.call()
        destination.unlink(); os.link(outside, destination)
        with self.assertRaises(remote.ReceiverError): self.call()

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory-chain guard')
    def test_posix_directory_swap_cannot_redirect_publication(self):
        old = self.paths.claude_staging.with_name('staging-moved')
        def checkpoint(event):
            if event == 'before_replace':
                self.paths.claude_staging.rename(old)
                self.paths.claude_staging.mkdir(mode=0o700)
        with self.assertRaises(remote.ReceiverError): self.call(checkpoint=checkpoint)
        self.assertFalse((self.paths.numeric / 'claude.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
