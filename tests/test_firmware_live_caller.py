"""Normal caller gate and actual voice admission block with SDK/app fakes."""
from pathlib import Path
import subprocess,tempfile,unittest
PRE=r'''
#include "pocket_live_app.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdatomic.h>
#define CONFIG_POCKET_LIVE_APP_CALLER 1
#define CONFIG_POCKET_OFFLINE_DIAGNOSTIC 0
#define pdTRUE 1
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
static int mux;static struct {bool busy,recording,playing;unsigned target,volume;char message[128];} state={.volume=60};
static atomic_bool audio_unavailable;static bool held,cancelled;static void*commands=(void*)1;
static bool ready;static uint64_t clock_ms=100;static unsigned reserves,releases,queues,closes,creates,executes,destroys,links;static int mode;
typedef struct{char origin[193],token[129],ca[2049];}pocket_link_config;
static int64_t esp_timer_get_time(void){return (int64_t)clock_ms*1000;}
static void message(const char*p){snprintf(state.message,sizeof state.message,"%s",p);}
bool pocket_live_v2_device_admission_allowed(void){return ready;}
static bool pocket_setup_voice_acquire(void){reserves++;return mode!=1;}
static void pocket_setup_voice_release(void){releases++;}
static void pocket_reader_close(void){closes++;}
static int xQueueSend(void*q,const void*p,unsigned t){assert(q&&*(const unsigned*)p==3&&!t);queues++;return mode==2?0:1;}
static bool pocket_setup_link(unsigned target,pocket_link_config*l){links++;assert(target==1);strcpy(l->origin,"https://example.test");strcpy(l->token,"abcdefghijklmnopqrstuvwxyz123456");l->ca[0]=0;return mode!=3;}
struct pocket_live_app{pocket_live_app_snapshot s;pocket_live_app_owner o;};static struct pocket_live_app app;
pocket_live_app*pocket_live_app_create(const pocket_live_app_request*r,const pocket_live_app_owner*o){creates++;assert(!strcmp(r->chat_id,"pocket")&&r->volume==60&&r->target==1&&r->deadline_ms==10100);if(mode==4)return NULL;app.o=*o;app.s=(pocket_live_app_snapshot){.phase=PLS_RESERVED};return &app;}
bool pocket_live_app_execute(pocket_live_app*a){executes++;assert(a->o.held(a->o.context));a->s.worker_ran=true;a->s.worker.reason=PLW_CONNECTION_LOST;a->s.backend_status_known=mode==11||mode==12;a->s.worker.backend_pending=mode==11||mode==13;a->s.worker.finalized=mode==12;a->s.phase=(mode==5||mode==6)?PLS_CLEANUP_PENDING:PLS_RELEASED;a->s.network_cleanup_pending=mode==5;if(a->s.phase==PLS_RELEASED)assert(a->o.release(a->o.context));return true;}
bool pocket_live_app_reap(pocket_live_app*a){if(mode==5){a->s.phase=PLS_RELEASED;a->s.network_cleanup_pending=false;assert(a->o.release(a->o.context));return true;}return false;}
void pocket_live_app_read(const pocket_live_app*a,pocket_live_app_snapshot*out){*out=a->s;}
bool pocket_live_app_destroy(pocket_live_app*a){assert(a->s.phase==PLS_RELEASED);destroys++;return true;}
'''
POST=r'''
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);uint32_t generation=pocket_voice_live_button(true);
 if(mode==0){assert(!pocket_voice_live_begin(1,100,generation));assert(!reserves&&!queues&&!creates&&!links&&!state.busy);assert(strstr(state.message,"validation"));return 0;}
 ready=true;if(mode==14||mode==15||mode==16){if(mode==14)(void)pocket_voice_live_button(true);if(mode==15)pocket_voice_live_input_lost();if(mode==16){live_generation=UINT32_MAX;generation=pocket_voice_live_button(true);}assert(!pocket_voice_live_begin(1,100,generation)&&!reserves);return 0;}
 if(mode==10){state.busy=true;pocket_link_config l={0};live_run(&l);assert(state.busy&&!releases&&!links&&!creates);return 0;}
 if(mode==7){clock_ms=1101;assert(!pocket_voice_live_begin(1,100,generation)&&!reserves);return 0;}
 bool admitted=pocket_voice_live_begin(1,100,generation);if(mode==1||mode==2){assert(!admitted&&!state.busy);if(mode==11)assert(strstr(state.message,"work pending"));
 if(mode==12)assert(strstr(state.message,"backend finalized"));
 if(mode==13)assert(strstr(state.message,"state unknown"));
 assert(!pocket_voice_live_begin(1,100,generation));return 0;}
 assert(admitted&&state.busy&&queues==1&&pocket_voice_live_active());state.volume=99;
 if(mode==8)pocket_voice_live_button(true);
 if(mode==9)clock_ms=10100;
 pocket_link_config link={0};live_run(&link);assert(!link.token[0]);
 if(mode==5||mode==6){assert(state.busy&&pocket_voice_live_cleanup_pending()&&!releases&&!destroys);if(mode==5){clock_ms+=250;live_progress();assert(!state.busy&&destroys==1&&releases==1);}else{clock_ms+=250;live_progress();assert(state.busy&&!releases&&!destroys);}}
 else assert(!state.busy&&releases==1);
 if(mode==8||mode==9)assert(!links&&!creates&&!executes);
 if(mode==11)assert(strstr(state.message,"work pending"));
 if(mode==12)assert(strstr(state.message,"backend finalized"));
 if(mode==13)assert(strstr(state.message,"state unknown"));
 assert(!pocket_voice_live_begin(1,100,generation));return 0;}
'''
class CallerTests(unittest.TestCase):
 def test_actual_voice_admission(self):
  root=Path(__file__).resolve().parents[1];main=root/'firmware/main';text=(main/'pocket_voice.c').read_text();start=text.index('#if CONFIG_POCKET_LIVE_APP_CALLER\nstatic atomic_bool live_stop');end=text.index('\n#endif /* CONFIG_POCKET_LIVE_APP_CALLER */',start)+len('\n#endif /* CONFIG_POCKET_LIVE_APP_CALLER */')
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(PRE+text[start:end]+POST)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(main),'-I',str(root/'firmware/core'),str(p/'test.c'),'-o',str(p/'test')],check=True)
   for mode in range(17):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
 def test_production_admission_always_false(self):
  root=Path(__file__).resolve().parents[1];main=root/'firmware/main'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text('#include "pocket_live_admission.h"\n#include <assert.h>\nint main(void){assert(!pocket_live_v2_device_admission_allowed());return 0;}')
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-DCONFIG_POCKET_LIVE_APP_CALLER=1','-I',str(main),str(p/'test.c'),str(main/'pocket_live_admission.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
