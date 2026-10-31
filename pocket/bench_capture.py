"""Finite offline Pocket bench records. No import-time I/O or provider access.

Collection is an explicit device operation for a separately approved bench.
It never flashes, enumerates ports, selects a device, or starts sound by itself:
ARM still requires the firmware's fresh physical confirmation. Opening a serial
port may cause platform-specific control-line glitches; no reset-free promise.
Raw paired microphone data stays in a new private local directory by default.
"""
import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import zlib

_HEADER = struct.Struct('<4sBBHIIIII')
_LIMIT = 1_200_000
_PREFIX = 16_384
_MAX = (1 << 32) - 1
_FIELDS = frozenset(('phase','profile','code','rate','channels','reference','captured',
                    'source_samples','restore_ok','actual_acoustic_validation',
                    'max_capture_frames','peak','volume'))
_ERRORS = frozenset(('stopped','clock_regression','arm_expired','unexpected_command',
    'invalid_command','usb_write_failed','status_failed','restore_failed','start_failed',
    'runtime_cap','startup_cap','capture_failed','capture_discontinuity','submit_failed','source_incomplete'))

class BenchError(ValueError):
    pass

@dataclass(frozen=True)
class Record:
    kind: int
    epoch: int
    sequence: int
    sample_offset: int
    payload: bytes

class RecordParser:
    """Bounded incremental parser; only pre-record boot noise can be skipped."""
    def __init__(self):
        self.buffer=bytearray();self.total=self.skipped=0;self.started=False
    def feed(self,data):
        if not isinstance(data,bytes):raise BenchError('invalid_record_bytes')
        self.total+=len(data)
        if self.total>_LIMIT:raise BenchError('capture_byte_cap')
        self.buffer.extend(data);out=[]
        while True:
            if not self.started:
                at=self.buffer.find(b'HPB1')
                if at<0:
                    discard=max(0,len(self.buffer)-3)
                    self.skipped+=discard;del self.buffer[:discard]
                    if self.skipped>_PREFIX:raise BenchError('bootstrap_byte_cap')
                    return out
                self.skipped+=at
                if self.skipped>_PREFIX:raise BenchError('bootstrap_byte_cap')
                del self.buffer[:at];self.started=True
            if len(self.buffer)<28:return out
            magic,kind,flags,reserved,epoch,seq,offset,size,crc=_HEADER.unpack_from(self.buffer)
            if (magic!=b'HPB1' or kind not in (1,2) or flags or reserved or not epoch or
                    seq==_MAX or not 1<=size<=1024 or (kind==2 and size!=960) or
                    (kind==1 and offset) or (kind==2 and offset>_MAX-240)):
                raise BenchError('invalid_record_header')
            if len(self.buffer)<28+size:return out
            prefix=bytes(self.buffer[:24]);payload=bytes(self.buffer[28:28+size])
            if zlib.crc32(payload,zlib.crc32(prefix))!=crc:raise BenchError('record_crc_mismatch')
            out.append(Record(kind,epoch,seq,offset,payload));del self.buffer[:28+size]
    def finish(self):
        if not self.started or self.buffer:raise BenchError('incomplete_record_stream')

def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise BenchError('duplicate_status_key')
        result[key]=value
    return result

def _constant(_):raise BenchError('invalid_status_number')

def _status(payload):
    try:
        text=payload.decode('ascii')
        value=json.loads(text,object_pairs_hook=_object,parse_constant=_constant)
    except (UnicodeError,json.JSONDecodeError,RecursionError):
        raise BenchError('invalid_status_json') from None
    if not isinstance(value,dict) or set(value)!=_FIELDS:raise BenchError('invalid_status_schema')
    if (type(value['rate']) is not int or value['rate']!=24000 or
            type(value['channels']) is not int or value['channels']!=2 or
            value['reference']!='es8311_dac_right' or value['actual_acoustic_validation'] is not False or
            type(value['restore_ok']) is not bool or
            any(type(value[key]) is not int or value[key]<0 for key in
                ('captured','source_samples','max_capture_frames','peak','volume')) or
            value['profile'] not in ('none','silent','tone') or
            value['phase'] not in ('armed','running','done','error') or
            not isinstance(value['code'],str)):
        raise BenchError('invalid_status_values')
    profile=value['profile'];limits=(0,0,0) if profile=='none' else (1000,0,0) if profile=='silent' else (500,512,5)
    if tuple(value[k] for k in ('max_capture_frames','peak','volume'))!=limits:
        raise BenchError('invalid_status_limits')
    phase=value['phase'];code=value['code']
    if (phase=='error' and code not in _ERRORS) or (phase!='error' and code!={'armed':'armed','running':'running','done':'complete'}[phase]):
        raise BenchError('invalid_terminal_code')
    if (value['captured']>limits[0] or value['source_samples']>limits[0]*240 or
            value['source_samples']%240 or (profile!='tone' and value['source_samples']!=0)):
        raise BenchError('invalid_status_counter')
    return value

