"""Explicit paired-reference wire v2. DMA credit is NOT audible playout.

Each capture packet holds 240 interleaved microphone-left/DAC-reference-right
PCM16 pairs from one ES8311 RX clock. No TX/RX epoch interpolation is performed.
The authenticated device owns truthful lane configuration and continuity.
"""
import asyncio
from dataclasses import dataclass
import struct
from .gpt_live import LiveError, LiveOutcome

_HEADER=struct.Struct('!2sBBIII')
_MAX=(1<<32)-1

@dataclass(frozen=True)
class PairedLiveOutcome(LiveOutcome):
    actual_playout: str = 'unknown'

@dataclass(frozen=True)
class AudioFrame:
    kind: int
    sequence: int
    sample_offset: int
    dma_consumed: int
    pcm: bytes

def _samples(frame):
    return len(frame.pcm)//(4 if frame.kind==1 else 2)

def _validate(frame):
    if (type(frame.kind) is not int or frame.kind not in (1,2) or
        any(type(n) is not int or not 0<=n<=_MAX for n in
            (frame.sequence,frame.sample_offset,frame.dma_consumed)) or
        not isinstance(frame.pcm,bytes) or not 2<=len(frame.pcm)<=960 or len(frame.pcm)%2 or
        (frame.kind==1 and len(frame.pcm)!=960) or
        (frame.kind==2 and frame.dma_consumed!=0) or
        frame.sequence==_MAX or frame.sample_offset>_MAX-_samples(frame)):
        raise LiveError('invalid_paired_device_frame')

def encode_frame(frame):
    _validate(frame)
    return _HEADER.pack(b'PL',2,frame.kind,frame.sequence,frame.sample_offset,frame.dma_consumed)+frame.pcm

def decode_frame(data):
    if not isinstance(data,bytes) or not 18<=len(data)<=976:
        raise LiveError('invalid_paired_device_frame')
    magic,version,kind,seq,offset,credit=_HEADER.unpack_from(data)
    if magic!=b'PL' or version!=2:raise LiveError('invalid_paired_device_frame')
    frame=AudioFrame(kind,seq,offset,credit,data[16:]);_validate(frame);return frame

class StreamState:
    def __init__(self,kind):
        if type(kind) is not int or kind not in (1,2):raise LiveError('invalid_paired_device_frame')
        self.kind=kind
        self.sequence=self.samples=self.dma_consumed=0
    def accept(self,frame,*,speaker_samples_sent=0):
        _validate(frame)
        if (frame.kind!=self.kind or frame.sequence!=self.sequence or frame.sample_offset!=self.samples or
            type(speaker_samples_sent) is not int or not 0<=speaker_samples_sent<=_MAX or
            (self.kind==1 and not self.dma_consumed<=frame.dma_consumed<=speaker_samples_sent)):
            raise LiveError('paired_device_discontinuity')
        self.sequence+=1;self.samples+=_samples(frame);self.dma_consumed=frame.dma_consumed

class PairedDeviceAudioBridge:
    def __init__(self,stream,echo,*,credit_timeout=1.0):
        if type(credit_timeout) not in (int,float) or not 0<credit_timeout<=2:
            raise LiveError('invalid_credit_timeout')
        self.credit_timeout=credit_timeout
        self._credit_changed=asyncio.Event()
        if not callable(getattr(echo,'capture_paired',None)):
            raise LiveError('paired_echo_processor_required')
        self.stream,self.echo=stream,echo
        self.microphone_queue=asyncio.Queue(maxsize=8)
        self.input_state,self.output_state=StreamState(1),StreamState(2)
        self._wire_lock=asyncio.Lock()
    @property
    def playback_pending(self):
        # Digital consumption cannot clear an actual-speaker-plausibly-pending flag.
        return self.output_state.samples>0
    async def receive(self,stop):
        from .live_device import _control
        while not stop.is_set():
            raw=await self.stream.recv()
            if raw is None:raise LiveError('device_connection_lost')
            if isinstance(raw,str):
                if _control(raw)!={'type':'stop'}:raise LiveError('invalid_device_control')
                stop.set();return
            frame=decode_frame(raw)
            async with self._wire_lock:
                previous=self.input_state.dma_consumed
                self.input_state.accept(frame,speaker_samples_sent=self.output_state.samples)
                if self.input_state.dma_consumed>previous:self._credit_changed.set()
            if self.microphone_queue.full():raise LiveError('device_capture_backpressure')
            self.microphone_queue.put_nowait(frame)
            await asyncio.sleep(0)
    async def microphone(self):
        await self.stream.send('{"type":"ready","version":2,"rate":24000,"reference":"es8311_dac_right"}')
        while True:
            frame=await self.microphone_queue.get()
            pcm=await self.echo.capture_paired(frame.pcm,frame.sample_offset,frame.dma_consumed)
            if not isinstance(pcm,bytes) or len(pcm)!=480:raise LiveError('invalid_echo_audio')
            yield pcm
    async def playback(self,pcm):
        if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>48000:
            raise LiveError('invalid_output_audio')
        for at in range(0,len(pcm),960):
            chunk=pcm[at:at+960]
            deadline=asyncio.get_running_loop().time()+self.credit_timeout
            while True:
                async with self._wire_lock:
                    if self.output_state.samples-self.input_state.dma_consumed+len(chunk)//2<=1920:
                        frame=AudioFrame(2,self.output_state.sequence,self.output_state.samples,0,chunk)
                        await self.stream.send(encode_frame(frame))
                        self.output_state.accept(frame)
                        break
                    self._credit_changed.clear()
                remaining=deadline-asyncio.get_running_loop().time()
                if remaining<=0:raise LiveError('device_playback_credit_timeout')
                try:
                    await asyncio.wait_for(self._credit_changed.wait(),remaining)
                except asyncio.TimeoutError:
                    raise LiveError('device_playback_credit_timeout') from None
            await asyncio.sleep(0)
