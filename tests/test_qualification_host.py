"""HPQ1 host boundary: synthetic bytes only, no real serial or hardware access."""
import asyncio
import struct
import tempfile
from pathlib import Path
import unittest
import zlib

try:
    from pocket import qualification as q
except ImportError:
    q = None

HEADER = struct.Struct('<4sBBBBQIIHHI')
NONCE = 0x1020304050607080


def record(kind, payload=b'', *, profile=1, sequence=0, offset=0, nonce=NONCE):
    prefix = HEADER.pack(b'HPQ1', 1, kind, profile, 0, nonce, sequence, offset, len(payload), 0, 0)
    crc = zlib.crc32(payload, zlib.crc32(prefix[:28]))
    return prefix[:28] + struct.pack('<I', crc) + payload


def start(profile=1):
    values = (7, 24000, 2, 240, 1000 if profile == 1 else 2400,
              0 if profile == 1 else 576000, 0 if profile == 1 else 144000,
              0 if profile == 1 else 512, 0 if profile == 1 else 5,
              2000, 10250 if profile == 1 else 24250, 0 if profile == 1 else 1)
    return record(16, struct.pack('<12I', *values), profile=profile)


def pcm(index, *, profile=1, sample=b'\0\0\0\0', **changes):
    fields = dict(epoch=7, capture_sequence=index, elapsed_ms=(index+1)*10,
                  submitted=0, consumed=0, queue=1, reserved_a=0, reserved_b=0)
    fields.update(changes)
    return record(17, struct.pack('<8I', *fields.values()) + sample*240,
                  profile=profile, sequence=index+1, offset=index*240)


def terminal(count, *, result=1, cleanup=1, source_complete=0, **changes):
    fields = dict(epoch=7, result=result, cleanup=cleanup, frames=count,
                  submitted=0, stimulus=0, consumed=0, elapsed_ms=(count+1)*10,
                  peak=0, wire_before=80+count*1024, source_complete=source_complete, reserved=0)
    fields.update(changes)
    return record(18, struct.pack('<12I', *fields.values()), sequence=count+1)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(q, 'qualification host module is not implemented')

    def capture(self, pieces):
        parser = q.Parser(); capture = q.Capture(1, NONCE)
        for piece in pieces:
            for at in range(0, len(piece), 4096):
                for item in parser.feed(piece[at:at+4096]): capture.accept(item)
        return capture

    def test_literal_codec_crc_and_bytewise_prefix(self):
        wire = start()
        parser = q.Parser(); records = []
        for byte in b'normal boot\r\n' + wire:
            records.extend(parser.feed(bytes([byte])))
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].kind, records[0].nonce, records[0].sequence), (16, NONCE, 0))
        self.assertEqual(parser.prefix_bytes, 13)
        self.assertEqual(q.encode(16, 1, NONCE, 0, 0, records[0].payload), wire)
        self.assertLessEqual(parser.peak_scratch_bytes, 8192)

    def test_crc_damage_is_terminal_not_resynchronization(self):
        parser = q.Parser(); broken = bytearray(start()); broken[-1] ^= 1
        with self.assertRaises(q.QualificationError): list(parser.feed(bytes(broken)+start()))
        with self.assertRaises(q.QualificationError): list(parser.feed(start()))

    def test_prefix_payload_chunk_and_aggregate_caps(self):
        parser = q.Parser()
        with self.assertRaises(q.QualificationError): list(parser.feed(b'x'*4097))
        parser = q.Parser()
        with self.assertRaises(q.QualificationError):
            for _ in range(5): list(parser.feed(b'x'*4096))
        for wire in (record(16, b'x'*1025), record(16, nonce=0), record(99)):
            with self.subTest(wire=wire[:8]):
                with self.assertRaises(q.QualificationError): list(q.Parser().feed(wire))
        parser = q.Parser()
        with self.assertRaises(q.QualificationError):
            for index in range(100000): list(parser.feed(record(2, sequence=index)))
        self.assertLessEqual(parser.peak_scratch_bytes, 8192)

    def test_last_pcm_is_not_terminal_and_completed_requires_exact_budget(self):
        capture = self.capture([start(), *[pcm(i) for i in range(1000)]])
        self.assertFalse(capture.complete)
        self.assertEqual(capture.raw_bytes, 960000)
        capture.accept(next(q.Parser().feed(terminal(1000, result=0, source_complete=1))))
        self.assertTrue(capture.complete)
        self.assertEqual(capture.report()['physical_acceptance'], 0)
        self.assertNotIn('pcm', capture.report())
        capture.close(); self.assertTrue(all(x == 0 for x in capture.raw))

    def test_nonce_sequence_epoch_offsets_metadata_terminal_rejected(self):
        cases = [record(17, b'\0'*992, sequence=1, nonce=1),
                 pcm(1), pcm(0, epoch=8), pcm(0, consumed=1),
                 pcm(0, queue=9), pcm(0, reserved_a=1),
                 terminal(0, result=0, source_complete=1),
                 terminal(0, wire_before=1), terminal(0, cleanup=3)]
        for wire in cases:
            with self.subTest(wire=wire[:32]):
                with self.assertRaises(q.QualificationError): self.capture([start(), wire])

    def test_numeric_allowlist_does_not_include_arbitrary_values(self):
        capture = self.capture([start(), pcm(0, sample=b'\x01\x02\x03\x04'), terminal(1)])
        report = capture.report(); text = q.numeric_json(report)
        self.assertNotIn('base64', text); self.assertNotIn('device', text)
        for bad in ({'pcm': 0}, {'profile': 'secret'}, {'frames': float('nan')}, {'frames': True}):
            with self.assertRaises(q.QualificationError): q.numeric_json(bad)

    def test_retention_requires_distinct_permission_and_fresh_posix_directory(self):
        capture = self.capture([start(), pcm(0, sample=b'\x01\0\x02\0'), terminal(1)])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'capture'
            self.assertEqual(list(Path(temporary).iterdir()), [])
            with self.assertRaises(q.QualificationError): q.retain_raw(capture, directory, approved=False)
            self.assertFalse(directory.exists())
            q.retain_raw(capture, directory, approved=True)
            self.assertEqual((directory/'paired.pcm').read_bytes(), b'\x01\0\x02\0'*240)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory/'paired.pcm').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(q.QualificationError): q.retain_raw(capture, directory, approved=True)
            self.assertEqual(len(list(directory.iterdir())), 2)


