"""Actual heap bundle/controller/config with fake IDF boundary; no live I/O."""
from pathlib import Path
import tempfile,subprocess,unittest
HARNESS=r'''
#include "pocket_live_app.h"
#define POCKET_LIVE_V2_IDF_INTERNAL 1
#include "pocket_live_v2_idf.h"
#include <stdlib.h>
#include <string.h>
#include <assert.h>
static int mode;static unsigned allocations,frees,prepares,runs,cleanups,scrubs,releases;static bool ready,stop,pending;static uint64_t now=100;
static const pocket_live_session_config*saved;static pocket_live_v2_idf_context*saved_ctx;
void*test_alloc(size_t n,size_t size){allocations++;return mode==7?NULL:calloc(n,size);}
void test_free(void*p){frees++;free(p);}
bool pocket_live_v2_device_admission_allowed(void){return ready;}
static bool held(void*v){(void)v;return true;}
static bool release(void*v){(void)v;assert(scrubs==1&&!saved->token[0]&&!saved_ctx->snapshot.token[0]);releases++;return true;}
static bool stopped(void*v){(void)v;return stop;}
static uint64_t clock_(void*v){(void)v;return now;}
bool pocket_live_v2_idf_prepare(pocket_live_v2_idf_context*c,const pocket_live_session_config*k,bool(*s)(void*),void*v,bool*acquired){prepares++;*acquired=mode!=6;if(!*acquired)return false;c->snapshot=*k;saved=k;saved_ctx=c;c->audio_clean=c->network_clean=true;c->config.stopped=s;c->config.stop_context=v;return true;}
pocket_live_v2_worker_result pocket_live_v2_idf_execute(pocket_live_v2_idf_context*c,pocket_live_v2_worker*w){runs++;assert(!strcmp(c->snapshot.chat_id,"pocket")&&c->snapshot.volume==60);w->capture.pcm[0]=44;assert(!c->config.stopped(c->config.stop_context));c->audio_clean=mode!=3;c->network_clean=mode!=2;return(pocket_live_v2_worker_result){.reason=PLW_CONNECTION_LOST,.cleanup_complete=c->audio_clean&&c->network_clean};}
bool pocket_live_v2_idf_cleanup(pocket_live_v2_idf_context*c){cleanups++;if(pending)return false;c->network_clean=true;return c->audio_clean;}
bool pocket_live_v2_idf_scrub(pocket_live_v2_idf_context*c){assert(c->audio_clean&&c->network_clean);scrubs++;memset(&c->snapshot,0,sizeof c->snapshot);return true;}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);pocket_live_session_epoch epochs={0};pocket_live_app_owner o={NULL,held,release,stopped,clock_,&epochs};pocket_live_app_request r={"https://example.test","abcdefghijklmnopqrstuvwxyz123456","","pocket",1,60,7,1000};
 if(mode==0){assert(!pocket_live_app_create(NULL,NULL)&&!allocations&&!prepares);return 0;}
 ready=true;if(mode==4)stop=true;if(mode==5)r.token="invalid";if(mode==8)r.deadline_ms=100;
 pocket_live_app*a=pocket_live_app_create(&r,&o);
 if(mode==4||mode==5||mode==7||mode==8){assert(!a&&!prepares&&!releases);assert(allocations==((mode==4||mode==8)?0:1));return 0;}
 assert(a);if(mode!=6){assert(pocket_live_app_execute(a));assert(!pocket_live_app_execute(a));}
 pocket_live_app_snapshot status;pocket_live_app_read(a,&status);
 if(mode==2||mode==3||mode==6){assert(status.phase==PLS_CLEANUP_PENDING&&!releases&&!pocket_live_app_destroy(a));assert(status.network_cleanup_pending==(mode==2));pending=true;assert(!pocket_live_app_reap(a));if(mode!=2){assert(!cleanups&&!scrubs&&!frees);return 0;}pending=false;assert(pocket_live_app_reap(a));}
 assert(pocket_live_app_destroy(a)&&releases==1&&frees==1&&runs==1);return 0;}
'''
class AppTests(unittest.TestCase):
 def test_actual_bundle(self):
  root=Path(__file__).resolve().parents[1];main=root/'firmware/main'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'esp_err.h').write_text('#pragma once\ntypedef int esp_err_t;\n');(p/'esp_transport.h').write_text('#pragma once\ntypedef void* esp_transport_handle_t;\n');(p/'test.c').write_text(HARNESS)
   inc=['-I',str(p),'-I',str(main),'-I',str(root/'firmware/core'),'-I',str(root/'firmware/components/bsp/include')]
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*inc,'-Dcalloc=test_alloc','-Dfree=test_free','-c',str(main/'pocket_live_app.c'),'-o',str(p/'app.o')],check=True)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',*inc,str(p/'test.c'),str(p/'app.o'),str(main/'pocket_live_session.c'),str(main/'pocket_live_session_config.c'),'-o',str(p/'test')],check=True)
   for mode in range(9):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
