"""AW-private native proof validation. Never include proof/evidence files in transport."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import quota_remote_020 as ops

PRIVATE_RESULT = 'native-aw-result-private.json'
# Frozen private evidence pins below are derived from reviewed bytes; changing them requires a new candidate review.
HELPER_PINS = {'native_aw_qualification.py': '264215065991d726e2a9052e698e494c8de11cf1de95ebeab352cae41bb3a3ea', 'test_native_aw_qualification.py': '71ac4722d3dff48e4ebcb1a7361fac0a5e601bba58aa5585010fe9e37fd224d2'}
HELPER_REPORT = 'native-aw-qualification-R3-report.json'
HELPER_REVIEW = 'native-aw-qualification-review-R3.json'
SUPERVISOR_REPORT = 'native-aw-supervisor-R2-report.json'
SUPERVISOR_REVIEW = 'native-aw-supervisor-review-R2.json'
SUPERVISOR_PINS = {'native_aw_supervisor.py': '8a0fbe92fb0e3c84845f8cd24e67f137cb6e770668b09b46adf5b037ad067231', 'test_native_aw_supervisor.py': 'cf397f598fb57ba7537d1b6a29c741149a9185a79a810d47e97c2d1ed7a5cfa4'}
EVIDENCE_PINS = {'native-aw-qualification-review.json': '168b28cd4f3bc54643e7b45e36523fc251a9719fcf69f3799045bdb5458bb011', 'native-aw-qualification-R2-report.json': '31e165f428f4576d1cc99a212565c0248b308597ad38f3566ba2b1d88b44a299', 'native-aw-qualification-review-R2.json': '17180141211019bf4e1c85e6bcc5bd4d29a19f8e3f9d13f20577a58798a733d9', 'native-aw-qualification-R3-report.json': '94bf3b622b833891a6d82775ae0e49cbad6454686ed6f25c1965212686a7998c', 'native-aw-qualification-review-R3.json': 'be02eab0aa574ef5a6748e59e193dd54f41ec7b4ef623aebf02fdf48cfe58af2', 'native-aw-supervisor-report.json': '2bd1f6cc398bd14970ace31e2c02dfaf036ee60fa438687f0d1401094e5ffd07', 'native-aw-supervisor-review.json': 'df32bcfa80ec9489c913550a0923f4b484fe07a9142ce4d3603493e55e150691', 'native-aw-supervisor-R2-report.json': 'cca0e4c0c716622ed7fb5fe301cc6af4005944734e377e144f98a1dee7989695', 'native-aw-supervisor-review-R2.json': '982ac83cebbf135fa2f48962549695e3680450b6227d63c09f6c2be84e8a1287'}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def digest_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def validate_private_result(envelope, source_hashes):
    ops.require(isinstance(envelope,dict) and set(envelope)=={'qualification','supervision'},'private_envelope_schema')
    supervision=envelope['supervision']
    true_fields={'assigned_before_resume','resumed','job_empty','all_pipes_closed',
        'io_threads_stopped','helper_process_handle_closed','job_handle_closed'}
    false_fields={'terminated_before_resume','termination_requested','timed_out'}
    ops.require(isinstance(supervision,dict) and set(supervision)==true_fields|false_fields|
        {'helper_exit_code','active_processes','total_processes'},'private_supervision_schema')
    ops.require(all(supervision[k] is True for k in true_fields)
        and all(supervision[k] is False for k in false_fields),'private_supervision_cleanup')
    ops.require(type(supervision['helper_exit_code']) is int and supervision['helper_exit_code']==0
        and type(supervision['active_processes']) is int and supervision['active_processes']==0
        and type(supervision['total_processes']) is int and supervision['total_processes']>=3,
        'private_supervision_cleanup')
    value=envelope['qualification']
    ops.require(isinstance(value,dict) and set(value)=={'snapshot','proof'},'private_native_schema')
    proof=value['proof']
    fields={'profile','executable','executable_sha256','account_binding_sha256',
        'approved_app_context_link_verified','account_checks_before_after','refresh_token_false',
        'read_started_at','fetched_at','read_completed_at','producer_exit_code',
        'all_children_reaped','all_pipes_closed','source_files'}
    ops.require(isinstance(proof,dict) and set(proof)==fields,'private_native_schema')
    expected={'native_aw_qualification.py':HELPER_PINS['native_aw_qualification.py'],
        'official_codex_quota.py':source_hashes['official_codex_quota.py']}
    ops.require(proof['profile']==ops.CODEX_PROFILE and proof['source_files']==expected,'private_native_source')
    for field in ('approved_app_context_link_verified','account_checks_before_after',
                  'refresh_token_false','all_children_reaped','all_pipes_closed'):
        ops.require(proof[field] is True,'private_native_cleanup')
    ops.require(type(proof['producer_exit_code']) is int and proof['producer_exit_code']==0,'private_native_cleanup')
    path=proof['executable']
    ops.require(isinstance(path,str) and PureWindowsPath(path).is_absolute() and '\x00' not in path
        and ops.valid_hash(proof['executable_sha256']) and ops.valid_hash(proof['account_binding_sha256']),
        'private_native_binding')
    start,at,end=(proof[k] for k in ('read_started_at','fetched_at','read_completed_at'))
    ops.require(all(type(t) is int and 0<=t<=2**53-1 for t in (start,at,end))
        and start<=at<=end and end-start<=60,'private_native_timing')
    snapshot=ops.validate_codex_snapshot(canonical(value['snapshot']),observed_at=end,started_at=start)
    ops.require(snapshot['fetched_at']==at,'private_native_timing')
    return proof

def read_private(root, source_hashes):
    root=Path(root)
    for name,expected in {**HELPER_PINS,**SUPERVISOR_PINS,**EVIDENCE_PINS}.items():
        ops.require(ops.digest(root/name)==expected,'private_helper_evidence_binding')
    for name,expected_files in ((HELPER_REPORT,HELPER_PINS),(HELPER_REVIEW,HELPER_PINS),
                                (SUPERVISOR_REPORT,SUPERVISOR_PINS),(SUPERVISOR_REVIEW,SUPERVISOR_PINS)):
        document=ops.read_json(root/name)
        pins={Path(key.replace('\\','/')).name:value for key,value in document['pins'].items()}
        ops.require(all(pins.get(key)==value for key,value in expected_files.items()),
            'private_helper_review_binding')
    # Exact immutable reviewed report/review bytes are pinned above, including prior review lineage.
    value=ops.read_json(root/PRIVATE_RESULT)
    proof=validate_private_result(value,source_hashes)
    return value,proof

def attestation_value(value, source_hashes, operations_test_sha256, operations_review_sha256):
    result={'version':2,'profile':ops.CODEX_PROFILE,'accepted':True,
        'source_files':source_hashes,'private_proof_sha256':digest_value(value),
        'helper_files':HELPER_PINS,'helper_evidence_sha256':digest_value({'evidence':EVIDENCE_PINS,'supervisor_files':SUPERVISOR_PINS}),
        'operations_test_sha256':operations_test_sha256,'operations_review_sha256':operations_review_sha256}
    ops.validate_codex_native(result,source_hashes)
    return result

def attestation(root, source_hashes, operations_test_sha256, operations_review_sha256):
    value,proof=read_private(root,source_hashes)
    return attestation_value(value,source_hashes,operations_test_sha256,operations_review_sha256)

def validate_aw_bundle(root, bundle=None):
    bundle=ops.validate_bundle(root) if bundle is None else bundle
    accepted=bundle['acceptance']
    value,proof=read_private(root,ops.hashes(accepted['collector_files']))
    expected=attestation_value(value,ops.hashes(accepted['collector_files']),
        accepted['artifact_hashes']['quotaops-tests.json'],accepted['artifact_hashes']['quotaops-review.json'])
    ops.require(bundle['native']==expected,'private_remote_attestation_binding')
    return bundle,proof