class FakeTransport:
    identity = 'approved-synthetic-device'
    def __init__(self, wire): self.wire=bytearray(wire); self.writes=[]; self.reads=0
    async def read(self, maximum):
        self.reads+=1
        data=bytes(self.wire[:maximum]); del self.wire[:maximum]
        await asyncio.sleep(0)
        return data
    async def write(self, data): self.writes.append(data); return len(data)


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.assertIsNotNone(q, 'qualification host module is not implemented')

    async def test_authority_and_identity_before_any_io_no_implicit_open(self):
        transport=FakeTransport(start()+terminal(0))
        for identity, approved, audible in [('wrong',True,False), (transport.identity,False,False)]:
            with self.assertRaises(q.QualificationError):
                await q.Collector(transport, identity, capture_approved=approved,
                                  audible_approved=audible, nonce=NONCE).run()
        self.assertEqual((transport.reads,transport.writes),(0,[]))
        with self.assertRaises(q.QualificationError):
            await q.Collector(transport, transport.identity, capture_approved=True,
                              profile=2, nonce=NONCE).run()
        self.assertEqual((transport.reads,transport.writes),(0,[]))

    async def test_arm_once_terminal_and_no_files_by_default(self):
        transport=FakeTransport(start()+pcm(0)+terminal(1))
        collector=q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE)
        result=await collector.run()
        self.assertTrue(result.terminal_received); self.assertFalse(result.complete)
        commands=list(q.Parser().feed(b''.join(transport.writes)))
        self.assertEqual([x.kind for x in commands],[1])
        self.assertEqual(commands[0].payload,struct.pack('<4I',24000,1000,1024,3145728))
        with self.assertRaises(q.QualificationError): await collector.run()

    async def test_missing_terminal_and_partial_write_never_complete(self):
        transport=FakeTransport(start()+pcm(0))
        result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE).run()
        self.assertFalse(result.terminal_received); self.assertFalse(result.complete)
        self.assertNotEqual(result.report()['host_error'],0)
        self.assertEqual([r.kind for r in q.Parser().feed(b''.join(transport.writes))],[1,3])
        class Partial(FakeTransport):
            async def write(self,data):self.writes.append(data);return len(data)-1
        transport=Partial(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE).run()
        self.assertFalse(result.complete);self.assertEqual(transport.reads,0)

    async def test_deadline_heartbeat_and_clock_regression(self):
        clock=[0.0]
        class Waiting(FakeTransport):
            async def read(self,maximum):
                clock[0]+=.11
                await asyncio.sleep(0)
                return None  # bounded no-data poll, not EOF
        transport=Waiting(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,
            nonce=NONCE,deadline_seconds=.35,clock=lambda:clock[0]).run()
        kinds=[r.kind for r in q.Parser().feed(b''.join(transport.writes))]
        self.assertIn(2,kinds);self.assertEqual(kinds[0],1);self.assertEqual(kinds[-1],3)
        self.assertFalse(result.complete);self.assertNotEqual(result.report()['host_error'],0)
        with self.assertRaises(q.QualificationError):
            q.Collector(transport,transport.identity,capture_approved=True,deadline_seconds=45.1)

