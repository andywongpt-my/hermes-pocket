"""Compile the actual feature-gated voice owner block against bounded SDK fakes."""
from pathlib import Path
import subprocess,tempfile,unittest
PRE=r'''
#include "pocket_offline_diagnostic.h"
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <assert.h>
#include <stdlib.h>
#define CONFIG_POCKET_OFFLINE_DIAGNOSTIC 1
#define ESP_OK 0
#define ESP_ERR_NOT_FOUND 1
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
typedef int esp_err_t;
typedef struct {uint32_t epoch,sequence,sample_offset,dma_consumed;uint8_t pcm[960];} bsp_audio_capture_frame_t;
static int mux;static struct {bool busy,recording,playing;} state;
static atomic_bool audio_unavailable;
static void *commands=(void*)1;
static bool held,cancelled;
static uint64_t now;static unsigned lease,releases,queued,closed,frames,begins,ends;static int mode;
static int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
static bool pocket_setup_voice_acquire(void){if(mode==1)return false;lease++;return true;}
static void pocket_setup_voice_release(void){assert(lease);lease--;releases++;}
static void pocket_reader_close(void){closed++;}
static int xQueueSend(void*q,const void*p,unsigned t){assert(q&&t==0);queued=*(const unsigned*)p;return mode==2?0:1;}
static void vTaskDelay(unsigned n){now+=n;}
static int bsp_audio_diagnostic_begin(uint32_t epoch,bool*cleanup){assert(epoch==1&&lease==1&&state.busy&&!state.playing);begins++;*cleanup=mode!=6;return mode==6?-1:0;}
static int bsp_audio_diagnostic_end(void){ends++;return mode==3?-1:0;}
static int bsp_audio_reference_poll(bsp_audio_capture_frame_t*f){now+=10;*f=(bsp_audio_capture_frame_t){.epoch=1,.sequence=frames,.sample_offset=frames*240};frames++;return 0;}
'''
POST=r'''
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);now=60000;
 assert(pocket_voice_diagnostic_arm(now,5));assert(!pocket_voice_diagnostic_active());
 pocket_voice_diagnostic_button(true,true,59999,6);assert(!pocket_voice_diagnostic_begin(59999,6));
 now=60001;pocket_voice_diagnostic_button(true,true,now,6);
 bool admitted=pocket_voice_diagnostic_begin(now,6);
 if(mode==1||mode==2){assert(!admitted&&!state.busy&&!lease&&!begins);return 0;}
 assert(admitted&&queued==2&&lease==1&&closed==1&&pocket_voice_diagnostic_active());
 if(mode==4)now=70000;
 if(mode==5)pocket_voice_diagnostic_button(false,true,60002,7);
 diagnostic_run();
 pocket_offline_diagnostic_snapshot s;pocket_voice_diagnostic_read(&s);
 assert(!state.playing&&!state.recording);assert(!pocket_voice_diagnostic_arm(now,10));
 if(mode==3||mode==6){assert(state.busy&&lease&&audio_unavailable&&diagnostic_quarantined&&pocket_voice_diagnostic_active()&&s.phase==PDIAG_CLEANUP_FAILED);}
 else {assert(!state.busy&&!lease&&!pocket_voice_diagnostic_active());assert(s.phase==(mode?PDIAG_ERROR:PDIAG_DONE));}
 if(mode==4||mode==5)assert(!begins&&!ends&&!s.restore_ok&&!s.cleanup_attempted);
 if(mode==6)assert(begins==1&&!ends&&!s.cleanup_attempted);
 return 0;}
'''
class OfflineVoiceTests(unittest.TestCase):
 def test_actual_voice_owner_block(self):
  root=Path(__file__).resolve().parents[1];text=(root/'firmware/main/pocket_voice.c').read_text();start=text.index('#if CONFIG_POCKET_OFFLINE_DIAGNOSTIC\nstatic pocket_offline_diagnostic_control');end=text.index('\n#endif',start)+len('\n#endif');block=text[start:end]
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(PRE+block+POST)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(root/'firmware/core'),str(p/'test.c'),str(root/'firmware/core/pocket_offline_diagnostic.c'),'-o',str(p/'test')],check=True)
   for mode in range(7):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
 def test_worker_branch_and_default_guards(self):
  root=Path(__file__).resolve().parents[1];s=(root/'firmware/main/pocket_voice.c').read_text();dispatch=s.index('if(target==2){diagnostic_run();continue;}');self.assertLess(dispatch,s.index('unsigned choice=pending_model_choice',dispatch));self.assertLess(dispatch,s.index('esp_fill_random',dispatch));self.assertIn('if(diagnostic_quarantined){vTaskDelay',s);self.assertIn('if(target>1 ||',s)
