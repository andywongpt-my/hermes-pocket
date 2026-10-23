"""Guarded 020 operations. Importing this module has no external side effects.

Execution is Linux-only and fixed-path. A root-produced acceptance record must
bind exact backend, source, native proof, passing operations tests and independent
review before staging or changing any service/data. Exceptions use fixed codes.
"""
from contextlib import contextmanager
import base64
import datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
import uuid

BASE = Path('/home/example/.hermes/pocket/gateway-only-20261015')
RELEASE = BASE / 'releases/voice-usage-preview-transport-v2-20261015-011'
STAGE = BASE / 'staging/account-quota-020-codex-ops-r3'
PACKAGE = BASE / 'staging/account-quota-020-r3/package'
RUNTIME = BASE / 'account-quota-020-codex'
NUMERIC = BASE / 'state/.linux-pocket.sqlite.account-quota-v1'
DB = BASE / 'state/linux-pocket.sqlite'
PYTHON = '/home/example/.hermes/hermes-agent/venv/bin/python'
SERVICE = 'hermes-pocket-gateway-only.service'
QUOTA_SERVICE = 'hermes-pocket-claude-quota-020.service'
DEVICE_ENV = '/home/example/.hermes/pocket/bridge-20261014/private/device.env'
CA = Path('/home/example/.hermes/pocket/bridge-20261014/tls/pocket-ca.pem')
BACKEND_IDENTITY = '37442dc244c2aeb0075d80f151bfadb0017d4b7b0f310930dca2a34d1b94b419'
BACKEND_PINS = {
    'manifest-R3.json': 'b59543b1c72fe0f7ec0724bbe07d5c7a5741fe2836e5635bf9ac1b90a17c8b43',
    'backend-freeze-R3.json': '1ea7dc31490796c6996e169beec597b2fea68ae1d03bb6574454b636307f0e37',
    'offline-linux-result-R3.json': '5017c98db1160944ed30bb152e4cf89a4c0ed761e93dfc0e87507e48dc4b196d',
    'review-account-quota-R3.json': '0bcdf1b54bd4870e2c728deedf3208732c0140709dfabefa04c3faa74261de3d'}
IMPLEMENTATIONS = ('quota_remote_020.py', 'operate_quota_020.py', 'quota_refresh_020.py', 'test_quota_ops_020.py', 'test_r3_preserved.py', 'private_aw_020.py', 'make_acceptance_020.py', 'test_split_proof_020.py')
COLLECTOR_FILES = ('official_codex_quota.py', 'test_official_codex_quota.py')
CHANGED = ('pocket/quota_export.py', 'pocket/quota_snapshots.py', 'pocket/server.py')
CHILD_ENVIRONMENT = {'overrides': {'CLAUDE_CODE_SKIP_PROMPT_HISTORY': '1',
    'DISABLE_UPDATES': '1', 'IS_DEMO': '1', 'TERM': 'xterm-256color'},
    'removed': ['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'], 'parent': 'inherited_existing_user_environment'}
GET_ROUTES = ('/health', '/targets/linux/status', '/targets/linux/usage', '/targets/linux/models')
CAPABILITIES = {'stt': True, 'tts': True, 'format': 'pcm_s16le', 'sample_rate': 16000,
                'channels': 1, 'max_seconds': 30, 'chunk_bytes': 4096}
CATALOG = {'version': 2, 'default_id': 'default', 'models': [
    {'id': 'default', 'name': 'Follow default', 'verified': True},
    {'id': 'opus', 'name': 'Claude Opus', 'verified': True},
    {'id': 'glm_flash', 'name': 'GLM 5.3 Flash', 'verified': False}]}
REPLY_CONTRACT_SHA256 = '017786c30b566d8e4647209dda28e7e0082b16fca3d9810f4f406cf0a9771246'
REPLY_DELIVERY = {'version': 1, 'text_pages': {'version': 1, 'page_bytes': 768,
    'max_result_utf8_bytes': 65536}, 'speech_segments': {'version': 2, 'available': True,
    'segment_seconds': 20, 'max_reply_seconds': 240, 'max_segments': 12, 'chunk_bytes': 4096,
    'sample_rate': 16000, 'channels': 1, 'sample_bits': 16, 'max_tts_utf8_bytes': 4096,
    'max_encoded_bytes': 3145728, 'generation_timeout_seconds': 120, 'decode_timeout_seconds': 20,
    'idle_ttl_seconds': 180, 'absolute_ttl_seconds': 900}}
PROTECTED = {'linux_config': Path('/home/example/.hermes/config.yaml'),
    'owner_config': BASE / 'owner-config-fish-voice-002.json',
    'speech_pins': BASE / 'hermes-speech-pins.json',
    'unit': Path('/home/example/.config/systemd/user/hermes-pocket-gateway-only.service'),
    'stt_helper': Path('/home/example/.hermes/pocket/helpers/pocket_local_stt.py'),
    'fish_helper': Path('/home/example/.hermes/scripts/fish-audio-tts.sh'),
    'ca': CA, 'transport_control': RELEASE / 'control_transport_011.py'}
PHASES = ('deployment', 'activation', 'rollback')
MARKERS = ('dispatched', 'started', 'progress', 'result')
MAX_BYTES = 1024 * 1024
MAX_PAYLOAD = 3 * MAX_BYTES
QUALIFICATION_PINS = {'official_codex_quota.py': '932ffa579860a91f37d2a84cd1e612fb9fd97a5a729b443e19ea5478335c778c', 'test_official_codex_quota.py': '8725cc0f9ae5221e989e399ca768b10d57405c98b9f414ee054965c13bb23eb0', 'codex-producer-resume-20261018-report.json': '4d90df8265c0ee088f44e3974ac58727efc89d1dec1d472776b7ed41c96c88de', 'codex-producer-review-R2-20261018.json': '45537dc3e93c3825dfec955e91bb6f88c29cd2d0259cd275561a16db74a10251'}
OFFLINE_FILES = (*IMPLEMENTATIONS, *BACKEND_PINS, *QUALIFICATION_PINS)
STAGE_MANIFEST = 'quotaops-stage.json'
OPERATION_LOCK = 'quotaops-operation.lock'
OPERATION_LOCK_BYTES = b'quota-020-operation-lock-v1\n'
OPERATION_LOCK_OFFSET = MAX_BYTES + 1


class OpsError(ValueError):
    """Fixed mechanical category only; never an external exception message."""


def require(condition, code):
    if not condition: raise OpsError(code)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def counters():
    return {'model_requests': 0, 'audio_requests': 0, 'microphone_requests': 0,
            'account_metadata_probes': 0}


def validate_info(info, *, directory=False, private=False, uid=None):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    require(kind(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024
            and (directory or info.st_nlink == 1), 'unsafe_path')
    if uid is None and os.name == 'posix': uid = os.geteuid()
    if uid is not None:
        mode = stat.S_IMODE(info.st_mode)
        require(info.st_uid in (0, uid) and mode & 0o022 == 0, 'unsafe_path')
        if private:
            require(info.st_uid == uid and mode == (0o700 if directory else 0o600), 'unsafe_path')
    return info


def guard(path, *, directory=False, private=False):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'unsafe_path')
    for parent in reversed(path.parents): validate_info(parent.lstat(), directory=True)
    return validate_info(path.lstat(), directory=directory, private=private)


def revision(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, *, private=False, maximum=MAX_BYTES):
    path = Path(path).absolute()
    before = guard(path, private=private)
    require(before.st_size <= maximum, 'input_limit')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                 getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        require(revision(os.fstat(fd)) == revision(before), 'path_changed')
        raw = os.read(fd, maximum + 1)
        require(len(raw) == before.st_size and revision(os.fstat(fd)) == revision(before)
                and revision(guard(path, private=private)) == revision(before), 'path_changed')
        return raw
    finally:
        os.close(fd)


def digest(path):
    return hashlib.sha256(read_file(path)).hexdigest()


def _pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, 'invalid_metadata'); result[key] = value
    return result


def parse_json(raw):
    try:
        require(len(raw) <= MAX_BYTES, 'input_limit')
        return json.loads(raw, object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(OpsError('invalid_metadata')))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise OpsError('invalid_metadata') from None


def read_json(path):
    return parse_json(read_file(path))


