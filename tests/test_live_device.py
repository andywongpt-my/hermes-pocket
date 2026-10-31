"""Offline device-stream contract; no provider, socket or physical audio."""
import asyncio
import base64
import json
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pocket.gpt_live import LiveError, LiveOutcome, LiveSettings
try:
    from pocket import live_device as wire
except ImportError:
    wire = None


PCM = b'\x01\x00' * 480


def packet(kind=1, sequence=0, offset=0, played=0, pcm=PCM):
    return b'PL' + bytes((1, kind)) + struct.pack('!III', sequence, offset, played) + pcm


def start(**changes):
    value = dict(type='start', version=1, rate=24000, channels=1,
                 format='pcm_s16le', chat_id='chat_one')
    value.update(changes)
    return json.dumps(value)


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(wire, 'device-stream codec is not implemented')

    def test_literal_wire_header_and_pcm_endianness(self):
        frame = wire.AudioFrame(2, 7, 480, 0, b'\x34\x12\xcc\xed')
        encoded = wire.encode_frame(frame)
        self.assertEqual(encoded, bytes.fromhex('504c010200000007000001e0000000003412cced'))
        self.assertEqual(wire.decode_frame(encoded), frame)

    def test_invalid_frames_are_rejected_before_reading_pcm(self):
        for value in (b'', b'PL', packet()[:15], packet()[:-1], packet(pcm=PCM+b'\0\0'),
                      b'XX'+packet()[2:], b'PL\x02'+packet()[3:], packet(kind=3),
                      packet(pcm=b'\0\0'), packet(kind=2, played=1)):
            with self.subTest(size=len(value)):
                with self.assertRaises(LiveError): wire.decode_frame(value)
        for field in (-1, 2**32-1, 2**32, True):
            with self.assertRaises(LiveError):
                wire.encode_frame(wire.AudioFrame(2, field, 0, 0, b'\0\0'))

    def test_state_rejects_replay_gap_ack_ahead_and_rollover_without_advancing(self):
        state = wire.StreamState(1)
        state.accept(wire.decode_frame(packet()), speaker_samples_sent=480)
        for value in (packet(), packet(sequence=2,offset=480),
                      packet(sequence=1,offset=481), packet(sequence=1,offset=480,played=481)):
            with self.assertRaises(LiveError):
                state.accept(wire.decode_frame(value), speaker_samples_sent=480)
        state.accept(wire.decode_frame(packet(sequence=1,offset=480,played=480)), speaker_samples_sent=480)
        with self.assertRaises(LiveError):
            state.accept(wire.decode_frame(packet(sequence=2,offset=960,played=0)), speaker_samples_sent=480)
        self.assertEqual((state.sequence,state.samples,state.played_samples),(2,960,480))
        state.sequence=2**32-1
        with self.assertRaises(LiveError):
            state.accept(wire.AudioFrame(1,2**32-1,960,480,PCM), speaker_samples_sent=480)
        state.sequence=2;state.samples=2**32-200
        with self.assertRaises(LiveError):
            state.accept(wire.AudioFrame(1,2,2**32-200,480,PCM), speaker_samples_sent=480)


class Stream:
    def __init__(self, first=None):
        self.incoming=asyncio.Queue();self.sent=[];self.closed=False
        if first is not None:self.incoming.put_nowait(first)
    async def recv(self): return await self.incoming.get()
    async def send(self, data): self.sent.append(data)
    async def close(self): self.closed=True


