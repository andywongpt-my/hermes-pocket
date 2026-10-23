"""Synthetic qualification tests. No native executable is ever launched."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_aw_qualification', HERE / 'native_aw_qualification.py')
q = None
if spec and spec.origin and Path(spec.origin).is_file():
    q = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = q
    spec.loader.exec_module(q)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(q, 'qualification implementation is missing')
        self.now = 1792300000
        self.exe_hash = q.PINNED_EXECUTABLE_SHA256
        self.receipt = {
            'version': 1, 'capture_kind': 'root_cim_current_tool_graph_v1',
            'linkage_policy': 'shared_managed_app_parent_v1',
            'captured_at': self.now, 'executable': q.PINNED_EXECUTABLE,
            'executable_sha256': self.exe_hash, 'current_tool_pid': 100,
            'current_context': True, 'no_account_profile_override': True,
            'inherited_default_codex_home': True,
            'executor_forced_login_method': 'chatgpt',
            'managed_app': {'pid': 400, 'executable': r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1013.4894.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe', 'created_at': self.now - 200},
            'app_server': {'pid': 300, 'parent_pid': 400, 'executable': q.PINNED_EXECUTABLE, 'created_at': self.now - 100, 'command': 'app-server', 'config_keys': ['features.code_mode_host', 'features.prefer_mxc'], 'config_boolean_features': {}, 'account_profile_override_absent': True},
            'executor': {'pid': 200, 'parent_pid': 400, 'executable': q.PINNED_EXECUTABLE, 'created_at': self.now - 100, 'command': 'exec-server', 'config_keys': ['features.prefer_mxc', 'forced_login_method'], 'account_profile_override_absent': True},
            'ancestry': [
                {'pid': 100, 'parent_pid': 200, 'executable': r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', 'created_at': self.now - 2, 'account_profile_override_absent': True},
                {'pid': 200, 'parent_pid': 400, 'executable': q.PINNED_EXECUTABLE, 'created_at': self.now - 100, 'account_profile_override_absent': True},
                {'pid': 400, 'parent_pid': 500, 'executable': r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1013.4894.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe', 'created_at': self.now - 200, 'account_profile_override_absent': True},
            ],
        }
        self.identity = {'account': {'type': 'chatgpt', 'email': 'Synthetic.Exact+Case@example.invalid'}, 'requiresOpenaiAuth': True}
        self.events = []
        self.clients = []
        self.accounts = []
        self.quota = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 0, 'windowDurationMins': 10080, 'resetsAt': self.now + 200}, 'secondary': {'usedPercent': None, 'windowDurationMins': 300, 'resetsAt': None}}}}
        self.cleanup_failure = None
        self.returncode = 0
        outer = self

        def enter(client):
            outer.events.append(('enter', client.expected_binding))
            outer.clients.append(client)
            client.child = types.SimpleNamespace(returncode=None, stdin=types.SimpleNamespace(closed=False), stdout=types.SimpleNamespace(closed=False))
            client._rpc('initialize', {'clientInfo': {'name': 'quota_reader', 'version': '1'}})
            return client

        def rpc(client, method, params=None, **kwargs):
            outer.events.append((method, copy.deepcopy(params)))
            if method == 'initialize':
                return {}
            if method == 'account/read':
                return copy.deepcopy(outer.accounts.pop(0) if outer.accounts else outer.identity)
            if method == 'account/rateLimits/read':
                return copy.deepcopy(outer.quota)
            raise AssertionError('unexpected method')

        def close(client):
            if client.closed:
                return False
            client.closed = True
            outer.events.append(('close', client.expected_binding))
            client.stop.set()
            if client.child:
                client.child.returncode = outer.returncode
                client.child.stdin.closed = True
                client.child.stdout.closed = True
            return outer.cleanup_failure == len(outer.clients)

        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(q.official.CodexQuotaClient, '__enter__', enter))
        self.stack.enter_context(patch.object(q.official.CodexQuotaClient, '_rpc', rpc))
        self.stack.enter_context(patch.object(q.official.CodexQuotaClient, 'close', close))
        self.stack.enter_context(patch.object(q.os, 'getppid', return_value=100))
        self.stack.enter_context(patch.object(q.time, 'time', return_value=self.now))
        real_hash = q._file_sha256
        self.stack.enter_context(patch.object(q, '_file_sha256', side_effect=lambda path: self.exe_hash if str(path) == q.PINNED_EXECUTABLE else real_hash(path)))

    def run_qualification(self, receipt=None, **kwargs):
        return q.qualify_current_account(receipt=self.receipt if receipt is None else receipt, executable=q.PINNED_EXECUTABLE, executable_sha256=self.exe_hash, already_approved_current_account=True, **kwargs)

    def assert_rejected(self, receipt):
        with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
            self.run_qualification(receipt)
        self.assertEqual(self.events, [])

    def test_invalid_receipts_prevent_every_launch(self):
        mutations = [
            ('captured_at', self.now - 301), ('captured_at', self.now + 6),
            ('captured_at', True), ('executable_sha256', 'b' * 64),
            ('executable', r'C:\other\codex.exe'), ('current_tool_pid', 101),
            ('current_context', False), ('no_account_profile_override', False),
            ('linkage_policy', 'direct_ancestor_assumption'),
            ('inherited_default_codex_home', False), ('executor_forced_login_method', 'api'),
            ('capture_kind', 'untrusted'),
            ('version', True), ('ancestry', []),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                receipt = copy.deepcopy(self.receipt)
                receipt[key] = value
                self.assert_rejected(receipt)

    def test_missing_receipt_fields_and_extra_payload_rejected(self):
        for key in self.receipt:
            with self.subTest(key=key):
                receipt = copy.deepcopy(self.receipt)
                del receipt[key]
                self.assert_rejected(receipt)
        receipt = copy.deepcopy(self.receipt)
        receipt['email'] = 'must-not-be-read@example.invalid'
        self.assert_rejected(receipt)

    def test_ancestry_must_be_contiguous_unique_current_and_unambiguous(self):
        for key, value in [('parent_pid', 999), ('pid', 400), ('account_profile_override_absent', False), ('created_at', self.now + 1)]:
            with self.subTest(key=key):
                receipt = copy.deepcopy(self.receipt)
                receipt['ancestry'][0][key] = value
                self.assert_rejected(receipt)
        receipt = copy.deepcopy(self.receipt)
        receipt['ancestry'][-1]['executable'] = r'C:\other\codex.exe'
        self.assert_rejected(receipt)

    def test_shared_managed_parent_is_verified_without_ancestor_invention(self):
        result = self.run_qualification()
        self.assertTrue(result['proof']['approved_app_context_link_verified'])
        self.assertNotIn(self.receipt['app_server']['pid'], [row['pid'] for row in self.receipt['ancestry']])

    def test_shared_graph_rejects_context_and_config_ambiguities_before_launch(self):
        changes = [
            ('app_server', 'parent_pid', 999), ('executor', 'parent_pid', 999),
            ('app_server', 'executable', r'C:\other\codex.exe'),
            ('app_server', 'command', 'app-server --profile other'),
            ('app_server', 'config_keys', ['profile']),
            ('executor', 'config_keys', ['forced_login_method', 'auth_file']),
            ('managed_app', 'executable', r'C:\other\ChatGPT.exe'),
            ('managed_app', 'created_at', self.now + 1),
            ('executor', 'pid', 300), ('app_server', 'pid', 200),
        ]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                receipt = copy.deepcopy(self.receipt)
                receipt[section][key] = value
                self.assert_rejected(receipt)

    def test_receipt_age_between_sixty_and_three_hundred_seconds_is_supported(self):
        self.receipt['captured_at'] = self.now - 299
        for row in self.receipt['ancestry']:
            row['created_at'] -= 300
        for key in ('app_server', 'executor', 'managed_app'):
            self.receipt[key]['created_at'] -= 300
        self.run_qualification()

    def test_executable_hash_is_recomputed_before_launch(self):
        with patch.object(q, '_file_sha256', return_value='b' * 64):
            self.assert_rejected(self.receipt)

    def test_missing_explicit_approval_prevents_launch(self):
        with self.assertRaises(q.QualificationError):
            q.qualify_current_account(receipt=self.receipt, executable=q.PINNED_EXECUTABLE, executable_sha256=self.exe_hash, already_approved_current_account=False)
        self.assertEqual(self.events, [])

    def test_nonpinned_executable_and_bad_deadlines_prevent_launch(self):
        for changes in ({'executable': r'C:\other\codex.exe'}, {'rpc_timeout': 0}, {'rpc_timeout': 31}, {'total_timeout': 0}, {'total_timeout': 121}):
            args = dict(receipt=self.receipt, executable=q.PINNED_EXECUTABLE, executable_sha256=self.exe_hash, already_approved_current_account=True)
            args.update(changes)
            with self.assertRaises(q.QualificationError):
                q.qualify_current_account(**args)
        self.assertEqual(self.events, [])

    def test_bootstrap_cannot_request_quota_or_read_with_placeholder(self):
        metadata = q._MetadataOnlyClient(q.PINNED_EXECUTABLE, rpc_timeout=1)
        for action in [metadata.read, lambda: metadata._rpc('account/rateLimits/read'), lambda: metadata._rpc('account/read', {'refreshToken': True}), lambda: metadata._rpc('model/list')]:
            with self.assertRaises(q.QualificationError):
                action()
        self.assertEqual(self.events, [])

    def test_success_uses_actual_canonical_binding_and_closes_before_return(self):
        result = self.run_qualification()
        canonical = json.dumps({'type': 'chatgpt', 'email': self.identity['account']['email']}, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
        binding = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(result['proof']['account_binding_sha256'], binding)
        self.assertEqual(self.clients[1].expected_binding, binding)
        self.assertIs(type(self.clients[1]), q.official.CodexQuotaClient)
        self.assertEqual([e[0] for e in self.events], ['enter', 'initialize', 'account/read', 'close', 'enter', 'initialize', 'account/read', 'account/rateLimits/read', 'account/read', 'close'])
        self.assertTrue(all(client.closed for client in self.clients))
        self.assertEqual(result['snapshot']['fetched_at'], self.now)
        self.assertEqual(result['snapshot']['metrics'][0]['used_percent'], 0)
        self.assertEqual(result['snapshot']['metrics'][0]['window_minutes'], 10080)
        self.assertIsNone(result['snapshot']['metrics'][1]['used_percent'])
        self.assertEqual(result['snapshot']['metrics'][1]['window_minutes'], 300)
        self.assertEqual(result['proof']['profile'], 'codex_aw_numeric_v1')
        self.assertEqual(result['proof']['producer_exit_code'], 0)
        for key in ('all_children_reaped', 'all_pipes_closed', 'approved_app_context_link_verified', 'account_checks_before_after', 'refresh_token_false'):
            self.assertIs(result['proof'][key], True)
        for method, params in self.events:
            if method == 'account/read':
                self.assertEqual(params, {'refreshToken': False})
        encoded = json.dumps(result)
        self.assertNotIn(self.identity['account']['email'], encoded)
        self.assertNotIn('requiresOpenaiAuth', encoded)
        self.assertEqual(set(result), {'snapshot', 'proof'})
        self.assertEqual(set(result['proof']['source_files']), {'native_aw_qualification.py', 'official_codex_quota.py'})

    def test_invalid_account_metadata_has_generic_error_and_no_quota(self):
        for value in ({'account': None}, {'account': {'type': 'apiKey'}}, {'account': {'type': 'chatgpt', 'email': ' bad@example.invalid'}}, {'account': {'type': 'chatgpt', 'email': 'bad\nidentity'}}, {'account': {'type': 'chatgpt', 'email': ''}}, {'account': self.identity['account'], 'requiresOpenaiAuth': False}):
            with self.subTest(value=value):
                self.events.clear()
                self.clients.clear()
                self.accounts = [value]
                with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
                    self.run_qualification()
                self.assertNotIn('account/rateLimits/read', [e[0] for e in self.events])
                self.assertTrue(all(client.closed for client in self.clients))

    def test_account_change_before_read_suppresses_quota(self):
        changed = copy.deepcopy(self.identity)
        changed['account']['email'] = 'other@example.invalid'
        self.accounts = [self.identity, changed]
        with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
            self.run_qualification()
        self.assertNotIn('account/rateLimits/read', [e[0] for e in self.events])
        self.assertTrue(all(client.closed for client in self.clients))

    def test_account_change_after_read_suppresses_result(self):
        changed = copy.deepcopy(self.identity)
        changed['account']['email'] = 'other@example.invalid'
        self.accounts = [self.identity, self.identity, changed]
        with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
            self.run_qualification()
        self.assertTrue(all(client.closed for client in self.clients))

    def test_cleanup_failure_never_emits_proof(self):
        for index in (1, 2):
            with self.subTest(index=index):
                self.clients.clear()
                self.events.clear()
                self.cleanup_failure = index
                with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
                    self.run_qualification()
                self.assertEqual(len(self.clients), index)

    def test_nonzero_exit_and_live_threads_fail_cleanup(self):
        self.returncode = 1
        with self.assertRaises(q.QualificationError):
            self.run_qualification()
        self.assertEqual(len(self.clients), 1)

    def test_unclosed_pipes_and_unreaped_threads_suppress_proof(self):
        original_close = q.official.CodexQuotaClient.close
        for mode in ('pipe', 'thread'):
            self.clients.clear()
            self.events.clear()
            def incomplete_close(client):
                result = original_close(client)
                if mode == 'pipe':
                    client.child.stdout.closed = False
                else:
                    client.reader = types.SimpleNamespace(is_alive=lambda: True)
                return result
            with self.subTest(mode=mode), patch.object(q.official.CodexQuotaClient, 'close', incomplete_close):
                with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
                    self.run_qualification()
                self.assertEqual(len(self.clients), 1)

    def test_total_deadline_exhaustion_prevents_first_launch(self):
        with patch.object(q.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
                self.run_qualification()
        self.assertEqual(self.events, [])

    def test_source_pin_mismatch_prevents_launch(self):
        real_hash = q._file_sha256
        with patch.object(q, '_file_sha256', side_effect=lambda path: 'b' * 64 if Path(path) == q._CLIENT_PATH else real_hash(path)):
            self.assert_rejected(self.receipt)

    def test_bootstrap_does_not_accept_unpinned_executable_digest(self):
        with patch.object(q, '_file_sha256', side_effect=lambda path: 'b' * 64 if str(path) == q.PINNED_EXECUTABLE else hashlib.sha256(Path(path).read_bytes()).hexdigest()):
            receipt = copy.deepcopy(self.receipt)
            receipt['executable_sha256'] = 'b' * 64
            with self.assertRaisesRegex(q.QualificationError, '^qualification_failed$'):
                q.qualify_current_account(receipt=receipt, executable=q.PINNED_EXECUTABLE, executable_sha256='b' * 64, already_approved_current_account=True)
        self.assertEqual(self.events, [])

    def test_unknown_only_quota_suppresses_output(self):
        self.quota['rateLimitsByLimitId']['codex']['primary']['usedPercent'] = None
        with self.assertRaises(q.QualificationError):
            self.run_qualification()

    def test_binding_compatibility_preserves_case_and_unicode(self):
        for email in ['A@example.invalid', 'a@example.invalid', 'synthetic+\u00e9@example.invalid']:
            with self.subTest(email=email):
                identity = {'account': {'type': 'chatgpt', 'email': email}}
                binding = q._account_binding(identity)
                client = q.official.CodexQuotaClient(q.PINNED_EXECUTABLE, expected_binding=binding)
                with patch.object(client, '_rpc', return_value=identity):
                    client._verify_account()
        self.assertNotEqual(q._account_binding({'account': {'type': 'chatgpt', 'email': 'A@example.invalid'}}), q._account_binding({'account': {'type': 'chatgpt', 'email': 'a@example.invalid'}}))


    def test_controlled_stdio_bootstrap_then_bound_read_two_cleanups(self):
        # Exercise the real reviewed startup/RPC/read/cleanup engine twice.
        self.receipt['app_server']['config_keys'] += ['bundled.mcp_servers.codex_app.enabled', 'review.enabled']
        self.receipt['app_server']['config_boolean_features'] = {'bundled.mcp_servers.codex_app.enabled': True, 'review.enabled': False}
        # Only process creation is redirected to synthetic Python stdio servers.
        self.stack.close()
        import subprocess
        real_popen = subprocess.Popen
        real_rpc = q.official.CodexQuotaClient._rpc
        real_close = q.official.CodexQuotaClient.close
        children, methods, cleanups = [], [], []
        script = """import json, sys
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialized':
        continue
    if method == 'initialize':
        result = {}
    elif method == 'account/read' and message.get('params') == {'refreshToken': False}:
        result = {'account': {'type': 'chatgpt', 'email': 'stdio-private@example.invalid'}, 'requiresOpenaiAuth': True, 'token': 'synthetic-private'}
    elif method == 'account/rateLimits/read':
        result = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 0, 'windowDurationMins': 10080, 'resetsAt': None}, 'secondary': {'usedPercent': None, 'windowDurationMins': 300}}}, 'token': 'synthetic-private'}
    else:
        raise SystemExit(9)
    print(json.dumps({'id': message['id'], 'result': result}), flush=True)
