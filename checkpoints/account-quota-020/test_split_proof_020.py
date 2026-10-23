"""Fixture-only tests of actual stage/accept payload and AW-local proof."""
import base64
import copy
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import quota_remote_020 as ops
import quota_refresh_020 as sender
import operate_quota_020 as client
import private_aw_020 as private
import make_acceptance_020 as seal
import test_r3_preserved as prior

def qualification(source_hashes):
    marker=hashlib.sha256(b'synthetic-only-private-account').hexdigest()
    proof={'profile':ops.CODEX_PROFILE,'executable':'C:\\'+marker+'\\codex.exe',
        'executable_sha256':hashlib.sha256(b'synthetic-only-native-file').hexdigest(),
        'account_binding_sha256':marker,'approved_app_context_link_verified':True,
        'account_checks_before_after':True,'refresh_token_false':True,
        'read_started_at':1000,'fetched_at':1000,'read_completed_at':1001,
        'producer_exit_code':0,'all_children_reaped':True,'all_pipes_closed':True,
        'source_files':{'native_aw_qualification.py':private.HELPER_PINS['native_aw_qualification.py'],
            'official_codex_quota.py':source_hashes['official_codex_quota.py']}}
    snapshot={'version':1,'scope':'account','provider':'codex','source':'codex_app','fetched_at':1000,
        'metrics':[{'window':'codex:primary','used_percent':33,'window_minutes':10080,'resets_at':2000}]}
    supervision={key:True for key in ('assigned_before_resume','resumed','job_empty','all_pipes_closed',
        'io_threads_stopped','helper_process_handle_closed','job_handle_closed')}
    supervision.update(terminated_before_resume=False,termination_requested=False,timed_out=False,
        helper_exit_code=0,active_processes=0,total_processes=3)
    return {'qualification':{'snapshot':snapshot,'proof':proof},'supervision':supervision}

@contextmanager
def fixture():
    from test_quota_ops_020 import AcceptanceFixtures
    with tempfile.TemporaryDirectory(prefix='split-proof-',dir=prior.TEMP_ROOT) as directory:
        target,old,metadata=AcceptanceFixtures().bundle(directory)
        helpers={name:('synthetic helper '+name).encode() for name in (*private.HELPER_PINS,*private.SUPERVISOR_PINS)}
        hp={name:hashlib.sha256(helpers[name]).hexdigest() for name in private.HELPER_PINS}
        sp={name:hashlib.sha256(helpers[name]).hexdigest() for name in private.SUPERVISOR_PINS}
        sources=ops.hashes(old['collector_files'])
        # Strict local pin mechanism is exercised with fixture-specific immutable evidence.
        evidence={name:private.canonical({'fixture':name}) for name in private.EVIDENCE_PINS}
        pins={**hp,**sp,'official_codex_quota.py':sources['official_codex_quota.py']}
        for name in (private.HELPER_REPORT,private.HELPER_REVIEW,private.SUPERVISOR_REPORT,private.SUPERVISOR_REVIEW):
            evidence[name]=private.canonical({'pins':pins})
        ep={name:hashlib.sha256(raw).hexdigest() for name,raw in evidence.items()}
        for name,raw in {**helpers,**evidence}.items():
            (target/name).write_bytes(raw); (target/name).chmod(0o600)
        with patch.object(private,'HELPER_PINS',hp),patch.object(private,'EVIDENCE_PINS',ep),patch.object(private,'SUPERVISOR_PINS',sp):
            value=qualification(sources)
            prior.write_json(target/private.PRIVATE_RESULT,value)
            blobs=seal.assemble(target)
            for name in ('quotaops-native-acceptance.json','quotaops-acceptance.json'):
                (target/name).write_bytes(blobs[name]); (target/name).chmod(0o600)
            yield target,value,blobs

def script_payload(script):
    tree=__import__('ast').parse(script)
    # Inspect the actual bootstrap payload argument without executing bootstrap/network.
    for node in __import__('ast').walk(tree):
        if isinstance(node,__import__('ast').Assign) and any(isinstance(t,__import__('ast').Name) and t.id=='payload' for t in node.targets):
            encoded=node.value.args[0].args[0].value
            return json.loads(base64.b64decode(encoded,validate=True))
    raise AssertionError('missing wire payload')

