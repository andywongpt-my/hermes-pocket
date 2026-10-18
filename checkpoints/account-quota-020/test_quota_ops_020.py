"""Offline operations contracts. External systemd/HTTP/native boundaries are fake.

Owned temporary files contain synthetic numeric metadata only. No test invokes
SSH, a native account client, an application service, or the 018 reference.
"""
import copy
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import json
import io
import os
from pathlib import Path
import signal
import stat
import sqlite3
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import quota_remote_020 as ops
import quota_refresh_020 as runner
import operate_quota_020 as client

ROOT = Path(__file__).resolve().parent
TEMP_ROOT = tempfile.gettempdir() if os.name == 'nt' else str(ROOT) if ROOT == ops.STAGE else None
BACKEND = ROOT / 'backend' if (ROOT / 'backend').is_dir() else ops.PACKAGE
sys.path.insert(0, str(BACKEND))
from pocket.quota_export import publish_snapshot
from pocket.quota_snapshots import validate_snapshot


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    path.chmod(0o600)


def snapshot(at=1000, used=25):
    return {'version': 1, 'scope': 'account', 'provider': 'claude',
            'source': 'claude_code', 'fetched_at': at,
            'metrics': [{'window': 'five_hour', 'used_percent': used,
                         'resets_at': None, 'window_minutes': 300}]}


def usage(at=1000):
    result = []
    for provider in ('codex', 'claude', 'ollama_cloud'):
        result.append({'provider': provider, 'availability': 'not_configured',
                       'value': None, 'fetched_at': None, 'metrics': [],
                       'local_usage': {'scope': 'pocket_runs', 'period': 'all_recorded',
                                       'availability': 'available' if provider == 'claude' else 'unavailable'}})
    return result


def fresh_usage(at=1000, window='five_hour'):
    result = usage(at)
    metric = {'provider': 'claude', 'category': 'subscription', 'window': window,
              'value': 75, 'unit': 'percent_remaining', 'fetched_at': at,
              'resets_at': None, 'source': 'Claude Code account limits',
              'availability': 'available', 'window_minutes': 300 if window == 'five_hour' else 10080}
    result[1].update(metric)
    result[1]['metrics'] = [copy.deepcopy(metric)]
    return result


class ReadinessTests(unittest.TestCase):
    def test_unknown_accounts_are_valid_before_activation(self):
        self.assertTrue(ops.usage_ready(usage(), quota_required=False, observed_at=1000))
        self.assertFalse(ops.usage_ready(usage(), quota_required=True, observed_at=1000))

    def test_either_claude_window_independently_meets_freshness(self):
        for window in ('five_hour', 'seven_day'):
            with self.subTest(window=window):
                self.assertTrue(ops.usage_ready(fresh_usage(window=window), quota_required=True, observed_at=1000))

    def test_freshness_rejects_wrong_semantics_and_times(self):
        for change in ({'value': 101}, {'value': True}, {'window_minutes': 301},
                       {'source': 'local token totals'}, {'fetched_at': 1001},
                       {'fetched_at': 700}, {'availability': 'stale'},
                       {'unit': 'tokens'}, {'window': 'context'}):
            with self.subTest(change=change):
                rows = fresh_usage()
                rows[1]['metrics'][0].update(change)
                self.assertFalse(ops.usage_ready(rows, quota_required=True, observed_at=1000))

    def test_other_account_sources_stay_unknown_even_when_local_usage_exists(self):
        rows = fresh_usage()
        rows[0].update(availability='available', value=300, fetched_at=1000)
        self.assertFalse(ops.usage_ready(rows, quota_required=True, observed_at=1000))

    def test_three_distinct_local_history_scopes_are_required(self):
        for change in ('missing', 'account_scope', 'duplicate'):
            with self.subTest(change=change):
                rows = fresh_usage()
                if change == 'missing': rows[0].pop('local_usage')
                elif change == 'account_scope': rows[0]['local_usage']['scope'] = 'account'
                else: rows[0]['provider'] = 'claude'
                self.assertFalse(ops.usage_ready(rows, quota_required=True, observed_at=1000))

    def test_probe_only_uses_read_allowlist_and_preserves_voice_reply_catalog(self):
        calls = []
        stt = {'resident': True, 'ready': True, 'generation': 1,
               'active': 0, 'pending': 0, 'worker_pid': 17}
        def request(method, route, **kwargs):
            calls.append((method, route, kwargs.get('auth', True)))
            if not kwargs.get('auth', True): return 401, {}, {}
            if route.endswith('/status'):
                return 200, {'backend': 'available', 'voice': ops.CAPABILITIES,
                             'reply_delivery': ops.REPLY_DELIVERY, 'voice_runtime': {'stt': stt}}, {}
            if route.endswith('/usage'): return 200, fresh_usage(), {}
            if route.endswith('/models'): return 200, ops.CATALOG, {}
            return 200, {'ok': True}, {}
        with patch.object(ops, 'request', request), patch.object(ops.time, 'time', return_value=1000):
            value = ops.probe(True, quota_required=True)
            self.assertTrue(value['ok'])
            stt['ready'] = False
            self.assertFalse(ops.probe(True, quota_required=True)['ok'])
        self.assertEqual({m for m, _, _ in calls}, {'GET'})
        self.assertEqual({r for _, r, _ in calls}, set(ops.GET_ROUTES))
        self.assertEqual(value['model_requests'], 0)
        self.assertEqual(value['audio_requests'], 0)
        self.assertEqual(value['microphone_requests'], 0)

    def test_request_rejects_mutation_and_task_routes_before_connection(self):
        with patch.object(ops.http.client, 'HTTPSConnection') as connection:
            for method, route in [('POST', '/health'), ('GET', '/targets/linux/tasks/x/speech')]:
                with self.assertRaises(ops.OpsError): ops.request(method, route)
            connection.assert_not_called()


