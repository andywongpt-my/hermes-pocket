"""Codex-only source and transport tests plus preserved R3 safety cases. Fixtures only."""
import copy
from contextlib import ExitStack, nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
import quota_remote_020 as ops
import quota_refresh_020 as sender
import test_r3_preserved as prior

BINDING='a'*64

def snapshot(at=1000,used=20):
    return {'version':1,'scope':'account','provider':'codex','source':'codex_app','fetched_at':at,
        'metrics':[{'window':'codex:primary','used_percent':used,'resets_at':2000,'window_minutes':10080}]}

def ack(raw):
    value=json.loads(raw)
    return {'ok':True,'profile':ops.CODEX_PROFILE,'provider':'codex','acceptance_sha256':BINDING,
        'fetched_at':value['fetched_at'],'snapshot_sha256':hashlib.sha256(raw).hexdigest()}

def rows():
    result=prior.usage()
    metric={'provider':'codex','category':'subscription','window':'codex:primary','value':80,
        'unit':'percent_remaining','fetched_at':1000,'resets_at':2000,'source':'Codex app account limits',
        'availability':'available','window_minutes':10080}
    result[0].update(availability='available',fetched_at=1000,metrics=[metric])
    return result

class CodexContracts(unittest.TestCase):

    def test_windows_executable_modes_preserve_all_real_revision_guards(self):
        from types import SimpleNamespace
        content=b'synthetic mode fixture'
        def info(**changes):
            values=dict(st_dev=1,st_ino=2,st_mode=0o100777,st_nlink=1,st_size=len(content),st_mtime_ns=1,st_ctime_ns=1)
            values.update(changes); return SimpleNamespace(**values)
        class Stream(io.BytesIO):
            def fileno(self): return 456
        path=Path(__file__).absolute().parent/'mode-fixture.exe'
        cases=[('windows_synthetic_execute','win32',{}, {}, {},True),
               ('posix_unchanged','linux',{}, {}, {},False)]
        for key,value in [('st_mode',0o100644),('st_mode',0o40666),('st_dev',3),('st_ino',3),('st_nlink',2),
                          ('st_size',len(content)+1),('st_mtime_ns',2),('st_ctime_ns',2)]:
            cases.append(('opened_'+key,'win32',{key:value},{},{},False))
        cases += [('descriptor_execute_drift','win32',{}, {'st_mode':0o100777},{},False),
                  ('path_execute_drift','win32',{}, {},{'st_mode':0o100666},False)]
        for label,platform,opened_changes,end_changes,path_changes,allowed in cases:
            with self.subTest(case=label,opened_changes=opened_changes):
                before=info(); opened=info(st_mode=0o100666); opened.__dict__.update(opened_changes)
                end=SimpleNamespace(**opened.__dict__); end.__dict__.update(end_changes)
                final=info(**path_changes); file_reads=[]
                def guard(p,**kwargs):
                    if kwargs.get('directory'): return before
                    file_reads.append(p); return before if len(file_reads)==1 else final
                with patch.object(sender.sys,'platform',platform),patch.object(ops,'guard',side_effect=guard),patch.object(sender.os,'fstat',side_effect=[opened,end]),patch.object(Path,'open',return_value=Stream(content)):
                    if allowed: self.assertEqual(sender.executable_digest(path),hashlib.sha256(content).hexdigest())
                    else:
                        with self.assertRaises(ops.OpsError): sender.executable_digest(path)


    def test_executable_digest_size_boundary_hash_and_revisions(self):
        from types import SimpleNamespace
        content=b'controlled executable bytes'*4000
        def stat(size, tick=1):
            return SimpleNamespace(st_dev=1,st_ino=2,st_mode=0o100600,st_nlink=1,
                st_size=size,st_mtime_ns=tick,st_ctime_ns=1)
        class Stream(io.BytesIO):
            def __init__(self): super().__init__(content); self.chunks=[]
            def fileno(self): return 123
            def read(self,n=-1): self.chunks.append(n); return super().read(n)
        path=Path(__file__).absolute().parent/'synthetic-executable.bin'
        for size in (341312816,512*1024*1024):
            with self.subTest(accepted_size=size):
                info=stat(size); stream=Stream()
                with patch.object(ops,'guard',return_value=info),patch.object(sender.os,'fstat',return_value=info),patch.object(Path,'open',return_value=stream) as opened:
                    result=sender.executable_digest(path)
                self.assertEqual(result,hashlib.sha256(content).hexdigest())
                self.assertNotEqual(result,hashlib.sha256(content+b'changed').hexdigest())
                self.assertTrue(stream.chunks and all(n==65536 for n in stream.chunks))
                opened.assert_called_once_with('rb')
        with patch.object(ops,'guard',return_value=stat(512*1024*1024+1)),patch.object(Path,'open') as opened:
            with self.assertRaises(ops.OpsError): sender.executable_digest(path)
            opened.assert_not_called()
        for drift in ('open_revision','end_revision','final_path_revision'):
            with self.subTest(drift=drift):
                info=stat(341312816); changed=stat(341312816,2); stream=Stream()
                file_checks=[]
                def guard(candidate,**kwargs):
                    if kwargs.get('directory'): return info
                    file_checks.append(candidate)
                    return changed if drift=='final_path_revision' and len(file_checks)>1 else info
                fstats=[changed,info] if drift=='open_revision' else [info,changed] if drift=='end_revision' else [info,info]
                with patch.object(ops,'guard',side_effect=guard),patch.object(sender.os,'fstat',side_effect=fstats),patch.object(Path,'open',return_value=stream):
                    with self.assertRaises(ops.OpsError): sender.executable_digest(path)

    def test_code_only_stage_includes_pinned_source_fixture_dependencies(self):
        import operate_quota_020 as client
        payload=client.make_stage_payload(Path(__file__).parent)
        blobs=ops.decode_payload(payload)
        self.assertTrue(set(ops.QUALIFICATION_PINS) <= set(blobs))
        ops.offline_plan(blobs)
        blobs['official_codex_quota.py']=b'changed'
        with self.assertRaises(ops.OpsError): ops.offline_plan(blobs)

    def test_primary_weekly_and_secondary_unknown_are_valid(self):
        value=rows(); self.assertTrue(ops.usage_ready(value,quota_required=True,observed_at=1001))
        value[0]['metrics'].append({**value[0]['metrics'][0],'window':'codex:secondary','value':None,'availability':'unavailable'})
        self.assertTrue(ops.usage_ready(value,quota_required=True,observed_at=1001))

    def test_inactive_stale_provider_preserves_original_metadata(self):
        value=rows()
        value[1].update(availability='stale',fetched_at=500,metrics=[{'availability':'stale','value':13,'fetched_at':500}])
        before=copy.deepcopy(value)
        self.assertTrue(ops.usage_ready(value,quota_required=True,observed_at=1001))
        self.assertEqual(value,before)
        value[1]['availability']='partial'
        value[1]['metrics'].append({'availability':'unavailable','value':None,'fetched_at':500})
        self.assertTrue(ops.usage_ready(value,quota_required=True,observed_at=1001))
        value[1]['metrics'][0]['availability']='available'
        self.assertFalse(ops.usage_ready(value,quota_required=True,observed_at=1001))

    def test_wrong_source_window_future_stale_and_fake_local_scope_rejected(self):
        for change in ({'source':'local tokens'},{'window':'five_hour'},{'fetched_at':1002},
                       {'fetched_at':700},{'value':True},{'value':101},{'unit':'tokens'},{'resets_at':999}):
            value=rows(); value[0]['metrics'][0].update(change)
            with self.subTest(change=change):
                self.assertFalse(ops.usage_ready(value,quota_required=True,observed_at=1001))
        value=rows(); value[1]['local_usage']['scope']='account'
        self.assertFalse(ops.usage_ready(value,quota_required=True,observed_at=1001))

    def test_numeric_schema_rejects_identity_extra_fields_and_duplicate_keys(self):
        value=snapshot()
        for changed in ({**value,'email':'not permitted'},{**value,'provider':'claude'},
                        {**value,'fetched_at':True},{**value,'metrics':[]}):
            with self.assertRaises(ops.OpsError):
                ops.validate_codex_snapshot(sender.canonical(changed),observed_at=1001)
        with self.assertRaises(ops.OpsError):
            ops.validate_codex_snapshot(b'{"version":1,"version":1}',observed_at=1001)
        with self.assertRaises(ops.OpsError):
            ops.validate_codex_snapshot(b'x'*16385,observed_at=1001)

    def test_exact_timestamp_preserved_and_new_identical_observation_allowed(self):
        sent=[]
        for at in (1000,1002):
            result=sender.refresh_once(lambda:snapshot(at),lambda raw:sent.append(raw) or ack(raw),
                acceptance_sha256=BINDING,clock=iter([at,at+1]).__next__)
            self.assertTrue(result['ok'])
        self.assertEqual([json.loads(raw)['fetched_at'] for raw in sent],[1000,1002])

    def test_source_cleanup_failure_never_transports(self):
        send=MagicMock()
        def fails_after_read():
            value=snapshot()
            raise RuntimeError('cleanup failed with sensitive diagnostics')
        result=sender.refresh_once(fails_after_read,send,acceptance_sha256=BINDING,clock=lambda:1000)
        self.assertEqual(result['category'],'source_rejected'); send.assert_not_called()
        self.assertNotIn('sensitive',json.dumps(result))

    def test_cached_future_slow_reads_never_transmit(self):
        for at,start,end in ((999,1000,1001),(1002,1000,1001),(1000,1000,1061)):
            send=MagicMock()
            result=sender.refresh_once(lambda:snapshot(at),send,acceptance_sha256=BINDING,clock=iter([start,end]).__next__)
            self.assertFalse(result['ok']); send.assert_not_called()

    def test_receipt_mismatch_or_transport_loss_requires_inspection_and_no_retry(self):
        for send in (lambda raw:{},MagicMock(side_effect=RuntimeError('private error'))):
            result=sender.refresh_once(lambda:snapshot(),send,acceptance_sha256=BINDING,clock=lambda:1000)
            self.assertTrue(result['outcome_uncertain'])
            self.assertEqual(result['attempted_fetched_at'],1000)
            self.assertNotIn('private',json.dumps(result))

    def test_cancel_between_read_and_transport_prevents_send(self):
        stop=threading.Event(); send=MagicMock()
        def read(): stop.set(); return snapshot()
        result=sender.refresh_once(read,send,acceptance_sha256=BINDING,clock=lambda:1000,cancelled=stop.is_set)
        self.assertEqual(result['category'],'interrupted'); send.assert_not_called()

    def test_serial_loop_stops_on_uncertain_result_and_waits_only_after_success(self):
        order=[]; stop=threading.Event()
        def wait(seconds): order.append(seconds); stop.set(); return True
        result=sender.run_loop(lambda:snapshot(),ack,acceptance_sha256=BINDING,stop=stop,count=0,clock=lambda:1000,emit=lambda v:None,wait=wait)
        self.assertEqual(result,130); self.assertEqual(order,[120])
        result=sender.run_loop(lambda:snapshot(),lambda raw:{},acceptance_sha256=BINDING,stop=threading.Event(),count=0,clock=lambda:1000,emit=lambda v:None,wait=lambda seconds:self.fail('retried'))
        self.assertEqual(result,2)

    def test_fixed_transport_has_existing_ssh_policy_and_no_shell(self):
        argv=sender.receiver_argv()
        self.assertIn('BatchMode=yes',argv); self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertEqual(argv[-1],'--push-codex')
        self.assertEqual(argv[7],'pocket-host')
        self.assertNotIn('token',' '.join(argv))

    def test_native_proof_requires_approved_context_binding_and_closed_pipes(self):
        import private_aw_020 as private
        from test_split_proof_020 import qualification
        sources={n:'a'*64 for n in ops.COLLECTOR_FILES}
        value=qualification(sources)
        private.validate_private_result(value,sources)
        for field,new in [('approved_app_context_link_verified',False),('account_binding_sha256',None),
            ('all_pipes_closed',False),('producer_exit_code',1),('refresh_token_false',False),('fetched_at',999)]:
            bad=copy.deepcopy(value); bad['qualification']['proof'][field]=new
            with self.subTest(field=field),self.assertRaises(ops.OpsError):
                private.validate_private_result(bad,sources)

