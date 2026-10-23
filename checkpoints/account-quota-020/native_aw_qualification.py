"""Private AW current-account qualification; no discovery or credential access.

The provenance receipt is trusted root-captured, nonsecret CIM evidence, not a
signature or a permission grant. Capture it for the SAME invoking parent process; use that process PID as
current_tool_pid (root Python when it starts this CLI). Do not reuse receipts across
tool calls. This helper does not inspect processes, environment, auth or config.
It can only validate the supplied evidence and executable bytes.

No raw account metadata is returned, printed or written. The returned opaque
binding and proof are AW-private; only snapshot may enter numeric transport.
The caller must impose a 55 second outer process deadline. Per-RPC, IO and
cleanup bounds come unchanged from the pinned reviewed stdio engine.
"""
import argparse
import hashlib
import hmac
import types
import json
import math
import ntpath
import os
from pathlib import Path
import re
import sys
import time


PINNED_EXECUTABLE = r'C:\Users\example\AppData\Local\OpenAI\Codex\bin\896a0166b774037a\codex.exe'
PINNED_EXECUTABLE_SHA256 = 'f6dbf48cb70f62649c645f26c87dc9bf84cc36986202dfad8e12fb5b8ca70312'
PINNED_MANAGED_APP_EXECUTABLE = r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1013.4894.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe'
REVIEWED_CLIENT_SHA256 = '932ffa579860a91f37d2a84cd1e612fb9fd97a5a729b443e19ea5478335c778c'
MAX_RECEIPT_AGE_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 5
MAX_RECEIPT_BYTES = 32768
PARENT_TOTAL_DEADLINE_SECONDS = 55
_CLIENT_PATH = Path(__file__).resolve().parent.parent / 'official_codex_quota.py'


class QualificationError(ValueError):
    """A fixed public error; vendor exceptions and payloads never escape."""


def _fail():
    raise QualificationError('qualification_failed')


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_reviewed_client():
    try:
        # Read, authenticate and execute exactly these bytes. SourceFileLoader
        # may instead use a timestamp-valid divergent .pyc, even under -B.
        with open(_CLIENT_PATH, 'rb') as stream:
            raw = stream.read()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), REVIEWED_CLIENT_SHA256):
            _fail()
        module = types.ModuleType('_aw_pinned_official_codex_quota')
        module.__file__ = str(_CLIENT_PATH)
        module.__package__ = ''
        module.__loader__ = None
        module.__spec__ = None
        exec(compile(raw, str(_CLIENT_PATH), 'exec', dont_inherit=True), module.__dict__)
        return module
    except Exception:
        raise QualificationError('qualification_failed') from None


try:
    official = _load_reviewed_client()
except QualificationError:
    if __name__ == '__main__':
        print('native_aw_qualification: qualification_failed', file=sys.stderr)
        raise SystemExit(1) from None
    raise


class _MetadataOnlyClient(official.CodexQuotaClient):
    """Reuse bounded stdio ownership without exposing quota under a placeholder.

    The reviewed constructor requires a 64-hex expected binding even when no
    binding comparison is requested. Its syntactic placeholder is private to
    this bootstrap class. Both read() and quota _rpc are rejected BEFORE the
    inherited engine is reached. The ordinary reviewed client is constructed
    afresh with the real approved-current-account fingerprint after cleanup.
    No public expected-binding policy or reviewed source is changed.
    """
    def __init__(self, executable, *, rpc_timeout):
        super().__init__(executable, expected_binding='0' * 64, rpc_timeout=rpc_timeout)

    def read(self):
        _fail()

    def _rpc(self, method, params=None, *, timeout=None):
        if method == 'account/read':
            if params != {'refreshToken': False} or type(params.get('refreshToken')) is not bool:
                _fail()
        elif method != 'initialize':
            _fail()
        return super()._rpc(method, params, timeout=timeout)


def _account_binding(data):
    """Match the pinned client's exact type/email/canonical JSON validation."""
    if not isinstance(data, dict):
        _fail()
    account = data.get('account')
    if (not isinstance(account, dict) or account.get('type') != 'chatgpt' or
            'requiresOpenaiAuth' in data and data['requiresOpenaiAuth'] is not True):
        _fail()
    email = account.get('email')
    if (not isinstance(email, str) or not 1 <= len(email) <= 320 or
            email != email.strip() or any(ord(c) < 32 for c in email)):
        _fail()
    identity = json.dumps({'type': 'chatgpt', 'email': email}, sort_keys=True,
                          separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hashlib.sha256(identity).hexdigest()


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= official.MAX_TIMESTAMP


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _path_equal(left, right):
    return (isinstance(left, str) and isinstance(right, str) and
            '\x00' not in left and '\x00' not in right and
            ntpath.isabs(left) and ntpath.isabs(right) and
            ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right)))


