"""Offline full-size device PCM frames through the real HTTP routes."""
import base64
import http.client
import json
import threading
import time
import unittest
from pocket.server import make_server
from pocket.store import Store
from pocket.service import Service, FakeHermes

class Speech:
    stt_enabled=True
    def transcribe(self,pcm):
        assert pcm==b'\x00\x00'*2048
        return 'hello'
    def speak(self,text):return b'\x00\x00'*2048

class VoiceHTTPTests(unittest.TestCase):
    def test_device_sized_pcm_roundtrip_and_keepalive(self):
        store=Store(':memory:');backend=FakeHermes()
        server=make_server({'linux':Service(store,backend)},{'fixture':'device'},speech={'linux':Speech()},decoder=lambda x:x)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        def call(method,path,body=None):
            client.request(method,'/targets/linux/'+path,body=json.dumps(body) if body is not None else None,headers={'Authorization':'Bearer fixture','Content-Type':'application/json'})
            response=client.getresponse();value=json.loads(response.read())
            self.assertLess(response.status,300,value)
            return value
        def wait(path,state):
            until=time.monotonic()+2
            while time.monotonic()<until:
                value=call('GET',path)
                if value['state']==state:return value
                time.sleep(.01)
            self.fail('job did not finish')
        try:
            call('POST','audio/one',{'chat_id':'pocket'})
            sock=client.sock
            call('POST','audio/one/chunk',{'sequence':0,'data':base64.b64encode(b'\x00\x00'*2048).decode()})
            self.assertIs(client.sock,sock)
            call('POST','audio/one/finish',{})
            job=wait('audio/one','submitted')
            task=store.get('device',job['task_id']);backend.complete(task['run_id'],'answer')
            view=call('GET','tasks/'+job['task_id']+'/view');self.assertEqual(view['result'],'answer')
            call('POST','tasks/'+job['task_id']+'/speech',{})
            wait('tasks/'+job['task_id']+'/speech','ready')
            chunk=call('GET','tasks/'+job['task_id']+'/speech/chunks/0')
            self.assertEqual(len(base64.b64decode(chunk['data'])),4096)
        finally:
            client.close();server.shutdown();thread.join();server.server_close();store.close()
