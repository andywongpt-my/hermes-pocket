"""Real C v2 worker -> authenticated loopback bridge -> native Speex -> fake Live.

No physical audio, TLS deployment, key, SDK client, or paid provider is used.
Native dependencies are explicit opt-ins, never downloaded by this test.
"""
import asyncio
import base64
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from pocket.live_echo import PairedSpeexEcho
from pocket.live_device_v2 import decode_frame
from pocket.live_websocket import LiveEndpoint
from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store
from tests.test_gpt_live import FakeConnection, sdk_client, transcript, delegation

LIBRARY = os.environ.get('POCKET_TEST_SPEEXDSP_LIBRARY')
CJSON = Path(os.environ.get('POCKET_CJSON_DIR', '/nonexistent/cjson'))


def far_samples(offset, count):
    result=[]
    for index in range(offset, offset+count):
        value=((index+1)*1664525+1013904223)&0xffffffff
        value^=value>>15;value=(value*2246822519)&0xffffffff;value^=value>>13
        result.append(value%18001-9000)
    return struct.pack('<%dh'%count,*result)


@unittest.skipUnless(LIBRARY, 'set POCKET_TEST_SPEEXDSP_LIBRARY for real AEC integration')
class CWorkerBridgeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root=Path(__file__).resolve().parents[1]
        fixture=cls.root/'tests/fixtures/live_v2_worker_harness.c'
        if not fixture.is_file():raise AssertionError('C-to-host v2 integration harness missing')
        if not shutil.which('gcc'):raise unittest.SkipTest('GCC unavailable')
        if not (CJSON/'cJSON.c').is_file():raise unittest.SkipTest('explicit pinned cJSON source unavailable')
        cls.temp=tempfile.TemporaryDirectory(prefix='pocket-c-live-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary=Path(cls.temp.name)/'worker'
        sources=['firmware/core/pocket_live_stream.c','firmware/core/pocket_live_v2_stream.c',
                 'firmware/main/pocket_live_client.c','firmware/main/pocket_live_v2_worker.c',
                 'firmware/main/pocket_live_v2_control.c']
        command=['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2',
                 '-I'+str(cls.root/'firmware/core'),'-I'+str(cls.root/'firmware/main'),'-I'+str(CJSON),
                 str(fixture),*[str(cls.root/name) for name in sources],str(CJSON/'cJSON.c'),'-lm','-o',str(cls.binary)]
        compiled=subprocess.run(command,capture_output=True,text=True,timeout=30)
        if compiled.returncode:raise AssertionError(compiled.stdout+compiled.stderr)

    async def run_path(self, *, mode='normal', tamper=None, pending_backend=False):
        class CompleteHermes(FakeHermes):
            stop_calls=0
            def create(self,*args,**kwargs):
                run=super().create(*args,**kwargs)
                if not pending_backend:self.complete(run,'C worker verified result')
                return run
            def stop(self,run):
                self.stop_calls+=1
                raise AssertionError('voice Stop must not cancel Hermes')
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        journal_path=Path(temporary.name)/'journal.sqlite'
        store=Store(journal_path);hermes=CompleteHermes()
        provider=FakeConnection();cleaned=[];captures=[];terminals=[];echoes=[]
        output_offset=0;credit_positive=False
        async def provider_send(event):
            nonlocal output_offset
            if event['type']=='session.start':
                self.assertEqual(event['session']['model'],'gpt-live-1')
                self.assertEqual(event['session']['delegation'],{'type':'client'})
            if event['type']=='session.input_audio.append':
                pcm=base64.b64decode(event['audio']);self.assertEqual(len(pcm),480)
                cleaned.append(pcm);self.assertLessEqual(len(cleaned),1300)
                if len(cleaned)==1:
                    await provider.events.put(transcript('verify durable C worker integration'))
                    await provider.events.put(delegation())
                    count=2880  # Six full packets: exceeds the four-packet/1920-sample credit window.
                elif mode=='normal':count=240
                else:count=0
                if count:
                    pcm=far_samples(output_offset,count);output_offset+=count
                    await provider.events.put({'type':'session.output_audio.delta','delta':base64.b64encode(pcm).decode()})
        provider.on_send=provider_send
        def echo_factory():
            echo=PairedSpeexEcho(LIBRARY);echoes.append(echo);return echo
        server=make_server({'linux':Service(store,hermes)},{'synthetic-c-worker-token':'c_device'},
            live={'linux':LiveEndpoint(sdk_client(provider),echo_factory)})
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        process=None;tasks=[];result=None
        try:
            async with connect(f'ws://127.0.0.1:{server.server_port}/targets/linux/live',
                    additional_headers={'Authorization':'Bearer synthetic-c-worker-token'},
                    proxy=None,compression=None,open_timeout=2,close_timeout=1,max_size=2048) as ws:
                process=await asyncio.create_subprocess_exec(str(self.binary),mode,
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                    env={'PATH':os.environ.get('PATH',''),'LANG':'C'})
                final=asyncio.get_running_loop().create_future()
                async def from_worker():
                    nonlocal credit_positive
                    try:
                        while True:
                            header=await process.stdout.readexactly(3)
                            size=int.from_bytes(header[1:],'big');self.assertLessEqual(size,1024)
                            data=await process.stdout.readexactly(size)
                            if header[0]==ord('R'):
                                if not final.done():final.set_result(json.loads(data));return
                            self.assertIn(header[0],(ord('T'),ord('B')))
                            if header[0]==ord('B'):
                                frame=decode_frame(data);captures.append(frame)
                                if tamper=='stale_credit' and credit_positive:
                                    data=data[:12]+b'\0\0\0\0'+data[16:]
                                credit_positive=credit_positive or frame.dma_consumed>0
                                # Real fragmented WebSocket messages, not a mock socket transport.
                                try:await ws.send([data[:7],data[7:]])
                                except ConnectionClosed:pass
                            else:
                                try:await ws.send(data.decode())
                                except ConnectionClosed:pass
                    except Exception as error:
                        if not final.done():final.set_exception(error)
                async def to_worker():
                    try:
                        async for data in ws:
                            raw=data.encode() if isinstance(data,str) else data
                            if isinstance(data,str):
                                control=json.loads(data)
                                if control.get('type') in ('closed','error'):terminals.append(dict(control))
                                if tamper=='known_playout' and control.get('type')=='closed':
                                    control['actual_playout']='known';raw=json.dumps(control).encode()
                            self.assertLessEqual(len(raw),1024)
                            record=(b'T' if isinstance(data,str) else b'B')+len(raw).to_bytes(2,'big')+raw
                            # Split pipe framing too; C receive must retain partial records.
                            for part in (record[:1],record[1:3],record[3:]):
                                process.stdin.write(part);await process.stdin.drain();await asyncio.sleep(0)
                    except (ConnectionClosed,BrokenPipeError,ConnectionResetError):
                        pass
                    finally:
                        if process.stdin and not process.stdin.is_closing():process.stdin.close()
                tasks=[asyncio.create_task(from_worker()),asyncio.create_task(to_worker())]
                result=await asyncio.wait_for(final,25)
                await asyncio.wait_for(process.wait(),2)
                stderr=await process.stderr.read(32769)
                self.assertEqual(process.returncode,0,stderr.decode(errors='replace'))
                self.assertEqual(stderr,b'')
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if process and process.returncode is None:
                process.kill();await process.wait()
            await asyncio.to_thread(server.shutdown)
            await asyncio.to_thread(server.server_close)
            thread.join(timeout=2)
            store.close()
            reopened=sqlite3.connect(journal_path)
            try:persisted=reopened.execute('SELECT device,state,result FROM tasks').fetchall()
            finally:reopened.close()
            journal_count=len(persisted)
        self.assertTrue(echoes);self.assertTrue(all(e._closed for e in echoes))
        self.assertFalse(thread.is_alive());self.assertTrue(provider.closed)
        self.assertEqual(hermes.stop_calls,0)
        if pending_backend:
            self.assertEqual(persisted,[('c_device','running',None)])
            self.assertEqual([run['state'] for run in hermes.runs.values()],['running'])
        elif mode=='normal' and tamper is None:
            self.assertEqual(persisted,[('c_device','completed','C worker verified result')])
        return result,captures,cleaned,terminals,hermes.submissions,journal_count,provider.sent

    async def test_c_worker_real_aec_hermes_credit_and_unknown_playout(self):
        result,captured,cleaned,terminal,submissions,journal,events=await self.run_path()
        self.assertEqual(result['reason'],0,(result,terminal,len(captured),len(cleaned)));self.assertTrue(result['finalized'])
        self.assertTrue(result['terminal_received']);self.assertTrue(result['cleanup_complete'])
        self.assertFalse(result['actual_playout_known']);self.assertTrue(result['playback_pending'])
        self.assertEqual((submissions,journal),(1,1))
        self.assertGreater(result['busy_submits'],0);self.assertEqual(result['max_queued'],4)
        self.assertLessEqual(result['max_source_pending'],1920)
        self.assertGreater(result['submitted_samples'],1920)
        self.assertEqual(len(captured),1300);self.assertEqual(len(cleaned),1300)
        self.assertEqual(terminal[-1]['actual_playout'],'unknown');self.assertTrue(terminal[-1]['playback_pending'])
        commentary=[event['content'] for event in events if event['type']=='session.commentary.append']
        self.assertEqual(commentary,['C worker verified result'])
        dry=wet=near_energy=correlation=0
        previous_credit=0
        for index,(frame,pcm) in enumerate(zip(captured,cleaned)):
            self.assertEqual((frame.sequence,frame.sample_offset),(index,index*240))
            pairs=struct.unpack('<480h',frame.pcm);out=struct.unpack('<240h',pcm)
            consumed=frame.dma_consumed-previous_credit
            self.assertTrue(0<=consumed<=240)
            expected=far_samples(previous_credit,consumed)+b'\0'*(2*(240-consumed))
            self.assertEqual(struct.pack('<240h',*pairs[1::2]),expected)
            previous_credit=frame.dma_consumed
            if 900<=index<1100:
                dry+=sum(x*x for x in pairs[::2]);wet+=sum(x*x for x in out)
            if index>=1200:
                near=[int(1500*math.sin(2*math.pi*719*(index*240+n)/24000)) for n in range(240)]
                near_energy+=sum(x*x for x in near);correlation+=sum(a*b for a,b in zip(near,out))
        self.assertGreater(10*math.log10(dry/max(1,wet)),15)
        self.assertGreater(correlation/near_energy,.5)

    async def test_c_stop_unblocks_host_waiting_on_dma_credit(self):
        result,_,_,terminal,_,_,_=await self.run_path(mode='stop-credit')
        self.assertEqual(result['reason'],0);self.assertTrue(result['finalized'])
        self.assertEqual(result['submitted_samples'],0);self.assertEqual(result['max_queued'],4)
        self.assertEqual(result['received_samples'],1920)
        self.assertTrue(result['playback_pending']);self.assertFalse(result['actual_playout_known'])
        self.assertTrue(terminal[-1]['finalized']);self.assertTrue(terminal[-1]['playback_pending'])

    async def test_c_stop_preserves_pending_hermes_task(self):
        result,_,_,terminal,submissions,journal,_=await self.run_path(
            mode='stop-credit',pending_backend=True)
        self.assertEqual(result['reason'],0);self.assertTrue(result['finalized'])
        self.assertTrue(result['backend_pending']);self.assertTrue(terminal[-1]['backend_pending'])
        self.assertTrue(result['playback_pending']);self.assertFalse(result['actual_playout_known'])
        self.assertEqual((submissions,journal),(1,1))

    async def test_c_invalid_credit_never_uploads_untruthful_packet(self):
        result,captured,_,_,submissions,journal,_=await self.run_path(mode='ahead-credit')
        self.assertEqual(result['reason'],9);self.assertFalse(result['finalized'])
        self.assertFalse(result['terminal_received']);self.assertFalse(result['actual_playout_known'])
        self.assertEqual((submissions,journal),(1,1))
        self.assertTrue(all(x.dma_consumed<=result['submitted_samples'] for x in captured))

    async def test_host_rejects_stale_credit_on_actual_c_wire(self):
        result,_,_,terminal,submissions,journal,_=await self.run_path(tamper='stale_credit')
        self.assertEqual(result['reason'],12);self.assertFalse(result['finalized'])
        self.assertFalse(result['actual_playout_known']);self.assertEqual((submissions,journal),(1,1))
        self.assertEqual(terminal[-1],{'type':'error','code':'session_unconfirmed'})

    async def test_c_rejects_terminal_claim_of_known_actual_playout(self):
        result,_,_,terminal,_,_,_=await self.run_path(mode='stop-credit',tamper='known_playout')
        self.assertEqual(result['reason'],7);self.assertFalse(result['terminal_received'])
        self.assertFalse(result['finalized']);self.assertFalse(result['actual_playout_known'])
        self.assertEqual(terminal[-1]['actual_playout'],'unknown')


if __name__=='__main__':unittest.main()
