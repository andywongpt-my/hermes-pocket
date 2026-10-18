"""Serial official Claude metadata refresh. No credentials or account repair.

One reviewed collector owns its own bounded PTY child. Its completed observation
time is passed unchanged to the reviewed publisher. This module creates no files
except that publisher's fixed numeric Claude snapshot.
"""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import threading
import time

BASE = Path('/opt/pocket-checkpoint/owner/.hermes/pocket/gateway-only-20261015')
RELEASE = BASE / 'releases/voice-usage-preview-transport-v2-20261015-011'
RUNTIME = BASE / 'account-quota-020'
STAGE = BASE / 'staging/account-quota-020-ops'
OUTPUT = BASE / 'state/.linux-pocket.sqlite.account-quota-v1/claude.json'
BACKEND_IDENTITY = '37442dc244c2aeb0075d80f151bfadb0017d4b7b0f310930dca2a34d1b94b419'
BACKEND_MANIFEST_SHA256 = 'b59543b1c72fe0f7ec0724bbe07d5c7a5741fe2836e5635bf9ac1b90a17c8b43'
CHILD_ENVIRONMENT = {'overrides': {'CLAUDE_CODE_SKIP_PROMPT_HISTORY': '1', 'DISABLE_UPDATES': '1',
    'IS_DEMO': '1', 'TERM': 'xterm-256color'}, 'removed': ['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'],
    'parent': 'inherited_existing_user_environment'}
INTERVAL = 120
FATAL = frozenset({'setup_required', 'login_required', 'trust_required',
                   'unknown_command', 'cleanup_failed', 'unsupported_platform'})
PROBE_ERRORS = frozenset({'unsupported_platform', 'spawn_failed', 'io_failed',
    'startup_timeout', 'freshness_unverified', 'no_usable_windows', 'refresh_failed',
    'stale_data', 'setup_required', 'login_required', 'trust_required', 'unknown_command',
    'output_limit', 'terminal_limit', 'terminal_invalid', 'terminal_unsupported',
    'child_exited', 'cleanup_failed', 'interrupted'})


def refresh_once(collector, validate, publish, *, clock=time.time, cancelled=lambda: False):
    started = clock()
    receipt = {'ok': False, 'category': 'fetch_failed', 'model_requests': 0,
               'audio_requests': 0, 'microphone_requests': 0, 'account_metadata_probes': 1}
    try:
        result = collector.probe()
    except KeyboardInterrupt:
        receipt['category'] = 'interrupted'
        return receipt
    except Exception as error:
        category = getattr(error, 'code', None)
        receipt['category'] = category if category in PROBE_ERRORS else 'fetch_failed'
        return receipt
    completed = clock()
    if cancelled():
        receipt['category'] = 'interrupted'
        return receipt
    try:
        current = validate(result.snapshot, provider='claude')
        # An initial cache, old observation, or fabricated future timestamp is
        # never restamped. int(started) tolerates the source's second precision.
        if (not math.isfinite(started) or not math.isfinite(completed) or
                not int(started) <= current['fetched_at'] <= completed or
                not 0 <= completed - started <= 60):
            raise ValueError('invalid_observation_time')
        known = {'five_hour': 300, 'seven_day': 10080}
        if not any(row['window'] in known and row.get('window_minutes') == known[row['window']]
                   and row['used_percent'] is not None for row in current['metrics']):
            raise ValueError('no_usable_windows')
    except (ValueError, TypeError, AttributeError, OverflowError):
        receipt['category'] = 'invalid_snapshot'
        return receipt
    if cancelled():
        receipt['category'] = 'interrupted'
        return receipt
    try:
        publish(current, OUTPUT)
    except KeyboardInterrupt:
        receipt['category'] = 'interrupted'
        return receipt
    except Exception:
        # Never print an exception message, path, terminal or native evidence.
        receipt['category'] = 'publication_failed'
        return receipt
    receipt.update(ok=True, category='published', fetched_at=current['fetched_at'],
                   completed_at=int(completed))
    return receipt


def run_loop(collector, validate, publish, *, stop, wait=None, clock=time.time,
             emit=lambda value: print(json.dumps(value, separators=(',', ':')), flush=True), count=0):
    wait = stop.wait if wait is None else wait
    completed = 0
    try:
        while not stop.is_set() and (count == 0 or completed < count):
            result = refresh_once(collector, validate, publish, clock=clock, cancelled=stop.is_set)
            emit(result)
            completed += 1
            if stop.is_set() or result['category'] == 'interrupted': return 130
            if result['category'] in FATAL: return 1
            if count and completed >= count: return 0 if result['ok'] else 1
            # This wait begins only after probe cleanup and publication finish.
            if wait(INTERVAL) or stop.is_set(): return 130
    except KeyboardInterrupt:
        stop.set()
        return 130
    return 130 if stop.is_set() else 0


def signal_handler(stop):
    def cancel(signum, frame):
        # The first signal unwinds the collector's owned-child finally block.
        # A second signal must not interrupt that bounded cleanup.
        if not stop.is_set():
            stop.set()
            raise KeyboardInterrupt()
    return cancel


def _guard(path, *, directory=False, private=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024 or
            not directory and info.st_nlink != 1):
        raise ValueError('unsafe_runtime')
    if os.name == 'posix':
        if (info.st_uid not in (0, os.geteuid()) or stat.S_IMODE(info.st_mode) & 0o022 or
                private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600))):
            raise ValueError('unsafe_runtime')
    return info


