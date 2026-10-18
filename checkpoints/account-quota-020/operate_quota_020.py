"""Fixed-scope root-operated quota tooling; importing performs no transport.

stage/test are code-only offline fixture operations. accept/deploy/activate/
verify/rollback require the concrete full acceptance record. Inspect is the only
next action after an uncertain transport or operator result.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

import quota_remote_020 as ops

ROOT = Path(__file__).resolve().parent
MODES = ('validate', 'stage', 'test', 'accept', 'inspect', 'deploy', 'activate', 'verify', 'rollback')
PHASE = {'deploy': 'deployment', 'activate': 'activation', 'rollback': 'rollback'}


def outcome_code(value):
    if not isinstance(value, dict): return 1
    if value.get('recovery_pending') is True or value.get('outcome_uncertain') is True:
        return 2
    return 0 if value.get('ok') is True else 1


def ssh_argv():
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=5', 'pocket-host.example.invalid', '/opt/pocket-checkpoint/owner/.hermes/hermes-agent/venv/bin/python', '-B', '-']


def make_stage_payload(root):
    root = Path(root)
    bodies = {name: ops.read_file(root / name) for name in ops.OFFLINE_FILES}
    for name, expected in ops.BACKEND_PINS.items():
        ops.require(hashlib.sha256(bodies[name]).hexdigest() == expected, 'backend_binding')
    plan = {'version': 1, 'fixture_only': True, 'backend_identity': ops.BACKEND_IDENTITY,
            'backend_pins': ops.BACKEND_PINS,
            'implementation_hashes': {name: hashlib.sha256(bodies[name]).hexdigest() for name in ops.IMPLEMENTATIONS}}
    bodies[ops.STAGE_MANIFEST] = json.dumps(plan, sort_keys=True).encode()
    return {'files': {name: base64.b64encode(raw).decode('ascii') for name, raw in bodies.items()}}


def make_acceptance_payload(root):
    bundle = ops.validate_bundle(root)
    stage = make_stage_payload(root)
    bodies = {name: ops.read_file(Path(root) / name) for name in ops.bundle_files(bundle)}
    bodies[ops.STAGE_MANIFEST] = base64.b64decode(stage['files'][ops.STAGE_MANIFEST], validate=True)
    # Check the bytes that will be sent, including a second source/evidence check.
    ops.validate_bundle(root, blobs=bodies)
    return {'files': {name: base64.b64encode(raw).decode('ascii') for name, raw in bodies.items()}}


def build_script(mode, payload, root=ROOT):
    ops.require(mode in MODES and mode != 'validate', 'unsupported_operation')
    raw = ops.read_file(Path(root) / 'quota_remote_020.py')
    source = base64.b64encode(raw).decode('ascii')
    wire = json.dumps(payload, separators=(',', ':')).encode('ascii')
    ops.require(len(wire) <= 5 * ops.MAX_BYTES, 'input_limit')
    encoded = base64.b64encode(wire).decode('ascii')
    # Bootstrap is the exact local reviewed helper, verified before compilation.
    # The invoked functions revalidate all fixed remote paths and staged hashes.
    script = """import base64,hashlib,json,sys
raw=base64.b64decode(@@SOURCE@@,validate=True)
if hashlib.sha256(raw).hexdigest()!=@@DIGEST@@ or not sys.platform.startswith('linux'):
 print(json.dumps({'ok':False,'category':'bootstrap_rejected'}),flush=True);raise SystemExit(1)
scope={'__name__':'quotaops_bootstrap','__file__':'/opt/pocket-checkpoint/owner/.hermes/pocket/gateway-only-20261015/staging/account-quota-020-ops/quota_remote_020.py'}
exec(compile(raw,scope['__file__'],'exec'),scope)
try:
 payload=json.loads(base64.b64decode(@@PAYLOAD@@,validate=True))
 mode=@@MODE@@
 if mode not in ('stage','inspect'):
  scope['require'](scope['digest'](scope['STAGE']/'quota_remote_020.py')==hashlib.sha256(raw).hexdigest(),'bootstrap_stage_drift')
 if mode=='stage':value=scope['stage_offline'](payload)
 elif mode=='test':value=scope['test_offline']()
 elif mode=='accept':value=scope['stage_acceptance'](payload)
 elif mode=='inspect':value=scope['inspect_stage']()
 elif mode=='verify':value=scope['verify']()
 else:value=scope['dispatch']({'deploy':'deployment','activate':'activation','rollback':'rollback'}[mode])
except Exception as error:
 value={'ok':False,'category':str(error) if isinstance(error,scope['OpsError']) else 'operation_rejected'}
print(json.dumps(value,allow_nan=False),flush=True)
raise SystemExit(0 if value.get('ok') is True and not value.get('outcome_uncertain') and not value.get('recovery_pending') else 2 if value.get('outcome_uncertain') or value.get('recovery_pending') else 1)
"""
    # Replace unique uppercase markers with Python string literals, never shell text.
    return (script.replace('@@SOURCE@@', repr(source)).replace('@@DIGEST@@', repr(hashlib.sha256(raw).hexdigest()))
            .replace('@@PAYLOAD@@', repr(encoded)).replace('@@MODE@@', repr(mode)))


def local_claim(mode, bundle):
    phase = PHASE[mode]
    path = ROOT / ('quotaops-client-' + phase + '-dispatched.json')
    ops.exclusive_bytes(path, json.dumps({'mode': mode, 'at_utc': ops.now(),
        'acceptance_sha256': bundle['acceptance_sha256'], 'next_action_if_uncertain': 'inspect'}).encode())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=MODES)
    args = parser.parse_args(argv)
    try:
        mode = args.mode
        bundle = None
        if mode in ('validate', 'accept', 'deploy', 'activate', 'verify', 'rollback'):
            bundle = ops.validate_bundle(ROOT)
        if mode == 'validate':
            value = {'ok': True, 'acceptance_sha256': bundle['acceptance_sha256'], 'execution_performed': False}
        else:
            payload = make_stage_payload(ROOT) if mode == 'stage' else make_acceptance_payload(ROOT) if mode == 'accept' else {}
            script = build_script(mode, payload, ROOT)
            if mode in PHASE: local_claim(mode, bundle)
            try:
                result = ops.bounded_run(ssh_argv(), input_bytes=script.encode('utf-8'),
                    timeout=330 if mode in PHASE else 90)
                value = ops.parse_json(result.stdout.encode('utf-8'))
                ops.require(isinstance(value, dict), 'invalid_remote_result')
                if result.returncode != outcome_code(value):
                    value = {'ok': False, 'outcome_uncertain': True, 'category': 'transport_outcome_mismatch_inspect'}
            except Exception:
                value = {'ok': False, 'outcome_uncertain': True, 'category': 'transport_outcome_uncertain_inspect'}
            # Local receipts use fresh, explicit names and never expose stderr.
            if mode == 'test' and value.get('ok') is True:
                ops.exclusive_bytes(ROOT / 'quotaops-tests.json', json.dumps(value, sort_keys=True).encode())
            elif mode in PHASE:
                ops.exclusive_bytes(ROOT / ('quotaops-client-' + PHASE[mode] + '-result.json'),
                                    json.dumps(value, sort_keys=True).encode())
        print(json.dumps(value, allow_nan=False), flush=True)
        return outcome_code(value)
    except Exception as error:
        print(json.dumps({'ok': False, 'category': str(error) if isinstance(error, ops.OpsError) else 'client_rejected'}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
