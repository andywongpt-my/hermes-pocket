"""Optional authenticated-device WebSocket transport, not a listener or key loader.

RFC6455 validation/framing is owned by websockets Sans-I/O. All protocol calls
are serialized on the session event loop. Reads retain the HTTP handler's
buffered rfile, including any audio bytes prefetched alongside request headers.
"""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import logging
import json
import socket
import threading
import time

from websockets.server import ServerProtocol
from websockets.frames import OP_TEXT, OP_BINARY, OP_CONT, OP_CLOSE
from websockets.protocol import OPEN

from .gpt_live import LiveError, LiveOutcome

_logger = logging.Logger('pocket.live.transport', level=logging.CRITICAL)
_logger.addHandler(logging.NullHandler())
_logger.propagate = False


@dataclass(frozen=True)
class LiveEndpoint:
    client: object
    echo_factory: object

    def __post_init__(self):
        if self.client is None or not callable(self.echo_factory):
            raise ValueError('explicit Live client and echo factory required')


def _disconnect(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


@dataclass
class _Lease:
    connection: object
    done: object = field(default_factory=threading.Event)
    loop: object = None
    stop: object = None


class LiveRegistry:
    """One session per device/target; closing prevents further admission."""
    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}
        self.closing = False

    def close(self):
        with self.lock:
            self.closing = True
            active = list(self.active.values())
            for lease in active:
                if lease.loop is not None:
                    try: lease.loop.call_soon_threadsafe(lease.stop.set)
                    except RuntimeError: pass
                _disconnect(lease.connection)
        deadline = time.monotonic() + 20
        for lease in active:
            if not lease.done.wait(max(0, deadline - time.monotonic())):
                # Do not let the server owner close stores under active work.
                raise RuntimeError('Live session cleanup incomplete')

    def serve(self, handler, endpoint, service, target, device):
        if len(handler.headers.get_all('Host', [])) != 1:
            return handler.respond(400, {'error': 'invalid websocket upgrade'})
        protocol = ServerProtocol(origins=[None], max_size=1024, logger=_logger)
        # Feed only the already-consumed HTTP header block. Actual frame bytes
        # remain in rfile and must never be read directly from the raw socket.
        headers = handler.raw_requestline + b''.join(
            (key + ': ' + value + '\r\n').encode('latin-1')
            for key, value in handler.headers.raw_items()) + b'\r\n'
        protocol.receive_data(headers)
        requests = protocol.events_received()
        if len(requests) != 1:
            return handler.respond(400, {'error': 'invalid websocket upgrade'})
        response = protocol.accept(requests[0])
        if response.status_code != 101:
            return handler.respond(response.status_code, {'error': 'invalid websocket upgrade'})
        key = (target, device)
        lease = _Lease(handler.connection)
        with self.lock:
            if self.closing:
                return handler.respond(503, {'error': 'live unavailable'})
            if key in self.active:
                return handler.respond(409, {'error': 'live session already active'})
            self.active[key] = lease
        handler.close_connection = True
        try:
            protocol.send_response(response)
            for data in protocol.data_to_send():
                if data: handler.wfile.write(data)
            handler.wfile.flush()
            async def run():
                from .live_device import run_device_gpt_live
                stop = asyncio.Event()
                with self.lock:
                    lease.loop, lease.stop = asyncio.get_running_loop(), stop
                    if self.closing: stop.set()
                stream = _Stream(handler, protocol)
                try:
                    if not stop.is_set():
                        echo = endpoint.echo_factory()
                        outcome = await run_device_gpt_live(endpoint.client, service, device,
                                                           stream, stop, echo=echo)
                        if isinstance(outcome, LiveOutcome):
                            await stream.report_outcome(outcome)
                except Exception:
                    # No provider errors, tokens, audio or transcript in logs.
                    pass
                finally:
                    await stream.close()
            asyncio.run(run())
        except Exception:
            pass  # Never send an HTTP error after a successful upgrade.
        finally:
            _disconnect(handler.connection)
            with self.lock:
                self.active.pop(key, None)
                lease.done.set()


