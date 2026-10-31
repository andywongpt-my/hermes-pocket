"""Extracted actual Stage A voice glue with real control/core, fake owner/USB/audio."""
from pathlib import Path
import subprocess,tempfile,unittest
HARNESS=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_qualification.h"
#include "pocket_qualification_control.h"
typedef struct {uint32_t epoch,sequence,sample_offset,dma_consumed;uint8_t pcm[960];}pocket_live_v2_capture;
typedef struct {uint32_t epoch;unsigned volume;bool(*stopped)(void*);void*stop_context;bool attempted,started,ready,cleanup_required,unavailable,output_enabled;}pocket_live_v2_audio_context;
static void fake_heartbeat(void);
void pocket_voice_qualification_input_lost(void);
static uint64_t ms=100;static unsigned acquired,released,starts,ends,submits,writes,export_ends,queued,frames;static int mode;static bool usb_owned;
static int mux,commands=1;static struct{bool busy,recording,playing;unsigned volume;}state={.volume=60};static bool held,cancelled;static atomic_bool audio_unavailable;
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
static int64_t esp_timer_get_time(void){return (int64_t)ms*1000;}
static void vTaskDelay(unsigned n){ms+=n;}
static bool pocket_setup_voice_acquire(void){acquired++;return mode!=1;}
static void pocket_setup_voice_release(void){released++;}
static void pocket_reader_close(void){}
static int xQueueSend(int q,const void*p,int t){(void)q;(void)t;queued=*(const unsigned*)p;return mode==2?0:1;}
static bool pocket_qualification_usb_available(void){return true;}
static bool pocket_qualification_usb_export_begin(void){if(mode==28)return false;usb_owned=true;return true;}
static int pocket_qualification_usb_export_ready(void){return mode==13||(mode==10&&ends)?0:1;}
static void pocket_qualification_usb_export_end(void){usb_owned=false;export_ends++;}
static int pocket_qualification_usb_read(uint8_t*p,size_t n){(void)p;(void)n;return 0;}
static int pocket_qualification_usb_write(const uint8_t*p,size_t n){pocket_qualification_packet v;assert(usb_owned&&pocket_qualification_decode(p,n,&v)==PQ_WIRE_OK);writes++;if(mode==33&&v.kind==PQ_TLS_TERMINAL)assert(pocket_qualification_get_u32(v.payload+8)==2);if(mode==31&&v.kind==PQ_TLS_TERMINAL){assert(pocket_qualification_get_u32(v.payload+8)==0&&pocket_qualification_get_u32(v.payload+56)==1);}if(mode==32&&v.kind==PQ_TLS_TERMINAL)assert(pocket_qualification_get_u32(v.payload+56)==1);if(mode==25&&v.kind==PQ_TLS_START)return -1;if(mode==26&&v.kind==PQ_TLS_TERMINAL)return -1;return 1;}
static bool pocket_live_v2_audio_start(pocket_live_v2_audio_context*a){starts++;if(mode==11){pocket_voice_qualification_input_lost();return false;}if(mode==12){a->attempted=true;return false;}assert(a->volume==((mode==7)?5:0));a->attempted=true;a->cleanup_required=true;return true;}
static int pocket_live_v2_audio_capture(pocket_live_v2_audio_context*a,pocket_live_v2_capture*f){ms+=10;if(mode>=6&&frames%10==0)fake_heartbeat();*f=(pocket_live_v2_capture){.epoch=a->epoch,.sequence=frames,.sample_offset=frames*240};frames++;return 1;}
static int pocket_live_v2_audio_submit(pocket_live_v2_audio_context*a,uint32_t o,const uint8_t*p,size_t n){(void)a;(void)o;(void)p;assert(n==480);submits++;return 1;}
static bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context*a){(void)a;ends++;return mode!=3;}
static void*fake_alloc(size_t n,size_t z){if(mode==30)ms+=501;return mode==9?NULL:calloc(n,z);}
#define calloc fake_alloc

