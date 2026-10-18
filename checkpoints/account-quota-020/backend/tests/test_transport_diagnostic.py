"""Real isolated route/arm behavior with synthetic bytes and no production calls."""
import http.client
import importlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest

from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store


def canonical(sequence):
    sizes=(2756,)
    prefix=('{"sequence":%d,"padding":"' % sequence).encode('ascii')
    return prefix+b'P'*(sizes[0]-len(prefix)-2)+b'"}'


class ArmFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='transport009-',dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.control=Path(self.temp.name)/'arm.json'
        self.now=[100_000_000_000]
        self.clock=None

    def controller(self,devices=None):
        spec=importlib.util.find_spec('pocket.transport_diagnostic')
        self.assertIsNotNone(spec,'file-armed bounded transport route is missing')
        module=importlib.import_module('pocket.transport_diagnostic')
        return module.FileArmedTransport(self.control,devices or {'fixture-device'},
            clock=self.clock or (lambda:self.now[0]),pid=os.getpid())

    def arm(self,**changes):
        value={'schema':1,'pid':os.getpid(),'start_ns':self.now[0],
               'deadline_ns':self.now[0]+120_000_000_000}
        value.update(changes)
        self.control.write_text(json.dumps(value))
        self.control.chmod(0o600)

    def dispatch(self,route,sequence=0,device='fixture-device',body=None,**changes):
        data=canonical(sequence) if body is None else body
        args={'authenticated_device':device,'method':'POST','path':'/diagnostics/transport',
              'lengths':[str(len(data))],'transfer_encoding':None,'read':lambda n:data[:n]}
        args.update(changes)
        return route.dispatch(**args)


