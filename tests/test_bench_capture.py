import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from pocket.bench_capture import BenchError, BenchValidator, RecordParser, inspect_capture, collect

HEADER=struct.Struct('<4sBBHIIIII')
def record(kind,seq,payload,offset=0,epoch=1):
    prefix=HEADER.pack(b'HPB1',kind,0,0,epoch,seq,offset,len(payload),0)[:24]
    return prefix+struct.pack('<I',zlib.crc32(payload,zlib.crc32(prefix)))+payload

def status(phase='armed',profile='silent',captured=0,source_samples=0,restore_ok=False,code='armed'):
    return dict(phase=phase,profile=profile,code=code,rate=24000,channels=2,reference='es8311_dac_right',captured=captured,source_samples=source_samples,restore_ok=restore_ok,actual_acoustic_validation=False,max_capture_frames=1000 if profile=='silent' else 500,peak=0 if profile=='silent' else 512,volume=0 if profile=='silent' else 5)
def wire_status(seq=0,**kw):return record(1,seq,json.dumps(status(**kw),separators=(',',':')).encode())

class BenchCaptureTests(unittest.TestCase):
    def test_literal_header_fragmentation_prefix_and_crc(self):
        payload=bytes(960);raw=record(2,2,payload)
        self.assertEqual(raw[:24],bytes.fromhex('4850423102000000010000000200000000000000c0030000'))
        parser=RecordParser();result=[]
        stream=b'boot log\r\n'+wire_status()+wire_status(1,phase='running',code='running')+raw
        for b in stream:result.extend(parser.feed(bytes([b])))
        self.assertEqual(len(result),3);self.assertEqual(result[-1].payload,payload)
        parser.finish()
        for index in (6,8,12,16,20,24,100):
            bad=bytearray(raw);bad[index]^=1
            with self.subTest(index=index),self.assertRaises(BenchError):
                decoder=RecordParser();decoder.feed(bytes(bad));decoder.finish()
    def test_prefix_size_partial_and_no_resynchronization(self):
        for raw in (b'x'*16385,b'HPB1',record(2,0,bytes(960))[:-1]):
            with self.subTest(n=len(raw)),self.assertRaises(BenchError):
                p=RecordParser();p.feed(raw);p.finish()
        p=RecordParser();p.feed(wire_status())
        with self.assertRaises(BenchError):p.feed(b'log!'+wire_status(1))
    def test_state_metadata_counter_and_terminal_guards(self):
        v=BenchValidator('silent');p=RecordParser()
        for raw in (wire_status(),wire_status(1,phase='running',code='running'),record(2,2,bytes(960))):
            for r in p.feed(raw):v.accept(r)
        self.assertEqual(v.captured,1)
        with self.assertRaises(BenchError):v.finish()
        for raw in (record(2,3,bytes(960),offset=480),record(2,4,bytes(960),offset=240),record(2,3,bytes(960),offset=240,epoch=2),wire_status(3,phase='done',captured=1,source_samples=240,restore_ok=True,code='complete')):
            vv=BenchValidator('silent');pp=RecordParser()
            with self.subTest(raw=raw[:28].hex()),self.assertRaises(BenchError):
                for chunk in (wire_status(),wire_status(1,phase='running',code='running'),record(2,2,bytes(960)),raw):
                    for r in pp.feed(chunk):vv.accept(r)
        for change in ({'actual_acoustic_validation':True},{'volume':6},{'max_capture_frames':1001},{'rate':True},{'extra':1}):
            s=status();s.update(change)
            with self.assertRaises(BenchError):
                BenchValidator('silent').accept(RecordParser().feed(record(1,0,json.dumps(s).encode()))[0])
    def test_complete_tone_capture_and_strict_json(self):
        records=[wire_status(profile='tone'),wire_status(1,phase='running',profile='tone',code='running')]
        records += [record(2,2+i,struct.pack('<480h',*([7,-11]*240)),offset=i*240) for i in range(500)]
        records += [wire_status(502,phase='done',profile='tone',captured=500,source_samples=120000,restore_ok=True,code='complete')]
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'capture.hpb';path.write_bytes(b''.join(records));report=inspect_capture(path,'tone')
            self.assertTrue(report['complete']);self.assertFalse(report['actual_acoustic_validation']);self.assertEqual(report['capture_frames'],500)
            self.assertEqual(report['microphone']['peak'],7);self.assertEqual(report['reference']['peak'],11)
        for bad in (b'{"phase":"armed","phase":"armed"}',b'{"phase":NaN}'):
            with self.assertRaises(BenchError):BenchValidator('silent').accept(RecordParser().feed(record(1,0,bad))[0])
    def test_startup_cap_is_a_known_failed_terminal(self):
        v=BenchValidator('silent');p=RecordParser()
        for raw in (wire_status(),wire_status(1,phase='error',code='startup_cap',restore_ok=True)):
            for r in p.feed(raw):v.accept(r)
        report=v.finish();self.assertFalse(report['complete']);self.assertEqual(report['terminal_code'],'startup_cap')
    def test_error_is_not_success_even_with_restore(self):
        v=BenchValidator('silent');p=RecordParser()
        for raw in (wire_status(),wire_status(1,phase='error',code='stopped',restore_ok=True)):
            for r in p.feed(raw):v.accept(r)
        result=v.finish();self.assertFalse(result['complete']);self.assertEqual(result['terminal_code'],'stopped')
        with self.assertRaises(BenchError):v.accept(p.feed(wire_status(2))[0])
    def test_source_counters_obey_profile_and_complete_block_units(self):
        for profile,source in (('silent',240),('tone',1),('tone',241)):
            with self.subTest(profile=profile,source=source),self.assertRaises(BenchError):
                v=BenchValidator(profile);p=RecordParser()
                for raw in (wire_status(profile=profile),wire_status(1,phase='error',profile=profile,code='stopped',source_samples=source)):
                    for r in p.feed(raw):v.accept(r)
    def test_fake_collection_complete_and_timeout(self):
        from unittest.mock import patch
        class Serial:
            def __init__(self,data=b''):self.data=data;self.commands=[];self.closed=False
            def open(self):pass
            def close(self):self.closed=True
            def write(self,data):self.commands.append(data);return len(data)
            def read(self,n):chunk,self.data=self.data[:n],self.data[n:];return chunk
        records=[wire_status(),wire_status(1,phase='running',code='running')]
        records.extend(record(2,2+i,bytes(960),offset=i*240) for i in range(1000))
        records.append(wire_status(1002,phase='done',captured=1000,source_samples=0,restore_ok=True,code='complete'))
        with tempfile.TemporaryDirectory() as root:
            fake=Serial(b''.join(records));out=Path(root)/'complete'
            report=collect(port='/dev/fake',profile='silent',output_dir=out,serial_factory=lambda:fake)
            self.assertTrue(report['complete']);self.assertTrue(fake.closed)
            self.assertEqual(fake.commands,[b'ARM SILENT\n'])
            self.assertEqual((out/'capture.hpb').stat().st_mode&0o777,0o600)
            self.assertEqual(inspect_capture(out/'capture.hpb','silent'),report)
            fake=Serial();out=Path(root)/'timeout'
            with patch('pocket.bench_capture.time.monotonic',side_effect=[0,31]),self.assertRaises(BenchError):
                collect(port='/dev/fake',profile='silent',output_dir=out,serial_factory=lambda:fake)
            self.assertEqual(fake.commands,[b'ARM SILENT\n',b'STOP\n'])
            self.assertFalse(json.loads((out/'report.json').read_text())['complete'])
    def test_real_c_core_records_are_accepted_without_wire_translation(self):
        import shutil
        import subprocess
        compiler=shutil.which('cc')
        if not compiler:self.skipTest('native C compiler required')
        root=Path(__file__).resolve().parents[1]
        fixture=r'''#include "bench_core.h"
#include <stdio.h>
#include <string.h>
#include <assert.h>
static uint64_t clock_ms;
static unsigned captures;
static bool start(void*v,bench_profile p){(void)v;(void)p;return true;}
static int capture(void*v,bench_capture*f){(void)v;memset(f,0,sizeof *f);f->epoch=1;f->sequence=captures;f->sample_offset=captures++*240;return 1;}
static int submit(void*v,uint32_t off,const int16_t*p,size_t n){(void)v;(void)off;(void)p;assert(n==240);return 1;}
static bool stop(void*v){(void)v;return true;}
static int write_record(void*v,const uint8_t*p,size_t n){(void)v;return (int)fwrite(p,1,n,stdout);}
static bool cancelled(void*v){(void)v;return false;}
static uint64_t now(void*v){(void)v;return clock_ms;}
int main(int argc,char**argv){assert(argc==2);bench_core c;bench_init(&c,0);bench_ops ops={start,capture,submit,stop,write_record,cancelled,now};
 const char*arm=strcmp(argv[1],"tone")==0?"ARM TONE\n":"ARM SILENT\n";clock_ms=10;bench_input(&c,(const uint8_t*)arm,strlen(arm),clock_ms);bench_step(&c,&ops,0,clock_ms);
 bench_button(&c,true,true,false,11);bench_button(&c,true,false,true,12);
 for(unsigned i=0;i<1100&&c.phase!=BENCH_TERMINAL;i++){clock_ms=20+(uint64_t)i*10;bench_step(&c,&ops,0,clock_ms);}
 assert(c.phase==BENCH_TERMINAL&&c.restore_ok&&!strcmp(c.code,"complete"));return 0;}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c=Path(tmp)/'emit.c';c.write_text(fixture);exe=Path(tmp)/'emit'
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I',str(root/'firmware/bench/main'),str(c),str(root/'firmware/bench/main/bench_core.c'),'-o',str(exe)],check=True,capture_output=True,timeout=30)
            for profile in ('silent','tone'):
                output=subprocess.run([str(exe),profile],check=True,capture_output=True,timeout=10).stdout
                path=Path(tmp)/(profile+'.hpb');path.write_bytes(output);report=inspect_capture(path,profile)
                self.assertTrue(report['complete']);self.assertEqual(report['capture_frames'],1000 if profile=='silent' else 500)
    def test_collection_has_explicit_port_no_overwrite_and_fatal_partial_arm(self):
        class Serial:
            def __init__(self):self.dtr=True;self.rts=True;self.port=None;self.opened=False;self.closed=False;self.commands=[]
            def open(self):self.opened=True;assert not self.dtr and not self.rts
            def close(self):self.closed=True
            def write(self,data):self.commands.append(data);return len(data)-1
            def read(self,n):raise AssertionError('must not read after partial arm')
        fake=Serial()
        with tempfile.TemporaryDirectory() as root:
            out=Path(root)/'capture'
            with self.assertRaises(BenchError):collect(port='',profile='silent',output_dir=out,serial_factory=lambda:fake)
            self.assertFalse(out.exists());self.assertFalse(fake.opened)
            with self.assertRaises(BenchError):collect(port='/dev/explicit',profile='silent',output_dir=out,serial_factory=lambda:fake)
            self.assertTrue(fake.closed);self.assertEqual(fake.commands,[b'ARM SILENT\n',b'STOP\n'])
            self.assertFalse(json.loads((out/'report.json').read_text())['complete'])
            with self.assertRaises(BenchError):collect(port='/dev/explicit',profile='silent',output_dir=out,serial_factory=lambda:fake)

if __name__=='__main__':unittest.main()
