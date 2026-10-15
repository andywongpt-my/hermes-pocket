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
        if not self.stt_enabled:raise AudioError('speech recognition disabled on Linux')
        if not isinstance(pcm,bytes) or not pcm or len(pcm)>MAX_PCM or len(pcm)%2:raise AudioError('invalid PCM recording')
        memory=io.BytesIO()
        with wave.open(memory,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(pcm)
        body={'data_url':'data:audio/wav;base64,'+base64.b64encode(memory.getvalue()).decode('ascii'),'mime_type':'audio/wav'}
        result=self.transport('/api/audio/transcribe',body)
        text=result.get('transcript')
        if result.get('ok') is not True or not isinstance(text,str) or len(text.encode())>4096:raise AudioError('transcription unavailable')
        return text.strip()
    def speak(self,text):
        if not isinstance(text,str) or not text.strip() or len(text.encode())>4096:raise AudioError('invalid speech text')
        result=self.transport('/api/audio/speak',{'text':text})
        value=result.get('data_url');mime=result.get('mime_type')
        if result.get('ok') is not True or not isinstance(value,str) or mime not in ('audio/mpeg','audio/wav','audio/ogg','audio/flac'):raise AudioError('speech synthesis unavailable')
        prefix='data:'+mime+';base64,'
        if not value.startswith(prefix) or len(value)>MAX_ENCODED:raise AudioError('invalid encoded speech')
        try:data=base64.b64decode(value[len(prefix):],validate=True)
        except (binascii.Error,ValueError):raise AudioError('invalid encoded speech') from None
        if not data:raise AudioError('empty speech')
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
    except (OSError,subprocess.SubprocessError):raise AudioError('server audio decoder unavailable') from None
    if not output or len(output)%2 or len(output)>=MAX_PCM:raise AudioError('speech exceeds playback limit')
    return output