class FileArmTests(ArmFixture):
    def test_bad_authenticated_get_closes_arm_before_any_post(self):
        self.arm();route=self.controller()
        self.assertEqual(self.dispatch(route,method='GET',lengths=['1'])[0],400)
        self.assertEqual(self.dispatch(route,method='GET',lengths=[])[0],404)
        self.assertNotEqual(self.dispatch(route,read=lambda _:self.fail('POST read after failed GET'))[0],200)
    @unittest.skipUnless(hasattr(os,'mkfifo'),'requires Linux FIFO semantics')
    def test_nonregular_arm_file_is_rejected_without_waiting_for_writer(self):
        os.mkfifo(self.control,0o600)
        code=('from pocket.transport_diagnostic import FileArmedTransport;import sys;'
              "r=FileArmedTransport(sys.argv[1],{'fixture-device'});"
              "print(r.dispatch('fixture-device','GET','/diagnostics/transport',[],None,lambda n:b'')[0])")
        try:
            result=subprocess.run([sys.executable,'-B','-c',code,str(self.control)],
                capture_output=True,text=True,timeout=1)
        except subprocess.TimeoutExpired:self.fail('nonregular arm open waited for a FIFO writer')
        self.assertEqual((result.returncode,result.stdout.strip()),(0,'404'))

    def test_readiness_get_does_not_consume_posts_and_latches_zero_after_first(self):
        route=self.controller();self.arm()
        def forbidden(_):self.fail('GET read a body')
        for _ in range(3):
            code,value=self.dispatch(route,method='GET',lengths=[],read=forbidden)
            self.assertEqual(code,200)
            self.assertEqual(value,{'version':2,'ready':1,'remaining_ms':120000,'requests':9,'bytes':24804})
        self.assertEqual(self.dispatch(route)[0],200)
        code,value=self.dispatch(route,method='GET',lengths=['0'],read=forbidden)
        self.assertEqual(code,200);self.assertEqual(value['ready'],0)
        for sequence in range(1,9):self.assertEqual(self.dispatch(route,sequence)[0],200)

    def test_old_matrix_lengths_are_rejected_before_body_read(self):
        self.arm()
        for size in (512,1024,1360,1440,2048,2757):
            route=self.controller()
            self.assertEqual(self.dispatch(route,lengths=[str(size)],
                read=lambda _:self.fail('non-2756 body read'))[0],413)

    def test_out_of_range_and_noncanonical_sequences_close_batch(self):
        self.arm()
        for body in (canonical(9),canonical(0).replace(b':0,',b':00,',1).replace(b'P',b'',1),
                     canonical(0)[:-1]+b'\n',canonical(0).replace(b'P',b'Q',1)):
            route=self.controller()
            self.assertEqual(self.dispatch(route,body=body,lengths=['2756'])[0],400)
            self.assertNotEqual(self.dispatch(route)[0],200)

    def test_malformed_post_reserves_before_framing_and_stops_second_body(self):
        route=self.controller();self.arm()
        self.assertEqual(self.dispatch(route,lengths=['512','512'],read=lambda _:self.fail('invalid framing read'))[0],400)
        self.assertNotEqual(self.dispatch(route,read=lambda _:self.fail('second attempt read'))[0],200)
        self.assertEqual(self.dispatch(route,method='GET',lengths=[])[0],404)

    def test_get_gate_is_404_after_completion_first_failure_or_expiry(self):
        self.arm();route=self.controller()
        for n in range(9):self.assertEqual(self.dispatch(route,n)[0],200)
        self.assertEqual(self.dispatch(route,method='GET',lengths=[])[0],404)
        route=self.controller()
        self.assertEqual(self.dispatch(route,body=b'Q'*2756)[0],400)
        self.assertEqual(self.dispatch(route,method='GET',lengths=[])[0],404)
        route=self.controller();self.now[0]+=120_000_000_000
        self.assertEqual(self.dispatch(route,method='GET',lengths=[])[0],404)

    def test_default_closed_and_stale_or_oversized_arm_cannot_read(self):
        route=self.controller()
        def forbidden(_):self.fail('closed gate read a body')
        self.assertEqual(self.dispatch(route,read=forbidden)[0],404)
        for changes in ({'pid':os.getpid()+100000},{'deadline_ns':self.now[0]+121_000_000_000},
                        {'start_ns':self.now[0]+1},{'schema':2},{'pid':True}):
            self.arm(**changes)
            self.assertEqual(self.dispatch(route,read=forbidden)[0],404)

    def test_exact_nine_requests_then_no_more_body_reads(self):
        route=self.controller();self.arm()
        total=0
        for n in range(9):
            code,value=self.dispatch(route,n)
            self.assertEqual((code,value),(200,{'sequence':n,'accepted':len(canonical(n))}))
            total+=value['accepted']
        self.assertEqual(total,24804)
        self.assertEqual(self.dispatch(route,read=lambda _:self.fail('10th body read'))[0],429)

    def test_expiry_and_manual_close_cannot_be_rearmed_in_same_process(self):
        route=self.controller();self.arm()
        self.assertEqual(self.dispatch(route)[0],200)
        self.now[0]+=120_000_000_000
        self.assertEqual(self.dispatch(route,1)[0],410)
        self.arm()
        self.assertNotEqual(self.dispatch(route,1)[0],200)
        self.control.unlink()
        self.assertEqual(self.dispatch(route,1)[0],404)

    def test_wrong_identity_cannot_read_or_consume_authorized_sequence(self):
        route=self.controller();self.arm()
        self.assertEqual(self.dispatch(route,device='other',read=lambda _:self.fail('wrong device read'))[0],403)
        self.assertEqual(self.dispatch(route)[0],200)

    def test_bad_pattern_stops_batch_and_no_payload_is_retained(self):
        route=self.controller();self.arm()
        self.assertEqual(self.dispatch(route,body=b'Q'*2756)[0],400)
        self.assertNotEqual(self.dispatch(route)[0],200)
        self.assertFalse(any(isinstance(v,bytes) for v in vars(route).values()))

    def test_timeout_closes_batch_and_expiry_during_read_rejects(self):
        route=self.controller();self.arm()
        def late(_):self.now[0]+=120_000_000_000;return canonical(0)
        self.assertEqual(self.dispatch(route,read=late)[0],410)
        self.control.unlink();self.arm();route=self.controller()
        def timeout(_):raise TimeoutError('fixture payload must not escape')
        with self.assertRaises(TimeoutError):self.dispatch(route,read=timeout)
        self.assertNotEqual(self.dispatch(route)[0],200)

    def test_multiple_paired_devices_fail_closed_without_guessing_identity(self):
        route=self.controller({'first','second'});self.arm()
        self.assertEqual(self.dispatch(route,read=lambda _:self.fail('ambiguous device read'))[0],404)