def _read(path, maximum=512 * 1024, *, private=False):
    for ancestor in reversed(path.parents): _guard(ancestor, directory=True)
    before = _guard(path, private=private)
    if before.st_size > maximum: raise ValueError('unsafe_runtime')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
                opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size):
            raise ValueError('unsafe_runtime')
        raw = os.read(fd, maximum + 1)
        after = _guard(path, private=private)
        if len(raw) > maximum or (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) != (
                before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size):
            raise ValueError('unsafe_runtime')
        return raw
    finally:
        os.close(fd)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('invalid_runtime_metadata')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid_runtime_metadata')))


def verify_runtime_bindings(manifest):
    """Bind runtime bytes back to the concrete accepted stage before imports."""
    fields = {'version', 'candidate_hashes_sha256', 'acceptance_sha256', 'cwd', 'child_environment',
              'interval_seconds', 'probe_deadline_seconds', 'runtime_hashes', 'backend_hashes'}
    if (set(manifest) != fields or type(manifest['version']) is not int or manifest['version'] != 1
            or manifest['candidate_hashes_sha256'] != BACKEND_IDENTITY or manifest['cwd'] != '/opt/pocket-checkpoint/owner'
            or manifest['child_environment'] != CHILD_ENVIRONMENT or manifest['interval_seconds'] != 120
            or manifest['probe_deadline_seconds'] != 55.0):
        raise ValueError('invalid_runtime_manifest')
    accepted_raw = _read(STAGE / 'quotaops-acceptance.json', private=True)
    if hashlib.sha256(accepted_raw).hexdigest() != manifest['acceptance_sha256']:
        raise ValueError('acceptance_drift')
    accepted = _json(accepted_raw)
    if accepted.get('approved') is not True or accepted.get('candidate_hashes_sha256') != BACKEND_IDENTITY:
        raise ValueError('acceptance_missing')
    if accepted['artifact_hashes'].get('manifest-R3.json') != BACKEND_MANIFEST_SHA256:
        raise ValueError('backend_drift')
    expected_runtime = {'official_claude_usage.py': accepted['collector_files']['official_claude_usage.py']['sha256'],
                        'quota_refresh_020.py': accepted['implementation_hashes']['quota_refresh_020.py']}
    if manifest['runtime_hashes'] != expected_runtime: raise ValueError('runtime_drift')
    stage_hashes = {**accepted['artifact_hashes'], **accepted['implementation_hashes'],
                    **{name: value['sha256'] for name, value in accepted['collector_files'].items()}}
    for name, expected in stage_hashes.items():
        if (re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) is None or
                not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None or
                hashlib.sha256(_read(STAGE / name, private=True)).hexdigest() != expected):
            raise ValueError('accepted_artifact_drift')
    native = _json(_read(STAGE / 'quotaops-native-acceptance.json', private=True))
    if (native.get('accepted') is not True or native.get('freshness_verified') is not True
            or native.get('cwd') != '/opt/pocket-checkpoint/owner' or native.get('child_environment') != CHILD_ENVIRONMENT
            or native.get('source_files') != accepted['collector_files']):
        raise ValueError('native_acceptance_missing')
    backend = _json(_read(STAGE / 'manifest-R3.json', private=True))
    identity = hashlib.sha256(json.dumps(backend['candidate_hashes'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected_backend = {name: value for name, value in backend['candidate_hashes'].items() if name.startswith('pocket/')}
    if identity != BACKEND_IDENTITY or manifest['backend_hashes'] != expected_backend:
        raise ValueError('backend_drift')
    for name, expected in expected_runtime.items():
        if hashlib.sha256(_read(RUNTIME / name, private=True)).hexdigest() != expected: raise ValueError('runtime_drift')
    for name, expected in expected_backend.items():
        if hashlib.sha256(_read(RELEASE / name)).hexdigest() != expected: raise ValueError('backend_drift')


def load_runtime():
    if (not sys.platform.startswith('linux') or Path(__file__).absolute() != RUNTIME / 'quota_refresh_020.py'
            or Path.cwd() != Path('/opt/pocket-checkpoint/owner')):
        raise ValueError('unsupported_runtime')
    _guard(RUNTIME, directory=True, private=True)
    if {p.name for p in RUNTIME.iterdir()} != {'official_claude_usage.py', 'quota_refresh_020.py', 'manifest.json'}:
        raise ValueError('unsafe_runtime')
    for name in ('official_claude_usage.py', 'quota_refresh_020.py', 'manifest.json'):
        _guard(RUNTIME / name, private=True)
    manifest = _json(_read(RUNTIME / 'manifest.json', private=True))
    verify_runtime_bindings(manifest)
    # Code and publisher are imported only after exact reviewed bytes are bound.
    sys.path.insert(0, str(RELEASE))
    from pocket.quota_snapshots import validate_snapshot
    from pocket.quota_export import publish_snapshot
    spec = importlib.util.spec_from_file_location('official_claude_usage', RUNTIME / 'official_claude_usage.py')
    collector = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = collector
    spec.loader.exec_module(collector)
    if collector.Limits().total_seconds != manifest['probe_deadline_seconds']:
        raise ValueError('collector_deadline_drift')
    return collector, validate_snapshot, publish_snapshot


def main():
    stop = threading.Event()
    previous = {signum: signal.signal(signum, signal_handler(stop)) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        collector, validate, publish = load_runtime()
        return run_loop(collector, validate, publish, stop=stop)
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({'ok': False, 'category': 'runtime_rejected'}), flush=True)
        return 1
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
