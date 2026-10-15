import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from pocket.server import make_server
from pocket.store import Store
from pocket.service import Service, FakeHermes

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db')
        self.fake=FakeHermes(); self.svc=Service(self.store,self.fake)
        self.server=make_server(self.svc,{'fixture-a':'dev-a','fixture-b':'dev-b'},port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.close();self.tmp.cleanup()
    def call(self,path,body=None,key='fixture-a'):
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
        data=json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers=headers),timeout=2) as r:
                return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            with e:return e.code,json.load(e)
    def test_auth(self):self.assertEqual(self.call('/health',key='wrong')[0],401)
    def test_owner_and_dedupe(self):
        payload={'version':1,'request_id':'one','text':'hello'}
        self.assertEqual(self.call('/tasks',payload)[0],202)
        self.call('/tasks',payload);self.assertEqual(self.fake.submissions,1)
        self.assertEqual(self.call('/tasks/one',key='fixture-b')[0],404)
        self.assertEqual(self.call('/tasks',dict(payload,text='changed'))[0],409)
    def test_result_and_events_after_reconnect(self):
        _,task=self.call('/tasks',{'version':1,'request_id':'one','text':'hello'})
        self.fake.complete(task['run_id'],'done')
        code,task=self.call('/tasks/one');self.assertEqual((code,task['result']),(200,'done'))
        code,events=self.call('/tasks/one/events?after=1');self.assertEqual(code,200)
        self.assertTrue(all(e['seq']>1 for e in events))
    def test_bad_inputs(self):
        self.assertEqual(self.call('/tasks',{'version':True})[0],400)
        self.assertEqual(self.call('/tasks/one/events?after=-1')[0],400)
        self.assertEqual(self.call('/tasks',{'version':1,'request_id':'x','text':'a'*9000})[0],413)
    def test_unavailable_usage_honest(self):
        code,data=self.call('/usage');self.assertEqual(code,200)
        self.assertEqual(len(data),3)
        self.assertTrue(all(x['availability']=='not_configured' and x['value'] is None for x in data))
    def test_no_nonloopback_bind(self):
        with self.assertRaises(ValueError):make_server(self.svc,{'fixture':'d'},host='0.0.0.0')
    def test_stop(self):
        self.call('/tasks',{'version':1,'request_id':'one','text':'hello'})
        self.assertEqual(self.call('/tasks/one/stop',{})[1]['state'],'cancelled')

if __name__=='__main__':unittest.main()