class ExistingHTTPIntegrationTests(ArmFixture):
    def start_server(self):
        self.assertIn('transport_diagnostic',inspect.signature(make_server).parameters,
                      'authenticated transport hook is missing')
        route=self.controller()
        store=Store(':memory:');self.addCleanup(store.close)
        server=make_server({'linux':Service(store,FakeHermes())},
            {'fixture-token':'fixture-device'},transport_diagnostic=route)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join(2)
        self.addCleanup(stop)
        return server

    def request(self,server,body=None,auth=True,path='/diagnostics/transport',method='POST'):
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        try:
            client.request(method,path,body=body,headers={'Authorization':'Bearer fixture-token'} if auth else {})
            response=client.getresponse();data=response.read()
            return response.status,json.loads(data),response.getheader('Connection')
        finally:client.close()

    def test_existing_auth_then_default_closed_before_route_or_body(self):
        server=self.start_server()
        self.assertEqual(self.request(server,auth=False)[0],401)
        self.assertEqual(self.request(server)[0],404)
        self.arm()
        code,gate,connection=self.request(server,path='/diagnostics/transport',method='GET')
        self.assertEqual((code,gate['ready'],connection),(200,1,'keep-alive'))
        code,value,connection=self.request(server,canonical(0))
        self.assertEqual((code,value,connection),(200,{'sequence':0,'accepted':2756},'keep-alive'))
        self.assertEqual(self.request(server,path='/diagnostics/transport',method='GET')[1]['ready'],0)
        self.control.unlink()
        self.assertEqual(self.request(server,canonical(1))[0],404)
        self.assertEqual(self.request(server,path='/health',method='GET')[0],200)

    def test_raw_framing_rejected_without_waiting_for_missing_body(self):
        server=self.start_server();self.arm()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=1)
        try:
            client.putrequest('POST','/diagnostics/transport')
            client.putheader('Authorization','Bearer fixture-token')
            client.putheader('Content-Length','2756');client.putheader('Content-Length','2756')
            client.endheaders()
            response=client.getresponse()
            self.assertEqual(response.status,400);self.assertEqual(response.getheader('Connection'),'close')
            response.read()
        finally:client.close()

    def test_disarmed_post_is_rejected_before_body_read(self):
        server=self.start_server()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=1)
        try:
            client.putrequest('POST','/diagnostics/transport')
            client.putheader('Authorization','Bearer fixture-token');client.putheader('Content-Length','2756')
            client.endheaders()
            response=client.getresponse();self.assertEqual(response.status,404);response.read()
        finally:client.close()

    def test_slow_fragments_cannot_extend_absolute_deadline(self):
        self.clock=time.monotonic_ns
        self.now[0]=time.monotonic_ns()
        server=self.start_server()
        self.arm(deadline_ns=self.now[0]+200_000_000)
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        client.putrequest('POST','/diagnostics/transport')
        client.putheader('Authorization','Bearer fixture-token');client.putheader('Content-Length','2756')
        client.endheaders()
        wire=client.sock
        def drip():
            try:
                body=canonical(0)
                for offset in range(0,len(body),32):
                    wire.sendall(body[offset:offset+32]);time.sleep(.05)
            except OSError:pass
        sender=threading.Thread(target=drip,daemon=True)
        began=time.monotonic();sender.start()
        try:
            response=client.getresponse();response.read()
            self.assertIn(response.status,(410,504))
            self.assertLess(time.monotonic()-began,.45,'body fragments extended the absolute arm deadline')
        finally:client.close();sender.join(1)


if __name__=='__main__':unittest.main()
