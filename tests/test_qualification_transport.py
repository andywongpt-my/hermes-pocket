"""Qualification-only real IDF composition, fake I/O boundaries; no device/TLS."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#include "pocket_qualification_transport.h"
#include <assert.h>
#include <string.h>
#include <stdlib.h>
static bool zeroed(const void*p,size_t n){const unsigned char*b=p;while(n--)if(*b++)return false;return true;}
static uint64_t clock_ms=100;static bool physical_stop,network_pending,audio_fail,busy;
static unsigned opens,starts,stops,closes,submits,runs;static int mode;
static uint64_t clock_(void*v){(void)v;return clock_ms;}
static bool stop_(void*v){(void)v;return physical_stop;}
static bool prep(pocket_qualification_transport*c,const pocket_live_session_config*k,bool(*stop)(void*),void*sc,uint64_t(*clock)(void*),void*cc,uint64_t request,uint64_t dispatch,uint32_t duration,bool*acquired){
 pocket_qualification_transport_options options={.profile=PQ_AUDIO,.request_ms=request,.dispatch_deadline_ms=dispatch,.duration_ms=duration,.stop=stop,.stop_context=sc,.clock_ms=clock,.clock_context=cc};
 return pocket_qualification_transport_prepare(c,k,&options,acquired);
}

int64_t esp_timer_get_time(void){return (int64_t)clock_ms*1000;}
void vTaskDelay(unsigned n){clock_ms+=n;}
bool pocket_live_network_open_begin(pocket_live_network_context*c,const pocket_live_network_config*k){opens++;assert(!strcmp(k->path,"/qualification/v2/live"));assert(!strcmp(k->host,"192.168.1.2"));assert(k->ca_pem[0]);assert(k->stopped&&!k->stopped(k->stop_context));c->phase=NET_DNS;return true;}
int pocket_live_network_open_step(pocket_live_network_context*c){c->phase=NET_OPEN;return 1;}
bool pocket_live_network_send(pocket_live_network_context*c,bool b,const uint8_t*p,size_t n){(void)c;(void)b;(void)p;(void)n;return true;}
int pocket_live_network_read(pocket_live_network_context*c,uint8_t*p,size_t n,pocket_live_network_message*m){(void)c;(void)p;(void)n;(void)m;return 0;}
bool pocket_live_network_close(pocket_live_network_context*c){closes++;c->cleanup_complete=!network_pending;c->phase=network_pending?NET_CLOSING:NET_CLOSED;return !network_pending;}
bool pocket_live_v2_audio_start(pocket_live_v2_audio_context*c){starts++;assert(c->volume<=5&&c->stopped&&!c->stopped(c->stop_context));c->attempted=true;c->started=true;return true;}
int pocket_live_v2_audio_capture(pocket_live_v2_audio_context*c,pocket_live_v2_capture*p){(void)c;(void)p;return 0;}
int pocket_live_v2_audio_submit(pocket_live_v2_audio_context*c,uint32_t off,const uint8_t*p,size_t n){(void)c;(void)off;(void)p;(void)n;submits++;return busy?0:1;}
bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context*c){stops++;if(audio_fail)return false;c->started=false;return true;}
bool pocket_live_v2_control(const char*p,size_t n,pocket_live_worker_event*e){(void)p;(void)n;(void)e;return false;}
/* Synthetic source stub only; actual source tests cover real waveform. */
int16_t pocket_qualification_source_sample(uint32_t offset){return (offset>=24000&&offset<72000)||(offset>=96000&&offset<144000)?123:0;}
pocket_live_v2_worker_result pocket_live_v2_worker_run(pocket_live_v2_worker*w,const pocket_live_v2_worker_ops*o,const char*chat,uint32_t epoch){
 runs++;assert(w&&epoch==7&&!strcmp(chat,"qualification"));assert(o->connect(o->context)&&o->audio_start(o->context));
 uint8_t pcm[480]={0};
 if(mode==1){pcm[0]=1;assert(o->submit(o->context,0,pcm,sizeof pcm)==-1&&!submits);}
 else if(mode==2){busy=true;assert(!o->submit(o->context,0,pcm,sizeof pcm));busy=false;assert(o->submit(o->context,0,pcm,sizeof pcm)==1);assert(o->submit(o->context,0,pcm,sizeof pcm)==-1);assert(submits==2);}
 else if(mode==3){clock_ms+=60000;assert(o->stopped(o->context));assert(o->submit(o->context,0,pcm,sizeof pcm)==-1&&!submits);}
 else if(mode==4){clock_ms=99;assert(o->stopped(o->context));}
 else if(mode==5){physical_stop=true;assert(o->stopped(o->context));}
 else {assert(o->submit(o->context,0,pcm,sizeof pcm)==1);}
 bool clean=o->audio_stop(o->context);bool closed=o->close(o->context);
 memset(&w->capture,0x5a,sizeof w->capture);memset(w->receive,0x5a,sizeof w->receive);memset(w->transmit,0x5a,sizeof w->transmit);memset(&w->speaker,0x5a,sizeof w->speaker);w->microphone.next_sequence=7;
 pocket_live_v2_idf_context*idf=o->context;memset(idf->message,0x5a,sizeof idf->message);memset(idf->chunk,0x5a,sizeof idf->chunk);memset(idf->network.mutable_wire,0x5a,sizeof idf->network.mutable_wire);
 return(pocket_live_v2_worker_result){.reason=PLW_STOPPED,.cleanup_complete=clean&&closed};
}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);pocket_qualification_transport c={0};pocket_live_session_config k={0};
 assert(pocket_live_session_config_snapshot(&k,"https://192.168.1.2:8443","abcdefghijklmnopqrstuvwxyz123456","-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n","qualification",0,7,5));
 if(mode==19){k.token[0]='.';bool acquired=true;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired)&&!acquired&&!opens);return 0;}
 if(mode==20){assert(!pocket_live_v2_idf_qualification_config_valid(&k,0));assert(!pocket_live_v2_idf_qualification_config_valid(&k,PQ_SILENT));return 0;}
 if(mode==21){const char*good[]={"10.0.0.1","10.255.255.254","172.16.0.1","172.31.255.254","192.168.0.1"};for(unsigned i=0;i<sizeof good/sizeof good[0];i++){strcpy(k.host,good[i]);assert(pocket_live_v2_idf_qualification_config_valid(&k,PQ_AUDIO));}return 0;}
 if(mode==22){bool got=true;assert(!prep(&c,&k,stop_,0,clock_,0,101,1000,60000,&got)&&!got);assert(!prep(&c,&k,stop_,0,clock_,0,UINT64_MAX-10,UINT64_MAX,60000,&got));assert(!prep(&c,&k,stop_,0,clock_,0,100,10101,60000,&got));clock_ms=500;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,300,&got)&&!got);return 0;}
 if(mode==6){const char*bad[]={"127.0.0.1","8.8.8.8","172.15.0.1","172.32.0.1","192.169.1.1","10.0.0.01","10.1","10.0.0.1.evil","0xa000001","169.254.1.2"};for(unsigned i=0;i<sizeof bad/sizeof bad[0];i++){strcpy(k.host,bad[i]);bool acquired=true;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired)&&!acquired&&!opens&&!starts);}return 0;}
 if(mode==7){k.ca_pem[0]=0;bool acquired=true;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired)&&!acquired);return 0;}
 if(mode==8){k.volume=6;bool acquired=true;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired)&&!acquired);return 0;}
 if(mode==9){bool acquired=true;assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60001,&acquired)&&!acquired);return 0;}
 if(mode==16||mode==17||mode==18){k.volume=0;pocket_live_v2_idf_context direct={0};bool got=false;assert(pocket_live_v2_idf_prepare_qualification(&direct,&k,mode==18?PQ_AUDIO:PQ_SILENT,stop_,0,NULL,NULL,&got));pocket_live_v2_worker_ops ops;assert(pocket_live_v2_idf_take_ops(&direct,&ops));direct.qualification_accepted=96000;uint8_t pcm[480]={0};
 if(mode==18){for(unsigned i=0;i<240;i++)pcm[2*i]=123;assert(ops.submit(&direct,96000,pcm,sizeof pcm)==1&&submits==1);}
 else if(mode==16)assert(ops.submit(&direct,96000,pcm,sizeof pcm)==1&&submits==1);
 else {for(unsigned i=0;i<240;i++)pcm[2*i]=123;assert(ops.submit(&direct,96000,pcm,sizeof pcm)==-1&&!submits);}
 return 0;}
 if(mode==13||mode==14||mode==15){pocket_live_v2_idf_context direct={0};bool got=false;assert(pocket_live_v2_idf_prepare_qualification(&direct,&k,PQ_AUDIO,stop_,0,NULL,NULL,&got)&&got);pocket_live_v2_worker_ops ops;assert(pocket_live_v2_idf_take_ops(&direct,&ops));
 if(mode==13){clock_ms+=60000;assert(ops.stopped(&direct));}
 if(mode==14){uint8_t pcm[480];for(unsigned i=0;i<240;i++){pcm[2*i]=123;pcm[2*i+1]=0;}direct.qualification_accepted=24000;assert(ops.submit(&direct,24000,pcm,sizeof pcm)==-1&&!submits);}
 if(mode==15){uint8_t pcm[480]={0};direct.qualification_accepted=1440000;assert(ops.submit(&direct,1440000,pcm,sizeof pcm)==-1&&!submits);}
 return 0;}
 bool acquired=false;assert(prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired)&&acquired&&!opens&&!starts);strcpy(k.host,"mutated");k.token[0]='X';
 if(mode==10)clock_ms=1000;
 if(mode==11)network_pending=true;
 if(mode==12)audio_fail=true;
 assert(pocket_qualification_transport_run(&c));assert(!pocket_qualification_transport_run(&c));
 pocket_qualification_transport_snapshot s;pocket_qualification_transport_read(&c,&s);
 assert(zeroed(&c.worker.capture,sizeof c.worker.capture)&&zeroed(c.worker.receive,sizeof c.worker.receive)&&zeroed(c.worker.transmit,sizeof c.worker.transmit)&&zeroed(&c.worker.speaker,sizeof c.worker.speaker));
 assert(zeroed(c.idf.message,sizeof c.idf.message)&&zeroed(c.idf.chunk,sizeof c.idf.chunk)&&zeroed(c.idf.network.mutable_wire,sizeof c.idf.network.mutable_wire));
 if(mode!=10)assert(c.worker.microphone.next_sequence==7&&c.idf.snapshot.token[0]&&s.capture_frames==7);
 if(mode==3)assert(s.deadline_reached&&!s.source_rejected);
 if(mode==10){assert(!runs&&!opens&&!starts&&s.cleanup_complete&&s.network_cleanup_complete&&!s.audio_attempted);}
 if(mode!=10)assert(s.audio_attempted);
 if(mode==11){assert(!s.network_cleanup_complete);assert(!s.cleanup_complete&&s.network_cleanup_pending&&!pocket_qualification_transport_scrub(&c));assert(!pocket_qualification_transport_cleanup(&c));network_pending=false;assert(pocket_qualification_transport_cleanup(&c)&&stops==1);}
 if(mode==12){assert(s.network_cleanup_complete);assert(!s.cleanup_complete&&!s.network_cleanup_pending&&!pocket_qualification_transport_cleanup(&c)&&stops==1);assert(!pocket_qualification_transport_scrub(&c));return 0;}
 assert(pocket_qualification_transport_scrub(&c));assert(!c.idf.snapshot.token[0]);assert(!prep(&c,&k,stop_,0,clock_,0,100,1000,60000,&acquired));return 0;
}
'''
class QualificationTransportTests(unittest.TestCase):
 def test_private_bounded_actual_idf_composition(self):
  main=ROOT/'firmware/main'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)
   headers={'sdkconfig.h':'#define CONFIG_POCKET_QUALIFICATION 1\n','esp_err.h':'typedef int esp_err_t;\n','esp_transport.h':'typedef void* esp_transport_handle_t;\n','esp_transport_ws.h':'#define WS_TRANSPORT_OPCODES_BINARY 2\n#define WS_TRANSPORT_OPCODES_TEXT 1\n','esp_timer.h':'#include <stdint.h>\nint64_t esp_timer_get_time(void);\n','freertos/FreeRTOS.h':'','freertos/task.h':'void vTaskDelay(unsigned);\n'}
   for name,body in headers.items():
    f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#pragma once\n'+body)
   (p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-DCONFIG_POCKET_QUALIFICATION=1','-I',str(p),'-I',str(main),'-I',str(ROOT/'firmware/core'),'-I',str(ROOT/'firmware/components/bsp/include'),str(p/'test.c'),str(main/'pocket_qualification_transport.c'),str(main/'pocket_live_v2_idf.c'),str(main/'pocket_live_session_config.c'),'-o',str(p/'test')],check=True)
   for mode in range(23):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)

REAL_HARNESS=r'''
#include "pocket_qualification_transport.h"
#include <assert.h>
#include <string.h>
#include <stdlib.h>
static uint64_t now=100;static unsigned step,captures,uploads,submits,stops,closes,monitor_calls;static bool stop_sent;static int mode;
static uint64_t clock_(void*v){(void)v;return now;}
static bool stopped_(void*v){(void)v;return captures>=3;}
static void monitor_(void*v,pocket_qualification_monitor_stage stage){assert(v==(void*)1&&stage>=PQTM_AUDIO_START&&stage<=PQTM_NETWORK_CLOSE_RETURNED);monitor_calls++;if((mode==3&&stage==PQTM_SUBMIT)||(mode==4&&stage==PQTM_CAPTURE)||(mode==5&&stage==PQTM_AUDIO_START))now=60100;}

int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
void vTaskDelay(unsigned n){now+=n;}
bool pocket_live_network_open_begin(pocket_live_network_context*c,const pocket_live_network_config*k){assert(!strcmp(k->path,"/qualification/v2/live"));assert(!strcmp(k->host,"10.1.2.3"));c->phase=NET_DNS;return true;}
int pocket_live_network_open_step(pocket_live_network_context*c){c->phase=NET_OPEN;return 1;}
bool pocket_live_network_send(pocket_live_network_context*c,bool b,const uint8_t*p,size_t n){(void)c;if(b){assert(n==976);uploads++;}else if(n==15&&!memcmp(p,"{\"type\":\"stop\"}",15))stop_sent=true;return true;}
int pocket_live_network_read(pocket_live_network_context*c,uint8_t*p,size_t n,pocket_live_network_message*m){(void)c;assert(n>=1024);now+=10;m->fin=true;
 if(stop_sent){memcpy(p,"closed",6);m->opcode=1;m->payload_bytes=6;return 6;}
 if(step++==0){memcpy(p,"ready",5);m->opcode=1;m->payload_bytes=5;return 5;}
 if(step==2){uint8_t pcm[480]={0};if(mode==1)pcm[0]=1;pocket_live_v2_packet packet={.kind=2,.pcm=pcm,.pcm_bytes=sizeof pcm};size_t used=0;assert(pocket_live_v2_pack(&packet,p,n,&used)==POCKET_LIVE_V2_OK);m->opcode=2;m->payload_bytes=used;return (int)used;}
 return 0;
}
bool pocket_live_network_close(pocket_live_network_context*c){closes++;c->cleanup_complete=true;c->phase=NET_CLOSED;return true;}
bool pocket_live_v2_audio_start(pocket_live_v2_audio_context*c){assert(c->volume==0&&!c->stopped(c->stop_context));c->attempted=true;c->started=true;return true;}
int pocket_live_v2_audio_capture(pocket_live_v2_audio_context*c,pocket_live_v2_capture*p){memset(p,0,sizeof *p);p->epoch=c->epoch;p->sequence=captures;p->sample_offset=captures*240;captures++;if(mode==2)now=60100;return 1;}
int pocket_live_v2_audio_submit(pocket_live_v2_audio_context*c,uint32_t off,const uint8_t*p,size_t n){assert(c->started&&!off&&p&&n==480);submits++;return 1;}
bool pocket_live_v2_audio_stop(pocket_live_v2_audio_context*c){stops++;c->started=false;return true;}
bool pocket_live_v2_control(const char*p,size_t n,pocket_live_worker_event*e){if(n==5&&!memcmp(p,"ready",5)){e->kind=PLW_READY;return true;}if(n==6&&!memcmp(p,"closed",6)){e->kind=PLW_CLOSED;e->finalized=true;return true;}return false;}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);pocket_qualification_transport c={0};pocket_live_session_config k={0};bool acquired=false;
 assert(pocket_live_session_config_snapshot(&k,"https://10.1.2.3:8443","abcdefghijklmnopqrstuvwxyz123456","-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n","qualification",0,7,0));
 pocket_qualification_transport_options options={.profile=PQ_SILENT,.request_ms=100,.dispatch_deadline_ms=1000,.duration_ms=60000,.stop=stopped_,.clock_ms=clock_,.monitor=monitor_,.monitor_context=(void*)1};
 assert(pocket_qualification_transport_prepare(&c,&k,&options,&acquired)&&acquired&&!monitor_calls);
 options.monitor=NULL;options.duration_ms=1;
 assert(pocket_qualification_transport_run(&c));pocket_qualification_transport_snapshot s;pocket_qualification_transport_read(&c,&s);
 assert(s.cleanup_complete&&!s.result.actual_playout_known&&stops==1&&closes==1);
 if(mode==0)assert(submits==1&&uploads==2&&s.result.terminal_received&&s.result.finalized);
 if(mode==1)assert(!submits&&s.source_rejected);
 if(mode==2)assert(!submits&&!uploads&&s.deadline_reached);
 if(mode==3)assert(!submits&&s.deadline_reached);
 if(mode==4||mode==5)assert(!captures&&!uploads&&!submits&&s.deadline_reached);
 assert(monitor_calls>=3&&s.audio_cleanup_complete&&s.audio_stop_begin_ms>=100&&s.audio_stop_end_ms>=s.audio_stop_begin_ms);
 assert(pocket_qualification_transport_scrub(&c));return 0;
}
'''
class RealQualificationWorkerTests(unittest.TestCase):
 def test_actual_worker_qualified_idf_and_real_source(self):
  main=ROOT/'firmware/main';core=ROOT/'firmware/core'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)
   for name,body in {'esp_err.h':'typedef int esp_err_t;\n','esp_transport.h':'typedef void* esp_transport_handle_t;\n','esp_transport_ws.h':'#define WS_TRANSPORT_OPCODES_BINARY 2\n#define WS_TRANSPORT_OPCODES_TEXT 1\n','esp_timer.h':'#include <stdint.h>\nint64_t esp_timer_get_time(void);\n','freertos/FreeRTOS.h':'','freertos/task.h':'void vTaskDelay(unsigned);\n'}.items():
    f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#pragma once\n'+body)
   (p/'test.c').write_text(REAL_HARNESS)
   files=[main/'pocket_qualification_transport.c',main/'pocket_live_v2_idf.c',main/'pocket_live_session_config.c',main/'pocket_live_v2_worker.c',main/'pocket_live_client.c',core/'pocket_live_stream.c',core/'pocket_live_v2_stream.c',core/'pocket_qualification.c',core/'pocket_qualification_protocol.c']
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-DCONFIG_POCKET_QUALIFICATION=1','-I',str(p),'-I',str(main),'-I',str(core),'-I',str(ROOT/'firmware/components/bsp/include'),str(p/'test.c'),*map(str,files),'-o',str(p/'test')],check=True)
   for mode in range(6):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True,timeout=5)