class BenchValidator:
    def __init__(self,profile):
        if profile not in ('silent','tone'):raise BenchError('invalid_profile')
        self.profile=profile;self.epoch=None;self.sequence=0;self.captured=0
        self.phase=None;self.terminal=None;self.source_samples=0
        self._sums=[0,0];self._squares=[0,0];self._peaks=[0,0];self._clipped=[0,0]
    def accept(self,record):
        if (self.terminal is not None or record.sequence!=self.sequence or
                (self.epoch is not None and record.epoch!=self.epoch)):
            raise BenchError('record_discontinuity')
        if record.kind==2:
            if self.phase!='running' or record.sample_offset!=self.captured*240:
                raise BenchError('capture_discontinuity')
            if self.captured>=(1000 if self.profile=='silent' else 500):raise BenchError('capture_frame_cap')
            values=struct.unpack('<480h',record.payload)
            for lane in (0,1):
                samples=values[lane::2];self._sums[lane]+=sum(samples)
                self._squares[lane]+=sum(x*x for x in samples)
                self._peaks[lane]=max(self._peaks[lane],max(abs(x) for x in samples))
                self._clipped[lane]+=sum(x in (-32768,32767) for x in samples)
            self.captured+=1
        else:
            value=_status(record.payload);phase=value['phase'];profile=value['profile']
            if profile!=self.profile and not (self.phase is None and phase=='error' and profile=='none'):
                raise BenchError('profile_changed')
            if value['captured']!=self.captured or value['source_samples']<self.source_samples:
                raise BenchError('status_counter_mismatch')
            if phase=='armed' and self.phase is not None:raise BenchError('repeated_arm')
            if phase=='running' and self.phase!='armed':raise BenchError('unexpected_running')
            if phase in ('armed','running') and (self.captured or value['source_samples'] or value['restore_ok']):
                raise BenchError('invalid_start_status')
            if phase=='done':
                target=1000 if self.profile=='silent' else 500
                if self.phase!='running' or self.captured!=target or not value['restore_ok']:
                    raise BenchError('premature_success')
                if self.profile=='tone' and value['source_samples']!=target*240:raise BenchError('incomplete_source')
                self.terminal=value
            elif phase=='error':self.terminal=value
            self.phase=phase;self.source_samples=value['source_samples']
        self.epoch=record.epoch;self.sequence+=1
    def finish(self):
        if self.terminal is None:raise BenchError('missing_terminal_status')
        count=self.captured*240
        lanes=[{'samples':count,'peak':self._peaks[i],
                'rms':math.sqrt(self._squares[i]/count) if count else 0,
                'mean':self._sums[i]/count if count else 0,'clipped_samples':self._clipped[i]} for i in (0,1)]
        return {'profile':self.profile,'epoch':self.epoch,'capture_frames':self.captured,
                'capture_samples':count,'source_samples':self.source_samples,
                'complete':self.terminal['phase']=='done','terminal_code':self.terminal['code'],
                'restore_ok':self.terminal['restore_ok'],'actual_acoustic_validation':False,
                'microphone':lanes[0],'reference':lanes[1],
                'scope':'digital record validation only; no lane, clock, SPL or AEC acceptance'}

def inspect_capture(path,profile):
    parser=RecordParser();validator=BenchValidator(profile)
    with open(path,'rb') as stream:
        while data:=stream.read(4096):
            for record in parser.feed(data):validator.accept(record)
    parser.finish();return validator.finish()

def _private_file(path):
    return os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb')