class ChannelFixtures(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='codex-profile-',dir=prior.TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name); self.stage=base/'stage'; self.stage.mkdir(mode=0o700)
        self.numeric=base/'.fixture.account-quota-v1'; self.numeric.mkdir(mode=0o700)
        self.bundle={'acceptance_sha256':BINDING,'manifest':{}}
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        for name,value in [('STAGE',self.stage),('NUMERIC',self.numeric),('RELEASE',prior.BACKEND)]:
            self.stack.enter_context(patch.object(ops,name,value))
        for name,value in [('validate_bundle',self.bundle),('operation_lock',nullcontext()),
            ('validate_remote_stage',None),('check_hashes',None),('gateway_invariants',None),('prior_operators_absent',{})]:
            self.stack.enter_context(patch.object(ops,name,return_value=value))
        self.stack.enter_context(patch.object(ops.time,'time',return_value=1001))
        ops.save('deployment-result.json',{'ok':True,'backend_deployed':True,'acceptance_sha256':BINDING,'after':{'tls':{'version':'TLSv1.3','leaf_sha256':'c'*64}}})

    def test_prepare_push_activate_preserves_snapshot_and_claude_file(self):
        ops.prepare_codex()
        # A new read after prepare uses the original observation timestamp.
        unchanged=b'old unrelated numeric bytes'
        (self.numeric/'claude.json').write_bytes(unchanged); (self.numeric/'claude.json').chmod(0o600)
        result=ops.push_codex(sender.canonical(snapshot(1001)))
        self.assertEqual(result['fetched_at'],1001)
        self.assertEqual((self.numeric/'claude.json').read_bytes(),unchanged)
        readback=json.loads((self.numeric/'codex.json').read_bytes())
        self.assertEqual(readback,snapshot(1001))
        with patch.object(ops,'begin_attempt',return_value={}),patch.object(ops,'wait_ready',return_value={'tls':{'version':'TLSv1.3','leaf_sha256':'c'*64}}) as ready:
            result=ops.activate('fixture-operation')
        self.assertTrue(result['quota_activated'])
        self.assertEqual(ready.call_args.kwargs['since'],1001)
        self.assertEqual(ops.read_channel(self.bundle,allow_pending=False)['state'],'active')

    def test_inspection_resolves_published_digest_without_restamping(self):
        ops.prepare_codex(); acknowledgement=ops.push_codex(sender.canonical(snapshot(1001)))
        with patch.object(ops.time,'time',return_value=1800):
            value=ops.inspect_codex_publication()
        self.assertEqual(value['snapshot_sha256'],acknowledgement['snapshot_sha256'])
        self.assertEqual(value['fetched_at'],1001)
        self.assertFalse(value['freshness_claimed'])

    def test_revocation_rejects_late_send_and_keeps_last_good(self):
        ops.prepare_codex(); ops.push_codex(sender.canonical(snapshot(1001)))
        before=(self.numeric/'codex.json').read_bytes()
        ops.stop_owned_quota()
        with self.assertRaises(ops.OpsError): ops.push_codex(sender.canonical(snapshot(1001)))
        self.assertEqual((self.numeric/'codex.json').read_bytes(),before)

    def test_expired_pre_activation_and_replay_are_rejected(self):
        ops.prepare_codex()
        with self.assertRaises(ops.OpsError): ops.push_codex(sender.canonical(snapshot(1000)))
        ops.push_codex(sender.canonical(snapshot(1001)))
        with self.assertRaises(ops.OpsError): ops.push_codex(sender.canonical(snapshot(1001)))
        with patch.object(ops.time,'time',return_value=1301),self.assertRaises(ops.OpsError):
            ops.push_codex(sender.canonical(snapshot(1301)))

    def test_rollback_dispatch_blocks_push_before_publication(self):
        ops.prepare_codex(); ops.save('rollback-dispatched.json',{})
        with self.assertRaises(ops.OpsError): ops.push_codex(sender.canonical(snapshot(1001)))
        self.assertFalse((self.numeric/'codex.json').exists())

    def test_readiness_requires_exact_numeric_mapping_and_new_revision(self):
        raw=sender.canonical(snapshot(1001)); ops.prepare_codex(); ops.push_codex(raw)
        metrics=rows()[0]['metrics']
        expected=[{k:metrics[0].get(k) for k in ('window','value','unit','source','fetched_at','window_minutes','resets_at')}]
        expected[0]['fetched_at']=1001
        self.assertTrue(ops.new_publication(None,1001,expected))
        old=ops.revision(ops.guard(self.numeric/'codex.json',private=True))
        self.assertFalse(ops.new_publication(old,1001,expected))
        expected[0]['value']=79
        self.assertFalse(ops.new_publication(None,1001,expected))