class _Stream:
    def __init__(self, handler, protocol):
        self.handler, self.protocol = handler, protocol
        self.lock, self.reader_lock = asyncio.Lock(), asyncio.Lock()
        self.wire_lock = threading.Lock()
        self.events = deque()
        self.fragments = bytearray()
        self.opcode = None
        self.closed = False
        self.reported = False
        self.close_code = 1011

    def _write(self, data):
        # Cancelling to_thread doesn't stop its sendall. The worker must retain
        # ownership until it finishes, even if the coroutine releases its lock.
        with self.wire_lock:
            self.handler.wfile.write(data)

    async def _flush(self):
        data = b''.join(self.protocol.data_to_send())
        if data:
            # Socket timeout is set by the HTTP handler (3 seconds), providing
            # a bound even if cancellation cannot stop this worker immediately.
            await asyncio.to_thread(self._write, data)

    async def recv(self):
        async with self.reader_lock:
            while not self.closed:
                while self.events:
                    frame = self.events.popleft()
                    if frame.opcode == OP_CLOSE:
                        return None
                    if frame.opcode in (OP_TEXT, OP_BINARY):
                        self.opcode = frame.opcode
                        self.fragments.clear()
                    if frame.opcode in (OP_TEXT, OP_BINARY, OP_CONT):
                        self.fragments.extend(frame.data)
                        if len(self.fragments) > 1024:
                            raise LiveError('device_message_too_large')
                        if frame.fin:
                            data = bytes(self.fragments)
                            self.fragments.clear()
                            return data.decode('utf-8') if self.opcode == OP_TEXT else data
                data = await asyncio.to_thread(self.handler.rfile.read1, 2048)
                async with self.lock:
                    if self.closed: return None
                    if data: self.protocol.receive_data(data)
                    else: self.protocol.receive_eof()
                    self.events.extend(self.protocol.events_received())
                    await self._flush()
                    if self.protocol.parser_exc is not None:
                        raise LiveError('invalid_device_websocket')
                if not data: return None
            return None

    async def send(self, message):
        if not isinstance(message, (str, bytes)):
            raise LiveError('invalid_device_message')
        data = message.encode('utf-8') if isinstance(message, str) else message
        if len(data) > 1024: raise LiveError('device_message_too_large')
        async with self.lock:
            if self.closed: raise LiveError('device_connection_lost')
            if isinstance(message, str): self.protocol.send_text(data)
            else: self.protocol.send_binary(data)
            await self._flush()

    async def report_outcome(self, outcome):
        # These flags describe provider finalization and remaining work, not
        # success of later local callback cleanup or actual speaker drain.
        if self.closed or self.reported: return
        allowed = {'stopped', 'session_limit', 'microphone_ended', 'server_closed',
                   'stopped_before_connect', 'stopped_before_start', 'stopped_before_ready'}
        if (not isinstance(outcome, LiveOutcome) or outcome.reason not in allowed or
                any(type(value) is not bool for value in
                    (outcome.finalized, outcome.backend_pending, outcome.playback_pending))):
            raise LiveError('invalid_live_outcome')
        actual_playout = getattr(outcome, 'actual_playout', None)
        if actual_playout not in (None, 'unknown'):
            raise LiveError('invalid_live_outcome')
        payload = {'type': 'closed', 'finalized': outcome.finalized,
            'reason': outcome.reason, 'backend_pending': outcome.backend_pending,
            'playback_pending': outcome.playback_pending}
        if actual_playout is not None: payload['actual_playout'] = actual_playout
        await self.send(json.dumps(payload, separators=(',', ':')))
        self.reported = True
        if outcome.finalized: self.close_code = 1000

    async def close(self):
        try:
            async with self.lock:
                if self.closed: return
                self.closed = True
                if self.protocol.state is OPEN:
                    if not self.reported:
                        self.protocol.send_text(b'{"type":"error","code":"session_unconfirmed"}')
                    self.protocol.send_close(self.close_code,
                        '' if self.close_code == 1000 else 'session unconfirmed')
                    await self._flush()
        finally:
            # End outstanding buffered reads before asyncio's worker shutdown.
            # No reconnect and no replay; peer acknowledgment isn't fabricated.
            _disconnect(self.handler.connection)
