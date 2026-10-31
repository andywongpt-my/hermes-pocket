"""Source-only paired v2 worker orchestration with no physical/network I/O."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
HARNESS=r'''

#include "pocket_live_v2_worker.h"
#include <assert.h>
#include <string.h>
#include <stdlib.h>
typedef struct { unsigned now, receives, sent, starts, stops, closes, captures, binary_sent,capture_frames,submits,accepted; int mode; bool stopped; } fake;
static bool connect_(void *v){fake *f=v; f->starts++; if(f->mode==24)f->now=10001; return f->mode!=8;}
static bool send_(void *v,bool binary,const uint8_t *p,size_t n){fake *f=v;assert(p&&n);f->sent++;if(f->mode==17&&!binary&&f->sent==1)f->now=9999;if(f->mode==25&&!binary&&f->sent==1)f->now=10001;if(f->mode==28&&!binary&&f->sent==4)f->now+=3001;if(binary){f->binary_sent++;assert(n==976&&p[2]==2&&p[3]==1);}return f->mode!=9;}
static bool receive_(void *v,pocket_live_worker_event *e,uint8_t *p,size_t cap){
 fake *f=v; f->now+=10; f->receives++; e->kind=PLW_NONE;
 if(f->mode==17&&f->receives==1)f->now=10001;
 if(f->mode==18&&f->receives==2)f->now=300001;
 if(f->mode==19&&f->receives==1)f->stopped=true;
 if(f->mode==23&&f->receives==2)f->now=0;
 if(f->mode==26&&f->receives==4)f->now+=3000;
 if(f->mode==2)return true;
 if(f->mode==16&&f->receives>=3){
  if(f->receives==5)f->stopped=true;
  if(f->receives==6){e->kind=PLW_CLOSED;e->finalized=true;}
  return true;
 }
 if(f->receives==1){e->kind=PLW_READY;return true;}
 if(f->mode==3&&f->receives==2){e->kind=PLW_READY;return true;}
 if(f->mode==4&&f->receives==2){e->kind=PLW_LOST;return true;}
 if(f->receives==2){
  uint8_t pcm[960]={0}; pocket_live_v2_packet q={2,0,0,0,pcm,sizeof pcm};
  assert(pocket_live_v2_pack(&q,p,cap,&e->bytes)==POCKET_LIVE_V2_OK);e->kind=PLW_AUDIO;return true;
 }
 if(f->receives==3){f->stopped=true;return true;}
 if(f->mode==11&&f->receives==4){
  uint8_t pcm[960]={0};pocket_live_v2_packet q={2,1,480,0,pcm,sizeof pcm};
  assert(pocket_live_v2_pack(&q,p,cap,&e->bytes)==POCKET_LIVE_V2_OK);e->kind=PLW_AUDIO;return true;
 }
 if(f->mode==11&&f->receives==5){e->kind=PLW_CLOSED;e->finalized=true;return true;}
 if(f->receives==4){e->kind=PLW_CLOSED;e->finalized=f->mode!=5;e->backend_pending=true;e->playback_pending=true;}
 return true;
}
static bool audio_start(void *v){fake*f=v;f->captures++;if(f->mode==20)f->now=300001;if(f->mode==31)f->stopped=true;return f->mode!=6;}
static int capture(void*v,pocket_live_v2_capture *p){fake*f=v;
 if(f->mode==12)f->stopped=true;
 if(f->mode==21)f->now=300001;
 *p=(pocket_live_v2_capture){.epoch=7,.sequence=f->capture_frames,.sample_offset=f->capture_frames*240,
  .dma_consumed=(f->mode==11&&f->receives>=2)?480u:0u};
 if(f->mode==13)p->epoch=8;
 if(f->mode==14)p->sample_offset++;
 if(f->mode==15)p->dma_consumed=1;
 memset(p->pcm,0,sizeof p->pcm);f->capture_frames++;if(f->mode==18&&f->capture_frames==1)f->now=299999;return 1;
}
static int submit(void*v,uint32_t offset,const uint8_t*p,size_t n){fake*f=v;assert(offset==0&&p&&n==960);f->submits++;if(f->mode==22)f->stopped=true;if(f->mode==29)f->now=300001;if(f->mode==16&&f->submits<=2)return 0;f->accepted++;return 1;}
static bool stop_audio(void*v){fake*f=v;f->stops++;if(f->mode==27)f->now+=3001;return f->mode!=7;}
static bool close_(void*v){fake*f=v;f->closes++;return true;}
static uint64_t now(void*v){return ((fake*)v)->now;}
static bool stop(void*v){return ((fake*)v)->stopped;}
int main(int argc,char**argv){assert(argc==2);fake f={.mode=atoi(argv[1])};
 pocket_live_v2_worker w; pocket_live_v2_worker_ops o={&f,connect_,send_,receive_,audio_start,capture,submit,stop_audio,close_,now,stop};
 if(f.mode==10)f.stopped=true;
 if(f.mode==17)f.now=0;
 pocket_live_v2_worker_result r=pocket_live_v2_worker_run(&w,&o,"chat",7);
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
 else if(f.mode>=13&&f.mode<=15){assert(r.reason==PLW_AUDIO_ERROR&&!r.finalized);}
 else if(f.mode==16){assert(r.finalized&&r.playback_pending&&f.submits==3&&f.accepted==1);}
 else if(f.mode==17||f.mode==24||f.mode==25){assert(r.reason==PLW_START_TIMEOUT&&f.captures==0&&f.binary_sent==0);}
 else if(f.mode==18){assert(r.finalized&&f.submits==0&&f.capture_frames==1&&f.binary_sent==1);}
 else if(f.mode==19){assert(r.finalized&&f.captures==0&&f.binary_sent==0);}
 else if(f.mode==20){assert(r.finalized&&f.capture_frames==0&&f.binary_sent==0);}
 else if(f.mode==21){assert(r.finalized&&f.binary_sent==0&&f.capture_frames==1);}
 else if(f.mode==22){assert(r.finalized&&f.capture_frames==1&&f.binary_sent==1);}
 else if(f.mode==23){assert(r.reason==PLW_PROTOCOL_ERROR&&f.submits==0&&f.capture_frames==1);}
 else if(f.mode==26){assert(r.reason==PLW_FINALIZE_TIMEOUT&&!r.terminal_received);}
 else if(f.mode==27){assert(r.reason==PLW_FINALIZE_TIMEOUT&&f.sent==3&&f.receives==3&&!r.terminal_received);}
 else if(f.mode==28){assert(r.reason==PLW_FINALIZE_TIMEOUT&&f.receives==3&&!r.terminal_received);}
 else if(f.mode==29){assert(r.finalized&&f.capture_frames==1&&f.binary_sent==1);}
 else if(f.mode==31){assert(r.finalized&&f.capture_frames==0&&f.binary_sent==0);}
 else assert(0);
 if(f.mode==24)assert(f.sent==0);
 if(f.mode==25)assert(f.receives==0);
 assert(!r.actual_playout_known);assert(sizeof w<=8600);return 0;
}
'''
class FirmwareLiveV2WorkerTests(unittest.TestCase):
    def run_case(self,n):
        root=Path(__file__).resolve().parents[1]
        self.assertTrue((root/'firmware/main/pocket_live_v2_worker.h').exists(),'v2 worker missing')
        cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(HARNESS)
            cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2',
                '-I'+str(root/'firmware/core'),'-I'+str(root/'firmware/main'),str(p/'test.c'),
                str(root/'firmware/core/pocket_live_stream.c'),str(root/'firmware/core/pocket_live_v2_stream.c'),
                str(root/'firmware/main/pocket_live_client.c'),str(root/'firmware/main/pocket_live_v2_worker.c'),'-o',str(p/'test')]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=20);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test'),str(n)],capture_output=True,text=True,timeout=5);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
    def test_paired_capture_and_conservative_close(self):self.run_case(1)
    def test_startup_deadline_without_audio(self):self.run_case(2)
    def test_duplicate_ready_terminal(self):self.run_case(3)
    def test_disconnected_backend_unknown(self):self.run_case(4)
    def test_unfinalized_status(self):self.run_case(5)
    def test_failed_audio_start_cleanup(self):self.run_case(6)
    def test_cleanup_failure_reported(self):self.run_case(7)
    def test_connect_failure_not_retried(self):self.run_case(8)
    def test_send_failure_not_retried(self):self.run_case(9)
    def test_preexisting_stop_no_side_effect(self):self.run_case(10)
    def test_inflight_audio_keeps_pending(self):self.run_case(11)
    def test_stop_during_capture_no_upload(self):self.run_case(12)
    def test_old_capture_epoch_rejected(self):self.run_case(13)
    def test_capture_gap_rejected(self):self.run_case(14)
    def test_dma_credit_never_exceeds_submitted(self):self.run_case(15)

    def test_render_busy_retains_frame_and_capture_continues(self):self.run_case(16)

    def test_ready_return_after_startup_deadline_never_starts_audio(self):self.run_case(17)
    def test_audio_return_after_session_deadline_never_submits_or_captures(self):self.run_case(18)
    def test_stop_during_ready_receive_never_starts_audio(self):self.run_case(19)
    def test_audio_start_crossing_session_deadline_never_captures(self):self.run_case(20)
    def test_capture_crossing_session_deadline_never_uploads(self):self.run_case(21)
    def test_stop_during_submit_never_captures(self):self.run_case(22)
    def test_receive_clock_regression_never_submits(self):self.run_case(23)
    def test_connect_crossing_startup_deadline_never_sends_hello(self):self.run_case(24)
    def test_hello_crossing_startup_deadline_never_receives(self):self.run_case(25)
    def test_late_closed_does_not_override_finalize_deadline(self):self.run_case(26)

    def test_audio_stop_consumes_finalization_budget(self):self.run_case(27)
    def test_stop_send_consumes_finalization_budget(self):self.run_case(28)
    def test_submit_crossing_session_deadline_never_captures(self):self.run_case(29)
    def test_stop_during_audio_start_never_captures(self):self.run_case(31)
