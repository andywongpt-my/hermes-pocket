"""Explicit Windows SILENT qualification; default/help/preflight opens no port.

A pending or uncertain driver owner keeps the guard process alive. Closing its
console or killing the process may close native handles/reset hardware: that is
not an automatic recovery action. Physical platform evidence is a prerequisite,
not something flags or cloud tests can establish.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import stat
import sys
from .qualification import Collector, REPORT_FIELDS, QualificationError
from .qualification_serial import ExplicitSerialTransport
from . import qualification_preflight as pre

EXTRA_FIELDS=frozenset(('launcher_version','serial_open_completed','host_close_completed',
    'host_owner_pending','host_cleanup_unknown','stop_requested','launcher_error',
    'capture_ram_wiped','flash_authorized','platform_behavior_verified',
    'probe_only','probe_api_completed','probe_hold_observed_ms'))

class StopLatch:
    def __init__(self):self.requested=False
    def request(self,*_):self.requested=True
    def __call__(self):return self.requested


def report_json(report):
    if (not isinstance(report,dict) or set(report)-(REPORT_FIELDS|EXTRA_FIELDS) or
            any(type(v) is not int or not 0<=v<(1<<64) for v in report.values()) or
            report.get('physical_acceptance',0)!=0 or report.get('flash_authorized',0)!=0 or
            report.get('raw_retained',0)!=0 or report.get('platform_behavior_verified',0)!=0):
        raise QualificationError('invalid_launcher_report')
    value=json.dumps(report,sort_keys=True,separators=(',',':'))
    if len(value)>8192:raise QualificationError('launcher_report_budget')
    return value


def output_path(value):
    lexical=pre.local_path(value)
    if sys.platform=='win32':pre.require_local_drive(lexical)
    path=Path(lexical)
    try:
        for parent in reversed(path.parents):
            info=os.lstat(parent)
            if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise QualificationError('summary_parent_invalid')
        if os.path.lexists(path):raise QualificationError('summary_must_be_new')
    except OSError:raise QualificationError('summary_path_unavailable') from None
    return path


def write_report(report,path):
    value=report_json(report)+'\n';target=output_path(path)
    try:
        with open(target,'x',encoding='ascii') as stream:stream.write(value)
    except (OSError,ValueError):raise QualificationError('summary_write_failed') from None


class SilentSession:
    """One injected, separately authorized owner. No discovery or retry.

    host_close_completed describes the wrapper/driver API outcome only, not an
    independently measured native-handle, control-line or device cleanup fact.
    """
    def __init__(self,transport,identity,stop):
        if not isinstance(identity,str) or not isinstance(stop,StopLatch):
            raise QualificationError('invalid_launcher_session')
        self.transport=transport;self.identity=identity;self.stop=stop
        self._used=False;self._close_completed=False;self._waited_pending=False
        self._cleanup_unknown=False
        self._report=dict(launcher_version=1,serial_open_completed=0,host_close_completed=0,
            host_owner_pending=0,host_cleanup_unknown=0,stop_requested=0,launcher_error=0,
            capture_ram_wiped=0,physical_acceptance=0,flash_authorized=0,raw_retained=0)
    def report(self):
        result=dict(self._report,host_close_completed=int(self._close_completed),
            host_owner_pending=int(self.transport.owner_pending),
            host_cleanup_unknown=int(self._cleanup_unknown),stop_requested=int(self.stop()))
        report_json(result);return result
    async def _close(self):
        try:closed=await self.transport.close()
        except asyncio.CancelledError:
            self.stop.request();closed=False
        except Exception:closed=False
        self._close_completed=closed is True
        self._waited_pending=not self._close_completed and self.transport.owner_pending
        self._cleanup_unknown=not self._close_completed and not self._waited_pending
    async def _body(self):
        return await Collector(self.transport,self.identity,capture_approved=True,
            profile=1,stop_requested=self.stop).run()
    async def run(self):
        if self._used:raise QualificationError('launcher_attempt_already_used')
        self._used=True;capture=None
        if self.stop():return self.report()  # No open/ARM/close before an attempt.
        try:
            await self.transport.open();self._report['serial_open_completed']=1
            if not self.stop():
                capture=await self._body()
                if capture is not None:self._report.update(capture.report())
        except asyncio.CancelledError:
            self.stop.request();self._report['launcher_error']=2
        except Exception:
            self._report['launcher_error']=1
        finally:
            if capture is not None:
                capture.close();self._report['capture_ram_wiped']=1
            await self._close()
        return self.report()
    async def observe_cleanup(self):
        # Only newly completed pending work permits one more observation. An
        # unknown finished close is not retried, and no overlapping IO is allowed.
        if self._close_completed:return True
        if self.transport.owner_pending:return False
        if self._waited_pending:
            self._waited_pending=False;await self._close()
        return self._close_completed


class PlatformProbeSession(SilentSession):
    """No read/write/ARM/capture. Observes only a single open/close API attempt."""
    def __init__(self,transport,identity,stop):
        super().__init__(transport,identity,stop)
        self._report.update(probe_only=1,probe_api_completed=0,
            platform_behavior_verified=0,probe_hold_observed_ms=0)
    async def _body(self):
        loop=asyncio.get_running_loop();began=loop.time();deadline=began+2.0
        while not self.stop() and loop.time()<deadline:
            await asyncio.sleep(min(.02,max(0,deadline-loop.time())))
        self._report['probe_hold_observed_ms']=max(0,int((loop.time()-began)*1000))
        return None
    def report(self):
        result=super().report()
        result['probe_api_completed']=int(result['serial_open_completed']==1 and
            result['host_close_completed']==1 and result['launcher_error']==0)
        return result


class _Parser(argparse.ArgumentParser):
    def error(self,message):raise QualificationError('qualification_arguments_invalid')


def _parser():
    p=_Parser(description='Prepared SILENT qualification; no flash, audio output, TLS or raw files.')
    sub=p.add_subparsers(dest='command')
    for command in ('preflight','run-silent','probe-open-close'):
        item=sub.add_parser(command)
        for name in ('candidate','recovery','port','identity'):
            item.add_argument('--'+name,required=True)
        if command!='preflight':
            item.add_argument('--summary',required=True)
            for name in ('approve-open','approve-close','acknowledge-reset-risk',
                         'confirm-recovery','verify-identity'):
                item.add_argument('--'+name,action='store_true',required=True)
            if command=='run-silent':
                for name in ('verify-platform-behavior','approve-capture'):
                    item.add_argument('--'+name,action='store_true',required=True)
    return p


def _emit(value,*,error=False):
    # Console/pipe failure must never outrank a retained native owner's lifetime.
    try:print(value,file=sys.stderr if error else sys.stdout,flush=True);return True
    except Exception:return False


async def _run(args,stop):
    probe=getattr(args,'command','run-silent')=='probe-open-close'
    transport=ExplicitSerialTransport(port=args.port,identity=args.identity,
        open_approved=args.approve_open,reset_risk_acknowledged=args.acknowledge_reset_risk,
        recovery_confirmed=args.confirm_recovery,identity_verified=args.verify_identity,
        platform_behavior_verified=getattr(args,'verify_platform_behavior',False),platform_probe=probe)
    session=(PlatformProbeSession if probe else SilentSession)(transport,args.identity,stop)
    report=await session.run()
    output_failed=not _emit(report_json(report))
    if report['serial_open_completed']==0 and stop() and report['launcher_error']==0:
        return 2  # Stop before open: no driver owner to guard.
    if not report['host_close_completed']:
        # Finite collection is over. Do not let asyncio.run/process shutdown
        # silently abandon a retained native owner. Signals only latch Stop.
        output_failed=not _emit('qualification_owner_retained_do_not_close_console',error=True) or output_failed
        while not await session.observe_cleanup():await asyncio.sleep(.1)
        report=session.report()
        output_failed=not _emit(report_json(report)) or output_failed
    if output_failed:report['launcher_error']=report['launcher_error'] or 3
    write_report(report,args.summary)
    completed=report.get('probe_api_completed') if probe else report.get('complete')
    return 0 if completed==1 and not report['launcher_error'] and not report['stop_requested'] else 2


def main(argv=None):
    parser=_parser()
    try:
        args=parser.parse_args(argv)
        if args.command is None:parser.print_help();return 0
        facts=pre.preflight(args.candidate,args.recovery,args.port,args.identity)
        if args.command=='preflight':
            print(json.dumps(facts,sort_keys=True,separators=(',',':')));return 0
        if facts['runtime_compatible']!=1:raise QualificationError('unsupported_windows_runtime')
        output_path(args.summary)  # Refuse unsafe/existing destination before opening.
        stop=StopLatch();signals=[signal.SIGINT]
        if hasattr(signal,'SIGBREAK'):signals.append(signal.SIGBREAK)
        previous={kind:signal.getsignal(kind) for kind in signals}
        try:
            for kind in signals:signal.signal(kind,stop.request)
            return asyncio.run(_run(args,stop))
        finally:
            for kind,handler in previous.items():signal.signal(kind,handler)
    except SystemExit as error:return int(error.code)
    except (QualificationError,pre.PreflightError):
        print('qualification_request_refused',file=sys.stderr);return 2
    except Exception:
        print('qualification_host_error',file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
