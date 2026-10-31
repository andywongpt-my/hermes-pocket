"""Synthetic worker orchestration; no I2S, network or physical acceptance."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HARNESS = r'''
#include "pocket_live_worker.h"
#include <assert.h>
#include <string.h>
#include <stdlib.h>
typedef struct { unsigned now, receives, sent, starts, stops, closes, captures, binary_sent; int mode; bool stopped; } fake;
static bool connect_(void *v){fake *f=v; f->starts++; return f->mode!=8;}
static bool send_(void *v,bool binary,const uint8_t *p,size_t n){fake *f=v;assert(p&&n);f->sent++;if(binary){f->binary_sent++;assert(n==976&&p[3]==1);}return f->mode!=9;}
static bool receive_(void *v,pocket_live_worker_event *e,uint8_t *p,size_t cap){
 fake *f=v; f->now+=10; f->receives++; e->kind=PLW_NONE;
 if(f->mode==2)return true;
 if(f->receives==1){e->kind=PLW_READY;return true;}
 if(f->mode==3&&f->receives==2){e->kind=PLW_READY;return true;}
 if(f->mode==4&&f->receives==2){e->kind=PLW_LOST;return true;}
 if(f->receives==2){
  uint8_t pcm[960]={0}; pocket_live_packet q={2,0,0,0,pcm,sizeof pcm};
  assert(pocket_live_pack(&q,p,cap,&e->bytes)==POCKET_LIVE_OK);e->kind=PLW_AUDIO;return true;
 }
 if(f->receives==3){f->stopped=true;return true;}
 if(f->mode==11&&f->receives==4){
  uint8_t pcm[960]={0};pocket_live_packet q={2,1,480,0,pcm,sizeof pcm};
  assert(pocket_live_pack(&q,p,cap,&e->bytes)==POCKET_LIVE_OK);e->kind=PLW_AUDIO;return true;
 }
 if(f->mode==11&&f->receives==5){e->kind=PLW_CLOSED;e->finalized=true;return true;}
 if(f->receives==4){e->kind=PLW_CLOSED;e->finalized=f->mode!=5;e->backend_pending=true;e->playback_pending=true;}
 return true;
}
static bool audio_start(void *v){fake*f=v;f->captures++;return f->mode!=6;}
static bool capture(void*v,uint8_t*p,size_t cap,size_t*n){fake*f=v;if(f->mode==12)f->stopped=true;assert(cap>=960);memset(p,0,960);*n=960;return true;}
static bool submit(void*v,uint32_t offset,const uint8_t*p,size_t n){(void)v;assert(offset==0&&p&&n==960);return true;}
static bool rendered(void*v,uint32_t*n){fake*f=v;*n=(f->mode==11&&f->receives>=2)?480u:0u;return true;}
static bool stop_audio(void*v){fake*f=v;f->stops++;return f->mode!=7;}
static bool close_(void*v){fake*f=v;f->closes++;return true;}
static uint64_t now(void*v){return ((fake*)v)->now;}
static bool stop(void*v){return ((fake*)v)->stopped;}
int main(int argc,char**argv){assert(argc==2);fake f={.mode=atoi(argv[1])};
 pocket_live_worker w; pocket_live_worker_ops o={&f,connect_,send_,receive_,audio_start,capture,submit,rendered,stop_audio,close_,now,stop};
 if(f.mode==10)f.stopped=true;
 pocket_live_worker_result r=pocket_live_worker_run(&w,&o,"chat");
 if(f.mode==1){assert(r.reason==PLW_STOPPED&&r.finalized&&r.backend_pending&&r.playback_pending&&r.terminal_received);assert(f.stops==1&&f.closes==1);assert(f.binary_sent==2);}
 else if(f.mode==2){assert(r.reason==PLW_START_TIMEOUT&&!r.finalized&&f.stops==0&&f.closes==1);}
 else if(f.mode==3){assert(r.reason==PLW_PROTOCOL_ERROR&&!r.finalized&&f.stops==1);}
 else if(f.mode==4){assert(r.reason==PLW_CONNECTION_LOST&&!r.finalized&&f.stops==1&&!r.terminal_received);}
 else if(f.mode==5){assert(r.reason==PLW_UNCONFIRMED&&!r.finalized);}
 else if(f.mode==6){assert(r.reason==PLW_AUDIO_ERROR&&f.stops==1);}
 else if(f.mode==7){assert(r.reason==PLW_CLEANUP_FAILED&&!r.cleanup_complete);}
 else if(f.mode==8){assert(r.reason==PLW_CONNECT_FAILED&&f.closes==1&&f.stops==0);}
 else if(f.mode==9){assert(r.reason==PLW_SEND_FAILED&&f.closes==1&&f.stops==0);}
 else if(f.mode==10){assert(r.reason==PLW_STOPPED_BEFORE_CONNECT&&f.starts==0&&f.closes==0);}
 else if(f.mode==11){assert(r.playback_pending&&r.finalized);}
 else if(f.mode==12){assert(r.finalized&&f.binary_sent==0);}
 else assert(0);
 assert(sizeof w<=7600);return 0;
}
'''
class FirmwareLiveWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
    def run_case(self,n):
        self.assertTrue((self.root/'firmware/main/pocket_live_worker.h').exists(), 'worker not implemented')
        compiler=shutil.which('gcc')
        if not compiler:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(HARNESS)
            args=[compiler,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2',
                '-I'+str(self.root/'firmware/core'),'-I'+str(self.root/'firmware/main'),str(p/'test.c'),
                str(self.root/'firmware/core/pocket_live_stream.c'),str(self.root/'firmware/main/pocket_live_client.c'),
                str(self.root/'firmware/main/pocket_live_worker.c'),'-o',str(p/'test')]
            r=subprocess.run(args,capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test'),str(n)],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_concurrent_capture_render_and_confirmed_stop(self):self.run_case(1)
    def test_startup_deadline_without_audio(self):self.run_case(2)
    def test_duplicate_ready_is_terminal(self):self.run_case(3)
    def test_disconnect_is_not_confirmation(self):self.run_case(4)
    def test_unfinalized_close_is_not_success(self):self.run_case(5)
    def test_partial_audio_start_is_cleaned(self):self.run_case(6)
    def test_cleanup_failure_retains_ownership_warning(self):self.run_case(7)
    def test_connect_failure_closes_partial_transport(self):self.run_case(8)
    def test_send_failure_is_not_retried(self):self.run_case(9)
    def test_preexisting_stop_has_no_side_effects(self):self.run_case(10)

    def test_inflight_audio_after_stop_remains_pending(self):self.run_case(11)

    def test_stop_during_capture_does_not_send_completed_frame(self):self.run_case(12)
