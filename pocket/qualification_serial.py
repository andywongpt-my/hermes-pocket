"""Explicit future local serial binding; importing this module opens nothing.

All cloud tests inject a fake. This is NOT Windows/platform validation or a
reset-free guarantee: opening/closing a driver may pulse DTR/RTS despite configured
states. Identity is the operator's independently verified exact-port attestation,
not automatic USB discovery. Device action needs separate approval and recovery.
A hung driver cannot be preempted; retain its owner and never overlap IO/close.
"""
import asyncio
import threading
from .qualification import QualificationError, _integer


class ExplicitSerialTransport:
    def __init__(self, *, port, identity, open_approved=False,
                 reset_risk_acknowledged=False, recovery_confirmed=False,
                 identity_verified=False, platform_behavior_verified=False,
                 platform_probe=False, serial_factory=None):
        if (type(platform_probe) is not bool or not isinstance(port,str) or not 1 <= len(port) <= 512 or
                '://' in port or any(ord(c)<32 or ord(c)==127 for c in port) or
                not isinstance(identity,str) or not 1 <= len(identity) <= 128 or
                any(ord(c)<33 or ord(c)>126 for c in identity) or
                serial_factory is not None and not callable(serial_factory)):
            raise QualificationError('explicit_local_port_identity_required')
        self._port=port;self.identity=identity;self._factory=serial_factory
        self._platform_probe=platform_probe
        self._authority=(open_approved,reset_risk_acknowledged,recovery_confirmed,
                         identity_verified,platform_behavior_verified)
        self._serial=None;self._pending=None
        self._used=self._opened=self._closed=self.quarantined=False
        self._awaiting=self._close_attempted=False
        self._open_failed=False

    @property
    def open_failed_unknown(self):
        """An open exception cannot prove pyserial released every native handle."""
        return self._open_failed

    @property
    def close_api_completed(self):
        """Public close returned: NOT native-handle release or device safety proof."""
        return self._closed

    @property
    def owner_pending(self):
        return self._pending is not None and (self._awaiting or not self._pending[0].is_set())

    def _retire_finished(self):
        if self._pending is not None and self._pending[0].is_set():
            self._pending[1].clear()  # Drop bounded bytes; not a secure-erasure claim.
            self._pending=None

    async def _operation(self, function, timeout):
        if self._pending is not None:
            raise QualificationError('serial_owner_retained')
        done=threading.Event();result=[]
        def run():
            try:result.extend((True,function()))
            except BaseException:result.extend((False,None))  # Never copy driver exception text.
            finally:done.set()
        self._pending=(done,result)
        self._awaiting=True
        try:
            threading.Thread(target=run,daemon=True,name='pocket-qualification-io').start()
        except Exception:
            self._pending=None;self._awaiting=False;self.quarantined=True
            raise QualificationError('serial_operation_failed') from None
        loop=asyncio.get_running_loop();deadline=loop.time()+timeout
        try:
            while not done.is_set():
                remaining=deadline-loop.time()
                if remaining <= 0:
                    self.quarantined=True
                    raise QualificationError('serial_operation_pending')
                await asyncio.sleep(min(.002,remaining))
        except asyncio.CancelledError:
            self.quarantined=True
            raise
        finally:
            self._awaiting=False
        success,value=result
        result.clear();self._pending=None
        if not success:
            self.quarantined=True
            raise QualificationError('serial_operation_failed')
        return value

    async def open(self):
        if self._used:
            raise QualificationError('serial_attempt_already_used')
        self._used=True
        if (not all(value is True for value in self._authority[:4]) or
                (not self._platform_probe and self._authority[4] is not True)):
            raise QualificationError('explicit_serial_authority_required')
        # Import only after all separate operator acknowledgements. No enumeration.
        factory=self._factory
        if factory is None:
            try:from serial import Serial
            except ImportError:
                raise QualificationError('pyserial_required_for_explicit_open') from None
            factory=Serial
        def configure_and_open():
            self._serial=factory()  # Unopened: never pass a port into the constructor.
            self._serial.port=self._port;self._serial.baudrate=115200
            self._serial.bytesize=8;self._serial.parity='N';self._serial.stopbits=1
            self._serial.xonxoff=False;self._serial.rtscts=False;self._serial.dsrdtr=False
            self._serial.inter_byte_timeout=None
            self._serial.timeout=.02;self._serial.write_timeout=.02
            self._serial.dtr=False;self._serial.rts=False
            try:self._serial.open()
            except BaseException:
                # Win32 open can suppress internal cleanup errors and discard
                # its handle while is_open remains false. Public close may then
                # do nothing. Never turn that uncertain outcome into success.
                self._open_failed=True
                raise
        await self._operation(configure_and_open,2.0)
        self._opened=True
        return self

    def _ready(self):
        if self._platform_probe:
            raise QualificationError('probe_transport_io_forbidden')
        if not self._opened or self._closed or self.quarantined or self._pending is not None:
            raise QualificationError('serial_owner_unavailable')

    async def read(self, maximum):
        self._ready()
        if not _integer(maximum,4096,1):
            raise QualificationError('invalid_serial_read')
        value=await self._operation(lambda:self._serial.read(maximum),.075)
        if not isinstance(value,bytes) or len(value)>maximum:
            self.quarantined=True
            raise QualificationError('invalid_serial_read_result')
        return value or None  # pyserial empty read is timeout, not framed EOF.

    async def write(self, data):
        self._ready()
        if not isinstance(data,bytes) or not 1 <= len(data) <= 1056:
            raise QualificationError('invalid_serial_write')
        value=await self._operation(lambda:self._serial.write(data),.075)
        if type(value) is not int or value != len(data):
            self.quarantined=True
            raise QualificationError('serial_write_not_fully_admitted')
        return value

    async def close(self):
        """Explicit API cleanup. True is API completion, never OS/device proof.

        If an operation is pending, close is not called. A later explicit close
        can be attempted after new evidence owner_pending is false; no auto retry.
        """
        if self.owner_pending:
            self.quarantined=True
            return False
        if self._open_failed:
            self._retire_finished();self.quarantined=True
            return False  # No bypass through pyserial private handles or retry.
        if self._close_attempted:
            # Observe a prior timed-out/cancelled close; never execute it twice.
            if self._pending is not None:
                success=self._pending[1][0]
                self._retire_finished()
                if success:
                    self._closed=True;self._opened=False;self._serial=None;self.quarantined=False
            return self._closed
        self._retire_finished()
        if self._serial is None or self._closed:
            return True
        self.quarantined=True
        self._close_attempted=True
        try:await self._operation(self._serial.close,.1)
        except QualificationError:return False
        self._closed=True;self._opened=False;self._serial=None;self.quarantined=False
        return True
