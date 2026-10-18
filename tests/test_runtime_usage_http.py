"""Authenticated loopback and explicit bridge wiring; synthetic metadata only."""
import http.client
import inspect
import json
import os
import socket
import threading
import unittest
from unittest.mock import patch

from pocket.run_bridge import build
from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store
from pocket.usage import UsageCache, codex_limits, claude_limits, ollama_balance
from tests.test_runtime_usage import RuntimeFixture


class RuntimeUsageHTTPTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        original = socket.socket.connect
        def loopback_only(sock, address):
            if address[0] != '127.0.0.1':
                raise AssertionError('test attempted a non-loopback connection')
            return original(sock, address)
        guard = patch.object(socket.socket, 'connect', loopback_only)
        guard.start(); self.addCleanup(guard.stop)
        self.other_store = Store(self.root / 'other.sqlite')
        self.addCleanup(self.other_store.close)
        self.services = {'linux': Service(self.store, FakeHermes()),
                         'windows': Service(self.other_store, FakeHermes())}
        self.cache = UsageCache()

    def start_server(self, runtime=True):
        self.assertIn('runtime_usage', inspect.signature(make_server).parameters,
                      'optional authenticated runtime mapping is missing')
        source = self.source()
        kwargs = {'runtime_usage': {'linux': source}} if runtime else {}
        self.server = make_server(self.services, {'fixture': 'owner', 'other': 'other'},
                                  usage_cache=self.cache, **kwargs)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        def close():
            self.server.shutdown(); thread.join(); self.server.server_close()
        self.addCleanup(close)
        return source

    def call(self, path, token='fixture', method='GET'):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            client.request(method, path, headers={'Authorization': 'Bearer ' + token})
            response = client.getresponse(); raw = response.read()
            return response.status, json.loads(raw), len(raw)
        finally: client.close()

    def test_http_adds_device_scoped_local_counts_while_all_quota_fields_stay_unknown(self):
        self.owned('owner', 'one'); self.owned('other', 'two')
        self.observation('one'); self.observation('two', values=(99, 99, 99, 99, 99, 99))
        self.start_server()
        code, rows, size = self.call('/targets/linux/usage?device=other')
        self.assertEqual(code, 200)
        self.assertLessEqual(size, 8000)
        self.assertEqual([r['provider'] for r in rows], ['codex', 'claude', 'ollama_cloud'])
        for row in rows:
            self.assertEqual(row['availability'], 'not_configured')
            self.assertIsNone(row['value']); self.assertIsNone(row['fetched_at'])
            self.assertIsNone(row['source']); self.assertEqual(row['metrics'], [])
        self.assertEqual(rows[2]['local_usage']['main']['api_calls'], 1)
        self.assertEqual(rows[0]['local_usage']['availability'], 'unavailable')
        self.assertEqual(self.call('/targets/linux/usage', token='other')[1][2]
                         ['local_usage']['main']['api_calls'], 99)
        self.assertTrue(all('local_usage' not in r for r in self.call('/targets/windows/usage')[1]))

    def test_authorization_and_route_boundaries_precede_any_runtime_read(self):
        source = self.start_server()
        with patch.object(source, 'report', wraps=source.report) as report:
            for path, token, method, status in (
                ('/targets/linux/usage', 'wrong', 'GET', 401),
                ('/targets/absent/usage', 'fixture', 'GET', 404),
                ('/usage', 'fixture', 'GET', 400),
                ('/targets/linux/usage', 'fixture', 'POST', 404)):
                self.assertEqual(self.call(path, token, method)[0], status)
            report.assert_not_called()

    def test_legacy_default_is_exact_and_configured_quota_is_preserved(self):
        self.start_server(runtime=False)
        self.assertEqual(self.call('/targets/linux/usage')[1], [
            self.cache.report('linux', provider, self.now)
            for provider in ('codex', 'claude', 'ollama_cloud')])

    def test_bounded_local_usage_does_not_overwrite_real_quota_snapshots(self):
        self.owned('owner', 'one'); self.observation('one', 'openai-codex')
        rows = codex_limits({'rateLimits': {'primary': {'usedPercent': 42,
                              'windowDurationMins': 300, 'resetsAt': 2000000000}}}, self.now)
        self.cache.put('linux', 'codex', rows)
        self.start_server()
        response = self.call('/targets/linux/usage')[1][0]
        expected = self.cache.report('linux', 'codex', __import__('time').time())
        local = response.pop('local_usage')
        self.assertEqual(response, expected)
        self.assertEqual(local['main']['api_calls'], 1)
        self.assertEqual(response['metrics'][0]['value'], 42)

    def test_maximum_supported_quota_and_runtime_fields_fit_device_response_limit(self):
        self.owned('owner', 'one')
        for provider in ('openai-codex', 'claude-subscription-directsdk-experimental', 'ollama-cloud'):
            self.observation('one', provider, values=(2**53 - 1,) * 6, last_seen=253402300799)
            self.observation('one', provider, task='title', values=(2**53 - 1,) * 6)
        fixtures = {'codex': codex_limits({'rateLimits': {'primary': {'usedPercent': 1}}}, self.now),
            'claude': claude_limits({'rate_limits': {'five_hour': {'used_percentage': 1}}}, self.now),
            'ollama_cloud': ollama_balance({'included': {'balance_usd': 1}}, self.now)}
        for provider, metrics in fixtures.items():
            self.cache.put('linux', provider, [dict(metrics[0], window='\\' * 46 + f'{i:02d}',
                source='s' * 64, fetched_at=2**53 - 200, resets_at=2**53 - 1,
                window_minutes=(2**53 - 1) / 60) for i in range(64)])
        self.start_server()
        self.assertLessEqual(self.call('/targets/linux/usage')[2], 8000)

    def test_runtime_mapping_cannot_bind_a_different_target_database(self):
        self.assertIn('runtime_usage', inspect.signature(make_server).parameters,
                      'optional authenticated runtime mapping is missing')
        source = self.source()
        with self.assertRaises(ValueError):
            make_server(self.services, {'fixture': 'owner'}, runtime_usage={'windows': source})
        with self.assertRaises(ValueError):
            make_server(self.services, {'fixture': 'owner'}, runtime_usage={'unknown': source})

    def test_oversized_runtime_reply_is_explicit_instead_of_truncated(self):
        self.owned('owner', 'one'); self.observation('one')
        source = self.start_server()
        for provider in ('codex', 'claude', 'ollama_cloud'):
            metric = dict(provider=provider, category='purchased_credit', window='\\' * 48,
                value=1.7976931348623157e308, unit='USD_credit', fetched_at=2**53 - 1,
                resets_at=2**53 - 1, source='s' * 64, window_minutes=(2**53 - 1) / 60)
            self.cache.put('linux', provider, [metric] * 64)
        rows = source.report('owner')
        for row in rows.values():
            row.update(availability='partial', reason='incomplete_metadata',
                       fetched_at=253402300799, last_seen=253402300799)
            for section in ('main', 'auxiliary', 'coverage'):
                row[section] = dict.fromkeys(row[section], 2**53 - 1)
        # This test exercises HTTP serialization of a bounded contract snapshot.
        with patch.object(source, 'report', return_value=rows):
            code, data, size = self.call('/targets/linux/usage')
        self.assertEqual(code, 413)
        self.assertEqual(data, {'error': 'usage report exceeds device limit'})
        self.assertLessEqual(size, 8000)

    def test_missing_runtime_source_does_not_break_status_or_chat_readiness(self):
        self.state.unlink(); self.start_server()
        code, rows, size = self.call('/targets/linux/usage')
        self.assertEqual(code, 200)
        self.assertTrue(all(r['local_usage']['availability'] == 'failed' for r in rows))
        self.assertEqual(self.call('/targets/linux/status')[1]['backend'], 'available')
        self.assertEqual(self.call('/targets/linux/models')[0], 200)


