import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from pocket.schema import decode, ProtocolError
from pocket.store import Store, Conflict, NotFound, InvalidTransition
from pocket.service import Service, FakeHermes


def request(**kw):
    return dict(version=1, request_id='req-1', text='hello', **kw)

class SchemaTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(decode(json.dumps(request()).encode())['text'], 'hello')
    def test_bad_version(self):
        for version in (True, 0, 2, '1'):
            with self.subTest(version=version), self.assertRaises(ProtocolError):
                decode(json.dumps(dict(request(), version=version)).encode())
    def test_invalid_frames(self):
        for raw in (b'[]', b'null', b'{', b'\xff', b'{"version":1,"version":1}', b'x'*9000):
            with self.subTest(raw=raw[:20]), self.assertRaises(ProtocolError): decode(raw)
    def test_fields(self):
        for patch in ({'text':''}, {'text':' '}, {'text':'猫'*1400}, {'request_id':'../x'}, {'extra':1}, {'text':True}):
            with self.subTest(patch=str(patch)[:30]), self.assertRaises(ProtocolError):
                decode(json.dumps(dict(request(), **patch)).encode())

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'tasks.db'; self.s=Store(self.path)
    def tearDown(self): self.s.close(); self.tmp.cleanup()
    def test_dedupe_conflict_and_owner(self):
        a,new=self.s.admit('dev-a',request()); self.assertTrue(new)
        b,new=self.s.admit('dev-a',request()); self.assertFalse(new); self.assertEqual(a,b)
        with self.assertRaises(Conflict): self.s.admit('dev-a',dict(request(),text='changed'))
        with self.assertRaises(NotFound): self.s.get('dev-b','req-1')
        _,new=self.s.admit('dev-b',request()); self.assertTrue(new)
    def test_restart_uncertain_dispatch_never_replayed(self):
        self.s.admit('dev-a',request()); self.s.transition('dev-a','req-1','dispatching')
        self.s.close(); self.s=Store(self.path); self.s.recover()
        self.assertEqual(self.s.get('dev-a','req-1')['state'],'unknown')
        self.assertFalse(self.s.admit('dev-a',request())[1])
    def test_terminal_is_immutable(self):
        self.s.admit('dev-a',request()); self.s.transition('dev-a','req-1','dispatching')
        self.s.transition('dev-a','req-1','running',run_id='run_a')
        self.s.transition('dev-a','req-1','completed',result='ok')
        with self.assertRaises(InvalidTransition): self.s.transition('dev-a','req-1','running')
        self.assertEqual(self.s.get('dev-a','req-1')['result'],'ok')
    def test_event_cursor(self):
        self.s.admit('dev-a',request()); self.s.transition('dev-a','req-1','dispatching')
        events=self.s.events('dev-a','req-1',0)
        self.assertEqual([e['seq'] for e in events],[1,2])
        self.assertEqual(len(self.s.events('dev-a','req-1',1)),1)
        with self.assertRaises(NotFound):self.s.events('other','req-1',0)
    def test_concurrent_admission(self):
        def run(_):
            s=Store(self.path)
            try:return s.admit('dev-a',request())[1]
            finally:s.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(run,range(12))),1)
    def test_read_does_not_mutate(self):
        self.s.admit('dev-a',request()); before=self.s.events('dev-a','req-1',0)
        self.s.get('dev-a','req-1'); self.assertEqual(before,self.s.events('dev-a','req-1',0))

class ServiceTests(unittest.TestCase):
    setUp = StoreTests.setUp
    tearDown = StoreTests.tearDown
    def test_fake_lifecycle_reconnect(self):
        backend=FakeHermes(); svc=Service(self.s,backend)
        a=svc.submit('dev-a',request()); self.assertEqual(a['state'],'queued_upstream')
        svc.submit('dev-a',request());self.assertEqual(backend.submissions,1)
        backend.complete(a['run_id'],'synthetic result')
        svc=Service(self.s,backend)
        self.assertEqual(svc.refresh('dev-a','req-1')['result'],'synthetic result')
    def test_submit_error_unknown_not_failed(self):
        class Lost(FakeHermes):
            def create(self,*args):raise TimeoutError('private-token')
        svc=Service(self.s,Lost());a=svc.submit('dev-a',request())
        self.assertEqual(a['state'],'unknown'); self.assertNotIn('private-token',str(a))
        self.assertEqual(svc.submit('dev-a',request())['state'],'unknown')
    def test_stop_confirmation(self):
        backend=FakeHermes();svc=Service(self.s,backend);svc.submit('dev-a',request())
        self.assertEqual(svc.stop('dev-a','req-1')['state'],'cancelled')
    def test_upstream_failure_preserves_state(self):
        backend=FakeHermes();svc=Service(self.s,backend);svc.submit('dev-a',request())
        backend.read=lambda _: (_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):svc.refresh('dev-a','req-1')
        self.assertEqual(self.s.get('dev-a','req-1')['state'],'queued_upstream')
    def test_approval_not_automatically_resolved(self):
        backend=FakeHermes();svc=Service(self.s,backend);a=svc.submit('dev-a',request())
        backend.runs[a['run_id']]['state']='awaiting_approval'
        self.assertEqual(svc.refresh('dev-a','req-1')['state'],'awaiting_approval')


class RegressionTests(unittest.TestCase):
    setUp = StoreTests.setUp
    tearDown = StoreTests.tearDown
    def test_stop_pending_is_not_cancelled(self):
        class Pending(FakeHermes):
            def stop(self,rid):self.runs[rid]['state']='stopping'
        svc=Service(self.s,Pending());svc.submit('dev-a',request())
        self.assertEqual(svc.stop('dev-a','req-1')['state'],'stopping')
    def test_invalid_event_cursor(self):
        self.s.admit('dev-a',request())
        for cursor in (True,-1,'1'):
            with self.assertRaises(ValueError):self.s.events('dev-a','req-1',cursor)
    def test_idle_queued_recovery_is_explicit(self):
        self.s.admit('dev-a',request());self.s.recover()
        self.assertEqual(self.s.get('dev-a','req-1')['state'],'unknown')
    def test_unknown_run_cannot_be_stopped(self):
        self.s.admit('dev-a',request());self.s.transition('dev-a','req-1','dispatching')
        self.s.transition('dev-a','req-1','unknown')
        with self.assertRaises(InvalidTransition):Service(self.s,FakeHermes()).stop('dev-a','req-1')

if __name__=='__main__':unittest.main()