def candidate_identity(manifest):
    return hashlib.sha256(json.dumps(manifest['candidate_hashes'],
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def hashes(entries):
    require(isinstance(entries, dict), 'artifact_binding')
    return {name: entry['sha256'] for name, entry in entries.items()}


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate_bundle(root, *, blobs=None):
    """Validate a concrete root acceptance, never infer acceptance from a cache."""
    root = Path(root).absolute()
    def body(name): return read_file(root / name) if blobs is None else blobs[name]
    def metadata(name): return parse_json(body(name))
    def file_digest(name): return hashlib.sha256(body(name)).hexdigest()
    try:
        acceptance = metadata('quotaops-acceptance.json')
        require(acceptance.get('version') == 1 and acceptance.get('approved') is True,
                'acceptance_missing')
        require(acceptance.get('candidate_hashes_sha256') == BACKEND_IDENTITY, 'backend_binding')
        review_name = acceptance.get('collector_review', '')
        require(re.fullmatch(r'codex-producer-review-R2-20261018\.json', review_name) is not None, 'collector_binding')
        required = {*BACKEND_PINS, 'codex-producer-resume-20261018-report.json', review_name,
                    'quotaops-native-acceptance.json', 'quotaops-tests.json', 'quotaops-review.json'}
        require(set(acceptance['artifact_hashes']) == required, 'artifact_binding')
        for name, expected in acceptance['artifact_hashes'].items():
            require(valid_hash(expected) and file_digest(name) == expected, 'artifact_binding')
        for name, expected in BACKEND_PINS.items():
            require(acceptance['artifact_hashes'][name] == expected, 'backend_binding')
        manifest = metadata('manifest-R3.json')
        tests = metadata('offline-linux-result-R3.json')
        freeze = metadata('backend-freeze-R3.json')
        review = metadata('review-account-quota-R3.json')
        identity = candidate_identity(manifest)
        require(identity == BACKEND_IDENTITY and freeze['backend_sha256'] == manifest['candidate_hashes']
                and tests['candidate_hashes_sha256'] == identity and review['candidate_map_identity'] == identity,
                'backend_binding')
        require(tests['exit_code'] == 0 and tests['test_count'] == manifest['expected_test_count'] == 385
                and review['verdict'] == 'approved' and not review['findings'], 'backend_unapproved')
        require(manifest['contract_sha256'] == tests['contract_sha256'] == review['contract_sha256'] == REPLY_CONTRACT_SHA256
                and tuple(sorted(manifest['changed_production_files'])) == CHANGED
                and len(manifest['protected_hashes']) == 8, 'backend_binding')
        implementations = acceptance['implementation_hashes']
        require(set(implementations) == set(IMPLEMENTATIONS), 'operations_binding')
        for name, expected in implementations.items():
            require(valid_hash(expected) and file_digest(name) == expected, 'operations_binding')
        source_hashes = hashes(acceptance['collector_files'])
        require(set(source_hashes) == set(COLLECTOR_FILES), 'collector_binding')
        for name, expected in source_hashes.items():
            require(valid_hash(expected) and file_digest(name) == expected, 'collector_binding')
        require(acceptance.get('source_profile') == 'codex_aw_numeric_v1', 'source_profile_binding')
        for name, expected in QUALIFICATION_PINS.items():
            require(file_digest(name) == expected, 'qualification_source_binding')
        source_freeze = metadata('codex-producer-resume-20261018-report.json')
        source_review = metadata(review_name)
        pins = {Path(p['path'].replace('\\', '/')).name: p['sha256'] for p in source_freeze['source_and_test_pins']}
        verified = source_freeze['verification']['post_review_covering_result']
        review_pins = {p['path']: p['sha256'] for p in source_review['reviewed_pins']}
        require(pins == source_hashes == review_pins and verified['tests'] == 30
                and verified['passed'] == 30 and verified['exit_code'] == 0
                and not verified['failures'] and not verified['errors'] and not verified['skipped_tests'],
                'collector_tests_binding')
        require(source_review['verdicts']['q1'] == 'ADDRESSED'
                and source_review['verdicts']['quality_fix_diff'] == 'PASS'
                and not source_review['finding_disposition']['new_findings'], 'collector_review_binding')
        native = metadata('quotaops-native-acceptance.json')
        validate_codex_native(native, source_hashes)
        require(native['operations_test_sha256'] == file_digest('quotaops-tests.json')
            and native['operations_review_sha256'] == file_digest('quotaops-review.json'), 'native_attestation_binding')
        ops_tests = metadata('quotaops-tests.json')
        ops_review = metadata('quotaops-review.json')
        require(ops_tests['ok'] is True and ops_tests['tests'] > 0 and ops_tests['failures'] == 0
                and ops_tests['errors'] == 0 and ops_tests['skipped'] == 0 and ops_tests['exit_code'] == 0
                and ops_tests['platform'] == 'linux' and ops_tests['implementation_hashes'] == implementations,
                'operations_tests_binding')
        require(ops_review['approved'] is True and not ops_review['findings']
                and ops_review['implementation_hashes'] == implementations, 'operations_review_binding')
        return {'acceptance': acceptance, 'manifest': manifest, 'native': native,
                'identity': identity, 'acceptance_sha256': file_digest('quotaops-acceptance.json')}
    except OpsError:
        raise
    except (OSError, KeyError, ValueError, TypeError, AttributeError, RecursionError):
        raise OpsError('artifact_missing_or_invalid') from None


def bundle_files(bundle):
    return {*bundle['acceptance']['artifact_hashes'], *IMPLEMENTATIONS, *COLLECTOR_FILES, 'quotaops-acceptance.json'}


def validate_scope(path, expected):
    """Reject every unknown entry; expected None permits a private owned receipt."""
    path = Path(path).absolute()
    guard(path, directory=True, private=True)
    actual = {entry.name for entry in path.iterdir()}
    require(actual <= set(expected), 'unknown_scope_content')
    for name in actual:
        require(Path(name).name == name and name not in ('.', '..'), 'unsafe_path')
        entry = path / name
        guard(entry, private=True)
        if expected[name] is not None: require(digest(entry) == expected[name], 'known_path_collision')


def assert_new_attempt(stage, phase):
    require(phase in PHASES, 'invalid_phase')
    for marker in MARKERS:
        require(not os.path.lexists(Path(stage) / (phase + '-' + marker + '.json')), 'attempt_already_dispatched_inspect')


def resident_ready(value):
    if not isinstance(value, dict): return False
    names = ('generation', 'active', 'pending', 'worker_pid')
    return (value.get('resident') is True and value.get('ready') is True
        and all(type(value.get(name)) is int for name in names)
        and value['generation'] >= 1 and value['worker_pid'] > 0
        and 0 <= value['active'] <= 1 and 0 <= value['pending'] <= 1)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def usage_ready(rows, *, quota_required, observed_at):
    if not isinstance(rows, list) or len(rows) != 3 or not _finite(observed_at): return False
    if any(not isinstance(row, dict) for row in rows): return False
    if {row.get('provider') for row in rows} != {'codex', 'claude', 'ollama_cloud'}: return False
    available_local = False; fresh = False
    for row in rows:
        local = row.get('local_usage')
        if (not isinstance(local, dict) or local.get('scope') != 'pocket_runs'
                or local.get('period') != 'all_recorded'
                or local.get('availability') not in ('available', 'partial', 'unavailable')): return False
        available_local |= local['availability'] in ('available', 'partial')
        metrics = row.get('metrics', [])
        if not isinstance(metrics, list) or len(metrics) > 64: return False
        if row['provider'] != 'codex':
            if row.get('availability') not in ('not_configured', 'unavailable', 'stale', 'failed', 'partial'): return False
            if row.get('value') is not None: return False
            for metric in metrics:
                if not isinstance(metric, dict) or metric.get('availability') not in ('stale', 'unavailable'): return False
                fetched = metric.get('fetched_at')
                if fetched is not None and (not _finite(fetched) or fetched > observed_at): return False
            continue
        if row.get('availability') not in ('not_configured', 'available', 'partial', 'unavailable', 'stale', 'failed'): return False
        if not metrics and row.get('availability') in ('available', 'partial'): return False
        windows = set()
        for metric in metrics:
            if not isinstance(metric, dict): return False
            window = metric.get('window')
            if window not in ('codex:primary', 'codex:secondary') or window in windows: return False
            windows.add(window)
            if (metric.get('provider') != 'codex' or metric.get('category') != 'subscription'
                    or metric.get('unit') != 'percent_remaining' or metric.get('source') != 'Codex app account limits'): return False
            duration = metric.get('window_minutes')
            if duration is not None and (type(duration) is not int or not 1 <= duration <= 4294967295): return False
            if metric.get('availability') == 'available':
                value, fetched, reset = metric.get('value'), metric.get('fetched_at'), metric.get('resets_at')
                if (not _finite(value) or not 0 <= value <= 100 or not _finite(fetched)
                        or not 0 <= observed_at - fetched < 300): return False
                if reset is not None and (not _finite(reset) or reset <= observed_at): return False
                fresh = True
            elif metric.get('availability') not in ('unavailable', 'stale'): return False
    return available_local and (fresh or not quota_required)


def user_env():
    uid = str(os.getuid())
    return dict(os.environ, XDG_RUNTIME_DIR='/run/user/' + uid,
                DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/' + uid + '/bus')


def bounded_run(argv, *, timeout, env=None, cwd=None, input_bytes=None):
    """Drain each pipe into a capped buffer; never allocate complete output first.

    Only this exact subprocess is killed on timeout/overflow. Daemon drainers own
    their pipe handles, so an inherited pipe cannot block cleanup of the caller.
    No external exception text or stderr is printed by this helper.
    """
    require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 330, 'invalid_deadline')
    require(input_bytes is None or isinstance(input_bytes, bytes) and len(input_bytes) <= 6 * MAX_BYTES, 'input_limit')
    process = subprocess.Popen(argv, env=env, cwd=cwd,
        stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event(); io_failed = threading.Event()
    def drain(stream, destination):
        try:
            while True:
                chunk = stream.read(min(4096, MAX_BYTES + 1))
                if not chunk: return
                if len(destination) + len(chunk) > MAX_BYTES:
                    overflow.set()
                    try: process.kill()
                    except OSError: pass
                    return
                destination.extend(chunk)
        except (OSError, ValueError): io_failed.set()
    threads = [threading.Thread(target=drain, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    for thread in threads: thread.start()
    if input_bytes is not None:
        def supply():
            try: process.stdin.write(input_bytes); process.stdin.close()
            except (OSError, ValueError): io_failed.set()
        threads.append(threading.Thread(target=supply, daemon=True)); threads[-1].start()
    try:
        process.wait(timeout=timeout)
        cleanup_deadline = time.monotonic() + 2
        for thread in threads: thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
        require(not overflow.is_set(), 'subprocess_output_limit')
        require(not io_failed.is_set() and not any(thread.is_alive() for thread in threads), 'subprocess_io_incomplete')
        return subprocess.CompletedProcess(argv, process.returncode,
            bytes(buffers[0]).decode('utf-8'), bytes(buffers[1]).decode('utf-8', errors='replace'))
    except BaseException:
        if process.poll() is None: process.kill()
        try: process.wait(timeout=2)
        except Exception: pass
        raise
    finally:
        # Do not close a buffered handle from a thread blocked inside its read.
        # Normal completion has joined both readers, making their close bounded.
        for thread, stream in zip(threads[:2], (process.stdout, process.stderr)):
            if not thread.is_alive(): stream.close()


def control(*args, check=True):
    result = bounded_run(['systemctl', '--user', *args], env=user_env(), timeout=20)
    require(not check or result.returncode == 0, 'service_control_failed')
    return result


def runtime(mode, timeout=35):
    require(mode in ('--health-only', '--probe-before', '--probe-after', '--probe-active'), 'invalid_runtime_mode')
    result = bounded_run(['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
        '-p', 'EnvironmentFile=' + DEVICE_ENV, PYTHON, '-B', str(STAGE / 'quota_remote_020.py'), mode],
        env=user_env(), timeout=timeout)
    value = parse_json(result.stdout.encode())
    require(result.returncode == 0 and value.get('ok') is True, 'readiness_failed')
    return value


def request(method, route, *, auth=True, timeout=5):
    require(method == 'GET' and route in GET_ROUTES, 'route_not_allowed')
    require(auth or route == '/targets/linux/models', 'route_not_allowed')
    context = ssl.create_default_context(cafile=str(CA))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection('192.0.2.10', 8443, context=context, timeout=timeout)
    headers = {}
    if auth: headers['Authorization'] = 'Bearer ' + os.environ['POCKET_DEVICE_TOKEN']
    try:
        connection.request('GET', route, headers=headers)
        require(connection.sock is not None, 'tls_identity_unavailable')
        tls = {'version': connection.sock.version(), 'leaf_sha256': hashlib.sha256(
            connection.sock.getpeercert(binary_form=True)).hexdigest()}
        response = connection.getresponse()
        raw = response.read(65537)
        require(len(raw) <= 65536, 'response_limit')
        return response.status, parse_json(raw), tls
    finally:
        connection.close()


def probe(resident, *, quota_required=False):
    result = {'at_utc': now(), **counters()}
    for route, label, auth in [(GET_ROUTES[0], 'health', True), (GET_ROUTES[1], 'status', True),
            (GET_ROUTES[2], 'usage', True), (GET_ROUTES[3], 'models', True), (GET_ROUTES[3], 'unauth_models', False)]:
        code, body, tls = request('GET', route, auth=auth)
        result[label + '_http'] = code
        if label == 'status':
            require(isinstance(body, dict), 'invalid_readiness')
            result['backend_available'] = body.get('backend') == 'available'
            result['voice_capabilities_ok'] = body.get('voice') == CAPABILITIES
            result['reply_capabilities_ok'] = body.get('reply_delivery') == REPLY_DELIVERY
            stt = (body.get('voice_runtime') or {}).get('stt') or {}
            names = ('resident', 'ready', 'generation', 'active', 'pending', 'worker_pid',
                'worker_rss_bytes', 'model_load_us', 'startup_us', 'restart_count')
            result['resident_stt'] = {name: stt[name] for name in names if name in stt}
        if label == 'usage':
            result['quota_ready'] = usage_ready(body, quota_required=quota_required, observed_at=time.time())
            result['local_usage_preserved'] = usage_ready(body, quota_required=False, observed_at=time.time())
            result['fresh_codex_metrics'] = []
            if isinstance(body, list):
                for row in body:
                    if isinstance(row, dict) and row.get('provider') == 'codex':
                        for metric in row.get('metrics', []):
                            if isinstance(metric, dict) and metric.get('availability') == 'available':
                                result['fresh_codex_metrics'].append({name: metric.get(name) for name in
                                    ('window', 'value', 'unit', 'source', 'fetched_at', 'window_minutes', 'resets_at')})
        if label == 'models': result['catalog_ok'] = body == CATALOG
        if tls: result['tls'] = tls
    result['ok'] = bool(result['health_http'] == result['status_http'] == result['usage_http'] == result['models_http'] == 200
        and result['unauth_models_http'] == 401 and result['backend_available']
        and result['voice_capabilities_ok'] and result['reply_capabilities_ok'] and result['catalog_ok']
        and result['quota_ready'] and result['local_usage_preserved']
        and (not resident or resident_ready(result['resident_stt'])))
    return result


def states(connection):
    return {table: dict(connection.execute('SELECT state,COUNT(*) FROM ' + table + ' GROUP BY state'))
            for table in ('tasks', 'audio_jobs', 'speech_jobs')}


def active_count(connection):
    return (connection.execute("SELECT COUNT(*) FROM tasks WHERE state NOT IN ('completed','failed','cancelled')").fetchone()[0]
        + connection.execute("SELECT COUNT(*) FROM audio_jobs WHERE state IN ('capturing','processing')").fetchone()[0]
        + connection.execute("SELECT COUNT(*) FROM speech_jobs WHERE state IN ('processing','ready')").fetchone()[0])


def service_state(service=SERVICE):
    shown = control('show', service, '--property=ActiveState,SubState,MainPID,Job', check=False)
    require(shown.returncode == 0, 'service_state_unavailable')
    return dict(line.split('=', 1) for line in shown.stdout.splitlines() if '=' in line)


def no_unit_job(value):
    return value.get('Job') in ('0', '', '0 /')


def stable_running(value):
    return (value.get('ActiveState') == 'active' and no_unit_job(value)
            and value.get('MainPID', '0').isdigit() and int(value['MainPID']) > 0)


def known_stopped(value):
    return value.get('ActiveState') in ('inactive', 'failed') and value.get('MainPID') == '0' and no_unit_job(value)


@contextmanager
def defer_operator_signals(receipt):
    previous = {}
    def defer(signum, frame): receipt['operator_signal_deferred'] = True
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', signal.SIGTERM)):
            if signum not in previous: previous[signum] = signal.signal(signum, defer)
        yield
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)


def stop_when_idle(receipt, label):
    guard(Path(DB).absolute())
    gate = sqlite3.connect(str(DB), timeout=2, isolation_level=None)
    try:
        gate.execute('BEGIN IMMEDIATE')
        receipt[label + '_states'] = states(gate)
        require(active_count(gate) == 0, 'owner_work_active')
        receipt[label + '_stop_attempted'] = True
        with defer_operator_signals(receipt):
            try: receipt[label + '_stop_exit'] = control('stop', SERVICE, check=False).returncode
            except Exception: receipt[label + '_stop_client_uncertain'] = True
            # Keep THIS original admission lock across arbitrarily prolonged or
            # uncertain systemd transition. The detached operator supervises it.
            notice_at = time.monotonic() + 110
            while True:
                try: observed = receipt[label + '_service'] = service_state()
                except Exception: observed = {}; receipt[label + '_state_unavailable'] = True
                if known_stopped(observed):
                    receipt.pop('recovery_pending', None)
                    return
                require(not stable_running(observed), 'stop_rejected_stable_running')
                if time.monotonic() >= notice_at:
                    receipt['recovery_pending'] = True
                    receipt['phase'] = label + '_stop_reconciliation'
                    try: save(receipt.get('operation_phase', 'deployment') + '-progress.json', receipt)
                    except Exception: pass  # Logging cannot release an unresolved admission gate.
                    notice_at = time.monotonic() + 10
                time.sleep(.25)
    finally:
        gate.rollback(); gate.close()


def atomic_bytes(path, raw, *, mode=0o600, replace=False):
    path = Path(path).absolute()
    require(len(raw) <= MAX_BYTES, 'input_limit')
    parent = guard(path.parent, directory=True)
    previous = digest(path) if os.path.lexists(path) else None
    require(replace or previous is None, 'known_path_collision')
    temporary = path.with_name('.quotaops-' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), mode)
    created = True
    try:
        if os.name == 'posix': os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(fd)
        # Windows replacement requires the owned temporary handle to be closed.
        # Its contents have already been flushed and fsynced before this point.
        os.close(fd)
        fd = None
        guard(temporary)
        require((parent.st_dev, parent.st_ino) == (path.parent.lstat().st_dev, path.parent.lstat().st_ino), 'path_changed')
        require((digest(path) if os.path.lexists(path) else None) == previous, 'path_changed')
        os.replace(temporary, path)
        created = False
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if fd is not None: os.close(fd)
        if created: temporary.unlink(missing_ok=True)


def save(name, value, *, exclusive=False):
    require(name in ('codex-channel.json','codex-push-result.json') or re.fullmatch(r'(deployment|activation|rollback|verification)-(dispatched|started|progress|result)\.json', name) is not None,
            'invalid_receipt_name')
    guard(STAGE, directory=True, private=True)
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if exclusive:
        # The marker itself is the atomic O_EXCL claim. A crash may leave a
        # partial marker, which still blocks retry and demands inspection.
        exclusive_bytes(STAGE / name, raw)
    else:
        atomic_bytes(STAGE / name, raw, replace=True)


def exclusive_bytes(path, raw):
    require(len(raw) <= MAX_BYTES, 'input_limit')
    guard(path.parent, directory=True, private=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    except FileExistsError:
        raise OpsError('exclusive_claim_exists_inspect') from None
    try:
        if os.name == 'posix': os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


def fsync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)


@contextmanager
def operation_lock():
    """One OS-held lock for the complete mutating operator, including recovery.

    The fixed private file is retained after release. A process exit releases the
    lock; file presence never means either a live or a dead operator.
    """
    guard(STAGE, directory=True, private=True)
    path = STAGE / OPERATION_LOCK
    if not os.path.lexists(path):
        try: exclusive_bytes(path, OPERATION_LOCK_BYTES)
        except OpsError as error:
            if str(error) != 'exclusive_claim_exists_inspect': raise
    before = guard(path, private=True)
    require(read_file(path, private=True) == OPERATION_LOCK_BYTES, 'operation_lock_collision')
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    held = False
    try:
        require(revision(os.fstat(fd)) == revision(before)
                and revision(guard(path, private=True)) == revision(before), 'path_changed')
        try:
            if os.name == 'posix':
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == 'nt':
                import msvcrt
                # Windows checks the requested read range, including past EOF.
                # Keep the byte beyond read_file's bounded capture range.
                os.lseek(fd, OPERATION_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else: raise OpsError('operation_lock_unavailable')
        except OSError:
            raise OpsError('operator_already_running_inspect') from None
        held = True
        yield
    finally:
        try:
            if held:
                if os.name == 'posix': fcntl.flock(fd, fcntl.LOCK_UN)
                elif os.name == 'nt':
                    os.lseek(fd, OPERATION_LOCK_OFFSET, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally: os.close(fd)


def operator_process_exists(pid):
    require(type(pid) is int and pid > 0, 'operator_identity_incomplete_inspect')
    return Path('/proc', str(pid)).exists()


def supervisor_identity(phase, acceptance_sha256=None):
    """Reconcile child-owned identity; a missing dispatch PID proves nothing."""
    require(phase in PHASES, 'invalid_phase')
    records = {marker: read_json(STAGE / (phase + '-' + marker + '.json'))
               for marker in MARKERS if os.path.lexists(STAGE / (phase + '-' + marker + '.json'))}
    if not records: return None
    require('dispatched' in records and 'started' in records
            and all(isinstance(value, dict) for value in records.values()), 'operator_identity_incomplete_inspect')
    dispatch, child = records['dispatched'], records['started']
    operation_id, pid = child.get('operation_id'), child.get('operator_pid')
    accepted = dispatch.get('acceptance_sha256')
    require(isinstance(operation_id, str) and re.fullmatch('[0-9a-f]{32}', operation_id) is not None
            and type(pid) is int and pid > 0 and valid_hash(accepted), 'operator_identity_incomplete_inspect')
    require(acceptance_sha256 is None or accepted == acceptance_sha256, 'operator_identity_inconsistent_inspect')
    for marker, value in records.items():
        require(value.get('operation_id') == operation_id and value.get('acceptance_sha256') == accepted
                and value.get('operation_phase', phase) == phase, 'operator_identity_inconsistent_inspect')
        if marker != 'dispatched' or 'operator_pid' in value:
            require(type(value.get('operator_pid')) is int and value['operator_pid'] == pid,
                    'operator_identity_inconsistent_inspect')
    return {'operator_pid': pid, 'operation_id': operation_id, 'receipts': records}


def prior_operators_absent(phase, acceptance_sha256):
    result = {}
    for previous in PHASES:
        if previous == phase: continue
        identity = supervisor_identity(previous, acceptance_sha256)
        if identity:
            require(not operator_process_exists(identity['operator_pid']), previous + '_operator_still_supervising')
            result[previous] = identity['receipts']
    return result


def stage_expected(bundle):
    expected = {name: bundle['acceptance']['artifact_hashes'].get(name) for name in bundle_files(bundle)}
    expected.update(bundle['acceptance']['implementation_hashes'])
    expected.update(hashes(bundle['acceptance']['collector_files']))
    expected['quotaops-acceptance.json'] = bundle['acceptance_sha256']
    expected.update({phase + '-' + marker + '.json': None for phase in PHASES for marker in MARKERS})
    expected['verification-result.json'] = None
    expected['codex-channel.json'] = None
    expected['codex-push-result.json'] = None
    expected[OPERATION_LOCK] = hashlib.sha256(OPERATION_LOCK_BYTES).hexdigest()
    for name in (STAGE_MANIFEST, 'quotaops-tests-dispatched.json', 'quotaops-tests-started.json', 'quotaops-tests.json', 'quotaops-linux.log'):
        expected.setdefault(name, None)
    return expected


def offline_plan(blobs):
    plan = parse_json(blobs[STAGE_MANIFEST])
    require(plan.get('version') == 1 and plan.get('fixture_only') is True
            and plan.get('backend_identity') == BACKEND_IDENTITY and plan.get('backend_pins') == BACKEND_PINS,
            'offline_stage_binding')
    require(set(plan['implementation_hashes']) == set(IMPLEMENTATIONS), 'offline_stage_binding')
    for name, expected in {**BACKEND_PINS, **QUALIFICATION_PINS, **plan['implementation_hashes']}.items():
        require(valid_hash(expected) and hashlib.sha256(blobs[name]).hexdigest() == expected, 'offline_stage_binding')
    require(candidate_identity(parse_json(blobs['manifest-R3.json'])) == BACKEND_IDENTITY, 'backend_binding')
    return plan


def decode_payload(payload):
    require(isinstance(payload, dict) and set(payload) == {'files'} and isinstance(payload['files'], dict)
            and len(payload['files']) <= 24, 'invalid_payload')
    blobs = {}; total = 0
    try:
        for name, encoded in payload['files'].items():
            require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) is not None
                    and isinstance(encoded, str) and len(encoded) <= 2 * MAX_BYTES, 'invalid_payload')
            raw = base64.b64decode(encoded, validate=True)
            total += len(raw)
            require(len(raw) <= MAX_BYTES and total <= MAX_PAYLOAD, 'input_limit')
            blobs[name] = raw
    except (ValueError, TypeError):
        raise OpsError('invalid_payload') from None
    return blobs


def stage_offline(payload):
    blobs = decode_payload(payload)
    require(set(blobs) == {*OFFLINE_FILES, STAGE_MANIFEST}, 'invalid_payload')
    plan = offline_plan(blobs)
    expected = {name: hashlib.sha256(raw).hexdigest() for name, raw in blobs.items()}
    if os.path.lexists(STAGE): validate_scope(STAGE, expected)
    else:
        guard(STAGE.parent, directory=True)
        STAGE.mkdir(mode=0o700)
        fsync_directory(STAGE.parent)
    guard(STAGE, directory=True, private=True)
    for name in (*OFFLINE_FILES, STAGE_MANIFEST):
        if not os.path.lexists(STAGE / name): exclusive_bytes(STAGE / name, blobs[name])
    validate_scope(STAGE, expected)
    return {'ok': True, 'fixture_only': True, 'production_accepted': False,
            'implementation_hashes': plan['implementation_hashes'], **counters()}


def validate_offline_stage():
    guard(STAGE, directory=True, private=True)
    blobs = {name: read_file(STAGE / name, private=True) for name in (*OFFLINE_FILES, STAGE_MANIFEST)}
    plan = offline_plan(blobs)
    allowed = {*OFFLINE_FILES, STAGE_MANIFEST, *COLLECTOR_FILES, 'codex-producer-resume-20261018-report.json',
        'quotaops-tests-dispatched.json', 'quotaops-tests-started.json', 'quotaops-tests.json',
        'quotaops-linux.log', 'quotaops-review.json', 'quotaops-native-acceptance.json', 'quotaops-acceptance.json',
        'verification-result.json', 'codex-channel.json', 'codex-push-result.json', OPERATION_LOCK, 'rollback', *(p + '-' + m + '.json' for p in PHASES for m in MARKERS)}
    for entry in STAGE.iterdir():
        require(entry.name in allowed or re.fullmatch(r'codex-producer-review-R2-20261018\.json', entry.name) is not None,
                'unknown_scope_content')
        if entry.name == 'rollback': guard(entry, directory=True, private=True)
        else:
            guard(entry, private=True)
            if entry.name == OPERATION_LOCK:
                require(read_file(entry, private=True) == OPERATION_LOCK_BYTES, 'operation_lock_collision')
    return plan


def stage_acceptance(payload):
    plan = validate_offline_stage()
    for phase in PHASES: assert_new_attempt(STAGE, phase)
    blobs = decode_payload(payload)
    bundle = validate_bundle(STAGE, blobs=blobs)
    require(bundle['acceptance']['implementation_hashes'] == plan['implementation_hashes'], 'operations_binding')
    require(set(blobs) == bundle_files(bundle) | {STAGE_MANIFEST}, 'invalid_payload')
    require(blobs[STAGE_MANIFEST] == read_file(STAGE / STAGE_MANIFEST, private=True), 'offline_stage_binding')
    # Existing known files must match. No source, evidence or other operation is overwritten.
    for name, raw in blobs.items():
        if os.path.lexists(STAGE / name): require(read_file(STAGE / name, private=True) == raw, 'known_path_collision')
    for name, raw in blobs.items():
        if not os.path.lexists(STAGE / name): exclusive_bytes(STAGE / name, raw)
    validate_remote_stage(bundle)
    return {'ok': True, 'production_accepted': True, 'activation_executed': False,
            'acceptance_sha256': bundle['acceptance_sha256'], **counters()}


def test_offline():
    require(sys.platform.startswith('linux'), 'linux_fixture_scope_required')
    plan = validate_offline_stage()
    for phase in PHASES: assert_new_attempt(STAGE, phase)
    for name in ('quotaops-tests-dispatched.json', 'quotaops-tests-started.json', 'quotaops-tests.json', 'quotaops-linux.log'):
        require(not os.path.lexists(STAGE / name), 'test_attempt_exists_inspect')
    validate_package(read_json(STAGE / 'manifest-R3.json'))
    claim = {'at_utc': now(), 'operator_pid': os.getpid(), 'fixture_only': True, 'implementation_hashes': plan['implementation_hashes'], **counters()}
    exclusive_bytes(STAGE / 'quotaops-tests-dispatched.json', json.dumps(claim, sort_keys=True).encode())
    exclusive_bytes(STAGE / 'quotaops-tests-started.json', json.dumps(claim, sort_keys=True).encode())
    try:
        completed = bounded_run([PYTHON, '-B', '-m', 'unittest', '-v', 'test_quota_ops_020'],
                                timeout=60, cwd=str(STAGE), env=user_env())
        raw = (completed.stdout + '\n' + completed.stderr).encode('utf-8')
        require(len(raw) <= MAX_BYTES, 'test_output_limit')
        exclusive_bytes(STAGE / 'quotaops-linux.log', raw)
        count = re.search(r'Ran ([0-9]+) tests? in ', completed.stderr)
        require(count is not None, 'test_summary_missing')
        errors = re.search(r'errors=([0-9]+)', completed.stderr)
        failures = re.search(r'failures=([0-9]+)', completed.stderr)
        skips = re.search(r'skipped=([0-9]+)', completed.stderr)
        value = {'ok': completed.returncode == 0, 'fixture_only': True, 'platform': 'linux',
            'tests': int(count.group(1)), 'errors': int(errors.group(1)) if errors else 0,
            'failures': int(failures.group(1)) if failures else 0, 'skipped': int(skips.group(1)) if skips else 0,
            'exit_code': completed.returncode, 'implementation_hashes': plan['implementation_hashes'],
            'log': 'quotaops-linux.log', 'log_sha256': hashlib.sha256(raw).hexdigest(),
            'backend_identity': BACKEND_IDENTITY, 'fixture_directory': str(STAGE), **counters()}
    except Exception:
        value = {'ok': False, 'category': 'offline_test_outcome_requires_inspect', 'fixture_only': True,
                 'implementation_hashes': plan['implementation_hashes'], **counters()}
    exclusive_bytes(STAGE / 'quotaops-tests.json', json.dumps(value, sort_keys=True).encode())
    return value


def validate_remote_stage(bundle):
    plan = validate_offline_stage()
    require(plan['implementation_hashes'] == bundle['acceptance']['implementation_hashes'], 'offline_stage_binding')
    expected = stage_expected(bundle)
    # The sole directory allowed under the operation stage holds exactly the old
    # changed production files, never the database or any protected configuration.
    backup = STAGE / 'rollback'
    if os.path.lexists(backup):
        guard(backup, directory=True, private=True)
        require({p.name for p in backup.iterdir()} == {'pocket'}, 'unknown_scope_content')
        guard(backup / 'pocket', directory=True, private=True)
        validate_scope(backup / 'pocket', {'server.py': bundle['manifest']['baseline_hashes']['pocket/server.py']})
    actual = {p.name for p in STAGE.iterdir()}
    require(actual <= set(expected) | {'rollback'}, 'unknown_scope_content')
    for name in actual - {'rollback'}:
        guard(STAGE / name, private=True)
        if expected[name] is not None: require(digest(STAGE / name) == expected[name], 'known_path_collision')
    require(bundle_files(bundle) <= actual, 'stage_incomplete')
    guard(STAGE, directory=True, private=True)


def validate_package(manifest):
    guard(PACKAGE, directory=True, private=True)
    actual = set()
    for directory, dirs, files in os.walk(PACKAGE, followlinks=False):
        require(len(actual) <= 256, 'input_limit')
        guard(Path(directory), directory=True, private=True)
        for child in dirs: guard(Path(directory) / child, directory=True, private=True)
        for name in files: actual.add((Path(directory) / name).relative_to(PACKAGE).as_posix())
    require(actual == set(manifest['candidate_hashes']), 'staged_package_collision')
    for name, expected in manifest['candidate_hashes'].items():
        require(digest(PACKAGE / name) == expected, 'staged_package_drift')


def check_hashes(manifest, deployed, *, mixed=False):
    for name in manifest['candidate_hashes']:
        if not name.startswith('pocket/'): continue
        path = RELEASE / name
        old, new = manifest['baseline_hashes'].get(name), manifest['candidate_hashes'][name]
        observed = digest(path) if os.path.lexists(path) else None
        expected = (old, new) if mixed and name in CHANGED else (new,) if deployed else (old,)
        require(observed in expected, 'production_hash_drift')
    require({name: digest(path) for name, path in PROTECTED.items()} == manifest['protected_hashes'], 'protected_hash_drift')
    require(not os.path.lexists(RELEASE / '.transport-011-arm.json'), 'diagnostic_armed')
    guard(RELEASE / '.transport-011-used.json')


def require_stopped():
    require(known_stopped(service_state()), 'restore_or_apply_requires_stopped_service')


def backup_delta(manifest):
    require_stopped()
    require(tuple(sorted(manifest['changed_production_files'])) == CHANGED, 'delta_binding')
    backup = STAGE / 'rollback'
    require(not os.path.lexists(backup), 'backup_collision')
    # Validate all destinations first, so an unrelated addition cannot be adopted.
    for name in CHANGED:
        path = RELEASE / name
        observed = digest(path) if os.path.lexists(path) else None
        require(observed == manifest['baseline_hashes'].get(name), 'production_hash_drift')
    backup.mkdir(mode=0o700)
    (backup / 'pocket').mkdir(mode=0o700)
    for name in CHANGED:
        if name in manifest['baseline_hashes']:
            require_stopped()
            atomic_bytes(backup / name, read_file(RELEASE / name))


def apply_delta(manifest):
    require(tuple(sorted(manifest['changed_production_files'])) == CHANGED, 'delta_binding')
    for name in CHANGED:
        require_stopped()
        require(digest(PACKAGE / name) == manifest['candidate_hashes'][name], 'staged_package_drift')
        current = digest(RELEASE / name) if os.path.lexists(RELEASE / name) else None
        require(current == manifest['baseline_hashes'].get(name), 'production_hash_drift')
        atomic_bytes(RELEASE / name, read_file(PACKAGE / name), mode=0o644, replace=current is not None)


def restore_preflight(manifest):
    """Read-only recoverability check, before stopping either owned service."""
    require(tuple(sorted(manifest['changed_production_files'])) == CHANGED, 'delta_binding')
    baseline = True
    for name in CHANGED:
        path = RELEASE / name
        observed = digest(path) if os.path.lexists(path) else None
        require(observed in (manifest['baseline_hashes'].get(name), manifest['candidate_hashes'][name]), 'unknown_restore_revision')
        baseline = baseline and observed == manifest['baseline_hashes'].get(name)
    if not baseline:
        backup = STAGE / 'rollback'
        guard(backup, directory=True, private=True)
        require({entry.name for entry in backup.iterdir()} == {'pocket'}, 'backup_incomplete')
        expected = {Path(name).name: manifest['baseline_hashes'][name] for name in CHANGED if name in manifest['baseline_hashes']}
        validate_scope(backup / 'pocket', expected)
        require({entry.name for entry in (backup / 'pocket').iterdir()} == set(expected), 'backup_incomplete')
    return {'baseline': baseline}


def restore_delta(manifest):
    require_stopped()
    # Recheck the complete set under the held operator lock and stopped service.
    if restore_preflight(manifest)['baseline']: return
    for name in CHANGED:
        require_stopped()
        path = RELEASE / name
        if name in manifest['baseline_hashes']:
            atomic_bytes(path, read_file(STAGE / 'rollback' / name), mode=0o644, replace=True)
        elif os.path.lexists(path):
            require(digest(path) == manifest['candidate_hashes'][name], 'unknown_restore_revision')
            path.unlink()


def closed(manifest=None):
    # Never execute the protected helper: its verify_closed() invokes a forbidden
    # diagnostics GET. Closure evidence here is exclusively local and hash bound.
    if manifest is None: manifest = read_json(STAGE / 'manifest-R3.json')
    expected = manifest['protected_hashes'].get('transport_control')
    require(valid_hash(expected) and digest(RELEASE / 'control_transport_011.py') == expected, 'protected_hash_drift')
    require(not os.path.lexists(RELEASE / '.transport-011-arm.json'), 'diagnostic_armed')
    require(os.path.lexists(RELEASE / '.transport-011-used.json'), 'diagnostic_closure_evidence_missing')
    used = read_file(RELEASE / '.transport-011-used.json')
    return {'ok': True, 'evidence': 'local_markers_and_pinned_helper',
            'helper_sha256': expected, 'used_marker_sha256': hashlib.sha256(used).hexdigest()}


def new_publication(previous_revision, since, metrics):
    path=NUMERIC/'codex.json'
    if not os.path.lexists(path): return False
    observed=guard(path,private=True)
    if previous_revision is not None and list(revision(observed)) == list(previous_revision): return False
    try:
        snapshot=validate_codex_snapshot(read_file(path,private=True,maximum=16384),observed_at=time.time())
        if snapshot['fetched_at'] < since: return False
        expected=[]
        for row in snapshot['metrics']:
            if row['used_percent'] is None or row['resets_at'] is not None and row['resets_at'] <= time.time(): continue
            expected.append({'window':row['window'],'value':100-row['used_percent'],'unit':'percent_remaining',
                'source':'Codex app account limits','fetched_at':snapshot['fetched_at'],
                'window_minutes':row.get('window_minutes'),'resets_at':row['resets_at']})
        return bool(expected) and sorted(metrics,key=lambda m:m['window']) == sorted(expected,key=lambda m:m['window'])
    except (OpsError,TypeError,KeyError,ValueError): return False


def wait_ready(*, quota_required=False, since=None, previous_revision=None):
    deadline = time.monotonic() + (90 if quota_required else 75)
    mode = '--probe-active' if quota_required else '--probe-after'
    while time.monotonic() < deadline:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0: break
            value = runtime(mode, timeout=min(35, remaining))
            if value.get('ok') and (since is None or new_publication(
                    previous_revision, since, value.get('fresh_codex_metrics', []))): return value
        except Exception: pass
        time.sleep(.4)
    raise OpsError('readiness_deadline')


def begin_attempt(phase, operation_id, bundle):
    require(phase in PHASES and re.fullmatch('[0-9a-f]{32}', operation_id or '') is not None, 'invalid_operation_id')
    dispatch = read_json(STAGE / (phase + '-dispatched.json'))
    require(dispatch.get('operation_id') == operation_id and dispatch.get('acceptance_sha256') == bundle['acceptance_sha256'], 'dispatch_binding')
    require('operator_pid' not in dispatch or (type(dispatch['operator_pid']) is int
            and dispatch['operator_pid'] == os.getpid()), 'operator_identity_inconsistent_inspect')
    for marker in ('started', 'progress', 'result'):
        require(not os.path.lexists(STAGE / (phase + '-' + marker + '.json')), 'attempt_already_started_inspect')
    receipt = {'at_utc': now(), 'ok': False, 'phase': 'preflight', 'operation_id': operation_id,
               'operator_pid': os.getpid(), 'operation_phase': phase,
               'acceptance_sha256': bundle['acceptance_sha256'], **counters()}
    save(phase + '-started.json', receipt, exclusive=True)
    return receipt


def gateway_invariants():
    require(control('is-enabled', SERVICE, check=False).stdout.strip() == 'disabled', 'autostart_drift')
    require(control('show', SERVICE, '-p', 'WorkingDirectory', '--value').stdout.strip() == str(RELEASE), 'gateway_cwd_drift')
    require(control('is-active', 'hermes-gateway.service', check=False).stdout.strip() == 'active', 'normal_gateway_unavailable')


def process_cwd(pid):
    require(type(pid) is int and pid > 0, 'invalid_process_identity')
    return str(Path('/proc', str(pid), 'cwd').resolve())


def process_metadata(pid):
    require(type(pid) is int and pid > 0, 'invalid_process_identity')
    path = Path('/proc', str(pid), 'status')
    guard(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try: raw = os.read(fd, 65537)
    finally: os.close(fd)
    require(len(raw) <= 65536, 'process_metadata_limit')
    values = {}
    for line in raw.decode('ascii').splitlines():
        key, _, value = line.partition(':')
        if key in ('Pid', 'PPid', 'Threads'): values[key] = int(value.strip())
        elif key == 'VmRSS': values['rss_bytes'] = int(value.split()[0]) * 1024
    require(values.get('Pid') == pid, 'process_identity_changed')
    return values


def verify_processes(status, pid):
    worker = status['resident_stt']['worker_pid']
    processes = {'bridge': process_metadata(pid), 'stt_worker': process_metadata(worker)}
    require(processes['stt_worker']['PPid'] == pid, 'stt_worker_ownership_changed')
    memory = control('show', SERVICE, '--property=MemoryCurrent,MemoryPeak,TasksCurrent,CPUUsageNSec,NRestarts').stdout
    resources = {key: int(value) for key, value in (line.split('=', 1) for line in memory.splitlines() if '=' in line) if value.isdigit()}
    require(resources.get('NRestarts') == 0, 'unexpected_gateway_restart')
    return {'processes': processes, 'resources': resources}


def same_tls(before, after):
    return (isinstance(before, dict) and isinstance(after, dict)
        and before == after and before.get('version') in ('TLSv1.2', 'TLSv1.3')
        and valid_hash(before.get('leaf_sha256')))


def deploy(operation_id):
    bundle = validate_bundle(STAGE)
    validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle)
        prior_operators_absent('deployment', bundle['acceptance_sha256'])
        validate_package(bundle['manifest'])
        manifest = bundle['manifest']
        check_hashes(manifest, False); gateway_invariants()
        require(stable_running(service_state()), 'original_service_not_stable')
        receipt = begin_attempt('deployment', operation_id, bundle)
        return execute_deployment(manifest, receipt)


def execute_deployment(manifest, receipt):
    """File/service transaction, after the caller's full production acceptance gate.

    Kept separate so synthetic owned file/database fixtures can exercise recovery
    without manufacturing an accepted production source or using real services.
    """
    applied = False
    try:
        receipt['before'] = runtime('--probe-before')
        receipt['diagnostic_before'] = closed(manifest)
        receipt['phase'] = 'idle_stop'; save('deployment-progress.json', receipt)
        stop_when_idle(receipt, 'initial')
        check_hashes(manifest, False)
        backup_delta(manifest)
        receipt['phase'] = 'apply'; save('deployment-progress.json', receipt)
        applied = True
        apply_delta(manifest); check_hashes(manifest, True)
        receipt['phase'] = 'start'; save('deployment-progress.json', receipt)
        control('start', SERVICE)
        receipt['after'] = wait_ready()
        receipt['diagnostic_after'] = closed(manifest)
        check_hashes(manifest, True); gateway_invariants()
        require(same_tls(receipt['before'].get('tls'), receipt['after'].get('tls')), 'tls_identity_changed')
        pid = int(control('show', SERVICE, '-p', 'MainPID', '--value').stdout)
        require(process_cwd(pid) == str(RELEASE), 'gateway_cwd_drift')
        receipt.update(ok=True, backend_deployed=True, quota_activated=False, phase='backend_ready',
                       main_pid=pid, changed_production_files=list(CHANGED), protected_hashes=manifest['protected_hashes'], autostart_disabled=True)
    except Exception as error:
        receipt['category'] = str(error) if isinstance(error, OpsError) else 'deployment_failed'
        if receipt.get('initial_stop_attempted') or applied:
            try:
                running = not applied and stable_running(service_state())
                if applied:
                    check_hashes(manifest, True, mixed=True)
                    restore_preflight(manifest)
                    stop_when_idle(receipt, 'rollback')
                    check_hashes(manifest, True, mixed=True)
                    restore_delta(manifest); check_hashes(manifest, False)
                elif not running: stop_when_idle(receipt, 'recovery')
                if not running: control('start', SERVICE)
                receipt['rollback_readiness'] = wait_ready()
                require(same_tls(receipt['before'].get('tls'), receipt['rollback_readiness'].get('tls')), 'tls_identity_changed')
                check_hashes(manifest, False); gateway_invariants()
                receipt.update(rolled_back=applied, original_service_restored=True)
            except Exception:
                receipt['recovery_pending'] = True
                receipt['recovery_category'] = 'reconciliation_required'
    save('deployment-result.json', receipt, exclusive=True)
    return receipt


def unit_info():
    # Compatibility adapter for inherited rollback checks; this is a receive channel, never a process/service.
    if not os.path.lexists(STAGE/CODEX_CHANNEL): return {'LoadState':'not-found'}
    value=read_json(STAGE/CODEX_CHANNEL)
    return {'LoadState':'loaded','ActiveState':'active' if value.get('enabled') is True else 'inactive',
        'SubState':'channel','MainPID':'0','Job':'0','profile':value.get('profile')}


def owned_quota_unit(value):
    return value.get('profile')==CODEX_PROFILE and value.get('SubState')=='channel' and value.get('MainPID')=='0'


def stop_owned_quota():
    if not os.path.lexists(STAGE/CODEX_CHANNEL): return
    bundle=validate_bundle(STAGE)
    value=read_json(STAGE/CODEX_CHANNEL)
    require(value.get('profile')==CODEX_PROFILE and value.get('acceptance_sha256')==bundle['acceptance_sha256'],'codex_channel_collision')
    value.update(enabled=False,state='revoked'); save(CODEX_CHANNEL,value)
    require(read_json(STAGE/CODEX_CHANNEL)==value,'codex_revoke_uncertain')


def install_runtime(bundle):
    raise OpsError('codex_uses_aw_sender')


def activate(operation_id):
    bundle=validate_bundle(STAGE); validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle); prior_operators_absent('activation',bundle['acceptance_sha256'])
        deployment=read_json(STAGE/'deployment-result.json')
        require(deployment.get('ok') is True and deployment.get('backend_deployed') is True
            and deployment.get('acceptance_sha256') == bundle['acceptance_sha256'],'backend_deployment_required')
        check_hashes(bundle['manifest'],True); gateway_invariants()
        channel=read_channel(bundle); require(channel['state']=='pending','activation_already_active')
        receipt=begin_attempt('activation',operation_id,bundle)
        try:
            receipt['after']=wait_ready(quota_required=True,since=channel['not_before'],previous_revision=channel['previous_revision'])
            require(same_tls(deployment['after'].get('tls'),receipt['after'].get('tls')),'tls_identity_changed')
            check_hashes(bundle['manifest'],True); gateway_invariants()
            channel['state']='active'; save(CODEX_CHANNEL,channel)
            receipt.update(ok=True,backend_deployed=True,quota_activated=True,phase='quota_fresh',
                source_profile=CODEX_PROFILE,automatic_refresh_seconds_after_completion=120,autostart_disabled=True)
        except Exception as error:
            receipt.update(backend_deployed=True,quota_activated=False,category=str(error) if isinstance(error,OpsError) else 'activation_failed')
            try: stop_owned_quota(); receipt['quota_channel_revoked']=True
            except Exception: receipt['recovery_pending']=True
        save('activation-result.json',receipt,exclusive=True)
        return receipt


def rollback(operation_id):
    bundle = validate_bundle(STAGE); validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle)
        previous = prior_operators_absent('rollback', bundle['acceptance_sha256'])
        require('deployment' in previous, 'no_owned_deployment')
        records = previous['deployment']
        original = records.get('result', records.get('progress', records['started']))
        manifest = bundle['manifest']
        check_hashes(manifest, True, mixed=True)
        recovery = restore_preflight(manifest)
        if os.path.lexists(STAGE/CODEX_CHANNEL): stop_owned_quota()
        receipt = begin_attempt('rollback', operation_id, bundle)
        try:
            if recovery['baseline']:
                before = service_state()
                quota = unit_info()
                require(quota.get('LoadState') == 'not-found' or (owned_quota_unit(quota)
                        and known_stopped(quota)), 'baseline_quota_unit_requires_inspect')
                if stable_running(before):
                    # A healthy unchanged baseline needs neither a backup nor a restart.
                    receipt['after'] = runtime('--probe-after')
                    receipt['no_op'] = True
                else:
                    require(known_stopped(before), 'baseline_service_requires_inspect')
                    prior = original.get('before') or {}
                    stopped = original.get('initial_service')
                    require(original.get('ok') is False and prior.get('ok') is True
                            and same_tls(prior.get('tls'), prior.get('tls'))
                            and original.get('initial_stop_attempted') is True
                            and isinstance(stopped, dict) and known_stopped(stopped)
                            and (original.get('recovery_pending') is True if 'result' in records else
                                 original.get('phase') in ('apply', 'start', 'initial_stop_reconciliation',
                                     'rollback_stop_reconciliation', 'recovery_stop_reconciliation'))
                            and all(value.get('ok') is not True and not value.get('original_service_restored')
                                and not value.get('backend_deployed') and value.get('phase') != 'backend_ready'
                                and value.get('before', prior) == prior
                                and value.get('initial_stop_attempted', True) is True
                                for value in records.values()), 'baseline_owned_stop_requires_inspect')
                    guard(Path(DB).absolute())
                    gate = sqlite3.connect(str(DB), timeout=2, isolation_level=None)
                    try:
                        gate.execute('BEGIN IMMEDIATE')
                        receipt['recovery_states'] = states(gate)
                        require(active_count(gate) == 0, 'owner_work_active')
                        check_hashes(manifest, False); gateway_invariants()
                        quota = unit_info()
                        require(quota.get('LoadState') == 'not-found' or (owned_quota_unit(quota)
                                and known_stopped(quota)), 'baseline_quota_unit_requires_inspect')
                        require_stopped()
                        # Hold admission through the sole start; readiness may need SQLite afterwards.
                        control('start', SERVICE)
                    finally:
                        gate.rollback(); gate.close()
                    receipt['after'] = wait_ready()
                    require(resident_ready(receipt['after'].get('resident_stt')), 'baseline_readiness_failed')
                require(receipt['after'].get('ok') is True and same_tls(receipt['after'].get('tls'),
                        receipt['after'].get('tls')), 'baseline_readiness_failed')
                original_tls = (original.get('before') or {}).get('tls')
                if original_tls is not None:
                    require(same_tls(original_tls, receipt['after'].get('tls')), 'tls_identity_changed')
                after = service_state()
                require(stable_running(after) and (not receipt.get('no_op') or before['MainPID'] == after['MainPID'])
                        and process_cwd(int(after['MainPID'])) == str(RELEASE), 'baseline_service_changed')
            else:
                stop_owned_quota()
                stop_when_idle(receipt, 'rollback')
                check_hashes(manifest, True, mixed=True)
                restore_delta(manifest); check_hashes(manifest, False)
                control('start', SERVICE)
                receipt['after'] = wait_ready()
                require(same_tls((original.get('before') or {}).get('tls'), receipt['after'].get('tls')), 'tls_identity_changed')
            gateway_invariants(); check_hashes(manifest, False)
            receipt.update(ok=True, phase='original_backend_restored', original_service_restored=True,
                           quota_activated=False, numeric_last_good_preserved=True)
        except Exception as error:
            receipt.update(recovery_pending=True, category=str(error) if isinstance(error, OpsError) else 'rollback_failed')
        save('rollback-result.json', receipt, exclusive=True)
        return receipt


def verify():
    bundle = validate_bundle(STAGE); validate_remote_stage(bundle)
    deployment = read_json(STAGE / 'deployment-result.json')
    require(deployment.get('ok') is True, 'successful_deployment_required')
    check_hashes(bundle['manifest'], True); gateway_invariants()
    activation_path = STAGE / 'activation-result.json'
    active = os.path.lexists(activation_path) and read_json(activation_path).get('ok') is True
    result = runtime('--probe-active' if active else '--probe-after')
    require(same_tls(deployment['after'].get('tls'), result.get('tls')), 'tls_identity_changed')
    require(closed(bundle['manifest'])['ok'], 'diagnostic_closure_failed')
    if active: read_channel(bundle, allow_pending=False)
    pid = int(control('show', SERVICE, '-p', 'MainPID', '--value').stdout)
    require(pid == deployment['main_pid'] and process_cwd(pid) == str(RELEASE), 'gateway_restarted_or_moved')
    process_status = verify_processes(result, pid)
    value = {'ok': result['ok'], 'at_utc': now(), 'backend_deployed': True, 'quota_activated': active,
        'status': result, 'autostart_disabled': True, 'protected_hashes': bundle['manifest']['protected_hashes'], **process_status, **counters()}
    save('verification-result.json', value)
    return value


def inspect_stage():
    if not os.path.lexists(STAGE): return {'ok': True, 'stage_exists': False, **counters()}
    guard(STAGE, directory=True, private=True)
    result = {'ok': True, 'stage_exists': True, **counters(), 'attempts': {}}
    try:
        plan = validate_offline_stage()
        result['offline_stage'] = {'fixture_only': True, 'implementation_hashes': plan['implementation_hashes']}
    except Exception:
        result.update(ok=False, category='offline_stage_invalid')
    result['production_accepted'] = False
    try:
        bundle = validate_bundle(STAGE); validate_remote_stage(bundle)
        result['accepted_bundle'] = bundle['acceptance_sha256']
        result['production_accepted'] = True
    except Exception:
        if os.path.lexists(STAGE / 'quotaops-acceptance.json'):
            result.update(ok=False, category='production_acceptance_invalid')
        else:
            result['acceptance_state'] = 'pending'
    result['offline_tests'] = {}
    test_fields = {'ok', 'fixture_only', 'platform', 'operator_pid', 'at_utc', 'tests', 'failures', 'errors',
        'skipped', 'exit_code', 'implementation_hashes', 'log', 'log_sha256', 'category', 'backend_identity'}
    for name in ('quotaops-tests-dispatched.json', 'quotaops-tests-started.json', 'quotaops-tests.json'):
        if os.path.lexists(STAGE / name):
            observed = read_json(STAGE / name)
            result['offline_tests'][name] = {key: observed[key] for key in test_fields if key in observed}
    if os.path.lexists(STAGE / 'quotaops-linux.log'):
        result['offline_test_log_sha256'] = digest(STAGE / 'quotaops-linux.log')
    allowed = {'ok', 'phase', 'category', 'recovery_pending', 'operator_pid', 'operation_id',
        'at_utc', 'backend_deployed', 'quota_activated', 'original_service_restored', 'rolled_back',
        'outcome_uncertain', 'acceptance_sha256', 'quota_unit_stopped', 'operation_phase', 'no_op'}
    for phase in PHASES:
        result['attempts'][phase] = {}
        for marker in MARKERS:
            path = STAGE / (phase + '-' + marker + '.json')
            if os.path.lexists(path):
                value = read_json(path)
                result['attempts'][phase][marker] = {key: value[key] for key in allowed if key in value}
        try:
            identity = supervisor_identity(phase)
            result['attempts'][phase]['ownership_state'] = 'child_identity_reconciled' if identity else 'no_attempt'
            result['attempts'][phase]['operator_process_exists'] = operator_process_exists(identity['operator_pid']) if identity else False
        except Exception:
            result['attempts'][phase].update(ownership_state='incomplete_or_inconsistent_inspect', operator_process_exists=None)
    if os.path.lexists(STAGE / 'codex-push-result.json'):
        result['last_codex_push'] = read_json(STAGE / 'codex-push-result.json')
    if result.get('production_accepted') and os.path.lexists(NUMERIC/'codex.json'):
        result['published_codex'] = inspect_codex_publication()
    result['gateway'] = service_state()
    quota = unit_info()
    result['quota_unit'] = {k: quota.get(k) for k in ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'Job')}
    return result


