"""Server-side relay to Hermes dashboard speech routes.

Provider selection and secrets stay in Hermes. This code never reads voice-config,
changes STT settings or sends credentials to the handheld. The adapter is based
on pinned upstream route schemas; compatibility with an installed fork must be
confirmed during owner deployment. No requests run at import time.
"""
import base64
import binascii
import io
import json
import ssl
import urllib.request
import urllib.error
import wave
from dataclasses import dataclass
from urllib.parse import urlsplit
from .audio import AudioError
from .voice_errors import VoiceError, safe_timings
from .transport import NoRedirect, unique

MAX_PCM=960000
MAX_ENCODED=4*1024*1024

@dataclass(frozen=True)
class EncodedSpeech:
    data: bytes
    mime_type: str
    provider: str | None

class DashboardTransport:
    """Relay credentials configured by the owner at deployment, never discovered.

    HTTPS is required for remote dashboard access. Plain HTTP is accepted only
    for an explicit loopback origin when the bridge runs on the same Linux host.
    Explicit Host routing never changes the HTTPS connection hostname or TLS
    verification target. Redirects and automatic POST retries are disabled.
    """
    def __init__(self,origin,token,*,opener=None,auth_header='Authorization',host_header=None):
        parsed=urlsplit(origin)
        local=parsed.hostname in ('127.0.0.1','::1','localhost')
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or not (parsed.scheme=='https' or parsed.scheme=='http' and local):
            raise ValueError('verified HTTPS or explicit loopback origin required')
        if not isinstance(token,str) or not token or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('invalid dashboard credential')
        if auth_header not in ('Authorization','X-Hermes-Session-Token'):raise ValueError('unsupported dashboard authentication')
        if host_header is not None:
            if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','::1')):
                raise ValueError('explicit Host routing requires verified HTTPS or a loopback tunnel')
            if not isinstance(host_header,str) or not host_header or any(ord(c)<33 or ord(c)>126 for c in host_header):
                raise ValueError('invalid routed Host')
            routed=urlsplit('//'+host_header)
            if not routed.hostname or routed.username or routed.password or routed.path or routed.query or routed.fragment or routed.netloc!=host_header:
                raise ValueError('invalid routed Host')
            if routed.port is not None and not 1<=routed.port<=65535:raise ValueError('invalid routed Host port')
        self.host_header=host_header
        self.origin=origin.rstrip('/');self._token=token;self.auth_header=auth_header
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def __call__(self,path,body):
        if path not in ('/api/audio/transcribe','/api/audio/speak'):raise AudioError('unsupported speech route')
        raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        if len(raw)>1400000:raise AudioError('speech request too large')
        credential='Bearer '+self._token if self.auth_header=='Authorization' else self._token
        headers={self.auth_header:credential,'Content-Type':'application/json','Accept':'application/json'}
        if self.host_header is not None:headers['Host']=self.host_header
        request=urllib.request.Request(self.origin+path,data=raw,method='POST',headers=headers)
        try:
            with self.opener.open(request,timeout=60) as response:
                if response.status!=200:raise AudioError('unexpected speech status')
                data=response.read(MAX_ENCODED+1)
            if len(data)>MAX_ENCODED:raise AudioError('speech response too large')
            result=json.loads(data.decode(),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(result,dict):raise AudioError('invalid speech response')
            return result
        except urllib.error.HTTPError as error:
            status=error.code;error.close()
            if status in (401,403):raise AudioError('dashboard authentication required') from None
            raise AudioError('speech service returned HTTP '+str(status)) from None
        except (OSError,ValueError,UnicodeError,RecursionError):raise AudioError('speech service unavailable') from None

class LinuxSpeechAdapter:
    def __init__(self,transport,*,stt_enabled=False):
        self.transport=transport
        # Explicit deployment choice. Never assume Linux's disabled STT is enabled.
        if type(stt_enabled) is not bool:raise ValueError('explicit STT state required')
        self.stt_enabled=stt_enabled
    def transcribe(self,pcm):
        return self.transcribe_job(pcm)['transcript']
    def transcribe_job(self,pcm,*,cancel=None):
        if not self.stt_enabled:raise AudioError('speech recognition disabled on Linux')
        if not isinstance(pcm,bytes) or not pcm or len(pcm)>MAX_PCM or len(pcm)%2:raise AudioError('invalid PCM recording')
        memory=io.BytesIO()
        with wave.open(memory,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(pcm)
        body={'data_url':'data:audio/wav;base64,'+base64.b64encode(memory.getvalue()).decode('ascii'),'mime_type':'audio/wav'}
        job=getattr(self.transport,'transcribe_job',None)
        result=job(body,cancel=cancel) if callable(job) else self.transport('/api/audio/transcribe',body)
        text=result.get('transcript')
        if result.get('ok',True if callable(job) else None) is not True or not isinstance(text,str) or len(text.encode())>4096:raise AudioError('transcription unavailable')
        return {'transcript':text.strip(),'timings_us':safe_timings(result.get('timings_us'))}
    def runtime_metadata(self):
        metadata=getattr(self.transport,'runtime_metadata',None)
        return metadata() if callable(metadata) else {'stt':{'resident':False,'ready':False}}
    def close(self):
        close=getattr(self.transport,'close',None)
        if callable(close):close()
    def speak(self,text):
        if isinstance(text,str) and len(text.encode())>4096:raise VoiceError('tts_text_too_long')
        if not isinstance(text,str) or not text.strip() or len(text.encode())>4096:raise AudioError('invalid speech text')
        result=self.transport('/api/audio/speak',{'text':text})
        return self._encoded(result)
    def speak_job(self,text,*,cancel,timeout):
        """Only the owned TTS invocation is cancelled; resident STT stays warm."""
        if not isinstance(text,str) or not text.strip() or len(text.encode())>4096:
            raise VoiceError('tts_text_too_long')
        job=getattr(self.transport,'speak_job',None)
        if not callable(job):raise VoiceError('tts_synthesis_failed')
        return self._encoded(job({'text':text},cancel=cancel,timeout=timeout),max_data_bytes=3145728)
    def _encoded(self,result,*,max_data_bytes=None):
        value=result.get('data_url');mime=result.get('mime_type')
        if result.get('ok') is not True or not isinstance(value,str) or mime not in ('audio/mpeg','audio/wav','audio/ogg','audio/flac'):raise AudioError('speech synthesis unavailable')
        prefix='data:'+mime+';base64,'
        limit=MAX_ENCODED if max_data_bytes is None else len(prefix)+4*((max_data_bytes+2)//3)
        if not value.startswith(prefix) or len(value)>limit:raise AudioError('invalid encoded speech')
        try:data=base64.b64decode(value[len(prefix):],validate=True)
        except (binascii.Error,ValueError):raise AudioError('invalid encoded speech') from None
        if not data:raise AudioError('empty speech')
        if max_data_bytes is not None and len(data)>max_data_bytes:raise AudioError('invalid encoded speech')
        provider=result.get('provider')
        if provider is not None and (not isinstance(provider,str) or len(provider)>128):raise AudioError('invalid speech provider')
        # Returns tagged encoded audio. Never play MP3 bytes as raw PCM.
        return EncodedSpeech(data,mime,provider)

def decode_speech_pcm(speech,*,ffmpeg='ffmpeg'):
    """Normalize relay audio on the server, never on the small handheld.

    Requires an owner-installed ffmpeg executable. Uses pipes, no raw-audio files,
    no shell, no network protocols, bounded duration/output and a wall-clock limit.
    This function does not install ffmpeg or make a provider request.
    """
    import subprocess
    if not isinstance(speech,EncodedSpeech) or not speech.data or len(speech.data)>3*1024*1024:raise AudioError('invalid encoded speech')
    formats={'audio/mpeg':'mp3','audio/wav':'wav','audio/ogg':'ogg','audio/flac':'flac'}
    if speech.mime_type not in formats:raise AudioError('unsupported speech encoding')
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-protocol_whitelist','pipe',
             '-f',formats[speech.mime_type],'-i','pipe:0','-vn','-ac','1','-ar','16000',
             '-t','30.001','-fs','960002','-f','s16le','pipe:1']
    try:
        output=subprocess.run(command,input=speech.data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              timeout=20,check=True).stdout
    except subprocess.TimeoutExpired:raise VoiceError('tts_decode_timeout') from None
    except (OSError,subprocess.SubprocessError):raise VoiceError('tts_decode_failed') from None
    if len(output)>=MAX_PCM:raise VoiceError('tts_playback_limit')
    if not output or len(output)%2:raise VoiceError('tts_decode_failed')
    return output


def decode_speech_to_file(speech,output,*,cancel,timeout=20,ffmpeg='ffmpeg'):
    """Decode one continuous stream to private disk with bounded pipe buffers.

    No duration clipping: an output beyond 240 seconds is an explicit failure.
    The decoder owns its process group, and returns only after it has reaped.
    """
    import os
    import queue
    import signal
    import subprocess
    import threading
    import time
    maximum=7680000
    if (not isinstance(speech,EncodedSpeech) or not isinstance(speech.data,bytes)
            or not speech.data or len(speech.data)>3145728):
        raise VoiceError('tts_decode_failed')
    formats={'audio/mpeg':'mp3','audio/wav':'wav','audio/ogg':'ogg','audio/flac':'flac'}
    if speech.mime_type not in formats:raise VoiceError('tts_decode_failed')
    if isinstance(timeout,bool) or not isinstance(timeout,(float,int)) or not 0<timeout<=20:
        raise VoiceError('tts_decode_timeout')
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-protocol_whitelist','pipe',
        '-f',formats[speech.mime_type],'-i','pipe:0','-vn','-ac','1','-ar','16000','-f','s16le','pipe:1']
    proc=None;threads=[];stopped=threading.Event();events=queue.Queue(maxsize=2)
    deadline=time.monotonic()+timeout
    def feed():
        try:
            remaining=memoryview(speech.data)
            while remaining and not stopped.is_set():
                count=os.write(proc.stdin.fileno(),remaining[:65536])
                if not count:break
                remaining=remaining[count:]
        except (OSError,ValueError):pass
        finally:
            try:proc.stdin.close()
            except OSError:pass
    def read():
        try:
            while not stopped.is_set():
                part=os.read(proc.stdout.fileno(),65536)
                while not stopped.is_set():
                    try:events.put(part,timeout=.05);break
                    except queue.Full:pass
                if not part:return
        except (OSError,ValueError):pass
    def check():
        if cancel.is_set():raise VoiceError('tts_shutdown')
        if time.monotonic()>=deadline:raise VoiceError('tts_decode_timeout')
    size=0
    try:
        check()
        proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,
            **({'start_new_session':True} if os.name=='posix' else {}))
        threads=[threading.Thread(target=feed,daemon=True),threading.Thread(target=read,daemon=True)]
        for thread in threads:thread.start()
        while True:
            check()
            try:part=events.get(timeout=min(.05,max(.001,deadline-time.monotonic())))
            except queue.Empty:continue
            if not part:break
            if size+len(part)>maximum:
                # At most one bounded block of overshoot can exist on disk; no
                # partial output is ever published as a ready manifest.
                raise VoiceError('tts_playback_limit')
            remaining=memoryview(part)
            while remaining:
                count=output.write(remaining)
                if not count:raise OSError(28,'reply spool write failed')
                remaining=remaining[count:];size+=count
        while proc.poll() is None:
            check()
            try:proc.wait(timeout=min(.05,max(.001,deadline-time.monotonic())))
            except subprocess.TimeoutExpired:pass
        check()
        if proc.returncode or not size or size%2:raise VoiceError('tts_decode_failed')
        output.flush()
        return size
    except VoiceError:raise
    except OSError:
        # Preserve disk-full errno for the session's safe reason mapping.
        raise
    except Exception:raise VoiceError('tts_decode_failed') from None
    finally:
        stopped.set()
        if proc is not None:
            if proc.poll() is None:
                try:
                    if os.name=='posix':os.killpg(proc.pid,signal.SIGKILL)
                    else:proc.kill()
                except ProcessLookupError:pass
            proc.wait()  # A terminal acknowledgement must mean reaped.
            for thread in threads:thread.join(timeout=.5)
            for pipe in (proc.stdin,proc.stdout):
                try:pipe.close()
                except (OSError,ValueError):pass
