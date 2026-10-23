"""Exercise the real nginx parser/proxy against a loopback-only fake Pocket."""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest

STAGE = '/home/example/.hermes/pocket/temporary-funnel-021'
SYNTHETIC_AUTH = 'Bearer synthetic-fixture-token-00000000000000'
UPSTREAM = 'https://192.0.2.10:8443'
args = None


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []

        class FakePocket(BaseHTTPRequestHandler):
            def handle_request(self):
                length = int(self.headers.get('Content-Length', '0'))
                self.rfile.read(length)
                cls.calls.append((self.command, self.path))
                ok = self.headers.get('Authorization') == SYNTHETIC_AUTH
                body = b'{"fixture":true}' if ok else b'{"error":"unauthorized"}'
                self.send_response(200 if ok else 401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = handle_request

            def log_message(self, *unused):
                pass

        cls.backend = ThreadingHTTPServer(('127.0.0.1', 0), FakePocket)
        cls.backend.daemon_threads = True
        cls.thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.backend.server_close)
        cls.addClassCleanup(cls.backend.shutdown)
        cls.tmp = tempfile.TemporaryDirectory(prefix='pocket-funnel-test-')
        cls.addClassCleanup(cls.tmp.cleanup)
        prefix = Path(cls.tmp.name)
        (prefix / 'body').mkdir()
        (prefix / 'proxy').mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        backend = f'http://127.0.0.1:{cls.backend.server_port}'
        if args.red_baseline:
            # Deliberately naive direct proxy: no public-route restriction.
            text = ('daemon off; master_process off; error_log /dev/null crit; '
                    'pid ' + str(prefix / 'nginx.pid') + '; events {} http {'
                    'access_log off; client_body_temp_path ' + str(prefix / 'body') + '; proxy_temp_path ' + str(prefix / 'proxy') + '; fastcgi_temp_path ' + str(prefix / 'fastcgi') + '; uwsgi_temp_path ' + str(prefix / 'uwsgi') + '; scgi_temp_path ' + str(prefix / 'scgi') + '; '
                    'server {listen 127.0.0.1:' + str(cls.port) + '; location / {'
                    'proxy_pass ' + backend + ';}}}')
        else:
            text = Path(args.config).read_text(encoding='utf-8')
            text = text.replace(STAGE, str(prefix))
            text = text.replace('127.0.0.1:18843', f'127.0.0.1:{cls.port}')
            text = text.replace(UPSTREAM, backend)
            text = text.replace('server 192.0.2.10:8443;', f'server 127.0.0.1:{cls.backend.server_port};')
            text = text.replace('proxy_pass https://pocket_lan_021;', 'proxy_pass http://pocket_lan_021;')
            assert 'server 192.0.2.10:8443;' not in text
        config = prefix / 'nginx.conf'
        config.write_text(text, encoding='utf-8')
        cls.process = subprocess.Popen(
            ['/usr/bin/nginx', '-p', str(prefix) + '/', '-c', str(config), '-e', '/dev/null'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.addClassCleanup(cls.stop_nginx)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise AssertionError('isolated nginx failed to start: '+cls.process.stderr.read(4096).decode('utf-8','replace'))
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    return
            except OSError:
                time.sleep(.02)
        raise AssertionError('isolated nginx did not listen')

    @classmethod
    def stop_nginx(cls):
        if cls.process.poll() is None:
            cls.process.send_signal(signal.SIGQUIT)
            try:
                cls.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=3)

    def request(self, method, path, auth=SYNTHETIC_AUTH, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Authorization': auth} if auth is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = (response.status, response.read(1024))
        connection.close()
        return result

    def test_authenticated_device_routes_forward(self):
        cases = [
            ('GET', '/health'), ('GET', '/targets'), ('GET', '/targets/linux/status'),
            ('GET', '/targets/linux/models'), ('GET', '/targets/linux/usage'),
            ('GET', '/targets/linux/tasks'), ('POST', '/targets/linux/tasks'),
            ('GET', '/targets/linux/tasks/task_1'), ('GET', '/targets/linux/tasks/task_1/view'),
            ('GET', '/targets/linux/tasks/task_1/events?after=12'),
            ('GET', '/targets/linux/tasks/task_1/result/pages?cursor=' + 'A' * 47),
            ('GET', '/targets/linux/tasks/task_1/speech'),
            ('POST', '/targets/linux/tasks/task_1/speech'),
            ('GET', '/targets/linux/tasks/task_1/speech/chunks/0'),
            ('GET', '/targets/linux/tasks/task_1/speech/v2?generation=' + 'a' * 32),
            ('POST', '/targets/linux/tasks/task_1/speech/v2'),
            ('POST', '/targets/linux/tasks/task_1/speech/v2/cancel'),
            ('GET', '/targets/linux/tasks/task_1/speech/v2/segments/0/chunks/0?generation=' + 'a' * 32),
            ('POST', '/targets/linux/tasks/task_1/stop'),
            ('POST', '/targets/linux/tasks/task_1/abandon'),
            ('GET', '/targets/linux/chats/chat_1/messages'),
            ('POST', '/targets/linux/chats/chat_1/messages'),
            ('GET', '/targets/linux/audio/capture_1'),
            ('POST', '/targets/linux/audio/capture_1'),
            ('POST', '/targets/linux/audio/capture_1/chunk'),
            ('POST', '/targets/linux/audio/capture_1/finish'),
            ('POST', '/targets/linux/audio/capture_1/abort'),
            ('GET', '/status'), ('GET', '/models'), ('GET', '/usage'),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                before = len(self.calls)
                self.assertEqual(self.request(method, path, body=b'{}' if method == 'POST' else None),
                                 (200, b'{"fixture":true}'))
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual(self.calls[-1], (method, path))

    def test_admin_pairing_and_ambiguous_routes_never_forward(self):
        paths = ['/', '/pair', '/pairing', '/setup', '/admin', '/diagnostics/transport',
                 '/targets/linux/diagnostics/transport', '/gateway', '/v1/chat/completions',
                 '/status/extra', '/targets/windows/status', '/targets/linux/admin',
                 '/targets/linux/../diagnostics/transport', '/targets/linux/%2e%2e/status',
                 '/targets/linux/tasks/a%2Fb', '//targets/linux/status', '/health?secret=x',
                 '/targets/linux/tasks/a/events?after=1&admin=1',
                 '/targets/linux/tasks/a/result/pages?cursor=1&path=/admin',
                 '/targets/linux/tasks/a/speech/v2?generation=g&admin=1']
        for path in paths:
            for method in ['GET', 'POST']:
                with self.subTest(method=method, path=path):
                    before = len(self.calls)
                    self.assertEqual(self.request(method, path)[0], 404)
                    self.assertEqual(len(self.calls), before)

    def test_unsupported_methods_never_forward(self):
        for method in ['HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE']:
            with self.subTest(method=method):
                before = len(self.calls)
                self.assertEqual(self.request(method, '/targets/linux/status')[0], 404)
                self.assertEqual(len(self.calls), before)

    def test_missing_or_malformed_auth_never_reaches_backend(self):
        for auth in [None, '', 'Basic test', 'Bearer short', 'Bearer ' + 'x' * 129]:
            with self.subTest(auth_case=0 if auth is None else len(auth)):
                before = len(self.calls)
                self.assertEqual(self.request('GET', '/targets/linux/status', auth=auth)[0], 401)
                self.assertEqual(len(self.calls), before)

    def test_backend_remains_pairing_authentication_authority(self):
        before = len(self.calls)
        result = self.request('GET', '/targets/linux/status', auth='Bearer ' + 'x' * 40)
        self.assertEqual(result, (401, b'{"error":"unauthorized"}'))
        self.assertEqual(len(self.calls), before + 1)

    def test_oversized_body_never_forwards(self):
        before = len(self.calls)
        self.assertEqual(self.request('POST', '/targets/linux/tasks', body=b'x' * 8193)[0], 413)
        self.assertEqual(len(self.calls), before)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--red-baseline', action='store_true')
    parser.add_argument('--config', default=str(Path(__file__).with_name('nginx.conf.example')))
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProxyTests))
    print(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skips': len(result.skipped),
                      'loopback_fixture_only': True, 'real_credentials': False,
                      'real_bridge_requests': 0, 'nginx_owned_process_reaped': ProxyTests.process.poll() is not None}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
