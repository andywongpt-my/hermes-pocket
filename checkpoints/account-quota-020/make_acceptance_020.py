"""Seal a reviewed bundle; AW private proof never enters its artifact list."""
from pathlib import Path
import argparse
import hashlib
import json
import quota_remote_020 as ops
import private_aw_020 as private
ROOT=Path(__file__).resolve().parent

def assemble(root):
    root=Path(root)
    names={*ops.BACKEND_PINS,*ops.IMPLEMENTATIONS,*ops.COLLECTOR_FILES,
        'codex-producer-resume-20261018-report.json','codex-producer-review-R2-20261018.json',
        'quotaops-tests.json','quotaops-review.json'}
    blobs={name:ops.read_file(root/name) for name in names}
    digest=lambda name:hashlib.sha256(blobs[name]).hexdigest()
    sources={name:digest(name) for name in ops.COLLECTOR_FILES}
    native=private.attestation(root,sources,digest('quotaops-tests.json'),digest('quotaops-review.json'))
    blobs['quotaops-native-acceptance.json']=private.canonical(native)
    names.add('quotaops-native-acceptance.json')
    artifacts=names-set(ops.IMPLEMENTATIONS)-set(ops.COLLECTOR_FILES)
    acceptance={'version':1,'approved':True,'source_profile':ops.CODEX_PROFILE,
        'candidate_hashes_sha256':ops.BACKEND_IDENTITY,
        'artifact_hashes':{name:digest(name) for name in sorted(artifacts)},
        'implementation_hashes':{name:digest(name) for name in ops.IMPLEMENTATIONS},
        'collector_files':{name:{'sha256':digest(name)} for name in ops.COLLECTOR_FILES},
        'collector_review':'codex-producer-review-R2-20261018.json'}
    blobs['quotaops-acceptance.json']=private.canonical(acceptance)
    bundle=ops.validate_bundle(root,blobs=blobs)
    private.validate_aw_bundle(root,bundle)
    return blobs

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal',action='store_true',required=True)
    parser.parse_args()
    try:
        blobs=assemble(ROOT)
        # Exclusive create preserves earlier proof/acceptance; inspect any partial result.
        for name in ('quotaops-native-acceptance.json','quotaops-acceptance.json'):
            ops.require(not (ROOT/name).exists(),'acceptance_collision')
        for name in ('quotaops-native-acceptance.json','quotaops-acceptance.json'):
            ops.exclusive_bytes(ROOT/name,blobs[name])
        print(json.dumps({'ok':True,'acceptance_sha256':hashlib.sha256(blobs['quotaops-acceptance.json']).hexdigest()}))
        return 0
    except Exception:
        print(json.dumps({'ok':False,'category':'acceptance_evidence_incomplete_or_collision'}))
        return 1

if __name__=='__main__': raise SystemExit(main())