class AdditionalHostTests(unittest.TestCase):
    setUp=HostTests.setUp
    capture=HostTests.capture
    def test_semantic_failure_poisoning_cannot_resume_capture(self):
        capture=self.capture([start()])
        with self.assertRaises(q.QualificationError):
            capture.accept(next(q.Parser().feed(pcm(1))))
        with self.assertRaises(q.QualificationError):
            capture.accept(next(q.Parser().feed(pcm(0))))

    def test_huge_report_integer_is_fixed_validation_error(self):
        with self.assertRaises(q.QualificationError): q.numeric_json({'frames':10**1000})

    def test_retention_symlink_parent_and_target_refuse_without_overwrite(self):
        capture=self.capture([start(),terminal(0)])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);target=root/'target';target.mkdir()
            linked=root/'linked';linked.symlink_to(target,target_is_directory=True)
            with self.assertRaises(q.QualificationError): q.retain_raw(capture,linked/'raw',approved=True)
            self.assertEqual(list(target.iterdir()),[])


class AdditionalCollectorTests(unittest.IsolatedAsyncioTestCase):
    setUp=CollectorTests.setUp
    async def test_heartbeat_write_crossing_stop_budget_never_reads_again(self):
        clock=[0.0]
        class DelayedHeartbeat(FakeTransport):
            async def read(self,maximum):
                self.reads+=1;clock[0]+=.11;await asyncio.sleep(0);return None
            async def write(self,data):
                self.writes.append(data)
                if data[5]==2:clock[0]+=.30
                return len(data)
        transport=DelayedHeartbeat(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,
            nonce=NONCE,deadline_seconds=.5,clock=lambda:clock[0]).run()
        self.assertFalse(result.complete);self.assertEqual(transport.reads,1)

    async def test_transport_exception_data_not_exposed_and_no_reconnect(self):
        class Failed(FakeTransport):
            async def read(self,maximum):raise RuntimeError('private microphone bytes must not escape')
        transport=Failed(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE).run()
        self.assertFalse(result.complete);self.assertNotEqual(result.report()['host_error'],0)
        self.assertNotIn('private',q.numeric_json(result.report()))

class WallDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_injected_clock_cannot_disable_real_wall_cap(self):
        class Empty(FakeTransport):
            async def read(self,maximum):await asyncio.sleep(0);return None
        transport=Empty(b'')
        result=await asyncio.wait_for(q.Collector(transport,transport.identity,capture_approved=True,
            nonce=NONCE,deadline_seconds=.08,clock=lambda:0.0).run(),.3)
        self.assertFalse(result.complete);self.assertEqual(result.host_error,2)
        self.assertEqual([r.kind for r in q.Parser().feed(b''.join(transport.writes))],[1,3])

