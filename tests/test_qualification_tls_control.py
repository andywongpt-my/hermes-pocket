"""Synthetic USB transport only: no serial discovery/open, network, or secrets."""
import asyncio
import ctypes
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest

from pocket import qualification as q
from pocket.qualification_tls_control import TLSConfig, arm_tls, TLSObservation, TLSCollector, numeric_json

TOKEN='synthetic-only-token-000000000001'
CA=b'-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n'


def config(**kw):
    return TLSConfig(**dict(dict(host='192.168.1.10',port=8443,token=TOKEN,ca_pem=CA,duration_ms=1000),**kw))


def start(nonce=7, profile=1, **kw):
    fields=[1,24000,2,240,1000,6000,1440000,0 if profile==1 else 144000,0,0,10000,0]
    for k,v in kw.items():fields[int(k)]=v
    return q.Record(19,profile,nonce,0,0,struct.pack('<12I',*fields))


def terminal(nonce=7, profile=1, **kw):
    fields=[1,0,1,10,2400,240,480,0,0,0,100,101,102,1,1,1,1,0,10000,5000,2000,10,110,0]
    for k,v in kw.items():fields[int(k)]=v
    return q.Record(20,profile,nonce,1,0,struct.pack('<24I',*fields))


def wire(record):
    return q.encode(record.kind,record.profile,record.nonce,record.sequence,record.sample_offset,record.payload)


class FakeUSB:
    identity='approved-synthetic-device'
    def __init__(self,chunks=()):self.chunks=list(chunks);self.writes=[];self.reads=0
    async def read(self,maximum):
        self.reads+=1
        if self.chunks:return self.chunks.pop(0)
        await asyncio.sleep(.01);return None
    async def write(self,data):self.writes.append(data);return len(data)


