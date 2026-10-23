"""Additive private Claude receiver. Importing never accesses accounts or services.

The production entry point is Linux-only and fixed-path. Authentication remains
the existing private SSH channel. Only strict numeric snapshots reach disk.
Codex's immutable operator and publication locks are never acquired here.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import types
import uuid

BASE = Path('/home/example/.hermes/pocket/gateway-only-20261015')
STAGE = BASE / 'staging/account-quota-020-claude-passive-r1'
PROFILE = 'codex_claude_passive_v1'
CODEX_OPS_SHA256 = 'a9a0dbdfdf359dd8bc1efb07367f44ed7fe5bac33d99032ddcb639c4c7c3730f'
CODEX_ACCEPTANCE_SHA256 = 'f4ea7831a59a5a3d183260894ddbe8f9a453a8cef2986186f2505d6bc6c5c43e'
MAX_BYTES = 16384
MAX_TIMESTAMP = 2**53 - 1
WINDOWS = {'5h': 300, '7d': 10080}


@dataclass(frozen=True)
class PathSet:
    stage: Path = STAGE
    codex_stage: Path = BASE / 'staging/account-quota-020-codex-ops-r3'
    release: Path = BASE / 'releases/voice-usage-preview-transport-v2-20261015-011'
    numeric: Path = BASE / 'state/.linux-pocket.sqlite.account-quota-v1'
    claude_staging: Path = BASE / 'state/.claude-passive-020.account-quota-v1'
    claude_lock: Path = BASE / 'state/.claude-passive-020-lock'


PATHS = PathSet()


class ReceiverError(ValueError):
    """Fixed, redacted mechanical rejection."""
    def __init__(self):
        super().__init__('receiver_rejected')


class PublicationUncertain(ReceiverError):
    """Replacement was attempted. Never restore or automatically retry it."""
    def __init__(self):
        ValueError.__init__(self, 'publication_uncertain')


def _require(condition):
    if not condition:
        raise ReceiverError()


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _valid_hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(value):
    raise ReceiverError()


def _float(value):
    result = float(value)
    _require(_finite(result))
    return result


def _json(raw, maximum=MAX_BYTES):
    _require(isinstance(raw, bytes) and len(raw) <= maximum)
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_float=_float, parse_constant=_constant)
    except Exception:
        raise ReceiverError() from None


def validate_snapshot(raw, now):
    """Return an independent existing-v1 snapshot, preserving observation time."""
    try:
        value = _json(raw)
        _require(isinstance(value, dict) and set(value) == {
            'version', 'scope', 'provider', 'source', 'fetched_at', 'metrics'})
        _require(type(value['version']) is int and value['version'] == 1
            and value['scope'] == 'account' and value['provider'] == 'claude'
            and value['source'] == 'claude_code')
        fetched = value['fetched_at']
        _require(_finite(now) and 0 <= now <= MAX_TIMESTAMP
            and type(fetched) is int and 0 <= fetched <= MAX_TIMESTAMP
            and 0 <= now - fetched < 300)
        rows = value['metrics']; windows = set(); usable = False; result = []
        _require(isinstance(rows, list) and 1 <= len(rows) <= 2)
        for row in rows:
            _require(isinstance(row, dict) and set(row) == {
                'window', 'used_percent', 'resets_at', 'window_minutes'})
            window, used, reset = row['window'], row['used_percent'], row['resets_at']
            _require(isinstance(window, str) and window in WINDOWS and window not in windows)
            windows.add(window)
            _require(type(row['window_minutes']) is int and row['window_minutes'] == WINDOWS[window])
            _require(used is None or _finite(used) and 0 <= used <= 100)
            _require(reset is None or type(reset) is int and now < reset <= MAX_TIMESTAMP)
            usable |= used is not None
            result.append({'window': window, 'used_percent': used,
                           'resets_at': reset, 'window_minutes': WINDOWS[window]})
        _require(usable)
        return {'version': 1, 'scope': 'account', 'provider': 'claude',
                'source': 'claude_code', 'fetched_at': fetched, 'metrics': result}
    except Exception:
        raise ReceiverError() from None


def _relative_name(name):
    return (isinstance(name, str) and 0 < len(name) <= 200
        and not PurePosixPath(name).is_absolute()
        and all(part not in ('', '.', '..') and re.fullmatch('[A-Za-z0-9_.-]+', part)
                for part in name.split('/')))


def validate_config(config):
    try:
        required = {'version', 'profile', 'enabled', 'codex_acceptance_sha256',
                    'codex_ops_sha256', 'source_pins'}
        _require(isinstance(config, dict) and required <= set(config)
                 and set(config) <= required | {'aw_source_pins'})
        _require(type(config['version']) is int and config['version'] == 1
            and config['profile'] == PROFILE and config['enabled'] is True
            and config['codex_acceptance_sha256'] == CODEX_ACCEPTANCE_SHA256
            and config['codex_ops_sha256'] == CODEX_OPS_SHA256)
        for name in ('source_pins', 'aw_source_pins'):
            pins = config.get(name, {})
            _require(isinstance(pins, dict) and len(pins) <= 64)
            for path, digest in pins.items():
                _require(_relative_name(path) and _valid_hash(digest))
        _require('claude_remote_020.py' in config['source_pins']
                 and 'combined-profile.json' not in config['source_pins'])
        return _json(_canonical(config), maximum=32768)
    except Exception:
        raise ReceiverError() from None


def _revision(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _info(info, *, directory=False, private=False):
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and not getattr(info, 'st_file_attributes', 0) & 1024
        and (directory or info.st_nlink == 1))
    if os.name == 'posix':
        _require(info.st_uid in (0, os.geteuid()) and stat.S_IMODE(info.st_mode) & 0o022 == 0)
        if private:
            _require(info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))


def _chain(path):
    _require(path.is_absolute() and '..' not in path.parts)
    result = []
    for directory in (*reversed(path.parents), path):
        info = directory.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024)
        result.append((str(directory), info.st_dev, info.st_ino))
    return result


def _read_file(path, *, private=True, maximum=1024 * 1024):
    path = Path(path)
    chain = _chain(path.parent)
    _info(path.parent.lstat(), directory=True, private=private)
    before = path.lstat(); _info(before, private=private)
    _require(before.st_size <= maximum)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        _require(_revision(os.fstat(fd)) == _revision(before))
        chunks = []; total = 0
        while total <= maximum:
            chunk = os.read(fd, maximum + 1 - total)
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
        _require(total == before.st_size and _revision(os.fstat(fd)) == _revision(before)
            and _revision(path.lstat()) == _revision(before) and _chain(path.parent) == chain)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _load_ops(paths, config):
    """Hash exact approved bytes, then compile those bytes without import cache."""
    try:
        raw = _read_file(paths.codex_stage / 'quota_remote_020.py')
        _require(_sha(raw) == CODEX_OPS_SHA256 == config['codex_ops_sha256'])
        module = types.ModuleType('_claude_approved_codex_ops')
        module.__file__ = str(paths.codex_stage / 'quota_remote_020.py')
        exec(compile(raw, module.__file__, 'exec'), module.__dict__)
        return module
    except Exception:
        raise ReceiverError() from None


def _load_backend(paths, manifest):
    """Load only the already pinned frozen numeric helpers, using exact bytes."""
    name = '_claude_frozen_backend_' + uuid.uuid4().hex
    package = types.ModuleType(name); package.__path__ = []
    sys.modules[name] = package; loaded = []
    try:
        for leaf in ('usage', 'quota_snapshots', 'quota_export'):
            relative = 'pocket/' + leaf + '.py'
            raw = _read_file(paths.release / relative, private=False)
            expected = manifest['candidate_hashes'][relative]
            _require(_valid_hash(expected) and _sha(raw) == expected)
            module_name = name + '.' + leaf
            module = types.ModuleType(module_name)
            module.__file__ = str(paths.release / relative); module.__package__ = name
            sys.modules[module_name] = module; loaded.append(module_name)
            exec(compile(raw, module.__file__, 'exec'), module.__dict__)
        return module
    except Exception:
        raise ReceiverError() from None
    finally:
        for module_name in loaded: sys.modules.pop(module_name, None)
        sys.modules.pop(name, None)


def _policy(paths, config, config_digest, ops, baseline=None):
    """Read-only gates. Never enter any old operation or publication lock."""
    _require(_sha(_read_file(paths.stage / 'combined-profile.json', maximum=32768)) == config_digest)
    for name, expected in config['source_pins'].items():
        _require(_sha(_read_file(paths.stage / name)) == expected)
    _require(_sha(_read_file(paths.codex_stage / 'quota_remote_020.py')) == CODEX_OPS_SHA256)
    _require(_sha(_read_file(paths.codex_stage / 'quotaops-acceptance.json')) == CODEX_ACCEPTANCE_SHA256)
    bundle = ops.validate_bundle(paths.codex_stage)
    _require(bundle['acceptance_sha256'] == CODEX_ACCEPTANCE_SHA256)
    ops.validate_remote_stage(bundle)
    for marker in ('dispatched', 'started', 'progress', 'result'):
        _require(not os.path.lexists(paths.codex_stage / ('rollback-' + marker + '.json')))
    channel = ops.read_channel(bundle, allow_pending=False)
    _require(channel.get('state') == 'active' and channel.get('enabled') is True
        and channel.get('acceptance_sha256') == CODEX_ACCEPTANCE_SHA256)
    state = {}
    for phase in ('deployment', 'activation'):
        path = paths.codex_stage / (phase + '-result.json')
        raw = _read_file(path); result = _json(raw, maximum=1024 * 1024)
        _require(isinstance(result, dict) and result.get('ok') is True
            and result.get('acceptance_sha256') == CODEX_ACCEPTANCE_SHA256
            and result.get('backend_deployed') is True
            and not result.get('recovery_pending') and not result.get('outcome_uncertain'))
        if phase == 'activation': _require(result.get('quota_activated') is True)
        identity = ops.supervisor_identity(phase, CODEX_ACCEPTANCE_SHA256)
        _require(identity is not None and identity['receipts'].get('result', {}).get('ok') is True
            and not ops.operator_process_exists(identity['operator_pid']))
        state[phase] = _sha(raw)
    state['channel'] = _sha(_read_file(paths.codex_stage / 'codex-channel.json'))
    _require(baseline is None or state == baseline)
    ops.check_hashes(bundle['manifest'], True)
    ops.gateway_invariants()
    return bundle, state


def _scope(directory, allowed, backend):
    directory.verify()
    names = set(os.listdir(directory.fd if directory.fd is not None else directory.path))
    _require(names <= set(allowed))
    for name in names: backend._private(directory.info(name))
    directory.verify()


def _current(directory, backend, observed):
    try:
        value, info = backend.read_private(directory, 'claude.json', provider='claude', include_info=True)
    except FileNotFoundError:
        return None, None
    strict = validate_snapshot(_canonical(value), value['fetched_at'])
    _require(strict['fetched_at'] <= observed)
    return strict, backend._revision(info)


def receive(raw, config, *, now=None, paths=None, ops=None, backend=None,
            profile_sha256=None, clock=None, checkpoint=None):
    """Validate and publish once; fixtures may inject paths and read-only gates.

    After a final replacement attempt, every failure is uncertain. There is no
    rollback, timestamp rewrite, alternate destination or publication retry.
    """
    attempted = False
    try:
        paths = PATHS if paths is None else paths
        read_clock = clock if clock is not None else time.time if now is None else lambda: now
        observed = read_clock()
        value = validate_snapshot(raw, observed)
        config = validate_config(config)
        profile_raw = _read_file(paths.stage / 'combined-profile.json', maximum=32768)
        _require(_json(profile_raw, maximum=32768) == config)
        profile_digest = _sha(profile_raw)
        _require(profile_sha256 is None or _valid_hash(profile_sha256) and profile_digest == profile_sha256)
        _require(len({str(getattr(paths, name).absolute()) for name in
            ('stage', 'codex_stage', 'release', 'numeric', 'claude_staging', 'claude_lock')}) == 6)
        ops = _load_ops(paths, config) if ops is None else ops
        bundle, baseline = _policy(paths, config, profile_digest, ops)
        backend = _load_backend(paths, bundle['manifest']) if backend is None else backend
        with backend.PrivateDirectory(paths.claude_lock) as lock:
            with backend._publication_lock(lock):
                with backend.PrivateDirectory(paths.numeric) as numeric, \
                     backend.PrivateDirectory(paths.claude_staging) as staging:
                    _scope(lock, set(), backend)
                    _scope(numeric, {'codex.json', 'claude.json'}, backend)
                    _scope(staging, {'claude.json'}, backend)
                    previous, previous_revision = _current(numeric, backend, observed)
                    _require(previous is None or value['fetched_at'] > previous['fetched_at'])
                    published = backend.publish_snapshot(value, paths.claude_staging / 'claude.json',
                                                         clock=lambda: observed)
                    _require(published == value)
                    staged, staged_revision = _current(staging, backend, observed)
                    _require(staged == value)
                    if checkpoint is not None: checkpoint('staged')
                    if checkpoint is not None: checkpoint('before_replace')
                    _policy(paths, config, profile_digest, ops, baseline)
                    validate_snapshot(_canonical(value), read_clock())
                    _scope(lock, set(), backend)
                    _scope(staging, {'claude.json'}, backend)
                    _scope(numeric, {'codex.json', 'claude.json'}, backend)
                    _require(backend._revision(staging.info('claude.json')) == staged_revision)
                    try:
                        existing = numeric.info('claude.json'); backend._private(existing)
                        existing_revision = backend._revision(existing)
                    except FileNotFoundError:
                        existing_revision = None
                    _require(existing_revision == previous_revision)
                    # Revalidate both complete chains immediately before the only
                    # cross-directory mutation. No temporary name enters NUMERIC.
                    lock.verify(); staging.verify(); numeric.verify()
                    attempted = True
                    if numeric.fd is not None and staging.fd is not None:
                        os.replace('claude.json', 'claude.json',
                            src_dir_fd=staging.fd, dst_dir_fd=numeric.fd)
                    else:
                        os.replace(paths.claude_staging / 'claude.json', paths.numeric / 'claude.json')
                    if checkpoint is not None: checkpoint('replaced')
                    for directory in (staging, numeric):
                        if directory.fd is not None: os.fsync(directory.fd)
                    lock.verify(); staging.verify(); numeric.verify()
                    _scope(staging, set(), backend)
                    _scope(numeric, {'codex.json', 'claude.json'}, backend)
                    actual, _ = _current(numeric, backend, observed)
                    _require(actual == value)
                    _policy(paths, config, profile_digest, ops, baseline)
                    validate_snapshot(_canonical(value), read_clock())
        return {'ok': True, 'profile': PROFILE, 'provider': 'claude',
                'fetched_at': value['fetched_at'], 'snapshot_sha256': _sha(_canonical(value))}
    except BaseException as error:
        if attempted:
            raise PublicationUncertain() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise ReceiverError() from None


def combined_usage_ready(rows, observed_at, require_claude=False):
    """Strict Codex readiness with legitimate passive Claude quota permitted."""
    try:
        if (not isinstance(rows, list) or len(rows) != 3 or not _finite(observed_at)
                or any(not isinstance(row, dict) for row in rows)
                or {row.get('provider') for row in rows} != {'codex', 'claude', 'ollama_cloud'}):
            return False
        local_available = False; fresh = {'codex': False, 'claude': False}
        for row in rows:
            local = row.get('local_usage')
            if (not isinstance(local, dict) or local.get('scope') != 'pocket_runs'
                    or local.get('period') != 'all_recorded'
                    or local.get('availability') not in ('available', 'partial', 'unavailable')):
                return False
            local_available |= local['availability'] in ('available', 'partial')
            provider = row['provider']; metrics = row.get('metrics', [])
            if not isinstance(metrics, list) or len(metrics) > 64 or row.get('value') is not None:
                return False
            if provider == 'ollama_cloud':
                if row.get('availability') not in ('not_configured', 'unavailable', 'stale', 'failed', 'partial'):
                    return False
                for metric in metrics:
                    if not isinstance(metric, dict) or metric.get('availability') not in ('stale', 'unavailable'):
                        return False
                    fetched = metric.get('fetched_at')
                    if fetched is not None and (not _finite(fetched) or fetched > observed_at): return False
                continue
            if row.get('availability') not in ('not_configured', 'available', 'partial', 'unavailable', 'stale', 'failed'):
                return False
            if not metrics and row.get('availability') in ('available', 'partial'): return False
            windows = set()
            for metric in metrics:
                if not isinstance(metric, dict): return False
                window = metric.get('window')
                allowed = ('codex:primary', 'codex:secondary') if provider == 'codex' else WINDOWS
                if not isinstance(window, str) or window not in allowed or window in windows: return False
                windows.add(window)
                source = 'Codex app account limits' if provider == 'codex' else 'Claude Code account limits'
                if (metric.get('provider') != provider or metric.get('category') != 'subscription'
                    or metric.get('unit') != 'percent_remaining' or metric.get('source') != source): return False
                duration = metric.get('window_minutes')
                if provider == 'claude':
                    if type(duration) is not int or duration != WINDOWS[window]: return False
                elif duration is not None and (type(duration) is not int or not 1 <= duration <= 4294967295):
                    return False
                if metric.get('availability') == 'available':
                    value, fetched, reset = metric.get('value'), metric.get('fetched_at'), metric.get('resets_at')
                    if (not _finite(value) or not 0 <= value <= 100 or not _finite(fetched)
                            or not 0 <= observed_at - fetched < 300): return False
                    if reset is not None and (not _finite(reset) or reset <= observed_at): return False
                    fresh[provider] = True
                elif metric.get('availability') not in ('unavailable', 'stale'): return False
                elif provider == 'claude':
                    fetched = metric.get('fetched_at')
                    if fetched is not None and (not _finite(fetched) or fetched > observed_at): return False
        return local_available and fresh['codex'] and (fresh['claude'] or not require_claude)
    except Exception:
        return False


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        _require(sys.platform.startswith('linux') and Path(__file__).absolute() == STAGE / 'claude_remote_020.py')
        _require(len(args) == 3 and args[:2] == ['receive', '--profile-sha256'] and _valid_hash(args[2]))
        raw_config = _read_file(STAGE / 'combined-profile.json', maximum=32768)
        _require(_sha(raw_config) == args[2])
        config = validate_config(_json(raw_config, maximum=32768))
        result = receive(sys.stdin.buffer.read(MAX_BYTES + 1), config, profile_sha256=args[2])
        code = 0
    except PublicationUncertain:
        result = {'ok': False, 'category': 'publication_uncertain', 'outcome_uncertain': True}
        code = 2
    except BaseException:
        result = {'ok': False, 'category': 'receiver_rejected'}
        code = 1
    print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False), flush=True)
    return code


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