#include "pocket_live_session_config.h"
typedef unsigned pocket_qualification_monitor_stage;
#define PLW_INVALID_ARGUMENT 2
#define PLW_CLEANUP_FAILED 10
typedef struct{uint8_t profile;uint64_t request_ms,dispatch_deadline_ms;uint32_t duration_ms;bool(*stop)(void*);void*stop_context;uint64_t(*clock_ms)(void*);void*clock_context;void(*monitor)(void*,pocket_qualification_monitor_stage);void*monitor_context;}pocket_qualification_transport_options;
typedef struct{unsigned reason;bool cleanup_complete,terminal_received,finalized,backend_pending;}fake_result;
typedef struct{fake_result result;uint32_t accepted_source_samples,capture_frames,capture_samples,dma_consumed;uint64_t stop_observed_ms,audio_stop_begin_ms,audio_stop_end_ms;bool cleanup_complete,audio_cleanup_complete,network_cleanup_complete,audio_attempted,network_cleanup_pending,source_rejected,clock_regressed,deadline_reached,worker_ran;}pocket_qualification_transport_snapshot;
typedef struct{pocket_qualification_transport_snapshot s;pocket_qualification_transport_options o;}pocket_qualification_transport;
static unsigned tls_prepares,tls_runs,tls_cleans,tls_scrubs;
static bool pocket_qualification_transport_prepare(pocket_qualification_transport*t,const pocket_live_session_config*k,const pocket_qualification_transport_options*o,bool*a){assert(!strcmp(k->chat_id,"qualification")&&!strcmp(k->host,"10.0.0.1")&&k->volume==0&&o->profile==PQ_SILENT&&o->request_ms==100);tls_prepares++;*a=mode!=23;t->o=*o;return mode!=23&&mode!=24;}
static bool pocket_qualification_transport_run(pocket_qualification_transport*t){tls_runs++;t->s.audio_attempted=mode!=31;t->s.audio_cleanup_complete=mode!=22&&mode!=32;t->s.network_cleanup_pending=mode==21;t->s.cleanup_complete=mode==20||mode==26||mode==31||mode==33;t->s.network_cleanup_complete=t->s.cleanup_complete||mode==32;t->s.worker_ran=true;t->s.result.reason=1;t->o.monitor(t->o.monitor_context,1);return true;}
static void pocket_qualification_transport_read(const pocket_qualification_transport*t,pocket_qualification_transport_snapshot*s){*s=t->s;}
static bool pocket_qualification_transport_cleanup(pocket_qualification_transport*t){tls_cleans++;t->s.cleanup_complete=true;t->s.network_cleanup_pending=false;return true;}
static bool pocket_qualification_transport_scrub(pocket_qualification_transport*t){(void)t;tls_scrubs++;return mode!=33;}
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_8BIT 2
static size_t heap_caps_get_free_size(unsigned x){(void)x;return 10000;}
static size_t heap_caps_get_minimum_free_size(unsigned x){(void)x;return 9000;}
static size_t heap_caps_get_largest_free_block(unsigned x){(void)x;return 8000;}
static unsigned uxTaskGetStackHighWaterMark(void*x){(void)x;return 1000;}
#include "pocket_qualification_voice.inc"
#undef calloc
static void fake_heartbeat(void){uint8_t b[32];pocket_qualification_packet q={.nonce=9,.kind=PQ_KEEPALIVE,.profile=(mode==7||mode==8)?PQ_AUDIO:PQ_SILENT,.sequence=qualification_control.state.host_sequence+1};size_t n=pocket_qualification_encode(&q,b,sizeof b);assert(pocket_qualification_control_feed(&qualification_control,b,n,ms));}
static void host_tls_arm(void){uint8_t p[1024],b[1056];const char*host="10.0.0.1",*token="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",*ca="-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n";size_t h=strlen(host),t=strlen(token),a=strlen(ca);uint32_t v[]={24000,60000,1440000,1024,3145728,443,60000,(uint32_t)(h|(t<<16)),(uint32_t)a};for(unsigned i=0;i<9;i++)pocket_qualification_put_u32(p+4*i,v[i]);memcpy(p+36,host,h);memcpy(p+36+h,token,t);memcpy(p+36+h+t,ca,a);pocket_qualification_packet q={.nonce=9,.kind=PQ_ARM_TLS,.profile=PQ_SILENT,.payload=p,.bytes=36+h+t+a};size_t n=pocket_qualification_encode(&q,b,sizeof b);for(size_t off=0;off<n;){size_t z=n-off>256?256:n-off;assert(pocket_qualification_control_feed(&qualification_control,b+off,z,ms));off+=z;}q.kind=PQ_KEEPALIVE;q.sequence=1;q.payload=NULL;q.bytes=0;n=pocket_qualification_encode(&q,b,sizeof b);assert(pocket_qualification_control_feed(&qualification_control,b,n,ms));}
static void host_arm(void){uint8_t p[16],b[100];uint32_t v[]={24000,(mode==7||mode==8)?2400:1000,1024,3145728};for(unsigned i=0;i<4;i++)pocket_qualification_put_u32(p+4*i,v[i]);pocket_qualification_packet q={.nonce=9,.kind=PQ_ARM,.profile=(mode==7||mode==8)?PQ_AUDIO:PQ_SILENT,.payload=p,.bytes=16};size_t n=pocket_qualification_encode(&q,b,sizeof b);assert(pocket_qualification_control_feed(&qualification_control,b,n,ms));q.kind=PQ_KEEPALIVE;q.sequence=1;q.payload=NULL;q.bytes=0;n=pocket_qualification_encode(&q,b,sizeof b);assert(pocket_qualification_control_feed(&qualification_control,b,n,ms));}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);if(mode==8)state.volume=0;qualification_init();if(mode>=20)host_tls_arm();else host_arm();uint32_t g=pocket_voice_qualification_button(true,true,false,&ms);assert(pocket_voice_qualification_arm(ms,g));pocket_voice_qualification_button(true,false,true,&ms);g=pocket_voice_qualification_button(true,true,false,&ms);if(mode==4)pocket_voice_qualification_input_lost();bool ok=pocket_voice_qualification_begin(ms,g);if(mode==1||mode==2||mode==4){assert(!ok&&!starts);return 0;}assert(ok&&queued==4);if(mode==5)pocket_voice_qualification_button(false,true,false,&ms);if(mode==27){uint8_t h='H';assert(pocket_qualification_control_feed(&qualification_control,&h,1,ms));}if(mode==29)ms+=10000;qualification_run();if(mode>=23){if(mode==33)assert(tls_runs==1&&!released&&pocket_voice_qualification_locked());if(mode==31)assert(tls_runs==1&&released==1&&!qualification_control.state.cleanup_verified);if(mode==32)assert(tls_runs==1&&!released&&pocket_voice_qualification_locked());if(mode==23)assert(tls_prepares==1&&!tls_runs&&released==1&&writes==2);if(mode==24)assert(tls_prepares==1&&!tls_runs&&!released&&writes==2&&pocket_voice_qualification_locked());if(mode==25)assert(!tls_prepares&&!tls_runs&&released==1&&writes==1);if(mode==26)assert(tls_runs==1&&released==1&&writes==2&&qualification_control.state.code==PQ_EMIT_ERROR);if(mode==27||mode==28||mode==29||mode==30)assert(!tls_prepares&&!tls_runs&&released==1&&!writes);return 0;}if(mode>=20){assert(tls_prepares==1&&tls_runs==1&&!starts&&writes==2);if(mode==20)assert(released==1&&tls_scrubs==1);if(mode==21){assert(!released);ms+=250;qualification_progress();assert(released==1&&tls_cleans==1&&tls_scrubs==1&&writes==2);}if(mode==22)assert(!released&&!tls_cleans&&pocket_voice_qualification_locked()&&usb_owned);return 0;}if(mode!=7&&mode!=8)assert(!submits);if(mode==9||mode==13){assert(!starts&&released==1);return 0;}if(mode==11){assert(starts==1&&!ends&&released==1&&qualification_control.state.safe_release&&!qualification_control.state.cleanup_verified);return 0;}if(mode==12){assert(starts==1&&!ends&&!released&&pocket_voice_qualification_locked()&&usb_owned);return 0;}if(mode==5){assert(!starts&&released==1);return 0;}/* No fake host heartbeat after500ms: bounded stop, never fabricated completion. */assert(starts==1&&ends==1&&writes>0);if(mode==6||mode==7||mode==8||mode==10){assert(frames==((mode==7||mode==8)?2400:1000));assert(qualification_control.state.code==(mode==10?PQ_EMIT_ERROR:PQ_COMPLETE));}if(mode==3){assert(pocket_voice_qualification_locked()&&state.busy&&!released&&usb_owned&&!export_ends);}else assert(!state.busy&&released==1&&!usb_owned&&export_ends==1);return 0;}
'''
class QualificationVoiceTests(unittest.TestCase):
 def test_actual_glue(self):
  root=Path(__file__).resolve().parents[1];core=root/'firmware/core';main=root/'firmware/main'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'t.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(core),'-I',str(main),str(p/'t.c'),str(core/'pocket_qualification.c'),str(core/'pocket_qualification_protocol.c'),str(core/'pocket_qualification_control.c'),str(main/'pocket_live_session_config.c'),'-o',str(p/'t')],check=True)
   for mode in list(range(14))+[20,21,22,23,24,25,26,27,28,29,30,31,32,33]:subprocess.run([str(p/'t'),str(mode)],check=True,timeout=3)
