"""Owner-configured Pocket bridge. No credential discovery or generation.

Startup performs only liveness/capability probes, never a model request.
LAN listening requires an explicit interface and certificate/key files.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
from .schema import identifier
from .transport import JSONTransport, unique
from .hermes import HermesAdapter
from .desktop import DesktopAdapter
from .websocket_rpc import DesktopRPC
from .linux_voice import DashboardTransport, LinuxSpeechAdapter
from .hermes_speech import HermesSpeechTransport
from .service import Service
from .store import Store
from .server import make_server

def secret(variable):
    if not isinstance(variable,str) or not variable.startswith('POCKET_'):
        raise ValueError('explicit POCKET_ environment variable required')
    value=os.environ.get(variable,'')
    if not value or len(value)>8192 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ValueError('missing or invalid owner credential')
    return value

def speech_adapter(voice,target):
    if not isinstance(voice,dict) or type(voice.get('stt_enabled')) is not bool:
        raise ValueError('explicit speech configuration required')
    if voice.get('kind')=='hermes-command':
        required={'kind','python','hermes_root','hermes_home','pins_file','stt_enabled'}
        if set(voice)!=required or target!='linux':
            raise ValueError('approved Linux Hermes command speech configuration required')
        transport=HermesSpeechTransport(voice['python'],voice['hermes_root'],voice['hermes_home'],voice['pins_file'])
        transport.check() # Metadata only: no speech provider or model request.
        return LinuxSpeechAdapter(transport,stt_enabled=voice['stt_enabled'])
    if not {'origin','token_env','stt_enabled'}<=set(voice) or set(voice)-{'origin','token_env','stt_enabled','host_header'}:
        raise ValueError('invalid speech configuration')
    return LinuxSpeechAdapter(DashboardTransport(voice['origin'],secret(voice['token_env']),host_header=voice.get('host_header')),stt_enabled=voice['stt_enabled'])

def build(config):
    required={'targets','devices','port'}
    if not isinstance(config,dict) or not required<=set(config) or set(config)-required-{'host','tls'}:
        raise ValueError('invalid bridge configuration')
    port=config['port'];host=config.get('host','127.0.0.1');tls=config.get('tls')
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('invalid port')
    address=ipaddress.ip_address(host)
    if host!='127.0.0.1':
        if not address.is_private or address.is_unspecified or address.is_multicast or not tls:
            raise ValueError('explicit private LAN interface and TLS required')
    if tls is not None and (not isinstance(tls,dict) or set(tls)!={'certificate','private_key'}):
        raise ValueError('TLS certificate/key paths required')
    definitions=config['targets'];devices=config['devices']
    if not isinstance(definitions,dict) or not 1<=len(definitions)<=2:raise ValueError('one or two targets required')
    if not isinstance(devices,dict) or not 1<=len(devices)<=8:raise ValueError('one to eight owner-paired devices required')
    tokens={};services={};resources=[];speech={}
    try:
        for device,variable in devices.items():
            identifier(device);token=secret(variable)
            if not 32<=len(token)<=128 or token in tokens:raise ValueError('invalid device credential')
            tokens[token]=device
        paths=set()
        for target,item in definitions.items():
            required_target={'origin','token_env','database','loopback_http'}
            if target not in ('windows','linux') or not isinstance(item,dict) or not required_target<=set(item) or set(item)-required_target-{'kind','speech'}:
                raise ValueError('invalid target configuration')
            if type(item['loopback_http']) is not bool:raise ValueError('explicit loopback HTTP choice required')
            database=item['database']
            if not isinstance(database,str) or not database or database==':memory:':raise ValueError('durable database path required')
            path=str(Path(database).resolve())
            if path in paths:raise ValueError('targets must have separate databases')
            paths.add(path);kind=item.get('kind','gateway');key=secret(item['token_env'])
            if kind=='gateway':
                adapter=HermesAdapter(JSONTransport(item['origin'],key,allow_loopback_http=item['loopback_http']))
            elif kind=='desktop':
                rpc=DesktopRPC(item['origin'],key);resources.append(rpc)
                adapter=DesktopAdapter(rpc,path+'.desktop');resources.append(adapter)
            else:raise ValueError('unsupported backend kind')
            adapter.check()
            store=Store(path);resources.append(store);store.recover()
            services[target]=Service(store,adapter)
            voice=item.get('speech')
            if voice is not None:
                speech[target]=speech_adapter(voice,target)
        return make_server(services,tokens,host=host,port=port,speech=speech,tls=tls),resources
    except BaseException:
        for resource in reversed(resources):resource.close()
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:
        data=Path(args.config).read_bytes()
        if len(data)>16384:raise ValueError('configuration too large')
        config=json.loads(data,object_pairs_hook=unique)
        server,resources=build(config)
    except Exception:
        parser.exit(2,'Bridge startup failed. Check configuration, credentials and backend capabilities locally.\n')
    print('Pocket bridge started with owner-configured identities; no startup model request was sent.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        for resource in reversed(resources):resource.close()
if __name__=='__main__':main()
