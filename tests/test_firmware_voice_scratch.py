"""Actual worker + cached SDK JSON/Base64; codec/RTOS/HTTP are local fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import test_firmware_voice as voice
import test_firmware_http as http

EXTRA=r'''
static const char *scenario;
static unsigned command_limit=1,capture_number,verified_chunks;
static bool limited_heap=true;
static void *json_allocate(size_t n){void *p=malloc(n);if(p)json_live++;return p;}
static void json_release(void *p){if(p){assert(json_live>0);json_live--;free(p);}}
static void output(char *out,size_t cap,const char *json){assert(strlen(json)<cap);memset(out,' ',cap);memcpy(out,json,strlen(json));out[cap-1]=0;}
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t cap,pocket_http_result *result){
 (void)origin;(void)token;(void)ca;assert(out && cap>=2);out[0]=0; /* Real helper clears output before inspecting POST. */
 memset(result,0,sizeof(*result));result->status=200;
 bool chunk=body && strstr(path,"/chunk");
 if(body){
  size_t bytes=strlen(body);uintptr_t a=(uintptr_t)body,b=(uintptr_t)out;
  assert(a+bytes+1<=b || b+cap<=a); /* Exercise the caller's actual nonoverlap contract. */
  cJSON *j=cJSON_Parse(body);assert(j && cJSON_IsObject(j));
  if(chunk){
   cJSON *seq=cJSON_GetObjectItemCaseSensitive(j,"sequence"),*data=cJSON_GetObjectItemCaseSensitive(j,"data");
   assert(cJSON_IsNumber(seq) && seq->valueint==(int)chunk_in_capture++ && cJSON_IsString(data));
   unsigned char pcm[2048];size_t bytes=0;
   assert(mbedtls_base64_decode(pcm,sizeof pcm,&bytes,(const unsigned char*)data->valuestring,strlen(data->valuestring))==0 && bytes==2048);
   unsigned seed=!strcmp(scenario,"multi")?(unsigned)seq->valueint:(unsigned)(capture_reads-1);
   for(size_t n=0;n<bytes;n++)assert(pcm[n]==(unsigned char)(n^seed));
   uploaded_bytes=bytes;verified_chunks++;chunk_attempts++;
  }
  cJSON_Delete(j);
 }
 snprintf(endpoint,sizeof endpoint,"%s",path);
 if(strstr(path,"/status")){assert(cap==8192);status_calls++;output(out,cap,"{\"backend\":\"available\",\"voice\":{\"stt\":true,\"tts\":false}}");}
 else if(chunk){
  char saved[2816];assert(strlen(body)<sizeof saved);memcpy(saved,body,strlen(body)+1);
  if(!strcmp(scenario,"transport") || !strcmp(scenario,"overflow")){
   memset(out,'X',cap);out[0]=0;result->status=-1;assert(!strcmp(saved,body));return ESP_FAIL;
  }
  bool malformed=!strcmp(scenario,"ack_error") || (!strcmp(scenario,"recover") && capture_number==1);
  /* Pad valid ACKs to the complete output range to overwrite the old frame. */
  char ack[64];snprintf(ack,sizeof ack,"{\"sequence\":%u,\"accepted_bytes\":%u}",chunk_in_capture-1,chunk_in_capture*2048);
  output(out,cap,malformed?"{":ack);
  assert(!strcmp(saved,body));
 }
 else if(strstr(path,"/finish")){assert(cap==8192);finish_calls++;output(out,cap,"{}");}
 else if(strstr(path,"/abort")){assert(cap==8192);abort_calls++;output(out,cap,"{}");}
 else if(body){assert(cap==8192);capture_number++;chunk_in_capture=0;output(out,cap,"{}");}
 else {assert(cap==8192);output(out,cap,"{\"state\":\"failed\"}");}
 result->length=strlen(out);return ESP_OK;
}
'''

MAIN=r'''
int main(int argc,char **argv){
 assert(argc==2);scenario=argv[1];command_limit=1;limited_heap=true;
 cJSON_Hooks hooks={json_allocate,json_release};cJSON_InitHooks(&hooks);
 if(!strcmp(scenario,"stalled")){
  limited_heap=false;reset(7);int outcome=setjmp(stopped);if(!outcome)network(NULL);
  assert(outcome==1 && state.busy && state.recording && queue_live==1 && live_allocations()==2);
  assert(peak_audio_bytes==queue_bytes+record_stack);
  char *scratch=NULL;for(unsigned n=0;n<16;n++)if(allocations[n] && allocation_sizes[n]==8192)scratch=allocations[n];assert(scratch);
  capture_frame producer={.size=2048};memset(producer.pcm,0x5a,sizeof producer.pcm);assert(xQueueSend(audio_queue,&producer,0)==pdTRUE);
  capture_frame *consumer=(capture_frame*)scratch;char *body=scratch+5376;
  assert(!release_capture(&consumer,&body) && !consumer && !body && queue_live==1);
  pocket_link_config link={0};cJSON *reply=request(&link,"/targets/linux/status",NULL,scratch,8192,VOICE_STATUS);assert(reply);cJSON_Delete(reply);
  capture_frame copied={0};assert(xQueueReceive(audio_queue,&copied,0)==pdTRUE && copied.size==2048 && !memcmp(copied.pcm,producer.pcm,2048));
  state.recording=false;assert(release_capture(&consumer,&body) && !queue_live && !json_live);
 }else{
  command_limit=!strcmp(scenario,"consecutive") || !strcmp(scenario,"recover")?2:1;
  bool multi=!strcmp(scenario,"multi");reset(multi?26:40);int outcome=setjmp(stopped);if(!outcome)network(NULL);
  assert(outcome==1 && verified_chunks==(multi?32:command_limit));
  assert(multi?produced_frames==32:capture_reads==(int)command_limit);
  bool failed=!strcmp(scenario,"ack_error") || !strcmp(scenario,"transport") || !strcmp(scenario,"overflow");
  assert(finish_calls==(failed?0:!strcmp(scenario,"recover")?1:(int)command_limit));
  assert(abort_calls==(failed || !strcmp(scenario,"recover")?1:0));
  assert(!state.busy && !state.recording && !queue_live && !audio_queue && live_allocations()==2 && !json_live);
  assert(uploaded_bytes==2048 && queue_count==4 && record_stack==6144 && peak_audio_bytes<=16500);
  printf("MEMORY consumer_frame_bytes=%zu eliminated_request_bytes=%zu peak_audio_request_bytes=%zu\n",sizeof(capture_frame),sizeof(capture_frame)+2816,peak_audio_bytes);
 }
 reset(0);assert(!json_live);puts("PASS: scratch lifetimes, actual JSON/Base64 and bounded capture cleanup");return 0;
}
'''

def harness():
    text=voice.HARNESS
    text=text.replace('#include "pocket_usage_client.h"','#undef assert\n#define assert(x) do{if(!(x)){fprintf(stderr,"assertion failed at line %d: %s\\n",__LINE__,#x);exit(77);}}while(0)\n#include "pocket_usage_client.h"',1)
    text=text.replace('static void *test_malloc(size_t size) {','static const char *scenario;\nstatic unsigned command_limit,capture_number,verified_chunks,chunk_in_capture;\nstatic bool limited_heap;\nstatic void *test_malloc(size_t size) {',1)
    text=text.replace('if(audio){audio_attempts++;','if(audio){audio_attempts++;if(limited_heap && audio_bytes+size>sizeof(capture_frame))return NULL;',1)
    start=text.index(' if(queue==commands){');end=text.index('\n assert(queue==&audio_handle)',start)
    text=text[:start]+' if(queue==commands){if(command_reads++>=(int)command_limit)longjmp(stopped,1);elapsed_ms+=ticks;*(unsigned*)out=1;state.target=1;held=true;cancelled=false;state.busy=true;return pdTRUE;}'+text[end:]
    text=text.replace('memset(out,0,size);capture_volume=last_volume;','for(size_t n=0;n<size;n++)((unsigned char*)out)[n]=(unsigned char)(n^capture_reads);capture_volume=last_volume;',1)
    text=text.replace('if(mode==7)return pdPASS;record_stack=stack;','if(mode==7){record_stack=stack;track_peak();return pdPASS;}record_stack=stack;',1)
    text=text.replace('memset(scheduled_frame->pcm,0,2048);','for(unsigned n=0;n<2048;n++)scheduled_frame->pcm[n]=(unsigned char)(n^produced_frames);',1)
    start=text.index('int mbedtls_base64_encode(');end=text.index('void pocket_http_close(void)',start)
    extra=EXTRA[EXTRA.index('static void *json_allocate'):]
    text=text[:start]+extra+text[end:]
    return text[:text.index('int main(int argc,char **argv)')]+MAIN

class VoiceScratchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang') or shutil.which('gcc')
        cached=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:raise unittest.SkipTest('host compiler unavailable')
        cls.directory=tempfile.TemporaryDirectory();temp=Path(cls.directory.name)
        root=Path(__file__).resolve().parents[1];sdk=Path(os.environ.get('IDF_PATH',str(root.parent/'HPsdk/idf-5.5.3')))
        json_source=sdk/'components/json/cJSON/cJSON.c';mbed=sdk/'components/mbedtls/mbedtls'
        if not json_source.is_file() or not (mbed/'library/base64.c').is_file():raise unittest.SkipTest('cached SDK JSON/Base64 unavailable')
        for name,content in voice.HEADERS.items():
            if name in ('cJSON.h','mbedtls/base64.h'):continue
            path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        (temp/'fixture_mbedtls_config.h').write_text('#define MBEDTLS_BASE64_C\n')
        (temp/'scratch.c').write_text(harness());cls.binary=temp/('scratch.exe' if os.name=='nt' else 'scratch')
        command=[compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-DMBEDTLS_CONFIG_FILE="fixture_mbedtls_config.h"',
            '-I'+str(temp),'-I'+str(root/'firmware/main'),'-I'+str(root/'firmware/core'),'-I'+str(json_source.parent),
            '-I'+str(mbed/'include'),'-I'+str(mbed/'library'),str(temp/'scratch.c'),str(json_source),str(mbed/'library/base64.c'),str(mbed/'library/constant_time.c'),'-o',str(cls.binary)]
        compiled=subprocess.run(command,capture_output=True,text=True)
        if compiled.returncode:raise AssertionError(compiled.stdout+compiled.stderr)

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def exercise(self,*cases):
        for case in cases:
            with self.subTest(case=case):
                result=subprocess.run([str(self.binary),case],capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('PASS:',result.stdout)

    def test_maximum_pcm_upload_with_only_producer_heap_and_ack_overwrite(self):self.exercise('maximum','multi')
    def test_invalid_ack_and_transport_output_overflow_release_capture(self):self.exercise('ack_error','transport','overflow')
    def test_consecutive_captures_and_failure_recovery_reuse_scratch(self):self.exercise('consecutive','recover')
    def test_stalled_recorder_keeps_queue_while_network_reuses_scratch(self):self.exercise('stalled')

    def test_real_http_callback_preserves_post_at_ack_capacity_boundary(self):
        compiler=shutil.which('clang') or shutil.which('gcc')
        cached=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():compiler=str(cached)
        source=Path(__file__).resolve().parents[1]/'firmware/main'
        with tempfile.TemporaryDirectory() as folder:
            temp=Path(folder)
            for name,content in http.HEADERS.items():
                path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
            fixture=http.HARNESS[:http.HARNESS.index('int main(void)')]
            fixture=fixture.replace('#include <assert.h>','#include <assert.h>\n#include <stdlib.h>\n#undef assert\n#define assert(x) do{if(!(x)){fprintf(stderr,"boundary assertion failed at line %d: %s\\n",__LINE__,#x);exit(77);}}while(0)',1)
            start=fixture.index('esp_err_t esp_http_client_set_post_field(')
            end=fixture.index('int esp_http_client_get_status_code(',start)
            fixture=fixture[:start]+HTTP_BOUNDARY+fixture[end:]+BOUNDARY_MAIN
            (temp/'http-boundary.c').write_text(fixture);binary=temp/('http-boundary.exe' if os.name=='nt' else 'http-boundary')
            compiled=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(temp),'-I'+str(source),str(source/'pocket_http.c'),str(temp/'http-boundary.c'),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS:',result.stdout)

HTTP_BOUNDARY=r'''
static const char *held_post;
static int held_size,callback_error;
static unsigned response_bytes,fragment_bytes,callbacks;
static char expected_post[2816],incoming[5376];
esp_err_t esp_http_client_set_post_field(esp_http_client_handle_t c,const char *value,int size){assert(c->active);held_post=value;held_size=size;return ESP_OK;}
esp_err_t esp_http_client_perform(esp_http_client_handle_t c){
 assert(c->active && held_size==2756 && !memcmp(held_post,expected_post,2816));performs++;
 for(unsigned offset=0;offset<response_bytes;){
  unsigned length=response_bytes-offset;if(length>fragment_bytes)length=fragment_bytes;
  esp_http_client_event_t event={.event_id=HTTP_EVENT_ON_DATA,.user_data=c->user_data,.client=c,.data=incoming+offset,.data_len=(int)length};
  callback_error=c->event_handler(&event);callbacks++;
  assert(!memcmp(held_post,expected_post,2816));offset+=length;
 }
 return ESP_OK; /* Real helper must also honor its stored overflow flag. */
}
'''

BOUNDARY_MAIN=r'''
int main(void){
 for(unsigned fragmented=0;fragmented<2;fragmented++)for(unsigned overflow=0;overflow<2;overflow++){
  reset(true,1735689600,0,0);response_bytes=5375+overflow;fragment_bytes=fragmented?2048:response_bytes;callbacks=0;callback_error=0;
  struct{unsigned char before[16];char scratch[8192];unsigned char after[16];}guarded;
  memset(&guarded,0xa5,sizeof guarded);memset(incoming,'A',sizeof incoming);
  char *body=guarded.scratch+5376;memset(body,'P',2756);body[2756]=0;memcpy(expected_post,body,2816);
  pocket_http_result result;int error=pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/audio/fixture/chunk",token,"SYNTHETIC_PUBLIC_CA",body,guarded.scratch,5376,&result);
  assert(performs==1 && callbacks==(fragmented?3:1) && !memcmp(body,expected_post,2816));
  for(unsigned n=0;n<16;n++)assert(guarded.before[n]==0xa5 && guarded.after[n]==0xa5);
  if(overflow)assert(error==ESP_ERR_INVALID_SIZE && callback_error==ESP_ERR_INVALID_SIZE && cleanups==1 && reads==1 && !result.length && !guarded.scratch[0]);
  else assert(error==ESP_OK && callback_error==ESP_OK && !cleanups && result.length==5375 && guarded.scratch[5375]==0 && held_size==2756);
  pocket_http_close();
 }
 puts("PASS: real helper5375/5376 single/fragmented ACK bounds, retained POST and no replay");return 0;
}
'''
