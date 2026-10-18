"""Offline real-client streaming against SDK cJSON/base64 and a serial HTTP fake."""
import copy
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


GENERATION = '0123456789abcdef0123456789abcdef'
REVISION = 'a' * 64
REQUEST = '11223344556677889900aabbccddeeff'
TASK = 'task_A-9'


def manifest(total=992000):
    segments = []
    offset = 0
    while offset < total:
        size = min(640000, total - offset)
        segments.append(dict(index=len(segments), bytes=size, chunks=(size + 4095) // 4096,
                             first_frame=offset // 2, frames=size // 2))
        offset += size
    return dict(version=2, task_id=TASK, request_id=REQUEST, result_revision=REVISION,
                generation=GENERATION, state='ready', format='pcm_s16le', sample_rate=16000,
                channels=1, sample_bits=16, chunk_bytes=4096, bytes=total,
                chunks=sum(s['chunks'] for s in segments), segment_count=len(segments), segments=segments)


CAPABILITY = dict(reply_delivery=dict(version=1,
    text_pages=dict(version=1, page_bytes=768, max_result_utf8_bytes=65536),
    speech_segments=dict(version=2, available=True, segment_seconds=20, max_reply_seconds=240,
        max_segments=12, chunk_bytes=4096, sample_rate=16000, channels=1, sample_bits=16,
        max_tts_utf8_bytes=4096, max_encoded_bytes=3145728, generation_timeout_seconds=120,
        decode_timeout_seconds=20, idle_ttl_seconds=180, absolute_ttl_seconds=900)))


def fixtures():
    assert len(REQUEST) == len(GENERATION) == 32 and len(REVISION) == 64
    compact = lambda value: json.dumps(value, separators=(',', ':'))
    caps = [(compact(CAPABILITY), True, True), ('{}', False, False), ('null', False, False)]
    value = copy.deepcopy(CAPABILITY);value['reply_delivery']['version'] = 2
    caps.append((compact(value), False, False))
    for section, expected in [('text_pages', (False, True)), ('speech_segments', (True, False))]:
        for key, original in CAPABILITY['reply_delivery'][section].items():
            value = copy.deepcopy(CAPABILITY)
            value['reply_delivery'][section][key] = False if key == 'available' else original + 1
            caps.append((compact(value), *expected))
            value = copy.deepcopy(CAPABILITY);del value['reply_delivery'][section][key]
            caps.append((compact(value), *expected))
    caps.append((compact(CAPABILITY).replace('"version":1', '"version":1,"version":1', 1), False, False))
    caps.append((compact(CAPABILITY).replace('"available":true', '"available":"true"'), True, False))
    bad_manifest = []
    for key, bad in [('version', 1), ('task_id', 'other'), ('request_id', '0' * 32),
                     ('generation', 'f' * 32), ('result_revision', 'b' * 64),
                     ('format', 'pcm_s16be'), ('sample_rate', 24000), ('channels', 2),
                     ('sample_bits', 8), ('chunk_bytes', 2048), ('state', 'unknown'),
                     ('bytes', 992002), ('bytes', 7680002), ('chunks', 244),
                     ('segment_count', 1), ('segment_count', 13), ('segment_count', 0),
                     ('chunks', 1.5), ('bytes', '992000'), ('segments', [])]:
        value = manifest();value[key] = bad;bad_manifest.append(compact(value))
    for index, key, bad in [(0, 'index', 1), (1, 'index', 0), (0, 'bytes', 640002),
                            (0, 'bytes', 639998), (1, 'bytes', 351999), (1, 'bytes', 0),
                            (0, 'chunks', 156), (1, 'chunks', 87), (0, 'first_frame', 1),
                            (1, 'first_frame', 319999), (1, 'frames', 176001),
                            (0, 'frames', '320000')]:
        value = manifest();value['segments'][index][key] = bad;bad_manifest.append(compact(value))
    value = manifest();del value['generation'];bad_manifest.append(compact(value))
    bad_manifest += [compact(manifest()).replace('"bytes":640000', '"bytes":640000,"bytes":640000', 1),
                     compact(manifest()).replace('"version":2', '"version":2,"version":2', 1),
                     compact(manifest()) + ' {}', compact(manifest()) + ' trailing',
                     compact(manifest()).replace(GENERATION, GENERATION + r'\u0000hidden'),
                     compact(dict(manifest(), extra=[[[[[[[[[]]]]]]]]])),
                     compact(dict(manifest(), extra=list(range(300))))]
    base = dict(version=2, generation=GENERATION, segment=0, sequence=0, bytes=4096,
                data='@DATA@', segment_eof=False, reply_eof=False)
    bad_chunk = []
    for key, bad in [('version', 1), ('generation', 'f' * 32), ('segment', 1), ('sequence', 1),
                     ('bytes', 0), ('bytes', 4095), ('bytes', 4098), ('bytes', 4094),
                     ('bytes', '4096'), ('sequence', .5), ('segment_eof', True),
                     ('reply_eof', True), ('segment_eof', 'false'), ('reply_eof', 0),
                     ('data', '!!!!'), ('data', 'AA=='), ('data', ''), ('data', '@DATA@\n'),
                     ('data', '@DATA@='), ('data', '@DATA@AAAA')]:
        value = copy.deepcopy(base);value[key] = bad;bad_chunk.append(compact(value))
    for key in base:
        value = copy.deepcopy(base);del value[key];bad_chunk.append(compact(value))
    bad_chunk += [compact(base).replace('"bytes":4096', '"bytes":4096,"bytes":4096'),
                  compact(base).replace('"sequence":0', '"sequence":0,"\\u0073equence":0'),
                  compact(base).replace(GENERATION, GENERATION + r'\u0000hidden'),
                  compact(base) + ' {}', compact(base) + ' trailing',
                  compact(dict(base, extra=[[[[[[[[[]]]]]]]]])),
                  compact(dict(base, extra=list(range(300))))]
    def strings(name, values):
        return 'static const char *const ' + name + '[]={' + ','.join(json.dumps(s) for s in values) + '};\n'
    return (strings('capability_fixtures', [v[0] for v in caps]) +
            'static const bool capability_pages[]={' + ','.join(str(v[1]).lower() for v in caps) + '};\n' +
            'static const bool capability_speech[]={' + ','.join(str(v[2]).lower() for v in caps) + '};\n' +
            strings('bad_manifests', bad_manifest) + strings('bad_chunks', bad_chunk))


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "mbedtls/base64.h"
#include "pocket_speech_client.h"
#include "pocket_http.h"
static unsigned own_allocations;
void *speech_test_malloc(size_t n){own_allocations++;return malloc(n);}
void *speech_test_calloc(size_t n,size_t size){own_allocations++;return calloc(n,size);}
void *speech_test_realloc(void *p,size_t n){own_allocations++;return realloc(p,n);}
void speech_test_free(void *p){free(p);}
#define malloc speech_test_malloc
#define calloc speech_test_calloc
#define realloc speech_test_realloc
#define free speech_test_free
#include "pocket_speech_client.c"
#undef malloc
#undef calloc
#undef realloc
#undef free

static const char generation_id[]="0123456789abcdef0123456789abcdef";
static const char revision_id[]="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static const char request_id[]="11223344556677889900aabbccddeeff";
static const char task_id[]="task_A-9";
static unsigned char caller_pcm[4096];
static char caller_response[8192];
static int64_t now_us;
static unsigned total_bytes,creates,polls,gets,cancels,starts,writes,drains,stops,ready_messages,cancellation_messages;
static unsigned written,next_segment,next_sequence,max_json_bytes,live_json_bytes,peak_json_bytes,live_json_allocations;
static unsigned cancel_at,fail_at,bad_eof,wire_fault,create_http_status,poll_http_status,chunk_http_status;
static int bad_manifest,bad_chunk;
static bool playing,local_cancelled,forever_processing;
static int64_t http_delay_us,chunk_delay_us,create_delay_us;
enum { CANCEL_BEFORE=1,CANCEL_CREATE,CANCEL_POLL,CANCEL_START,CANCEL_CHUNK,CANCEL_WRITE,CANCEL_LAST_WRITE,CANCEL_DRAIN,
       FAIL_CREATE=20,FAIL_POLL,FAIL_CHUNK,FAIL_START,FAIL_WRITE,FAIL_DRAIN,FAIL_CANCEL };
typedef union { max_align_t alignment;size_t bytes; } allocation_prefix;
static void *json_alloc(size_t size){
 allocation_prefix *block=malloc(sizeof(*block)+size);assert(block);block->bytes=size;
 live_json_bytes+=(unsigned)size;live_json_allocations++;if(live_json_bytes>peak_json_bytes)peak_json_bytes=live_json_bytes;
 return block+1;
}
static void json_free(void *pointer){if(pointer){allocation_prefix *block=(allocation_prefix*)pointer-1;live_json_bytes-=(unsigned)block->bytes;live_json_allocations--;free(block);}}
int64_t esp_timer_get_time(void){return now_us;}
void vTaskDelay(unsigned ticks){now_us+=(int64_t)ticks*1000;}
static unsigned char sample_byte(unsigned offset){return (unsigned char)(offset*37u+(offset>>7)+(offset>>16));}
static void append(char *out,size_t capacity,const char *format,...){
 size_t at=strlen(out);va_list args;va_start(args,format);int size=vsnprintf(out+at,capacity-at,format,args);va_end(args);
 assert(size>=0 && (size_t)size<capacity-at);
}
static void make_manifest(char *out,size_t capacity,bool ready){
 unsigned count=ready?(total_bytes+639999)/640000:0,chunks=0;
 for(unsigned i=0;i<count;i++){unsigned n=total_bytes-i*640000;if(n>640000)n=640000;chunks+=(n+4095)/4096;}
 snprintf(out,capacity,"{\"version\":2,\"task_id\":\"%s\",\"request_id\":\"%s\",\"result_revision\":\"%s\",\"generation\":\"%s\",\"state\":\"%s\",\"format\":\"pcm_s16le\",\"sample_rate\":16000,\"channels\":1,\"sample_bits\":16,\"chunk_bytes\":4096,\"bytes\":%u,\"chunks\":%u,\"segment_count\":%u,\"segments\":[",task_id,request_id,revision_id,generation_id,ready?"ready":"processing",ready?total_bytes:0,chunks,count);
 for(unsigned i=0;i<count;i++){
  unsigned n=total_bytes-i*640000;if(n>640000)n=640000;
  append(out,capacity,"%s{\"index\":%u,\"bytes\":%u,\"chunks\":%u,\"first_frame\":%u,\"frames\":%u}",i?",":"",i,n,(n+4095)/4096,i*320000,n/2);
 }
 append(out,capacity,"]}");
}
static void make_chunk(char *out,size_t capacity,unsigned segment,unsigned sequence){
 unsigned segment_bytes=total_bytes-segment*640000;if(segment_bytes>640000)segment_bytes=640000;
 unsigned bytes=segment_bytes-sequence*4096;if(bytes>4096)bytes=4096;
 unsigned offset=segment*640000+sequence*4096;unsigned char data[4096],encoded[5465];size_t encoded_bytes=0;
 for(unsigned i=0;i<bytes;i++)data[i]=sample_byte(offset+i);
 assert(!mbedtls_base64_encode(encoded,sizeof(encoded),&encoded_bytes,data,bytes));
 if(bad_chunk>=0){
  const char *raw=bad_chunks[bad_chunk],*marker=strstr(raw,"@DATA@");
  if(marker){size_t prefix=(size_t)(marker-raw);assert(prefix+encoded_bytes+strlen(marker+6)<capacity);memcpy(out,raw,prefix);memcpy(out+prefix,encoded,encoded_bytes);snprintf(out+prefix+encoded_bytes,capacity-prefix-encoded_bytes,"%s",marker+6);}
  else snprintf(out,capacity,"%s",raw);
 }else{
  bool segment_eof=sequence+1==(segment_bytes+4095)/4096,reply_eof=offset+bytes==total_bytes;
  if(bad_eof==1 && reply_eof)reply_eof=false;
  if(bad_eof==2 && segment_eof)segment_eof=false;
  if(bad_eof==3 && segment==0 && segment_eof)reply_eof=true;
  snprintf(out,capacity,"{\"version\":2,\"generation\":\"%s\",\"segment\":%u,\"sequence\":%u,\"bytes\":%u,\"data\":\"%s\",\"segment_eof\":%s,\"reply_eof\":%s}",generation_id,segment,sequence,bytes,encoded,segment_eof?"true":"false",reply_eof?"true":"false");
 }
 next_sequence++;if(next_sequence==(segment_bytes+4095)/4096){next_segment++;next_sequence=0;}
}
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t capacity,pocket_http_result *result){
 assert(!strcmp(origin,"https://fixture.invalid") && !strcmp(token,"SYNTHETIC_TOKEN_0123456789abcdef") && !strcmp(ca,"SYNTHETIC_CA"));
 assert(out==caller_response && capacity==sizeof(caller_response));
 memset(result,0,sizeof(*result));result->performed=true;now_us+=http_delay_us;
 if(strstr(path,"/cancel")){
  cancels++;assert(!playing && cancels==1);assert(body && !strcmp(body,"{\"generation\":\"0123456789abcdef0123456789abcdef\"}"));
  assert(!strcmp(path,"/targets/linux/tasks/task_A-9/speech/v2/cancel"));
  snprintf(out,capacity,"ignored cancellation response");result->status=200;result->length=strlen(out);return fail_at==FAIL_CANCEL?ESP_FAIL:ESP_OK;
 }
 if(body){
  creates++;assert(creates==1 && !strcmp(path,"/targets/linux/tasks/task_A-9/speech/v2"));
  assert(!strcmp(body,"{\"request_id\":\"11223344556677889900aabbccddeeff\"}"));
  make_manifest(out,capacity,false);result->status=(int)create_http_status;now_us+=create_delay_us;
  if(cancel_at==CANCEL_CREATE)local_cancelled=true;
  if(fail_at==FAIL_CREATE)return ESP_FAIL;
 }else if(strstr(path,"/segments/")){
  gets++;now_us+=chunk_delay_us;unsigned segment=99,sequence=999;int consumed=0;
  assert(sscanf(path,"/targets/linux/tasks/task_A-9/speech/v2/segments/%u/chunks/%u?generation=0123456789abcdef0123456789abcdef%n",&segment,&sequence,&consumed)==2 && path[consumed]==0);
  assert(segment==next_segment && sequence==next_sequence && playing);
  make_chunk(out,capacity,segment,sequence);result->status=(int)chunk_http_status;
  if(cancel_at==CANCEL_CHUNK)local_cancelled=true;
  if(fail_at==FAIL_CHUNK)return ESP_FAIL;
  if(wire_fault==1){size_t n=strlen(out);memcpy(out+n+1,"hidden",7);result->length=n+7;return ESP_OK;}
  if(wire_fault==2){memset(out+2,' ',7999);out[0]='{';out[1]='}';out[8001]=0;}
 }else{
  polls++;assert(!strcmp(path,"/targets/linux/tasks/task_A-9/speech/v2?generation=0123456789abcdef0123456789abcdef"));
  if(bad_manifest>=0)snprintf(out,capacity,"%s",bad_manifests[bad_manifest]);else make_manifest(out,capacity,!forever_processing);
  result->status=(int)poll_http_status;if(cancel_at==CANCEL_POLL)local_cancelled=true;
  if(fail_at==FAIL_POLL)return ESP_FAIL;
 }
 result->length=strlen(out);if(result->length>max_json_bytes)max_json_bytes=(unsigned)result->length;return ESP_OK;
}
void pocket_http_close(void){}
static bool audio_cancelled(void *context){assert(context==&playing);return local_cancelled;}
static bool audio_start(void *context){assert(context==&playing && !playing);starts++;playing=true;if(cancel_at==CANCEL_START)local_cancelled=true;return fail_at!=FAIL_START;}
static bool audio_write(void *context,const void *data,size_t length){
 assert(context==&playing && playing && data==caller_pcm && length>0 && length<=4096 && !(length%2));
 assert(!live_json_allocations);for(size_t i=0;i<length;i++)assert(((const unsigned char*)data)[i]==sample_byte(written+(unsigned)i));
 written+=(unsigned)length;writes++;
 if(cancel_at==CANCEL_WRITE || (cancel_at==CANCEL_LAST_WRITE && written==total_bytes))local_cancelled=true;
 return fail_at!=FAIL_WRITE;
}
static bool audio_drain(void *context){assert(context==&playing && playing && written==total_bytes && !live_json_allocations);drains++;if(cancel_at==CANCEL_DRAIN)local_cancelled=true;return fail_at!=FAIL_DRAIN;}
static void audio_stop(void *context){assert(context==&playing && playing);stops++;playing=false;}
static void audio_message(void *context,const char *text){
 assert(context==&playing && text && !strstr(text,task_id) && !strstr(text,request_id) && !strstr(text,generation_id) && !strstr(text,"https"));
 if(!strcmp(text,"Reply ready")){assert(!playing && drains==1 && written==total_bytes);ready_messages++;}
 if(!strcmp(text,"Playback stopped"))cancellation_messages++;
}
static pocket_link_config link;
static const pocket_speech_io audio={.context=&playing,.cancelled=audio_cancelled,.start=audio_start,.write=audio_write,.drain=audio_drain,.stop=audio_stop,.message=audio_message};
static void reset_fixture(unsigned bytes){
 assert(!live_json_allocations);now_us=0;total_bytes=bytes;creates=polls=gets=cancels=starts=writes=drains=stops=ready_messages=cancellation_messages=written=next_segment=next_sequence=max_json_bytes=peak_json_bytes=own_allocations=0;
 cancel_at=fail_at=bad_eof=wire_fault=0;bad_manifest=bad_chunk=-1;playing=local_cancelled=forever_processing=false;
 http_delay_us=1000;chunk_delay_us=create_delay_us=0;create_http_status=202;poll_http_status=chunk_http_status=200;
 snprintf(link.origin,sizeof(link.origin),"https://fixture.invalid");snprintf(link.token,sizeof(link.token),"SYNTHETIC_TOKEN_0123456789abcdef");snprintf(link.ca,sizeof(link.ca),"SYNTHETIC_CA");
 memset(caller_response,0,sizeof(caller_response));memset(caller_pcm,0,sizeof(caller_pcm));
}
static bool run_play(void){return pocket_speech_v2_play(&link,1,task_id,request_id,caller_response,sizeof(caller_response),caller_pcm,sizeof(caller_pcm),&audio);}
#define CHECK(c,m) do {if(!(c)){puts("FAIL: " m);return 1;}}while(0)
static int check_resources(void){
 CHECK(!own_allocations && !live_json_allocations && !live_json_bytes,"client allocated audio/persistent memory or leaked JSON");
 CHECK(peak_json_bytes<20000,"JSON memory/resource bound exceeded");return 0;
}
static int test_capabilities(void){
 for(size_t i=0;i<sizeof(capability_fixtures)/sizeof(capability_fixtures[0]);i++){
  cJSON *json=cJSON_Parse(capability_fixtures[i]);bool pages=pocket_delivery_text_pages(json),speech=pocket_delivery_speech_segments(json);cJSON_Delete(json);
  if(pages!=capability_pages[i] || speech!=capability_speech[i]){printf("FAIL: capability fixture %zu pages=%u speech=%u\n",i,(unsigned)pages,(unsigned)speech);return 1;}
 }
 CHECK(!pocket_delivery_text_pages(NULL) && !pocket_delivery_speech_segments(NULL),"missing negotiation enabled V2");return 0;
}
static int test_streams(void){
 const unsigned durations[]={31,65,240},expected_chunks[]={243,511,1884};
 for(unsigned i=0;i<3;i++){
  reset_fixture(durations[i]*32000);
  CHECK(run_play(),"valid long reply failed");
  CHECK(creates==1 && polls==1 && gets==expected_chunks[i] && writes==gets && written==total_bytes,"long reply was truncated, repeated, skipped or resynthesized");
  CHECK(starts==1 && drains==1 && stops==1 && !playing && !cancels && ready_messages==1,"playing did not survive through final PCM drain");
  CHECK(max_json_bytes<=8000,"accepted HTTP JSON exceeded the contract response bound");
  if(check_resources())return 1;
  printf("STREAM seconds=%u bytes=%u chunks=%u json_peak=%u\n",durations[i],written,gets,peak_json_bytes);
 }
 return 0;
}
static int test_manifests(void){
 for(size_t i=0;i<sizeof(bad_manifests)/sizeof(bad_manifests[0]);i++){
  reset_fixture(992000);bad_manifest=(int)i;
  if(run_play()){printf("FAIL: malformed manifest fixture %zu played\n",i);return 1;}
  CHECK(creates==1 && polls==1 && cancels==1 && !starts && !writes && !drains && !ready_messages,"malformed manifest started audio or restarted synthesis");
  if(check_resources())return 1;
 }
 reset_fixture(7680002);CHECK(!run_play() && cancels==1 && !starts,"240-second overcap result was accepted");return check_resources();
}
static int test_chunks(void){
 for(size_t i=0;i<sizeof(bad_chunks)/sizeof(bad_chunks[0]);i++){
  reset_fixture(992000);bad_chunk=(int)i;
  if(run_play()){printf("FAIL: malformed chunk fixture %zu played\n",i);return 1;}
  CHECK(creates==1 && gets==1 && starts==1 && stops==1 && cancels==1 && !writes && !drains && !ready_messages,"malformed or stale chunk reached audio or was retried");
  if(check_resources())return 1;
 }
 for(unsigned i=1;i<=3;i++){reset_fixture(992000);bad_eof=i;CHECK(!run_play() && stops==1 && cancels==1 && !drains && !ready_messages,"missing or early final EOF claimed success");if(check_resources())return 1;}
 for(unsigned i=1;i<=2;i++){reset_fixture(992000);wire_fault=i;CHECK(!run_play() && !writes && stops==1 && cancels==1 && !drains,"oversize/NUL-hidden HTTP payload accepted");if(check_resources())return 1;}
 return 0;
}
static int test_cancellation(void){
 for(unsigned i=CANCEL_BEFORE;i<=CANCEL_DRAIN;i++){
  reset_fixture(992000);cancel_at=i;local_cancelled=i==CANCEL_BEFORE;
  CHECK(!run_play() && !playing && !ready_messages && creates<=1,"cancellation succeeded or restarted synthesis");
  CHECK(cancels==(i>=CANCEL_CREATE?1u:0u) && stops==(i>=CANCEL_START?1u:0u),"cancel did not stop locally before generation retirement");
  CHECK(drains==(i==CANCEL_DRAIN?1u:0u),"cancelled audio drained");if(check_resources())return 1;
 }
 const unsigned failures[]={FAIL_CREATE,FAIL_POLL,FAIL_CHUNK,FAIL_START,FAIL_WRITE,FAIL_DRAIN};
 for(size_t i=0;i<sizeof(failures)/sizeof(failures[0]);i++){
  reset_fixture(992000);fail_at=failures[i];CHECK(!run_play() && creates==1 && !playing && !ready_messages,"failure retried creation or claimed success");
  CHECK(cancels==(fail_at==FAIL_CREATE?0u:1u) && drains==(fail_at==FAIL_DRAIN?1u:0u),"failure cleanup or drain ordering changed");if(check_resources())return 1;
 }
 const unsigned simultaneous_cancel[]={CANCEL_START,CANCEL_WRITE,CANCEL_DRAIN},simultaneous_failure[]={FAIL_START,FAIL_WRITE,FAIL_DRAIN};
 for(unsigned i=0;i<3;i++){
  reset_fixture(992000);cancel_at=simultaneous_cancel[i];fail_at=simultaneous_failure[i];
  CHECK(!run_play() && stops==1 && cancels==1 && cancellation_messages==1,"callback failure bypassed its post-I/O cancellation check");if(check_resources())return 1;
 }
 reset_fixture(992000);cancel_at=CANCEL_WRITE;fail_at=FAIL_CANCEL;CHECK(!run_play() && stops==1 && cancels==1 && !drains,"cancel failure retried or delayed local stop");
 reset_fixture(992000);chunk_http_status=410;CHECK(!run_play() && cancels==1 && !writes && !drains && creates==1,"expired generation automatically restarted");
 reset_fixture(992000);poll_http_status=409;CHECK(!run_play() && cancels==1 && !starts && creates==1,"changed generation automatically restarted");
 reset_fixture(992000);create_http_status=200;CHECK(!run_play() && !starts && !cancels && creates==1,"creation accepted a non-202 response");
 reset_fixture(992000);create_delay_us=120000000;
 CHECK(!run_play() && creates==1 && cancels==1 && !polls && !starts && !drains,"late create identity was not retired without reviving playback");
 reset_fixture(992000);forever_processing=true;http_delay_us=15000000;
 CHECK(!run_play() && creates==1 && cancels==1 && !starts && now_us<150000000,"generation deadline was not bounded");
 reset_fixture(992000);chunk_delay_us=450000000;
 CHECK(!run_play() && creates==1 && cancels==1 && writes==1 && !drains && now_us<916000000,"absolute generation lifetime was not enforced");
 return check_resources();
}
static int test_arguments(void){
 reset_fixture(992000);
 CHECK(!pocket_speech_v2_play(&link,2,task_id,request_id,caller_response,sizeof(caller_response),caller_pcm,sizeof(caller_pcm),&audio),"unknown target accepted");
 CHECK(!pocket_speech_v2_play(&link,1,"../bad",request_id,caller_response,sizeof(caller_response),caller_pcm,sizeof(caller_pcm),&audio),"unsafe task ID accepted");
 CHECK(!pocket_speech_v2_play(&link,1,task_id,"request",caller_response,sizeof(caller_response),caller_pcm,sizeof(caller_pcm),&audio),"unbounded request ID accepted");
 CHECK(!pocket_speech_v2_play(&link,1,task_id,request_id,caller_response,8000,caller_pcm,sizeof(caller_pcm),&audio),"HTTP scratch smaller than 8192 accepted");
 CHECK(!pocket_speech_v2_play(&link,1,task_id,request_id,caller_response,sizeof(caller_response),caller_pcm,4095,&audio),"PCM scratch smaller than 4096 accepted");
 CHECK(!pocket_speech_v2_play(&link,1,task_id,request_id,caller_response,sizeof(caller_response),caller_pcm,sizeof(caller_pcm),NULL),"missing callbacks accepted");
 CHECK(!creates && !polls && !starts,"invalid input reached HTTP/audio");return check_resources();
}
int main(int argc,char **argv){
 cJSON_Hooks hooks={.malloc_fn=json_alloc,.free_fn=json_free};cJSON_InitHooks(&hooks);reset_fixture(992000);
 if(argc!=2)return 2;int error=0;
 if(!strcmp(argv[1],"capabilities"))error=test_capabilities();
 else if(!strcmp(argv[1],"streams"))error=test_streams();
 else if(!strcmp(argv[1],"manifests"))error=test_manifests();
 else if(!strcmp(argv[1],"chunks"))error=test_chunks();
 else if(!strcmp(argv[1],"cancellation"))error=test_cancellation();
 else if(!strcmp(argv[1],"arguments"))error=test_arguments();else return 2;
 if(error)return error;if(live_json_allocations)return 3;puts("PASS: speech V2 SDK JSON/base64 serial contract");return 0;
}
'''


class FirmwareSpeechV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        cls.root = pathlib.Path(__file__).resolve().parents[1]
        sdk = cls.root.parent/'HPsdk/idf-5.5.3/components'
        json_dir = sdk/'json/cJSON'
        tls_dir = sdk/'mbedtls/mbedtls'
        if not compiler or not (json_dir/'cJSON.c').is_file() or not (tls_dir/'library/base64.c').is_file():
            raise unittest.SkipTest('cached host compiler / SDK cJSON / SDK mbedTLS unavailable')
        cls.directory = tempfile.TemporaryDirectory(prefix='speech-v2-', dir=cls.root)
        cls.addClassCleanup(cls.directory.cleanup)
        temp = pathlib.Path(cls.directory.name)
        headers = {
            'esp_err.h': '#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n',
            'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
            'freertos/FreeRTOS.h': '#pragma once\n#define pdMS_TO_TICKS(value) (value)\n',
            'freertos/task.h': '#pragma once\nvoid vTaskDelay(unsigned);\n',
            'speech_mbedtls_config.h': '#pragma once\n#define MBEDTLS_BASE64_C\n',
        }
        for name, content in headers.items():
            path = temp/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(content, encoding='utf-8')
        harness = temp/'speech_v2.c'
        harness.write_text('#include <stdbool.h>\n' + fixtures() + HARNESS, encoding='utf-8')
        cls.binary = temp/('speech-v2.exe' if os.name == 'nt' else 'speech-v2')
        cls.environment = os.environ.copy()
        cls.environment.update(TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp))
        command = [compiler, '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror', '-pedantic',
                   '-D_CRT_SECURE_NO_WARNINGS', '-DMBEDTLS_CONFIG_FILE="speech_mbedtls_config.h"',
                   '-I'+str(temp), '-I'+str(cls.root/'firmware/main'), '-I'+str(json_dir),
                   '-I'+str(tls_dir/'include'), '-I'+str(tls_dir/'library'),
                   str(harness), str(json_dir/'cJSON.c'), str(tls_dir/'library/base64.c'),
                   str(tls_dir/'library/constant_time.c'), '-o', str(cls.binary)]
        if os.name != 'nt':
            command += ['-lm']
        compile_result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=cls.environment)
        if compile_result.returncode:
            raise AssertionError(compile_result.stdout + compile_result.stderr)

    def run_fixture(self, name):
        run = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=30, env=self.environment)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('PASS:', run.stdout)

    def test_exact_optional_delivery_capabilities(self):
        self.run_fixture('capabilities')

    def test_31_65_240_second_streams_have_exact_continuity_and_drain(self):
        self.run_fixture('streams')

    def test_malformed_manifests_never_start_audio(self):
        self.run_fixture('manifests')

    def test_chunk_identity_lengths_eof_and_json_are_fail_closed(self):
        self.run_fixture('chunks')

    def test_cancellation_failures_and_deadlines_never_restart_or_drain(self):
        self.run_fixture('cancellation')

    def test_invalid_inputs_do_not_reach_http_or_audio(self):
        self.run_fixture('arguments')
