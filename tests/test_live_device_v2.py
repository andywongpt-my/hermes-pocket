"""Paired ES8311 microphone/DAC-reference wire v2; offline only."""
import asyncio
import json
import struct
import unittest
from unittest.mock import patch
from pocket.gpt_live import LiveError, LiveOutcome
from pocket.live_device import run_device_gpt_live
from tests.test_live_device import Stream
try:
    from pocket import live_device_v2 as v2
except ImportError:
    v2 = None

PAIR = struct.pack('<480h', *([1000, -2000] * 240))
MONO = struct.pack('<240h', *([1000] * 240))

def start(**changes):
    value = dict(type='start', version=2, rate=24000, channels=2,
                 format='pcm_s16le', reference='es8311_dac_right', chat_id='chat_one')
    value.update(changes)
    return json.dumps(value)

class PairedEcho:
    def __init__(self): self.frames=[]; self.closed=False
    async def capture_paired(self, pcm, capture_sample, dma_consumed):
        self.frames.append((pcm,capture_sample,dma_consumed))
        return MONO
    async def aclose(self): self.closed=True

class CodecTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(v2, 'paired v2 wire is missing')
    def test_literal_pair_packet_and_sample_units(self):
        packet=v2.AudioFrame(1,0,0,480,PAIR)
        encoded=v2.encode_frame(packet)
        self.assertEqual(encoded[:16],bytes.fromhex('504c02010000000000000000000001e0'))
        self.assertEqual(encoded[16:],PAIR)
        self.assertEqual(v2.decode_frame(encoded),packet)
        state=v2.StreamState(1);state.accept(packet,speaker_samples_sent=480)
        self.assertEqual((state.samples,state.dma_consumed),(240,480))
    def test_replay_format_credit_overflow_reject_without_advancing(self):
        s=v2.StreamState(1)
        for bad in (v2.AudioFrame(1,1,0,0,PAIR),v2.AudioFrame(1,0,0,1,PAIR),
                    v2.AudioFrame(1,0,0,0,PAIR[:-4]),v2.AudioFrame(1,0,0,0,PAIR+PAIR),
                    v2.AudioFrame(1,0,2**32-200,0,PAIR)):
            with self.assertRaises(LiveError): s.accept(bad)
        self.assertEqual((s.samples,s.sequence,s.dma_consumed),(0,0,0))
        encoded=b'PL\1\1'+struct.pack('!III',0,0,0)+PAIR
        with self.assertRaises(LiveError):v2.decode_frame(encoded)
        with self.assertRaises(LiveError):v2.encode_frame(v2.AudioFrame(2,0,0,1,MONO))

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_after_validated_v2_start_retains_unknown_playout_shape(self):
        from pocket.schema import identifier
        stop=asyncio.Event()
        def stop_on_chat(value):
            result=identifier(value)
            if value=='chat_one':stop.set()
            return result
        with patch('pocket.live_device.identifier',stop_on_chat):
            out=await run_device_gpt_live(object(),object(),'device',Stream(start()),stop,echo=PairedEcho())
        self.assertEqual(out.actual_playout,'unknown')
    async def test_v2_sends_only_processed_mic_and_never_treats_credit_as_playout(self):
        self.assertIsNotNone(v2)
        stream=Stream(start()); echo=PairedEcho();stop=asyncio.Event()
        from pocket.service import Service, FakeHermes
        from pocket.store import Store
        store=Store(':memory:');self.addCleanup(store.close);service=Service(store,FakeHermes())
        async def session(client,backend,mic,playback,signal,*,settings):
            stream.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,0,0,0,PAIR)))
            self.assertEqual(await anext(mic),MONO)
            await playback(MONO)
            stream.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,1,240,240,PAIR)))
            self.assertEqual(await anext(mic),MONO)
            stream.incoming.put_nowait('{"type":"stop"}')
            await signal.wait()
            return LiveOutcome(True,'stopped',False,False)
        with patch('pocket.live_device.run_gpt_live',session):
            out=await run_device_gpt_live(object(),service,'device',stream,stop,echo=echo)
        self.assertTrue(out.playback_pending, 'all DMA consumed is not proven actual playout')
        self.assertEqual(out.actual_playout,'unknown')
        ready=json.loads(stream.sent[0])
        self.assertEqual(ready,dict(type='ready',version=2,rate=24000,reference='es8311_dac_right'))
        self.assertEqual(v2.decode_frame(stream.sent[1]).pcm,MONO)
        self.assertEqual(echo.frames,[(PAIR,0,0),(PAIR,240,240)])
        self.assertTrue(echo.closed)
    async def test_wrong_reference_or_processor_fails_before_provider(self):
        for raw,echo in ((start(reference='guessed'),PairedEcho()),(start(channels=1),PairedEcho()),
                         (start(),object()),(start(version=True),PairedEcho())):
            with patch('pocket.live_device.run_gpt_live') as run:
                with self.assertRaises(LiveError):
                    await run_device_gpt_live(object(),object(),'device',Stream(raw),asyncio.Event(),echo=echo)
                run.assert_not_called()
    async def test_bounded_paired_capture_queue_and_credit_window(self):
        self.assertIsNotNone(v2)
        stream=Stream();bridge=v2.PairedDeviceAudioBridge(stream,PairedEcho(),credit_timeout=.02)
        for n in range(9):stream.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,n,240*n,0,PAIR)))
        with self.assertRaisesRegex(LiveError,'device_capture_backpressure'):
            await bridge.receive(asyncio.Event())
        self.assertEqual(bridge.microphone_queue.qsize(),8)
        with self.assertRaisesRegex(LiveError,'device_playback_credit_timeout'):
            await bridge.playback(MONO*32)
        self.assertEqual(bridge.output_state.samples,1920)

    async def test_credit_releases_window_without_claiming_audible_completion(self):
        window_full=asyncio.Event()
        class Observed(Stream):
            async def send(self,data):
                await super().send(data)
                if len(self.sent)==4:window_full.set()
        stream=Observed();bridge=v2.PairedDeviceAudioBridge(stream,PairedEcho(),credit_timeout=.5)
        stop=asyncio.Event();reader=asyncio.create_task(bridge.receive(stop))
        sender=asyncio.create_task(bridge.playback(MONO*10))
        try:
            await asyncio.wait_for(window_full.wait(),1)
            self.assertFalse(sender.done())
            stream.incoming.put_nowait(v2.encode_frame(v2.AudioFrame(1,0,0,480,PAIR)))
            await asyncio.wait_for(sender,1)
            frames=[v2.decode_frame(x) for x in stream.sent]
            self.assertEqual([x.sample_offset for x in frames],[0,480,960,1440,1920])
            self.assertTrue(bridge.playback_pending)
        finally:
            reader.cancel();sender.cancel()
            await asyncio.gather(reader,sender,return_exceptions=True)
