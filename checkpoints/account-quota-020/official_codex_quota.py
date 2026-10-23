"""Opt-in, bounded Codex metadata reader; emits only numeric quota candidates.

No binding discovery, credential access, login, refresh, model call or file
publication is implemented. The caller must supply an independently approved
binding: SHA256 of canonical JSON {"email": <exact metadata>, "type":"chatgpt"}
(sorted keys, compact separators, ensure_ascii=True, UTF-8). Identity metadata
exists transiently only. A digest is an account selector, not a secret or proof
of owner approval; deployment must establish approval separately.
"""
import argparse
import hashlib
import hmac
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time


MAX_LINE = 65536
MAX_OUTPUT = 16384
MAX_TIMESTAMP = 2**53 - 1


class ProducerError(ValueError):
    """Fixed failure category, never vendor data or exception text."""


def _reject(*args):
    raise ProducerError('protocol_error')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        _reject()
    return result


def _decode(raw):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=_reject, parse_float=_float)
        if not isinstance(value, dict):
            _reject()
        return value
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        raise ProducerError('protocol_error') from None


def _bounded_number(value, maximum):
    if type(value) not in (int, float):
        raise ProducerError('unusable_quota')
    try:
        valid = math.isfinite(value) and 0 <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ProducerError('unusable_quota')
    return value


def _metrics(data):
    buckets = data.get('rateLimitsByLimitId')
    if buckets is None:
        limits = data.get('rateLimits')
    else:
        if not isinstance(buckets, dict) or len(buckets) > 64:
            raise ProducerError('unusable_quota')
        limits = buckets.get('codex')
    if not isinstance(limits, dict):
        raise ProducerError('unusable_quota')
    rows = []
    for name in ('primary', 'secondary'):
        value = limits.get(name)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ProducerError('unusable_quota')
        used = value.get('usedPercent')
        if used is not None:
            used = _bounded_number(used, 100)
        reset = value.get('resetsAt')
        if reset is not None:
            if type(reset) is not int:
                raise ProducerError('unusable_quota')
            reset = _bounded_number(reset, MAX_TIMESTAMP)
        row = {'window': 'codex:' + name, 'used_percent': used, 'resets_at': reset}
        duration = value.get('windowDurationMins')
        if duration is not None:
            if type(duration) is not int or not 1 <= duration <= 4294967295:
                raise ProducerError('unusable_quota')
            row['window_minutes'] = duration
        rows.append(row)
    if not any(row['used_percent'] is not None for row in rows):
        raise ProducerError('unusable_quota')
    return rows