class RefreshTests(unittest.TestCase):
    def test_failed_fetch_preserves_last_good_file_and_timestamp(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            directory = Path(d) / '.fixture.account-quota-v1'
            directory.mkdir(mode=0o700)
            output = directory / 'claude.json'
            publish_snapshot(snapshot(), output, clock=lambda: 1000)
            before = output.read_bytes()
            class Failed(Exception):
                code = 'refresh_failed'
            collector = SimpleNamespace(probe=MagicMock(side_effect=Failed('secret native text')))
            with patch.object(runner, 'OUTPUT', output):
                receipt = runner.refresh_once(collector, validate_snapshot, publish_snapshot, clock=lambda: 1001)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(receipt['category'], 'refresh_failed')
            self.assertNotIn('secret', json.dumps(receipt))

    def test_publication_retains_source_fetch_time_and_allows_unchanged_fresh_value(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            directory = Path(d) / '.fixture.account-quota-v1'
            directory.mkdir(mode=0o700)
            output = directory / 'claude.json'
            publish_snapshot(snapshot(), output, clock=lambda: 1000)
            collector = SimpleNamespace(probe=lambda: SimpleNamespace(snapshot=snapshot(1002), evidence={}))
            with patch.object(runner, 'OUTPUT', output):
                receipt = runner.refresh_once(collector, validate_snapshot,
                    lambda v, p: publish_snapshot(v, p, clock=lambda: 1003), clock=iter([1001, 1003]).__next__)
            self.assertTrue(receipt['ok'])
            self.assertEqual(json.loads(output.read_text())['fetched_at'], 1002)

    def test_old_cache_and_failed_publication_leave_last_good_untouched(self):
        for at in (999, 1004):
            with self.subTest(at=at), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
                directory = Path(d) / '.fixture.account-quota-v1'
                directory.mkdir(mode=0o700)
                output = directory / 'claude.json'
                publish_snapshot(snapshot(), output, clock=lambda: 1000)
                before = output.read_bytes()
                collector = SimpleNamespace(probe=lambda: SimpleNamespace(snapshot=snapshot(at), evidence={}))
                with patch.object(runner, 'OUTPUT', output):
                    result = runner.refresh_once(collector, validate_snapshot, publish_snapshot,
                                                 clock=iter([1001, 1003]).__next__)
                self.assertFalse(result['ok'])
                self.assertEqual(output.read_bytes(), before)

    def test_serial_loop_waits_120_after_completion_and_cancellation_prevents_new_child(self):
        event = threading.Event()
        order = []
        collector = SimpleNamespace(probe=lambda: order.append('probe') or SimpleNamespace(snapshot=snapshot(), evidence={}))
        def wait(seconds):
            order.append(('wait', seconds)); event.set(); return True
        result = runner.run_loop(collector, validate_snapshot,
            lambda v, p: order.append('publish'), stop=event, wait=wait,
            clock=lambda: 1000, emit=lambda v: order.append('receipt'))
        self.assertEqual(order, ['probe', 'publish', 'receipt', ('wait', 120)])
        self.assertEqual(result, 130)

    def test_cancel_during_probe_does_not_publish_or_start_another_child(self):
        event = threading.Event()
        calls = []
        def probe():
            calls.append('probe'); event.set(); raise KeyboardInterrupt()
        result = runner.run_loop(SimpleNamespace(probe=probe), validate_snapshot,
            lambda v, p: calls.append('publish'), stop=event, clock=lambda: 1000,
            wait=lambda t: self.fail('wait after interruption'), emit=lambda v: None)
        self.assertEqual(calls, ['probe'])
        self.assertEqual(result, 130)

    def test_consent_and_login_errors_stop_without_auth_repair(self):
        for category in ('setup_required', 'login_required', 'trust_required', 'unknown_command', 'cleanup_failed'):
            with self.subTest(category=category):
                class Failed(Exception): code = category
                collector = SimpleNamespace(probe=MagicMock(side_effect=Failed('sensitive')))
                result = runner.run_loop(collector, validate_snapshot, MagicMock(),
                    stop=threading.Event(), clock=lambda: 1000,
                    wait=lambda t: self.fail('fatal failure retried'), emit=lambda v: None)
                self.assertEqual(result, 1)
                self.assertEqual(collector.probe.call_count, 1)

    def test_signal_cancels_active_probe_and_second_signal_does_not_interrupt_cleanup(self):
        stop = threading.Event()
        handler = runner.signal_handler(stop)
        with self.assertRaises(KeyboardInterrupt): handler(signal.SIGTERM, None)
        self.assertTrue(stop.is_set())
        handler(signal.SIGTERM, None)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT)
        self.addCleanup(fixture.cleanup)
        database = Path(fixture.name) / 'guarded.sqlite'
        database.write_bytes(b'guarded synthetic boundary'); database.chmod(0o600)
        patcher = patch.object(ops, 'DB', database)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_real_sqlite_writer_gate_prevents_owner_admission_until_stop_known(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            database = Path(d) / 'fixture.sqlite'
            connection = sqlite3.connect(database)
            for table in ('tasks', 'audio_jobs', 'speech_jobs'):
                connection.execute('CREATE TABLE ' + table + '(state TEXT)')
            connection.commit(); connection.close()
            transitions = iter([{'ActiveState': 'deactivating', 'MainPID': '17', 'Job': '9'},
                                {'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}])
            def state():
                competing = sqlite3.connect(database, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError): competing.execute('BEGIN IMMEDIATE')
                finally: competing.close()
                return next(transitions)
            with patch.object(ops, 'DB', database), patch.object(ops, 'control', return_value=SimpleNamespace(returncode=0)), \
                    patch.object(ops, 'service_state', side_effect=state), patch.object(ops.time, 'sleep'):
                ops.stop_when_idle({}, 'initial')
            connection = sqlite3.connect(database, timeout=0)
            connection.execute('BEGIN IMMEDIATE'); connection.rollback(); connection.close()

    def test_prolonged_unknown_stop_keeps_original_gate_across_progress_logging_failure(self):
        gate = MagicMock()
        seen = []
        def state():
            gate.rollback.assert_not_called(); seen.append(1)
            return {'ActiveState': 'deactivating', 'MainPID': '17', 'Job': '9'} if len(seen) < 3 else {'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}
        receipt = {}
        with patch.object(ops.sqlite3, 'connect', return_value=gate), patch.object(ops, 'states', return_value={}), \
                patch.object(ops, 'active_count', return_value=0), \
                patch.object(ops, 'control', side_effect=subprocess.TimeoutExpired('fixture', 20)), \
                patch.object(ops, 'service_state', side_effect=state), \
                patch.object(ops, 'save', side_effect=OSError('fixture')), \
                patch.object(ops.time, 'monotonic', side_effect=[0, 111, 111, 122, 122]), patch.object(ops.time, 'sleep'):
            ops.stop_when_idle(receipt, 'initial')
        self.assertEqual(len(seen), 3)
        self.assertNotIn('recovery_pending', receipt)
        gate.rollback.assert_called_once(); gate.close.assert_called_once()

    def test_active_work_blocks_stop_and_stable_running_rejection_releases_gate(self):
        for active in (0, 1):
            with self.subTest(active=active):
                gate = MagicMock(); control = MagicMock(return_value=SimpleNamespace(returncode=5))
                with patch.object(ops.sqlite3, 'connect', return_value=gate), \
                        patch.object(ops, 'states', return_value={}), patch.object(ops, 'active_count', return_value=active), \
                        patch.object(ops, 'control', control), patch.object(ops, 'service_state', return_value={'ActiveState': 'active', 'MainPID': '17', 'Job': ''}):
                    with self.assertRaises(ops.OpsError): ops.stop_when_idle({}, 'rollback')
                self.assertEqual(control.call_count, 0 if active else 1)
                gate.rollback.assert_called_once()


class BindingAndPathTests(unittest.TestCase):
    def bundle(self, directory):
        # Synthetic approvals exist only inside this owned temporary fixture.
        root = Path(directory)
        for name in ops.BACKEND_PINS:
            (root / name).write_bytes((ROOT / name).read_bytes()); (root / name).chmod(0o600)
        implementations = {name: sha(('synthetic ' + name).encode()) for name in ops.IMPLEMENTATIONS}
        for name in ops.IMPLEMENTATIONS:
            (root / name).write_bytes(('synthetic ' + name).encode()); (root / name).chmod(0o600)
        for name in ('official_claude_usage.py', 'test_official_claude_usage.py'):
            (root / name).write_bytes(('synthetic ' + name).encode()); (root / name).chmod(0o600)
        source_files = {n: {'sha256': sha((root / n).read_bytes())} for n in ('official_claude_usage.py', 'test_official_claude_usage.py')}
        freeze = {'files': source_files, 'current_linux_verification': {
            'tests': 68, 'passed': 68, 'failures': 0, 'errors': 0, 'skipped': 0, 'source_files': source_files}}
        native = {'accepted': True, 'source_files': source_files, 'freshness_verified': True,
                  'actual_usage_reads': 1, 'fresh_snapshots': 1, 'fetched_at': 1000,
                  'completed_at': 1001, 'cwd': '/opt/pocket-checkpoint/owner', 'child_environment': ops.CHILD_ENVIRONMENT,
                  'all_children_reaped': True, 'all_ptys_closed': True}
        metadata = {'claude-producer-freeze.json': freeze,
                    'review-claude-usage-R4.json': {'verdict': 'approved_offline_code_only', 'source_files': source_files, 'findings': []},
                    'quotaops-native-acceptance.json': native,
                    'quotaops-tests.json': {'ok': True, 'tests': 30, 'failures': 0, 'errors': 0, 'skipped': 0, 'exit_code': 0, 'platform': 'linux', 'implementation_hashes': implementations},
                    'quotaops-review.json': {'approved': True, 'implementation_hashes': implementations, 'findings': []}}
        for name, value in metadata.items(): write_json(root / name, value)
        artifacts = {name: sha((root / name).read_bytes()) for name in (*ops.BACKEND_PINS, *metadata)}
        acceptance = {'version': 1, 'approved': True, 'candidate_hashes_sha256': ops.BACKEND_IDENTITY,
                      'artifact_hashes': artifacts, 'implementation_hashes': implementations,
                      'collector_files': source_files, 'collector_review': 'review-claude-usage-R4.json'}
        write_json(root / 'quotaops-acceptance.json', acceptance)
        return root, acceptance, metadata

    def test_exact_passing_reviewed_bundle_is_accepted_in_fixture(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            root, _, _ = self.bundle(d)
            self.assertEqual(ops.validate_bundle(root)['identity'], ops.BACKEND_IDENTITY)

    def test_missing_approval_drift_native_or_ops_binding_fails_closed(self):
        for drift in ('unapproved', 'backend', 'collector', 'ops', 'native', 'linux', 'review'):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
                root, acceptance, metadata = self.bundle(d)
                if drift == 'unapproved': acceptance['approved'] = False
                elif drift == 'backend': acceptance['candidate_hashes_sha256'] = '0' * 64
                elif drift == 'collector': (root / 'official_claude_usage.py').write_text('changed')
                elif drift == 'ops': (root / 'quota_refresh_020.py').write_text('changed')
                else:
                    name = {'native': 'quotaops-native-acceptance.json', 'linux': 'claude-producer-freeze.json', 'review': 'quotaops-review.json'}[drift]
                    if drift == 'native': metadata[name]['freshness_verified'] = False
                    elif drift == 'linux': metadata[name]['current_linux_verification']['source_files'] = {}
                    else: metadata[name]['implementation_hashes'] = {}
                    write_json(root / name, metadata[name]); acceptance['artifact_hashes'][name] = sha((root / name).read_bytes())
                write_json(root / 'quotaops-acceptance.json', acceptance)
                with self.assertRaises(ops.OpsError): ops.validate_bundle(root)

    def test_any_prior_marker_blocks_repeat_even_if_result_failed(self):
        for marker in ('dispatched', 'started', 'progress', 'result'):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
                root = Path(d); write_json(root / ('deployment-' + marker + '.json'), {'ok': False})
                with self.assertRaises(ops.OpsError): ops.assert_new_attempt(root, 'deployment')

    def test_scope_rejects_unknown_content_and_hash_collision(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            root = Path(d); owned = root / 'owned.py'; owned.write_bytes(b'ours'); owned.chmod(0o600)
            ops.validate_scope(root, {'owned.py': sha(b'ours')})
            (root / 'unrelated').write_text('other owner')
            with self.assertRaises(ops.OpsError): ops.validate_scope(root, {'owned.py': sha(b'ours')})
            (root / 'unrelated').unlink(); owned.write_bytes(b'changed')
            with self.assertRaises(ops.OpsError): ops.validate_scope(root, {'owned.py': sha(b'ours')})

    def test_symlink_hardlink_and_wrong_mode_rejected_without_following(self):
        # Synthetic lstat metadata also exercises POSIX policy on Windows.
        import stat
        good = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_uid=42, st_file_attributes=0)
        for delta in ({'st_mode': stat.S_IFLNK | 0o777}, {'st_nlink': 2},
                      {'st_mode': stat.S_IFREG | 0o644}, {'st_uid': 7}, {'st_file_attributes': 1024}):
            info = copy.copy(good)
            for key, value in delta.items(): setattr(info, key, value)
            with self.assertRaises(ops.OpsError): ops.validate_info(info, directory=False, private=True, uid=42)

    def test_client_only_accepts_known_success_and_fixed_ssh_policy(self):
        for value, expected in (({'ok': True}, 0), ({'ok': False}, 1),
                ({'ok': 1}, 1), ({'ok': 'true'}, 1), ({}, 1), (None, 1),
                ({'ok': False, 'recovery_pending': True}, 2),
                ({'ok': True, 'recovery_pending': True}, 2),
                ({'ok': True, 'outcome_uncertain': True}, 2)):
            with self.subTest(outcome=value): self.assertEqual(client.outcome_code(value), expected)
        argv = client.ssh_argv()
        self.assertEqual(argv, ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=5', 'pocket-host.example.invalid', '/opt/pocket-checkpoint/owner/.hermes/hermes-agent/venv/bin/python', '-B', '-'])


class DeltaAndRecoveryTests(unittest.TestCase):
    def fixture(self, directory):
        base = Path(directory)
        release, package, stage = (base / n for n in ('release', 'package', 'stage'))
        for path in (release / 'pocket', package / 'pocket'): path.mkdir(parents=True)
        stage.mkdir(mode=0o700)
        stage.chmod(0o700)
        ops.guard(stage, directory=True, private=True)
        (release / 'pocket/server.py').write_bytes(b'old server')
        candidate = {}
        for name in ('pocket/server.py', 'pocket/quota_export.py', 'pocket/quota_snapshots.py'):
            body = ('reviewed ' + name).encode(); (package / name).write_bytes(body); candidate[name] = sha(body)
        manifest = {'baseline_hashes': {'pocket/server.py': sha(b'old server')},
                    'candidate_hashes': candidate, 'changed_production_files': list(ops.CHANGED)}
        return release, package, stage, manifest

    def test_exact_three_file_apply_and_rollback_restores_old_server_only(self):
        self.assertTrue(callable(getattr(ops, 'backup_delta', None)), 'backup_delta is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            release, package, stage, manifest = self.fixture(d)
            with patch.object(ops, 'RELEASE', release), patch.object(ops, 'PACKAGE', package), patch.object(ops, 'STAGE', stage), \
                    patch.object(ops, 'service_state', return_value={'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}):
                ops.backup_delta(manifest)
                self.assertEqual({p.relative_to(stage / 'rollback').as_posix() for p in (stage / 'rollback').rglob('*') if p.is_file()}, {'pocket/server.py'})
                ops.apply_delta(manifest)
                self.assertEqual({n: sha((release / n).read_bytes()) for n in manifest['candidate_hashes']}, manifest['candidate_hashes'])
                ops.restore_delta(manifest)
                self.assertEqual((release / 'pocket/server.py').read_bytes(), b'old server')
                self.assertFalse((release / 'pocket/quota_export.py').exists())
                self.assertFalse((release / 'pocket/quota_snapshots.py').exists())

    def test_restore_refuses_live_service_and_unknown_file_without_writing(self):
        self.assertTrue(callable(getattr(ops, 'restore_delta', None)), 'restore_delta is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            release, package, stage, manifest = self.fixture(d)
            with patch.object(ops, 'RELEASE', release), patch.object(ops, 'PACKAGE', package), patch.object(ops, 'STAGE', stage), \
                    patch.object(ops, 'service_state', return_value={'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}) as state:
                ops.backup_delta(manifest); ops.apply_delta(manifest)
                before = {n: (release / n).read_bytes() for n in manifest['candidate_hashes']}
                state.return_value = {'ActiveState': 'active', 'MainPID': '17', 'Job': ''}
                with self.assertRaises(ops.OpsError): ops.restore_delta(manifest)
                self.assertEqual({n: (release / n).read_bytes() for n in before}, before)
                state.return_value = {'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}
                (release / 'pocket/quota_export.py').write_bytes(b'another operation')
                with self.assertRaises(ops.OpsError): ops.restore_delta(manifest)
                self.assertEqual((release / 'pocket/quota_export.py').read_bytes(), b'another operation')

    def test_partial_apply_can_restore_only_owned_known_revisions(self):
        self.assertTrue(callable(getattr(ops, 'restore_delta', None)), 'restore_delta is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            release, package, stage, manifest = self.fixture(d)
            with patch.object(ops, 'RELEASE', release), patch.object(ops, 'PACKAGE', package), patch.object(ops, 'STAGE', stage), \
                    patch.object(ops, 'service_state', return_value={'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}):
                ops.backup_delta(manifest)
                (release / 'pocket/quota_export.py').write_bytes((package / 'pocket/quota_export.py').read_bytes())
                ops.restore_delta(manifest)
                self.assertEqual((release / 'pocket/server.py').read_bytes(), b'old server')
                self.assertFalse((release / 'pocket/quota_export.py').exists())

    def test_missing_acceptance_blocks_deploy_before_any_external_action(self):
        self.assertTrue(callable(getattr(ops, 'deploy', None)), 'deploy is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            with patch.object(ops, 'STAGE', Path(d)), patch.object(ops, 'control') as control:
                with self.assertRaises(ops.OpsError): ops.deploy('a' * 32)
                control.assert_not_called()


class StreamingProcess:
    """Synthetic subprocess boundary; all reads remain real BytesIO reads."""
    def __init__(self, stdout=b'', stderr=b'', returncode=0):
        class Stream(io.BytesIO):
            def __init__(self, data): super().__init__(data); self.read_sizes = []
            def read(self, size=-1): self.read_sizes.append(size); return super().read(size)
        self.stdout = Stream(stdout); self.stderr = Stream(stderr); self.stdin = io.BytesIO()
        self.returncode = returncode; self.pid = 123; self.communicated = False; self.killed = False
    def communicate(self, **kwargs):
        self.communicated = True
        return self.stdout.getvalue(), self.stderr.getvalue()
    def wait(self, timeout=None): return self.returncode
    def poll(self): return self.returncode
    def kill(self): self.killed = True; self.returncode = -9


class CompletionSafetyTests(unittest.TestCase):
    def test_capture_streams_in_bounded_chunks_instead_of_communicate(self):
        process = StreamingProcess(stdout=b'x' * 65)
        with patch.object(ops.subprocess, 'Popen', return_value=process), patch.object(ops, 'MAX_BYTES', 32):
            with self.assertRaises(ops.OpsError): ops.bounded_run(['synthetic-tool'], timeout=2)
        self.assertFalse(process.communicated, 'communicate allocated the complete external output')
        self.assertTrue(process.stdout.read_sizes)
        self.assertTrue(all(0 < size <= 4096 for size in process.stdout.read_sizes))

    def test_exclusive_receipt_claim_has_one_winner_under_concurrent_dispatch(self):
        barrier = threading.Barrier(2)
        original_replace = os.replace
        def racing_replace(*args, **kwargs):
            barrier.wait(timeout=2)
            return original_replace(*args, **kwargs)
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            stage = Path(d); outcomes = []
            def claim():
                try: ops.save('deployment-dispatched.json', {'ok': False}, exclusive=True); outcomes.append('claimed')
                except (OSError, ops.OpsError): outcomes.append('blocked')
            with patch.object(ops, 'STAGE', stage), patch.object(ops.os, 'replace', side_effect=racing_replace):
                threads = [threading.Thread(target=claim) for _ in range(2)]
                for thread in threads: thread.start()
                for thread in threads: thread.join(timeout=3)
            self.assertEqual(sorted(outcomes), ['blocked', 'claimed'])
            self.assertEqual(json.loads((stage / 'deployment-dispatched.json').read_text()), {'ok': False})

    def test_http_captures_tls_identity_before_server_closes_socket(self):
        sock = SimpleNamespace(version=lambda: 'TLSv1.3', getpeercert=lambda **kw: b'synthetic certificate')
        connection = SimpleNamespace(sock=sock, request=lambda *a, **kw: None, close=lambda: None)
        def response():
            connection.sock = None
            return SimpleNamespace(status=200, read=lambda maximum: b'{"ok":true}')
        connection.getresponse = response
        with patch.object(ops.ssl, 'create_default_context', return_value=SimpleNamespace()), \
                patch.object(ops.http.client, 'HTTPSConnection', return_value=connection):
            code, body, tls = ops.request('GET', '/targets/linux/models', auth=False)
        self.assertEqual(code, 200)
        self.assertEqual(tls, {'version': 'TLSv1.3', 'leaf_sha256': sha(b'synthetic certificate')})

    def test_activation_requires_new_file_revision_even_with_same_second_timestamp(self):
        self.assertTrue(callable(getattr(ops, 'new_publication', None)), 'new publication check is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            directory = Path(d); output = directory / 'claude.json'; write_json(output, snapshot())
            prior = ops.revision(output.lstat())
            with patch.object(ops, 'NUMERIC', directory):
                self.assertFalse(ops.new_publication(prior, 1000, [{'fetched_at': 1000}]))
                ops.atomic_bytes(output, json.dumps(snapshot()).encode(), replace=True)
                self.assertTrue(ops.new_publication(prior, 1000, [{'fetched_at': 1000}]))

    def test_foreign_quota_unit_is_never_stopped(self):
        foreign = {'LoadState': 'loaded', 'Transient': 'no', 'ActiveState': 'active', 'MainPID': '7', 'Job': ''}
        with patch.object(ops, 'unit_info', return_value=foreign), patch.object(ops, 'control') as control:
            with self.assertRaises(ops.OpsError): ops.stop_owned_quota()
            control.assert_not_called()

    def test_private_stage_and_test_payload_need_no_native_acceptance(self):
        self.assertTrue(callable(getattr(client, 'make_stage_payload', None)), 'offline stage payload is missing')
        self.assertTrue(callable(getattr(ops, 'stage_offline', None)), 'offline stage is missing')
        payload = client.make_stage_payload(ROOT)
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            stage = Path(d) / 'owned'
            with patch.object(ops, 'STAGE', stage), patch.object(ops, 'control') as control:
                receipt = ops.stage_offline(payload)
                self.assertTrue(receipt['ok'])
                self.assertFalse(receipt['production_accepted'])
                self.assertFalse((stage / 'quotaops-acceptance.json').exists())
                self.assertTrue(ops.validate_offline_stage()['fixture_only'])
                control.assert_not_called()
                with patch.object(ops, 'service_state', return_value={}), patch.object(ops, 'unit_info', return_value={}):
                    inspected = ops.inspect_stage()
                self.assertTrue(inspected['ok'])
                self.assertFalse(inspected['production_accepted'])
                (stage / 'another-owner').write_text('collision')
                with self.assertRaises(ops.OpsError): ops.stage_offline(payload)

    def test_bootstrap_has_fixed_scope_and_never_embeds_native_or_secret_material(self):
        self.assertTrue(callable(getattr(client, 'build_script', None)), 'bootstrap is missing')
        script = client.build_script('stage', client.make_stage_payload(ROOT), ROOT)
        compile(script, '<review-only-bootstrap>', 'exec')
        self.assertIn('account-quota-020-ops', script)
        self.assertNotIn('POCKET_DEVICE_TOKEN=', script)
        with self.assertRaises(ops.OpsError): client.build_script('arbitrary-shell-command', {}, ROOT)

    def test_client_missing_full_acceptance_prevents_any_transport(self):
        self.assertTrue(callable(getattr(client, 'main', None)), 'client CLI is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            with patch.object(client, 'ROOT', Path(d)), patch.object(ops, 'bounded_run') as process:
                self.assertEqual(client.main(['deploy']), 1)
                process.assert_not_called()

    def test_missing_or_changed_tls_identity_is_not_a_preservation_proof(self):
        self.assertTrue(callable(getattr(ops, 'same_tls', None)), 'TLS comparison is missing')
        tls = {'version': 'TLSv1.3', 'leaf_sha256': 'a' * 64}
        self.assertTrue(ops.same_tls(tls, dict(tls)))
        for bad in (None, {}, {'version': 'TLSv1.1', 'leaf_sha256': 'a' * 64},
                    {'version': 'TLSv1.3', 'leaf_sha256': 'b' * 64}):
            self.assertFalse(ops.same_tls(tls, bad))

    def test_verification_requires_stt_process_parent_and_zero_gateway_restarts(self):
        self.assertTrue(callable(getattr(ops, 'verify_processes', None)), 'process verification is missing')
        for parent, restarts in ((17, 0), (7, 0), (17, 1)):
            with self.subTest(parent=parent, restarts=restarts):
                def metadata(pid): return {'Pid': pid, 'PPid': 1 if pid == 17 else parent, 'Threads': 1, 'rss_bytes': 65536}
                with patch.object(ops, 'process_metadata', side_effect=metadata), \
                        patch.object(ops, 'control', return_value=SimpleNamespace(stdout='NRestarts=' + str(restarts) + '\nMemoryCurrent=65536')):
                    if parent == 17 and restarts == 0:
                        result = ops.verify_processes({'resident_stt': {'worker_pid': 18}}, 17)
                        self.assertEqual(result['processes']['stt_worker']['PPid'], 17)
                    else:
                        with self.assertRaises(ops.OpsError): ops.verify_processes({'resident_stt': {'worker_pid': 18}}, 17)


class WorkflowTests(unittest.TestCase):
    fixture = DeltaAndRecoveryTests.fixture
    def execute_fixture(self, directory, *, fail_candidate=False, admit_owner=False):
        self.assertTrue(callable(getattr(ops, 'execute_deployment', None)), 'deployment core is missing')
        from contextlib import ExitStack
        release, package, stage, manifest = self.fixture(directory)
        write_json(release / '.transport-011-used.json', {})
        helper = release / 'control_transport_011.py'
        helper.write_text('def verify_closed():\n    return {"ok": True}\n'); helper.chmod(0o600)
        protected = {'transport_control': helper}
        for i in range(7):
            path = Path(directory) / ('protected-' + str(i)); path.write_bytes(('protected ' + str(i)).encode()); path.chmod(0o600)
            protected['protected_' + str(i)] = path
        manifest['protected_hashes'] = {n: sha(p.read_bytes()) for n, p in protected.items()}
        database = Path(directory) / 'workflow.sqlite'
        connection = sqlite3.connect(database)
        for table in ('tasks', 'audio_jobs', 'speech_jobs'): connection.execute('CREATE TABLE ' + table + '(state TEXT)')
        connection.commit(); connection.close(); database.chmod(0o600)
        state = {'ActiveState': 'active', 'MainPID': '17', 'Job': ''}
        actions = []; clock = [0]
        tls = {'version': 'TLSv1.3', 'leaf_sha256': 'a' * 64}
        def control(*args, **kwargs):
            actions.append(args)
            if args[0] == 'stop':
                competing = sqlite3.connect(database, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError): competing.execute('BEGIN IMMEDIATE')
                finally: competing.close()
                state.update(ActiveState='inactive', MainPID='0')
            if args[0] == 'start':
                state.update(ActiveState='active', MainPID='17')
                if admit_owner and (release / 'pocket/server.py').read_bytes() != b'old server':
                    writer = sqlite3.connect(database)
                    writer.execute("INSERT INTO tasks VALUES('running')"); writer.commit(); writer.close()
            text = ('disabled' if args[0] == 'is-enabled' else str(release) if 'WorkingDirectory' in args else
                    'active' if args[0] == 'is-active' else '17' if 'MainPID' in args else '\n'.join(k + '=' + v for k, v in state.items()))
            return SimpleNamespace(returncode=0, stdout=text)
        def runtime(mode, **kwargs):
            if mode == '--probe-after' and fail_candidate and (release / 'pocket/server.py').read_bytes() != b'old server':
                clock[0] += 100
                raise ops.OpsError('synthetic_readiness_failure')
            return {'ok': True, 'tls': tls, 'resident_stt': {'worker_pid': 18}}
        def monotonic(): clock[0] += 1; return clock[0]
        with ExitStack() as stack:
            for name, value in (('RELEASE', release), ('PACKAGE', package), ('STAGE', stage), ('DB', database), ('PROTECTED', protected)):
                stack.enter_context(patch.object(ops, name, value))
            stack.enter_context(patch.object(ops, 'control', side_effect=control))
            stack.enter_context(patch.object(ops, 'runtime', side_effect=runtime))
            stack.enter_context(patch.object(ops, 'process_cwd', return_value=str(release)))
            stack.enter_context(patch.object(ops.time, 'monotonic', side_effect=monotonic))
            stack.enter_context(patch.object(ops.time, 'sleep'))
            receipt = ops.execute_deployment(manifest, {'ok': False, **ops.counters()})
        return receipt, actions, release, manifest

    def test_deployment_failure_restores_exact_files_under_a_new_idle_gate(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            receipt, actions, release, manifest = self.execute_fixture(d, fail_candidate=True)
            self.assertFalse(receipt['ok'])
            self.assertTrue(receipt['rolled_back'])
            self.assertTrue(receipt['original_service_restored'])
            self.assertEqual(sum(a[0] == 'stop' for a in actions), 2)
            self.assertEqual((release / 'pocket/server.py').read_bytes(), b'old server')
            self.assertFalse((release / 'pocket/quota_export.py').exists())

    def test_owner_admitted_after_restart_defers_rollback_without_restoring_live_files(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            receipt, actions, release, manifest = self.execute_fixture(d, fail_candidate=True, admit_owner=True)
            self.assertFalse(receipt['ok'])
            self.assertTrue(receipt['recovery_pending'])
            self.assertEqual(sum(a[0] == 'stop' for a in actions), 1)
            self.assertEqual({n: sha((release / n).read_bytes()) for n in manifest['candidate_hashes']}, manifest['candidate_hashes'])


class ReviewRegressionTests(unittest.TestCase):
    """R1 regressions use owned files/SQLite and fake every external boundary."""
    fixture = DeltaAndRecoveryTests.fixture

    @contextmanager
    def rollback_fixture(self, directory, *, deployed=False, backup=False):
        release, package, stage, manifest = self.fixture(directory)
        # Isolate rollback regressions from the separately tested R1 umask bug.
        stage.chmod(0o700)
        manifest['protected_hashes'] = {}
        write_json(release / '.transport-011-used.json', {})
        database = Path(directory) / 'rollback.sqlite'
        connection = sqlite3.connect(database)
        for table in ('tasks', 'audio_jobs', 'speech_jobs'):
            connection.execute('CREATE TABLE ' + table + '(state TEXT)')
        connection.commit(); connection.close(); database.chmod(0o600)
        acceptance = 'b' * 64; original_id = 'a' * 32; rollback_id = 'c' * 32
        tls = {'version': 'TLSv1.3', 'leaf_sha256': 'd' * 64}
        original = {'operation_id': original_id, 'operator_pid': 23456,
                    'acceptance_sha256': acceptance, 'operation_phase': 'deployment',
                    'ok': False, 'before': {'ok': True, 'tls': tls}}
        write_json(stage / 'deployment-dispatched.json', dict(original))
        write_json(stage / 'deployment-started.json', dict(original))
        write_json(stage / 'deployment-result.json', dict(original))
        write_json(stage / 'rollback-dispatched.json', {'operation_id': rollback_id,
            'operator_pid': os.getpid(), 'acceptance_sha256': acceptance})
        state = {'ActiveState': 'active', 'MainPID': '17', 'Job': ''}
        actions = []
        def control(*args, **kwargs):
            actions.append(args)
            if args[0] == 'stop':
                competing = sqlite3.connect(database, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError): competing.execute('BEGIN IMMEDIATE')
                finally: competing.close()
                state.update(ActiveState='inactive', MainPID='0')
            elif args[0] == 'start': state.update(ActiveState='active', MainPID='17')
            text = ('disabled' if args[0] == 'is-enabled' else str(release) if 'WorkingDirectory' in args else
                    'active' if args[0] == 'is-active' else '17' if 'MainPID' in args else
                    '\n'.join(k + '=' + v for k, v in state.items()))
            return SimpleNamespace(returncode=0, stdout=text)
        bundle = {'acceptance_sha256': acceptance, 'manifest': manifest}
        with ExitStack() as stack:
            for name, value in (('RELEASE', release), ('PACKAGE', package), ('STAGE', stage),
                                ('DB', database), ('PROTECTED', {})):
                stack.enter_context(patch.object(ops, name, value))
            stack.enter_context(patch.object(ops, 'validate_bundle', return_value=bundle))
            stack.enter_context(patch.object(ops, 'validate_remote_stage'))
            stack.enter_context(patch.object(ops, 'control', side_effect=control))
            runtime = stack.enter_context(patch.object(ops, 'runtime', return_value={'ok': True, 'tls': tls}))
            stop_quota = stack.enter_context(patch.object(ops, 'stop_owned_quota'))
            stack.enter_context(patch.object(ops, 'unit_info', return_value={'LoadState': 'not-found'}))
            stack.enter_context(patch.object(ops, 'process_cwd', return_value=str(release)))
            alive = stack.enter_context(patch.object(ops, 'operator_process_exists', return_value=False, create=True))
            if backup or deployed:
                state.update(ActiveState='inactive', MainPID='0')
                if backup: ops.backup_delta(manifest)
                if deployed: ops.apply_delta(manifest)
                state.update(ActiveState='active', MainPID='17')
            yield SimpleNamespace(stage=stage, release=release, manifest=manifest, actions=actions,
                runtime=runtime, stop_quota=stop_quota, alive=alive, state=state,
                rollback_id=rollback_id, original=original, bundle=bundle)

    def test_closure_uses_local_markers_and_pinned_helper_without_executing_it(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            root = Path(d); release = root / 'release'; release.mkdir(mode=0o700)
            helper = release / 'control_transport_011.py'
            # This fake protected helper exposes the same transitive route bug.
            helper.write_text('import http.client\ndef verify_closed():\n'
                '    http.client.HTTPSConnection("synthetic-host").request("GET", "/diagnostics/transport")\n'
                '    return {"ok": True}\n')
            helper.chmod(0o600)
            helper_bytes = helper.read_bytes()
            manifest = {'protected_hashes': {'transport_control': sha(helper.read_bytes())}}
            write_json(root / 'manifest-R3.json', manifest)
            write_json(release / '.transport-011-used.json', {})
            with patch.object(ops, 'RELEASE', release), patch.object(ops, 'STAGE', root), \
                    patch.object(ops.http.client, 'HTTPSConnection') as http, \
                    patch.object(ops.subprocess, 'Popen') as process:
                result = ops.closed()
                self.assertTrue(result['ok'])
                http.assert_not_called(); process.assert_not_called()
                self.assertEqual(result['evidence'], 'local_markers_and_pinned_helper')
                for changed in ('arm', 'helper', 'missing_used'):
                    with self.subTest(changed=changed):
                        helper.write_bytes(helper_bytes)
                        if changed == 'arm': write_json(release / '.transport-011-arm.json', {})
                        elif changed == 'helper': helper.write_text('raise AssertionError("must never execute")\n')
                        else: (release / '.transport-011-used.json').unlink()
                        with self.assertRaises(ops.OpsError): ops.closed()
                        if changed == 'arm': (release / '.transport-011-arm.json').unlink()
                http.assert_not_called(); process.assert_not_called()

    def test_held_operation_lock_excludes_every_mutating_phase_and_dispatch(self):
        self.assertTrue(callable(getattr(ops, 'operation_lock', None)), 'held operation lock is missing')
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            stage = Path(d)
            with patch.object(ops, 'STAGE', stage), patch.object(ops, 'validate_bundle', return_value={}), \
                    patch.object(ops, 'validate_remote_stage'), patch.object(ops, 'control') as control, \
                    patch.object(ops.subprocess, 'Popen') as process:
                with ops.operation_lock():
                    for phase, function in (('deployment', ops.deploy), ('activation', ops.activate), ('rollback', ops.rollback)):
                        with self.subTest(phase=phase):
                            outcome = []
                            def compete():
                                try: function('a' * 32)
                                except Exception as error: outcome.append(str(error))
                            thread = threading.Thread(target=compete); thread.start(); thread.join(timeout=3)
                            self.assertFalse(thread.is_alive())
                            self.assertEqual(outcome, ['operator_already_running_inspect'])
                    with self.assertRaisesRegex(ops.OpsError, 'operator_already_running_inspect'):
                        ops.dispatch('rollback')
                    control.assert_not_called(); process.assert_not_called()
                # The OS releases the held lock; no stale-lock deletion is needed.
                with ops.operation_lock(): pass
                (stage / ops.OPERATION_LOCK).write_bytes(b'foreign lock contents')
                with self.assertRaisesRegex(ops.OpsError, 'operation_lock_collision'):
                    with ops.operation_lock(): self.fail('unknown lock was adopted')

    def test_live_started_child_blocks_rollback_when_dispatch_pid_is_missing(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, self.rollback_fixture(d) as fixture:
            dispatch = dict(fixture.original); dispatch.pop('operator_pid')
            write_json(fixture.stage / 'deployment-dispatched.json', dispatch)
            fixture.alive.return_value = True
            inspected = ops.inspect_stage()['attempts']['deployment']
            self.assertEqual(inspected['ownership_state'], 'child_identity_reconciled')
            self.assertTrue(inspected['operator_process_exists'])
            with self.assertRaisesRegex(ops.OpsError, 'deployment_operator_still_supervising'):
                ops.rollback(fixture.rollback_id)
            fixture.stop_quota.assert_not_called()
            self.assertFalse(any(a[0] in ('stop', 'start') for a in fixture.actions))

    def test_incomplete_or_inconsistent_child_receipts_block_before_any_stop(self):
        for change in ('dispatch_pid', 'progress_identity', 'result_pid', 'missing_child'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, self.rollback_fixture(d) as fixture:
                if change == 'missing_child': (fixture.stage / 'deployment-started.json').unlink()
                else:
                    marker = {'dispatch_pid': 'dispatched', 'progress_identity': 'progress', 'result_pid': 'result'}[change]
                    receipt = dict(fixture.original)
                    if change == 'dispatch_pid': receipt['operator_pid'] = 34567
                    elif change == 'progress_identity': receipt['operation_id'] = 'f' * 32
                    else: receipt.pop('operator_pid')
                    write_json(fixture.stage / ('deployment-' + marker + '.json'), receipt)
                with self.assertRaises(ops.OpsError): ops.rollback(fixture.rollback_id)
                fixture.stop_quota.assert_not_called()
                self.assertFalse(any(a[0] in ('stop', 'start') for a in fixture.actions))

    def test_spawn_success_pid_receipt_failure_retains_child_identity_and_requires_inspect(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
            stage = Path(d); bundle = {'acceptance_sha256': 'b' * 64}
            original_save = ops.save
            def save(name, value, *, exclusive=False):
                if name == 'deployment-dispatched.json' and not exclusive:
                    raise OSError('synthetic post-spawn receipt failure')
                return original_save(name, value, exclusive=exclusive)
            def spawn(argv, **kwargs):
                with patch.object(ops.os, 'getpid', return_value=23456):
                    child = ops.begin_attempt('deployment', argv[-1], bundle)
                    ops.save('deployment-progress.json', child)
                return SimpleNamespace(pid=23456)
            with patch.object(ops, 'STAGE', stage), patch.object(ops, 'validate_bundle', return_value=bundle), \
                    patch.object(ops, 'validate_remote_stage'), patch.object(ops, 'save', side_effect=save), \
                    patch.object(ops.subprocess, 'Popen', side_effect=spawn), patch.object(ops, 'control') as control:
                result = ops.dispatch('deployment')
            self.assertFalse(result['ok']); self.assertTrue(result['outcome_uncertain'])
            self.assertTrue(result['recovery_pending'])
            dispatch = ops.read_json(stage / 'deployment-dispatched.json')
            started = ops.read_json(stage / 'deployment-started.json')
            self.assertNotIn('operator_pid', dispatch)
            self.assertEqual(started['operator_pid'], 23456)
            self.assertEqual(started['operation_id'], dispatch['operation_id'])
            control.assert_not_called()

    def test_unchanged_verified_baseline_without_backup_is_noop_and_keeps_service_running(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, self.rollback_fixture(d) as fixture:
            result = ops.rollback(fixture.rollback_id)
            self.assertTrue(result['ok']); self.assertTrue(result['no_op'])
            self.assertTrue(result['original_service_restored'])
            self.assertEqual(fixture.state['ActiveState'], 'active')
            self.assertEqual((fixture.release / 'pocket/server.py').read_bytes(), b'old server')
            self.assertFalse((fixture.stage / 'rollback').exists())
            fixture.runtime.assert_called_once_with('--probe-after')
            fixture.stop_quota.assert_not_called()
            self.assertFalse(any(a[0] in ('stop', 'start') for a in fixture.actions))

    def test_missing_or_bad_complete_backup_blocks_delta_rollback_before_either_stop(self):
        for change in ('missing', 'partial', 'drift'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, \
                    self.rollback_fixture(d, deployed=True, backup=change == 'drift') as fixture:
                if change == 'partial': (fixture.release / 'pocket/server.py').write_bytes(b'old server')
                if change == 'drift': (fixture.stage / 'rollback/pocket/server.py').write_bytes(b'unknown backup')
                before = {p.name: p.read_bytes() for p in (fixture.release / 'pocket').iterdir()}
                with self.assertRaises((ops.OpsError, FileNotFoundError)): ops.rollback(fixture.rollback_id)
                self.assertEqual({p.name: p.read_bytes() for p in (fixture.release / 'pocket').iterdir()}, before)
                self.assertEqual(fixture.state['ActiveState'], 'active')
                fixture.stop_quota.assert_not_called()
                self.assertFalse(any(a[0] in ('stop', 'start') for a in fixture.actions))

    def test_valid_backup_rollback_keeps_real_admission_gate_and_restores_exact_files(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, self.rollback_fixture(d, deployed=True, backup=True) as fixture:
            result = ops.rollback(fixture.rollback_id)
            self.assertTrue(result['ok']); self.assertTrue(result['original_service_restored'])
            self.assertEqual([a[0] for a in fixture.actions if a[0] in ('stop', 'start')], ['stop', 'start'])
            self.assertEqual((fixture.release / 'pocket/server.py').read_bytes(), b'old server')
            self.assertFalse((fixture.release / 'pocket/quota_export.py').exists())
            self.assertFalse((fixture.release / 'pocket/quota_snapshots.py').exists())

    def test_uncertain_rollback_keeps_both_locks_and_its_own_progress_identity(self):
        with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, self.rollback_fixture(d, deployed=True, backup=True) as fixture:
            original_receipt = (fixture.stage / 'deployment-result.json').read_bytes()
            observed = []; clock = [0]
            def state():
                if len(observed) < 3:
                    competing = sqlite3.connect(ops.DB, timeout=0)
                    try:
                        with self.assertRaises(sqlite3.OperationalError): competing.execute('BEGIN IMMEDIATE')
                    finally: competing.close()
                    with self.assertRaisesRegex(ops.OpsError, 'operator_already_running_inspect'):
                        with ops.operation_lock(): self.fail('operator lock was released during stop')
                    observed.append(1)
                    if len(observed) < 3:
                        return {'ActiveState': 'deactivating', 'MainPID': '17', 'Job': '9'}
                return dict(fixture.state)
            def monotonic(): clock[0] += 200 if len(observed) < 3 else .01; return clock[0]
            with patch.object(ops, 'service_state', side_effect=state), \
                    patch.object(ops.time, 'monotonic', side_effect=monotonic), patch.object(ops.time, 'sleep'):
                result = ops.rollback(fixture.rollback_id)
            self.assertTrue(result['ok'])
            self.assertEqual(len(observed), 3)
            self.assertEqual((fixture.stage / 'deployment-result.json').read_bytes(), original_receipt)
            self.assertFalse((fixture.stage / 'deployment-progress.json').exists())
            progress = ops.read_json(fixture.stage / 'rollback-progress.json')
            self.assertEqual(progress['operation_phase'], 'rollback')
            self.assertEqual(progress['operation_id'], fixture.rollback_id)
            self.assertEqual(progress['operator_pid'], os.getpid())

    def test_fixture_stage_explicitly_private_under_umask_022(self):
        requested = []; original_mkdir = Path.mkdir
        def mkdir(path, mode=0o777, parents=False, exist_ok=False):
            if path.name == 'stage': requested.append(mode)
            return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d, patch.object(Path, 'mkdir', mkdir):
                _, _, stage, _ = self.fixture(d)
                self.assertEqual(requested, [0o700])
                if os.name == 'posix': self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o700)
                with patch.object(ops, 'STAGE', stage): ops.save('deployment-progress.json', {'ok': False})
                self.assertEqual(ops.read_json(stage / 'deployment-progress.json'), {'ok': False})
        finally: os.umask(previous)


class RuntimeBindingTests(unittest.TestCase):
    def test_runner_requires_accepted_hashes_even_when_runtime_manifest_is_self_consistent(self):
        self.assertTrue(callable(getattr(runner, 'verify_runtime_bindings', None)), 'runtime acceptance binding is missing')
        for drift in (None, 'collector', 'backend_subset', 'acceptance'):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory(prefix='quotaops-', dir=TEMP_ROOT) as d:
                base = Path(d); stage = base / 'stage'; stage.mkdir(mode=0o700)
                stage, acceptance, metadata = BindingAndPathTests().bundle(stage)
                runtime = base / 'runtime'; runtime.mkdir(mode=0o700)
                release = base / 'release'; (release / 'pocket').mkdir(parents=True)
                manifest = json.loads((ROOT / 'manifest-R3.json').read_text())
                backend_hashes = {n: h for n, h in manifest['candidate_hashes'].items() if n.startswith('pocket/')}
                for name in backend_hashes: (release / name).write_bytes((BACKEND / name).read_bytes())
                for name in ('official_claude_usage.py', 'quota_refresh_020.py'):
                    (runtime / name).write_bytes((stage / name).read_bytes()); (runtime / name).chmod(0o600)
                profile = {'version': 1, 'candidate_hashes_sha256': ops.BACKEND_IDENTITY,
                    'acceptance_sha256': sha((stage / 'quotaops-acceptance.json').read_bytes()), 'cwd': '/opt/pocket-checkpoint/owner',
                    'child_environment': ops.CHILD_ENVIRONMENT, 'interval_seconds': 120, 'probe_deadline_seconds': 55.0,
                    'runtime_hashes': {name: sha((runtime / name).read_bytes()) for name in ('official_claude_usage.py', 'quota_refresh_020.py')},
                    'backend_hashes': backend_hashes}
                if drift == 'collector':
                    (runtime / 'official_claude_usage.py').write_bytes(b'changed collector')
                    profile['runtime_hashes']['official_claude_usage.py'] = sha(b'changed collector')
                elif drift == 'backend_subset': profile['backend_hashes'] = {'pocket/quota_export.py': backend_hashes['pocket/quota_export.py']}
                elif drift == 'acceptance': profile['acceptance_sha256'] = '0' * 64
                with patch.object(runner, 'STAGE', stage), patch.object(runner, 'RUNTIME', runtime), patch.object(runner, 'RELEASE', release):
                    if drift is None: runner.verify_runtime_bindings(profile)
                    else:
                        with self.assertRaises(ValueError): runner.verify_runtime_bindings(profile)


if __name__ == '__main__':
    unittest.main()
