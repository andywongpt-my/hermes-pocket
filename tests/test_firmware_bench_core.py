"""Finite no-key bench control/wire tests; no hardware or serial access."""
from pathlib import Path
import subprocess, tempfile, unittest
HARNESS = r'''
#include "bench_core.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
typedef struct {int starts,stops,writes,submits,mode;uint64_t now;uint32_t frames,source;bool cancel;bench_core*c;} fake;
static bool start(void*v,bench_profile p){fake*f=v;f->starts++;if(f->mode==19)f->now+=6000;if(f->mode==24)f->now+=1000;if(f->mode==1)f->cancel=true;return p<=BENCH_TONE&&f->mode!=2;}
static int capture(void*v,bench_capture*p){fake*f=v;if(f->mode==20)f->now=0;if(f->mode==3)return -1;if(f->mode==4)return 0;*p=(bench_capture){.epoch=1,.sequence=f->frames,.sample_offset=f->frames*240};if(f->mode==5)p->sample_offset++;if(f->mode==6)f->cancel=true;f->frames++;return 1;}
static int submit(void*v,uint32_t off,const int16_t*p,size_t n){fake*f=v;f->submits++;assert(n==240&&off==f->source);for(size_t i=0;i<n;i++)assert(p[i]>=-512&&p[i]<=512);if(f->mode==7&&f->submits<4)return 0;if(f->mode==8)return -1;f->source+=240;return 1;}
static bool stop(void*v){fake*f=v;f->stops++;return f->mode!=9;}
static int write_(void*v,const uint8_t*p,size_t n){fake*f=v;assert(p[12]==(uint8_t)f->writes&&p[13]==(uint8_t)(f->writes>>8));f->writes++;if(f->mode==21)f->now+=6000;assert(n>=28&&!memcmp(p,"HPB1",4));if(f->c->phase==BENCH_TERMINAL)assert(f->stops==1);return f->mode==10?(int)n-1:(int)n;}
static bool cancelled(void*v){return ((fake*)v)->cancel;}
static uint64_t now_(void*v){return ((fake*)v)->now;}
static const bench_ops ops={start,capture,submit,stop,write_,cancelled,now_};
static void tick(bench_core*c,const bench_ops*o,fake*f,uint64_t now){f->now=now;bench_step(c,o,f,now);}
static void arm(bench_core*c){bench_input(c,(const uint8_t*)"ARM TONE\n",9,1);assert(c->phase==BENCH_ARMED);bench_button(c,true,true,false,2);bench_button(c,true,false,true,3);assert(c->phase==BENCH_ACTIVE);}
int main(int argc,char**argv){assert(argc==2);int mode=atoi(argv[1]);bench_core c;bench_init(&c,0);fake f={.c=&c};
 if(mode==0){arm(&c);for(unsigned i=0;i<501&&c.phase!=BENCH_TERMINAL;i++)tick(&c,&ops,&f,4+i*10);assert(c.phase==BENCH_TERMINAL&&c.captured==500&&c.restore_ok);assert(f.starts==1&&f.stops==1);bench_input(&c,(const uint8_t*)"ARM TONE\n",9,9999);assert(c.phase==BENCH_TERMINAL);}
 else if(mode<=10){f.mode=mode;arm(&c);for(unsigned i=0;i<600&&c.phase!=BENCH_TERMINAL;i++)tick(&c,&ops,&f,4+i*10);tick(&c,&ops,&f,6004);assert(c.phase==BENCH_TERMINAL&&f.stops==1);if(mode==1||mode==6)assert(f.submits==0);if(mode==9)assert(!c.restore_ok);if(mode==7)assert(c.captured==500);}
 else if(mode>=19&&mode<=21){f.mode=mode;arm(&c);tick(&c,&ops,&f,4);assert(c.phase==BENCH_TERMINAL&&f.submits==0&&f.stops==1);}
 else if(mode==24){f.mode=24;arm(&c);tick(&c,&ops,&f,4);assert(c.phase==BENCH_ACTIVE);tick(&c,&ops,&f,6004);assert(c.phase==BENCH_ACTIVE);tick(&c,&ops,&f,6254);assert(c.phase==BENCH_TERMINAL);}
 else if(mode==25){f.mode=4;arm(&c);tick(&c,&ops,&f,4);tick(&c,&ops,&f,5004);assert(c.phase==BENCH_ACTIVE);tick(&c,&ops,&f,5254);assert(c.phase==BENCH_TERMINAL);}
 else if(mode==22){bench_input(&c,(const uint8_t*)"ARM TONE\n",9,1);bench_button(&c,true,true,false,4);bench_button(&c,true,false,true,3);assert(c.phase==BENCH_ARMED);}
 else if(mode==23){for(unsigned t=0;t<100;t++)tick(&c,&ops,&f,t);assert(f.writes==0&&f.starts==0&&f.stops==0);bench_input(&c,(const uint8_t*)"ARM TONE\n",9,101);tick(&c,&ops,&f,101);assert(f.writes==1&&c.wire_sequence==1&&c.phase==BENCH_ARMED);}
 else if(mode==11){bench_input(&c,(const uint8_t*)"ARM SILENT\n",11,1);bench_button(&c,true,false,true,2);assert(c.phase==BENCH_ARMED);bench_button(&c,true,true,false,0);bench_button(&c,true,false,true,2);assert(c.phase==BENCH_ARMED);tick(&c,&ops,&f,10001);assert(c.phase==BENCH_TERMINAL&&f.starts==0&&f.stops==1);}
 else if(mode==12){arm(&c);tick(&c,&ops,&f,4);bench_button(&c,false,true,false,5);tick(&c,&ops,&f,5);assert(c.phase==BENCH_TERMINAL&&f.stops==1);}
 else if(mode==13){arm(&c);tick(&c,&ops,&f,4);bench_input(&c,(const uint8_t*)"STOP\n",5,5);tick(&c,&ops,&f,5);assert(c.phase==BENCH_TERMINAL&&f.stops==1);}
 else if(mode==14){arm(&c);tick(&c,&ops,&f,4);tick(&c,&ops,&f,3);assert(c.phase==BENCH_TERMINAL&&!strcmp(c.code,"clock_regression"));}
 else if(mode==15){bench_input(&c,(const uint8_t*)"ARM TONE\nARM TONE\n",18,1);bench_button(&c,true,true,false,2);bench_button(&c,true,false,true,3);tick(&c,&ops,&f,4);assert(c.phase==BENCH_TERMINAL&&f.starts==0);}
 else if(mode==16){int16_t p[240];unsigned nonzero=0;for(uint32_t k=0;k<120000;k+=240){bench_tone(k,p);for(unsigned i=0;i<240;i++){int x=p[i];assert(x>=-512&&x<=512);if(x){nonzero++;assert((k+i>=24000&&k+i<28800)||(k+i>=52800&&k+i<57600)||(k+i>=81600&&k+i<86400));}}}assert(nonzero>10000);}
 else if(mode==17){uint8_t p[3]={1,2,3},out[40];assert(bench_encode(out,sizeof out,2,4,240,p,3)==31);assert(!memcmp(out,"HPB1",4)&&out[4]==2&&out[8]==1&&out[12]==4&&out[16]==240&&out[20]==3);for(unsigned i=0;i<31;i++)printf("%02x",out[i]);puts("");}
 else if(mode==18){bench_input(&c,(const uint8_t*)"ARM SILENT\n",11,1);bench_button(&c,true,true,false,2);bench_button(&c,true,false,true,3);for(unsigned i=0;i<1001&&c.phase!=BENCH_TERMINAL;i++)tick(&c,&ops,&f,4+i*10);assert(c.phase==BENCH_TERMINAL&&c.captured==1000&&f.source==0);}
 return 0;}
'''.replace('#include <stdlib.h>', '#include <stdlib.h>\n#include <stdio.h>')
class BenchCoreTests(unittest.TestCase):
 def test_finite_core(self):
  root=Path(__file__).resolve().parents[1]; core=root/'firmware/bench/main'
  with tempfile.TemporaryDirectory() as td:
   src=Path(td)/'test.c';src.write_text(HARNESS);exe=Path(td)/'test'
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-O2','-I',str(core),str(src),str(core/'bench_core.c'),'-o',str(exe)],check=True)
   for mode in range(26):
    with self.subTest(mode=mode):
     output=subprocess.check_output([str(exe),str(mode)],text=True)
     if mode==17:
      import struct,zlib
      b=bytes.fromhex(output.strip());self.assertEqual(struct.unpack_from('<I',b,24)[0],zlib.crc32(b[28:],zlib.crc32(b[:24])))
