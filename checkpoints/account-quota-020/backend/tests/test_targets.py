"""Two-host routing tests, entirely synthetic and loopback-only."""
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

class TargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.backends={k:FakeHermes() for k in ('windows','linux')}
        self.services={k:Service(Store(Path(self.tmp.name)/k),v) for k,v in self.backends.items()}
        self.server=make_server(self.services,{'fixture':'device','other':'other-device'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.payload={'version':1,'request_id':'same','text':'hello'}
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for svc in self.services.values():svc.store.close()
        self.tmp.cleanup()
    def call(self,path,body=None,token='fixture'):
        request=urllib.request.Request(self.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token})
        try:
            with urllib.request.urlopen(request,timeout=2) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:
            with error:return error.code,json.load(error)
    def test_list_targets(self):
        code,data=self.call('/targets');self.assertEqual(code,200)
        self.assertEqual(data['targets'],['windows','linux'])
        self.assertIsNone(data['default_target'])
    def test_explicit_target_required(self):
        self.assertEqual(self.call('/tasks',self.payload)[0],400)
        self.assertTrue(all(x.submissions==0 for x in self.backends.values()))
    def test_switch_preserves_original(self):
        _,win=self.call('/targets/windows/tasks',self.payload)
        _,linux=self.call('/targets/linux/tasks',dict(self.payload,text='linux only'))
        self.backends['windows'].complete(win['run_id'],'windows result')
        self.assertEqual(self.call('/targets/windows/tasks/same')[1]['result'],'windows result')
        self.assertEqual(self.call('/targets/linux/tasks/same')[1]['state'],'running')
        self.assertNotEqual(win['run_id'],linux['run_id'])
        self.assertEqual(self.call('/targets/windows/tasks',self.payload)[1]['run_id'],win['run_id'])
        self.assertEqual(self.backends['windows'].submissions,1)
    def test_stop_is_target_scoped(self):
        for target in self.services:self.call('/targets/'+target+'/tasks',self.payload)
        self.assertEqual(self.call('/targets/linux/tasks/same/stop',{})[1]['state'],'cancelled')
        self.assertEqual(self.call('/targets/windows/tasks/same')[1]['state'],'running')
    def test_events_and_device_isolation(self):
        self.call('/targets/windows/tasks',self.payload)
        self.assertEqual(self.call('/targets/linux/tasks/same/events')[0],404)
        self.assertEqual(self.call('/targets/windows/tasks/same',token='other')[0],404)
    def test_unknown_target_never_falls_back(self):
        self.assertEqual(self.call('/targets/missing/tasks',self.payload)[0],404)
        self.assertTrue(all(x.submissions==0 for x in self.backends.values()))
    def test_failed_target_never_fails_over(self):
        def unavailable(*args):raise ConnectionError('offline')
        self.backends['windows'].create=unavailable
        self.assertEqual(self.call('/targets/windows/tasks',self.payload)[1]['state'],'unknown')
        self.assertEqual(self.backends['linux'].submissions,0)
    def test_reject_shared_store(self):
        with self.assertRaises(ValueError):make_server({'windows':self.services['windows'],'linux':self.services['windows']},{'fixture':'device'})

    def test_chat_routes_stay_separate(self):
        win='/targets/windows/chats/main/messages'
        linux='/targets/linux/chats/main/messages'
        code,task=self.call(win,self.payload);self.assertEqual(code,202)
        self.backends['windows'].complete(task['run_id'],'Windows reply')
        self.assertEqual(self.call(win)[1][0]['result'],'Windows reply')
        self.assertEqual(self.call(linux)[1],[])
        self.assertEqual(self.call(win,token='other')[1],[])
        self.assertEqual(self.call(linux,self.payload)[0],202)
