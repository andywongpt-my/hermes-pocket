"""Actual authenticated localhost HTTP with synthetic adjacent account files."""
import http.client
import json
import os
import socket
import threading
import time
from unittest.mock import patch

from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store
from pocket.usage import UsageCache
from tests.test_quota_snapshots import snapshot
from tests.test_runtime_usage import RuntimeFixture


class QuotaHTTPTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        original = socket.socket.connect
        def loopback_only(sock, address):
            if address[0] != '127.0.0.1': raise AssertionError('non-loopback test connection')
            return original(sock, address)
        guard = patch.object(socket.socket, 'connect', loopback_only)
        guard.start(); self.addCleanup(guard.stop)
        self.cache = UsageCache()
        self.other = Store(self.root / 'other.sqlite'); self.addCleanup(self.other.close)
        self.folder = self.root / ('.' + self.store.db.execute('PRAGMA database_list').fetchone()[2].split(os.sep)[-1] + '.account-quota-v1')
        self.folder.mkdir(mode=0o700)

    def write(self, data, provider='codex'):
        path = self.folder / (provider + '.json')
        path.write_bytes(json.dumps(data).encode()); path.chmod(0o600)

    def start(self):
        self.local = self.source()
        self.server = make_server({'linux': Service(self.store, FakeHermes()),
            'windows': Service(self.other, FakeHermes())}, {'fixture': 'owner', 'other': 'other'},
            usage_cache=self.cache, runtime_usage={'linux': self.local})
        thread = threading.Thread(target=self.server.serve_forever); thread.start()
        def close(): self.server.shutdown(); thread.join(); self.server.server_close()
        self.addCleanup(close)

    def call(self, path='/targets/linux/usage', token='fixture', method='GET'):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            client.request(method, path, headers={'Authorization': 'Bearer ' + token})
            response = client.getresponse(); raw = response.read()
            return response.status, json.loads(raw), raw, dict(response.getheaders())
        finally: client.close()

    def test_remaining_rows_keep_local_history_exact_and_target_separate(self):
        self.owned('owner', 'one'); self.observation('one', 'openai-codex')
        stamp = int(time.time())
        self.write(snapshot(fetched_at=stamp)); self.write(snapshot('claude', stamp, [
            {'window': 'seven_day', 'used_percent': 60, 'resets_at': None, 'window_minutes': 10080}]), 'claude')
        self.start(); expected_local = self.local.report('owner')
        code, rows, raw, headers = self.call()
        self.assertEqual(code, 200)
        self.assertLessEqual(len(raw), 8000)
        self.assertEqual(headers.get('Cache-Control'), 'no-store')
        self.assertEqual([r['provider'] for r in rows], ['codex', 'claude', 'ollama_cloud'])
        self.assertTrue(rows[0]['metrics'], 'adjacent Codex account source is not read')
        self.assertTrue(rows[1]['metrics'], 'adjacent Claude account source is not read')
        self.assertEqual(rows[0]['metrics'][0]['value'], 75)
        self.assertEqual(rows[1]['metrics'][0]['value'], 40)
        self.assertEqual(rows[0]['metrics'][0]['unit'], 'percent_remaining')
        for row in rows: self.assertEqual(row['local_usage'], expected_local[row['provider']])
        self.assertTrue(all(r['availability'] == 'not_configured' for r in self.call('/targets/windows/usage')[1]))

    def test_initial_invalid_source_does_not_break_status_models_or_auth(self):
        self.write({'synthetic-private-source-error': 'secret'}); self.start()
        code, rows, raw, _ = self.call()
        self.assertEqual((code, rows[0]['availability'], rows[0]['metrics']), (200, 'failed', []))
        self.assertNotIn(b'synthetic-private', raw)
        self.assertEqual(self.call('/targets/linux/status')[1]['backend'], 'available')
        self.assertEqual(self.call('/targets/linux/models')[0], 200)
        self.assertEqual(self.call(token='wrong')[0], 401)
        self.assertEqual(self.call(method='POST')[0], 404)
        self.assertEqual(self.call('/targets/absent/usage')[0], 404)

    def test_expired_source_retains_original_timestamp_and_independent_windows(self):
        stamp = int(time.time())
        self.write(snapshot(fetched_at=stamp, metrics=[
            {'window': 'codex:primary', 'used_percent': 10, 'resets_at': stamp - 1},
            {'window': 'codex:secondary', 'used_percent': 90, 'resets_at': stamp + 600}]))
        self.start(); rows = self.call()[1][0]
        self.assertEqual(rows['fetched_at'], stamp)
        self.assertEqual([m['availability'] for m in rows['metrics']], ['stale', 'available'])
        self.assertEqual([m['value'] for m in rows['metrics']], [90, 10])

    def test_maximum_snapshot_windows_and_local_numbers_respect_8000_byte_body(self):
        self.owned('owner', 'one'); self.observation('one', 'openai-codex', values=(2**53 - 1,) * 6)
        stamp = int(time.time())
        metrics = [{'window': '\\' * 46 + f'{i:02d}', 'used_percent': 0.12345678901234567,
                    'resets_at': 2**53 - 1, 'window_minutes': 4294967295} for i in range(64)]
        for provider in ('codex', 'claude'): self.write(snapshot(provider, stamp, metrics), provider)
        self.start(); code, rows, raw, _ = self.call()
        self.assertEqual(code, 200)
        self.assertIn('omitted_metrics', rows[0], '64-window account snapshot is not read')
        self.assertEqual(rows[0]['omitted_metrics'], 60)
        self.assertEqual(rows[1]['omitted_metrics'], 60)
        self.assertLessEqual(len(raw), 8000)
