"""Disabled-by-default synthetic sink contract; no listener or provider imports.

An existing authenticated TLS dispatcher may opt in after explicit approval.
Call its authorization gate before reading a bounded body, then handle().
This module never binds a socket, stores a body, or calls another subsystem.
"""
import re
import threading
import time

PATH = '/diagnostics/transport'
SIZES = (512, 1024, 1360, 1440, 2048, 2756)
MAX_REQUESTS = 18
MAX_BYTES = sum(SIZES) * 3
MAX_RECEIVED_BYTES = MAX_REQUESTS * max(SIZES)
DURATION_SECONDS = 120


def body_for(sequence):
    if type(sequence) is not int or not 0 <= sequence < MAX_REQUESTS:
        raise ValueError('invalid synthetic sequence')
    size = SIZES[sequence % len(SIZES)]
    prefix = ('{"sequence":%d,"padding":"' % sequence).encode('ascii')
    return prefix + b'P' * (size - len(prefix) - 2) + b'"}'



class SyntheticTransportSink:
    """One explicit, identity-scoped, time/byte/request-bounded arm operation."""
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._device = None
        self._deadline = 0.0
        self.count = 0
        self.accepted_bytes = 0
        self.attempts = self.received_bytes = 0

    def arm(self, authenticated_device):
        if not isinstance(authenticated_device, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', authenticated_device):
            raise ValueError('invalid device identity')
        with self._lock:
            self._device = authenticated_device
            self._deadline = self._clock() + DURATION_SECONDS
            self.count = self.accepted_bytes = 0
            self.attempts = self.received_bytes = 0

    def disarm(self):
        with self._lock:
            self._device = None

    def _gate(self, authenticated_device):
        if self._device is None:
            return 404
        if authenticated_device != self._device:
            return 403
        if self._clock() >= self._deadline:
            return 410
        if self.attempts >= MAX_REQUESTS or self.received_bytes >= MAX_RECEIVED_BYTES:
            return 429
        return 200

    def admit(self, authenticated_device):
        """Run after existing TLS/Bearer auth, before reading any request body."""
        with self._lock:
            return self._gate(authenticated_device)

    def handle(self, authenticated_device, body):
        with self._lock:
            code = self._gate(authenticated_device)
            if code != 200:
                return code, {'error': code}
            self.attempts += 1
            if not isinstance(body, bytes):
                return 400, {'error': 400}
            self.received_bytes += len(body)
            if len(body) > max(SIZES):
                return 413, {'error': 413}
            match = re.fullmatch(rb'\{"sequence":([0-9]{1,2}),"padding":"P*"\}', body)
            if not match:
                return 400, {'error': 400}
            sequence = int(match.group(1))
            if not 0 <= sequence < MAX_REQUESTS:
                return 400, {'error': 400}
            if sequence != self.count:
                return 409, {'error': 409}
            if body != body_for(sequence):
                return 400, {'error': 400}
            if self.accepted_bytes + len(body) > MAX_BYTES:
                return 429, {'error': 429}
            self.count += 1
            self.accepted_bytes += len(body)
            return 200, {'sequence': sequence, 'accepted': len(body)}


def dispatch_synthetic(sink, authenticated_device, method, path, lengths, transfer_encoding, read):
    """Optional existing-TLS dispatcher hook. Never installs a route or listener.

    Caller must first complete its existing Bearer/device authentication.
    Reject framing/gate failures before reading, and close that HTTP connection.
    The existing socket read timeout must remain in effect.
    """
    if path != PATH:
        return None
    code = sink.admit(authenticated_device)
    if code != 200:
        return code, {'error': code}
    if method != 'POST':
        return 405, {'error': 405}
    if transfer_encoding is not None or len(lengths) != 1:
        return 400, {'error': 400}
    text = lengths[0]
    if not isinstance(text, str) or not re.fullmatch(r'[0-9]{1,4}', text):
        return 400, {'error': 400}
    length = int(text)
    if length not in SIZES:
        return 413, {'error': 413}
    body = read(length)
    if not isinstance(body, bytes) or len(body) != length:
        # A truncated/malformed attempt also consumes its arm budget.
        sink.handle(authenticated_device, b'')
        return 400, {'error': 400}
    return sink.handle(authenticated_device, body)