def _validate_receipt(receipt, executable, executable_sha256):
    keys = {'version', 'capture_kind', 'linkage_policy', 'captured_at',
            'executable', 'executable_sha256', 'current_tool_pid',
            'current_context', 'no_account_profile_override',
            'inherited_default_codex_home', 'executor_forced_login_method',
            'managed_app', 'ancestry', 'app_server', 'executor'}
    now = time.time()
    if (not isinstance(receipt, dict) or set(receipt) != keys or
            type(receipt['version']) is not int or receipt['version'] != 1 or
            receipt['capture_kind'] != 'root_cim_current_tool_graph_v1' or
            receipt['linkage_policy'] != 'shared_managed_app_parent_v1' or
            not _integer(receipt['captured_at']) or
            not -MAX_FUTURE_SKEW_SECONDS <= now - receipt['captured_at'] <= MAX_RECEIPT_AGE_SECONDS or
            not _path_equal(executable, PINNED_EXECUTABLE) or
            not _path_equal(receipt['executable'], executable) or
            not _sha256(executable_sha256) or
            not hmac.compare_digest(executable_sha256, PINNED_EXECUTABLE_SHA256) or
            not _sha256(receipt['executable_sha256']) or
            not hmac.compare_digest(receipt['executable_sha256'], executable_sha256) or
            any(receipt[name] is not True for name in ('current_context',
                'no_account_profile_override', 'inherited_default_codex_home')) or
            receipt['executor_forced_login_method'] != 'chatgpt' or
            not _integer(receipt['current_tool_pid'], 1) or
            receipt['current_tool_pid'] != os.getppid()):
        _fail()
    ancestry = receipt['ancestry']
    if not isinstance(ancestry, list) or not 3 <= len(ancestry) <= 32:
        _fail()
    node_keys = {'pid', 'parent_pid', 'executable', 'created_at',
                 'account_profile_override_absent'}
    seen = set()
    for index, row in enumerate(ancestry):
        if (not isinstance(row, dict) or set(row) != node_keys or
                not _integer(row['pid'], 1) or row['pid'] in seen or
                not _integer(row['parent_pid'], 1) or row['parent_pid'] == row['pid'] or
                not isinstance(row['executable'], str) or '\x00' in row['executable'] or
                not ntpath.isabs(row['executable']) or
                not _integer(row['created_at']) or row['created_at'] > receipt['captured_at'] or
                row['account_profile_override_absent'] is not True):
            _fail()
        seen.add(row['pid'])
        if index and (ancestry[index - 1]['parent_pid'] != row['pid'] or
                      ancestry[index - 1]['created_at'] < row['created_at']):
            _fail()
    managed = receipt['managed_app']
    if (not isinstance(managed, dict) or
            set(managed) != {'pid', 'executable', 'created_at'} or
            not _integer(managed['pid'], 1) or not _integer(managed['created_at']) or
            any(managed[key] != ancestry[-1][key] for key in managed) or
            ancestry[0]['pid'] != receipt['current_tool_pid'] or
            ancestry[-1]['parent_pid'] in seen):
        _fail()
    # The trusted CIM capture must identify the official installed managed app.
    # A same-name process elsewhere cannot establish this app-context linkage.
    if not _path_equal(managed['executable'], PINNED_MANAGED_APP_EXECUTABLE):
        _fail()
    for name, command, allowed_keys in (
            ('app_server', 'app-server', {'features.code_mode_host', 'features.prefer_mxc',
                                          'bundled.mcp_servers.codex_app.enabled', 'review.enabled'}),
            ('executor', 'exec-server', {'features.prefer_mxc', 'forced_login_method'})):
        process = receipt[name]
        extra_keys = {'config_boolean_features'} if name == 'app_server' else set()
        if (not isinstance(process, dict) or
                set(process) != node_keys | {'command', 'config_keys'} | extra_keys or
                not _integer(process['pid'], 1) or
                not _integer(process['parent_pid'], 1) or
                process['parent_pid'] != managed['pid'] or
                not _path_equal(process['executable'], executable) or
                not _integer(process['created_at']) or
                not managed['created_at'] <= process['created_at'] <= receipt['captured_at'] or
                process['command'] != command or
                process['account_profile_override_absent'] is not True or
                not isinstance(process['config_keys'], list) or
                any(not isinstance(key, str) or key not in allowed_keys
                    for key in process['config_keys']) or
                len(set(process['config_keys'])) != len(process['config_keys'])):
            _fail()
        if name == 'app_server':
            # These two exact optional feature keys need trusted-root literal
            # TOML boolean evidence. No value is copied into child argv.
            features = process['config_boolean_features']
            present = set(process['config_keys']) & {
                'bundled.mcp_servers.codex_app.enabled', 'review.enabled'}
            if (not isinstance(features, dict) or set(features) != present or
                    any(type(value) is not bool for value in features.values())):
                _fail()
    executor, server = receipt['executor'], receipt['app_server']
    if (any(executor[key] != ancestry[-2][key] for key in node_keys) or
            executor['pid'] == server['pid'] or server['pid'] in seen or
            not hmac.compare_digest(_file_sha256(executable), executable_sha256)):
        _fail()