class AcceptanceFixtures(unittest.TestCase):
    def bundle(self,directory):
        target=Path(directory)
        for name in (*ops.BACKEND_PINS,*ops.COLLECTOR_FILES,'codex-producer-resume-20261018-report.json','codex-producer-review-R2-20261018.json'):
            (target/name).write_bytes((Path(__file__).parent/name).read_bytes()); (target/name).chmod(0o600)
        implementation={}
        for name in ops.IMPLEMENTATIONS:
            raw=('synthetic fixture '+name).encode(); (target/name).write_bytes(raw); (target/name).chmod(0o600)
            implementation[name]=hashlib.sha256(raw).hexdigest()
        sources={name:{'sha256':hashlib.sha256((target/name).read_bytes()).hexdigest()} for name in ops.COLLECTOR_FILES}
        metadata={
            'quotaops-tests.json':{'ok':True,'tests':58,'failures':0,'errors':0,'skipped':0,'exit_code':0,'platform':'linux','implementation_hashes':implementation},
            'quotaops-review.json':{'approved':True,'implementation_hashes':implementation,'findings':[]}}
        for name,value in metadata.items(): prior.write_json(target/name,value)
        native={'version':2,'profile':ops.CODEX_PROFILE,'accepted':True,'source_files':ops.hashes(sources),
            'private_proof_sha256':'c'*64,'helper_files':{'native_aw_qualification.py':'d'*64,'test_native_aw_qualification.py':'e'*64},
            'helper_evidence_sha256':'f'*64,'operations_test_sha256':ops.digest(target/'quotaops-tests.json'),
            'operations_review_sha256':ops.digest(target/'quotaops-review.json')}
        metadata['quotaops-native-acceptance.json']=native
        for name,value in metadata.items(): prior.write_json(target/name,value)
        artifacts={name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in
            (*ops.BACKEND_PINS,'codex-producer-resume-20261018-report.json','codex-producer-review-R2-20261018.json',*metadata)}
        acceptance={'version':1,'approved':True,'source_profile':ops.CODEX_PROFILE,'candidate_hashes_sha256':ops.BACKEND_IDENTITY,
            'artifact_hashes':artifacts,'implementation_hashes':implementation,'collector_files':sources,
            'collector_review':'codex-producer-review-R2-20261018.json'}
        prior.write_json(target/'quotaops-acceptance.json',acceptance)
        return target,acceptance,metadata

    def test_exact_codex_acceptance_is_valid_and_source_specific(self):
        with tempfile.TemporaryDirectory(prefix='codex-acceptance-',dir=prior.TEMP_ROOT) as directory:
            target,acceptance,_=self.bundle(directory)
            result=ops.validate_bundle(target)
            self.assertEqual(result['identity'],ops.BACKEND_IDENTITY)
            acceptance['source_profile']='claude'
            prior.write_json(target/'quotaops-acceptance.json',acceptance)
            with self.assertRaises(ops.OpsError): ops.validate_bundle(target)

    def test_native_ops_backend_source_and_review_drift_fail_closed(self):
        for drift in ('approval','native','ops_tests','review','source','backend','implementation'):
            with self.subTest(drift=drift),tempfile.TemporaryDirectory(prefix='codex-acceptance-',dir=prior.TEMP_ROOT) as directory:
                target,acceptance,metadata=self.bundle(directory)
                if drift=='approval': acceptance['approved']=False
                elif drift=='backend': acceptance['candidate_hashes_sha256']='0'*64
                elif drift=='source': (target/'official_codex_quota.py').write_text('changed')
                elif drift=='implementation': (target/'quota_refresh_020.py').write_text('changed')
                else:
                    name={'native':'quotaops-native-acceptance.json','ops_tests':'quotaops-tests.json','review':'quotaops-review.json'}[drift]
                    if drift=='native': metadata[name]['approved_app_context_link_verified']=False
                    elif drift=='ops_tests': metadata[name]['skipped']=1
                    else: metadata[name]['implementation_hashes']={}
                    prior.write_json(target/name,metadata[name])
                    acceptance['artifact_hashes'][name]=hashlib.sha256((target/name).read_bytes()).hexdigest()
                prior.write_json(target/'quotaops-acceptance.json',acceptance)
                with self.assertRaises(ops.OpsError): ops.validate_bundle(target)

