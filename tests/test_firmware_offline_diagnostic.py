"""Actual finite silent diagnostic runner; fake peripheral ops, no hardware."""
from pathlib import Path
import subprocess,tempfile,unittest
HARNESS=r'''
#include "pocket_offline_diagnostic.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
typedef struct {uint64_t now;unsigned frames,begin,end,publishes;int mode;bool stop;pocket_offline_diagnostic_snapshot last;} fake;
static bool begin(void*v,uint32_t epoch,bool*cleanup){fake*f=v;assert(epoch==1);f->begin++;*cleanup=f->mode!=2;if(f->mode==3)f->now+=2000;if(f->mode==4)f->stop=true;return f->mode!=1&&f->mode!=2;}
static int capture(void*v,pocket_offline_diagnostic_frame*p){fake*f=v;f->now+=10;if(f->mode==5)return -1;if(f->mode==6)return 0;if(f->mode==7)f->now=0;if(f->mode==8)f->stop=true;*p=(pocket_offline_diagnostic_frame){.epoch=1,.sequence=f->frames,.sample_offset=f->frames*240};if(f->mode==9)p->sequence++;for(unsigned i=0;i<240;i++){p->pcm[i*4]=0xff;p->pcm[i*4+1]=0x7f;p->pcm[i*4+2]=0;p->pcm[i*4+3]=0x80;}f->frames++;return 1;}
static bool end(void*v){fake*f=v;f->end++;return f->mode!=10;}
static bool cancelled(void*v){return ((fake*)v)->stop;}
static uint64_t now(void*v){return ((fake*)v)->now;}
static void idle(void*v){((fake*)v)->now++;}
static void publish(void*v,const pocket_offline_diagnostic_snapshot*s){fake*f=v;f->publishes++;f->last=*s;if(s->phase>=PDIAG_DONE)assert(f->end==1||f->mode==2||f->mode==11);}
static const pocket_offline_diagnostic_ops ops={begin,capture,end,cancelled,now,idle,publish};
static void control_test(void){pocket_offline_diagnostic_control other={0};assert(pocket_offline_diagnostic_arm(&other,100,4));pocket_offline_diagnostic_button(&other,false,true,101,5);assert(other.stop&&other.phase==PDIAG_ERROR);pocket_offline_diagnostic_control c={0};assert(pocket_offline_diagnostic_arm(&c,100,4));pocket_offline_diagnostic_button(&c,true,true,99,5);assert(!pocket_offline_diagnostic_admit(&c,99,5,101));pocket_offline_diagnostic_button(&c,true,true,101,5);assert(!pocket_offline_diagnostic_admit(&c,101,6,102));assert(pocket_offline_diagnostic_admit(&c,101,5,102));assert(!pocket_offline_diagnostic_dispatch(&c,10100));assert(c.code==PDIAG_EXPIRED);assert(!pocket_offline_diagnostic_arm(&c,10200,9));c=(pocket_offline_diagnostic_control){0};assert(pocket_offline_diagnostic_arm(&c,100,4));pocket_offline_diagnostic_button(&c,true,true,101,5);assert(pocket_offline_diagnostic_admit(&c,101,5,102));pocket_offline_diagnostic_button(&c,false,true,103,6);assert(!pocket_offline_diagnostic_dispatch(&c,104));c=(pocket_offline_diagnostic_control){0};assert(!pocket_offline_diagnostic_arm(&c,100,UINT32_MAX));c=(pocket_offline_diagnostic_control){0};assert(pocket_offline_diagnostic_arm(&c,100,4));pocket_offline_diagnostic_input_lost(&c);assert(c.stop&&c.phase==PDIAG_ERROR);}
int main(int argc,char**argv){control_test();assert(argc==2);fake f={.now=100,.mode=atoi(argv[1])};if(f.mode==11)f.stop=true;bool ok=pocket_offline_diagnostic_run(&ops,&f);assert(ok==(f.mode!=10&&f.mode!=2));assert(f.end==(f.mode==2||f.mode==11?0:1));assert(f.last.restore_ok==(ok&&f.mode!=11));if(f.mode==0){assert(f.last.phase==PDIAG_DONE&&f.last.frames==1000&&f.last.samples==240000);assert(f.last.mean[0]==32767&&f.last.mean[1]==-32768&&f.last.rms[0]==32767&&f.last.rms[1]==32768&&f.last.peak[1]==32768&&f.last.clips[0]==240000);assert(f.publishes<=105);}else assert(f.last.phase==PDIAG_ERROR||f.last.phase==PDIAG_CLEANUP_FAILED);if(f.mode==3||f.mode==4||f.mode==11)assert(f.frames==0);return 0;}
'''
class OfflineDiagnosticTests(unittest.TestCase):
 def test_actual_core(self):
  root=Path(__file__).resolve().parents[1];core=root/'firmware/core'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(core),str(p/'test.c'),str(core/'pocket_offline_diagnostic.c'),'-o',str(p/'test')],check=True)
   for mode in range(12):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
