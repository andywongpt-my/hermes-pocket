"""Finite HPQ1 collection. No serial discovery/open/reset, provider or network API.

An operator-supplied transport must already have independently verified identity
and bounded, cancellation-cooperative read/write semantics. This module does not
claim that a serial open/close leaves DTR/RTS or the physical device unchanged.
Raw paired PCM remains in one bounded RAM buffer unless separately retained.
"""
import asyncio
from array import array
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import secrets
import stat
import struct
import time
import zlib

MAGIC = b'HPQ1'
HEADER = struct.Struct('<4sBBBBQIIHHI')
MAX_PAYLOAD = 1024
MAX_WIRE = 3 * 1024 * 1024
MAX_PREFIX = 16384
MAX_CHUNK = 4096
MAX_RAW = 2304000
UNKNOWN = (1 << 32) - 1
LENGTHS = {1: 16, 2: 0, 3: 0, 4: None, 16: 48, 17: 992, 18: 48, 19: 48, 20: 96}


class QualificationError(ValueError):
    """Fixed-code error: never embeds transport data, identity or PCM."""


def _integer(value, maximum=UNKNOWN, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def _profile(profile):
    if type(profile) is not int or profile not in (1, 2):
        raise QualificationError('invalid_profile')
    return 1000 if profile == 1 else 2400


def _length_valid(kind, length):
    return kind in LENGTHS and (76 <= length <= 947 if kind == 4 else length == LENGTHS[kind])


def _tls_structure(kind, sequence, payload):
    """Framing only; TLS control additionally validates host/token/PEM grammar."""
    if kind == 19 and sequence != 0 or kind == 20 and sequence != 1:
        raise QualificationError('invalid_tls_sequence')
    if kind != 4:
        return
    if sequence != 0 or len(payload) < 36:
        raise QualificationError('invalid_tls_arm')
    rate, duration_cap, source_cap, packet_cap, wire_cap, port, duration, lengths, ca = struct.unpack_from('<9I', payload)
    host, token = lengths & 65535, lengths >> 16
    if ((rate, duration_cap, source_cap, packet_cap, wire_cap) != (24000, 60000, 1440000, MAX_PAYLOAD, MAX_WIRE) or
            not 1 <= port <= 65535 or not 1 <= duration <= 60000 or
            not 7 <= host <= 15 or not 32 <= token <= 128 or not 1 <= ca <= 768 or
            len(payload) != 36 + host + token + ca):
        raise QualificationError('invalid_tls_arm')


def encode(kind, profile, nonce, sequence, sample_offset, payload=b''):
    frames = _profile(profile)
    if (type(kind) is not int or kind not in LENGTHS or
            not _integer(nonce, (1 << 64)-1, 1) or not _integer(sequence) or
            not _integer(sample_offset) or not isinstance(payload, bytes) or
            not _length_valid(kind, len(payload))):
        raise QualificationError('invalid_record')
    if kind != 17 and sample_offset != 0:
        raise QualificationError('invalid_offset')
    if kind == 1 and payload != struct.pack('<4I', 24000, frames, MAX_PAYLOAD, MAX_WIRE):
        raise QualificationError('invalid_arm_budget')
    _tls_structure(kind, sequence, payload)
    header = HEADER.pack(MAGIC, 1, kind, profile, 0, nonce, sequence,
                         sample_offset, len(payload), 0, 0)
    crc = zlib.crc32(payload, zlib.crc32(header[:28]))
    return header[:28] + struct.pack('<I', crc) + payload


@dataclass(frozen=True)
class Record:
    kind: int
    profile: int
    nonce: int
    sequence: int
    sample_offset: int
    payload: bytes = field(repr=False)


class Parser:
    """Streaming framing scratch <=8192B; prefix is counted/discarded, not logged.

    Consumers must exhaust each feed generator before feeding again. A failed
    parser never resynchronizes. Feed chunks are capped to match bounded reads.
    """
    def __init__(self):
        self._buffer = bytearray()
        self._locked = self._failed = self._feeding = False
        self._last_kind = None
        self.prefix_bytes = self.wire_bytes = self.peak_scratch_bytes = 0

    def discard_terminal_tail(self):
        """Close at a validated terminal boundary, discarding resumed console.

        Live callers first semantically accept the terminal and close the feed
        generator. Offline callers need not use this: no resync is introduced.
        Returns the discarded byte count, never its potentially private content.
        """
        if self._feeding or self._failed or self._last_kind not in (18, 20):
            raise QualificationError('terminal_boundary_required')
        discarded = len(self._buffer)
        self._buffer[:] = b'\0' * discarded
        self._buffer.clear(); self._failed = True
        self.wire_bytes -= discarded
        return discarded

    def feed(self, data):
        if self._failed or self._feeding:
            raise QualificationError('parser_closed')
        if not isinstance(data, bytes) or len(data) > MAX_CHUNK:
            self._failed = True
            raise QualificationError('invalid_transport_chunk')
        if self.wire_bytes + len(data) > MAX_WIRE:
            self._failed = True
            raise QualificationError('wire_budget')
        self.wire_bytes += len(data)
        self._feeding = True
        try:
            self._buffer.extend(data)
            # Largest record materialization plus its payload and CRC prefix.
            self.peak_scratch_bytes = max(self.peak_scratch_bytes, len(self._buffer)+1056+1024+28)
            if self.peak_scratch_bytes > 8192:
                raise QualificationError('parser_budget')
            if not self._locked:
                at = self._buffer.find(MAGIC)
                discard = at if at >= 0 else max(0, len(self._buffer)-3)
                self.prefix_bytes += discard
                del self._buffer[:discard]
                if self.prefix_bytes > MAX_PREFIX:
                    raise QualificationError('prefix_budget')
                if at < 0:
                    return
                self._locked = True
            while len(self._buffer) >= HEADER.size:
                magic, version, kind, profile, flags, nonce, sequence, offset, length, reserved, crc = HEADER.unpack_from(self._buffer)
                if (magic != MAGIC or version != 1 or flags or reserved or
                        profile not in (1, 2) or not nonce or kind not in LENGTHS or
                        length > MAX_PAYLOAD or not _length_valid(kind, length) or
                        (kind != 17 and offset)):
                    raise QualificationError('invalid_header')
                total = HEADER.size + length
                if len(self._buffer) < total:
                    return
                wire = bytes(self._buffer[:total])
                payload = wire[HEADER.size:]
                if zlib.crc32(payload, zlib.crc32(wire[:28])) != crc:
                    raise QualificationError('invalid_crc')
                if kind == 1 and payload != struct.pack('<4I', 24000, _profile(profile), MAX_PAYLOAD, MAX_WIRE):
                    raise QualificationError('invalid_arm_budget')
                _tls_structure(kind, sequence, payload)
                del self._buffer[:total]
                self._last_kind = kind
                yield Record(kind, profile, nonce, sequence, offset, payload)
        except QualificationError:
            self._failed = True
            self._buffer.clear()
            raise
        finally:
            self._feeding = False


REPORT_FIELDS = frozenset(('version', 'profile', 'frames', 'raw_bytes', 'terminal_received',
    'complete', 'physical_acceptance', 'host_error', 'result', 'cleanup', 'source_complete',
    'source_submitted_samples', 'stimulus_submitted_samples', 'dma_consumed_samples',
    'peak_submitted', 'elapsed_observed_ms', 'wire_bytes', 'prefix_bytes', 'raw_retained',
    'host_stop_requested', 'host_stop_sent'))


def _finite_number(value):
    return ((type(value) is int and -(1 << 63) <= value < (1 << 64)) or
            (type(value) is float and math.isfinite(value) and abs(value) <= 1e15))


def numeric_json(report):
    if (not isinstance(report, dict) or set(report)-REPORT_FIELDS or
            any(not _finite_number(value) for value in report.values()) or
            report.get('physical_acceptance', 0) != 0):
        raise QualificationError('invalid_numeric_report')
    value = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(value) > 8192:
        raise QualificationError('report_budget')
    return value


def save_summary(report, path):
    """Write only allowlisted numeric metadata to an explicit fresh local file.

    No raw data, directory discovery, overwrite, upload, or platform ACL claim.
    """
    value = numeric_json(report) + '\n'
    try:
        with open(path, 'x', encoding='ascii') as stream:
            stream.write(value)
    except (OSError, TypeError, ValueError):
        raise QualificationError('summary_write_failed') from None


class Capture:
    """Owned bounded PCM buffer; close wipes its used content, not swap/dumps.

    `complete` means checked protocol/terminal completion, never physical pass.
    A stopped/failed capture may have usable RAM data but not passing acceptance.
    """
    def __init__(self, profile, nonce):
        self.target_frames = _profile(profile)
        if not _integer(nonce, (1 << 64)-1, 1):
            raise QualificationError('invalid_nonce')
        self.profile, self.nonce = profile, nonce
        self.raw = bytearray(self.target_frames * 960)
        self.elapsed_ms, self.submitted, self.consumed, self.queues = (array('I') for _ in range(4))
        self.frames = self._sequence = self._wire_bytes = 0
        self.epoch = self.host_error = self.wire_bytes = self.prefix_bytes = 0
        self.start_fields = self.terminal_fields = None
        self.terminal_received = self.complete = self.closed = self._failed = False
        self.host_stop_requested = self.host_stop_sent = False

    @property
    def raw_bytes(self):
        return self.frames * 960

    def accept(self, item):
        try:
            self._accept(item)
        except QualificationError:
            self._failed = True; self.host_error = 1; self.complete = False
            raise

    def _accept(self, item):
        if (self._failed or self.closed or self.terminal_received or not isinstance(item, Record) or
                item.profile != self.profile or item.nonce != self.nonce or
                not isinstance(item.payload, bytes) or not _integer(item.sequence) or
                item.sequence != self._sequence):
            raise QualificationError('record_discontinuity')
        source_cap, stimulus_cap, peak_cap = (0, 0, 0) if self.profile == 1 else (576000, 144000, 512)
        if self.start_fields is None:
            if item.kind != 16 or item.sample_offset != 0 or len(item.payload) != 48:
                raise QualificationError('start_required')
            fields = struct.unpack('<12I', item.payload)
            expected = (24000, 2, 240, self.target_frames, source_cap, stimulus_cap, peak_cap)
            if (fields[0] == 0 or fields[1:8] != expected or
                    not 0 <= fields[8] <= (0 if self.profile == 1 else 5) or
                    fields[9:] != (2000, 10250 if self.profile == 1 else 24250, self.profile-1)):
                raise QualificationError('invalid_start')
            self.epoch, self.start_fields = fields[0], fields
        elif item.kind == 17:
            if len(item.payload) != 992 or self.frames >= self.target_frames:
                raise QualificationError('capture_budget')
            epoch, seq, elapsed, submitted, consumed, queue, a, b = struct.unpack_from('<8I', item.payload)
            if (epoch != self.epoch or seq != self.frames or item.sample_offset != self.frames*240 or
                    a or b or submitted % 240 or not consumed <= submitted <= source_cap or
                    queue not in (*range(9), UNKNOWN) or
                    (self.frames and (elapsed < self.elapsed_ms[-1] or
                     submitted < self.submitted[-1] or consumed < self.consumed[-1]))):
                raise QualificationError('capture_discontinuity')
            self.raw[self.raw_bytes:self.raw_bytes+960] = item.payload[32:]
            for values, value in ((self.elapsed_ms, elapsed), (self.submitted, submitted),
                                  (self.consumed, consumed), (self.queues, queue)):
                values.append(value)
            self.frames += 1
        elif item.kind == 18:
            if len(item.payload) != 48 or item.sample_offset:
                raise QualificationError('invalid_terminal')
            fields = struct.unpack('<12I', item.payload)
            epoch, result, cleanup, count, submitted, stimulus, consumed, elapsed, peak, wire, complete, reserved = fields
            expected_stimulus = sum(max(0, min(submitted, end)-begin) for begin, end in
                                    ((96000,144000),(240000,288000),(432000,480000)))
            if (epoch != self.epoch or result > 12 or cleanup > 2 or count != self.frames or
                    submitted % 240 or not consumed <= submitted <= source_cap or stimulus > stimulus_cap or
                    stimulus != expected_stimulus or (stimulus > 0 and peak == 0) or
                    stimulus > submitted or peak > peak_cap or wire != self._wire_bytes or
                    complete not in (0, 1) or reserved or
                    (self.frames and (elapsed < self.elapsed_ms[-1] or submitted < self.submitted[-1] or
                                      consumed < self.consumed[-1])) or
                    (complete and submitted != source_cap) or
                    (result == 0 and (cleanup != 1 or count != self.target_frames or not complete))):
                raise QualificationError('invalid_terminal')
            self.terminal_fields, self.terminal_received = fields, True
            self.complete = result == 0 and self.host_error == 0
        else:
            raise QualificationError('unexpected_record')
        self._sequence += 1
        self._wire_bytes += HEADER.size + len(item.payload)

    def report(self):
        fields = self.terminal_fields or (self.epoch, 0, 0, self.frames, 0, 0, 0, 0, 0, 0, 0, 0)
        return dict(version=1, profile=self.profile, frames=self.frames, raw_bytes=self.raw_bytes,
            terminal_received=int(self.terminal_received), complete=int(self.complete),
            physical_acceptance=0, host_error=self.host_error, result=fields[1], cleanup=fields[2],
            source_complete=fields[10], source_submitted_samples=fields[4],
            stimulus_submitted_samples=fields[5], dma_consumed_samples=fields[6],
            elapsed_observed_ms=fields[7], peak_submitted=fields[8],
            wire_bytes=self.wire_bytes or self._wire_bytes, prefix_bytes=self.prefix_bytes, raw_retained=0,
            host_stop_requested=int(self.host_stop_requested), host_stop_sent=int(self.host_stop_sent))

    def close(self):
        for at in range(0, len(self.raw), 4096):
            size = min(4096, len(self.raw)-at)
            self.raw[at:at+size] = b'\0'*size
        self.closed = True


class Collector:
    """One-use, explicitly approved injected transport. No open/close/DTR/RTS API.

    read(maximum) -> bounded bytes, None for no data, b'' for EOF;
    write(bytes) -> exact admitted length. The caller owns transport lifetime.
    Cooperative callbacks are required; software timeouts cannot stop hung code.

    Optional stop_requested is a fast, nonblocking strict-bool callback. Stop
    before ARM has no transport effects; later Stop waits for healthy I/O, sends
    at most one STOP and observes terminal for <=3s inside the original deadline.
    host_error 5 means operator Stop, 6 means invalid/raising callback. Separate
    numeric Stop flags preserve the request/admission facts when a fault wins.
    """
    def __init__(self, transport, approved_identity, *, capture_approved=False,
                 audible_approved=False, profile=1, nonce=None, deadline_seconds=45, clock=time.monotonic,
                 stop_requested=None):
        self.target_frames = _profile(profile)
        if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds) or
                not 0 < deadline_seconds <= 45 or not callable(clock) or
                (stop_requested is not None and not callable(stop_requested))):
            raise QualificationError('invalid_host_deadline')
        self.transport, self.identity = transport, approved_identity
        self.capture_approved, self.audible_approved = capture_approved, audible_approved
        self.profile, self.nonce = profile, nonce
        self.duration, self.clock, self._used = deadline_seconds, clock, False
        self.stop_requested = stop_requested

    async def run(self):
        if self._used:
            raise QualificationError('attempt_already_used')
        self._used = True
        if (self.capture_approved is not True or
                (self.profile == 2 and self.audible_approved is not True) or
                not isinstance(self.identity, str) or not 1 <= len(self.identity) <= 128 or
                any(ord(c) < 33 or ord(c) > 126 for c in self.identity) or
                getattr(self.transport, 'identity', None) != self.identity):
            raise QualificationError('explicit_matching_capture_authority_required')
        nonce = self.nonce if self.nonce is not None else (secrets.randbits(64) or 1)
        capture = Capture(self.profile, nonce)
        parser = Parser(); sequence = 0; arm_attempted = stop_attempted = False
        began = self.clock(); last = began; heartbeat = began
        if not _finite_number(began):
            capture.close(); raise QualificationError('invalid_clock')
        deadline = began + self.duration
        loop = asyncio.get_running_loop()
        heartbeat_wall = loop.time()
        wall_deadline = loop.time() + self.duration
        reserve = min(.1, self.duration / 2)
        stop_at, wall_stop_at = deadline-reserve, wall_deadline-reserve
        def requested():
            if capture.host_stop_requested:
                return True
            if self.stop_requested is None:
                return False
            try:
                value = self.stop_requested()
                if type(value) is not bool:
                    raise ValueError
            except Exception:
                capture.host_error = 6
                raise QualificationError('invalid_stop_callback') from None
            if value:
                capture.host_stop_requested = True
                capture.host_error = capture.host_error or 5
            return value

        def accept_data(data):
            records = parser.feed(data)
            try:
                for item in records:
                    capture.accept(item)
                    if capture.terminal_received:
                        break
            finally:
                records.close()
            if capture.terminal_received:
                parser.discard_terminal_tail()

        def checked_time():
            nonlocal last
            now = self.clock()
            if not _finite_number(now) or now < last:
                capture.host_error = 4
                raise QualificationError('invalid_clock')
            last = now
            return now
        async def send(kind):
            nonlocal sequence, arm_attempted, stop_attempted
            if kind != 3 and requested():
                return False
            if kind == 3:
                if stop_attempted:
                    return False
                stop_attempted = True
            # Stop uses the independent trusted wall reserve even if the injected
            # observation clock has regressed. Other writes cannot consume it.
            remaining = (wall_deadline-loop.time() if kind == 3 else
                         min(stop_at-checked_time(), wall_stop_at-loop.time()))
            if remaining <= 0:
                capture.host_error = capture.host_error or 2
                raise QualificationError('host_deadline')
            payload = struct.pack('<4I', 24000, self.target_frames, MAX_PAYLOAD, MAX_WIRE) if kind == 1 else b''
            wire = encode(kind, self.profile, nonce, sequence, 0, payload)
            sequence += 1  # A partial/failed admission is never retried/replayed.
            if kind == 1:
                arm_attempted = True
            admitted = await asyncio.wait_for(self.transport.write(wire), min(.1, remaining))
            if kind == 3 and type(admitted) is int and admitted == len(wire):
                capture.host_stop_sent = True
            if ((kind != 3 and checked_time() >= stop_at) or
                    loop.time() >= (wall_deadline if kind == 3 else wall_stop_at)):
                capture.host_error = capture.host_error or 2
                raise QualificationError('host_deadline')
            if type(admitted) is not int or admitted != len(wire):
                raise QualificationError('control_write_failed')
            return not requested() if kind != 3 else True

        async def observe_stopped_terminal():
            # Observation only, never a new runtime allowance. No heartbeat or
            # additional control writes. Healthy reads retain their full quantum.
            grace_wall = min(wall_deadline, loop.time()+3)
            grace_clock = min(deadline, checked_time()+3)
            while not capture.terminal_received:
                remaining = min(grace_wall-loop.time(), grace_clock-checked_time())
                if remaining < .1:
                    break
                try:
                    data = await asyncio.wait_for(self.transport.read(MAX_CHUNK), .1)
                except asyncio.TimeoutError:
                    continue
                if loop.time() >= grace_wall or checked_time() >= grace_clock:
                    break
                if data is None:
                    await asyncio.sleep(max(0, min(.01, grace_wall-loop.time())))
                    continue
                if data == b'':
                    break
                accept_data(data)
        try:
            armed = await send(1)
            while armed and not capture.terminal_received:
                if requested():
                    break
                now = checked_time()
                if now >= stop_at or loop.time() >= wall_stop_at:
                    capture.host_error = 2; break
                if now-heartbeat >= .1 or loop.time()-heartbeat_wall >= .1:
                    if not await send(2):
                        break
                    heartbeat = checked_time(); now = heartbeat; heartbeat_wall = loop.time()
                    if now >= stop_at or loop.time() >= wall_stop_at:
                        capture.host_error = 2; break
                # Do not cancel an otherwise healthy bounded driver poll merely
                # because this run has less than one full read quantum remaining.
                if min(stop_at-now, wall_stop_at-loop.time()) < .1:
                    capture.host_error = 2; break
                if requested():
                    break
                try:
                    data = await asyncio.wait_for(self.transport.read(MAX_CHUNK), .1)
                except asyncio.TimeoutError:
                    continue
                stopping = requested()
                # Recheck the operation's elapsed budget before accepting its result.
                checked = checked_time()
                if checked >= stop_at or loop.time() >= wall_stop_at:
                    capture.host_error = 2; break
                if data is None:
                    if stopping:
                        break
                    await asyncio.sleep(max(0, min(.01, stop_at-checked, wall_stop_at-loop.time()))); continue
                if data == b'':
                    capture.host_error = capture.host_error or 3; break
                accept_data(data)
                if stopping:
                    break
        except asyncio.CancelledError:
            capture.close(); raise
        except Exception:
            if capture.host_error in (0, 5):
                capture.host_error = 1
        finally:
            if arm_attempted and not capture.terminal_received:
                try:
                    await send(3)
                    if capture.host_stop_requested and capture.host_stop_sent and capture.host_error == 5:
                        await observe_stopped_terminal()
                except asyncio.CancelledError:
                    capture.close(); raise
                except Exception:
                    if capture.host_stop_sent and capture.host_error == 5:
                        capture.host_error = 1  # Invalid terminal/transport during observation.
                    # Independent physical/device heartbeat guards remain essential.
            capture.wire_bytes, capture.prefix_bytes = parser.wire_bytes, parser.prefix_bytes
        # A signal can arrive while parsing the final terminal, after the
        # post-read check. Preserve its receipt but never label manual Stop as
        # a complete run. No extra control is needed after verified terminal.
        try:
            requested()
        except QualificationError:
            pass  # Fixed callback failure code was recorded; no private text.
        capture.complete = capture.complete and capture.host_error == 0
        return capture