def _require_clean_close(client):
    failed = client.close()
    child = client.child
    if (failed or not client.closed or child is None or child.returncode != 0 or
            not child.stdin.closed or not child.stdout.closed or
            any(thread is not None and thread.is_alive() for thread in (client.reader, client.writer))):
        _fail()


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        _fail()


def qualify_current_account(*, receipt, executable, executable_sha256,
                            already_approved_current_account=False,
                            rpc_timeout=5, total_timeout=48):
    """Return {snapshot, proof} only after BOTH owned children close cleanly.

    All caller-supplied receipt facts must originate in root's safe current-tool
    CIM capture. A malicious fabricated receipt is outside this trust boundary.
    This operation does not establish owner approval; the explicit flag records
    the already-established authorization for this current app context only.
    """
    try:
        if (already_approved_current_account is not True or
                type(rpc_timeout) not in (int, float) or not math.isfinite(rpc_timeout) or
                not 0 < rpc_timeout <= 30 or type(total_timeout) not in (int, float) or
                not math.isfinite(total_timeout) or not 0 < total_timeout <= PARENT_TOTAL_DEADLINE_SECONDS or
                2 * max(5, rpc_timeout) + 6 * rpc_timeout + 8 > total_timeout):
            _fail()
        deadline = time.monotonic() + total_timeout
        _validate_receipt(receipt, executable, executable_sha256)
        # Recheck reviewed engine bytes for every run, including long-lived callers.
        if not hmac.compare_digest(_file_sha256(_CLIENT_PATH), REVIEWED_CLIENT_SHA256):
            _fail()
        source_files = {'native_aw_qualification.py': _file_sha256(Path(__file__).resolve()),
                        'official_codex_quota.py': REVIEWED_CLIENT_SHA256}
        _check_deadline(deadline)
        metadata = _MetadataOnlyClient(executable, rpc_timeout=rpc_timeout)
        try:
            metadata.__enter__()
            binding = _account_binding(metadata._rpc('account/read', {'refreshToken': False}))
        finally:
            _require_clean_close(metadata)
        _check_deadline(deadline)
        # Hash and receipt freshness are rechecked before the second child too.
        _validate_receipt(receipt, executable, executable_sha256)
        client = official.CodexQuotaClient(executable, expected_binding=binding,
                                          rpc_timeout=rpc_timeout)
        try:
            client.__enter__()
            _check_deadline(deadline)
            read_started_at = int(time.time())
            snapshot = client.read()
            read_completed_at = int(time.time())
        finally:
            _require_clean_close(client)
        _check_deadline(deadline)
        if not read_started_at <= snapshot['fetched_at'] <= read_completed_at:
            _fail()
        proof = {'profile': 'codex_aw_numeric_v1', 'executable': executable,
                 'executable_sha256': executable_sha256,
                 'account_binding_sha256': binding,
                 'approved_app_context_link_verified': True,
                 'account_checks_before_after': True, 'refresh_token_false': True,
                 'read_started_at': read_started_at, 'fetched_at': snapshot['fetched_at'],
                 'read_completed_at': read_completed_at, 'producer_exit_code': 0,
                 'all_children_reaped': True, 'all_pipes_closed': True,
                 'source_files': source_files}
        return {'snapshot': snapshot, 'proof': proof}
    except (KeyboardInterrupt, Exception):
        raise QualificationError('qualification_failed') from None


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        _fail()


def main(argv=None):
    try:
        parser = _Arguments(add_help=False, allow_abbrev=False)
        parser.add_argument('--receipt', required=True)
        parser.add_argument('--executable', required=True)
        parser.add_argument('--executable-sha256', required=True)
        parser.add_argument('--already-approved-current-account', action='store_true')
        parser.add_argument('--rpc-timeout', type=float, default=5)
        parser.add_argument('--total-timeout', type=float, default=48)
        args = parser.parse_args(argv)
        with open(args.receipt, 'rb') as stream:
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(raw) > MAX_RECEIPT_BYTES:
            _fail()
        receipt = official._decode(raw)
        result = qualify_current_account(receipt=receipt, executable=args.executable,
                                         executable_sha256=args.executable_sha256,
                                         already_approved_current_account=args.already_approved_current_account,
                                         rpc_timeout=args.rpc_timeout, total_timeout=args.total_timeout)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
        return 0
    except (KeyboardInterrupt, Exception):
        print('native_aw_qualification: qualification_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
