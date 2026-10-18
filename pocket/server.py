"""Private bridge candidate with authenticated, bounded HTTP/TLS routes."""
import argparse
import hashlib
import hmac
import json
import re
import socket
import ssl
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, parse_qsl
from .schema import decode, ProtocolError, MAX_FRAME, identifier
from .store import Store, Conflict, NotFound, InvalidTransition
from .service import Service, FakeHermes
from .chat import ChatService
from .audio import AudioError
from .voice_jobs import VoiceJobs
from .linux_voice import decode_speech_pcm, decode_speech_to_file
from .result_pages import ResultPages, ReplyError
from .reply_speech import ReplySpools, ReplySpeech, reply_capabilities
from .transport import unique
from .usage import UsageCache, PROVIDERS
from .runtime_usage import HermesRuntimeUsage
from .chunk_timing import NumericTimingLog
from .transport_diagnostic import FileArmedTransport, ARM_FILENAME


def _json_bytes(value,*,compact=False):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,
                      separators=(',',':') if compact else None).encode()


def _task_view_response(task):
    """Bound only the display preview; keep task identity and stored text intact."""
    view=dict(task)
    text=view.get('result')
    if text is None:
        view['result']=None;view['truncated']=False
        if len(_json_bytes(view))<=8000:return 200,view
        return 413,{'error':'task preview metadata exceeds device limit'}
    prefix=text.encode()[:3000].decode('utf-8',errors='ignore')
    view['result']=''.join(c if ord(c)>=32 or c in '\n\r\t' else ' ' for c in prefix)
    view['truncated']=view['result']!=text
    if len(_json_bytes(view))<=8000:return 200,view
    prefix=view['result'];view['result']='';view['truncated']=True
    if len(_json_bytes(view))>8000:
        return 413,{'error':'task preview metadata exceeds device limit'}
    low,high=0,len(prefix)
    while low<high:
        middle=(low+high+1)//2;view['result']=prefix[:middle]
        if len(_json_bytes(view))<=8000:low=middle
        else:high=middle-1
    view['result']=prefix[:low]
    return 200,view


def _reply_query(query, allowed, required=()):
    if re.search(r'%(?![0-9a-fA-F]{2})',query):raise ReplyError(400,'invalid_request')
    try:pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,encoding='ascii',errors='strict') if query else []
    except (ValueError,UnicodeError):raise ReplyError(400,'invalid_request') from None
    values={}
    for key,value in pairs:
        if key not in allowed or key in values or not value:raise ReplyError(400,'invalid_request')
        values[key]=value
    if not set(required)<=values.keys():raise ReplyError(400,'invalid_request')
    return values


def _reply_body(raw):
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise ReplyError(400,'invalid_request') from None
    if not isinstance(value,dict):raise ReplyError(400,'invalid_request')
    return value


class _ServerOwner:
    """Unwind partial startup and retain recovery leases through voice reap."""
    def __init__(self):
        self.lock=threading.Lock()
        self.spools=None;self.voices={};self.server=None;self.timing=None
        self.closed=False

    def close(self):
        with self.lock:
            if self.closed:return
            if self.server is not None:ThreadingHTTPServer.server_close(self.server)
            # V2 provider/decoder work must finish before closing an adapter's
            # resident STT. Keep leases until every legacy voice has also reaped.
            if self.spools is not None:self.spools.drain()
            voice_error=None
            for voice in self.voices.values():
                try:voice.close()
                except BaseException as error:
                    if voice_error is None:voice_error=error
            try:
                if self.timing is not None:self.timing.close()
            finally:
                # A failed voice shutdown cannot honestly release ownership to
                # another server that would recover its still-active journal.
                if voice_error is None and self.spools is not None:self.spools.close()
            if voice_error is not None:raise voice_error
            self.closed=True