def dispatch(phase):
    require(phase in PHASES, 'invalid_phase')
    bundle = validate_bundle(STAGE); validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle)
        assert_new_attempt(STAGE, phase)
        prior_operators_absent(phase, bundle['acceptance_sha256'])
        operation_id = uuid.uuid4().hex
        claim = {'at_utc': now(), 'operation_id': operation_id,
                 'acceptance_sha256': bundle['acceptance_sha256'], **counters()}
        save(phase + '-dispatched.json', claim, exclusive=True)
    # The child acquires the same lock before writing its own identity or touching
    # service/data. A claim with no child identity remains inspect-only.
    try:
        operator = subprocess.Popen([PYTHON, '-B', str(STAGE / 'quota_remote_020.py'), '--run', phase, operation_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        return {'ok': False, 'outcome_uncertain': True, 'recovery_pending': True,
                'category': 'operator_spawn_outcome_inspect', 'operation_id': operation_id, **counters()}
    claim['operator_pid'] = operator.pid
    try: save(phase + '-dispatched.json', claim)
    except Exception:
        return {'ok': False, 'outcome_uncertain': True, 'recovery_pending': True,
                'category': 'operator_pid_receipt_uncertain_inspect', 'operator_pid': operator.pid,
                'operation_id': operation_id, **counters()}
    deadline = time.monotonic() + 270
    while time.monotonic() < deadline:
        result_path = STAGE / (phase + '-result.json')
        if os.path.lexists(result_path):
            identity = supervisor_identity(phase, bundle['acceptance_sha256'])
            require(identity['operator_pid'] == operator.pid and identity['operation_id'] == operation_id,
                    'operator_identity_inconsistent_inspect')
            # Do not expose success until the detached supervisor has exited and
            # released its operation lock, including its result-file fsync.
            if operator.poll() is not None: return identity['receipts']['result']
            time.sleep(.1)
            continue
        if operator.poll() is not None:
            return {'ok': False, 'outcome_uncertain': True, 'recovery_pending': True,
                    'category': 'operator_exited_without_receipt_inspect', 'operator_pid': operator.pid, **counters()}
        progress = STAGE / (phase + '-progress.json')
        if os.path.lexists(progress) and read_json(progress).get('recovery_pending'):
            return {'ok': False, 'recovery_pending': True, 'category': 'operator_supervising_stop_inspect',
                    'operator_pid': operator.pid, **counters()}
        time.sleep(.5)
    return {'ok': False, 'outcome_uncertain': True, 'recovery_pending': True,
            'category': 'operator_still_supervising_inspect', 'operator_pid': operator.pid, **counters()}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        require(sys.platform.startswith('linux') and Path(__file__).absolute() == STAGE / 'quota_remote_020.py', 'fixed_linux_scope_required')
        if args == ['--health-only']: result = {'ok': request('GET', '/health', timeout=2)[0] == 200, **counters()}
        elif args == ['--probe-before']: result = probe(True)
        elif args == ['--probe-after']: result = probe(True)
        elif args == ['--probe-active']: result = probe(True, quota_required=True)
        elif args == ['--inspect']: result = inspect_stage()
        elif args == ['--verify']: result = verify()
        elif args == ['--prepare-codex']: result = prepare_codex()
        elif args == ['--push-codex']: result = push_codex(sys.stdin.buffer.read(16385))
        elif len(args) == 3 and args[0] == '--run' and args[1] in PHASES:
            result = {'deployment': deploy, 'activation': activate, 'rollback': rollback}[args[1]](args[2])
        else: raise OpsError('unsupported_operation')
    except Exception as error:
        result = {'ok': False, 'category': str(error) if isinstance(error, OpsError) else 'operation_rejected', **counters()}
    print(json.dumps(result, allow_nan=False), flush=True)
    return 0 if result.get('ok') is True else 2 if result.get('recovery_pending') or result.get('outcome_uncertain') else 1


CODEX_PROFILE = 'codex_aw_numeric_v1'
CODEX_CHANNEL = 'codex-channel.json'


def validate_codex_native(native, source_hashes):
    fields={'version','profile','accepted','source_files','private_proof_sha256',
        'helper_files','helper_evidence_sha256','operations_test_sha256','operations_review_sha256'}
    require(isinstance(native,dict) and set(native)==fields,'native_attestation_schema')
    require(type(native['version']) is int and native['version']==2 and native['profile']==CODEX_PROFILE
        and native['accepted'] is True and native['source_files']==source_hashes,'native_attestation_binding')
    require(isinstance(native['helper_files'],dict) and set(native['helper_files'])==
        {'native_aw_qualification.py','test_native_aw_qualification.py'}
        and all(valid_hash(v) for v in native['helper_files'].values()),'native_attestation_binding')
    for key in ('private_proof_sha256','helper_evidence_sha256','operations_test_sha256','operations_review_sha256'):
        require(valid_hash(native[key]),'native_attestation_binding')


def validate_codex_snapshot(raw, *, observed_at, started_at=None):
    require(isinstance(raw, bytes) and len(raw) <= 16384, 'invalid_codex_snapshot')
    value = parse_json(raw)
    require(isinstance(value, dict) and set(value) == {'version','scope','provider','source','fetched_at','metrics'}, 'invalid_codex_snapshot')
    require(type(value['version']) is int and value['version'] == 1 and value['scope'] == 'account'
        and value['provider'] == 'codex' and value['source'] == 'codex_app', 'invalid_codex_snapshot')
    fetched = value['fetched_at']
    require(type(fetched) is int and 0 <= fetched <= 2**53-1 and _finite(observed_at)
        and 0 <= observed_at-fetched < 300, 'stale_codex_snapshot')
    if started_at is not None:
        require(_finite(started_at) and int(started_at) <= fetched and 0 <= observed_at-started_at <= 60, 'stale_codex_snapshot')
    rows=value['metrics']; windows=set(); usable=False
    require(isinstance(rows,list) and 1 <= len(rows) <= 2, 'invalid_codex_snapshot')
    for row in rows:
        require(isinstance(row,dict) and {'window','used_percent','resets_at'} <= set(row)
            and set(row) <= {'window','used_percent','resets_at','window_minutes'}, 'invalid_codex_snapshot')
        require(isinstance(row['window'],str) and row['window'] in ('codex:primary','codex:secondary')
            and row['window'] not in windows, 'invalid_codex_snapshot')
        windows.add(row['window']); used=row['used_percent']; reset=row['resets_at']
        require(used is None or _finite(used) and 0 <= used <= 100, 'invalid_codex_snapshot')
        require(reset is None or type(reset) is int and 0 <= reset <= 2**53-1, 'invalid_codex_snapshot')
        if 'window_minutes' in row:
            require(type(row['window_minutes']) is int and 1 <= row['window_minutes'] <= 4294967295, 'invalid_codex_snapshot')
        usable |= used is not None and (reset is None or reset > observed_at)
    require(usable, 'unusable_codex_snapshot')
    return value


def read_channel(bundle, *, allow_pending=True):
    value = read_json(STAGE / CODEX_CHANNEL)
    require(set(value) == {'version','profile','acceptance_sha256','enabled','state','not_before','expires_at','previous_revision'}
        and value['version'] == 1 and type(value['version']) is int and value['profile'] == CODEX_PROFILE
        and value['acceptance_sha256'] == bundle['acceptance_sha256']
        and value['enabled'] is True and value['state'] in (('pending','active') if allow_pending else ('active',)),
        'codex_channel_disabled')
    require(type(value['not_before']) is int and value['not_before'] >= 0 and type(value['expires_at']) is int
        and value['expires_at'] == value['not_before'] + 300, 'invalid_codex_channel')
    if value['state'] == 'pending': require(time.time() < value['expires_at'], 'codex_activation_expired')
    return value


def prepare_codex():
    bundle=validate_bundle(STAGE); validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle); assert_new_attempt(STAGE,'activation')
        prior_operators_absent('activation',bundle['acceptance_sha256'])
        require(not any(os.path.lexists(STAGE / ('rollback-'+m+'.json')) for m in MARKERS), 'rollback_in_progress')
        deployment=read_json(STAGE/'deployment-result.json')
        require(deployment.get('ok') is True and deployment.get('backend_deployed') is True
            and deployment.get('acceptance_sha256') == bundle['acceptance_sha256'], 'backend_deployment_required')
        check_hashes(bundle['manifest'],True); gateway_invariants()
        require(not os.path.lexists(STAGE/CODEX_CHANNEL),'codex_channel_exists_inspect')
        if not os.path.lexists(NUMERIC):
            guard(NUMERIC.parent,directory=True); NUMERIC.mkdir(mode=0o700)
        validate_scope(NUMERIC,{'codex.json':None,'claude.json':None})
        old=revision(guard(NUMERIC/'codex.json',private=True)) if os.path.lexists(NUMERIC/'codex.json') else None
        started=int(time.time())
        value={'version':1,'profile':CODEX_PROFILE,'acceptance_sha256':bundle['acceptance_sha256'],
            'enabled':True,'state':'pending','not_before':started,'expires_at':started+300,'previous_revision':old}
        save(CODEX_CHANNEL,value,exclusive=True)
        return {'ok':True,'profile':CODEX_PROFILE,'acceptance_sha256':bundle['acceptance_sha256'],
            'not_before':started,'expires_at':started+300,**counters()}


def push_codex(raw):
    bundle=validate_bundle(STAGE); validate_remote_stage(bundle)
    with operation_lock():
        validate_remote_stage(bundle)
        require(not any(os.path.lexists(STAGE/('rollback-'+m+'.json')) for m in MARKERS),'rollback_in_progress')
        channel=read_channel(bundle)
        deployment=read_json(STAGE/'deployment-result.json')
        require(deployment.get('ok') is True and deployment.get('acceptance_sha256') == bundle['acceptance_sha256'], 'backend_deployment_required')
        if channel['state'] == 'active':
            require(read_json(STAGE/'activation-result.json').get('ok') is True,'activation_required')
        check_hashes(bundle['manifest'],True); gateway_invariants()
        value=validate_codex_snapshot(raw,observed_at=time.time())
        require(value['fetched_at'] >= channel['not_before'],'preactivation_snapshot')
        validate_scope(NUMERIC,{'codex.json':None,'claude.json':None})
        sys.path.insert(0,str(RELEASE))
        from pocket.quota_export import publish_snapshot
        from pocket.quota_snapshots import PrivateDirectory, read_private
        with PrivateDirectory(NUMERIC) as directory:
            try: previous=read_private(directory,'codex.json',provider='codex')
            except FileNotFoundError: previous=None
        require(previous is None or value['fetched_at'] > previous['fetched_at'],'codex_replay')
        published=publish_snapshot(value,NUMERIC/'codex.json')
        receipt={'ok':True,'profile':CODEX_PROFILE,'provider':'codex','acceptance_sha256':bundle['acceptance_sha256'],
            'fetched_at':published['fetched_at'],'snapshot_sha256':hashlib.sha256(json.dumps(published,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        save('codex-push-result.json',receipt)
        return receipt
















def inspect_codex_publication():
    try:
        raw=read_file(NUMERIC/'codex.json',private=True,maximum=16384)
        decoded=parse_json(raw)
        value=validate_codex_snapshot(raw,observed_at=decoded.get('fetched_at'))
        canonical=json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')
        return {'provider':'codex','fetched_at':value['fetched_at'],'snapshot_sha256':hashlib.sha256(canonical).hexdigest(),
            'freshness_claimed':False}
    except Exception:
        return {'category':'publication_inspection_rejected','freshness_claimed':False}


if __name__ == '__main__':
    raise SystemExit(main())
