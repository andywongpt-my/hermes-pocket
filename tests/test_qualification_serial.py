"""Fake driver only: never import pyserial, enumerate or open an OS device."""
import asyncio
import threading
import unittest
try:
    from pocket.qualification_serial import ExplicitSerialTransport
except ImportError:
    ExplicitSerialTransport=None
from pocket.qualification import QualificationError


class FakeSerial:
    def __init__(self):
        self.events=[];self.is_open=False;self.output=b'abc'
    def open(self):
        self.events.append(('open',self.port,self.dtr,self.rts,self.timeout,self.write_timeout))
        self.is_open=True
    def read(self,maximum):self.events.append(('read',maximum));return self.output[:maximum]
    def write(self,data):self.events.append(('write',len(data)));return len(data)
    def close(self):self.events.append(('close',));self.is_open=False


class SerialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.assertIsNotNone(ExplicitSerialTransport,'explicit serial adapter missing')
    def adapter(self,driver=None,**changes):
        self.driver=driver or FakeSerial();self.constructed=0
        def factory():self.constructed+=1;return self.driver
        kwargs=dict(port='COM7',identity='operator-attested-board',open_approved=True,
                    reset_risk_acknowledged=True,recovery_confirmed=True,identity_verified=True,
                    platform_behavior_verified=True,
                    serial_factory=factory)
        kwargs.update(changes)
        return ExplicitSerialTransport(**kwargs)
    async def test_all_authority_and_port_checks_precede_factory(self):
        for key in ('open_approved','reset_risk_acknowledged','recovery_confirmed','identity_verified','platform_behavior_verified'):
            item=self.adapter(**{key:False})
            with self.assertRaises(QualificationError):await item.open()
            self.assertEqual(self.constructed,0)
        for port in ('','socket://private:9000','COM7\n','x'*513):
            with self.assertRaises(QualificationError):self.adapter(port=port)
    async def test_one_open_configures_false_lines_before_open_no_enumeration(self):
        item=self.adapter();await item.open()
        self.assertEqual(self.driver.events[0],('open','COM7',False,False,.02,.02))
        self.assertEqual(await item.read(2),b'ab')
        self.driver.output=b'';self.assertIsNone(await item.read(2))
        self.assertEqual(await item.write(b'HPQ1'),4)
        self.assertTrue(await item.close());self.assertFalse(item.owner_pending)
        with self.assertRaises(QualificationError):await item.open()
        self.assertEqual(self.constructed,1)
        self.assertEqual([x[0] for x in self.driver.events],['open','read','read','write','close'])
    async def test_invalid_io_does_not_reach_driver(self):
        item=self.adapter();await item.open()
        for count in (0,4097,True):
            with self.assertRaises(QualificationError):await item.read(count)
        for data in (b'',b'x'*1057,'private'):
            with self.assertRaises(QualificationError):await item.write(data)
        self.assertEqual(len(self.driver.events),1);await item.close()
    async def test_cancelled_hung_driver_retains_owner_and_refuses_overlap_close(self):
        entered=threading.Event();release=threading.Event()
        class Blocking(FakeSerial):
            def read(self,maximum):entered.set();release.wait();return b'private'
        item=self.adapter(Blocking());await item.open()
        task=asyncio.create_task(item.read(4096))
        try:
            while not entered.is_set():await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(item.owner_pending);self.assertTrue(item.quarantined)
            with self.assertRaises(QualificationError):await item.write(b'HPQ1')
            self.assertFalse(await item.close());self.assertTrue(self.driver.is_open)
        finally:release.set()
        while item.owner_pending:await asyncio.sleep(.001)
        self.assertTrue(await item.close());self.assertFalse(self.driver.is_open)
    async def test_driver_error_is_fixed_code_and_quarantines(self):
        class Failed(FakeSerial):
            def read(self,maximum):raise OSError('private port and microphone bytes')
        item=self.adapter(Failed());await item.open()
        with self.assertRaises(QualificationError) as error:await item.read(4096)
        self.assertEqual(str(error.exception),'serial_operation_failed')
        self.assertTrue(item.quarantined);self.assertTrue(await item.close())
    async def test_driver_overread_is_rejected_without_returning_private_data(self):
        class Failed(FakeSerial):
            def read(self,maximum):return b'x'*4097
        item=self.adapter(Failed());await item.open()
        with self.assertRaises(QualificationError):await item.read(4096)
        self.assertTrue(item.quarantined);await item.close()

    async def test_close_does_not_race_completed_driver_before_coroutine_collects(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        class Paused(FakeSerial):
            def read(self,maximum):entered.set();release.wait();finished.set();return b'x'
        item=self.adapter(Paused());await item.open()
        task=asyncio.create_task(item.read(2))
        while not entered.is_set():await asyncio.sleep(.001)
        release.set()
        # Wait synchronously only for this fake's bounded completion to hold the
        # event loop before the read coroutine can collect its completed result.
        self.assertTrue(finished.wait(.1))
        self.assertFalse(await item.close())
        self.assertEqual(await task,b'x')
        self.assertTrue(await item.close())

    async def test_failed_close_is_not_retried_after_uncertain_outcome(self):
        class FailedClose(FakeSerial):
            def close(self):self.events.append(('close',));raise OSError('unknown closure')
        item=self.adapter(FailedClose());await item.open()
        self.assertFalse(await item.close());self.assertFalse(await item.close())
        self.assertEqual(self.driver.events.count(('close',)),1)
        self.assertTrue(item.quarantined)

    async def test_explicit_8n1_and_all_flow_controls_disabled_before_open(self):
        class Inspect(FakeSerial):
            def open(self):
                self.observed=tuple(getattr(self,key,None) for key in ('bytesize','parity','stopbits','xonxoff','rtscts','dsrdtr','inter_byte_timeout'))
                super().open()
        item=self.adapter(Inspect());await item.open()
        self.assertEqual(self.driver.observed,(8,'N',1,False,False,False,None))
        self.assertTrue(await item.close())

    async def test_failed_open_then_noop_close_never_certifies_cleanup(self):
        class FailedOpen(FakeSerial):
            def open(self):self.events.append(('open',));raise OSError('native handle cleanup unknown')
            def close(self):
                if self.is_open:super().close()
        item=self.adapter(FailedOpen())
        with self.assertRaises(QualificationError):await item.open()
        self.assertFalse(await item.close());self.assertFalse(await item.close())
        self.assertTrue(item.quarantined);self.assertFalse(item.owner_pending)
        self.assertTrue(item.open_failed_unknown)
        self.assertFalse(item.close_api_completed)
        self.assertEqual(self.driver.events,[('open',)])

    async def test_cancelled_open_that_later_fails_remains_unknown(self):
        entered=threading.Event();release=threading.Event()
        class FailedOpen(FakeSerial):
            def open(self):entered.set();release.wait();raise OSError('unknown')
        item=self.adapter(FailedOpen());task=asyncio.create_task(item.open())
        try:
            while not entered.is_set():await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertFalse(await item.close())
        finally:release.set()
        while item.owner_pending:await asyncio.sleep(.001)
        self.assertFalse(await item.close());self.assertTrue(item.open_failed_unknown)

    async def test_cancelled_open_that_later_succeeds_can_close_once(self):
        entered=threading.Event();release=threading.Event()
        class LateOpen(FakeSerial):
            def open(self):entered.set();release.wait();super().open()
        item=self.adapter(LateOpen());task=asyncio.create_task(item.open())
        try:
            while not entered.is_set():await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertFalse(await item.close())
        finally:release.set()
        while item.owner_pending:await asyncio.sleep(.001)
        self.assertTrue(await item.close());self.assertTrue(item.close_api_completed)
        self.assertFalse(item.open_failed_unknown)
        self.assertTrue(await item.close());self.assertEqual(self.driver.events.count(('close',)),1)

    async def test_explicit_probe_purpose_opens_without_asserting_platform_validation(self):
        item=self.adapter(platform_probe=True,platform_behavior_verified=False)
        await item.open()
        self.assertTrue(self.driver.is_open)
        self.assertTrue(await item.close())
        self.assertEqual([event[0] for event in self.driver.events],['open','close'])
        normal=self.adapter(platform_behavior_verified=False)
        with self.assertRaises(QualificationError):await normal.open()
        self.assertEqual(self.constructed,0)

    async def test_probe_never_reads_or_writes_even_when_open(self):
        item=self.adapter(platform_probe=True,platform_behavior_verified=False)
        await item.open()
        for action in (lambda:item.read(4096),lambda:item.write(b'HPQ1')):
            with self.assertRaisesRegex(QualificationError,'probe_transport_io_forbidden'):
                await action()
        self.assertEqual([event[0] for event in self.driver.events],['open'])
        self.assertTrue(await item.close())

    async def test_probe_keeps_other_authority_requirements_before_factory(self):
        for key in ('open_approved','reset_risk_acknowledged','recovery_confirmed','identity_verified'):
            item=self.adapter(platform_probe=True,platform_behavior_verified=False,**{key:False})
            with self.assertRaises(QualificationError):await item.open()
            self.assertEqual(self.constructed,0)
        for invalid in (1,'probe',None):
            with self.assertRaises(QualificationError):self.adapter(platform_probe=invalid)

    async def test_probe_failed_open_retains_unknown_ownership(self):
        class FailedOpen(FakeSerial):
            def open(self):raise OSError('unknown native cleanup')
        item=self.adapter(FailedOpen(),platform_probe=True,platform_behavior_verified=False)
        with self.assertRaises(QualificationError):await item.open()
        self.assertFalse(await item.close());self.assertTrue(item.open_failed_unknown)
        self.assertTrue(item.quarantined)