class TLSFramingTests(unittest.TestCase):
    @staticmethod
    def arm():
        host=b'192.168.1.20';token=b'a'*32;ca=b'test-public-ca'
        return struct.pack('<9I',24000,60000,1440000,1024,3145728,443,60000,
                           len(host)|(len(token)<<16),len(ca))+host+token+ca

    def test_new_tls_records_roundtrip_and_stage_a_rejects(self):
        for kind,seq,payload in ((4,0,self.arm()),(19,0,b'\0'*48),(20,1,b'\0'*96)):
            wire=q.encode(kind,1,NONCE,seq,0,payload)
            item,=q.Parser().feed(wire)
            self.assertEqual((item.kind,item.sequence,item.payload),(kind,seq,payload))
            with self.assertRaises(q.QualificationError): q.Capture(1,NONCE).accept(item)

    def test_tls_sequence_lengths_reserved_and_budgets_fail_closed(self):
        original=self.arm()
        bad=[(4,1,original),(19,1,b'\0'*48),(20,0,b'\0'*96),(20,1,b'\0'*95)]
        for index,value in ((0,16000),(1,60001),(2,1440001),(3,1025),(4,3145729),
                            (5,0),(5,65536),(6,0),(6,60001),(7,7|(31<<16)),(8,65536|13)):
            mutated=bytearray(original);struct.pack_into('<I',mutated,index*4,value)
            bad.append((4,0,bytes(mutated)))
        bad.append((4,0,original+b'x'))
        for kind,seq,payload in bad:
            with self.subTest(kind=kind,seq=seq,payload_len=len(payload)):
                with self.assertRaises(q.QualificationError): q.encode(kind,1,NONCE,seq,0,payload)
                parser=q.Parser()
                with self.assertRaises(q.QualificationError): list(parser.feed(record(kind,payload,sequence=seq)))
                with self.assertRaises(q.QualificationError): list(parser.feed(start()))

    def test_numeric_report_cannot_claim_physical_acceptance(self):
        with self.assertRaises(q.QualificationError):q.numeric_json({'physical_acceptance':1})

class TerminalClosureTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_terminal_discards_coalesced_normal_console_without_further_read(self):
        valid=start()+b''.join(pcm(i) for i in range(1000))+terminal(1000,result=0,source_complete=1)
        for extra in (b'normal console resumed: private text\n',b'HPQ1',terminal(1000,result=0,source_complete=1)):
            transport=FakeTransport(valid+extra)
            result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE).run()
            self.assertTrue(result.complete)
            self.assertEqual(result.host_error,0)
            self.assertEqual(result.wire_bytes,len(valid))
            self.assertEqual(transport.reads,(len(valid)+len(extra)+4095)//4096)
            self.assertNotIn('private',q.numeric_json(result.report()))

class SummaryWriterTests(unittest.TestCase):
    def test_numeric_summary_creates_only_explicit_new_file(self):
        self.assertTrue(hasattr(q,'save_summary'),'numeric-only summary writer missing')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'summary.json'
            q.save_summary({'frames':0,'physical_acceptance':0},path)
            self.assertEqual(path.read_text(),'{"frames":0,"physical_acceptance":0}\n')
            self.assertEqual(list(Path(temp).iterdir()),[path])
            with self.assertRaises(q.QualificationError):q.save_summary({'frames':1},path)
            self.assertIn('"frames":0',path.read_text())
            invalid=Path(temp)/'invalid.json'
            with self.assertRaises(q.QualificationError):q.save_summary({'address':'private'},invalid)
            self.assertFalse(invalid.exists())

class ReviewGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_arm_preserves_stop_reserve(self):
        class SlowArm(FakeTransport):
            async def write(self,data):
                self.writes.append(data)
                if data[5]==1:await asyncio.sleep(.03)
                return len(data)
        transport=SlowArm(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE,
                                 deadline_seconds=.03).run()
        self.assertFalse(result.complete)
        self.assertEqual([x[5] for x in transport.writes],[1,3])

    async def test_clock_regression_still_attempts_stop_using_trusted_wall_budget(self):
        times=iter((5.,5.,4.))
        transport=FakeTransport(b'')
        result=await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE,
                                 clock=lambda:next(times,4.)).run()
        self.assertEqual(result.host_error,4)
        self.assertEqual([x[5] for x in transport.writes],[1,3])

    async def test_frozen_injected_clock_does_not_suppress_heartbeat(self):
        class Empty(FakeTransport):
            async def read(self,maximum):await asyncio.sleep(.01);return None
        transport=Empty(b'')
        await q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE,
                          deadline_seconds=.35,clock=lambda:0.).run()
        self.assertIn(2,[x[5] for x in transport.writes])

