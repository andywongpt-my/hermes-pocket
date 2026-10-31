"""Offline host opt-in: real service/server construction, no bound listener."""
import inspect
import sqlite3
import tempfile
import unittest
from collections import UserDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pocket.live_websocket import LiveEndpoint
from pocket.run_bridge import build


class LiveHostWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'linux.sqlite'
        self.config = {'port': 8765, 'devices': {'device-a': 'POCKET_TEST_DEVICE'},
            'targets': {'linux': {'origin': 'http://127.0.0.1:8888',
                'token_env': 'POCKET_TEST_BACKEND', 'database': str(self.db),
                'loopback_http': True}}}
        self.probes = []
        def backend(transport, method, path, body, headers):
            self.probes.append((method, path))
            return {'features': {name: True for name in
                ('run_submission', 'run_status', 'run_stop')}}
        self.enterContext(patch('pocket.transport.JSONTransport.__call__', backend))
        self.enterContext(patch('http.server.HTTPServer.server_bind'))
        self.enterContext(patch('socketserver.TCPServer.server_activate'))
        self.enterContext(patch.dict('os.environ', {
            'POCKET_TEST_DEVICE': 'x' * 32, 'POCKET_TEST_BACKEND': 'fixture'}, clear=True))
        self.client = SimpleNamespace(close=lambda: self.fail('caller client closed'))
        self.endpoint = LiveEndpoint(self.client,
            lambda: self.fail('echo factory invoked at startup'))

    def constructed(self, **kwargs):
        server, resources = build(self.config, **kwargs)
        def cleanup():
            server.server_close()
            for resource in reversed(resources):
                resource.close()
        self.addCleanup(cleanup)
        options = inspect.getclosurevars(server.RequestHandlerClass.dispatch).nonlocals
        return server, resources, options

    def test_default_off_even_with_live_looking_environment(self):
        with patch.dict('os.environ', {'POCKET_LIVE_ENABLED': '1',
                                      'OPENAI_API_KEY': 'not-a-key'}):
            _, _, options = self.constructed()
        self.assertEqual(options['live'], {})
        self.assertIsNone(options['owner'].live)
        self.assertEqual(self.probes, [('GET', '/v1/capabilities')])

    def test_explicit_mapping_preserves_endpoint_identity_and_snapshots_mapping(self):
        endpoints = UserDict({'linux': self.endpoint})
        server, resources, options = self.constructed(live=endpoints)
        endpoints.clear()
        self.assertEqual(set(options['live']), {'linux'})
        self.assertIs(options['live']['linux'], self.endpoint)
        self.assertIs(options['live']['linux'].client, self.client)
        self.assertIsNotNone(options['owner'].live)
        self.assertNotIn(self.endpoint, resources)
        self.assertNotIn(self.client, resources)
        server.server_close()
        self.assertTrue(options['owner'].live.closing)
        self.assertEqual(self.probes, [('GET', '/v1/capabilities')])

    def test_empty_explicit_mapping_stays_off(self):
        _, _, options = self.constructed(live={})
        self.assertEqual(options['live'], {})
        self.assertIsNone(options['owner'].live)

    def test_bad_mappings_rejected_before_credentials_probes_or_database(self):
        for invalid in ([], [('linux', self.endpoint)], 'linux', True,
                        {'windows': self.endpoint}, {'other': self.endpoint},
                        {'linux': object()}, {'linux': None}):
            with self.subTest(invalid=invalid), patch.dict('os.environ', {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'Live'):
                    build(self.config, live=invalid)
                self.assertEqual(self.probes, [])
                self.assertFalse(self.db.exists())

    def test_opt_in_is_not_a_json_configuration_key(self):
        self.config['live'] = {'linux': self.endpoint}
        with self.assertRaisesRegex(ValueError, 'invalid bridge configuration'):
            build(self.config)
        self.assertEqual(self.probes, [])
        self.assertFalse(self.db.exists())

    def test_listener_failure_closes_owned_store_and_registry_not_client(self):
        captured = {}
        def fail_bind(server):
            captured.update(inspect.getclosurevars(
                server.RequestHandlerClass.dispatch).nonlocals)
            raise OSError('synthetic listener failure')
        with patch('http.server.HTTPServer.server_bind', fail_bind):
            with self.assertRaisesRegex(OSError, 'synthetic listener failure'):
                build(self.config, live={'linux': self.endpoint})
        self.assertTrue(captured['owner'].closed)
        self.assertTrue(captured['owner'].live.closing)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured['targets']['linux'].store.db.execute('SELECT 1')
        self.assertIs(captured['live']['linux'].client, self.client)


if __name__ == '__main__':
    unittest.main()
