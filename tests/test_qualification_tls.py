"""Local TLS only: ephemeral test certificate, synthetic PCM; never devices."""
import asyncio
import json
import os
from pathlib import Path
import ssl
import struct
import subprocess
import tempfile
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from pocket.qualification_tls import QualificationTLSServer, ROUTE

TOKEN = 'synthetic-test-only-token-00000001'
START = {'type':'start','version':2,'rate':24000,'channels':2,
         'format':'pcm_s16le','reference':'es8311_dac_right','chat_id':'qualification'}

class QualificationTLSTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cert = Path(cls.tmp.name)/'cert.pem'; key = Path(cls.tmp.name)/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
            '-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost',
            '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'],
            check=True, capture_output=True, timeout=10)
        cls.server_tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_tls.load_cert_chain(cert,key)
        cls.client_tls=ssl.create_default_context(cafile=str(cert))

    def fixture(self, **kwargs):
        return QualificationTLSServer(ssl_context=self.server_tls, token=TOKEN, **kwargs)

    def connection(self, server, *, route=ROUTE, token=TOKEN, tls=None):
        return connect(f'wss://127.0.0.1:{server.port}{route}', ssl=tls or self.client_tls,
            additional_headers={'Authorization':'Bearer '+token}, proxy=None,
            compression=None, open_timeout=1, close_timeout=.2, max_size=1024)

    async def start(self, ws):
        await ws.send(json.dumps(START))
        self.assertEqual(json.loads(await ws.recv()), {'type':'ready','version':2,
                         'rate':24000,'reference':'es8311_dac_right'})

    async def test_continuous_native_pairs_credit_and_numeric_summary(self):
        async with self.fixture(output_samples=2880) as server:
            async with self.connection(server) as ws:
                await self.start(ws)
                sent=0
                for sequence in range(6):
                    packet=await asyncio.wait_for(ws.recv(),1)
                    self.assertEqual(struct.unpack('!2sBBIII',packet[:16]),
                                     (b'PL',2,2,sequence,sent,0))
                    self.assertEqual(packet[16:],bytes(960));sent+=480
                    # Paired left/right preserved on wire; fixture only counts, never retains PCM.
                    for half in range(2):
                        n=sequence*2+half
                        await ws.send(struct.pack('!2sBBIII',b'PL',2,1,n,n*240,(n+1)*240)+bytes(960))
                await ws.send('{"type":"stop"}')
                terminal=json.loads(await ws.recv())
                self.assertEqual(terminal['actual_playout'],'unknown')
                self.assertTrue(terminal['finalized'])
            await server.wait_closed()
        report=server.summary()
        self.assertEqual(report['capture_frames'],12)
        self.assertEqual(report['speaker_samples'],2880)
        self.assertLessEqual(report['max_source_pending'],1920)
        self.assertEqual(report['physical_acceptance'],0)
        self.assertEqual(report['actual_playout_known'],0)
        self.assertTrue(all(type(v) in (int,float) for v in report.values()))

    async def test_stop_while_credit_exhausted(self):
        async with self.fixture() as server:
            async with self.connection(server) as ws:
                await self.start(ws)
                for _ in range(4):await ws.recv()
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(),.05)
                await ws.send('{"type":"stop"}')
                terminal=json.loads(await asyncio.wait_for(ws.recv(),.5))
                self.assertTrue(terminal['playback_pending'])
            await server.wait_closed()
        self.assertEqual(server.summary()['speaker_samples'],1920)

    async def test_wrong_route_auth_tls_and_single_use(self):
        async with self.fixture() as server:
            for kwargs in ({'route':'/targets/linux/live'}, {'route':ROUTE+'?x=1'}, {'token':'wrong'}):
                with self.assertRaises(InvalidStatus):
                    async with self.connection(server,**kwargs):pass
            loop=asyncio.get_running_loop(); errors=[]
            old=loop.get_exception_handler()
            loop.set_exception_handler(lambda loop, context: errors.append(context))
            try:
                with self.assertRaises(ssl.SSLCertVerificationError):
                    async with self.connection(server,tls=ssl.create_default_context()):pass
                await asyncio.sleep(.02)
                self.assertTrue(all(isinstance(e.get('exception'), ConnectionResetError) for e in errors))
            finally:loop.set_exception_handler(old)
            async with self.connection(server) as ws:
                await self.start(ws)
                with self.assertRaises(InvalidStatus):
                    async with self.connection(server):pass
                await ws.close()
            await server.wait_closed()
        self.assertEqual(server.summary()['completed'],0)

    async def test_malformed_oversize_and_discontinuity(self):
        for bad in (b'x'*1025, b'x'*976,
                    struct.pack('!2sBBIII',b'PL',2,1,1,0,0)+bytes(960),
                    struct.pack('!2sBBIII',b'PL',2,1,0,0,99999)+bytes(960)):
            async with self.fixture(output_samples=0) as server:
                async with self.connection(server) as ws:
                    await self.start(ws); await ws.send(bad)
                    try:
                        terminal=await ws.recv()
                        self.assertEqual(json.loads(terminal),{'type':'error','code':'session_unconfirmed'})
                    except ConnectionClosed:pass
                await server.wait_closed()
            self.assertEqual(server.summary()['completed'],0)

    async def test_deadline_without_start_and_capture_cap(self):
        async with self.fixture(deadline_seconds=.2) as server:
            async with self.connection(server) as ws:
                await asyncio.wait_for(server.wait_closed(),1)
        self.assertEqual(server.summary()['deadline_expired'],1)
        async with self.fixture(capture_frames=1,output_samples=0) as server:
            async with self.connection(server) as ws:
                await self.start(ws)
                for n in range(2):
                    await ws.send(struct.pack('!2sBBIII',b'PL',2,1,n,n*240,0)+bytes(960))
                self.assertEqual(json.loads(await ws.recv())['type'],'error')
            await server.wait_closed()
        self.assertEqual(server.summary()['capture_frames'],1)

    def test_explicit_loopback_tls_and_bounds(self):
        for kwargs in ({'host':'0.0.0.0'},{'host':'localhost'},{'deadline_seconds':61},
                       {'deadline_seconds':float('nan')},{'capture_frames':6001},
                       {'output_samples':144001},{'output_samples':True}):
            with self.assertRaises(ValueError):self.fixture(**kwargs)
        with self.assertRaises(ValueError):QualificationTLSServer(ssl_context=None,token=TOKEN)
        with self.assertRaises(ValueError):QualificationTLSServer(ssl_context=self.server_tls,token='x')

    async def test_finished_attempt_closes_listener_and_reentry_rejected(self):
        async with self.fixture(output_samples=0) as server:
            async with self.connection(server) as ws:
                await self.start(ws);await ws.send('{"type":"stop"}');await ws.recv()
            await server.wait_closed()
            with self.assertRaises(OSError):
                async with self.connection(server):pass
        with self.assertRaises(ValueError):await server.__aenter__()

    async def test_duplicate_start_and_noninteger_rate_fail_closed(self):
        for start in (json.dumps(START).replace('qualification','chat_one'),
                      json.dumps(START).replace('24000','24000.0'),
                      json.dumps(START).replace('"type": "start"','"type":"start","type":"start"')):
            async with self.fixture() as server:
                async with self.connection(server) as ws:
                    await ws.send(start)
                    self.assertEqual(json.loads(await ws.recv())['type'],'error')
                await server.wait_closed()
            self.assertEqual(server.summary()['protocol_failed'],1)

    @unittest.skipUnless(os.environ.get('POCKET_CJSON_DIR'), 'explicit pinned cJSON required')
    async def test_actual_c_worker_through_tls_stop_under_pressure_and_continuous(self):
        root=Path(__file__).resolve().parents[1]
        cjson=Path(os.environ['POCKET_CJSON_DIR'])
        binary=Path(self.tmp.name)/'qualification-worker'
        sources=['firmware/core/pocket_live_stream.c','firmware/core/pocket_live_v2_stream.c',
                 'firmware/main/pocket_live_client.c','firmware/main/pocket_live_v2_worker.c',
                 'firmware/main/pocket_live_v2_control.c']
        built=await asyncio.create_subprocess_exec('gcc','-std=c11','-Wall','-Wextra','-Werror',
            '-Wconversion','-Wshadow','-pedantic','-O2','-I'+str(root/'firmware/core'),
            '-I'+str(root/'firmware/main'),'-I'+str(cjson),
            str(root/'tests/fixtures/qualification_tls_worker_harness.c'),*[str(root/p) for p in sources],
            str(cjson/'cJSON.c'),'-lm','-o',str(binary),
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(built.communicate(),30)
        self.assertEqual(built.returncode,0,(out,err))
        for mode in ('stop-credit','normal'):
            async with self.fixture(deadline_seconds=25) as server:
                async with self.connection(server) as ws:
                    process=await asyncio.create_subprocess_exec(str(binary),mode,
                        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,env={'PATH':os.environ.get('PATH',''),'LANG':'C'})
                    async def from_worker():
                        while True:
                            header=await process.stdout.readexactly(3)
                            size=int.from_bytes(header[1:],'big');self.assertLessEqual(size,1024)
                            raw=await process.stdout.readexactly(size)
                            if header[0]==ord('R'):return json.loads(raw)
                            self.assertIn(header[0],(ord('B'),ord('T')))
                            await ws.send(raw if header[0]==ord('B') else raw.decode())
                    async def to_worker():
                        try:
                            async for data in ws:
                                raw=data.encode() if isinstance(data,str) else data
                                self.assertLessEqual(len(raw),1024)
                                record=(b'T' if isinstance(data,str) else b'B')+len(raw).to_bytes(2,'big')+raw
                                process.stdin.write(record);await process.stdin.drain()
                        except (ConnectionClosed,BrokenPipeError,ConnectionResetError):pass
                        finally:process.stdin.close()
                    sender=asyncio.create_task(from_worker());receiver=asyncio.create_task(to_worker())
                    try:
                        result=await asyncio.wait_for(sender,23)
                        await asyncio.wait_for(process.wait(),2)
                        self.assertEqual(await process.stderr.read(),b'')
                        self.assertEqual(process.returncode,0)
                    finally:
                        sender.cancel();receiver.cancel()
                        await asyncio.gather(sender,receiver,return_exceptions=True)
                        if process.returncode is None:process.kill();await process.wait()
                await server.wait_closed()
            self.assertEqual(result['reason'],0,result)
            self.assertTrue(result['cleanup_complete']);self.assertTrue(result['finalized'])
            self.assertFalse(result['actual_playout_known'])
            self.assertEqual(result['max_queued'],4)
            self.assertLessEqual(result['max_source_pending'],1920)
            self.assertGreater(result['busy_submits'],0)
            self.assertEqual(server.summary()['capture_frames'],19 if mode=='stop-credit' else 1300)