class ReviewCounterTests(unittest.TestCase):
    def test_audio_terminal_stimulus_must_match_exact_submitted_prefix(self):
        for submitted,stimulus in ((96001,1),(96240,0),(144000,47999),(576000,0)):
            capture=q.Capture(2,NONCE);capture.accept(next(q.Parser().feed(start(2))))
            payload=struct.pack('<12I',7,1,1,0,submitted,stimulus,0,1,0,80,0,0)
            with self.assertRaises(q.QualificationError):capture.accept(q.Record(18,2,NONCE,1,0,payload))

class SerialCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_bounded_serial_read_is_not_cancelled_near_stop(self):
        import time
        from pocket.qualification_serial import ExplicitSerialTransport
        from tests.test_qualification_serial import FakeSerial
        class TwentyMs(FakeSerial):
            def read(self,maximum):time.sleep(.02);return b''
        driver=TwentyMs(); observed=[0.]
        class NearDeadline(ExplicitSerialTransport):
            async def read(self,maximum):
                value=await super().read(maximum);observed[0]=.149;return value
        adapter=NearDeadline(port='COM7',identity='synthetic-only',open_approved=True,
            reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
            platform_behavior_verified=True,serial_factory=lambda:driver)
        await adapter.open()
        try:
            result=await q.Collector(adapter,adapter.identity,capture_approved=True,nonce=NONCE,
                                     deadline_seconds=.25,clock=lambda:observed[0]).run()
            self.assertFalse(result.complete)
            self.assertFalse(adapter.quarantined)
        finally:
            while adapter.owner_pending:await asyncio.sleep(.002)
            self.assertTrue(await adapter.close())

