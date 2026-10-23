"""Exercise a real proxy/parser against a loopback-only fake Pocket."""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
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
        cls.records = []

        class FakePocket(BaseHTTPRequestHandler):
            def handle_request(self):
                length = int(self.headers.get('Content-Length', '0'))
                payload = self.rfile.read(length)
                cls.records.append({'payload': payload, 'auth': self.headers.get('Authorization'), 'host': self.headers.get('Host'), 'override': self.headers.get('X-Original-URL')})
                cls.calls.append((self.command, self.path))
                ok = self.headers.get('Authorization') == SYNTHETIC_AUTH
                body = b'{"fixture":true}' if ok else b'{"error":"unauthorized"}'
                streamed = False
                if ok and self.path.endswith('/fixture_response_exact'):
                    body = b'{"data":"' + b'A' * (262144 - len(b'{"data":""}')) + b'"}'
                elif ok and self.path.endswith('/fixture_declared_large'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', '262145')
                    self.end_headers()
                    return
                elif ok and self.path.endswith('/fixture_streamed_large'):
                    body = b'{"data":"' + b'A' * 262144 + b'"}'
                    streamed = True
                elif ok and self.path.endswith('/fixture_slow_upstream'):
                    time.sleep(1.25)
                self.send_response(200 if ok else 401)
                self.send_header('Content-Type', 'application/json')
                if not streamed:
                    self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A bounded proxy may close a rejected fixture response.

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
            config = prefix / 'nginx.conf'
            config.write_text(text, encoding='utf-8')
            command = ['/usr/bin/nginx', '-p', str(prefix) + '/', '-c', str(config), '-e', '/dev/null']
        else:
            code = ('import sys; sys.path.insert(0,' + repr(str(Path(__file__).resolve().parent)) + '); '
                    'from aiohttp import web; import pocket_funnel_021 as proxy; '
                    'proxy.REQUEST_TIMEOUT=.75; '
                    'web.run_app(proxy.make_app(' + repr(backend) + ',None),host="127.0.0.1",port=' + str(cls.port) +
                    ',access_log=None,print=None,handler_cancellation=True)')
            command = [sys.executable, '-B', '-c', code]
        cls.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.addClassCleanup(cls.stop_proxy)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise AssertionError('isolated proxy failed to start: '+cls.process.stderr.read(4096).decode('utf-8','replace'))
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    return
            except OSError:
                time.sleep(.02)
        raise AssertionError('isolated proxy did not listen')

    @classmethod
    def stop_proxy(cls):
        if cls.process.poll() is None:
            cls.process.send_signal(signal.SIGQUIT if args.red_baseline else signal.SIGTERM)
            try:
                cls.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=3)

    def request(self, method, path, auth=SYNTHETIC_AUTH, body=None, read_limit=1024):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Authorization': auth} if auth is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = (response.status, response.read(read_limit))
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

    def test_exact_voice_body_auth_and_host_survive_forwarding(self):
        body = b'{"sequence":7,"data":"AAECAwQFBgcICQoL/8=="}'
        self.assertEqual(self.request('POST', '/targets/linux/audio/cap_1/chunk', body=body)[0], 200)
        self.assertEqual(self.records[-1]['payload'], body)
        self.assertEqual(self.records[-1]['auth'], SYNTHETIC_AUTH)
        self.assertEqual(self.records[-1]['host'], '192.0.2.10:8443')

    def test_exact_8192_byte_body_is_preserved(self):
        body = b'{"data":"' + b'A' * (8192 - len(b'{"data":""}')) + b'"}'
        self.assertEqual(len(body), 8192)
        self.assertEqual(self.request('POST', '/targets/linux/audio/cap_1/chunk', body=body)[0], 200)
        self.assertEqual(self.records[-1]['payload'], body)

    def test_ambiguous_framing_and_duplicate_auth_never_forward(self):
        extra_headers = [
            b'Content-Length: 2\r\nContent-Length: 2\r\n',
            b'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n',
            b'Transfer-Encoding: chunked\r\n',
            b'Content-Length: 2\r\nAuthorization: Bearer invalid-second-header-00000000000\r\n',
        ]
        for headers in extra_headers:
            with self.subTest(header_case=extra_headers.index(headers)):
                before = len(self.calls)
                with socket.create_connection(('127.0.0.1', self.port), timeout=3) as sock:
                    request = (b'POST /targets/linux/tasks HTTP/1.1\r\nHost: localhost\r\nAuthorization: ' +
                               SYNTHETIC_AUTH.encode() + b'\r\n' + headers + b'Connection: close\r\n\r\n{}')
                    sock.sendall(request)
                    response = http.client.HTTPResponse(sock)
                    response.begin()
                    self.assertIn(response.status, (400, 401))
                    response.close()
                self.assertEqual(len(self.calls), before)

    def test_slow_body_has_a_total_deadline(self):
        before = len(self.calls)
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as sock:
            sock.sendall(b'POST /targets/linux/tasks HTTP/1.1\r\nHost: localhost\r\nAuthorization: ' +
                         SYNTHETIC_AUTH.encode() + b'\r\nContent-Length: 4\r\nConnection: close\r\n\r\n{')
            began = time.monotonic()
            response = http.client.HTTPResponse(sock)
            try:
                response.begin()
            except TimeoutError:
                self.fail('proxy did not enforce a total request-body deadline')
            self.assertEqual(response.status, 408)
            self.assertLess(time.monotonic() - began, 2)
            response.close()
        self.assertEqual(len(self.calls), before)

    def test_declared_and_streamed_oversized_responses_are_rejected(self):
        for fixture in ('fixture_declared_large', 'fixture_streamed_large'):
            with self.subTest(fixture=fixture):
                status, body = self.request('GET', '/targets/linux/tasks/' + fixture)
                self.assertEqual(status, 502)
                self.assertLess(len(body), 1024)

    def test_exact_262144_byte_response_is_preserved(self):
        expected = b'{"data":"' + b'A' * (262144 - len(b'{"data":""}')) + b'"}'
        result = self.request('GET', '/targets/linux/tasks/fixture_response_exact', read_limit=262145)
        self.assertEqual(result, (200, expected))

    def test_active_request_limit_and_disconnect_recovery(self):
        held = []
        before = len(self.calls)
        try:
            for unused in range(16):
                sock = socket.create_connection(('127.0.0.1', self.port), timeout=3)
                held.append(sock)
                sock.sendall(b'POST /targets/linux/tasks HTTP/1.1\r\nHost: localhost\r\nAuthorization: ' +
                             SYNTHETIC_AUTH.encode() + b'\r\nContent-Length: 4\r\nConnection: close\r\n\r\n{')
            time.sleep(.06)  # Let all complete headers enter their body-read handlers.
            self.assertEqual(self.request('GET', '/targets/linux/status')[0], 429)
            self.assertEqual(len(self.calls), before)
        finally:
            for sock in held:
                sock.close()
        deadline = time.monotonic() + 1.5
        while True:
            result = self.request('GET', '/targets/linux/status')
            if result[0] == 200 or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        self.assertEqual(result, (200, b'{"fixture":true}'))

    def test_upstream_has_a_total_deadline(self):
        began = time.monotonic()
        self.assertEqual(self.request('GET', '/targets/linux/tasks/fixture_slow_upstream')[0], 504)
        self.assertLess(time.monotonic() - began, 2)

    def test_idle_and_partial_headers_have_an_initial_deadline(self):
        before = len(self.calls)
        began = time.monotonic()
        sockets = [socket.create_connection(('127.0.0.1', self.port), timeout=18)
                   for unused in range(2)]
        try:
            sockets[1].sendall(b'GET /health HTTP/1.1\r\nHost: localhost')
            for sock in sockets:
                reply = sock.recv(1024)
                self.assertTrue(not reply or reply.startswith((b'HTTP/1.0 408', b'HTTP/1.1 408')),
                                'initial-header deadline must close or return408')
            self.assertLess(time.monotonic() - began, 17.5)
            self.assertEqual(len(self.calls), before)
        finally:
            for sock in sockets:
                sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--red-baseline', action='store_true')
    parser.add_argument('--config', default=str(Path(__file__).with_name('nginx.conf')))
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProxyTests))
    print(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skips': len(result.skipped),
                      'loopback_fixture_only': True, 'real_credentials': False,
                      'real_bridge_requests': 0, 'proxy_owned_process_reaped': ProxyTests.process.poll() is not None}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
