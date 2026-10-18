"""Bounded, private account-quota data; no credentials, collectors or network.

The only SQLite operation locates a target's database filename. Snapshot files
are optional and the reader never creates them. All failures use fixed codes.
"""
from pathlib import Path
import json
import math
import os
import stat
import threading
import time

from .usage import MAX_TIMESTAMP, epoch, nullable_number, number

MAX_FILE_BYTES = 16384
MAX_INPUT_BYTES = 65536
MAX_METRICS = 64
REFRESH_SECONDS = 15
MAX_FUTURE_SECONDS = 30
SOURCES = {'codex': 'codex_app', 'claude': 'claude_code'}
LABELS = {'codex': 'Codex app account limits', 'claude': 'Claude Code account limits'}
_ENVELOPE = {'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'}
_METRIC = {'window', 'used_percent', 'resets_at'}


class QuotaError(ValueError):
    """A fixed safe quota failure, with no vendor data or filesystem paths."""


def validate_snapshot(data, *, provider=None):
    """Return an independent, strict v1 numeric snapshot, normalizing resets."""
    try:
        if not isinstance(data, dict) or set(data) != _ENVELOPE:
            raise QuotaError('invalid_snapshot')
        owner = data['provider']
        if (not isinstance(owner, str) or owner not in SOURCES or
                provider is not None and owner != provider or
                data['source'] != SOURCES[owner] or
                type(data['version']) is not int or data['version'] != 1 or
                data['scope'] != 'account'):
            raise QuotaError('invalid_snapshot')
        fetched_at = data['fetched_at']
        if type(fetched_at) is not int:
            raise QuotaError('invalid_snapshot')
        number(fetched_at, MAX_TIMESTAMP)
        rows = data['metrics']
        if not isinstance(rows, list) or len(rows) > MAX_METRICS:
            raise QuotaError('invalid_snapshot')
        result = []; windows = set()
        for row in rows:
            if (not isinstance(row, dict) or not _METRIC <= set(row) or
                    set(row) - (_METRIC | {'window_minutes'})):
                raise QuotaError('invalid_snapshot')
            window = row['window']
            if (not isinstance(window, str) or not 1 <= len(window) <= 48 or
                    any(not 32 <= ord(c) < 127 for c in window) or window in windows):
                raise QuotaError('invalid_snapshot')
            windows.add(window)
            reset = row['resets_at']
            metric = {'window': window,
                      'used_percent': nullable_number(row['used_percent'], 100),
                      'resets_at': None if reset is None else epoch(reset)}
            if 'window_minutes' in row:
                duration = row['window_minutes']
                if type(duration) is not int or not 1 <= duration <= 4294967295:
                    raise QuotaError('invalid_snapshot')
                metric['window_minutes'] = duration
            result.append(metric)
        return {'version': 1, 'scope': 'account', 'provider': owner,
                'source': SOURCES[owner], 'fetched_at': fetched_at, 'metrics': result}
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise QuotaError('invalid_snapshot') from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuotaError('invalid_input')
        result[key] = value
    return result


def _constant(value):
    raise QuotaError('invalid_input')


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise QuotaError('invalid_input')
    return result


def loads_json(raw, *, maximum):
    """Strict bounded UTF-8 JSON, including duplicate and nonfinite rejection."""
    try:
        if not isinstance(raw, bytes) or len(raw) > maximum:
            raise QuotaError('invalid_input')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=_constant, parse_float=_float)
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        raise QuotaError('invalid_input') from None


def loads_snapshot(raw, *, provider=None):
    return validate_snapshot(loads_json(raw, maximum=MAX_FILE_BYTES), provider=provider)


def payload_key(snapshot):
    """Quota meaning without observation time or dictionary/window ordering."""
    return (snapshot['provider'], snapshot['source'], tuple(sorted(
        (row['window'], row['used_percent'], row['resets_at'], row.get('window_minutes'))
        for row in snapshot['metrics'])))


def require_update(previous, current):
    """Older observations and same-time changes must never displace data."""
    if previous is not None and (current['fetched_at'] < previous['fetched_at'] or
            current['fetched_at'] == previous['fetched_at'] and
            payload_key(current) != payload_key(previous)):
        raise QuotaError('outdated_snapshot')


def _reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) &
                getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))


def _identity(info):
    return info.st_dev, info.st_ino


def _revision(info):
    return (_identity(info), info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _private(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or _reparse(info) or
            not directory and info.st_nlink != 1):
        raise QuotaError('unsafe_source')
    if os.name == 'posix' and (info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)):
        raise QuotaError('unsafe_source')


def _directory_chain(path):
    chain = []
    for parent in (*reversed(path.parents), path):
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or _reparse(info):
            raise QuotaError('unsafe_source')
        chain.append((parent, _identity(info)))
    _private(info, directory=True)
    return chain