class CooperativeStopTests(unittest.IsolatedAsyncioTestCase):
    def collector(self,transport,callback,**kwargs):
        return q.Collector(transport,transport.identity,capture_approved=True,nonce=NONCE,
                           stop_requested=callback,**kwargs)
    async def test_stop_before_arm_has_no_transport_effects(self):
        transport=FakeTransport(start())
        result=await self.collector(transport,lambda:True).run()
        self.assertEqual(transport.writes,[]);self.assertEqual(transport.reads,0)
        self.assertEqual(result.host_error,5);self.assertFalse(result.complete)
        self.assertEqual(result.report()['host_stop_requested'],1)
        self.assertEqual(result.report()['host_stop_sent'],0)

    async def test_stop_during_healthy_read_keeps_pcm_then_observes_terminal(self):
        requested=[False]
        class Stopping(FakeTransport):
            async def read(self,maximum):
                self.reads+=1
                if self.reads==1:
                    requested[0]=True
                    await asyncio.sleep(.02) # Ctrl+C latch never cancels this healthy read.
                    return start()+pcm(0)
                return terminal(1)
        transport=Stopping(b'')
        result=await self.collector(transport,lambda:requested[0]).run()
        self.assertEqual([wire[5] for wire in transport.writes],[1,3])
        self.assertEqual(transport.reads,2);self.assertEqual(result.frames,1)
        self.assertTrue(result.terminal_received);self.assertEqual(result.terminal_fields[2],1)
        self.assertEqual(result.host_error,5);self.assertFalse(result.complete)
        self.assertEqual(result.report()['host_stop_sent'],1)

    async def test_stop_after_arm_completion_has_no_heartbeat_and_only_one_stop(self):
        requested=[False]
        class ArmStop(FakeTransport):
            async def write(self,data):
                self.writes.append(data)
                if data[5]==1:requested[0]=True
                return len(data)
        transport=ArmStop(start()+terminal(0))
        result=await self.collector(transport,lambda:requested[0]).run()
        self.assertEqual([wire[5] for wire in transport.writes],[1,3])
        self.assertTrue(result.terminal_received);self.assertFalse(result.complete)

    async def test_missing_terminal_stays_within_original_deadline_no_heartbeat(self):
        requested=[False]
        class Empty(FakeTransport):
            async def write(self,data):
                self.writes.append(data);requested[0]=True;return len(data)
            async def read(self,maximum):self.reads+=1;await asyncio.sleep(.02);return None
        transport=Empty(b'');loop=asyncio.get_running_loop();before=loop.time()
        result=await self.collector(transport,lambda:requested[0],deadline_seconds=.25).run()
        self.assertLess(loop.time()-before,.35);self.assertFalse(result.terminal_received)
        self.assertEqual([wire[5] for wire in transport.writes],[1,3])

    async def test_invalid_or_raising_callback_is_fixed_failure_and_no_arm(self):
        def broken():raise RuntimeError('private local details')
        for callback in (lambda:1,broken):
            transport=FakeTransport(b'')
            result=await self.collector(transport,callback).run()
            self.assertEqual(result.host_error,6);self.assertEqual(transport.writes,[])
            self.assertNotIn('private',q.numeric_json(result.report()))

    async def test_partial_stop_write_is_not_retried_or_claimed_delivered(self):
        requested=[False]
        class Partial(FakeTransport):
            async def write(self,data):
                self.writes.append(data);requested[0]=True
                return len(data)-(1 if data[5]==3 else 0)
        transport=Partial(b'')
        result=await self.collector(transport,lambda:requested[0]).run()
        self.assertEqual([wire[5] for wire in transport.writes],[1,3])
        self.assertEqual(result.report()['host_stop_sent'],0);self.assertEqual(transport.reads,0)

    async def test_late_terminal_is_not_admitted_after_original_deadline(self):
        requested=[False];now=[0.]
        class Late(FakeTransport):
            async def write(self,data):self.writes.append(data);requested[0]=True;return len(data)
            async def read(self,maximum):now[0]=46.;return start()+terminal(0)
        transport=Late(b'')
        result=await self.collector(transport,lambda:requested[0],clock=lambda:now[0]).run()
        self.assertFalse(result.terminal_received);self.assertEqual([x[5] for x in transport.writes],[1,3])

    async def test_stop_arriving_during_terminal_acceptance_never_reports_complete(self):
        from unittest.mock import patch
        requested=[False]
        valid=start()+b''.join(pcm(i) for i in range(1000))+terminal(1000,result=0,source_complete=1)
        transport=FakeTransport(valid);original=q.Capture.accept
        def signal_during_accept(capture,item):
            original(capture,item)
            if item.kind==18:requested[0]=True
        with patch.object(q.Capture,'accept',signal_during_accept):
            result=await self.collector(transport,lambda:requested[0]).run()
        self.assertTrue(result.terminal_received);self.assertFalse(result.complete)
        self.assertEqual(result.host_error,5);self.assertTrue(result.host_stop_requested)
        self.assertEqual([wire[5] for wire in transport.writes],[1])

    async def test_final_stop_poll_cannot_erase_callback_failure_code(self):
        calls=iter((1,True));transport=FakeTransport(b'')
        result=await self.collector(transport,lambda:next(calls,True)).run()
        self.assertEqual(result.host_error,6);self.assertTrue(result.host_stop_requested)
        self.assertEqual(transport.writes,[])

    async def test_corrupt_terminal_during_stop_observation_is_protocol_failure(self):
        requested=[False]
        class Corrupt(FakeTransport):
            async def write(self,data):self.writes.append(data);requested[0]=True;return len(data)
        data=bytearray(start()+terminal(0));data[-1]^=1
        transport=Corrupt(bytes(data))
        result=await self.collector(transport,lambda:requested[0]).run()
        self.assertEqual(result.host_error,1);self.assertFalse(result.terminal_received)
        self.assertTrue(result.host_stop_requested);self.assertTrue(result.host_stop_sent)

    async def test_corrupt_read_with_simultaneous_stop_keeps_protocol_failure(self):
        requested=[False]
        class Corrupt(FakeTransport):
            async def read(self,maximum):requested[0]=True;return await super().read(maximum)
        data=bytearray(start());data[-1]^=1
        transport=Corrupt(bytes(data))
        result=await self.collector(transport,lambda:requested[0]).run()
        self.assertEqual(result.host_error,1);self.assertFalse(result.terminal_received)
        self.assertTrue(result.host_stop_requested)
