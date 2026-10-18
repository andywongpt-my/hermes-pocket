"""Pure official-quota JSON normalizers and private atomic stdin publication.

No account access, credential discovery, subprocesses, clients or network.
Callers supply the actual observation time. --cached-event preserves the old
time for unchanged quota data; authoritative fresh observations may advance it.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .quota_snapshots import (MAX_FILE_BYTES, MAX_INPUT_BYTES, MAX_FUTURE_SECONDS, SOURCES, PrivateDirectory,
    QuotaError, _private, _revision, loads_json, payload_key, read_private,
    require_update, validate_snapshot)
from .usage import MAX_TIMESTAMP, codex_limits, claude_limits, number


def _snapshot(provider, rows, fetched_at):
    metrics = []
    for row in rows:
        metric = {'window': row['window'], 'used_percent': row['value'],
                  'resets_at': row['resets_at']}
        if row.get('window_minutes') is not None:
            metric['window_minutes'] = row['window_minutes']
        metrics.append(metric)
    return validate_snapshot({'version': 1, 'scope': 'account', 'provider': provider,
        'source': SOURCES[provider], 'fetched_at': fetched_at, 'metrics': metrics})


def codex_snapshot(data, *, fetched_at):
    """Normalize supplied Codex app limits, retaining bucket/window identities."""
    try:
        if not isinstance(data, dict): raise QuotaError('invalid_input')
        buckets = data.get('rateLimitsByLimitId')
        if buckets is not None and (not isinstance(buckets, dict) or
                any(not isinstance(key, str) for key in buckets)):
            raise QuotaError('invalid_input')
        return _snapshot('codex', codex_limits(data, fetched_at), fetched_at)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QuotaError('invalid_input') from None


def claude_snapshot(data, *, fetched_at):
    """Normalize rate_limits only; context/token/session data is never quota."""
    try:
        if (not isinstance(data, dict) or data.get('rate_limits') is not None and
                not isinstance(data['rate_limits'], dict)):
            raise QuotaError('invalid_input')
        rows = claude_limits(data, fetched_at)
        for row in rows:
            row['window_minutes'] = {'five_hour': 300, 'seven_day': 10080}[row['window']]
        return _snapshot('claude', rows, fetched_at)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QuotaError('invalid_input') from None


def _output_path(output, provider):
    path = Path(output)
    if (not path.is_absolute() or path.name != provider + '.json' or
            not path.parent.name.startswith('.') or
            not path.parent.name.endswith('.account-quota-v1') or
            len(path.parent.name) <= len('..account-quota-v1') or
            '..' in path.parts):
        raise QuotaError('unsafe_output')
    return path


@contextmanager
def _publication_lock(directory):
    """Serialize cooperating publishers without persistent lock files.

    POSIX uses the already-open private directory; Windows uses a named mutex
    scoped to that directory's file identity. A busy writer fails safely and
    may retry later. Abrupt process exit releases either OS lock automatically.
    """
    if os.name == 'posix':
        import fcntl
        try:
            fcntl.flock(directory.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise QuotaError('busy_output') from None
        try:
            yield
        finally:
            fcntl.flock(directory.fd, fcntl.LOCK_UN)
    elif os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        device, inode = directory.chain[-1][1]
        handle = kernel.CreateMutexW(None, False, f'Global\\PocketQuotaV1_{device:x}_{inode:x}')
        if not handle:
            raise QuotaError('busy_output')
        acquired = False
        try:
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 128)
            if not acquired:
                raise QuotaError('busy_output')
            yield
        finally:
            if acquired: kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)
    else:
        raise QuotaError('unsupported_output')


def publish_snapshot(data, output, *, cached_event=False, clock=None):
    """Publish validated numeric data in a pre-existing private quota directory.

    Raw vendor input is never written. Exclusive same-directory temporary files
    are flushed and fsynced before replacement; failed attempts remove the temp.
    Excessive future candidates fail before output access. A previously poisoned
    future file may be recovered only by nonfuture authoritative data younger
    than the 300-second TTL; valid observations retain normal replay protection.
    """
    current = validate_snapshot(data)
    now = number(time.time() if clock is None else clock(), MAX_TIMESTAMP)
    if current['fetched_at'] > now + MAX_FUTURE_SECONDS:
        raise QuotaError('future_snapshot')
    path = _output_path(output, current['provider'])
    with PrivateDirectory(path.parent) as directory, _publication_lock(directory):
        try:
            previous, previous_info = read_private(directory, path.name,
                provider=current['provider'], include_info=True)
        except FileNotFoundError:
            previous = None; previous_info = None
        replay_previous = previous
        if previous is not None and previous['fetched_at'] > now + MAX_FUTURE_SECONDS:
            if cached_event or not now - 300 < current['fetched_at'] <= now:
                raise QuotaError('future_snapshot')
            # Keep previous_info for the exact revision check before replace.
            # Only invalid future data loses replay precedence, never a valid
            # recent observation or a tolerated <=30-second future observation.
            replay_previous = None
        require_update(replay_previous, current)
        if cached_event and previous is not None and payload_key(current) == payload_key(previous):
            current['fetched_at'] = previous['fetched_at']
        raw = json.dumps(current, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
        if len(raw) > MAX_FILE_BYTES:
            raise QuotaError('invalid_snapshot')
        temporary = '.quota-' + uuid.uuid4().hex + '.tmp'
        created = False
        try:
            directory.verify()
            fd = directory.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            created = True
            try:
                info = os.fstat(fd); _private(info)
                directory.verify()
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(raw); stream.flush(); os.fsync(fd)
                if _revision(directory.info(temporary)) != _revision(os.fstat(fd)):
                    raise QuotaError('unsafe_output')
            finally:
                os.close(fd)
            directory.verify()
            try:
                existing = directory.info(path.name); _private(existing)
            except FileNotFoundError:
                existing = None
            if (existing is None) != (previous_info is None) or (existing is not None and
                    _revision(existing) != _revision(previous_info)):
                raise QuotaError('outdated_snapshot')
            directory.replace(temporary, path.name)
            created = False
            if directory.fd is not None:
                os.fsync(directory.fd)
        finally:
            if created:
                directory.unlink(temporary)
    return current


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise QuotaError('invalid_input')


def main(argv=None):
    try:
        parser = _SafeParser(description='Publish supplied numeric account-quota metadata.')
        parser.add_argument('--provider', required=True, choices=tuple(SOURCES))
        parser.add_argument('--source', required=True, choices=tuple(SOURCES.values()))
        parser.add_argument('--fetched-at', required=True, type=int)
        parser.add_argument('--output', required=True)
        parser.add_argument('--cached-event', action='store_true')
        args = parser.parse_args(argv)
        if SOURCES[args.provider] != args.source:
            raise QuotaError('invalid_input')
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        data = loads_json(raw, maximum=MAX_INPUT_BYTES)
        if isinstance(data, dict) and 'version' in data:
            current = validate_snapshot(data, provider=args.provider)
            if current['fetched_at'] != args.fetched_at:
                raise QuotaError('invalid_input')
        else:
            normalizer = {'codex': codex_snapshot, 'claude': claude_snapshot}[args.provider]
            current = normalizer(data, fetched_at=args.fetched_at)
        publish_snapshot(current, args.output, cached_event=args.cached_event)
        return 0
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print('quota_export: invalid_input', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
