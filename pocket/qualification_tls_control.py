"""Explicit Stage B USB control/observation; no serial/network/file/provider I/O.

Only an already verified, caller-owned, bounded cooperative transport is accepted.
A host command never substitutes for the device's fresh physical confirmation.
No PCM is accepted here. Configuration is transient and repr-redacted, not a
secure-erasure guarantee for Python objects, swap, crash dumps or caller copies.
"""
import asyncio
from dataclasses import dataclass, field
import ipaddress
import json
import math
import re
import secrets
import struct
import time

from . import qualification as q


def _uint(value, maximum=q.UNKNOWN, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _profile(value):
    if type(value) is not int or value not in (1, 2):
        raise q.QualificationError('invalid_profile')


def _clock_value(value):
    return ((type(value) is int and abs(value) <= 10**15) or
            (type(value) is float and math.isfinite(value) and abs(value) <= 1e15))


@dataclass(frozen=True)
class TLSConfig:
    host: str = field(repr=False)
    port: int = field(repr=False)
    token: str = field(repr=False)
    ca_pem: bytes = field(repr=False)
    duration_ms: int = 60000

    def __post_init__(self):
        try:
            address = ipaddress.IPv4Address(self.host)
            private = any(address in ipaddress.IPv4Network(block) for block in
                          ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        except (ValueError, TypeError):
            private = False
        if (not isinstance(self.host, str) or not private or str(address) != self.host or
                not 7 <= len(self.host) <= 15 or not _uint(self.port, 65535, 1) or
                not _uint(self.duration_ms, 60000, 1) or not isinstance(self.token, str) or
                re.fullmatch(r'[A-Za-z0-9_-]{32,128}', self.token) is None or
                not isinstance(self.ca_pem, bytes) or not 1 <= len(self.ca_pem) <= 768 or
                re.fullmatch(rb'-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+\n-----END CERTIFICATE-----(?:\r?\n)?', self.ca_pem) is None or
                not re.search(rb'[A-Za-z0-9+/=]', self.ca_pem[28:-26])):
            raise q.QualificationError('invalid_explicit_tls_config')


def arm_tls(config, *, nonce, profile=1):
    """Build a redacted record; only q.encode performs framing/CRC."""
    _profile(profile)
    if not isinstance(config, TLSConfig) or not _uint(nonce, (1 << 64)-1, 1):
        raise q.QualificationError('invalid_tls_arm')
    host, token = config.host.encode('ascii'), config.token.encode('ascii')
    payload = struct.pack('<9I', 24000, 60000, 1440000, 1024, 3145728,
                          config.port, config.duration_ms, len(host) | (len(token) << 16),
                          len(config.ca_pem)) + host + token + config.ca_pem
    return q.Record(4, profile, nonce, 0, 0, payload)


_TERMINAL_FIELDS = ('epoch', 'worker_reason', 'cleanup', 'capture_frames',
    'capture_samples', 'last_dma', 'source_accepted', 'source_rejected',
    'deadline_expired', 'clock_failed', 'stop_observed_ms', 'audio_stop_begin_ms',
    'audio_stop_end_ms', 'audio_clean', 'network_clean', 'tls_terminal_received',
    'finalized', 'backend_pending', 'min_free_heap', 'min_largest_block',
    'min_task_stack', 'monitor_count', 'elapsed_device_ms')
REPORT_FIELDS = frozenset(_TERMINAL_FIELDS + ('version', 'profile', 'start_received',
    'terminal_received', 'protocol_complete', 'host_error', 'physical_acceptance',
    'actual_playout_known', 'wire_bytes', 'prefix_bytes', 'elapsed_host_ms',
    'requested_duration_ms', 'dispatch_delay_ms', 'requested_volume'))


def numeric_json(report):
    if (not isinstance(report, dict) or set(report) - REPORT_FIELDS or
            report.get('physical_acceptance', 0) != 0 or report.get('actual_playout_known', 0) != 0 or
            any(not _clock_value(value) for value in report.values())):
        raise q.QualificationError('invalid_tls_numeric_report')
    result = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(result) > 4096:
        raise q.QualificationError('tls_report_budget')
    return result


class TLSObservation:
    """Two numeric device records only; completion is never physical acceptance."""
    def __init__(self, *, profile, nonce, duration_ms):
        _profile(profile)
        if not _uint(nonce, (1 << 64)-1, 1) or not _uint(duration_ms, 60000, 1):
            raise q.QualificationError('invalid_tls_observation')
        self.profile, self.nonce, self.duration_ms = profile, nonce, duration_ms
        self.start = self.terminal = None
        self.host_error = self.wire_bytes = self.prefix_bytes = self.elapsed_host_ms = 0
        self._failed = False

    @property
    def terminal_received(self):
        return self.terminal is not None

    def accept(self, record):
        try:
            self._accept(record)
        except q.QualificationError:
            self._failed = True
            self.host_error = self.host_error or 1
            raise

    def _accept(self, record):
        if (self._failed or self.terminal_received or not isinstance(record, q.Record) or
                record.profile != self.profile or record.nonce != self.nonce or
                record.sample_offset != 0 or not isinstance(record.payload, bytes)):
            raise q.QualificationError('tls_record_discontinuity')
        if self.start is None:
            if record.kind != 19 or record.sequence != 0 or len(record.payload) != 48:
                raise q.QualificationError('tls_start_required')
            values = struct.unpack('<12I', record.payload)
            if (not values[0] or values[1:8] != (24000, 2, 240, self.duration_ms, 6000,
                    1440000, 0 if self.profile == 1 else 144000) or
                    values[8] > (0 if self.profile == 1 else 5) or
                    values[9] > 10000 or values[10:] != (10000, 0)):
                raise q.QualificationError('invalid_tls_start')
            self.start = values
            return
        if record.kind != 20 or record.sequence != 1 or len(record.payload) != 96:
            raise q.QualificationError('tls_terminal_required')
        values = struct.unpack('<24I', record.payload)
        if (values[0] != self.start[0] or values[1] > 14 or values[2] > 2 or
                values[3] > 6000 or values[4] != values[3] * 240 or
                not values[5] <= values[6] <= 1440000 or
                any(values[i] not in (0, 1) for i in (7, 8, 9, 13, 14, 15, 16, 17)) or
                values[23] or (values[2] == 1 and values[13:15] != (1, 1)) or
                (values[16] and not values[15])):
            raise q.QualificationError('invalid_tls_terminal')
        if not values[9]:
            times = values[10:13]
            if (any(value != q.UNKNOWN and value > values[22] for value in times) or
                    (times[1] != q.UNKNOWN and times[2] != q.UNKNOWN and times[2] < times[1])):
                raise q.QualificationError('invalid_tls_times')
        self.terminal = values

    def report(self):
        terminal = self.terminal
        result = {name: q.UNKNOWN for name in _TERMINAL_FIELDS}
        if terminal is not None:
            result.update(zip(_TERMINAL_FIELDS, terminal[:23]))
        result.update(version=1, profile=self.profile, start_received=int(self.start is not None),
            terminal_received=int(self.terminal_received), protocol_complete=int(bool(terminal is not None and
                terminal[1] == 0 and terminal[2] == 1 and terminal[15] == 1 and terminal[16] == 1 and
                not terminal[7] and not terminal[9] and not terminal[17] and not self.host_error)),
            host_error=self.host_error, physical_acceptance=0, actual_playout_known=0,
            wire_bytes=self.wire_bytes, prefix_bytes=self.prefix_bytes, elapsed_host_ms=self.elapsed_host_ms,
            requested_duration_ms=self.duration_ms, dispatch_delay_ms=self.start[9] if self.start else q.UNKNOWN,
            requested_volume=self.start[8] if self.start else q.UNKNOWN)
        return result


class TLSCollector:
    """One use; exact identity and capture authority, distinct AUDIO approval.

    Transport read(maximum) returns bytes, None (idle), or b'' (EOF).
    write(bytes) returns exactly admitted bytes. Caller owns identity verification
    and transport lifetime/control lines. No open/close/reset/reconnect is called.
    75s observation cap starts at ARM, never START: 10s arm + 60s run + 3s worker
    finalization + 2s slack. Device runtime remains its original <=60s predicate.
    """
    def __init__(self, transport, approved_identity, config, *, capture_approved=False,
                 audible_approved=False, profile=1, nonce=None, deadline_seconds=75,
                 clock=time.monotonic):
        _profile(profile)
        if (not isinstance(config, TLSConfig) or not _clock_value(deadline_seconds) or
                not 0 < deadline_seconds <= 75 or not callable(clock)):
            raise q.QualificationError('invalid_tls_collector')
        self.transport, self.identity, self.config = transport, approved_identity, config
        self.capture_approved, self.audible_approved = capture_approved, audible_approved
        self.profile, self.nonce, self.duration, self.clock = profile, nonce, deadline_seconds, clock
        self._used = False

    async def run(self):
        if self._used:
            raise q.QualificationError('attempt_already_used')
        self._used = True
        config, self.config = self.config, None
        if (self.capture_approved is not True or (self.profile == 2 and self.audible_approved is not True) or
                not isinstance(self.identity, str) or not 1 <= len(self.identity) <= 128 or
                any(ord(c) < 33 or ord(c) > 126 for c in self.identity) or
                getattr(self.transport, 'identity', None) != self.identity):
            raise q.QualificationError('explicit_matching_capture_authority_required')
        nonce = self.nonce if self.nonce is not None else (secrets.randbits(64) or 1)
        observation = TLSObservation(profile=self.profile, nonce=nonce, duration_ms=config.duration_ms)
        arm = arm_tls(config, nonce=nonce, profile=self.profile)
        config = None
        parser = q.Parser()
        loop = asyncio.get_running_loop()
        wall_began = loop.time(); wall_end = wall_began + self.duration
        began = self.clock(); last = began
        if not _clock_value(began):
            raise q.QualificationError('invalid_clock')
        injected_end = began + self.duration
        reserve = min(.1, self.duration / 3)
        heartbeat = wall_began
        sequence = 0

        def checked_time():
            nonlocal last
            now = self.clock()
            if not _clock_value(now) or now < last:
                observation.host_error = 4
                raise q.QualificationError('invalid_clock')
            last = now
            return now

        async def send(kind):
            nonlocal sequence, arm
            # Stop on clock failure still uses trusted loop budget; no retry.
            remaining = wall_end - loop.time()
            if kind != 3:
                remaining = min(remaining, injected_end - checked_time()) - reserve
            if remaining <= 0:
                raise q.QualificationError('host_deadline')
            payload = arm.payload if kind == 4 else b''
            packet = q.encode(kind, self.profile, nonce, sequence, 0, payload)
            sequence += 1
            if kind == 4:
                arm = None
            admitted = await asyncio.wait_for(self.transport.write(packet), min(.05, remaining))
            if type(admitted) is not int or admitted != len(packet):
                raise q.QualificationError('control_write_failed')
            if kind != 3 and (checked_time() >= injected_end or loop.time() >= wall_end):
                observation.host_error = 2
                raise q.QualificationError('host_deadline')

        try:
            await send(4)
            while not observation.terminal_received:
                now = checked_time(); wall_now = loop.time()
                remaining = min(injected_end-now, wall_end-wall_now) - reserve
                if remaining <= 0:
                    observation.host_error = 2
                    break
                if wall_now - heartbeat >= .01:
                    await send(2); heartbeat = loop.time()
                    continue
                # Never shrink a normal bounded read just to hit a heartbeat
                # tick: cancelling a threaded serial poll retains/quarantines
                # its owner. Reserve a full 40ms quantum or request Stop early.
                if remaining < .04:
                    observation.host_error = 2
                    break
                timeout = .04
                try:
                    data = await asyncio.wait_for(self.transport.read(q.MAX_CHUNK), timeout)
                except asyncio.TimeoutError:
                    continue
                if checked_time() >= injected_end-reserve or loop.time() >= wall_end-reserve:
                    observation.host_error = 2
                    break
                if data is None:
                    await asyncio.sleep(min(.005, max(0, wall_end-reserve-loop.time())))
                    continue
                if data == b'':
                    observation.host_error = 3
                    break
                records = parser.feed(data)
                try:
                    for record in records:
                        observation.accept(record)
                        if observation.terminal_received:
                            break
                finally:
                    records.close()
                if observation.terminal_received:
                    # Normal console may resume immediately after the checked
                    # terminal drain. This is the live-session boundary, not
                    # parser resynchronization. Never inspect or retain its tail.
                    parser.discard_terminal_tail()
                del data
        except asyncio.CancelledError:
            observation.host_error = 5
            raise
        except Exception:
            observation.host_error = observation.host_error or 1
        finally:
            arm = None
            if not observation.terminal_received or observation.host_error:
                try:
                    await send(3)
                except Exception:
                    pass  # Physical/device deadline and heartbeat remain independent.
            observation.wire_bytes = 208 if observation.terminal_received else max(0, parser.wire_bytes-parser.prefix_bytes)
            observation.prefix_bytes = parser.prefix_bytes
            observation.elapsed_host_ms = max(0, int((loop.time()-wall_began)*1000))
        return observation
