"""Real accepted sockets must send small response bodies without Nagle delay."""
import http.client
import json
from pathlib import Path
import queue
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store


class HTTPNoDelayTests(unittest.TestCase):
    def check_accepted_socket(self, tls=None):
        store = Store(':memory:')
        backend = FakeHermes()
        server = make_server({'linux': Service(store, backend)},
                             {'fixture': 'fixture-device'}, tls=tls)
        accepted = queue.Queue()
        original_setup = server.RequestHandlerClass.setup

        def observe_setup(handler):
            original_setup(handler)
            accepted.put((handler.connection.getsockopt(socket.IPPROTO_TCP,
                                                         socket.TCP_NODELAY),
                          handler.connection.gettimeout()))

        server.RequestHandlerClass.setup = observe_setup
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        if tls:
            context = ssl.create_default_context(cafile=tls['certificate'])
            context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
            client = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                                                 timeout=3, context=context)
        else:
            client = http.client.HTTPConnection('127.0.0.1', server.server_port,
                                                timeout=3)
        try:
            connection = None
            for path in ('/health', '/targets'):
                client.request('GET', path, headers={'Authorization': 'Bearer fixture'})
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(len(body), int(response.getheader('Content-Length')))
                json.loads(body)
                if connection is None:
                    connection = client.sock
                self.assertIs(client.sock, connection)
            nodelay, timeout = accepted.get(timeout=1)
            self.assertEqual(nodelay, 1, 'accepted server socket must disable Nagle')
            self.assertEqual(timeout, 3)
            self.assertTrue(accepted.empty(), 'the requests must reuse one connection')
            self.assertEqual(backend.submissions, 0)
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
            store.close()

    def test_plain_http_accepted_socket_disables_nagle(self):
        self.check_accepted_socket()

    @unittest.skipUnless(shutil.which('openssl'), 'fixture certificate generation needs openssl')
    def test_tls12_accepted_socket_disables_nagle(self):
        with tempfile.TemporaryDirectory(prefix='pocket-test-tls-') as directory:
            certificate = Path(directory) / 'fixture-cert.pem'
            private_key = Path(directory) / 'fixture-key.pem'
            subprocess.run([shutil.which('openssl'), 'req', '-x509', '-newkey', 'ec',
                            '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes', '-days', '1',
                            '-subj', '/CN=localhost', '-addext',
                            'subjectAltName=IP:127.0.0.1', '-keyout', str(private_key),
                            '-out', str(certificate)], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.check_accepted_socket({'certificate': str(certificate),
                                        'private_key': str(private_key)})


if __name__ == '__main__':
    unittest.main()