class SplitProofTests(unittest.TestCase):
    def test_actual_stage_and_accept_scripts_exclude_private_native_markers(self):
        with fixture() as (target,value,blobs):
            proof=value['qualification']['proof']
            markers=[proof['account_binding_sha256'],proof['executable'],proof['executable_sha256']]
            for mode,payload in (('stage',client.make_stage_payload(target)),('accept',client.make_acceptance_payload(target))):
                wire=script_payload(client.build_script(mode,payload,target))
                self.assertEqual(wire,payload)
                decoded=ops.decode_payload(wire)
                self.assertNotIn(private.PRIVATE_RESULT,decoded)
                self.assertTrue(set(private.EVIDENCE_PINS).isdisjoint(decoded))
                self.assertTrue(set(private.HELPER_PINS).union(private.SUPERVISOR_PINS).isdisjoint(decoded))
                for marker in markers:
                    for raw in decoded.values(): self.assertNotIn(marker.encode(),raw)
            remote=json.loads(blobs['quotaops-native-acceptance.json'])
            self.assertTrue((set(proof)-{'profile','source_files'}).isdisjoint(remote))
            self.assertEqual(remote['private_proof_sha256'],private.digest_value(value))

    def test_private_and_remote_validate_with_exact_helper_schema(self):
        with fixture() as (target,value,_):
            bundle,proof=private.validate_aw_bundle(target)
            self.assertEqual(proof,value['qualification']['proof'])
            self.assertEqual(set(proof['source_files']),{'native_aw_qualification.py','official_codex_quota.py'})
            self.assertEqual(bundle['native']['helper_files'],private.HELPER_PINS)

    def test_binding_returned_is_from_same_private_read_as_attestation(self):
        with fixture() as (target,value,_):
            original=private.read_private
            observed=[]
            def alternating(*args):
                item=original(*args); observed.append(item)
                if len(observed)>1:
                    item[1]['account_binding_sha256']='e'*64
                return item
            with patch.object(private,'read_private',side_effect=alternating):
                bundle,proof=private.validate_aw_bundle(target)
            self.assertEqual(proof['account_binding_sha256'],value['qualification']['proof']['account_binding_sha256'])
            self.assertEqual(len(observed),1)

    def test_missing_private_or_helper_evidence_prevents_accept_payload(self):
        for kind in ('private','helper','review','supervisor_review'):
            with self.subTest(kind=kind),fixture() as (target,value,_):
                name={'private':private.PRIVATE_RESULT,'helper':'native_aw_qualification.py',
                      'review':private.HELPER_REVIEW,'supervisor_review':private.SUPERVISOR_REVIEW}[kind]
                (target/name).unlink()
                with self.assertRaises((ops.OpsError,OSError,KeyError)): client.make_acceptance_payload(target)

    def test_private_tampering_cannot_reuse_remote_attestation(self):
        for field,value in (('account_binding_sha256','f'*64),('executable',r'C:\other\codex.exe'),
                            ('all_pipes_closed',False),('source_files',{}),('accepted',True)):
            with self.subTest(field=field),fixture() as (target,original,_):
                original['qualification']['proof'][field]=value
                prior.write_json(target/private.PRIVATE_RESULT,original)
                with self.assertRaises(ops.OpsError): client.make_acceptance_payload(target)

    def test_raw_private_proof_or_extra_fields_rejected_even_with_rehashed_artifact(self):
        for kind in ('private','extra','test_digest','review_digest','missing'):
            with self.subTest(kind=kind),fixture() as (target,value,blobs):
                acceptance=json.loads(blobs['quotaops-acceptance.json'])
                remote=json.loads(blobs['quotaops-native-acceptance.json'])
                if kind=='private': remote=value['qualification']['proof']
                elif kind=='extra': remote['account_binding_sha256']=value['qualification']['proof']['account_binding_sha256']
                elif kind=='test_digest': remote['operations_test_sha256']='a'*64
                elif kind=='review_digest': remote['operations_review_sha256']='a'*64
                else: remote.pop('helper_evidence_sha256')
                prior.write_json(target/'quotaops-native-acceptance.json',remote)
                acceptance['artifact_hashes']['quotaops-native-acceptance.json']=ops.digest(target/'quotaops-native-acceptance.json')
                prior.write_json(target/'quotaops-acceptance.json',acceptance)
                with self.assertRaises(ops.OpsError): ops.validate_bundle(target)

    def test_helper_evidence_or_source_bytes_drift_rejected(self):
        for name in ('native_aw_qualification.py','test_native_aw_qualification.py','native_aw_supervisor.py',private.SUPERVISOR_REVIEW,private.HELPER_REPORT,
                     private.HELPER_REVIEW):
            with self.subTest(name=name),fixture() as (target,value,_):
                (target/name).write_bytes(b'changed')
                with self.assertRaises(ops.OpsError): client.make_acceptance_payload(target)

    def test_incomplete_supervision_fails_before_seal(self):
        for field,new in (('job_empty',False),('all_pipes_closed',False),('io_threads_stopped',False),
                          ('active_processes',1),('helper_exit_code',1),('timed_out',True),
                          ('job_handle_closed',False),('assigned_before_resume',False)):
            with self.subTest(field=field),fixture() as (target,value,_):
                value['supervision'][field]=new
                prior.write_json(target/private.PRIVATE_RESULT,value)
                with self.assertRaises(ops.OpsError): seal.assemble(target)

    def test_sender_executes_same_hashed_source_despite_valid_divergent_cache(self):
        with tempfile.TemporaryDirectory(prefix='sender-cache-',dir=prior.TEMP_ROOT) as directory:
            path=Path(directory)/'fixture.py'
            good=b"MARKER='checked-source'\n"
            bad =b"MARKER='cached-diverge'\n"
            self.assertEqual(len(good),len(bad))
            path.write_bytes(good)
            stat=path.stat()
            cache=Path(importlib.util.cache_from_source(str(path))); cache.parent.mkdir()
            raw=importlib._bootstrap_external._code_to_timestamp_pyc(compile(bad,str(path),'exec'),
                int(stat.st_mtime),len(bad))
            cache.write_bytes(raw)
            spec=importlib.util.spec_from_file_location('old_fixture_loader',path)
            old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
            self.assertEqual(old.MARKER,'cached-diverge')
            module=sender.load_checked_client(path,hashlib.sha256(good).hexdigest())
            self.assertEqual(module.MARKER,'checked-source')
            self.assertEqual(path.read_bytes(),good)
            with self.assertRaises(ops.OpsError): sender.load_checked_client(path,'a'*64)

    def test_versioned_stage_avoids_mutating_r1_receipts(self):
        self.assertEqual(ops.STAGE.name,'account-quota-020-codex-ops-r3')
        self.assertIn('account-quota-020-codex-ops-r3',client.build_script('stage',{}))
        self.assertIn('account-quota-020-codex-ops-r3',sender.receiver_argv()[-2])

if __name__=='__main__': unittest.main()