def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    from test_split_proof_020 import SplitProofTests
    suite.addTests(loader.loadTestsFromTestCase(SplitProofTests))
    for cls in (CodexContracts,ChannelFixtures,AcceptanceFixtures,prior.AdmissionTests,prior.DeltaAndRecoveryTests,
                prior.WorkflowTests,prior.ReviewRegressionTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    for cls,names in [(prior.BindingAndPathTests,[
        'test_any_prior_marker_blocks_repeat_even_if_result_failed','test_scope_rejects_unknown_content_and_hash_collision','test_symlink_hardlink_and_wrong_mode_rejected_without_following',
        'test_client_only_accepts_known_success_and_fixed_ssh_policy']),
        (prior.CompletionSafetyTests,[
        'test_capture_streams_in_bounded_chunks_instead_of_communicate','test_exclusive_receipt_claim_has_one_winner_under_concurrent_dispatch',
        'test_http_captures_tls_identity_before_server_closes_socket','test_private_stage_and_test_payload_need_no_native_acceptance',
        'test_bootstrap_has_fixed_scope_and_never_embeds_native_or_secret_material','test_client_missing_full_acceptance_prevents_any_transport',
        'test_missing_or_changed_tls_identity_is_not_a_preservation_proof','test_verification_requires_stt_process_parent_and_zero_gateway_restarts'])]:
        for name in names: suite.addTest(cls(name))
    return suite

if __name__=='__main__': unittest.main()
