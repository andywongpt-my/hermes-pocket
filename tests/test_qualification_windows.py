"""CLI/orchestration with fake transports only. No serial module or OS port."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from pocket.qualification import encode, HEADER
from pocket.qualification_serial import ExplicitSerialTransport
try:
    from pocket import qualification_windows as w
except ImportError:
    w=None

class Driver:
    def __init__(self):self.events=[];self.data=bytearray();self.is_open=False
    def open(self):self.events.append('open');self.is_open=True
    def read(self,n):
        self.events.append('read');data=bytes(self.data[:n]);del self.data[:n];return data
    def write(self,b):
        kind=HEADER.unpack_from(b)[2];self.events.append(('write',kind))
        if kind==1:
            nonce=HEADER.unpack_from(b)[5]
            start=encode(16,1,nonce,0,0,struct.pack('<12I',1,24000,2,240,1000,0,0,0,0,2000,10250,0))
            body=b''.join(encode(17,1,nonce,i+1,i*240,struct.pack('<8I',1,i,(i+1)*10,0,0,0,0,0)+b'\0'*960) for i in range(1000))
            end=encode(18,1,nonce,1001,0,struct.pack('<12I',1,0,1,1000,0,0,0,10000,0,len(start)+len(body),1,0))
            self.data.extend(start+body+end+b'private resumed console')
        return len(b)
    def close(self):self.events.append('close');self.is_open=False

class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.assertIsNotNone(w,'Windows SILENT launcher missing')
    async def entered(self,event):
        async def wait():
            while not event.is_set():await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),1)
    def adapter(self,driver):
        return ExplicitSerialTransport(port='COM7',identity='board-1',open_approved=True,
            reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
            platform_behavior_verified=True,serial_factory=lambda:driver)
    async def test_real_protocol_complete_wipes_capture_and_closes_exactly_once(self):
        driver=Driver();session=w.SilentSession(self.adapter(driver),'board-1',w.StopLatch())
        report=await session.run()
        self.assertEqual(report['complete'],1);self.assertEqual(report['frames'],1000)
        self.assertEqual(report['capture_ram_wiped'],1);self.assertEqual(report['physical_acceptance'],0)
        self.assertEqual(report['host_close_completed'],1);self.assertEqual(driver.events.count('close'),1)
        self.assertNotIn('private',w.report_json(report));self.assertNotIn('board-1',w.report_json(report))
        with self.assertRaises(ValueError):await session.run()
    async def test_signal_before_run_does_not_open_or_arm(self):
        driver=Driver();stop=w.StopLatch();stop.request();stop.request()
        session=w.SilentSession(self.adapter(driver),'board-1',stop)
        report=await session.run();self.assertEqual(driver.events,[])
        self.assertEqual(report['stop_requested'],1);self.assertEqual(report['serial_open_completed'],0)
    async def test_stop_during_open_waits_without_cancelling_and_does_not_arm(self):
        entered=threading.Event();release=threading.Event()
        class Late(Driver):
            def open(self):entered.set();release.wait();super().open()
        driver=Late();stop=w.StopLatch();session=w.SilentSession(self.adapter(driver),'board-1',stop)
        task=asyncio.create_task(session.run())
        await self.entered(entered)
        stop.request();self.assertFalse(task.done());release.set();report=await task
        self.assertEqual(driver.events,['open','close']);self.assertEqual(report['stop_requested'],1)
    async def test_pending_close_retains_owner_and_later_observes_no_retry(self):
        entered=threading.Event();release=threading.Event()
        class Late(Driver):
            def close(self):self.events.append('close');entered.set();release.wait();self.is_open=False
        driver=Late();session=w.SilentSession(self.adapter(driver),'board-1',w.StopLatch())
        try:
            report=await session.run();self.assertTrue(entered.is_set())
            self.assertEqual(report['host_owner_pending'],1);self.assertEqual(report['host_close_completed'],0)
            self.assertFalse(await session.observe_cleanup());self.assertEqual(driver.events.count('close'),1)
        finally:release.set()
        while session.transport.owner_pending:await asyncio.sleep(.001)
        self.assertTrue(await session.observe_cleanup());self.assertEqual(driver.events.count('close'),1)
    async def test_unknown_close_is_not_retried_or_reported_safe(self):
        class Failed(Driver):
            def close(self):self.events.append('close');raise OSError('private native failure')
        driver=Failed();session=w.SilentSession(self.adapter(driver),'board-1',w.StopLatch())
        report=await session.run();self.assertEqual(report['host_cleanup_unknown'],1)
        self.assertFalse(await session.observe_cleanup());self.assertFalse(await session.observe_cleanup())
        self.assertEqual(driver.events.count('close'),1)
    async def test_broken_output_does_not_escape_retained_owner_guard(self):
        class Pending:
            def __init__(self):self.observed=0
            def report(self):return dict(launcher_version=1,serial_open_completed=1,
                host_close_completed=int(self.observed>=2),host_owner_pending=int(self.observed<2),
                host_cleanup_unknown=0,stop_requested=0,launcher_error=0,
                physical_acceptance=0,flash_authorized=0,raw_retained=0)
            async def run(self):return self.report()
            async def observe_cleanup(self):self.observed+=1;return self.observed>=2
        pending=Pending()
        with tempfile.TemporaryDirectory() as d:
            summary=Path(d)/'report.json'
            args=SimpleNamespace(port='COM7',identity='board-1',approve_open=True,
                acknowledge_reset_risk=True,confirm_recovery=True,verify_identity=True,
                verify_platform_behavior=True,summary=str(summary))
            with mock.patch.object(w,'SilentSession',return_value=pending),mock.patch('builtins.print',side_effect=BrokenPipeError):
                try:result=await w._run(args,w.StopLatch())
                except BrokenPipeError:self.fail('output error abandoned retained owner')
            self.assertEqual(result,2);self.assertEqual(pending.observed,2)
            self.assertEqual(json.loads(summary.read_text())['launcher_error'],3)
    async def test_probe_only_opens_holds_closes_and_never_collects(self):
        self.assertTrue(callable(getattr(w,'PlatformProbeSession',None)),'open-close probe missing')
        driver=Driver();adapter=ExplicitSerialTransport(port='COM7',identity='board-1',
            open_approved=True,reset_risk_acknowledged=True,recovery_confirmed=True,
            identity_verified=True,platform_probe=True,serial_factory=lambda:driver)
        session=w.PlatformProbeSession(adapter,'board-1',w.StopLatch())
        began=asyncio.get_running_loop().time();report=await session.run()
        self.assertLess(asyncio.get_running_loop().time()-began,2.5)
        self.assertEqual(driver.events,['open','close'])
        self.assertEqual(report['probe_only'],1);self.assertEqual(report['probe_api_completed'],1)
        self.assertEqual(report['platform_behavior_verified'],0);self.assertEqual(report['physical_acceptance'],0)
        self.assertNotIn('frames',report)
    async def test_probe_stop_shortens_hold_without_read_write_or_second_close(self):
        self.assertTrue(callable(getattr(w,'PlatformProbeSession',None)),'open-close probe missing')
        driver=Driver();stop=w.StopLatch()
        adapter=ExplicitSerialTransport(port='COM7',identity='board-1',open_approved=True,
            reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
            platform_probe=True,serial_factory=lambda:driver)
        session=w.PlatformProbeSession(adapter,'board-1',stop)
        task=asyncio.create_task(session.run());await asyncio.sleep(.02);stop.request()
        report=await asyncio.wait_for(task,1)
        self.assertEqual(driver.events,['open','close']);self.assertEqual(report['stop_requested'],1)
    async def test_external_task_cancellation_keeps_guard_owner(self):
        entered=threading.Event();release=threading.Event()
        class Late(Driver):
            def read(self,n):entered.set();release.wait();return b''
        driver=Late();session=w.SilentSession(self.adapter(driver),'board-1',w.StopLatch())
        task=asyncio.create_task(session.run())
        try:
            await self.entered(entered)
            task.cancel();report=await task
            self.assertEqual(report['host_owner_pending'],1);self.assertEqual(driver.events.count('close'),0)
        finally:release.set()
        while session.transport.owner_pending:await asyncio.sleep(.001)
        await session.observe_cleanup()

class BoundaryTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(w,'Windows SILENT launcher missing')
    def test_default_and_help_never_import_serial(self):
        for argv in ([],['--help']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out),mock.patch.dict('sys.modules',{'serial':None}):
                self.assertEqual(w.main(argv),0)
            self.assertNotIn('Traceback',out.getvalue())
    def test_unknown_options_and_missing_authority_are_fixed_and_have_no_adapter(self):
        for argv in (['run-silent','--token','PRIVATE-TOKEN'],['run-silent'],['--profile','AUDIO']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out),mock.patch.object(w,'ExplicitSerialTransport') as adapter:
                self.assertNotEqual(w.main(argv),0);adapter.assert_not_called()
            self.assertNotIn('PRIVATE-TOKEN',out.getvalue());self.assertNotIn('Traceback',out.getvalue())
    def run_args(self,summary):
        return ['run-silent','--candidate','unused.zip','--recovery','unused.bin',
            '--port','COM7','--identity','board-1','--summary',summary,
            '--approve-open','--approve-close','--acknowledge-reset-risk',
            '--confirm-recovery','--verify-identity','--verify-platform-behavior','--approve-capture']
    def test_unsupported_runtime_refuses_before_adapter(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stderr(io.StringIO()),mock.patch.object(w.pre,'preflight',return_value={'runtime_compatible':0}),mock.patch.object(w,'ExplicitSerialTransport') as adapter:
            self.assertEqual(w.main(self.run_args(str(Path(d)/'report.json'))),2)
            adapter.assert_not_called()
    def test_explicit_fake_cli_uses_silent_and_restores_signal_handlers(self):
        driver=Driver()
        adapter=ExplicitSerialTransport(port='COM7',identity='board-1',open_approved=True,
            reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
            platform_behavior_verified=True,serial_factory=lambda:driver)
        old=w.signal.getsignal(w.signal.SIGINT)
        with tempfile.TemporaryDirectory() as d:
            summary=Path(d)/'report.json'
            with contextlib.redirect_stdout(io.StringIO()),mock.patch.object(w.pre,'preflight',return_value={'runtime_compatible':1}),mock.patch.object(w,'ExplicitSerialTransport',return_value=adapter):
                self.assertEqual(w.main(self.run_args(str(summary))),0)
            report=json.loads(summary.read_text());self.assertEqual(report['profile'],1)
            self.assertEqual(report['complete'],1);self.assertEqual(report['raw_retained'],0)
        self.assertEqual(w.signal.getsignal(w.signal.SIGINT),old)
    def test_explicit_fake_probe_cli_needs_no_capture_or_platform_attestation(self):
        driver=Driver()
        adapter=ExplicitSerialTransport(port='COM7',identity='board-1',open_approved=True,
            reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
            platform_probe=True,serial_factory=lambda:driver)
        with tempfile.TemporaryDirectory() as d:
            summary=Path(d)/'probe.json';args=self.run_args(str(summary))
            args[0]='probe-open-close';args.remove('--verify-platform-behavior');args.remove('--approve-capture')
            with contextlib.redirect_stdout(io.StringIO()),mock.patch.object(w.pre,'preflight',return_value={'runtime_compatible':1}),mock.patch.object(w,'ExplicitSerialTransport',return_value=adapter) as constructor:
                self.assertEqual(w.main(args),0)
            self.assertTrue(constructor.call_args.kwargs['platform_probe'])
            self.assertFalse(constructor.call_args.kwargs['platform_behavior_verified'])
            self.assertEqual(driver.events,['open','close'])
            report=json.loads(summary.read_text());self.assertEqual(report['platform_behavior_verified'],0)
            self.assertEqual(report['probe_api_completed'],1);self.assertNotIn('frames',report)
    def test_numeric_report_rejects_unknown_fields_and_false_physical_acceptance(self):
        for report in ({'private':'text'},{'physical_acceptance':1},{'host_owner_pending':float('nan')}):
            with self.assertRaises(ValueError):w.report_json(report)
    def test_report_file_is_fresh_numeric_only(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'summary.json');w.write_report({'physical_acceptance':0,'host_close_completed':1},path)
            self.assertEqual(json.loads(Path(path).read_text())['host_close_completed'],1)
            with self.assertRaises(ValueError):w.write_report({'physical_acceptance':0},path)
if __name__=='__main__':unittest.main()