def collect(*,port,profile,output_dir,serial_factory=None):
    """Explicit local hardware operation. Tests inject a fake serial endpoint.

    Fixed 30-second host deadline; no enumerate/reset/flash/retry/reconnect. Any
    failure sends STOP once if possible, preserves a failed local report and
    never declares the board safe or cleanup complete without its terminal.
    """
    if not isinstance(port,str) or not port or len(port)>512 or any(c in port for c in ('\x00','\n','\r')) or '://' in port:
        raise BenchError('explicit_local_port_required')
    validator=BenchValidator(profile)
    if serial_factory is None:
        try:from serial import Serial
        except ImportError:raise BenchError('pyserial_required_for_explicit_collection') from None
        serial_factory=Serial
    output_dir=Path(output_dir)
    try:output_dir.mkdir(mode=0o700)
    except OSError:raise BenchError('new_private_output_directory_required') from None
    serial=None;report={'complete':False,'actual_acoustic_validation':False,'hardware_state':'unknown'}
    error=None;parser=RecordParser()
    try:
        serial=serial_factory();serial.port=port;serial.baudrate=115200
        serial.timeout=0.05;serial.write_timeout=0.1
        # Configure before open. Some OS/drivers can still pulse these lines.
        serial.dtr=False;serial.rts=False;serial.open()
        command=('ARM '+profile.upper()+'\n').encode('ascii')
        if serial.write(command)!=len(command):raise BenchError('partial_arm_write')
        deadline=time.monotonic()+30
        with _private_file(output_dir/'capture.hpb') as sink:
            while validator.terminal is None:
                if time.monotonic()>=deadline:raise BenchError('host_capture_timeout')
                data=serial.read(4096)
                if not isinstance(data,bytes):raise BenchError('invalid_serial_result')
                if data:
                    if parser.total+len(data)>_LIMIT:raise BenchError('capture_byte_cap')
                    sink.write(data)
                    for record in parser.feed(data):validator.accept(record)
            parser.finish();report=validator.finish()
    except (BenchError,OSError,ValueError) as exc:
        error=exc if isinstance(exc,BenchError) else BenchError('local_collection_failed')
        report['error']=str(error)
        if serial is not None:
            try:serial.write(b'STOP\n')
            except Exception:pass
    except BaseException:
        report['error']='interrupted_hardware_state_unknown'
        if serial is not None:
            try:serial.write(b'STOP\n')
            except Exception:pass
        raise
    finally:
        if serial is not None:
            try:serial.close()
            except Exception:
                report={'complete':False,'actual_acoustic_validation':False,'error':'serial_close_failed','hardware_state':'unknown'}
                if error is None:error=BenchError('serial_close_failed')
        with _private_file(output_dir/'report.json') as sink:
            sink.write((json.dumps(report,indent=2)+'\n').encode('ascii'))
    if error is not None:raise error
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='mode',required=True)
    inspect=commands.add_parser('inspect',help='offline only: validate a saved capture')
    inspect.add_argument('--input',required=True);inspect.add_argument('--profile',choices=('silent','tone'),required=True)
    capture=commands.add_parser('collect',help='explicit separately approved USB bench operation')
    capture.add_argument('--port',required=True);capture.add_argument('--profile',choices=('silent','tone'),required=True)
    capture.add_argument('--output-dir',required=True)
    capture.add_argument('--approved-device-test',action='store_true')
    capture.add_argument('--record-private-microphone',action='store_true')
    capture.add_argument('--allow-low-level-tone',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.mode=='inspect':report=inspect_capture(args.input,args.profile)
        else:
            if not args.approved_device_test or not args.record_private_microphone or (args.profile=='tone' and not args.allow_low_level_tone):
                raise BenchError('explicit_device_recording_and_tone_approval_flags_required')
            print('Opening only the named port. Press and release physical OK within 10 seconds after arming.\nAny active-run button press stops. USB opening may reset some systems. Raw microphone data stays local.',file=sys.stderr)
            report=collect(port=args.port,profile=args.profile,output_dir=args.output_dir)
        print(json.dumps(report,indent=2));return 0 if report['complete'] else 1
    except (BenchError,OSError) as exc:
        print('Bench failed: '+str(exc)+'; hardware state is not confirmed safe.',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