def make_server(service,tokens,*,host='127.0.0.1',port=0,speech=None,tls=None,decoder=decode_speech_pcm,usage_cache=None,transport_diagnostic=None,runtime_usage=None,reply_decoder=decode_speech_to_file,reply_spool_root=None):
    if host!='127.0.0.1' and (not tls or host in ('0.0.0.0','::')):raise ValueError('LAN ingress requires TLS and a specific interface address')
    if not tokens or any(not isinstance(k,str) or not k for k in tokens):raise ValueError('authentication required')
    tokens=dict(tokens)
    for device in tokens.values():identifier(device)
    context=None
    if tls:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls['certificate'],tls['private_key'])
    usage_cache=UsageCache() if usage_cache is None else usage_cache
    # Multi-target mode has no implicit default or mutable global selection.
    targets=dict(service) if isinstance(service,dict) else {'default':service}
    legacy=not isinstance(service,dict)
    if not targets:raise ValueError('at least one target required')
    identities=set()
    for name,svc in targets.items():
        identifier(name)
        filename=svc.store.db.execute('PRAGMA database_list').fetchone()[2]
        identity=filename or id(svc.store)
        if identity in identities:raise ValueError('each target requires a separate database')
        identities.add(identity)
    if runtime_usage is not None and not isinstance(runtime_usage,dict):
        raise ValueError('explicit runtime usage mapping required')
    runtime_usage={} if runtime_usage is None else dict(runtime_usage)
    for name,source in runtime_usage.items():
        if (name not in targets or not isinstance(source,HermesRuntimeUsage) or
                not source.matches_store(targets[name].store)):
            raise ValueError('runtime usage requires its own target database')
    if transport_diagnostic is None:
        transport_diagnostic=FileArmedTransport(Path(__file__).resolve().parent.parent/ARM_FILENAME,set(tokens.values()))
    owner=_ServerOwner()
    try:
        return _make_owned_server(targets,tokens,legacy=legacy,host=host,port=port,
            speech=speech,context=context,decoder=decoder,usage_cache=usage_cache,
            transport_diagnostic=transport_diagnostic,runtime_usage=runtime_usage,
            reply_decoder=reply_decoder,reply_spool_root=reply_spool_root,owner=owner)
    except BaseException:
        owner.close()
        raise


