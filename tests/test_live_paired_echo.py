"""Real pinned Speex processing of co-captured ES8311 microphone/DAC pairs."""
import math
import os
import random
import struct
import unittest
from pocket.gpt_live import LiveError
from pocket.live_echo import SpeexEchoCanceller
try:
    from pocket.live_echo import PairedSpeexEcho
except ImportError:
    PairedSpeexEcho=None
LIBRARY=os.environ.get('POCKET_TEST_SPEEXDSP_LIBRARY')

class PairedContractTests(unittest.TestCase):
    def test_only_audited_native_frame_sizes_are_accepted_before_load(self):
        for size in (0,128,True,960):
            with self.assertRaisesRegex(LiveError,'echo_format_unsupported'):
                SpeexEchoCanceller('/nonexistent/library.so',frame_samples=size)

@unittest.skipUnless(LIBRARY,'set POCKET_TEST_SPEEXDSP_LIBRARY for native paired tests')
class PairedNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_deinterleaving_matches_native_reference_processing(self):
        self.assertIsNotNone(PairedSpeexEcho)
        paired=PairedSpeexEcho(LIBRARY)
        direct=SpeexEchoCanceller(LIBRARY,frame_samples=240)
        try:
            for frame in range(12):
                left=[int(4000*math.sin((frame*240+n)*.17)) for n in range(240)]
                right=[int(7000*math.sin((frame*240+n)*.051)) for n in range(240)]
                packed=struct.pack('<480h',*[x for pair in zip(left,right) for x in pair])
                expected=direct.process(struct.pack('<240h',*left),struct.pack('<240h',*right))
                self.assertEqual(await paired.capture_paired(packed,frame*240,0),expected)
            with self.assertRaisesRegex(LiveError,'echo_discontinuity'):
                await paired.capture_paired(b'\0'*960,0,0)
            with self.assertRaisesRegex(LiveError,'invalid_echo_audio'):
                await paired.capture_paired(b'\0'*480,12*240,0)
        finally:
            direct.close();await paired.aclose()
        with self.assertRaisesRegex(LiveError,'echo_closed'):
            await paired.capture_paired(b'\0'*960,12*240,0)
    async def test_native_10ms_echo_and_double_talk_not_mute_or_reference_leak(self):
        self.assertIsNotNone(PairedSpeexEcho)
        rng=random.Random(407);far=[rng.randint(-9000,9000) for _ in range(240*1300)]
        dry=wet=near_power=corr=0
        paired=PairedSpeexEcho(LIBRARY)
        try:
            for frame in range(1300):
                b=frame*240
                near=[int(1500*math.sin(2*math.pi*719*(b+n)/24000)) if frame>=1100 else 0 for n in range(240)]
                echo=[int(.55*far[b+n-120]+.18*far[b+n-260]) if b+n>=260 else 0 for n in range(240)]
                capture=[a+c for a,c in zip(near,echo)]
                packed=struct.pack('<480h',*[x for pair in zip(capture,far[b:b+240]) for x in pair])
                out=struct.unpack('<240h',await paired.capture_paired(packed,b,0))
                if 900<=frame<1100:dry+=sum(n*n for n in capture);wet+=sum(n*n for n in out)
                if frame>=1200:near_power+=sum(n*n for n in near);corr+=sum(a*c for a,c in zip(out,near))
            self.assertGreater(10*math.log10(dry/max(wet,1)),15)
            self.assertGreater(corr/near_power,.5)
        finally:await paired.aclose()

    async def test_full_paired_audio_path_preserves_live_and_durable_hermes(self):
        import asyncio
        import base64
        from pocket.live_device import run_device_gpt_live
        from pocket import live_device_v2 as v2
        from pocket.service import Service, FakeHermes
        from pocket.store import Store
        from tests.test_live_device import Stream
        from tests.test_live_device_v2 import start
        from tests.test_gpt_live import FakeConnection, sdk_client, transcript, delegation
        class CompleteHermes(FakeHermes):
            def create(self,*args,**kwargs):
                run=super().create(*args,**kwargs);self.complete(run,'paired result');return run
        paired_pcm=struct.pack('<480h',*([0,5000]*240))
        got_input=asyncio.Event();got_output=asyncio.Event()
        class Device(Stream):
            async def send(self,data):
                await super().send(data)
                if isinstance(data,bytes):
                    self.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,1,240,480,paired_pcm)))
                    got_output.set()
        store=Store(':memory:');self.addCleanup(store.close)
        hermes=CompleteHermes();service=Service(store,hermes)
        stream=Device(start());stream.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,0,0,0,paired_pcm)))
        connection=FakeConnection()
        connection.after_start=[transcript(),delegation(),
            {'type':'session.output_audio.delta','delta':base64.b64encode(b'\1\0'*480).decode()}]
        count=0
        async def observe(event):
            nonlocal count
            if event['type']=='session.input_audio.append':
                pcm=base64.b64decode(event['audio'])
                self.assertEqual(pcm,b'\0'*480,'DAC reference must not leak as microphone')
                count+=1
                if count==2:got_input.set()
            if event['type']=='session.commentary.append':
                await got_input.wait();await got_output.wait()
                stream.incoming.put_nowait('{"type":"stop"}')
        connection.on_send=observe
        result=await asyncio.wait_for(run_device_gpt_live(sdk_client(connection),service,
            'device',stream,asyncio.Event(),echo=PairedSpeexEcho(LIBRARY)),3)
        self.assertTrue(result.finalized);self.assertTrue(result.playback_pending)
        self.assertEqual(result.actual_playout,'unknown')
        self.assertEqual(hermes.submissions,1)
        self.assertEqual(store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertTrue(connection.closed);self.assertTrue(stream.closed)