class RuntimeUsageConfigurationTests(RuntimeFixture):
    def config(self, usage=True):
        target = {'origin': 'http://127.0.0.1:1', 'token_env': 'POCKET_BACKEND',
                  'database': str(self.root / 'bridge.sqlite'), 'loopback_http': True}
        if usage: target['usage'] = {'kind': 'hermes-runtime', 'state_db': str(self.state)}
        return {'targets': {'linux': target}, 'devices': {'owner': 'POCKET_DEVICE'},
                'port': 18765}

    def build_fixture(self, config, make=None):
        class CheckedFake(FakeHermes):
            def check(self): return None
        stack = __import__('contextlib').ExitStack()
        stack.enter_context(patch.dict(os.environ, {'POCKET_DEVICE': 'x' * 32,
                                                   'POCKET_BACKEND': 'synthetic'}))
        stack.enter_context(patch('pocket.run_bridge.HermesAdapter', return_value=CheckedFake()))
        factory = stack.enter_context(patch('pocket.run_bridge.make_server'))
        if make is not None: factory.side_effect = make
        try:
            server, resources = build(config)
        except BaseException:
            stack.close(); raise
        self.addCleanup(stack.close)
        for resource in resources: self.addCleanup(resource.close)
        return factory, resources

    def test_explicit_configuration_wires_a_source_derived_from_the_target_store(self):
        try: factory, resources = self.build_fixture(self.config())
        except ValueError as error:
            self.fail('explicit hermes-runtime configuration is not accepted: ' + str(error))
        source = factory.call_args.kwargs['runtime_usage']['linux']
        service = factory.call_args.args[0]['linux']
        self.assertTrue(source.matches_store(service.store))
        self.assertIn(source, resources)
        self.assertEqual(source.state_db, self.state.resolve())

    def test_omitted_configuration_keeps_runtime_source_disabled(self):
        factory, resources = self.build_fixture(self.config(usage=False))
        self.assertFalse(factory.call_args.kwargs.get('runtime_usage'))
        self.assertEqual(len(resources), 1)

    def test_configuration_requires_exact_keys_explicit_kind_and_absolute_local_path(self):
        invalid = (None, {}, {'kind': 'other', 'state_db': str(self.state)},
                   {'kind': 'hermes-runtime', 'state_db': 'relative'},
                   {'kind': 'hermes-runtime', 'state_db': str(self.state), 'token_env': 'POCKET_X'})
        for usage in invalid:
            config = self.config(); config['targets']['linux']['usage'] = usage
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                self.build_fixture(config)

    def test_build_error_closes_created_runtime_source_and_close_is_idempotent(self):
        captured = {}
        def fail_server(*args, **kwargs):
            captured.update(kwargs)
            raise OSError('synthetic bind failure')
        try:
            with self.assertRaises(OSError): self.build_fixture(self.config(), make=fail_server)
        except ValueError as error:
            self.fail('runtime configuration prevented cleanup test: ' + str(error))
        source = captured['runtime_usage']['linux']
        source.close()
        self.assertEqual(source.report('owner')['codex']['reason'], 'closed')


if __name__ == '__main__': unittest.main()
