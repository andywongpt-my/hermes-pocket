"""Frozen v2 reply wire over real local HTTP, entirely synthetic adapters."""
import base64
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pocket.server import make_server
from pocket.service import Service, FakeHermes
from pocket.store import Store
from test_result_pages import completed
from test_segmented_speech import SyntheticSpeech, synthetic_decoder


class ReplyDeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root/'tasks.sqlite'); self.addCleanup(self.store.close)
        self.backend = FakeHermes(); self.speech = SyntheticSpeech()
        # Attribute assertion is a clean missing-feature failure before adding API keywords.
        import pocket.server as module
        self.assertTrue(hasattr(module, 'ResultPages'), 'reply HTTP routing is missing')
        self.server = make_server({'linux':Service(self.store,self.backend)},
            {'fixture':'device','other':'other'},speech={'linux':self.speech},
            reply_decoder=synthetic_decoder(65*32000),reply_spool_root=self.root/'spools')
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        completed(self.store,text=' \0\t中文🙂' * 400)

    def stop(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()

    def call(self, method, suffix, body=None, token='fixture', headers=None, task='one', raw_body=None):
        client = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        request_headers = {'Authorization':'Bearer '+token,'Content-Type':'application/json'}
        request_headers.update(headers or {})
        raw = raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
        try:
            client.request(method,'/targets/linux/tasks/'+task+'/'+suffix,raw,request_headers)
            response=client.getresponse(); raw=response.read()
            self.assertLessEqual(len(raw),8000)
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            return response.status,json.loads(raw)
        finally: client.close()

    def test_pages_reconstruct_exact_result_and_ownership_without_refresh(self):
        self.backend.status = lambda *args: self.fail('page route must not refresh upstream')
        code,page=self.call('GET','result/pages');self.assertEqual(code,200,page)
        result=page['text']; cursor=page['next']
        while cursor:
            code,page=self.call('GET','result/pages?cursor='+cursor);self.assertEqual(code,200,page)
            result+=page['text'];cursor=page['next']
        self.assertEqual(result,self.store.get('device','one')['result'])
        for token,code in (('other',404),('absent',401)):
            self.assertEqual(self.call('GET','result/pages?cursor=bad',token=token)[0],code)
        for query in ('?page=0','?cursor=','?cursor=x&cursor=x','?cursor=%ZZ','?x=1','?cursor=x&x=1'):
            self.assertEqual(self.call('GET','result/pages'+query)[0],400,query)
        self.assertEqual(self.call('GET','result/pages',headers={'Range':'bytes=0-1'})[0],400)

    def test_speech_create_generation_status_chunks_cancel_and_legacy_capability(self):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        client.request('GET','/targets/linux/status',headers={'Authorization':'Bearer fixture'})
        response=client.getresponse();status=json.loads(response.read());client.close()
        self.assertEqual(status['voice']['max_seconds'],30)
        self.assertEqual(status['reply_delivery']['text_pages']['page_bytes'],768)
        self.assertEqual(status['reply_delivery']['speech_segments']['max_segments'],12)
        # Short accepted text for TTS; the pages test retains the full 64KiB limit.
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE tasks SET result='accepted exact text',seq=seq+1 WHERE request_id='one'")
        code,first=self.call('POST','speech/v2',{'request_id':'speak-one'});self.assertEqual(code,202,first)
        generation=first['generation'];until=time.monotonic()+3
        while time.monotonic()<until:
            code,job=self.call('GET','speech/v2?generation='+generation)
            if job['state']!='processing':break
            time.sleep(.01)
        self.assertEqual((code,job['state'],job['segment_count']),(200,'ready',4),job)
        code,retry=self.call('POST','speech/v2',{'request_id':'speak-one'})
        self.assertEqual((code,retry),(202,job));self.assertEqual(len(self.speech.calls),1)
        code,chunk=self.call('GET','speech/v2/segments/0/chunks/0?generation='+generation)
        self.assertEqual(code,200,chunk);self.assertEqual(len(base64.b64decode(chunk['data'])),4096)
        self.assertEqual(self.call('GET','speech/v2/segments/00000000/chunks/00000000?generation='+generation)[0],200)
        for token,code in (('other',404),('absent',401)):
            self.assertEqual(self.call('GET','speech/v2?generation='+generation,token=token)[0],code)
        self.assertEqual(self.call('GET','speech/v2?generation='+'0'*32)[0],409)
        for suffix in ('speech/v2','speech/v2?generation=bad','speech/v2?generation='+generation+'&generation='+generation,
                       'speech/v2/segments/-1/chunks/0?generation='+generation,
                       'speech/v2/segments/0/chunks/0?generation='+generation+'&x=1'):
            self.assertEqual(self.call('GET',suffix)[0],400,suffix)
        code,value=self.call('POST','speech/v2/cancel',{'generation':generation})
        self.assertEqual((code,value['state']),(200,'cancelled'))
        self.assertEqual(self.call('GET','speech/v2/segments/0/chunks/0?generation='+generation)[0],410)

    def test_new_routes_reject_duplicate_keys_extra_fields_and_non_json_values(self):
        for body in ({},{'request_id':'x','x':1},{'request_id':True},{'request_id':'x','result_revision':'bad'},[]):
            self.assertEqual(self.call('POST','speech/v2',body)[0],400,body)
        for body in ({},{'generation':'a'*32,'x':1},{'generation':'BAD'},{'generation':True}):
            self.assertEqual(self.call('POST','speech/v2/cancel',body)[0],400,body)
        for raw in (b'{"request_id":"x","request_id":"x"}',
                    '{"request_id":"x"}'.encode('utf-16'),b'{"request_id":NaN}'):
            self.assertEqual(self.call('POST','speech/v2',raw_body=raw)[0],400)

    def test_wrong_owner_and_unknown_target_never_expose_generation_metadata(self):
        for suffix,method,body in (('result/pages','GET',None),('speech/v2','POST',{'request_id':'x'}),
                ('speech/v2?generation='+'a'*32,'GET',None),
                ('speech/v2/cancel','POST',{'generation':'a'*32}),
                ('speech/v2/segments/0/chunks/0?generation='+'a'*32,'GET',None)):
            code,value=self.call(method,suffix,body,token='other')
            self.assertEqual((code,value),(404,{'error':'not found'}))
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            client.request('GET','/targets/unknown/tasks/one/result/pages',headers={'Authorization':'Bearer fixture'})
            response=client.getresponse();self.assertEqual(response.status,404);response.read()
        finally:client.close()
        self.assertEqual(len(self.store.db.execute('PRAGMA table_info(audio_jobs)').fetchall()),6)
        self.assertEqual(len(self.store.db.execute('PRAGMA table_info(speech_jobs)').fetchall()),3)


class ServerOwnershipTests(unittest.TestCase):
    def test_constructor_failures_close_owned_resources_and_same_root_retry_succeeds(self):
        import pocket.server as module
        original_spools=module.ReplySpools;original_voice=module.VoiceJobs
        original_bind=module.ThreadingHTTPServer.server_bind
        for phase in ('invalid_device','voice','pages','bind','tls','tls_wrap','timing'):
            with self.subTest(phase=phase),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);store=Store(root/'tasks.sqlite');other=Store(root/'other.sqlite')
                controllers=[];voices=[];servers=[];retry=None;occupied=None
                def spools(*args,**kwargs):
                    value=original_spools(*args,**kwargs);controllers.append(value);return value
                def voice(*args,**kwargs):
                    if phase=='voice' and voices:raise RuntimeError('synthetic voice constructor failure')
                    value=original_voice(*args,**kwargs);voices.append(value);return value
                def bind(server):servers.append(server);return original_bind(server)
                targets={'linux':Service(store,FakeHermes())}
                if phase=='voice':targets['other']=Service(other,FakeHermes())
                options={'reply_spool_root':root/'spools'}
                if phase=='bind':
                    occupied=socket.socket()
                    if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):occupied.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                    occupied.bind(('127.0.0.1',0));occupied.listen()
                    options['port']=occupied.getsockname()[1]
                if phase in ('tls','tls_wrap'):
                    options['tls']={'certificate':str(root/'missing-cert.pem'),'private_key':str(root/'missing-key.pem')}
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(module,'ReplySpools',side_effect=spools))
                        stack.enter_context(patch.object(module,'VoiceJobs',side_effect=voice))
                        stack.enter_context(patch.object(module.ThreadingHTTPServer,'server_bind',bind))
                        if phase=='pages':stack.enter_context(patch.object(module,'ResultPages',side_effect=RuntimeError('synthetic page constructor failure')))
                        if phase=='timing':stack.enter_context(patch.object(module,'NumericTimingLog',side_effect=RuntimeError('synthetic timing constructor failure')))
                        if phase=='tls_wrap':
                            class Context:
                                def load_cert_chain(self,*args):pass
                                def wrap_socket(self,*args,**kwargs):raise OSError('synthetic socket wrap failure')
                            stack.enter_context(patch.object(module.ssl,'SSLContext',return_value=Context()))
                        with self.assertRaises((ValueError,RuntimeError,OSError)):
                            module.make_server(targets,{'fixture':'invalid device' if phase=='invalid_device' else 'device'},**options)
                    failed_closed=all(control.closed and not control.cleaner.is_alive() and control.pool._shutdown and
                        all(item.fd is None for item in control.directories.values()) for control in controllers)
                    voices_closed=all(item.closed and item.pool._shutdown for item in voices)
                    sockets_closed=all(item.socket.fileno()==-1 for item in servers)
                    retry_error=None
                    try:retry=module.make_server({'linux':Service(store,FakeHermes())},{'fixture':'device'},reply_spool_root=root/'spools')
                    except Exception as error:retry_error=type(error).__name__
                    self.assertTrue(failed_closed,'failed construction retained spool lease/cleaner/pool: '+phase)
                    self.assertTrue(voices_closed,'failed construction retained voice resources: '+phase)
                    self.assertTrue(sockets_closed,'failed construction retained a bound socket: '+phase)
                    self.assertIsNone(retry_error,'same-root retry failed: '+str(retry_error))
                finally:
                    # Keep the RED run itself contained even when the old code
                    # leaks the resources that this test deliberately exposes.
                    if occupied is not None:occupied.close()
                    if retry is not None:retry.server_close()
                    for server in servers:server.server_close()
                    for item in voices:item.close()
                    for control in controllers:control.close()
                    store.close();other.close()

    def test_close_retains_exclusive_lease_until_legacy_provider_and_voice_cleanup_finish(self):
        import pocket.server as module
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'tasks.sqlite');completed(store)
            started=threading.Event();closing=threading.Event();release=threading.Event();returned=threading.Event()
            class Speech:
                stt_enabled=True
                def speak(self,text):
                    started.set();release.wait(4);returned.set();return b'\0\0'*2048
                def close(self):closing.set();release.wait(4)
            voices=[];original_voice=module.VoiceJobs
            def voice(*args,**kwargs):
                value=original_voice(*args,**kwargs);voices.append(value);return value
            old=None;second=None;closer=None
            try:
                with patch.object(module,'VoiceJobs',side_effect=voice):
                    old=module.make_server({'linux':Service(store,FakeHermes())},{'fixture':'device'},
                        speech={'linux':Speech()},decoder=lambda value:value,reply_spool_root=root/'spools')
                voices[0].speak('device','one');self.assertTrue(started.wait(1))
                closer=threading.Thread(target=old.server_close);closer.start();self.assertTrue(closing.wait(1))
                blocked=False
                try:second=module.make_server({'linux':Service(store,FakeHermes())},{'fixture':'device'},reply_spool_root=root/'spools')
                except OSError:blocked=True
                marker=store.db.execute("SELECT state FROM speech_jobs WHERE task='one'").fetchone()[0]
                self.assertTrue(blocked,'second server acquired lease while old legacy provider/cleanup was active')
                self.assertEqual(marker,'processing')
            finally:
                release.set()
                if closer is not None:closer.join(timeout=3)
                if second is not None:second.server_close()
                if old is not None:old.server_close()
                try:
                    retry=module.make_server({'linux':Service(store,FakeHermes())},{'fixture':'device'},reply_spool_root=root/'spools')
                    retry.server_close()
                finally:store.close()
            self.assertTrue(returned.is_set())
            self.assertFalse(closer.is_alive())
