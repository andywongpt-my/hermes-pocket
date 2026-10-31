"""Inactive owner lifecycle. Actual C controller with fake adapter/owner only."""
from pathlib import Path
import subprocess,tempfile,unittest
HARNESS=r'''
#include "pocket_live_session.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
typedef struct {unsigned reserve,release,prepare,run,cleanup,scrub;uint64_t now;int mode;bool pending;const pocket_live_session_config*cfg;bool(*stop)(void*);void*stopctx;} fake;
static bool reserve(void*v){fake*f=v;f->reserve++;return f->mode!=1;}
static bool release(void*v){fake*f=v;f->release++;if(f->prepare)assert(f->scrub==1&&!f->cfg->token[0]);return f->mode!=2||!f->pending;}
static bool prepare(void*v,const pocket_live_session_config*c,bool(*stop)(void*),void*ctx,bool*acquired){fake*f=v;f->prepare++;*acquired=f->mode!=17;f->cfg=c;f->stop=stop;f->stopctx=ctx;return f->mode!=3&&f->mode!=14&&f->mode!=17;}
static pocket_live_v2_worker_result run(void*v){fake*f=v;f->run++;assert(f->cfg->epoch==7&&!strcmp(f->cfg->host,"example.test"));if(f->mode==4)pocket_live_session_request_stop(f->stopctx);assert(f->mode!=4||f->stop(f->stopctx));return(pocket_live_v2_worker_result){.reason=PLW_CONNECTION_LOST,.cleanup_complete=f->mode!=5,.terminal_received=f->mode==6,.backend_pending=f->mode==6};}
static bool cleanup(void*v){fake*f=v;f->cleanup++;return !f->pending;}
static bool scrub(void*v){fake*f=v;if(f->mode==15&&f->pending)return false;assert(!f->release&&f->cfg->token[0]);f->scrub++;return true;}
static uint64_t now(void*v){return ((fake*)v)->now;}
int main(int argc,char**argv){assert(argc==2);fake f={.now=100,.mode=atoi(argv[1])};pocket_live_session s={0};pocket_live_session_epoch epochs={0};pocket_live_session_ops o={&f,&f,reserve,release,prepare,run,cleanup,scrub,now,&epochs};pocket_live_session_config c={0};assert(pocket_live_session_config_snapshot(&c,"https://example.test","abcdefghijklmnopqrstuvwxyz123456","","chat_1",0,7,60));assert(pocket_live_session_init(&s,&o));assert(!pocket_live_session_init(&s,&o));
 if(f.mode==7)pocket_live_session_request_stop(&s);
 if(f.mode==11)epochs.last=7;
 if(f.mode==12)epochs.last=8;
 if(f.mode==13)epochs.exhausted=true;
 if(f.mode==14)f.pending=true;
 if(f.mode==16)c.epoch=UINT32_MAX;
 bool admitted=pocket_live_session_admit(&s,&c,1000);if(f.mode==17){assert(!admitted&&s.phase==PLS_CLEANUP_PENDING&&s.owner_held&&s.ownership_unknown);assert(!f.cleanup&&!f.scrub&&!f.release);assert(!pocket_live_session_reap(&s)&&!f.cleanup&&!f.scrub&&!f.release);return 0;}
 if(f.mode==11||f.mode==12||f.mode==13){assert(!admitted&&s.reason==PLS_EPOCH_REJECTED&&s.phase==PLS_RELEASED&&!f.prepare);return 0;}
 if(f.mode==14){assert(!admitted&&s.phase==PLS_CLEANUP_PENDING&&s.owner_held&&s.config.token[0]);assert(!pocket_live_session_reap(&s));f.pending=false;assert(pocket_live_session_reap(&s)&&!s.config.token[0]&&!f.run);return 0;}
 if(f.mode==1||f.mode==3||f.mode==7){assert(!admitted&&s.phase==PLS_RELEASED&&!s.owner_held&&!f.run);return 0;}
 assert(admitted&&f.reserve==1&&f.prepare==1);strcpy(c.host,"mutated");assert(!strcmp(s.config.host,"example.test"));assert(!pocket_live_session_admit(&s,&c,1000));
 if(f.mode==8||f.mode==16)pocket_live_session_request_stop(&s);
 if(f.mode==16)assert(epochs.last==UINT32_MAX&&epochs.exhausted);
 if(f.mode==9)f.now=1000;
 if(f.mode==10)f.now=99;
 if(f.mode==2||f.mode==5||f.mode==15)f.pending=true;
 assert(pocket_live_session_execute(&s));assert(!pocket_live_session_execute(&s));assert(s.backend_status_known==(f.mode==6));
 if(f.mode==2||f.mode==5||f.mode==15){assert(s.phase==PLS_CLEANUP_PENDING&&s.owner_held&&(f.mode!=2?s.config.token[0]!=0:s.config.token[0]==0)&&(f.mode!=2?f.scrub==0:f.scrub==1));assert(!pocket_live_session_init(&s,&o));assert(!pocket_live_session_reap(&s));f.pending=false;assert(pocket_live_session_reap(&s));}
 assert(s.phase==PLS_RELEASED&&!s.owner_held&&!s.config.token[0]&&f.scrub==1);unsigned runs=f.run;assert(pocket_live_session_reap(&s)&&f.run==runs);if(f.mode==8||f.mode==9||f.mode==10||f.mode==16)assert(!f.run);else assert(f.run==1);return 0;}
'''
class SessionTests(unittest.TestCase):
 def test_actual_controller(self):
  root=Path(__file__).resolve().parents[1];main=root/'firmware/main';core=root/'firmware/core'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(main),'-I',str(core),str(p/'test.c'),str(main/'pocket_live_session.c'),str(main/'pocket_live_session_config.c'),'-o',str(p/'test')],check=True)
   for mode in range(18):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
