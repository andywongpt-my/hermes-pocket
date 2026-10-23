"""Account-bound passive AW observer. Raw client/auth input exists only in memory.

Only main invokes the exact configured auth-status and SSH commands. Tests inject
both functions. No login, refresh, model call, credential discovery, or settings
editing is provided. All source dependencies execute from verified bytes.
"""
import argparse
import builtins
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import types
import uuid

PROFILE = 'codex_claude_passive_v1'
MAX_INPUT = 65536
MAX_EPOCH = 253402300799
CONFIG_KEYS = {'version', 'profile', 'expected_account_sha256', 'auth_argv',
               'auth_file_pins', 'source_pins', 'state_directory', 'output',
               'relay_lock_directory', 'audit_path', 'receiver_argv'}
ERROR_CODES = {'invalid_input', 'invalid_config', 'unsafe_file', 'pin_mismatch',
               'unsafe_source', 'invalid_clock', 'invalid_auth', 'account_mismatch',
               'invalid_snapshot', 'invalid_receipt', 'audit_failed', 'callback_failed',
               'busy', 'send_failed', 'process_failed', 'observer_failed'}
BASE = Path(__file__).absolute().parent.parent
SOURCES = {
    'wrapper': Path(__file__).absolute(),
    'adapter': BASE / 'claude_statusline_quota_020.py',
    'callback': BASE / 'claude-callback-020' / 'claude_callback_020.py',
    'pocket': BASE / 'backend' / 'pocket' / '__init__.py',
    'usage': BASE / 'backend' / 'pocket' / 'usage.py',
    'quota_snapshots': BASE / 'backend' / 'pocket' / 'quota_snapshots.py',
    'quota_export': BASE / 'backend' / 'pocket' / 'quota_export.py',
}


class ObserverError(ValueError):
    """Only locally selected fixed error codes may reach an audit."""


def _require(condition, code):
    if not condition:
        raise ObserverError(code)


def _error_code(error):
    return str(error) if type(error) is ObserverError and str(error) in ERROR_CODES else 'observer_failed'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return (type(value) is str and len(value) == 64 and
            all(c in '0123456789abcdef' for c in value))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'invalid_input')
        result[key] = value
    return result


def _constant(value):
    raise ObserverError('invalid_input')


def _float(value):
    result = float(value)
    _require(math.isfinite(result), 'invalid_input')
    return result


def _json(raw, maximum=MAX_INPUT):
    _require(type(raw) is bytes and len(raw) <= maximum, 'invalid_input')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                      parse_constant=_constant, parse_float=_float)


def _path(value):
    _require(type(value) is str and 1 <= len(value) <= 4096 and
             not any(ord(c) < 32 for c in value), 'invalid_config')
    path = Path(value)
    _require(path.is_absolute() and '..' not in path.parts, 'invalid_config')
    return path


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _reparse(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 1024)