"""
        def spawn(command, **kwargs):
            self.assertEqual(command, [q.PINNED_EXECUTABLE, 'app-server'])
            self.assertFalse(kwargs['shell'])
            child = real_popen([sys.executable, '-B', '-u', '-c', script], **kwargs)
            children.append(child)
            return child
        def record_rpc(client, method, params=None, **kwargs):
            methods.append((method, copy.deepcopy(params)))
            return real_rpc(client, method, params, **kwargs)
        def record_close(client):
            failure = real_close(client)
            cleanups.append((client, failure))
            return failure
        def cleanup_fixture_children():
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
        self.addCleanup(cleanup_fixture_children)
        real_hash = q._file_sha256
        with patch.object(q.official.subprocess, 'Popen', side_effect=spawn), \
                patch.object(q.official.CodexQuotaClient, '_rpc', record_rpc), \
                patch.object(q.official.CodexQuotaClient, 'close', record_close), \
                patch.object(q.os, 'getppid', return_value=100), \
                patch.object(q.time, 'time', return_value=self.now), \
                patch.object(q, '_file_sha256', side_effect=lambda path: self.exe_hash if str(path) == q.PINNED_EXECUTABLE else real_hash(path)):
            result = self.run_qualification()
        self.assertEqual(len(children), 2)
        self.assertEqual([method for method, _ in methods], ['initialize', 'account/read', 'initialize', 'account/read', 'account/rateLimits/read', 'account/read'])
        self.assertEqual(len(cleanups), 2)
        self.assertTrue(all(not failed for _, failed in cleanups))
        self.assertTrue(all(child.poll() == 0 and child.stdin.closed and child.stdout.closed for child in children))
        self.assertTrue(all(not client.reader.is_alive() and not client.writer.is_alive() for client, _ in cleanups))
        self.assertEqual(result['snapshot']['fetched_at'], self.now)
        self.assertEqual(result['snapshot']['metrics'][0]['used_percent'], 0)
        self.assertEqual(result['snapshot']['metrics'][0]['window_minutes'], 10080)
        self.assertIsNone(result['snapshot']['metrics'][1]['used_percent'])
        self.assertEqual(result['snapshot']['metrics'][1]['window_minutes'], 300)
        self.assertIs(type(cleanups[1][0]), q.official.CodexQuotaClient)
        expected = hashlib.sha256(json.dumps({'type': 'chatgpt', 'email': 'stdio-private@example.invalid'}, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()
        self.assertEqual(cleanups[1][0].expected_binding, expected)
        self.assertEqual(result['proof']['account_binding_sha256'], expected)
        for method, params in methods:
            if method == 'account/read':
                self.assertEqual(params, {'refreshToken': False})
        self.assertNotIn('stdio-private', json.dumps(result))
        self.assertNotIn('synthetic-private', json.dumps(result))


    def test_unknown_managed_package_is_rejected_before_launch(self):
        receipt = copy.deepcopy(self.receipt)
        replacement = receipt['managed_app']['executable'].replace('26.1013.4894.0', '99.9999.9999.0')
        receipt['managed_app']['executable'] = replacement
        receipt['ancestry'][-1]['executable'] = replacement
        self.assert_rejected(receipt)

    def test_cli_import_pin_failure_has_only_fixed_error(self):
        import builtins
        import runpy
        real_open = builtins.open
        def changed_source(path, *args, **kwargs):
            if Path(path) == q._CLIENT_PATH:
                return io.BytesIO(b'synthetic unreviewed source')
            return real_open(path, *args, **kwargs)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(builtins, 'open', changed_source), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                runpy.run_path(str(HERE / 'native_aw_qualification.py'), run_name='__main__')
            except SystemExit as error:
                self.assertEqual(error.code, 1)
            except Exception:
                self.fail('CLI import failure escaped instead of fixed stderr')
            else:
                self.fail('changed source pin was accepted')
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), 'native_aw_qualification: qualification_failed\n')


    def test_verified_source_bytes_execute_even_with_divergent_valid_pyc(self):
        import os
        import importlib._bootstrap_external
        import tempfile
        verified = b"CACHE_PAYLOAD = 'verified-source'\n"
        divergent = b"CACHE_PAYLOAD = 'divergent-cache'\n"
        self.assertEqual(len(verified), len(divergent))
        with tempfile.TemporaryDirectory(prefix='native-aw-loader-', dir=r'C:\Users\example\AppData\Local\Temp') as directory:
            source = Path(directory) / 'synthetic_reviewed.py'
            source.write_bytes(divergent)
            original_stat = source.stat()
            cached = Path(importlib.util.cache_from_source(str(source)))
            cached.parent.mkdir()
            cached.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(compile(divergent, str(source), 'exec'), int(original_stat.st_mtime), len(divergent)))
            source.write_bytes(verified)
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            self.assertTrue(Path(importlib.util.cache_from_source(str(source))).is_file())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), hashlib.sha256(verified).hexdigest())
            with patch.object(q, '_CLIENT_PATH', source), patch.object(q, 'REVIEWED_CLIENT_SHA256', hashlib.sha256(verified).hexdigest()):
                loaded = q._load_reviewed_client()
            self.assertEqual(loaded.CACHE_PAYLOAD, 'verified-source')
            self.assertEqual(loaded.__file__, str(source))
            self.assertEqual(source.read_bytes(), verified)


    def test_app_server_literal_boolean_feature_keys_are_accepted(self):
        keys = ['bundled.mcp_servers.codex_app.enabled', 'review.enabled']
        for first, second in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(first=first, second=second):
                receipt = copy.deepcopy(self.receipt)
                receipt['app_server']['config_keys'] += keys
                receipt['app_server']['config_boolean_features'] = dict(zip(keys, (first, second)))
                try:
                    result = self.run_qualification(receipt)
                except q.QualificationError:
                    self.fail('literal boolean feature receipt rejected')
                self.assertTrue(result['proof']['approved_app_context_link_verified'])

    def test_single_literal_boolean_feature_requires_exact_matching_map(self):
        for key in ('bundled.mcp_servers.codex_app.enabled', 'review.enabled'):
            with self.subTest(key=key):
                receipt = copy.deepcopy(self.receipt)
                receipt['app_server']['config_keys'].append(key)
                receipt['app_server']['config_boolean_features'] = {key: False}
                try:
                    self.run_qualification(receipt)
                except q.QualificationError:
                    self.fail('single literal boolean feature receipt rejected')

    def test_boolean_feature_map_requires_exact_json_booleans(self):
        key = 'bundled.mcp_servers.codex_app.enabled'
        for value in ('true', 'false', '"true"', 1, 0, 1.0, None, [], {}):
            with self.subTest(value=value):
                receipt = copy.deepcopy(self.receipt)
                receipt['app_server']['config_keys'].append(key)
                receipt['app_server']['config_boolean_features'] = {key: value}
                self.assert_rejected(receipt)

    def test_boolean_feature_map_missing_unknown_and_ambiguous_shapes_rejected(self):
        for mode in ('absent', 'missing_present_key', 'unlisted_key', 'unknown_key', 'non_map', 'unknown_config'):
            with self.subTest(mode=mode):
                receipt = copy.deepcopy(self.receipt)
                server = receipt['app_server']
                if mode == 'absent':
                    del server['config_boolean_features']
                elif mode == 'missing_present_key':
                    server['config_keys'].append('review.enabled')
                elif mode == 'unlisted_key':
                    server['config_boolean_features'] = {'review.enabled': True}
                elif mode == 'unknown_key':
                    server['config_boolean_features'] = {'auth.enabled': True}
                elif mode == 'non_map':
                    server['config_boolean_features'] = []
                else:
                    server['config_keys'].append('auth.enabled')
                    server['config_boolean_features'] = {'auth.enabled': True}
                self.assert_rejected(receipt)

    def test_cli_invalid_arguments_emit_only_fixed_error(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = q.main(['--codex', 'secret-identity@example.invalid'])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), 'native_aw_qualification: qualification_failed\n')


if __name__ == '__main__':
    unittest.main()