class TLSConfigTests(unittest.TestCase):
    def test_private_config_exact_wire_and_redacted_repr(self):
        value=config(); record=arm_tls(value,nonce=7)
        self.assertEqual((record.kind,record.profile,record.sequence,record.sample_offset),(4,1,0,0))
        lengths=(len(value.host)|(len(TOKEN)<<16),len(CA))
        self.assertEqual(record.payload,struct.pack('<9I',24000,60000,1440000,1024,3145728,8443,1000,*lengths)+value.host.encode()+TOKEN.encode()+CA)
        for secret in (TOKEN,value.host,'CERTIFICATE'):
            self.assertNotIn(secret,repr(value));self.assertNotIn(secret,repr(record))
        self.assertEqual(list(q.Parser().feed(wire(record))),[record])

    def test_invalid_explicit_config_boundaries(self):
        for kw in ({'host':'127.0.0.1'},{'host':'192.168.01.1'},{'host':'172.32.0.1'},
                   {'host':'example.com'},{'host':'169.254.1.1'},{'host':'10.0.0.1\n'},
                   {'token':'x'*31},{'token':'x'*129},{'token':'x'*31+'/'},
                   {'port':True},{'port':0},{'duration_ms':60001},{'duration_ms':0},
                   {'ca_pem':b''},{'ca_pem':b'x'*769},{'ca_pem':b'not PEM'}):
            with self.subTest(kw=tuple(kw)):
                with self.assertRaises(q.QualificationError):config(**kw)
        for host in ('10.0.0.1','172.16.0.1','172.31.255.255','192.168.0.1'):
            self.assertEqual(config(host=host).host,host)

    def test_actual_c_codec_roundtrip(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            library=Path(directory)/'wire.so'
            built=subprocess.run(['gcc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                str(root/'firmware/core/pocket_qualification_protocol.c'),'-o',str(library)],
                capture_output=True,timeout=20)
            self.assertEqual(built.returncode,0,built.stderr)
            class Packet(ctypes.Structure):
                _fields_=[('nonce',ctypes.c_uint64),('sequence',ctypes.c_uint32),('offset',ctypes.c_uint32),
                          ('kind',ctypes.c_uint8),('profile',ctypes.c_uint8),('payload',ctypes.c_void_p),('bytes',ctypes.c_size_t)]
            lib=ctypes.CDLL(str(library))
            lib.pocket_qualification_decode.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(Packet)]
            lib.pocket_qualification_encode.argtypes=[ctypes.POINTER(Packet),ctypes.c_void_p,ctypes.c_size_t]
            lib.pocket_qualification_encode.restype=ctypes.c_size_t
            for record in (arm_tls(config(),nonce=7),start(),terminal()):
                raw=wire(record);data=ctypes.create_string_buffer(raw);packet=Packet()
                self.assertEqual(lib.pocket_qualification_decode(data,len(raw),ctypes.byref(packet)),0)
                output=ctypes.create_string_buffer(1056)
                n=lib.pocket_qualification_encode(ctypes.byref(packet),output,len(output))
                self.assertEqual(output.raw[:n],raw)


class ObservationTests(unittest.TestCase):
    def test_numeric_summary_unknown_playout_never_accepts_physical(self):
        observation=TLSObservation(profile=1,nonce=7,duration_ms=1000)
        observation.accept(start());observation.accept(terminal())
        result=json.loads(numeric_json(observation.report()))
        self.assertEqual(result['terminal_received'],1);self.assertEqual(result['protocol_complete'],1)
        self.assertEqual(result['actual_playout_known'],0);self.assertEqual(result['physical_acceptance'],0)
        self.assertTrue(all(type(v) in (int,float) for v in result.values()))
        with self.assertRaises(q.QualificationError):numeric_json(dict(result,token=123))

    def test_start_terminal_continuity_and_caps(self):
        for record in (start(nonce=8),terminal(),start(**{'4':2000}),start(**{'8':1}),start(**{'11':1})):
            with self.subTest(kind=record.kind):
                with self.assertRaises(q.QualificationError):TLSObservation(profile=1,nonce=7,duration_ms=1000).accept(record)
        for changes in ({'0':2},{'1':15},{'2':3},{'3':6001},{'4':2401},{'5':481},
                        {'6':1440001},{'7':2},{'13':2},{'23':1},{'12':100},
                        {'10':111},{'2':1,'13':0},{'16':1,'15':0}):
            observation=TLSObservation(profile=1,nonce=7,duration_ms=1000);observation.accept(start())
            with self.subTest(changes=changes):
                with self.assertRaises(q.QualificationError):observation.accept(terminal(**changes))

    def test_pending_backend_is_not_complete_and_forged_pass_is_rejected(self):
        for changes in ({'17':1},):
            observation=TLSObservation(profile=1,nonce=7,duration_ms=1000)
            observation.accept(start());observation.accept(terminal(**changes))
            self.assertEqual(observation.report()['protocol_complete'],0)
        for report in ({'physical_acceptance':1},{'actual_playout_known':1}):
            with self.assertRaises(q.QualificationError):numeric_json(report)

    def test_clean_deadline_stop_is_protocol_complete_not_physical_acceptance(self):
        observation=TLSObservation(profile=1,nonce=7,duration_ms=1000)
        observation.accept(start())
        observation.accept(terminal(**{'8':1,'10':1000,'11':1001,'12':1002,'22':1010}))
        report=observation.report()
        self.assertEqual(report['protocol_complete'],1)
        self.assertEqual(report['deadline_expired'],1)
        self.assertEqual(report['physical_acceptance'],0)
        self.assertEqual(report['actual_playout_known'],0)

    def test_large_numeric_values_fail_with_fixed_validation_error(self):
        for value in (10**1000,float('nan'),float('inf'),True):
            with self.assertRaises(q.QualificationError):numeric_json({'epoch':value})
        with self.assertRaises(q.QualificationError):
            TLSCollector(FakeUSB(),'approved-synthetic-device',config(),deadline_seconds=10**1000)

    def test_quarantine_is_receipt_not_completion(self):
        observation=TLSObservation(profile=1,nonce=7,duration_ms=1000);observation.accept(start())
        observation.accept(terminal(**{'1':10,'2':2,'12':q.UNKNOWN,'13':0,'14':0,'15':0,'16':0}))
        self.assertEqual(observation.report()['terminal_received'],1)
        self.assertEqual(observation.report()['protocol_complete'],0)


class TLSCollectorTests(unittest.IsolatedAsyncioTestCase):
    def collector(self,usb,**kw):
        return TLSCollector(usb,'approved-synthetic-device',config(),capture_approved=True,nonce=7,**kw)

    async def test_two_record_stream_one_arm_and_no_raw(self):
        raw=wire(start())+wire(terminal())
        usb=FakeUSB([raw[:17],raw[17:90],raw[90:]])
        collector=self.collector(usb);result=await collector.run()
        self.assertEqual(result.report()['protocol_complete'],1)
        sent=[item for data in usb.writes for item in q.Parser().feed(data)]
        self.assertEqual([x.kind for x in sent],[4])
        self.assertFalse(hasattr(result,'raw'))
        self.assertIsNone(collector.config)
        with self.assertRaises(q.QualificationError):await collector.run()

    async def test_authority_is_exact_and_audio_distinct(self):
        for kwargs in ({'capture_approved':False},{'profile':2},{'approved_identity':'other'}):
            usb=FakeUSB();options=dict(capture_approved=True,approved_identity=usb.identity);options.update(kwargs)
            collector=TLSCollector(usb,options.pop('approved_identity'),config(),**options)
            with self.assertRaises(q.QualificationError):await collector.run()
            self.assertEqual(usb.writes,[])
        usb=FakeUSB([wire(start(profile=2))+wire(terminal(profile=2))])
        result=await self.collector(usb,profile=2,audible_approved=True).run()
        self.assertEqual(result.report()['protocol_complete'],1)

    async def test_missing_terminal_oversize_corruption_trailing_are_incomplete_and_stop(self):
        badcrc=bytearray(wire(start()));badcrc[-1]^=1
        for chunks in ([b''],[bytes(4097)],[bytes(badcrc)],[wire(start()),b'']):
            usb=FakeUSB(chunks);result=await self.collector(usb).run()
            self.assertEqual(result.report()['protocol_complete'],0)
            self.assertNotEqual(result.report()['host_error'],0)
            self.assertEqual(usb.writes[-1][5],3)

    async def test_live_terminal_boundary_discards_resumed_console_without_further_reads(self):
        for tail in (b'normal console token must not be retained',wire(terminal())):
            usb=FakeUSB([wire(start())+wire(terminal())+tail,b'never read'])
            result=await self.collector(usb).run()
            report=result.report()
            self.assertEqual(report['protocol_complete'],1)
            self.assertEqual(report['wire_bytes'],208)
            self.assertEqual(usb.reads,1)
            self.assertNotIn('token',numeric_json(report))
        observation=TLSObservation(profile=1,nonce=7,duration_ms=1000)
        observation.accept(start());observation.accept(terminal())
        with self.assertRaises(q.QualificationError):observation.accept(terminal())

    async def test_stalled_clock_real_deadline_heartbeat_and_bounded_stop(self):
        usb=FakeUSB();began=time.monotonic()
        result=await self.collector(usb,deadline_seconds=.22,clock=lambda:1).run()
        self.assertLess(time.monotonic()-began,.4)
        self.assertEqual(result.report()['host_error'],2)
        controls=[list(q.Parser().feed(x))[0] for x in usb.writes]
        self.assertEqual(controls[0].kind,4);self.assertEqual(controls[-1].kind,3)
        self.assertIn(2,[x.kind for x in controls])
        self.assertEqual([x.sequence for x in controls],list(range(len(controls))))

    async def test_clock_regression_write_loss_cancellation(self):
        ticks=iter([5,5,4,4,4]);usb=FakeUSB()
        result=await self.collector(usb,clock=lambda:next(ticks,4)).run()
        self.assertEqual(result.report()['host_error'],4)
        class Partial(FakeUSB):
            async def write(self,data):self.writes.append(data);return len(data)-1
        usb=Partial();result=await self.collector(usb).run()
        self.assertEqual([x[5] for x in usb.writes],[4,3]);self.assertEqual(result.report()['protocol_complete'],0)
        usb=FakeUSB();collector=self.collector(usb)
        task=asyncio.create_task(collector.run());await asyncio.sleep(.02);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(usb.writes[-1][5],3);self.assertIsNone(collector.config)

    async def test_slow_initial_write_reserves_time_for_stop(self):
        class SlowARM(FakeUSB):
            async def write(self,data):
                self.writes.append(data)
                if data[5]==4:await asyncio.sleep(1)
                return len(data)
        usb=SlowARM();began=time.monotonic()
        result=await self.collector(usb,deadline_seconds=.03).run()
        self.assertLess(time.monotonic()-began,.1)
        self.assertEqual([x[5] for x in usb.writes],[4,3])
        self.assertEqual(result.report()['protocol_complete'],0)

    async def test_bounded_serial_idle_poll_is_not_cancelled_for_heartbeat_or_stop(self):
        from pocket.qualification_serial import ExplicitSerialTransport
        events=[]
        class Driver:
            def open(self):pass
            def close(self):pass
            def write(self,data):events.append(data[5]);return len(data)
            def read(self,maximum):time.sleep(.02);return b''
        usb=ExplicitSerialTransport(port='synthetic-only',identity='approved-synthetic-device',
            open_approved=True,reset_risk_acknowledged=True,recovery_confirmed=True,
            identity_verified=True,platform_behavior_verified=True,serial_factory=Driver)
        await usb.open()
        try:
            result=await self.collector(usb,deadline_seconds=.3).run()
            self.assertEqual(result.report()['host_error'],2)
            self.assertFalse(usb.quarantined)
            self.assertIn(2,events);self.assertEqual(events[-1],3)
        finally:
            while usb.owner_pending:await asyncio.sleep(.005)
            self.assertTrue(await usb.close())

    async def test_start_never_refreshes_total_observation_deadline(self):
        class Delayed(FakeUSB):
            async def read(self,maximum):
                await asyncio.sleep(.03)
                if self.chunks:return self.chunks.pop(0)
                return None
        usb=Delayed([wire(start())]);began=time.monotonic()
        result=await self.collector(usb,deadline_seconds=.15).run()
        self.assertLess(time.monotonic()-began,.3)
        self.assertEqual(result.report()['host_error'],2)
