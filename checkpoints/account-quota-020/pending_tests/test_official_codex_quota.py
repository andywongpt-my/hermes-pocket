"""Offline producer tests. All native-client traffic is a controlled child."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    producer = importlib.import_module('official_codex_quota')
except ModuleNotFoundError as error:
    if error.name != 'official_codex_quota':
        raise
    producer = None


ACCOUNT = {'account': {'type': 'chatgpt', 'email': 'fixture@example.invalid',
                       'planType': 'fixture-plan'}, 'requiresOpenaiAuth': True}
QUOTA = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 71,
    'windowDurationMins': 10080, 'resetsAt': 1792649595}, 'secondary': None}},
    'rateLimits': {'primary': {'usedPercent': 99}},
    'email': 'fixture-private', 'credits': {'balance': 'fixture-private'}}


CHILD = r'''
import json
from pathlib import Path
import sys
import time

config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
record = Path(sys.argv[2])
account_reads = 0
quota_reads = 0

def note(data):
    with record.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(data) + '\n')

def send(data):
    sys.stdout.buffer.write(json.dumps(data, allow_nan=True).encode('utf-8') + b'\n')
    sys.stdout.buffer.flush()

note({'started_at': time.time()})
for line in sys.stdin:
    msg = json.loads(line)
    note({'received': msg})
    method = msg.get('method')
    if method == 'initialized' or 'error' in msg:
        continue
    if method == 'initialize':
        time.sleep(config.get('initialize_delay', 0))
        send({'id': msg['id'], 'result': {'userAgent': 'fixture-private'}})
        continue
    if method == 'account/read':
        accounts = config['accounts']
        account = accounts[min(account_reads, len(accounts) - 1)]
        account_reads += 1
        send({'id': msg['id'], 'result': account})
        continue
    if method != 'account/rateLimits/read':
        raise SystemExit(8)
    quota_reads += 1
    mode = config.get('mode', 'success')
    if mode == 'timeout':
        time.sleep(20)
    if mode == 'exit':
        raise SystemExit(7)
    if mode == 'raw':
        sys.stdout.buffer.write(config['raw'].encode('utf-8'))
        sys.stdout.buffer.flush()
        time.sleep(20)
    if mode == 'rpc_error':
        send({'id': msg['id'], 'error': {'code': -32000,
            'message': 'fixture-private error C:\\fixture-secret',
            'data': {'token': 'fixture-private'}}})
        continue
    if mode == 'request_flood':
        for i in range(256):
            send({'id': 'fixture-request-' + str(i), 'method': 'auth/fixture',
                  'params': {'token': 'fixture-private'}})
        time.sleep(20)
    if mode == 'events':
        for i in range(200):
            send({'method': 'fixture/notification', 'params': {'private': i}})
        send({'id': 'fixture-server-request', 'method': 'account/chatgptAuthTokens/refresh',
              'params': {'private': 'fixture-private'}})
        refusal = json.loads(sys.stdin.readline())
        note({'refusal': refusal})
    if mode == 'wrong_id':
        send({'id': msg['id'] + 1, 'result': config['quotas'][0]})
        continue
    if mode == 'bad_envelope':
        send({'id': msg['id'], 'result': {}, 'error': {'code': 1}})
        continue
    time.sleep(config.get('quota_delay', 0))
    quotas = config['quotas']
    quota = quotas[min(quota_reads - 1, len(quotas) - 1)]
    note({'quota_response_at': time.time()})
    send({'id': msg['id'], 'result': quota})
note({'stdin_eof': True})
if config.get('hang_after_eof'):
    time.sleep(20)
raise SystemExit(config.get('exit_after_eof', 0))
'''


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(producer, 'bounded official producer is missing')
        self.temp_parent = Path(tempfile.gettempdir()).resolve()
        self.root = Path(tempfile.mkdtemp(prefix='codex-producer-test-',
                                         dir=self.temp_parent)).resolve()
        self.addCleanup(self.clean_fixture)
        self.child = self.root / 'controlled_stdio.py'
        self.child.write_text(CHILD, encoding='utf-8')
        self.config_path = self.root / 'scenario.json'
        self.record = self.root / 'received.jsonl'
        self.executable = self.root / 'official-codex-fixture.exe'
        self.children = []
        self.commands = []
        self.original_popen = subprocess.Popen
        self.config = {'accounts': [deepcopy(ACCOUNT)], 'quotas': [deepcopy(QUOTA)]}

    def clean_fixture(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
        target = self.root.resolve()
        if (target.parent != self.temp_parent or target != self.root or
                not target.name.startswith('codex-producer-test-')):
            raise AssertionError('unsafe producer fixture cleanup')
        shutil.rmtree(target)

    def spawn(self, command, **kwargs):
        self.commands.append((command, kwargs))
        self.assertEqual(command, [str(self.executable), 'app-server', '--stdio'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        child = self.original_popen([sys.executable, '-B', str(self.child),
            str(self.config_path), str(self.record)], **kwargs)
        self.children.append(child)
        return child

    def transport(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        return patch.object(producer.subprocess, 'Popen', side_effect=self.spawn)

    def run_main(self, *extra):
        output = io.StringIO()
        errors = io.StringIO()
        with self.transport(), redirect_stdout(output), redirect_stderr(errors):
            status = producer.main(['--codex', str(self.executable), *extra])
        return status, output.getvalue(), errors.getvalue()

    def records(self):
        return [json.loads(line) for line in self.record.read_text(encoding='utf-8').splitlines()]

    def assert_error(self, result, category):
        status, output, errors = result
        self.assertNotEqual(status, 0)
        self.assertEqual(output, '')
        self.assertEqual(errors, 'official_codex_quota: ' + category + '\n')
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_one_shot_protocol_filters_private_data_and_preserves_weekly_duration(self):
        status, output, errors = self.run_main()
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(len(output.splitlines()), 1)
        snapshot = json.loads(output)
        self.assertEqual(set(snapshot), {'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'})
        self.assertEqual(snapshot['metrics'], [{'window': 'codex:primary', 'used_percent': 71,
            'resets_at': 1792649595, 'window_minutes': 10080}])
        self.assertEqual((snapshot['version'], snapshot['scope'], snapshot['provider'], snapshot['source']),
                         (1, 'account', 'codex', 'codex_app'))
        self.assertLessEqual(len(output.encode('ascii')), 16384)
        self.assertNotIn('fixture-private', output)
        messages = [row['received'] for row in self.records() if 'received' in row]
        self.assertEqual([msg['method'] for msg in messages],
                         ['initialize', 'initialized', 'account/read', 'account/rateLimits/read'])
        self.assertEqual(messages[2]['params'], {'refreshToken': False})
        self.assertTrue(any(row.get('stdin_eof') for row in self.records()))
        self.assertEqual(len(self.children), 1)

    def test_timestamp_is_after_fresh_response_and_unchanged_reads_advance(self):
        self.config['initialize_delay'] = 1.05
        self.config['quota_delay'] = 1.05
        with self.transport(), producer.CodexQuotaClient(str(self.executable), rpc_timeout=5) as client:
            first = client.read()
            second = client.read()
        times = [row['quota_response_at'] for row in self.records() if 'quota_response_at' in row]
        self.assertEqual(first['fetched_at'], int(times[0]))
        self.assertEqual(second['fetched_at'], int(times[1]))
        self.assertGreater(second['fetched_at'], first['fetched_at'])
        self.assertEqual(first['metrics'], second['metrics'])
        self.assertGreater(first['fetched_at'], int(self.records()[0]['started_at']))

    def test_account_switch_stops_before_second_quota_read(self):
        changed = deepcopy(ACCOUNT)
        changed['account']['email'] = 'changed@example.invalid'
        self.config['accounts'].append(changed)
        with self.transport(), producer.CodexQuotaClient(str(self.executable)) as client:
            client.read()
            with self.assertRaisesRegex(producer.ProducerError, '^account_changed$'):
                client.read()
        messages = [row['received']['method'] for row in self.records() if 'received' in row]
        self.assertEqual(messages.count('account/read'), 2)
        self.assertEqual(messages.count('account/rateLimits/read'), 1)

    def test_missing_login_and_non_chatgpt_types_fail_closed(self):
        cases = [(None, 'authentication_required'), ({'type': 'apiKey'}, 'account_type_unsupported'),
            ({'type': 'chatgpt', 'email': None}, 'account_identity_unavailable'),
            ({'type': 'chatgpt'}, 'account_identity_unavailable')]
        for account, error in cases:
            with self.subTest(account=account):
                self.config['accounts'] = [{'account': account}]
                self.assert_error(self.run_main(), error)
        self.assertNotIn('quota_response_at', self.record.read_text(encoding='utf-8'))

    def test_absent_or_unknown_only_quota_does_not_emit_snapshot(self):
        for quota in ({}, {'rateLimitsByLimitId': {}}, {'rateLimits': {'primary': None}},
                      {'rateLimits': {'primary': {'usedPercent': None, 'resetsAt': 2000}}}):
            with self.subTest(quota=quota):
                self.config['quotas'] = [quota]
                self.assert_error(self.run_main(), 'unusable_quota')

    def test_bad_numeric_fields_and_excess_windows_do_not_emit_snapshot(self):
        cases = [{'rateLimits': {'primary': {'usedPercent': value}}}
                 for value in (True, -1, 101, '71', float('nan'), float('inf'))]
        cases += [{'rateLimits': {'primary': {'usedPercent': 1, 'windowDurationMins': 0}}},
                  {'rateLimitsByLimitId': {str(i): {'primary': {'usedPercent': 1}}
                                          for i in range(65)}}]
        for quota in cases:
            with self.subTest(quota=quota):
                self.config['quotas'] = [quota]
                self.assert_error(self.run_main(), 'protocol_error' if any(
                    word in json.dumps(quota) for word in ('NaN', 'Infinity')) else 'unusable_quota')

    def test_notifications_are_discarded_and_server_request_is_declined(self):
        self.config['mode'] = 'events'
        status, output, errors = self.run_main()
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output)['metrics'][0]['used_percent'], 71)
        refusal = next(row['refusal'] for row in self.records() if 'refusal' in row)
        self.assertEqual(refusal, {'id': 'fixture-server-request',
            'error': {'code': -32601, 'message': 'Unsupported server request'}})

    def test_rpc_error_is_generic_and_never_repeats_vendor_data(self):
        self.config['mode'] = 'rpc_error'
        self.assert_error(self.run_main(), 'rpc_error')

    def test_malformed_duplicate_nonobject_and_nonfinite_protocol_fail_safely(self):
        for raw in ('fixture-private\n', '[]\n', '{"id":3,"id":3,"result":{}}\n',
                    '{"id":3,"result":{"secret":NaN}}\n',
                    '{"id":3,"result":' + '[' * 1100 + '0' + ']' * 1100 + '}\n'):
            with self.subTest(raw=raw[:40]):
                self.config.update(mode='raw', raw=raw)
                self.assert_error(self.run_main('--rpc-timeout', '0.25'), 'protocol_error')

    def test_oversized_and_unterminated_lines_are_bounded(self):
        self.config.update(mode='raw', raw='x' * 65537)
        self.assert_error(self.run_main('--rpc-timeout', '0.25'), 'protocol_error')
        self.config['raw'] = '{"id":3,"result":{}}'
        started = time.monotonic()
        self.assert_error(self.run_main('--rpc-timeout', '0.15'), 'timeout')
        self.assertLess(time.monotonic() - started, 8)

    def test_wrong_id_and_ambiguous_response_fail_protocol(self):
        for mode in ('wrong_id', 'bad_envelope'):
            with self.subTest(mode=mode):
                self.config['mode'] = mode
                self.assert_error(self.run_main(), 'protocol_error')

    def test_request_flood_is_bounded_and_owned_child_is_reaped(self):
        self.config['mode'] = 'request_flood'
        started = time.monotonic()
        self.assert_error(self.run_main('--rpc-timeout', '0.25'), 'protocol_error')
        self.assertLess(time.monotonic() - started, 8)

    def test_timeout_reaps_only_the_owned_child(self):
        self.config['mode'] = 'timeout'
        started = time.monotonic()
        self.assert_error(self.run_main('--rpc-timeout', '0.15'), 'timeout')
        self.assertEqual(len(self.children), 1)
        self.assertLess(time.monotonic() - started, 8)

    def test_premature_child_exit_is_process_failure(self):
        self.config['mode'] = 'exit'
        self.assert_error(self.run_main(), 'process_failed')

    def test_stubborn_child_is_closed_then_terminated_and_reaped(self):
        self.config['hang_after_eof'] = True
        started = time.monotonic()
        status, output, errors = self.run_main()
        self.assertEqual((status, errors), (0, ''))
        self.assertEqual(json.loads(output)['provider'], 'codex')
        self.assertTrue(any(row.get('stdin_eof') for row in self.records()))
        self.assertTrue(all(child.poll() is not None for child in self.children))
        self.assertLess(time.monotonic() - started, 8)

    def test_nonzero_exit_after_response_is_reported_without_fabricated_output(self):
        self.config['exit_after_eof'] = 7
        status, output, errors = self.run_main()
        self.assertNotEqual(status, 0)
        self.assertEqual(errors, 'official_codex_quota: process_failed\n')
        self.assertEqual(len(output.splitlines()), 1)

    def test_argument_bounds_reject_before_child_start(self):
        for argv in ([], ['--codex', 'relative.exe'],
            ['--codex', str(self.executable), '--interval', '59.9'],
            ['--codex', str(self.executable), '--interval', 'nan'],
            ['--codex', str(self.executable), '--count', '0'],
            ['--codex', str(self.executable), '--count', '-1'],
            ['--codex', str(self.executable), '--rpc-timeout', '31'],
            ['--codex', str(self.executable), '--rpc-timeout', '0'],
            ['--codex', str(self.executable), '--unknown', 'fixture-private']):
            with self.subTest(argv=argv), self.transport(), redirect_stdout(io.StringIO()) as output, \
                    redirect_stderr(io.StringIO()) as errors:
                status = producer.main(argv)
                self.assert_error((status, output.getvalue(), errors.getvalue()), 'invalid_arguments')
        self.assertEqual(self.children, [])

    def test_spawn_failure_does_not_expose_path_or_exception(self):
        with (patch.object(producer.subprocess, 'Popen', side_effect=OSError('fixture-private')),
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors):
            status = producer.main(['--codex', str(self.executable)])
        self.assert_error((status, output.getvalue(), errors.getvalue()), 'process_start_failed')


if __name__ == '__main__':
    unittest.main()
