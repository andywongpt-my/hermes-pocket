"""Synthetic AW wrapper checks; no CLI, network, real account, or settings calls."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TARGET = Path(__file__).with_name('claude_aw_020.py')
BASE = TARGET.parent.parent
NOW = 1800000000
IDENTITY = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
            'subscriptionType': 'max', 'email': 'synthetic-private@example.invalid',
            'orgId': 'synthetic-private-org'}
SOURCE_PATHS = [TARGET, BASE / 'claude-callback-020' / 'claude_callback_020.py',
                BASE / 'claude_statusline_quota_020.py',
                *[BASE / 'backend' / 'pocket' / x for x in
                  ('__init__.py', 'usage.py', 'quota_snapshots.py', 'quota_export.py')]]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identity_digest(value=IDENTITY):
    return digest(canonical({k: value[k] for k in
        ('authMethod', 'apiProvider', 'subscriptionType', 'email', 'orgId')}))


def event(i=10, o=20, **extra):
    value = {'session_id': 'synthetic-PRIVATE-session', 'context_window': {
        'total_input_tokens': i, 'total_output_tokens': o}, 'rate_limits': {
        'five_hour': {'used_percentage': 0, 'resets_at': NOW + 3600},
        'seven_day': {'used_percentage': 23.5, 'resets_at': NOW + 86400}}}
    value.update(extra)
    return canonical(value)


class AWTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TARGET.exists():
            spec = importlib.util.spec_from_file_location('synthetic_aw020', TARGET)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertTrue(TARGET.exists(), 'AW passive wrapper has not been implemented')
        temporary = tempfile.TemporaryDirectory(prefix='claude-aw-fixture-', dir=TARGET.parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / '.callback-state'
        self.numeric = self.root / '.fixture.account-quota-v1'
        self.relay = self.root / '.relay-lock'
        for folder in (self.state, self.numeric, self.relay):
            folder.mkdir(mode=0o700)
        # These are inert checked files; injected functions never execute them.
        self.executable = self.root / 'synthetic-claude.exe'
        self.executable.write_bytes(b'inert auth fixture')
        self.ssh = self.root / 'synthetic-ssh.exe'
        self.ssh.write_bytes(b'inert ssh fixture')
        self.config = {'version': 1, 'profile': 'codex_claude_passive_v1',
            'expected_account_sha256': identity_digest(),
            'auth_argv': [str(self.executable), 'auth', 'status'],
            'auth_file_pins': {str(self.executable): digest(self.executable.read_bytes())},
            'source_pins': {str(x): digest(x.read_bytes()) for x in SOURCE_PATHS},
            'state_directory': str(self.state), 'output': str(self.numeric / 'claude.json'),
            'relay_lock_directory': str(self.relay),
            'audit_path': str(self.relay / 'audit.json'),
            'receiver_argv': [str(self.ssh), '-T', '-oBatchMode=yes',
                              'synthetic-host', 'fixed-claude-receiver']}
        self.sent = []
        self.auth_calls = []

    def auth(self):
        self.auth_calls.append(True)
        return copy.deepcopy(IDENTITY)

    def send(self, raw):
        self.sent.append(raw)
        value = json.loads(raw)
        return {'ok': True, 'profile': 'codex_claude_passive_v1', 'provider': 'claude',
                'fetched_at': value['fetched_at'], 'snapshot_sha256': digest(raw)}

    def call(self, raw=None, at=NOW, **kwargs):
        return self.module.run_callback(event() if raw is None else raw, self.config,
            auth_status=kwargs.get('auth_status', self.auth),
            send=kwargs.get('send', self.send), clock=kwargs.get('clock', lambda: at))

    def fresh(self):
        self.assertEqual(self.call()['status'], 'baseline')
        result = self.call(event(11, 22), NOW + 1)
        self.assertEqual(result['status'], 'sent')
        return result

    def persisted(self):
        return b''.join(path.read_bytes() for folder in
            (self.state, self.numeric, self.relay) for path in folder.iterdir() if path.is_file())

    def test_baseline_then_fresh_sends_only_numeric_snapshot_with_original_time(self):
        result = self.fresh()
        self.assertEqual(len(self.sent), 1)
        value = json.loads(self.sent[0])
        self.assertEqual(set(value), {'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'})
        self.assertEqual(value['fetched_at'], NOW + 1)
        self.assertEqual(value['metrics'][0]['used_percent'], 0)
        self.assertEqual(result['snapshot_sha256'], digest(self.sent[0]))
        self.assertEqual(json.loads(Path(self.config['audit_path']).read_bytes()), result)

    def test_account_mismatch_blocks_callback_state_and_transport(self):
        changed = {**IDENTITY, 'email': 'another-private@example.invalid'}
        result = self.call(auth_status=lambda: changed)
        self.assertEqual(result['error'], 'account_mismatch')
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(list(self.numeric.iterdir()), [])
        self.assertEqual(self.sent, [])
        self.assertNotIn(changed['email'].encode(), self.persisted())

    def test_unacceptable_or_malformed_auth_is_private_and_never_consumes(self):
        cases = [{**IDENTITY, 'loggedIn': 1}, {**IDENTITY, 'authMethod': 'api'},
                 {**IDENTITY, 'apiProvider': 'thirdParty'}, {**IDENTITY, 'subscriptionType': 'pro'},
                 {**IDENTITY, 'email': ''}, {**IDENTITY, 'email': 'x' * 321},
                 {**IDENTITY, 'email': 'private\nidentity'}, {**IDENTITY, 'orgId': 12},
                 b'{"loggedIn":true,"loggedIn":false}', b'{"x":NaN}', b'x' * 65537]
        for value in cases:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(self.call(auth_status=lambda: value)['error'], 'invalid_auth')
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.sent, [])

    def test_raw_markers_and_metadata_never_persist_or_travel(self):
        marker = 'PRIVATE-RAW-DO-NOT-SAVE'
        self.call(event(transcript_path=marker, model={'id': marker}, cwd=marker))
        self.call(event(11, 22, transcript_path=marker, api_key=marker), NOW + 1)
        combined = self.persisted() + b''.join(self.sent)
        for private in (marker, IDENTITY['email'], IDENTITY['orgId'], 'synthetic-PRIVATE-session'):
            self.assertNotIn(private.encode(), combined)

    def test_strict_duplicate_nonfinite_and_bounded_input_preserve_callback_state(self):
        self.fresh()
        before = (self.state / 'claude-state.json').read_bytes()
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}',
                    b'\xff', b'[]', b'null', b'{}{}', b'x' * 65537):
            self.assertEqual(self.call(raw, NOW + 2)['status'], 'error')
            self.assertEqual((self.state / 'claude-state.json').read_bytes(), before)
        self.assertEqual(len(self.sent), 1)

    def test_cached_error_timer_missing_and_expired_never_send_or_restamp(self):
        self.fresh()
        output = (self.numeric / 'claude.json').read_bytes()
        events = [event(11, 22), event(12, 23, cached=True), event(12, 23),
                  event(13, 24, error={'message': 'PRIVATE-error'}), event(13, 24),
                  event(14, 25, type='timer'), event(15, 26, rate_limits=None),
                  event(16, 27, rate_limits={'five_hour': {
                      'used_percentage': 20, 'resets_at': NOW}})]
        for offset, raw in enumerate(events, 2):
            self.assertNotEqual(self.call(raw, NOW + offset)['status'], 'sent')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.numeric / 'claude.json').read_bytes(), output)

    def test_identity_switch_cannot_advance_high_water_state(self):
        self.fresh()
        before = (self.state / 'claude-state.json').read_bytes()
        self.call(event(80, 90), NOW + 2,
                  auth_status=lambda: {**IDENTITY, 'orgId': 'switched-private-org'})
        self.assertEqual((self.state / 'claude-state.json').read_bytes(), before)
        self.assertEqual(self.call(event(12, 23), NOW + 3)['status'], 'sent')
        self.assertEqual(len(self.sent), 2)

    def test_clock_regression_and_stale_before_send_never_relay(self):
        self.fresh()
        self.assertNotEqual(self.call(event(12, 23), NOW)['status'], 'sent')
        times = iter((NOW + 2, NOW + 302))
        self.assertEqual(self.call(event(12, 23), clock=lambda: next(times))['error'], 'invalid_snapshot')
        self.assertEqual(len(self.sent), 1)

    def test_lost_receipt_is_audited_before_send_and_replay_never_retries(self):
        self.call()
        attempts = []
        def lose(raw):
            attempts.append(raw)
            pending = json.loads(Path(self.config['audit_path']).read_bytes())
            self.assertEqual(pending['status'], 'attempted')
            self.assertEqual(pending['snapshot_sha256'], digest(raw))
            raise TimeoutError('PRIVATE-vendor-error')
        result = self.call(event(11, 22), NOW + 1, send=lose)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(result['attempted_at'], NOW + 1)
        self.assertEqual(result['snapshot_sha256'], digest(attempts[0]))
        self.call(event(11, 22), NOW + 2, send=lose)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(json.loads(Path(self.config['audit_path']).read_bytes())['status'], 'uncertain')
        self.assertNotIn(b'PRIVATE-vendor-error', self.persisted())

    def test_arbitrary_vendor_exception_text_never_reaches_audit(self):
        def failure():
            raise self.module.ObserverError('PRIVATE-vendor-injected-text')
        result = self.call(auth_status=failure)
        self.assertEqual(result['error'], 'observer_failed')
        self.assertNotIn(b'PRIVATE-vendor-injected-text', self.persisted())

    def test_interrupted_send_keeps_pending_digest_and_replay_never_erases_it(self):
        self.call()
        def interrupted(raw):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.call(event(11, 22), NOW + 1, send=interrupted)
        pending = Path(self.config['audit_path']).read_bytes()
        self.assertEqual(json.loads(pending)['status'], 'attempted')
        self.call(event(11, 22), NOW + 2)
        self.assertEqual(Path(self.config['audit_path']).read_bytes(), pending)
        self.assertEqual(self.sent, [])

    def test_auth_org_may_be_absent_and_same_account_hashes_null(self):
        without_org = {k: v for k, v in IDENTITY.items() if k != 'orgId'}
        expected = {**IDENTITY, 'orgId': None}
        self.config['expected_account_sha256'] = identity_digest(expected)
        self.assertEqual(self.call(auth_status=lambda: canonical(without_org))['status'], 'baseline')

    def test_config_hash_drift_stops_cli_before_auth(self):
        filename = self.root / 'config.json'
        filename.write_bytes(canonical(self.config))
        filename.chmod(0o600)
        with patch.object(self.module, '_bounded_process', side_effect=AssertionError('native forbidden')):
            stderr = io.StringIO()
            result = self.module.main(['--config', str(filename), '--config-sha256', '0' * 64],
                stdin=io.BytesIO(event()), stdout=io.StringIO(), stderr=stderr)
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), 'claude_passive: observer_failed\n')
        self.assertEqual(list(self.state.iterdir()), [])

    def test_cli_requires_config_hash_before_any_auth_or_state(self):
        filename = self.root / 'config.json'
        filename.write_bytes(canonical(self.config))
        filename.chmod(0o600)
        with patch.object(self.module, '_bounded_process', side_effect=AssertionError('native forbidden')) as process:
            stderr = io.StringIO()
            self.module.main(['--config', str(filename)], stdin=io.BytesIO(event()),
                             stdout=io.StringIO(), stderr=stderr)
            process.assert_not_called()
        self.assertEqual(stderr.getvalue(), 'claude_passive: observer_failed\n')
        self.assertEqual(list(self.state.iterdir()), [])

    def test_subprocess_overflow_and_timeout_are_bounded_without_native_calls(self):
        module = self.module
        class Process:
            stdin = None
            def __init__(self, raw, timeout=False):
                self.stdout = io.BytesIO(raw)
                self.timeout = timeout
                self.returncode = None
                self.killed = False
                self.waits = []
            def wait(self, timeout):
                self.waits.append(timeout)
                if self.timeout and not self.killed:
                    raise module.subprocess.TimeoutExpired(['synthetic'], timeout)
                self.returncode = -1 if self.killed else 0
                return self.returncode
            def kill(self):
                self.killed = True
            def poll(self):
                return self.returncode
        for process in (Process(b'x' * 65537), Process(b'', timeout=True)):
            with patch.object(module.subprocess, 'Popen', return_value=process), self.assertRaises(ValueError):
                module._bounded_process(['synthetic-never-executed'], maximum=65536, timeout=8)
            self.assertTrue(process.killed)
            self.assertEqual(process.waits[0], 8)
            self.assertTrue(process.stdout.closed)

    def test_receipt_is_exact_and_cannot_accept_extra_or_bool_timestamp(self):
        for mutate in (lambda a: {**a, 'extra': 'PRIVATE'},
                       lambda a: {**a, 'fetched_at': True},
                       lambda a: {**a, 'snapshot_sha256': '0' * 64},
                       lambda a: {**a, 'ok': 1}):
            with self.subTest(mutate=mutate):
                # Separate sessions establish new baselines without resetting directories.
                session = 'receipt-' + str(len(self.auth_calls))
                at = NOW + 10 + len(self.auth_calls)
                self.call(event(session_id=session), at)
                result = self.call(event(11, 22, session_id=session), at + 1,
                                   send=lambda raw: mutate(self.send(raw)))
                self.assertEqual(result['status'], 'uncertain')
                self.assertEqual(result['error'], 'invalid_receipt')

    def test_full_transaction_is_locked_before_auth_and_until_send_finishes(self):
        self.call()
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def blocked_send(raw):
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture release timeout')
            return self.send(raw)
        worker = threading.Thread(target=lambda: outcomes.append(
            self.call(event(11, 22), NOW + 1, send=blocked_send)))
        worker.start()
        self.assertTrue(entered.wait(5))
        before = len(self.auth_calls)
        try:
            busy = self.call(event(12, 23), NOW + 2)
            self.assertEqual(busy['error'], 'busy')
            self.assertEqual(len(self.auth_calls), before)
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0]['status'], 'sent')

    def test_bad_config_paths_pins_or_template_identity_block_before_auth(self):
        cases = [lambda c: c.update(expected_account_sha256='0' * 64),
                 lambda c: c.update(relay_lock_directory=c['state_directory']),
                 lambda c: c.update(auth_argv=[str(self.executable), 'login']),
                 lambda c: c.update(output=str(self.numeric / 'codex.json')),
                 lambda c: c['source_pins'].update({str(TARGET): '1' * 64}),
                 lambda c: c['auth_file_pins'].update({str(self.executable): '1' * 64}),
                 lambda c: c.update(unknown='PRIVATE')]
        original = self.config
        for change in cases:
            self.config = copy.deepcopy(original)
            change(self.config)
            self.assertEqual(self.call()['status'], 'error')
        self.config = original
        self.assertEqual(self.auth_calls, [])
        self.assertEqual(list(self.state.iterdir()), [])

    def test_checked_loader_executes_sources_ignoring_poisoned_cached_bytecode(self):
        copied_base = self.root / 'checked-sources'
        copied_paths = []
        for source in SOURCE_PATHS:
            destination = copied_base / source.relative_to(BASE)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied_paths.append(destination)
        copied_target = copied_base / TARGET.relative_to(BASE)
        # A legacy .pyc whose source would raise if an import loader trusted it.
        import importlib._bootstrap_external as bootstrap
        poison_source = b'raise AssertionError("PRIVATE-poisoned-bytecode")'
        copied_adapter = copied_base / 'claude_statusline_quota_020.py'
        cache = Path(importlib.util.cache_from_source(str(copied_adapter)))
        cache.parent.mkdir()
        stat = copied_adapter.stat()
        cache.write_bytes(bootstrap._code_to_timestamp_pyc(
            compile(poison_source, str(copied_adapter), 'exec'), int(stat.st_mtime), stat.st_size))
        spec = importlib.util.spec_from_file_location('copied_aw020', copied_target)
        module = importlib.util.module_from_spec(spec)
        exec(compile(copied_target.read_bytes(), str(copied_target), 'exec'), module.__dict__)
        self.config['source_pins'] = {str(x): digest(x.read_bytes()) for x in copied_paths}
        result = module.run_callback(event(), self.config, auth_status=self.auth,
                                     send=self.send, clock=lambda: NOW)
        self.assertEqual(result['status'], 'baseline')

    def test_regular_file_and_reparse_guards_apply_to_checked_files(self):
        original = self.module.os.lstat
        def reparse(path):
            value = original(path)
            if os.path.normcase(str(path)) == os.path.normcase(str(self.executable)):
                class Fake:
                    st_file_attributes = 1024
                    def __getattr__(self, name):
                        return getattr(value, name)
                return Fake()
            return value
        with patch.object(self.module.os, 'lstat', reparse):
            self.assertEqual(self.call()['status'], 'error')
        self.assertEqual(self.auth_calls, [])

    def test_cli_keeps_stdout_empty_and_returns_zero_on_observer_error(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        result = self.module.main(['--config', str(self.root / 'missing-private-config')],
            stdin=io.BytesIO(b'PRIVATE-raw'), stdout=stdout, stderr=stderr)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'claude_passive: observer_failed\n')

    def test_cli_uses_exact_configured_commands_without_native_test_processes(self):
        config_path = self.root / 'config.json'
        config_path.write_bytes(canonical(self.config))
        config_path.chmod(0o600)
        commands = []
        def process(argv, **kwargs):
            commands.append((argv, kwargs))
            if argv == self.config['auth_argv']:
                return canonical(IDENTITY)
            return canonical(self.send(kwargs['input_bytes']))
        def invoke(raw, at):
            with patch.object(self.module, '_bounded_process', process), \
                 patch.object(self.module.time, 'time', return_value=at):
                return self.module.main(['--config', str(config_path), '--config-sha256',
                                         digest(config_path.read_bytes())],
                    stdin=io.BytesIO(raw), stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(invoke(event(), NOW), 0)
        self.assertEqual(invoke(event(11, 22), NOW + 1), 0)
        self.assertEqual([x[0] for x in commands],
                         [self.config['auth_argv'], self.config['auth_argv'], self.config['receiver_argv']])
        self.assertEqual(commands[0][1]['timeout'], 8)
        self.assertEqual(commands[0][1]['maximum'], 65536)


if __name__ == '__main__':
    unittest.main()