class Echo:
    def __init__(self):self.inputs=[];self.references=[]
    async def capture(self, pcm, capture_sample, played_samples):
        self.inputs.append((pcm,capture_sample,played_samples));return pcm
    async def playback(self, pcm, sample_offset):self.references.append((pcm,sample_offset))


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.assertIsNotNone(wire, 'device-stream adapter is not implemented')

    async def test_owned_native_echo_is_closed_even_before_session_start(self):
        class OwnedEcho(Echo):
            def __init__(self): super().__init__(); self.closed = False
            async def aclose(self): self.closed = True
        echo = OwnedEcho()
        stream = Stream(start(rate=16000))
        with self.assertRaises(LiveError):
            await wire.run_device_gpt_live(object(), object(), 'device', stream,
                                          asyncio.Event(), echo=echo)
        self.assertTrue(echo.closed, 'native AEC owner must be disposed on invalid handshake')

    async def test_echo_cleanup_failure_remains_explicit(self):
        class BadClose(Echo):
            async def aclose(self): raise RuntimeError('synthetic cleanup error')
        stop = asyncio.Event(); stop.set()
        with self.assertRaisesRegex(LiveError, 'device_echo_cleanup_failed'):
            await wire.run_device_gpt_live(object(), object(), 'device', Stream(), stop,
                                          echo=BadClose())

    async def test_wrong_format_or_missing_echo_never_calls_live(self):
        for first,echo in ((start(rate=16000),Echo()),(start(),None),
                           ('{"type":"start","type":"stop"}',Echo()),
                           (start(version=True),Echo()),(start(extra=1),Echo())):
            stream=Stream(first)
            with patch('pocket.live_device.run_gpt_live') as run:
                with self.assertRaises(LiveError):
                    await wire.run_device_gpt_live(object(),object(),'device',stream,asyncio.Event(),echo=echo)
                run.assert_not_called()
            self.assertTrue(stream.closed)

    async def test_trusted_identity_and_preexisting_stop_prevent_acquisition(self):
        stream=Stream(start());stop=asyncio.Event();stop.set()
        with patch('pocket.live_device.run_gpt_live') as run:
            result=await wire.run_device_gpt_live(object(),object(),'device',stream,stop,echo=Echo())
            self.assertFalse(result.finalized);run.assert_not_called()
        self.assertTrue(stream.closed)
        with self.assertRaises(LiveError):
            await wire.run_device_gpt_live(object(),object(),'bad device',Stream(start()),asyncio.Event(),echo=Echo())

    async def test_playback_splits_frames_and_microphone_preserves_actual_playout_ack(self):
        stream=Stream();echo=Echo();bridge=wire.DeviceAudioBridge(stream,echo)
        await bridge.playback(PCM+PCM+b'\x02\x00')
        frames=[wire.decode_frame(value) for value in stream.sent]
        self.assertEqual([(x.sequence,x.sample_offset,len(x.pcm)) for x in frames],[(0,0,960),(1,480,960),(2,960,2)])
        self.assertEqual(b''.join(x.pcm for x in frames),PCM+PCM+b'\x02\x00')
        self.assertEqual([x[1] for x in echo.references],[0,480,960])
        stream.incoming.put_nowait(packet(played=480));stream.incoming.put_nowait('{"type":"stop"}')
        stop=asyncio.Event();await bridge.receive(stop)
        mic=bridge.microphone();self.assertEqual(await anext(mic),PCM)
        self.assertEqual(echo.inputs,[(PCM,0,480)])
        await mic.aclose();self.assertTrue(stop.is_set())
        self.assertEqual(json.loads(stream.sent[-1])['type'],'ready')

    async def test_bounded_microphone_and_playback_backpressure_fail_closed(self):
        stream=Stream();bridge=wire.DeviceAudioBridge(stream,Echo())
        for n in range(9):stream.incoming.put_nowait(packet(sequence=n,offset=480*n))
        with self.assertRaisesRegex(LiveError,'device_capture_backpressure'):
            await bridge.receive(asyncio.Event())
        self.assertEqual(bridge.microphone_queue.qsize(),8)
        stream=Stream();bridge=wire.DeviceAudioBridge(stream,Echo())
        await bridge.playback(PCM*16)
        with self.assertRaisesRegex(LiveError,'device_playback_backpressure'):await bridge.playback(PCM)
        self.assertEqual(len(stream.sent),16)

    async def test_eof_bad_echo_and_out_of_order_frames_fail_explicitly(self):
        stream=Stream();bridge=wire.DeviceAudioBridge(stream,Echo())
        stream.incoming.put_nowait(None)
        with self.assertRaisesRegex(LiveError,'device_connection_lost'):await bridge.receive(asyncio.Event())
        stream.incoming.put_nowait(packet(sequence=3))
        with self.assertRaises(LiveError):await bridge.receive(asyncio.Event())
        class BadEcho(Echo):
            async def capture(self,*args):return b''
        stream=Stream();bridge=wire.DeviceAudioBridge(stream,BadEcho())
        stream.incoming.put_nowait(packet());stream.incoming.put_nowait('{"type":"stop"}')
        await bridge.receive(asyncio.Event())
        mic=bridge.microphone()
        with self.assertRaisesRegex(LiveError,'invalid_echo_audio'):await anext(mic)
        await mic.aclose()

    async def test_adapter_delegates_to_existing_hermes_with_concurrent_audio_and_stop(self):
        # Mock only the paid transport boundary; real durable Hermes path is used.
        from pocket.service import Service,FakeHermes
        from pocket.store import Store
        class CompleteHermes(FakeHermes):
            def create(self,*args,**kwargs):
                run=super().create(*args,**kwargs);self.complete(run,'synthetic result');return run
        store=Store(':memory:');service=Service(store,CompleteHermes());stream=Stream(start());echo=Echo();stop=asyncio.Event()
        async def session(client,backend,microphone,playback,signal,*,settings):
            stream.incoming.put_nowait(packet())
            self.assertEqual(await anext(microphone),PCM)
            result=await backend('live_one','delegation_one','synthetic request')
            self.assertEqual(result['state'],'completed')
            await playback(PCM)
            stream.incoming.put_nowait('{"type":"stop"}')
            await asyncio.wait_for(signal.wait(),1)
            return LiveOutcome(True,'stopped',False,False)
        try:
            with patch('pocket.live_device.run_gpt_live',session):
                result=await wire.run_device_gpt_live(object(),service,'device',stream,stop,echo=echo)
            self.assertTrue(result.finalized);self.assertTrue(stream.closed)
            self.assertTrue(result.playback_pending, 'sent audio is not actual device playout')
            self.assertEqual(store.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        finally:store.close()

    async def test_full_adapter_runs_live_pump_and_durable_hermes_without_provider(self):
        from tests.test_gpt_live import FakeConnection,sdk_client,transcript,delegation
        from pocket.service import Service,FakeHermes
        from pocket.store import Store
        class CompleteHermes(FakeHermes):
            def create(self,*args,**kwargs):
                run=super().create(*args,**kwargs);self.complete(run,'verified result');return run
        played=asyncio.Event()
        class Device(Stream):
            async def send(self,data):
                await super().send(data)
                if isinstance(data,bytes):
                    self.incoming.put_nowait(packet(sequence=1,offset=480,played=480))
                    played.set()
        store=Store(':memory:');self.addCleanup(store.close)
        upstream=CompleteHermes();service=Service(store,upstream)
        stream=Device(start());stream.incoming.put_nowait(packet())
        connection=FakeConnection()
        connection.after_start=[transcript(),delegation(),
            {'type':'session.output_audio.delta','delta':base64.b64encode(PCM).decode()}]
        async def observe(event):
            if event['type']=='session.commentary.append':
                await played.wait();stream.incoming.put_nowait('{"type":"stop"}')
        connection.on_send=observe
        result=await asyncio.wait_for(wire.run_device_gpt_live(sdk_client(connection),service,
            'device',stream,asyncio.Event(),echo=Echo()),1)
        self.assertTrue(result.finalized);self.assertFalse(result.playback_pending)
        self.assertEqual(upstream.submissions,1);self.assertTrue(connection.closed);self.assertTrue(stream.closed)
        self.assertTrue(any(event['type']=='session.input_audio.append' for event in connection.sent))
        commentary=[event for event in connection.sent if event['type']=='session.commentary.append']
        self.assertEqual(commentary[0]['content'],'verified result')

    async def test_preparing_echo_does_not_acknowledge_unsent_speaker_audio(self):
        prepared=asyncio.Event()
        class WaitingEcho(Echo):
            async def playback(self,*args):
                prepared.set();await asyncio.Event().wait()
        stream=Stream();bridge=wire.DeviceAudioBridge(stream,WaitingEcho())
        speaker=asyncio.create_task(bridge.playback(PCM))
        try:
            await prepared.wait()
            stream.incoming.put_nowait(packet(played=480))
            stream.incoming.put_nowait('{"type":"stop"}')
            with self.assertRaisesRegex(LiveError,'device_stream_discontinuity'):
                await bridge.receive(asyncio.Event())
            self.assertEqual(stream.sent,[])
        finally:
            speaker.cancel();await asyncio.gather(speaker,return_exceptions=True)

    async def test_stop_during_hello_returns_stopped_without_waiting_for_timeout(self):
        stop=asyncio.Event();receiving=asyncio.Event()
        class WaitingStream(Stream):
            async def recv(self):
                receiving.set();return await super().recv()
        stream=WaitingStream()
        task=asyncio.create_task(wire.run_device_gpt_live(object(),object(),'device',stream,stop,
            echo=Echo(),settings=LiveSettings(startup_timeout=.3)))
        await receiving.wait();stop.set()
        result=await asyncio.wait_for(task,.1)
        self.assertEqual(result.reason,'stopped_before_connect');self.assertTrue(stream.closed)

    async def test_reader_failure_cancels_local_wait_and_closes_transport(self):
        from pocket.service import Service,FakeHermes
        from pocket.store import Store
        store=Store(':memory:');self.addCleanup(store.close)
        service=Service(store,FakeHermes())
        stream=Stream(start());closed=asyncio.Event()
        async def session(*args,**kwargs):
            stream.incoming.put_nowait(packet(sequence=5))
            try:await asyncio.Event().wait()
            finally:closed.set()
        with patch('pocket.live_device.run_gpt_live',session):
            with self.assertRaises(LiveError):
                await wire.run_device_gpt_live(object(),service,'device',stream,asyncio.Event(),echo=Echo())
        self.assertTrue(stream.closed);self.assertTrue(closed.is_set())


if __name__=='__main__':unittest.main()
