"""Bounded in-memory PCM lifecycle. Providers must be explicitly supplied.

PCM contract: mono signed 16-bit little-endian, 16 kHz. No raw-audio files.
This module is not an exposed HTTP upload endpoint or a production STT/TTS client.
"""
from .schema import identifier

class AudioError(ValueError):pass

class Capture:
    def __init__(self,target,request_id,*,max_seconds=30):
        self.target=identifier(target);self.request_id=identifier(request_id)
        if type(max_seconds) is not int or not 1<=max_seconds<=30:raise AudioError('invalid duration limit')
        self.limit=32000*max_seconds;self.buffer=bytearray();self.next_sequence=0;self.closed=False
    @property
    def size(self):return len(self.buffer)
    def abort(self):
        self.buffer.clear();self.closed=True
    def append(self,sequence,pcm):
        if self.closed:raise AudioError('capture closed')
        if type(sequence) is not int or sequence!=self.next_sequence or not isinstance(pcm,bytes) or not pcm or len(pcm)>4096 or len(pcm)%2 or self.size+len(pcm)>self.limit:
            self.abort();raise AudioError('invalid audio chunk')
        self.buffer.extend(pcm);self.next_sequence+=1
    def finish(self):
        if self.closed or not self.buffer:self.abort();raise AudioError('empty or closed capture')
        pcm=bytes(self.buffer);self.abort();return pcm

class AudioPipeline:
    def __init__(self,transcribe,submit_text,synthesize):
        self.transcribe=transcribe;self.submit_text=submit_text;self.synthesize=synthesize
    def submit(self,capture):
        pcm=capture.finish()
        try:text=self.transcribe(pcm)
        except Exception:raise AudioError('transcription unavailable') from None
        finally:del pcm
        if not isinstance(text,str) or not text.strip() or len(text.encode())>4096:raise AudioError('empty or oversized transcription')
        return self.submit_text(capture.target,capture.request_id,text.strip())
    def speak(self,text):
        if not isinstance(text,str) or not text.strip() or len(text.encode())>4096:raise AudioError('invalid speech text')
        try:pcm=self.synthesize(text)
        except Exception:raise AudioError('speech unavailable') from None
        if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>960000:raise AudioError('invalid speech audio')
        return pcm