def _make_owned_server(targets,tokens,*,legacy,host,port,speech,context,decoder,
        usage_cache,transport_diagnostic,runtime_usage,reply_decoder,reply_spool_root,owner):
    chats={name:ChatService(svc) for name,svc in targets.items()}
    # Lease before VoiceJobs' legacy recovery, so a second server cannot reset
    # another instance's active markers or remove its PCM.
    reply_spools=owner.spools=ReplySpools({name:svc.store for name,svc in targets.items()},root=reply_spool_root)
    voices=owner.voices
    for name,chat in chats.items():voices[name]=VoiceJobs(chat,(speech or {}).get(name),decode=decoder)
    pages={name:ResultPages(name,svc.store) for name,svc in targets.items()}
    replies={name:ReplySpeech(name,svc.store,(speech or {}).get(name),reply_spools,decode=reply_decoder) for name,svc in targets.items()}
    timing_log=None
    class Handler(BaseHTTPRequestHandler):
        protocol_version="HTTP/1.1"
        disable_nagle_algorithm=True # Avoid delayed-ACK stalls between headers and body.
        def log_message(self,*args):pass # No paths, headers or content in logs.
        def setup(self):
            super().setup();self.connection.settimeout(3)
            if isinstance(self.connection,ssl.SSLSocket):self.connection.do_handshake()
        def parse_request(self):
            started=time.perf_counter_ns()
            try:return super().parse_request()
            finally:self.header_parse_us=(time.perf_counter_ns()-started)//1000
        def respond(self,code,value,*,compact=False):
            body=_json_bytes(value,compact=compact)
            self.response_code=code;self.response_bytes=len(body)
            started=time.perf_counter_ns()
            try:
                self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
                self.close_connection=not (200<=code<300 and getattr(self,'body_consumed',False))
                self.send_header('Connection','close' if self.close_connection else 'keep-alive');self.end_headers()
            finally:self.header_write_us=(time.perf_counter_ns()-started)//1000
            started=time.perf_counter_ns()
            try:self.wfile.write(body)
            finally:self.body_write_us=(time.perf_counter_ns()-started)//1000
        def dispatch(self):
            started=time.perf_counter_ns();utc_us=time.time_ns()//1000
            self.chunk_tag=None;self.chunk_sequence=-1;self.voice_timing=[-1,-1]
            self.body_read_us=-1;self.header_write_us=-1;self.body_write_us=-1
            self.response_code=0;self.response_bytes=0
            self.body_consumed=False
            length=0
            try:
                auth=self.headers.get('Authorization','');device=None
                for token,name in tokens.items():
                    if hmac.compare_digest(auth.encode(),('Bearer '+token).encode()):device=name
                if device is None:return self.respond(401,{'error':'unauthorized'})
                if urlsplit(self.path).path=='/diagnostics/transport':
                    if self.path!='/diagnostics/transport':return self.respond(404,{'error':'not found'})
                    # Negative peer TCP port distinguishes synthetic rows from capture hashes.
                    self.chunk_tag=-int(self.client_address[1])
                    def read_synthetic(count):
                        nonlocal length
                        length=count;read_started=time.perf_counter_ns()
                        value=bytearray()
                        try:
                            while len(value)<count:
                                remaining=transport_diagnostic.remaining_seconds()
                                if remaining<=0:raise TimeoutError('synthetic deadline expired')
                                self.connection.settimeout(max(.001,min(3,remaining)))
                                part=self.rfile.read1(count-len(value))
                                if not part:break
                                value.extend(part)
                            self.body_consumed=len(value)==count
                            return bytes(value)
                        except OSError as exc:
                            exc.transport_received_bytes=len(value)
                            raise
                        finally:
                            self.connection.settimeout(3)
                            self.body_read_us=(time.perf_counter_ns()-read_started)//1000
                    code,value=transport_diagnostic.dispatch(device,self.command,self.path,
                        self.headers.get_all('Content-Length',[]),self.headers.get('Transfer-Encoding'),read_synthetic)
                    if code==200:
                        if self.command=='GET':self.body_consumed=True
                        else:self.chunk_sequence=value['sequence']
                    return self.respond(code,value)
                if self.headers.get('Transfer-Encoding'):return self.respond(400,{'error':'unsupported transfer encoding'})
                lengths=self.headers.get_all('Content-Length',[])
                if len(lengths)>1:return self.respond(400,{'error':'duplicate content length'})
                try:length=int(lengths[0]) if lengths else 0
                except ValueError:return self.respond(400,{'error':'invalid length'})
                if length<0:return self.respond(400,{'error':'invalid length'})
                if length>MAX_FRAME:return self.respond(413,{'error':'frame too large'})
                read_started=time.perf_counter_ns()
                try:raw=self.rfile.read(length) if length else b''
                finally:self.body_read_us=(time.perf_counter_ns()-read_started)//1000
                if len(raw)!=length:return self.respond(400,{'error':'incomplete body'})
                self.body_consumed=True
                parsed=urlsplit(self.path);parts=parsed.path.strip('/').split('/')
                if self.command=='GET' and parsed.path=='/health':
                    return self.respond(200,{'bridge':'ready','backend':'fake' if all(isinstance(s.backend,FakeHermes) for s in targets.values()) else 'hermes','production_ready':False})
                if self.command=='GET' and parsed.path=='/targets':
                    return self.respond(200,{'targets':list(targets),'default_target':'default' if legacy else None})
                route=parsed.path
                if len(parts)>=2 and parts[0]=='targets':
                    target=identifier(parts[1])
                    if target not in targets:return self.respond(404,{'error':'unknown target'})
                    selected=targets[target]
                    parts=parts[2:];route='/'+('/'.join(parts))
                elif legacy:target='default';selected=targets[target]
                else:return self.respond(400,{'error':'explicit target required'})
                if self.command=='GET' and route=='/status':
                    try:
                        selected.backend.check() if hasattr(selected.backend,'check') else None
                        available=True
                    except Exception:available=False
                    return self.respond(200,{'target':target,'backend':'available' if available else 'unavailable','voice':voices[target].capabilities(),'voice_runtime':voices[target].runtime_metadata(),'reply_delivery':reply_capabilities(voices[target].capabilities()['tts'])})
                if self.command=='GET' and route=='/models':
                    return self.respond(200,selected.model_options())
                if len(parts)>=2 and parts[0]=='audio':
                    capture_id=identifier(parts[1]);voice=voices[target]
                    if len(parts)==2 and self.command=='GET':return self.respond(200,voice.status(device,capture_id))
                    if self.command=='POST':
                        if len(parts)==3 and parts[2]=='chunk':
                            self.chunk_tag=int.from_bytes(hashlib.sha256(capture_id.encode()).digest()[:4],'big')
                        data=json.loads(raw,object_pairs_hook=unique)
                        if not isinstance(data,dict):raise ProtocolError('invalid audio request')
                        if len(parts)==2 and set(data)=={'chat_id'}:return self.respond(201,voice.begin(device,target,capture_id,identifier(data['chat_id'])))
                        if len(parts)==2 and set(data)=={'version','chat_id','model_choice'}:
                            if type(data['version']) is not int or data['version']!=2:raise ProtocolError('invalid voice model selection version')
                            voice.chat.service.validate_model_choice(data['model_choice'])
                            return self.respond(201,voice.begin(device,target,capture_id,identifier(data['chat_id']),model_choice=data['model_choice']))
                        if len(parts)==3 and parts[2]=='chunk' and set(data)=={'sequence','data'}:
                            sequence=data['sequence']
                            self.chunk_sequence=sequence if type(sequence) is int and 0<=sequence<=1000000 else -1
                            return self.respond(200,voice.chunk(device,capture_id,sequence,data['data'],timing=self.voice_timing))
                        if raw==b'{}' and len(parts)==3 and parts[2]=='finish':return self.respond(202,voice.finish(device,capture_id))
                        if raw==b'{}' and len(parts)==3 and parts[2]=='abort':return self.respond(200,voice.abort(device,capture_id))
                        raise ProtocolError('unsupported audio request')
                if len(parts)==3 and parts[0]=='chats' and parts[2]=='messages':
                    chat_id=identifier(parts[1])
                    if self.command=='POST':return self.respond(202,chats[target].submit(device,chat_id,decode(raw)))
                    if self.command=='GET':return self.respond(200,chats[target].transcript(device,chat_id))
                if self.command=='GET' and route=='/usage':
                    now=time.time()
                    rows=[usage_cache.report(target,p,now) for p in PROVIDERS]
                    if target in runtime_usage:
                        local=runtime_usage[target].report(device)
                        for row in rows:row['local_usage']=local[row['provider']]
                        if len(_json_bytes(rows,compact=True))>8000:
                            return self.respond(413,{'error':'usage report exceeds device limit'})
                    return self.respond(200,rows,compact=target in runtime_usage)
                if self.command=='POST' and route=='/tasks':
                    return self.respond(202,selected.submit(device,decode(raw)))
                if self.command=='GET' and route=='/tasks':
                    with selected.store.lock:
                        rows=list(selected.store.db.execute('SELECT request_id,state,seq FROM tasks WHERE device=? ORDER BY rowid DESC LIMIT 12',(device,)))
                    return self.respond(200,[dict(r) for r in rows])
                if len(parts)>=2 and parts[0]=='tasks':
                    rid=identifier(parts[1])
                    if parts[2:]==['result','pages']:
                        selected.store.get(device,rid)
                        if self.command!='GET' or raw or self.headers.get('Range'):raise ReplyError(400,'invalid_request')
                        query=_reply_query(parsed.query,{'cursor'})
                        return self.respond(200,pages[target].get(device,rid,query.get('cursor')),compact=True)
                    if len(parts)>=4 and parts[2:4]==['speech','v2']:
                        selected.store.get(device,rid) # Before job/generation metadata.
                        if self.headers.get('Range'):raise ReplyError(400,'invalid_request')
                        reply=replies[target]
                        if len(parts)==4:
                            if self.command=='POST':
                                _reply_query(parsed.query,set())
                                data=_reply_body(raw)
                                if not {'request_id'}<=data.keys() or not data.keys()<={'request_id','result_revision'}:raise ReplyError(400,'invalid_request')
                                if 'result_revision' in data and data['result_revision'] is None:raise ReplyError(400,'invalid_request')
                                return self.respond(202,reply.create(device,rid,data['request_id'],data.get('result_revision')),compact=True)
                            if self.command=='GET':
                                if raw:raise ReplyError(400,'invalid_request')
                                query=_reply_query(parsed.query,{'generation'},{'generation'})
                                return self.respond(200,reply.status(device,rid,query['generation']),compact=True)
                        if len(parts)==5 and parts[4]=='cancel' and self.command=='POST':
                            _reply_query(parsed.query,set())
                            data=_reply_body(raw)
                            if set(data)!={'generation'}:raise ReplyError(400,'invalid_request')
                            code,value=reply.cancel(device,rid,data['generation'])
                            return self.respond(code,value,compact=True)
                        if len(parts)==8 and parts[4]=='segments' and parts[6]=='chunks' and self.command=='GET':
                            if raw or not re.fullmatch(r'[0-9]+',parts[5]) or not re.fullmatch(r'[0-9]+',parts[7]):raise ReplyError(400,'invalid_request')
                            query=_reply_query(parsed.query,{'generation'},{'generation'})
                            return self.respond(200,reply.chunk(device,rid,query['generation'],int(parts[5]),int(parts[7])),compact=True)
                        raise ReplyError(400,'invalid_request')
                    if len(parts)==3 and parts[2]=='view' and self.command=='GET':
                        task=selected.refresh(device,rid)
                        if hasattr(selected.backend,'handoff') and task['run_id'] and task['state'] in ('awaiting_approval','unknown'):
                            task['handoff']=selected.backend.handoff(task['run_id'])
                        code,view=_task_view_response(task)
                        return self.respond(code,view)
                    if len(parts)==3 and parts[2]=='speech':
                        if self.command=='POST' and raw==b'{}':return self.respond(202,voices[target].speak(device,rid))
                        if self.command=='GET':return self.respond(200,voices[target].speech_status(device,rid))
                    if len(parts)==5 and parts[2:4]==['speech','chunks'] and self.command=='GET':
                        return self.respond(200,voices[target].playback_chunk(device,rid,int(parts[4])))
                    if len(parts)==2 and self.command=='GET':return self.respond(200,selected.refresh(device,rid))
                    if len(parts)==3 and parts[2]=='events' and self.command=='GET':
                        q=parse_qs(parsed.query);values=q.get('after',['0'])
                        if len(values)!=1:raise ProtocolError('invalid cursor')
                        after=int(values[0])
                        return self.respond(200,selected.store.events(device,rid,after))
                    if len(parts)==3 and parts[2]=='stop' and self.command=='POST':
                        if raw!=b'{}':raise ProtocolError('stop requires empty object')
                        return self.respond(200,selected.stop(device,rid))
                    if len(parts)==3 and parts[2]=='abandon' and self.command=='POST':
                        if raw!=b'{}':raise ProtocolError('abandon requires empty object')
                        return self.respond(200,selected.abandon(device,rid))
                return self.respond(404,{'error':'not found'})
            except ReplyError as exc:self.respond(exc.status,{'error':exc.code},compact=True)
            except (ProtocolError,ValueError) as exc:
                code=409 if isinstance(exc,(Conflict,InvalidTransition)) else 400
                self.respond(code,{'error':'conflict' if code==409 else 'invalid request'})
            except NotFound:self.respond(404,{'error':'not found'})
            except (TimeoutError,socket.timeout):self.respond(504,{'error':'request timed out'})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.respond(502,{'error':'backend unavailable'})
            finally:
                if self.chunk_tag is not None:
                    timing_log.emit((utc_us,self.chunk_tag,self.chunk_sequence,self.response_code,
                        length,self.response_bytes,self.header_parse_us,self.body_read_us,
                        *self.voice_timing,self.header_write_us,self.body_write_us,
                        (time.perf_counter_ns()-started)//1000))
        do_GET=dispatch
        do_POST=dispatch
    class BoundedServer(ThreadingHTTPServer):
        daemon_threads=True
        def __init__(self,*args):
            self.slots=threading.BoundedSemaphore(8)
            super().__init__(*args)
        def process_request(self,request,address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request);return
            try:super().process_request(request,address)
            except BaseException:self.slots.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:self.slots.release()
        def server_close(self):
            super().server_close()
            owner.close()
    server=owner.server=BoundedServer((host,port),Handler)
    if context is not None:
        server.socket=context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
    timing_log=owner.timing=NumericTimingLog()
    return server


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--demo',action='store_true',required=True)
    p.add_argument('--two-targets',action='store_true',help='simulate separate Windows and Linux instances');p.add_argument('--db',default='pocket-demo.sqlite');p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    stores={name:Store(a.db+'.'+name) for name in ('windows','linux')} if a.two_targets else {'default':Store(a.db)}
    for store in stores.values():store.recover()
    services={name:Service(store,FakeHermes()) for name,store in stores.items()}
    svc=services if a.two_targets else services['default']
    server=make_server(svc,{'pocket-demo-only':'demo-device'},port=a.port)
    print(f'MOCK ONLY: http://127.0.0.1:{server.server_port}; public demo token: pocket-demo-only',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        for store in stores.values():store.close()
if __name__=='__main__':main()
