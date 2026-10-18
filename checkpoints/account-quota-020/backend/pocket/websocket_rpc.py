"""Lazy Desktop JSON-RPC transport. Mutations are never retried.

The owner's session credential remains on the host in memory; the device
receives only its independently paired Pocket credential.
"""
import json
import queue
import threading
import uuid
from urllib.parse import urlsplit
from .transport import TransportError, unique


class DesktopRPC:
    ALLOWED = {'ping', 'session.create', 'prompt.submit',
               'session.events.since', 'session.interrupt'}

    def __init__(self, origin, token, *, timeout=15):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('Desktop requires a loopback HTTP origin')
        if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError('owner-provided Desktop session credential required')
        self.url = 'ws://' + parsed.netloc + '/api/ws'
        self._token = token
        self.timeout = timeout
        self.connection = None
        self.lock = threading.RLock()
        self.pending = {}
        self.events = queue.Queue(maxsize=1024)
        self.overflow = False

    def _connect(self):
        if self.connection is not None:
            return
        from websockets.sync.client import connect
        connection = connect(self.url, additional_headers={'X-Hermes-Session-Token': self._token},
                             proxy=None, open_timeout=self.timeout, close_timeout=2,
                             max_size=262144)
        # Negotiate truthfully: Pocket does not answer approval/secret requests.
        # Those operations must be completed in the trusted Hermes interface.
        handshake_id = 'pocket_cap_' + uuid.uuid4().hex
        try:
            connection.send(json.dumps({'jsonrpc':'2.0','id':handshake_id,
                'method':'client.capabilities','params':{'server_requests':False}}))
            import time
            deadline = time.monotonic() + self.timeout
            for _ in range(64):
                raw = connection.recv(timeout=max(0.01, deadline-time.monotonic()))
                frame = json.loads(raw, object_pairs_hook=unique)
                if not isinstance(frame,dict) or frame.get('jsonrpc')!='2.0':
                    raise ValueError('invalid handshake frame')
                if frame.get('id')==handshake_id:
                    if 'error' in frame or not isinstance(frame.get('result'),dict):
                        raise ValueError('Desktop capability negotiation failed')
                    break
                self.events.put_nowait(frame)
            else:
                raise ValueError('Desktop handshake exceeded frame limit')
        except Exception:
            connection.close()
            raise TransportError('Desktop capability negotiation failed') from None
        self.connection = connection
        threading.Thread(target=self._receive, args=(connection,), daemon=True).start()

    def _receive(self, connection):
        try:
            for raw in connection:
                frame = json.loads(raw, object_pairs_hook=unique)
                if not isinstance(frame, dict) or frame.get('jsonrpc') != '2.0':
                    raise ValueError('invalid RPC frame')
                with self.lock:
                    waiter = self.pending.get(frame.get('id')) if 'method' not in frame else None
                if waiter is not None:
                    try:waiter.put_nowait(frame)
                    except queue.Full:pass
                else:
                    try:
                        self.events.put_nowait(frame)
                    except queue.Full:
                        self.overflow = True
        except Exception:
            pass
        finally:
            with self.lock:
                if self.connection is connection:
                    self.connection = None
                    for waiter in self.pending.values():
                        try:
                            waiter.put_nowait({'error': {'message': 'connection lost'}})
                        except queue.Full:
                            pass

    def call(self, method, params):
        if method not in self.ALLOWED:
            raise ValueError('unsupported Desktop operation')
        request_id = 'pocket_' + uuid.uuid4().hex
        waiter = queue.Queue(maxsize=1)
        try:
            with self.lock:
                self._connect()
                self.pending[request_id] = waiter
                self.connection.send(json.dumps({'jsonrpc': '2.0', 'id': request_id,
                                                 'method': method, 'params': params}, ensure_ascii=False))
            response = waiter.get(timeout=self.timeout)
            if 'error' in response or not isinstance(response.get('result'), dict):
                raise TransportError('Desktop RPC unavailable')
            return response['result']
        except Exception:
            raise TransportError('Desktop RPC unavailable; admission may be uncertain') from None
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def drain(self):
        if self.overflow:
            self.overflow = False
            # The caller must surface uncertainty and reconcile through replay.
            raise TransportError('Desktop event queue overflow; replay required')
        frames = []
        while True:
            try:
                frames.append(self.events.get_nowait())
            except queue.Empty:
                return frames

    def close(self):
        with self.lock:
            connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()