class CodexQuotaClient:
    """Own exactly one stdio child and check explicit identity around each read."""

    def __init__(self, executable, *, expected_binding=None, rpc_timeout=5):
        if (not isinstance(executable, str) or not Path(executable).is_absolute() or
                '\x00' in executable or not isinstance(expected_binding, str) or
                re.fullmatch(r'[0-9a-f]{64}', expected_binding) is None or
                type(rpc_timeout) not in (int, float) or
                not math.isfinite(rpc_timeout) or not 0 < rpc_timeout <= 30):
            raise ProducerError('invalid_arguments')
        self.executable = executable
        self.expected_binding = expected_binding
        self.rpc_timeout = rpc_timeout
        self.child = None
        self.reader = None
        self.writer = None
        self.stop = threading.Event()
        self.lines = queue.Queue(maxsize=8)
        self.outbound = queue.Queue(maxsize=1)
        self.writing = threading.Event()
        self.next_id = 0
        self.closed = False

    def __enter__(self):
        if self.child is not None or self.closed:
            raise ProducerError('process_failed')
        try:
            try:
                self.child = subprocess.Popen(
                    [self.executable, 'app-server'], shell=False,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=0)
            except OSError:
                raise ProducerError('process_start_failed') from None
            self.reader = threading.Thread(target=self._read_lines,
                                           name='codex-quota-stdio', daemon=True)
            self.reader.start()
            self.writer = threading.Thread(target=self._write_messages,
                                           name='codex-quota-write', daemon=True)
            self.writer.start()
            self._rpc('initialize', {'clientInfo': {'name': 'quota_reader', 'version': '1'}},
                      timeout=max(5, self.rpc_timeout))
            self._send({'method': 'initialized'})
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, tb):
        failed = self.close()
        if failed and exc_type is None:
            raise ProducerError('process_failed')

    def _enqueue(self, item):
        while not self.stop.is_set():
            try:
                self.lines.put(item, timeout=0.05)
                return
            except queue.Full:
                pass

    def _read_lines(self):
        pending = bytearray()
        try:
            while not self.stop.is_set():
                chunk = self.child.stdout.read(4096)
                if not chunk:
                    self._enqueue(('error', 'process_failed'))
                    return
                for part in chunk.splitlines(keepends=True):
                    pending.extend(part)
                    if len(pending) > MAX_LINE:
                        self._enqueue(('error', 'protocol_error'))
                        return
                    if pending.endswith(b'\n'):
                        self._enqueue(('line', bytes(pending)))
                        pending.clear()
        except (OSError, ValueError):
            self._enqueue(('error', 'process_failed'))

    def _write_messages(self):
        while not self.stop.is_set():
            try:
                raw, completed, failure = self.outbound.get(timeout=0.05)
            except queue.Empty:
                continue
            self.writing.set()
            try:
                pending = memoryview(raw)
                while pending:
                    written = self.child.stdin.write(pending)
                    if not written:
                        raise OSError()
                    pending = pending[written:]
                self.child.stdin.flush()
            except (OSError, ValueError):
                failure.append(True)
            finally:
                self.writing.clear()
                completed.set()

    def _send(self, message, *, timeout=None):
        raw = json.dumps(message, ensure_ascii=True, allow_nan=False,
                         separators=(',', ':')).encode('ascii') + b'\n'
        completed = threading.Event()
        failure = []
        # Pipe writes can block too: a server may flood requests without reading
        # our refusals. Keep the caller's deadline and terminate our child before
        # closing a pipe that still has an active write during cleanup.
        try:
            self.outbound.put_nowait((raw, completed, failure))
        except queue.Full:
            raise ProducerError('protocol_error') from None
        if not completed.wait(self.rpc_timeout if timeout is None else max(0, timeout)):
            raise ProducerError('timeout')
        if failure:
            raise ProducerError('process_failed')

    def _rpc(self, method, params=None, *, timeout=None):
        if method not in ('initialize', 'account/read', 'account/rateLimits/read'):
            raise ProducerError('protocol_error')
        self.next_id += 1
        request_id = self.next_id
        request = {'id': request_id, 'method': method}
        if params is not None:
            request['params'] = params
        deadline = time.monotonic() + (self.rpc_timeout if timeout is None else timeout)
        self._send(request, timeout=deadline - time.monotonic())
        events = 0
        requests = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProducerError('timeout')
            try:
                kind, raw = self.lines.get(timeout=remaining)
            except queue.Empty:
                raise ProducerError('timeout') from None
            if kind == 'error':
                raise ProducerError(raw)
            message = _decode(raw)
            if 'jsonrpc' in message and message['jsonrpc'] != '2.0':
                _reject()
            if 'method' in message:
                events += 1
                if (not isinstance(message['method'], str) or
                        not 1 <= len(message['method']) <= 128 or
                        'result' in message or 'error' in message or events > 256):
                    _reject()
                if 'id' in message:
                    requests += 1
                    server_id = message['id']
                    if (requests > 16 or not (
                            type(server_id) is int and abs(server_id) <= MAX_TIMESTAMP or
                            isinstance(server_id, str) and 1 <= len(server_id) <= 128)):
                        _reject()
                    self._send({'id': server_id, 'error': {'code': -32601,
                               'message': 'Unsupported server request'}},
                               timeout=deadline - time.monotonic())
                continue
            if (type(message.get('id')) is not int or message['id'] != request_id or
                    ('result' in message) == ('error' in message)):
                _reject()
            if 'error' in message:
                raise ProducerError('rpc_error')
            if not isinstance(message['result'], dict):
                _reject()
            return message['result']

    def _verify_account(self):
        data = self._rpc('account/read', {'refreshToken': False})
        account = data.get('account')
        if account is None:
            raise ProducerError('authentication_required')
        if (not isinstance(account, dict) or account.get('type') != 'chatgpt' or
                'requiresOpenaiAuth' in data and data['requiresOpenaiAuth'] is not True):
            raise ProducerError('account_type_unsupported')
        email = account.get('email')
        if (not isinstance(email, str) or not 1 <= len(email) <= 320 or
                email != email.strip() or any(ord(c) < 32 for c in email)):
            raise ProducerError('account_identity_unavailable')
        identity = json.dumps({'type': 'chatgpt', 'email': email}, sort_keys=True,
                              separators=(',', ':'), ensure_ascii=True).encode('utf-8')
        binding = hashlib.sha256(identity).hexdigest()
        if not hmac.compare_digest(binding, self.expected_binding):
            raise ProducerError('account_changed')

    def read(self):
        if self.child is None or self.closed:
            raise ProducerError('process_failed')
        try:
            self._verify_account()
            data = self._rpc('account/rateLimits/read')
            rows = _metrics(data)
            self._verify_account()
            fetched_at = int(time.time())
            if not 0 <= fetched_at <= MAX_TIMESTAMP:
                raise ProducerError('protocol_error')
            snapshot = {'version': 1, 'scope': 'account', 'provider': 'codex',
                        'source': 'codex_app', 'fetched_at': fetched_at, 'metrics': rows}
            if len(json.dumps(snapshot, allow_nan=False).encode('ascii')) > MAX_OUTPUT:
                raise ProducerError('unusable_quota')
            return snapshot
        except BaseException:
            self.close()
            raise

    def close(self):
        """Close pipes, then bound waits and reap only our Popen process handle."""
        if self.closed:
            return False
        self.closed = True
        self.stop.set()
        if self.child is None:
            return False
        forced = False
        try:
            if self.writing.is_set():
                forced = True
                self.child.terminate()
            else:
                try:
                    self.child.stdin.close()
                except OSError:
                    pass
            try:
                self.child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                forced = True
                self.child.terminate()
                try:
                    self.child.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=1)
            return not forced and self.child.returncode != 0
        finally:
            self.child.stdin.close()
            self.child.stdout.close()
            if self.writer is not None:
                self.writer.join(timeout=1)
            if self.reader is not None:
                self.reader.join(timeout=1)


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ProducerError('invalid_arguments')


def main(argv=None):
    try:
        parser = _Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument('--codex', required=True)
        parser.add_argument('--account-binding', required=True)
        parser.add_argument('--rpc-timeout', type=float, default=5)
        parser.add_argument('--interval', type=float, default=60)
        parser.add_argument('--count', type=int, default=1)
        args = parser.parse_args(argv)
        if (not math.isfinite(args.interval) or not 60 <= args.interval <= 86400 or
                not 1 <= args.count <= 10000):
            raise ProducerError('invalid_arguments')
        with CodexQuotaClient(args.codex, expected_binding=args.account_binding,
                              rpc_timeout=args.rpc_timeout) as client:
            for index in range(args.count):
                if index:
                    time.sleep(args.interval)
                snapshot = client.read()
                print(json.dumps(snapshot, ensure_ascii=True, allow_nan=False,
                                 separators=(',', ':')), flush=True)
        return 0
    except ProducerError as error:
        category = str(error)
    except KeyboardInterrupt:
        category = 'cancelled'
    except Exception:
        category = 'internal_error'
    print('official_codex_quota: ' + category, file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