class PrivateDirectory:
    """Guard an existing directory; anchor operations with dirfd on POSIX.

    Before reading bytes, revalidate descriptor identity and every ancestor.
    Windows reparse checks and file identities apply; POSIX also enforces uid,
    exact modes and no-follow/nonblocking opens. No directory is ever created.
    """
    def __init__(self, path):
        self.path = Path(os.path.abspath(path))
        self.fd = None

    def __enter__(self):
        self.chain = _directory_chain(self.path)
        try:
            if os.name == 'posix':
                self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY |
                                  os.O_NOFOLLOW | os.O_NONBLOCK)
                if _identity(os.fstat(self.fd)) != self.chain[-1][1]:
                    raise QuotaError('unsafe_source')
            self.verify()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd); self.fd = None

    def verify(self):
        if _directory_chain(self.path) != self.chain:
            raise QuotaError('unsafe_source')
        if self.fd is not None:
            info = os.fstat(self.fd); _private(info, directory=True)
            if _identity(info) != self.chain[-1][1]:
                raise QuotaError('unsafe_source')

    def info(self, name):
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return os.lstat(self.path / name)

    def open(self, name, flags, mode=0o600):
        flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        if self.fd is not None:
            return os.open(name, flags, mode, dir_fd=self.fd)
        return os.open(self.path / name, flags, mode)

    def replace(self, old, new):
        if self.fd is not None:
            os.replace(old, new, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        else:
            os.replace(self.path / old, self.path / new)

    def unlink(self, name):
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            os.unlink(self.path / name)


def read_private(directory, name, *, provider, include_info=False):
    """Read at most 16385 bytes and reject races before accepting any data."""
    directory.verify()
    before = directory.info(name); _private(before)
    if before.st_size > MAX_FILE_BYTES:
        raise QuotaError('invalid_snapshot')
    fd = directory.open(name, os.O_RDONLY)
    try:
        opened = os.fstat(fd); _private(opened)
        if _revision(opened) != _revision(before):
            raise QuotaError('unsafe_source')
        directory.verify()
        if _revision(directory.info(name)) != _revision(before):
            raise QuotaError('unsafe_source')
        chunks = []; total = 0
        while total <= MAX_FILE_BYTES:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - total)
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
        after = os.fstat(fd); _private(after)
        directory.verify()
        if (_revision(after) != _revision(before) or total != after.st_size or
                _revision(directory.info(name)) != _revision(before)):
            raise QuotaError('unsafe_source')
        result = loads_snapshot(b''.join(chunks), provider=provider)
        return (result, before) if include_info else result
    finally:
        os.close(fd)


class QuotaSnapshots:
    """Two optional numeric sources for one target, refreshed at most every 15s."""
    def __init__(self, target, store, cache, *, clock=time.time):
        # Validate scope without configuring an absent source.
        cache._key(target, 'codex')
        filename = store.db.execute('PRAGMA database_list').fetchone()[2]
        path = Path(filename) if filename else None
        self.directory = (path.parent / ('.' + path.name + '.account-quota-v1')
                          if path is not None else None)
        self.target = target; self.cache = cache; self.clock = clock
        self._checked_at = None; self._accepted = {}; self._seen = set()
        self._lock = threading.Lock()

    def refresh(self):
        if self.directory is None or not self._lock.acquire(blocking=False):
            return
        try:
            now = number(self.clock(), MAX_TIMESTAMP)
            if self._checked_at is not None and now - self._checked_at < REFRESH_SECONDS:
                return
            self._checked_at = now
            for provider in SOURCES:
                try:
                    with PrivateDirectory(self.directory) as directory:
                        current = read_private(directory, provider + '.json', provider=provider)
                    if current['fetched_at'] > now + MAX_FUTURE_SECONDS:
                        raise QuotaError('future_snapshot')
                    require_update(self._accepted.get(provider), current)
                    rows = []
                    for metric in current['metrics']:
                        used = metric['used_percent']
                        row = {'category': 'subscription', 'window': metric['window'],
                               'value': None if used is None else 100 - used,
                               'unit': 'percent_remaining', 'fetched_at': current['fetched_at'],
                               'resets_at': metric['resets_at'], 'source': LABELS[provider]}
                        if 'window_minutes' in metric:
                            row['window_minutes'] = metric['window_minutes']
                        rows.append(row)
                    self.cache.put(self.target, provider, rows,
                                   fetched_at=current['fetched_at'], source=LABELS[provider])
                    self._accepted[provider] = current; self._seen.add(provider)
                except FileNotFoundError:
                    if provider in self._seen:
                        self.cache.failed(self.target, provider, source=LABELS[provider])
                except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                    self._seen.add(provider)
                    self.cache.failed(self.target, provider, source=LABELS[provider])
        finally:
            self._lock.release()
