"""Optional Live upgrade boundary; synthetic loopback only, no provider calls."""
import asyncio
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus, ConnectionClosed
from websockets.frames import Frame, OP_TEXT
from pocket.server import make_server
from pocket.service import Service, FakeHermes
from pocket.store import Store
try:
    from pocket.live_websocket import LiveEndpoint
except ImportError:
    LiveEndpoint = None


class LiveWebSocketTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(LiveEndpoint, 'optional Live WebSocket route missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.service = Service(self.store, FakeHermes())
        self.calls = []
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.client = SimpleNamespace()
        self.echo = object()
        async def adapter(client, service, device, stream, stop, *, echo):
            self.calls.append((client, service, device, echo))
            self.entered.set()
            try:
                while not stop.is_set():
                    message = await stream.recv()
                    if message is None: return
                    await stream.send(message)
            finally:
                await stream.close()
                self.finished.set()
        from pocket.live_device import run_device_gpt_live
        self.real_adapter = run_device_gpt_live
        self.patch = patch('pocket.live_device.run_device_gpt_live', adapter)
        self.patch.start()
        self.server = make_server({'linux': self.service},
            {'fixture-a': 'dev-a', 'fixture-b': 'dev-b'},
            live={'linux': LiveEndpoint(self.client, lambda: self.echo)})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'ws://127.0.0.1:{self.server.server_port}/targets/linux/live'

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.patch.stop()
            self.store.close()
            self.tmp.cleanup()

    def connection(self, token='fixture-a', **kwargs):
        return connect(self.url, additional_headers={'Authorization': 'Bearer ' + token},
                       open_timeout=2, close_timeout=1, compression=None, **kwargs)

    def test_authenticated_target_device_and_binary_text_transport(self):
        with self.connection() as ws:
            ws.send('hello')
            self.assertEqual(ws.recv(timeout=2), 'hello')
            ws.send(b'\x00\xff' * 480)
            self.assertEqual(ws.recv(timeout=2), b'\x00\xff' * 480)
        self.assertTrue(self.finished.wait(2))
        self.assertEqual(self.calls, [(self.client, self.service, 'dev-a', self.echo)])

    def test_real_device_adapter_waits_for_hello_and_runs_only_gpt_live(self):
        from tests.test_gpt_live import FakeConnection, sdk_client
        connection = FakeConnection()
        self.client.live = sdk_client(connection).live
        class Echo:
            async def capture(self, pcm, capture_sample, played_samples): return pcm
            async def playback(self, pcm, sample_offset): pass
        self.echo = Echo()
        with patch('pocket.live_device.run_device_gpt_live', self.real_adapter):
            with self.connection() as ws:
                self.assertEqual(connection.sent, [])
                ws.send(json.dumps({'type': 'start', 'version': 1, 'rate': 24000,
                    'channels': 1, 'format': 'pcm_s16le', 'chat_id': 'chat'}))
                self.assertEqual(json.loads(ws.recv(timeout=2))['type'], 'ready')
                ws.send('{"type":"stop"}')
                self.assertEqual(json.loads(ws.recv(timeout=2)), {
                    'type': 'closed', 'finalized': True, 'reason': 'stopped',
                    'backend_pending': False, 'playback_pending': False})
                with self.assertRaises(ConnectionClosed) as caught: ws.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1000)
        self.assertEqual(connection.sent[0]['session']['model'], 'gpt-live-1')
        self.assertEqual(connection.sent[0]['session']['delegation'], {'type': 'client'})
        self.assertIn('session.close', [event['type'] for event in connection.sent])
        self.assertTrue(connection.closed)

    def test_real_device_adapter_rejects_bad_hello_without_provider(self):
        from tests.test_gpt_live import FakeConnection, sdk_client
        connection = FakeConnection()
        self.client.live = sdk_client(connection).live
        class Echo:
            async def capture(self, pcm, capture_sample, played_samples): return pcm
            async def playback(self, pcm, sample_offset): pass
        self.echo = Echo()
        with patch('pocket.live_device.run_device_gpt_live', self.real_adapter):
            with self.connection() as ws:
                ws.send('{"type":"start","device":"other-owner"}')
                self.assertEqual(json.loads(ws.recv(timeout=2)),
                                 {'type': 'error', 'code': 'session_unconfirmed'})
                with self.assertRaises(ConnectionClosed) as caught: ws.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1011)
        self.assertEqual(connection.sent, [])

    def test_unauthorized_and_origin_never_reach_adapter(self):
        for kwargs, status in (({'token': 'wrong'}, 401), ({'origin': 'https://untrusted.example'}, 403)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InvalidStatus) as caught:
                    self.connection(**kwargs)
                self.assertEqual(caught.exception.response.status_code, status)
        self.assertEqual(self.calls, [])

    def test_query_and_duplicate_auth_are_rejected(self):
        for suffix, headers in (('?token=fixture-a', [('Authorization', 'Bearer fixture-a')]),
                                ('', [('Authorization', 'Bearer fixture-a')] * 2)):
            with self.subTest(suffix=suffix, count=len(headers)):
                with self.assertRaises(InvalidStatus) as caught:
                    connect(self.url + suffix, additional_headers=headers, open_timeout=2)
                self.assertEqual(caught.exception.response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_body_and_invalid_handshake_fail_before_adapter(self):
        for extra in ('Content-Length: 5\r\n', 'Sec-WebSocket-Key: invalid\r\n'):
            with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as sock:
                # Don't send the declared body: rejection must precede reading it.
                sock.sendall(('GET /targets/linux/live HTTP/1.1\r\nHost: localhost\r\n'
                    'Authorization: Bearer fixture-a\r\nUpgrade: websocket\r\n'
                    'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n' + extra + '\r\n').encode())
                self.assertIn(b'400', sock.recv(4096).split(b'\r\n')[0])
        self.assertEqual(self.calls, [])

    def test_duplicate_host_rejected_before_upgrade(self):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as sock:
            sock.sendall(b'GET /targets/linux/live HTTP/1.1\r\nHost: localhost\r\nHost: second\r\n'
                b'Authorization: Bearer fixture-a\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                b'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==\r\n\r\n')
            self.assertIn(b'400', sock.recv(4096).split(b'\r\n')[0])
        self.assertEqual(self.calls, [])

    def test_adapter_error_after_upgrade_never_becomes_http_or_private_message(self):
        async def broken(*args, **kwargs):
            raise RuntimeError('private-provider-secret')
        with patch('pocket.live_device.run_device_gpt_live', broken):
            with self.connection() as ws:
                self.assertEqual(json.loads(ws.recv(timeout=2)),
                                 {'type': 'error', 'code': 'session_unconfirmed'})
                with self.assertRaises(ConnectionClosed) as caught: ws.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1011)
                self.assertNotIn('private-provider-secret', str(caught.exception))

    def test_unfinalized_outcome_is_reported_but_never_normal_close(self):
        from pocket.gpt_live import LiveOutcome
        async def stopped(*args, **kwargs):
            return LiveOutcome(False, 'stopped_before_connect', False, False)
        with patch('pocket.live_device.run_device_gpt_live', stopped):
            with self.connection() as ws:
                self.assertEqual(json.loads(ws.recv(timeout=2)), {
                    'type': 'closed', 'finalized': False, 'reason': 'stopped_before_connect',
                    'backend_pending': False, 'playback_pending': False})
                with self.assertRaises(ConnectionClosed) as caught: ws.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1011)

    def test_v2_outcome_explicitly_reports_actual_playout_unknown(self):
        from pocket.live_device_v2 import PairedLiveOutcome
        async def stopped(*args, **kwargs):
            return PairedLiveOutcome(True, 'stopped', False, True)
        with patch('pocket.live_device.run_device_gpt_live', stopped):
            with self.connection() as ws:
                self.assertEqual(json.loads(ws.recv(timeout=2)), {
                    'type': 'closed', 'finalized': True, 'reason': 'stopped',
                    'backend_pending': False, 'playback_pending': True,
                    'actual_playout': 'unknown'})

    def test_v2_outcome_cannot_claim_audible_playout(self):
        from pocket.live_device_v2 import PairedLiveOutcome
        async def stopped(*args, **kwargs):
            return PairedLiveOutcome(True, 'stopped', False, False, 'complete')
        with patch('pocket.live_device.run_device_gpt_live', stopped):
            with self.connection() as ws:
                self.assertEqual(json.loads(ws.recv(timeout=2))['type'], 'error')

    def test_stop_before_provider_ready_reports_unfinalized_outcome(self):
        from pocket.gpt_live import LiveOutcome
        async def stopped(*args, **kwargs):
            return LiveOutcome(False, 'stopped_before_ready', False, False)
        with patch('pocket.live_device.run_device_gpt_live', stopped):
            with self.connection() as ws:
                self.assertEqual(json.loads(ws.recv(timeout=2)), {
                    'type': 'closed', 'finalized': False, 'reason': 'stopped_before_ready',
                    'backend_pending': False, 'playback_pending': False})
                with self.assertRaises(ConnectionClosed) as caught: ws.recv(timeout=2)
                self.assertEqual(caught.exception.rcvd.code, 1011)

    def test_only_one_session_per_device_and_target(self):
        with self.connection() as first:
            self.assertTrue(self.entered.wait(2))
            with self.assertRaises(InvalidStatus) as caught:
                self.connection()
            self.assertEqual(caught.exception.response.status_code, 409)
            with self.connection(token='fixture-b') as second:
                second.send('separate device')
                self.assertEqual(second.recv(timeout=2), 'separate device')
            first.send('still active')
            self.assertEqual(first.recv(timeout=2), 'still active')

    def test_oversized_and_fragmented_messages_are_bounded(self):
        with self.connection() as ws:
            ws.send(['a' * 600, 'b' * 600])
            with self.assertRaises(ConnectionClosed): ws.recv(timeout=2)
        self.assertTrue(self.finished.wait(2))

    def test_valid_fragmentation_and_ping(self):
        with self.connection() as ws:
            ws.send(['a' * 400, 'b' * 400])
            self.assertEqual(ws.recv(timeout=2), 'a' * 400 + 'b' * 400)
            pong = ws.ping(b'ping')
            self.assertTrue(pong.wait(2))

    def test_prefetched_first_frame_after_http_headers_is_not_lost(self):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as sock:
            request = (f'GET /targets/linux/live HTTP/1.1\r\nHost: localhost\r\n'
                'Authorization: Bearer fixture-a\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==\r\n\r\n').encode()
            sock.sendall(request + Frame(OP_TEXT, b'prefetched').serialize(mask=True))
            data = b''
            while b'prefetched' not in data:
                part = sock.recv(4096)
                self.assertTrue(part)
                data += part
            self.assertIn(b'101 Switching Protocols', data)
            self.assertIn(b'prefetched', data)

    def test_server_close_stops_active_session_before_store_cleanup(self):
        ws = self.connection()
        try:
            self.assertTrue(self.entered.wait(2))
            started = time.monotonic()
            self.server.server_close()
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(self.finished.is_set())
            with self.assertRaises(ConnectionClosed): ws.recv(timeout=2)
        finally:
            ws.close()

    def test_missing_endpoint_is_unavailable_without_provider(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.server = make_server({'linux': self.service}, {'fixture-a': 'dev-a'})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'ws://127.0.0.1:{self.server.server_port}/targets/linux/live'
        with self.assertRaises(InvalidStatus) as caught: self.connection()
        self.assertEqual(caught.exception.response.status_code, 503)
        self.assertEqual(self.calls, [])


class WireCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_write_cannot_overlap_close_frame_on_socket(self):
        from types import SimpleNamespace
        from websockets.protocol import OPEN
        from websockets.server import ServerProtocol
        from pocket.live_websocket import _Stream
        entered, release = threading.Event(), threading.Event()
        active = 0
        overlap = False
        state_lock = threading.Lock()
        def write(data):
            nonlocal active, overlap
            with state_lock:
                overlap |= active != 0
                active += 1
            entered.set()
            release.wait(2)
            with state_lock: active -= 1
        handler = SimpleNamespace(wfile=SimpleNamespace(write=write),
                                  connection=SimpleNamespace(shutdown=lambda how: None))
        stream = _Stream(handler, ServerProtocol(state=OPEN))
        task = asyncio.create_task(stream.send(b'audio'))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            closing = asyncio.create_task(stream.close())
            await asyncio.sleep(.05)
            release.set()
            await asyncio.wait_for(closing, 2)
            self.assertFalse(overlap, 'cancelled worker still owns the socket write')
        finally:
            release.set()
