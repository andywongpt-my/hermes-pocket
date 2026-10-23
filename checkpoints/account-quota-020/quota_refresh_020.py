"""Explicit AW Codex sender. No auto-start, credentials, account repair or raw native output.

Requires a complete accepted Codex bundle and a prepared/active Linux channel.
Each one-shot reader must close successfully before its numeric result is sent.
"""
import argparse
import hashlib
import types
import private_aw_020 as private
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import quota_remote_020 as ops

ROOT=Path(__file__).resolve().parent
INTERVAL=120


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def receiver_argv():
    return ['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
        'pocket-host',ops.PYTHON,'-B',(ops.STAGE/'quota_remote_020.py').as_posix(),'--push-codex']


def transport(raw):
    result=ops.bounded_run(receiver_argv(),timeout=30,input_bytes=raw)
    value=ops.parse_json(result.stdout.encode('utf-8'))
    ops.require(result.returncode==0 and value.get('ok') is True,'transport_outcome_inspect')
    return value


def refresh_once(read_closed, send, *, acceptance_sha256, clock=time.time, cancelled=lambda:False):
    receipt={'ok':False,'profile':ops.CODEX_PROFILE,'model_requests':0,'audio_requests':0,'microphone_requests':0}
    try:
        if cancelled(): return {**receipt,'category':'interrupted'}
        started=clock()
        value=read_closed()
        completed=clock()
        if cancelled(): return {**receipt,'category':'interrupted'}
        raw=canonical(value)
        value=ops.validate_codex_snapshot(raw,observed_at=completed,started_at=started)
    except KeyboardInterrupt:
        return {**receipt,'category':'interrupted'}
    except Exception:
        return {**receipt,'category':'source_rejected'}
    if cancelled(): return {**receipt,'category':'interrupted'}
    try:
        acknowledgement=send(raw)
        expected={'ok':True,'profile':ops.CODEX_PROFILE,'provider':'codex',
            'acceptance_sha256':acceptance_sha256,'fetched_at':value['fetched_at'],
            'snapshot_sha256':hashlib.sha256(raw).hexdigest()}
        ops.require(acknowledgement==expected,'transport_receipt_mismatch')
    except BaseException:
        # Publication may have completed: never blindly retry or invent a timestamp.
        return {**receipt,'category':'transport_outcome_inspect','outcome_uncertain':True,
            'attempted_fetched_at':value['fetched_at'],'attempted_snapshot_sha256':hashlib.sha256(raw).hexdigest()}
    return {**receipt,'ok':True,'category':'published','fetched_at':value['fetched_at'],
        'snapshot_sha256':hashlib.sha256(raw).hexdigest()}


def executable_digest(path):
    path=Path(path)
    ops.require(path.is_absolute(),'native_executable_binding')
    for ancestor in reversed(path.parents): ops.guard(ancestor,directory=True)
    before=ops.guard(path)
    ops.require(before.st_size<=512*1024*1024,'native_executable_binding')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        path_revision=ops.revision(before)
        opened_revision=ops.revision(opened)
        if sys.platform=='win32':
            # Windows path stat synthesizes .exe execute bits; fstat has no filename.
            # Only this cross-view comparison normalizes those synthetic bits.
            path_revision=(*path_revision[:2],path_revision[2]&~0o111,*path_revision[3:])
            opened_revision=(*opened_revision[:2],opened_revision[2]&~0o111,*opened_revision[3:])
        ops.require(path_revision==opened_revision,'native_executable_binding')
        while True:
            data=stream.read(65536)
            if not data: break
            digest.update(data)
        ops.require(ops.revision(opened)==ops.revision(os.fstat(stream.fileno())),'native_executable_binding')
    ops.require(ops.revision(before)==ops.revision(ops.guard(path)),'native_executable_binding')
    return digest.hexdigest()


def load_checked_client(path, expected_sha256):
    raw=ops.read_file(path)
    ops.require(hashlib.sha256(raw).hexdigest()==expected_sha256,'collector_binding')
    module=types.ModuleType('accepted_codex_quota')
    module.__file__=str(path); module.__package__=''; module.__loader__=None; module.__spec__=None
    exec(compile(raw,str(path),'exec',dont_inherit=True),module.__dict__)
    return module


def load_reader():
    ops.require(os.name=='nt','aw_runtime_required')
    bundle,native=private.validate_aw_bundle(ROOT)
    ops.require(executable_digest(native['executable'])==native['executable_sha256'],'native_executable_binding')
    producer=load_checked_client(ROOT/'official_codex_quota.py',ops.hashes(bundle['acceptance']['collector_files'])['official_codex_quota.py'])
    def read_closed():
        current,current_native=private.validate_aw_bundle(ROOT)
        ops.require(current_native==native,'private_native_drift')
        ops.require(current['acceptance_sha256']==bundle['acceptance_sha256'],'acceptance_drift')
        ops.require(executable_digest(native['executable'])==native['executable_sha256'],'native_executable_binding')
        with producer.CodexQuotaClient(native['executable'],expected_binding=native['account_binding_sha256'],rpc_timeout=5) as client:
            value=client.read()
        # Context exit checks cleanup; no value escapes if shutdown fails.
        return value
    return read_closed,bundle['acceptance_sha256']


def signal_handler(stop):
    def cancel(signum,frame):
        if not stop.is_set(): stop.set(); raise KeyboardInterrupt()
    return cancel


def run_loop(reader,send,*,acceptance_sha256,stop,count=1,clock=time.time,emit=print,wait=None):
    wait=stop.wait if wait is None else wait
    completed=0
    while not stop.is_set() and (count==0 or completed<count):
        result=refresh_once(reader,send,acceptance_sha256=acceptance_sha256,clock=clock,cancelled=stop.is_set)
        emit(json.dumps(result,separators=(',',':'))); completed+=1
        if not result['ok']: return 2 if result.get('outcome_uncertain') else 130 if result['category']=='interrupted' else 1
        if count and completed>=count: return 0
        if wait(INTERVAL): return 130
    return 130 if stop.is_set() else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=1,help='0 runs serially until explicitly stopped')
    args=parser.parse_args(argv)
    stop=threading.Event()
    previous={s:signal.signal(s,signal_handler(stop)) for s in (signal.SIGINT,signal.SIGTERM)}
    try:
        ops.require(0<=args.count<=10000,'invalid_count')
        reader,binding=load_reader()
        return run_loop(reader,transport,acceptance_sha256=binding,stop=stop,count=args.count)
    except KeyboardInterrupt: return 130
    except Exception:
        print(json.dumps({'ok':False,'category':'aw_runtime_rejected'})); return 1
    finally:
        for s,handler in previous.items(): signal.signal(s,handler)


if __name__=='__main__': raise SystemExit(main())