def retain_raw(capture, directory, *, approved=False):
    """Distinct explicit POSIX-only retention into a fresh owner-private directory.

    This is not Windows ACL support or a secure-erasure promise. No symlink/file
    overwrite, automatic temp/raw file, upload, transcription or playback occurs.
    """
    if approved is not True or os.name != 'posix' or not isinstance(capture, Capture) or capture.closed:
        raise QualificationError('explicit_supported_retention_required')
    directory = Path(directory)
    if not directory.is_absolute() or directory.name in ('', '.', '..'):
        raise QualificationError('invalid_retention_directory')
    parent_fd = child_fd = None
    created = False; files = []
    try:
        parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        os.mkdir(directory.name, mode=0o700, dir_fd=parent_fd); created = True
        child_fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(child_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise QualificationError('private_retention_unavailable')
        for name, data in (('paired.pcm', memoryview(capture.raw)[:capture.raw_bytes]),
                           ('summary.json', numeric_json(dict(capture.report(), raw_retained=1)).encode())):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child_fd)
            files.append(name)
            try:
                info = os.fstat(fd)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise QualificationError('private_retention_unavailable')
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0: raise QualificationError('retention_write_failed')
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return directory
    except (OSError, QualificationError):
        if child_fd is not None:
            for name in files:
                try: os.unlink(name, dir_fd=child_fd)
                except OSError: pass
        if created and parent_fd is not None:
            try: os.rmdir(directory.name, dir_fd=parent_fd)
            except OSError: pass
        raise QualificationError('retention_failed') from None
    finally:
        if child_fd is not None: os.close(child_fd)
        if parent_fd is not None: os.close(parent_fd)