def _revision(info):
    # Windows lstat synthesizes executable bits from .exe; fstat does not.
    mode = info.st_mode & ~0o111 if os.name == 'nt' else info.st_mode
    return (info.st_dev, info.st_ino, mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _file(info):
    _require(stat.S_ISREG(info.st_mode) and not _reparse(info) and
             info.st_nlink == 1, 'unsafe_file')


def _chain(path):
    result = []
    for parent in (*reversed(path.parents), path):
        info = os.lstat(parent)
        _require(stat.S_ISDIR(info.st_mode) and not _reparse(info), 'unsafe_file')
        result.append((str(parent), info.st_dev, info.st_ino))
    return result


def _checked_file(path, *, expected=None, maximum=2 * 1024 * 1024,
                  keep=True, private=False):
    """No-follow regular file, bounded read, and before/after identity checks."""
    path = _path(str(path))
    chain = _chain(path.parent)
    before = os.lstat(path)
    _file(before)
    _require(before.st_size <= maximum, 'unsafe_file')
    if private and os.name == 'posix':
        _require(before.st_uid == os.geteuid() and
                 stat.S_IMODE(before.st_mode) == 0o600, 'unsafe_file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    flags |= getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        _file(opened)
        _require(_revision(opened) == _revision(before) and
                 _chain(path.parent) == chain, 'unsafe_file')
        chunks, total, hasher = [], 0, hashlib.sha256()
        while total <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            hasher.update(chunk)
            if keep:
                chunks.append(chunk)
        _require(total <= maximum and total == opened.st_size and
                 _revision(os.fstat(fd)) == _revision(before) and
                 _revision(os.lstat(path)) == _revision(before) and
                 _chain(path.parent) == chain, 'unsafe_file')
        _require(expected is None or hasher.hexdigest() == expected, 'pin_mismatch')
        return b''.join(chunks) if keep else hasher.hexdigest()
    finally:
        os.close(fd)


def _argv(value):
    _require(type(value) is list and 1 <= len(value) <= 32 and
             all(type(x) is str and 1 <= len(x) <= 4096 and
                 not any(ord(c) < 32 for c in x) for x in value), 'invalid_config')
    _path(value[0])
    return value


def _pins(value):
    _require(type(value) is dict and 1 <= len(value) <= 16, 'invalid_config')
    result = {}
    for path, digest in value.items():
        _require(_hash(digest), 'invalid_config')
        key = _key(_path(path))
        _require(key not in result, 'invalid_config')
        result[key] = digest
    return result


def _config(value):
    _require(type(value) is dict and set(value) == CONFIG_KEYS, 'invalid_config')
    # Freeze caller-owned containers before any injected callback can run.
    value = _json(canonical(value))
    _require(type(value['version']) is int and value['version'] == 1 and
             value['profile'] == PROFILE and _hash(value['expected_account_sha256']) and
             len(set(value['expected_account_sha256'])) > 1, 'invalid_config')
    auth = _argv(value['auth_argv'])
    _require(len(auth) in (3, 4) and auth[-2:] == ['auth', 'status'], 'invalid_config')
    if len(auth) == 4:
        _require(Path(auth[0]).name.lower() in ('node', 'node.exe') and
                 _path(auth[1]).suffix.lower() in ('.js', '.mjs', '.cjs'), 'invalid_config')
    auth_pins = _pins(value['auth_file_pins'])
    _require(set(auth_pins) == {_key(_path(x)) for x in auth[:-2]}, 'invalid_config')
    source_pins = _pins(value['source_pins'])
    _require(set(source_pins) == {_key(x) for x in SOURCES.values()}, 'invalid_config')
    for field in ('state_directory', 'output', 'relay_lock_directory', 'audit_path'):
        _path(value[field])
    output = Path(value['output'])
    _require(output.name == 'claude.json' and output.parent.name.startswith('.') and
             output.parent.name.endswith('.account-quota-v1') and
             len(output.parent.name) > len('..account-quota-v1'), 'invalid_config')
    folders = [Path(value['state_directory']), output.parent, Path(value['relay_lock_directory'])]
    _require(len({_key(x) for x in folders}) == 3, 'invalid_config')
    audit = Path(value['audit_path'])
    _require(audit.suffix.lower() == '.json' and _key(audit.parent) not in
             {_key(folders[0]), _key(folders[1])}, 'invalid_config')
    _argv(value['receiver_argv'])
    before = os.lstat(value['receiver_argv'][0])
    _chain(Path(value['receiver_argv'][0]).parent)
    _file(before)
    return value, source_pins, auth_pins


def _verify_auth(value, pins):
    for filename in value['auth_argv'][:-2]:
        _checked_file(filename, expected=pins[_key(filename)],
                      maximum=512 * 1024 * 1024, keep=False)


def _dependencies(pins):
    """Isolated imports backed by checked source bytes, with no pyc or sys.path IO."""
    data = {name: _checked_file(path, expected=pins[_key(path)])
            for name, path in SOURCES.items()}
    modules = {}
    names = {'adapter': 'claude_statusline_quota_020', 'pocket': 'pocket',
             'usage': 'pocket.usage', 'quota_snapshots': 'pocket.quota_snapshots',
             'quota_export': 'pocket.quota_export', 'callback': 'claude_callback_020'}
    system = types.ModuleType('sys')
    system.__dict__.update(sys.__dict__)
    system.path = list(sys.path)
    def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
        absolute = 'pocket.' + name if level == 1 else name
        if absolute in modules:
            return modules[absolute]
        if absolute == 'sys':
            return system
        _require(not (absolute == 'pocket' or absolute.startswith('pocket.') or
                      absolute == 'claude_statusline_quota_020'), 'unsafe_source')
        return builtins.__import__(name, globals, locals, fromlist, level)
    checked_builtins = {**vars(builtins), '__import__': checked_import}
    for key in ('pocket', 'usage', 'quota_snapshots', 'quota_export', 'adapter', 'callback'):
        name = names[key]
        module = types.ModuleType(name)
        module.__dict__.update(__file__=str(SOURCES[key]),
            __package__='pocket' if key in ('pocket', 'usage', 'quota_snapshots', 'quota_export') else '',
            __builtins__=checked_builtins)
        if key == 'pocket':
            module.__path__ = []
        modules[name] = module
        exec(compile(data[key], str(SOURCES[key]), 'exec'), module.__dict__)
    return modules['claude_callback_020'], modules['pocket.quota_snapshots'], modules['pocket.quota_export']


def _now(clock):
    value = clock()
    _require(type(value) in (int, float) and math.isfinite(value) and
             0 <= value <= MAX_EPOCH, 'invalid_clock')
    return int(value)


def _identity(raw):
    try:
        value = _json(raw) if type(raw) is bytes else raw
        _require(type(value) is dict and value.get('loggedIn') is True and
                 value.get('authMethod') == 'claude.ai' and
                 value.get('apiProvider') == 'firstParty' and
                 value.get('subscriptionType') == 'max', 'invalid_auth')
        email, org = value.get('email'), value.get('orgId')
        _require(type(email) is str and 1 <= len(email) <= 320 and email.strip() == email and
                 not any(ord(c) < 32 or ord(c) == 127 for c in email), 'invalid_auth')
        _require(org is None or type(org) is str and len(org) <= 256 and
                 not any(ord(c) < 32 or ord(c) == 127 for c in org), 'invalid_auth')
        identity = {key: value.get(key) for key in
                    ('authMethod', 'apiProvider', 'subscriptionType', 'email', 'orgId')}
        return _sha(canonical(identity))
    except Exception:
        raise ObserverError('invalid_auth') from None


def _numeric(snapshot, observed_at, now):
    _require(type(snapshot) is dict and set(snapshot) ==
             {'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'} and
             type(snapshot['version']) is int and snapshot['version'] == 1 and
             snapshot['scope'] == 'account' and snapshot['provider'] == 'claude' and
             snapshot['source'] == 'claude_code' and
             type(snapshot['fetched_at']) is int and snapshot['fetched_at'] == observed_at and
             0 <= now - observed_at < 300, 'invalid_snapshot')
    rows = snapshot['metrics']
    _require(type(rows) is list and 1 <= len(rows) <= 2, 'invalid_snapshot')
    seen, usable = set(), False
    for row in rows:
        _require(type(row) is dict and set(row) ==
                 {'window', 'window_minutes', 'used_percent', 'resets_at'}, 'invalid_snapshot')
        window, used, reset = row['window'], row['used_percent'], row['resets_at']
        _require(type(window) is str and window in ('5h', '7d') and window not in seen and
                 type(row['window_minutes']) is int and
                 row['window_minutes'] == {'5h': 300, '7d': 10080}[window], 'invalid_snapshot')
        _require(reset is None or type(reset) is int and now < reset <= MAX_EPOCH,
                 'invalid_snapshot')
        _require(used is None or type(used) in (int, float) and
                 math.isfinite(used) and 0 <= used <= 100 and reset is not None,
                 'invalid_snapshot')
        usable = usable or used is not None
        seen.add(window)
    _require(usable, 'invalid_snapshot')
    raw = canonical(snapshot)
    _require(len(raw) <= 16384, 'invalid_snapshot')
    return raw


def _receipt(raw, fetched_at, digest):
    try:
        value = _json(raw, 4096) if type(raw) is bytes else raw
        _require(type(value) is dict and set(value) ==
                 {'ok', 'profile', 'provider', 'fetched_at', 'snapshot_sha256'} and
                 value['ok'] is True and value['profile'] == PROFILE and
                 value['provider'] == 'claude' and type(value['fetched_at']) is int and
                 value['fetched_at'] == fetched_at and value['snapshot_sha256'] == digest,
                 'invalid_receipt')
    except Exception:
        raise ObserverError('invalid_receipt') from None


def _audit(value, config, snapshots):
    path = Path(config['audit_path'])
    with snapshots.PrivateDirectory(path.parent) as directory:
        directory.verify()
        try:
            previous = directory.info(path.name)
            snapshots._private(previous)
        except FileNotFoundError:
            previous = None
        if previous is not None and value['status'] in ('baseline', 'cached', 'unknown'):
            # A replay must not erase the only durable evidence of a lost ACK.
            old = _json(_checked_file(path, maximum=4096, private=True), 4096)
            if type(old) is dict and old.get('status') in ('attempted', 'uncertain'):
                expected = {'status', 'observed_at', 'attempted_at', 'fetched_at', 'snapshot_sha256'}
                if old['status'] == 'uncertain':
                    expected.add('error')
                _require(set(old) == expected and
                         all(type(old.get(k)) is int and 0 <= old[k] <= MAX_EPOCH
                             for k in ('observed_at', 'attempted_at', 'fetched_at')) and
                         _hash(old['snapshot_sha256']) and
                         ('error' not in old or old['error'] in ERROR_CODES),
                         'audit_failed')
                return
        temporary = '.claude-audit-' + uuid.uuid4().hex + '.tmp'
        created = False
        try:
            fd = directory.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            created = True
            try:
                snapshots._private(os.fstat(fd))
                raw = canonical(value)
                with os.fdopen(fd, 'wb', closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(fd)
                directory.verify()
                _require(snapshots._revision(directory.info(temporary)) ==
                         snapshots._revision(os.fstat(fd)), 'audit_failed')
            finally:
                os.close(fd)
            directory.verify()
            try:
                current = directory.info(path.name)
                snapshots._private(current)
            except FileNotFoundError:
                current = None
            _require((current is None) == (previous is None) and
                     (current is None or snapshots._revision(current) ==
                      snapshots._revision(previous)), 'audit_failed')
            directory.replace(temporary, path.name)
            created = False
            if directory.fd is not None:
                os.fsync(directory.fd)
        finally:
            if created:
                directory.unlink(temporary)


def run_callback(raw, config, *, auth_status, send, clock=time.time):
    """Consume once and, only on a fresh publication, send once. Return safe audit.

    auth_status() returns supported auth-status JSON bytes or a parsed object;
    send(canonical_numeric_bytes) returns the exact receiver ACK. Any uncertain
    send is recorded with its digest/time. Frozen high-water state prevents retry.
    """
    at, value, snapshots, attempted = 0, None, None, False
    audit = {'status': 'error', 'observed_at': 0, 'error': 'observer_failed'}
    try:
        at = _now(clock)
        audit['observed_at'] = at
        _require(type(raw) is bytes and len(raw) <= MAX_INPUT, 'invalid_input')
        value, source_pins, auth_pins = _config(config)
        callback, snapshots, exporter = _dependencies(source_pins)
        with snapshots.PrivateDirectory(value['relay_lock_directory']) as directory:
            try:
                lock = exporter._publication_lock(directory)
                lock.__enter__()
            except Exception:
                raise ObserverError('busy') from None
            try:
                try:
                    _verify_auth(value, auth_pins)
                    binding = _identity(auth_status())
                    _verify_auth(value, auth_pins)
                    _require(binding == value['expected_account_sha256'], 'account_mismatch')
                    try:
                        result = callback.consume(io.BytesIO(raw),
                            state_directory=value['state_directory'], output=value['output'],
                            account_binding=binding, observed_at=at)
                    except Exception:
                        raise ObserverError('callback_failed') from None
                    _require(type(result) is dict and set(result) == {'status', 'published'} and
                             type(result['published']) is bool and result['status'] in
                             ('baseline', 'cached', 'unknown', 'fresh') and
                             (result['status'] == 'fresh') == result['published'], 'callback_failed')
                    audit = {'status': result['status'], 'observed_at': at}
                    if result['published']:
                        with snapshots.PrivateDirectory(Path(value['output']).parent) as numeric:
                            snapshot = snapshots.read_private(numeric, 'claude.json', provider='claude')
                        now = _now(clock)
                        wire = _numeric(snapshot, at, now)
                        audit.update(status='attempted', attempted_at=now, fetched_at=at,
                                     snapshot_sha256=_sha(wire))
                        _audit(audit, value, snapshots)
                        attempted = True
                        try:
                            receipt = send(wire)
                        except Exception:
                            raise ObserverError('send_failed') from None
                        _receipt(receipt, at, audit['snapshot_sha256'])
                        audit['status'] = 'sent'
                    _audit(audit, value, snapshots)
                    return audit
                except Exception as error:
                    code = _error_code(error)
                    audit = ({**audit, 'status': 'uncertain', 'error': code} if attempted else
                             {'status': 'error', 'observed_at': at, 'error': code})
                    try:
                        _audit(audit, value, snapshots)
                    except Exception:
                        pass
                    return audit
            finally:
                lock.__exit__(None, None, None)
    except Exception as error:
        code = _error_code(error)
        return {'status': 'error', 'observed_at': at, 'error': code}


def _bounded_process(argv, *, input_bytes=None, maximum=MAX_INPUT, timeout=8):
    """Drain only bounded stdout; discard stderr and reap on timeout/overflow."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    chunks, failures = [], []
    def read():
        total = 0
        try:
            while total <= maximum:
                chunk = process.stdout.read(min(8192, maximum + 1 - total))
                if not chunk:
                    return
                total += len(chunk)
                if total > maximum:
                    failures.append(True)
                    process.kill()
                    return
                chunks.append(chunk)
        except Exception:
            failures.append(True)
    def write():
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except Exception:
            failures.append(True)
    reader = threading.Thread(target=read, daemon=True)
    writer = None
    reader.start()
    if input_bytes is not None:
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
    try:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            failures.append(True)
            process.kill()
            code = process.wait(timeout=1)
        reader.join(timeout=1)
        if writer is not None:
            writer.join(timeout=1)
        _require(code == 0 and not failures and not reader.is_alive() and
                 (writer is None or not writer.is_alive()), 'process_failed')
        return b''.join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ObserverError('invalid_input')


def main(argv=None, *, stdin=None, stdout=None, stderr=None):
    """A statusLine observer always leaves stdout empty and exits zero."""
    try:
        parser = _Parser(add_help=False)
        parser.add_argument('--config', required=True)
        parser.add_argument('--config-sha256', required=True)
        args = parser.parse_args(argv)
        _require(_hash(args.config_sha256), 'invalid_config')
        raw_config = _checked_file(_path(args.config), expected=args.config_sha256,
                                   maximum=MAX_INPUT, private=True)
        config = _json(raw_config)
        source = sys.stdin.buffer if stdin is None else stdin
        raw = source.read(MAX_INPUT + 1)
        result = run_callback(raw, config,
            auth_status=lambda: _bounded_process(config['auth_argv'], maximum=MAX_INPUT, timeout=8),
            send=lambda numeric: _bounded_process(config['receiver_argv'], input_bytes=numeric,
                                                   maximum=4096, timeout=8),
            clock=time.time)
        if result['status'] in ('error', 'uncertain'):
            raise ObserverError('observer_failed')
    except Exception:
        print('claude_passive: observer_failed', file=sys.stderr if stderr is None else stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
