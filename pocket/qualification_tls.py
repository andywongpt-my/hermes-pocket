"""Explicit, single-use loopback TLS qualification fixture. No providers or audio I/O.

PLv2 capture is native 24 kHz microphone-left/reference-right PCM16. Incoming
PCM is validated and discarded, not retained or logged. Speaker PCM is silence.
Digital credit and successful protocol Stop never establish physical acceptance.
"""
import asyncio
import hmac
import json
import logging
import math
import re
import ssl
import struct

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

ROUTE = '/qualification/v2/live'
_HEADER = struct.Struct('!2sBBIII')
_LOG = logging.Logger('qualification_tls_silent', level=logging.CRITICAL + 1)
_LOG.addHandler(logging.NullHandler())
_LOG.propagate = False


def _control(raw):
    if not isinstance(raw, str) or len(raw.encode()) > 1024:
        raise ValueError('control')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('control')
    return value


class QualificationTLSServer:
    """Caller supplies ephemeral certificate context and synthetic bearer token.

    No CLI, credentials/settings discovery, reconnect, raw persistence, provider,
    LAN bind, or nonzero output capability. Enter once; call wait_closed to await
    the single attempt or global deadline. Context exit also closes idle clients.
    """
    def __init__(self, *, ssl_context, token, host='127.0.0.1',
                 deadline_seconds=60, capture_frames=6000, output_samples=144000):
        if (not isinstance(ssl_context, ssl.SSLContext) or
                ssl_context.protocol != ssl.PROTOCOL_TLS_SERVER or
                host not in ('127.0.0.1', '::1') or
                not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token) or
                type(deadline_seconds) not in (int, float) or
                not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 60 or
                type(capture_frames) is not int or not 1 <= capture_frames <= 6000 or
                type(output_samples) is not int or not 0 <= output_samples <= 144000):
            raise ValueError('explicit bounded TLS qualification configuration required')
        self._tls, self._token, self._host = ssl_context, token, host
        self._duration, self._frame_cap, self._output_cap = deadline_seconds, capture_frames, output_samples
        self._entered = self._claimed = False
        self._server = self._timer = None
        self._done = asyncio.Event()
        self._report = dict(capture_frames=0, capture_wire_bytes=0, speaker_samples=0,
                            speaker_frames=0, speaker_wire_bytes=0, max_source_pending=0,
                            completed=0, deadline_expired=0, protocol_failed=0,
                            actual_playout_known=0, physical_acceptance=0)
        self.port = None

    def summary(self):
        """Fixed numeric allowlist only; no PCM, token, address, or peer text."""
        return dict(self._report)

    async def __aenter__(self):
        if self._entered:
            raise ValueError('single-use fixture')
        self._entered = True
        self._end = asyncio.get_running_loop().time() + self._duration
        self._server = await serve(self._handle, self._host, 0, ssl=self._tls,
            process_request=self._authorize, compression=None, max_size=1024,
            max_queue=4, write_limit=4096, open_timeout=min(1, self._duration),
            ssl_handshake_timeout=min(1, self._duration / 2),
            ssl_shutdown_timeout=min(.2, self._duration / 4),
            close_timeout=min(.2, self._duration / 4), ping_interval=None, logger=_LOG)
        self.port = self._server.sockets[0].getsockname()[1]
        self._timer = asyncio.create_task(self._expire())
        return self

    async def _expire(self):
        # Reserve bounded close grace within the overall requested run budget.
        await asyncio.sleep(max(0, self._end - asyncio.get_running_loop().time()
                                - min(.25, self._duration / 3)))
        if not self._done.is_set():
            self._report['deadline_expired'] = 1
            self._server.close()
            await self._server.wait_closed()
            self._token = ''
            self._done.set()

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()
        self._timer.cancel()
        await asyncio.gather(self._timer, return_exceptions=True)
        self._token = ''  # Drop reference; not a secure-erasure promise.
        self._done.set()

    async def wait_closed(self):
        await self._done.wait()
        await self._server.wait_closed()

    def _authorize(self, connection, request):
        values = request.headers.get_all('Authorization')
        valid = (request.path == ROUTE and len(values) == 1 and
                 hmac.compare_digest(values[0].encode(), ('Bearer ' + self._token).encode()))
        if not valid:
            return connection.respond(403, 'Forbidden\n')
        if self._claimed:
            return connection.respond(409, 'Already used\n')
        self._claimed = True
        return None

    async def _handle(self, ws):
        try:
            async with asyncio.timeout_at(self._end - min(.25, self._duration / 3)):
                await self._session(ws)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            self._report['protocol_failed'] = 1
            try:
                async with asyncio.timeout(.1):
                    await ws.send('{"type":"error","code":"session_unconfirmed"}')
            except (TimeoutError, ConnectionClosed):
                pass
        except TimeoutError:
            self._report['deadline_expired'] = 1
        except ConnectionClosed:
            pass
        finally:
            self._server.close()
            await ws.close()
            self._token = ''
            self._done.set()

    async def _session(self, ws):
        start = _control(await ws.recv())
        chat = start.pop('chat_id', None)
        expected = dict(type='start', version=2, rate=24000, channels=2,
                        format='pcm_s16le', reference='es8311_dac_right')
        if (start != expected or any(type(start.get(k)) is not int for k in ('version','rate','channels'))
                or chat != 'qualification'):
            raise ValueError('start')
        await ws.send('{"type":"ready","version":2,"rate":24000,"reference":"es8311_dac_right"}')
        credit = 0
        while True:
            # At most four packets/1920 samples; never wait for credit in a
            # separate blocking output loop. Receive remains able to observe Stop.
            while self._report['speaker_samples'] < self._output_cap:
                count = min(480, self._output_cap - self._report['speaker_samples'])
                pending = self._report['speaker_samples'] - credit
                if pending + count > 1920:
                    break
                packet = _HEADER.pack(b'PL', 2, 2, self._report['speaker_frames'],
                                      self._report['speaker_samples'], 0) + bytes(count * 2)
                await ws.send(packet)
                self._report['speaker_frames'] += 1
                self._report['speaker_samples'] += count
                self._report['speaker_wire_bytes'] += len(packet)
                self._report['max_source_pending'] = max(self._report['max_source_pending'], pending + count)
            raw = await ws.recv()
            if isinstance(raw, str):
                if _control(raw) != {'type':'stop'}:
                    raise ValueError('stop')
                await ws.send(json.dumps(dict(type='closed', finalized=True, reason='stopped',
                    backend_pending=False, playback_pending=self._report['speaker_samples'] > 0,
                    actual_playout='unknown'), separators=(',', ':')))
                self._report['completed'] = 1
                return
            if len(raw) != 976 or self._report['capture_frames'] >= self._frame_cap:
                raise ValueError('capture size/cap')
            magic, version, kind, seq, offset, new_credit = _HEADER.unpack_from(raw)
            if ((magic, version, kind) != (b'PL', 2, 1) or
                    seq != self._report['capture_frames'] or offset != seq * 240 or
                    not credit <= new_credit <= self._report['speaker_samples']):
                raise ValueError('capture continuity')
            credit = new_credit
            self._report['capture_frames'] += 1
            self._report['capture_wire_bytes'] += len(raw)
            del raw  # No accumulated raw audio; library buffering is capped above.
