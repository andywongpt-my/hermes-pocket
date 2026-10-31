"""Actual private v2 context construction/teardown with no SDK I/O."""
from pathlib import Path
import tempfile,subprocess,unittest
HARNESS=r'''
#define POCKET_LIVE_V2_IDF_INTERNAL 1
#include "pocket_live_v2_idf.h"
#include "pocket_live_v2_control.h"
#include <assert.h>
#include <string.h>
static unsigned opens,starts,stops,closes,worker_calls;static bool pending;
bool pocket_live_network_open_begin(pocket_live_network_context*c,const pocket_live_network_config*k){opens++;assert(!strcmp(k->host,"example.test")&&!strcmp(k->path,"/targets/linux/live"));c->phase=NET_DNS;return true;}
int pocket_live_network_open_step(pocket_live_network_context*c){c->phase=NET_OPEN;return 1;}
bool pocket_live_network_send(pocket_live_network_context*c,bool b,const uint8_t*p,size_t n){(void)c;(void)b;(void)p;(void)n;return true;}
int pocket_live_network_read(pocket_live_network_context*c,uint8_t*p,size_t n,pocket_live_network_message*m){(void)c;(void)p;(void)n;(void)m;return 0;}
bool pocket_live_network_close(pocket_live_network_context*c){closes++;c->cleanup_complete=!pending;c->phase=pending?NET_CLOSING:NET_CLOSED;return !pending;}
bool pocket_live_v2_audio_start(pocket_live_v2_audio_context*c){assert(c->epoch==7&&c->volume==60&&c->stopped&&c->stop_context&&!c->stopped(c->stop_context));starts++;c->started=true;return true;}
int pocket_live_v2_audio_capture(pocket_live_v2_audio_context*c,pocket_live_v2_capture*p){(void)c;(void)p;return 0;}
int pocket_live_v2_audio_submit(pocket_live_v2_audio_context*c,uint32_t o,const uint8_t*p,size_t n){(void)c;(void)o;(void)p;(void)n;return 1;}
bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context*c){stops++;c->started=false;return true;}
bool pocket_live_v2_control(const char*p,size_t n,pocket_live_worker_event*e){(void)p;(void)n;(void)e;return false;}
int64_t esp_timer_get_time(void){return 100000;}
void vTaskDelay(unsigned ticks){(void)ticks;}
pocket_live_v2_worker_result pocket_live_v2_worker_run(pocket_live_v2_worker*w,const pocket_live_v2_worker_ops*o,const char*chat,uint32_t epoch){worker_calls++;assert(w&&o->context&&epoch==7&&!strcmp(chat,"chat_1"));assert(!w->capture.pcm[0]);w->capture.pcm[0]=77;assert(o->connect(o->context)&&o->audio_start(o->context));assert(o->audio_stop(o->context));bool closed=o->close(o->context);return(pocket_live_v2_worker_result){.reason=PLW_CONNECTION_LOST,.cleanup_complete=closed};}
static bool stopped(void*v){return *(bool*)v;}
int main(void){pocket_live_v2_idf_context c={0};pocket_live_session_config config={0};bool stop=false,acquired=false;assert(pocket_live_session_config_snapshot(&config,"https://example.test","abcdefghijklmnopqrstuvwxyz123456","","chat_1",1,7,60));assert(pocket_live_v2_idf_prepare(&c,&config,stopped,&stop,&acquired));assert(acquired&&!opens&&!starts&&!stops&&!closes);assert(c.audio.stopped==stopped&&c.audio.stop_context==&stop);stop=true;assert(c.audio.stopped(c.audio.stop_context));stop=false;strcpy(config.host,"mutated");assert(!strcmp(c.snapshot.host,"example.test"));assert(!pocket_live_v2_idf_prepare(&c,&config,stopped,&stop,&acquired)&&!acquired);pocket_live_v2_worker_ops ops={0};assert(pocket_live_v2_idf_take_ops(&c,&ops));assert(ops.context==&c);assert(!pocket_live_v2_idf_take_ops(&c,&ops));assert(ops.connect(&c)&&ops.audio_start(&c));pending=true;assert(!pocket_live_v2_idf_cleanup(&c));assert(!pocket_live_v2_idf_scrub(&c));assert(c.snapshot.token[0]&&stops==1&&closes==1);pending=false;assert(pocket_live_v2_idf_cleanup(&c));assert(stops==1&&closes==2);assert(pocket_live_v2_idf_scrub(&c));assert(!c.snapshot.token[0]&&!c.config.token&&!c.prepared&&c.retired);assert(!pocket_live_v2_idf_prepare(&c,&config,stopped,&stop,&acquired));pocket_live_v2_idf_context c2={0};pocket_live_v2_worker worker;memset(&worker,0xa5,sizeof worker);assert(pocket_live_session_config_snapshot(&config,"https://example.test","abcdefghijklmnopqrstuvwxyz123456","","chat_1",1,7,60));assert(pocket_live_v2_idf_prepare(&c2,&config,stopped,&stop,&acquired));pending=true;pocket_live_v2_worker_result r=pocket_live_v2_idf_execute(&c2,&worker);assert(!r.cleanup_complete&&worker_calls==1&&worker.capture.pcm[0]==77);r=pocket_live_v2_idf_execute(&c2,&worker);assert(r.reason==PLW_INVALID_ARGUMENT&&worker_calls==1&&!r.cleanup_complete);assert(!pocket_live_v2_idf_scrub(&c2)&&worker.capture.pcm[0]==77);pending=false;assert(pocket_live_v2_idf_cleanup(&c2));assert(pocket_live_v2_idf_scrub(&c2));for(size_t i=0;i<sizeof worker;i++)assert(((uint8_t*)&worker)[i]==0);return 0;}
'''
class ContextTests(unittest.TestCase):
 def test_actual_context(self):
  root=Path(__file__).resolve().parents[1];main=root/'firmware/main'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)
   for name,body in {'esp_err.h':'#pragma once\ntypedef int esp_err_t;\n','esp_transport.h':'#pragma once\ntypedef void* esp_transport_handle_t;\n','esp_transport_ws.h':'#pragma once\n#define WS_TRANSPORT_OPCODES_BINARY 2\n#define WS_TRANSPORT_OPCODES_TEXT 1\n','esp_timer.h':'#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n','freertos/FreeRTOS.h':'#pragma once\n','freertos/task.h':'#pragma once\nvoid vTaskDelay(unsigned);\n'}.items():
    f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(body)
   (p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(p),'-I',str(main),'-I',str(root/'firmware/core'),'-I',str(root/'firmware/components/bsp/include'),str(p/'test.c'),str(main/'pocket_live_v2_idf.c'),str(main/'pocket_live_session_config.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
