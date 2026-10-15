import unittest
from pocket.hermes import HermesAdapter, UpstreamError
from pocket.transport import UpstreamRejected

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.response={}
        def transport(method,path,body,headers):
            self.calls.append((method,path,body,headers));return self.response
        self.adapter=HermesAdapter(transport)
    def test_requires_capabilities(self):
        with self.assertRaises(UpstreamError):self.adapter.create('hi','d:r')
    def ready(self):
        self.response={'features':{'run_submission':True,'run_status':True,'run_stop':True}}
        self.adapter.check()
    def test_input_contract(self):
        self.ready();self.response={'run_id':'run_123','status':'started'}
        self.assertEqual(self.adapter.create('hi','device:request'),'run_123')
        method,path,body,headers=self.calls[-1]
        self.assertEqual((method,path,body),('POST','/v1/runs',{'input':'hi'}))
        self.assertEqual(headers['Idempotency-Key'],'device:request')
    def test_status_mapping(self):
        self.ready()
        for status,expected in [('queued','queued_upstream'),('running','running'),('stopping','stopping'),('completed','completed'),('waiting_for_approval','awaiting_approval')]:
            self.response={'status':status,'output':'answer'}
            self.assertEqual(self.adapter.read('run_123')['state'],expected)
    def test_unknown_status_fail_closed(self):
        self.ready();self.response={'status':'brand-new'}
        with self.assertRaises(UpstreamError):self.adapter.read('run_a')
    def test_invalid_run_identifier(self):
        self.ready()
        with self.assertRaises(ValueError):self.adapter.read('../../secret')
    def test_missing_capability(self):
        self.response={'features':{'run_submission':True}}
        with self.assertRaises(UpstreamError):self.adapter.check()
    def test_unreachable_probe_keeps_verdict(self):
        self.ready()
        def offline(*args):raise OSError('offline')
        self.adapter.transport=offline
        with self.assertRaises(OSError):self.adapter.check()
        self.assertTrue(self.adapter.ready)
    def test_rejected_probe_clears_verdict(self):
        self.ready()
        def revoked(*args):raise UpstreamRejected('upstream HTTP 401')
        self.adapter.transport=revoked
        with self.assertRaises(UpstreamRejected):self.adapter.check()
        self.assertFalse(self.adapter.ready)
        with self.assertRaises(UpstreamError):self.adapter.create('hi','d:r')
    def test_probe_does_not_block_concurrent_calls(self):
        self.ready();seen=[]
        def transport(method,path,body,headers):
            if path=='/v1/capabilities':seen.append(self.adapter.ready)
            return {'features':{'run_submission':True,'run_status':True,'run_stop':True}}
        self.adapter.transport=transport;self.adapter.check()
        self.assertEqual(seen,[True])
    def test_malformed_admission(self):
        self.ready();self.response={'run_id':False}
        with self.assertRaises(ValueError):self.adapter.create('hi','device:request')

if __name__=='__main__':unittest.main()

class ChatContractTests(unittest.TestCase):
    def test_passes_history_separately(self):
        calls=[]
        def transport(method,path,body,headers):
            calls.append(body)
            return {'features':{'run_submission':True,'run_status':True,'run_stop':True}} if path.endswith('capabilities') else {'run_id':'r'}
        adapter=HermesAdapter(transport);adapter.check()
        history=[{'role':'user','content':'first'},{'role':'assistant','content':'reply'}]
        adapter.create('next','key',history=history)
        self.assertEqual(calls[-1],{'input':'next','conversation_history':history})
