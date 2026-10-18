"""A local, one-shot arm gate for the approved existing-TLS synthetic contract.

The non-secret arm file is absent by default and must name this exact PID.
No listener, credentials, payload storage, provider or production capture here.
"""
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from .synthetic_payload_v2 import body_for, PATH, SIZES, REQUESTS, TOTAL_BYTES
from .transport import unique


ARM_FILENAME='.transport-011-arm.json'


class _Expired(Exception):
    pass


class FileArmedTransport:
    def __init__(self,control,devices,*,clock=time.monotonic_ns,pid=None):
        self.control=Path(control)
        self._device=next(iter(devices)) if len(devices)==1 else None
        self._clock=clock
        self._pid=os.getpid() if pid is None else pid
        self._lock=threading.Lock()
        self._armed=None
        self._closed=None
        self._attempts=0
        self._received_bytes=0
        self._accepted=0

    def _read_arm(self):
        try:
            descriptor=os.open(self.control,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
            with os.fdopen(descriptor,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):return None
                if os.name=='posix' and (info.st_uid!=os.getuid() or info.st_mode&0o077):return None
                data=stream.read(513)
            if len(data)>512:return None
            value=json.loads(data,object_pairs_hook=unique)
            keys={'schema','pid','start_ns','deadline_ns'}
            if not isinstance(value,dict) or set(value)!=keys:return None
            if any(type(value[k]) is not int for k in keys):return None
            if value['schema']!=1 or value['pid']!=self._pid:return None
            if not 0<value['deadline_ns']-value['start_ns']<=120_000_000_000:return None
            if value['start_ns']>self._clock():return None
            return value['pid'],value['start_ns'],value['deadline_ns']
        except (OSError,ValueError,TypeError,RecursionError):return None

    def remaining_seconds(self):
        return max(0,(self._armed[2]-self._clock())/1_000_000_000) if self._armed else 0

    def dispatch(self,authenticated_device,method,path,lengths,transfer_encoding,read):
        if path!=PATH:return None
        with self._lock:
            arm=self._read_arm() if self._device is not None else None
            if arm is None or (self._armed is not None and arm!=self._armed):
                if self._armed is not None:self._closed=404
                return 404,{'error':404}
            if self._clock()>=arm[2]:
                self._closed=410
                code=404 if method=='GET' else 410
                return code,{'error':code}
            if authenticated_device!=self._device:return 403,{'error':403}
            if self._armed is None:
                self._armed=arm
            # GET is a metadata-only gate, independent of the POST attempt quota.
            if method=='GET':
                if self._closed is not None:return 404,{'error':404}
                if transfer_encoding is not None or lengths not in ([],['0'],()):
                    self._closed=410
                    return 400,{'error':400}
                remaining=(arm[2]-self._clock()+999_999)//1_000_000
                if remaining<=0:return 404,{'error':404}
                return 200,{'version':2,'ready':int(self._attempts==0 and self._closed is None),
                    'remaining_ms':remaining,'requests':REQUESTS,'bytes':TOTAL_BYTES}
            if self._closed is not None:return self._closed,{'error':self._closed}
            if self._attempts>=REQUESTS or self._received_bytes>=REQUESTS*max(SIZES):
                self._closed=429
                return 429,{'error':429}
            # Reservation and the bounded read share this route-only lock. Invalid
            # framing/short reads cannot escape the attempt quota under concurrency.
            self._attempts+=1
            try:
                result=self._post(method,lengths,transfer_encoding,read,arm[2])
            except _Expired:
                self._closed=410
                return 410,{'error':410}
            except BaseException:
                self._closed=410
                raise
            if result[0]!=200 or self._attempts>=REQUESTS:
                self._closed=429 if result[0]==200 else 410
            return result

    def _post(self,method,lengths,transfer_encoding,read,deadline):
        if method!='POST':return 405,{'error':405}
        if transfer_encoding is not None or len(lengths)!=1:return 400,{'error':400}
        if not isinstance(lengths[0],str) or not re.fullmatch(r'[0-9]{1,4}',lengths[0]):
            return 400,{'error':400}
        length=int(lengths[0])
        if length not in SIZES:return 413,{'error':413}
        try:body=read(length)
        except BaseException as exc:
            received=getattr(exc,'transport_received_bytes',0)
            if type(received) is int and 0<=received<=length:self._received_bytes+=received
            raise
        if isinstance(body,bytes):self._received_bytes+=len(body)
        if self._clock()>=deadline:raise _Expired()
        if not isinstance(body,bytes) or len(body)!=length:return 400,{'error':400}
        match=re.fullmatch(rb'\{"sequence":([0-8]),"padding":"P*"\}',body)
        if not match:return 400,{'error':400}
        sequence=int(match[1])
        if body!=body_for(sequence):return 400,{'error':400}
        if sequence!=self._accepted:return 409,{'error':409}
        self._accepted+=1
        return 200,{'sequence':sequence,'accepted':length}
