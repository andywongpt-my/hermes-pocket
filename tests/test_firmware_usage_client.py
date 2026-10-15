"""Execute the actual bounded Usage client at the SDK/JSON-tree boundary."""
import copy
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from test_firmware_voice import HEADERS as VOICE_HEADERS

NOW = 1735689600
PROVIDERS = ('codex', 'claude', 'ollama_cloud')


def metric(value=0, unit='percent_used', category='subscription', window='five_hour'):
    return dict(value=value, unit=unit, category=category, window=window,
                fetched_at=NOW-10, resets_at=NOW+200, expires_at=NOW+290,
                window_minutes=300, availability='available')


def reports():
    return [dict(provider=p, availability='available', value=None,
                 metrics=[metric()]) for p in PROVIDERS]


FIXTURES = [reports()]
FIXTURES[0][2]['metrics'] = [metric(2.5, 'USD_credit', 'included_credit', 'included'),
                           metric(3, 'USD_credit', 'purchased_credit', 'purchased')]
unknown = reports()
unknown[0]['availability'] = 'partial'
unknown[0]['metrics'].append(dict(metric(None), availability='unavailable'))
FIXTURES.append(unknown)
for mutate in (
    lambda rows: rows[0].update(provider='claude'),
    lambda rows: rows[0]['metrics'][0].update(value=101),
    lambda rows: rows[0]['metrics'][0].update(unit='mystery'),
    lambda rows: rows[0]['metrics'][0].update(window='x'*49),
    lambda rows: rows[0].update(metrics=[metric() for _ in range(5)]),
    lambda rows: rows[0].update(availability='invented'),
    lambda rows: rows[0]['metrics'][0].update(value=True),
    lambda rows: rows[0]['metrics'][0].update(value=float('nan')),
):
    item = reports()
    mutate(item)
    FIXTURES.append(item)
FIXTURES.append([dict(provider=p, availability='not_configured', value=None,
                      fetched_at=None, source=None) for p in PROVIDERS])
FIXTURES.append({'provider': 'codex'})
duplicate = reports()
duplicate[0]['metrics'][0]['value_duplicate'] = 33
FIXTURES.append(duplicate)
deep = reports()
nested = None
for _ in range(12):
    nested = {'extra': nested}
deep[0]['extra'] = nested
FIXTURES.append(deep)
untimed = reports()
untimed[0].update(availability='stale')
untimed[0]['metrics'][0].update(value=1, fetched_at=None, resets_at=None, expires_at=None, availability='stale')
FIXTURES.append(untimed)
zero_duration = reports()
zero_duration[0]['metrics'][0]['window_minutes'] = 0
FIXTURES.append(zero_duration)
FIXTURES.append(reports())  # Valid prefix followed by invalid trailing bytes.
FIXTURES.append(reports())  # Valid prefix followed by a second JSON value.
FIXTURES.append(reports())  # Transport injects an embedded NUL plus trailing bytes.

CJSON_HEADER = r'''#pragma once
typedef struct cJSON { struct cJSON *next,*child;int type;const char *valuestring;double valuedouble;const char *string; } cJSON;
enum { JT_FALSE=1,JT_TRUE=2,JT_NULL=4,JT_NUMBER=8,JT_STRING=16,JT_ARRAY=32,JT_OBJECT=64 };
cJSON *cJSON_Parse(const char *);
void cJSON_Delete(cJSON *);
cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *,const char *);
cJSON *cJSON_GetArrayItem(const cJSON *,int);
int cJSON_GetArraySize(const cJSON *);
int cJSON_IsObject(const cJSON *);
int cJSON_IsArray(const cJSON *);
int cJSON_IsString(const cJSON *);
int cJSON_IsNumber(const cJSON *);
int cJSON_IsNull(const cJSON *);
'''


def trees():
    """Mirror cJSON's public tree shape; its third-party parser is not reimplemented."""
    nodes = []

    def add(value, key=None):
        index = len(nodes)
        node = dict(type='JT_NULL', key=key, child=None, next=None, text=None, number='0')
        nodes.append(node)
        if isinstance(value, (dict, list)):
            node['type'] = 'JT_OBJECT' if isinstance(value, dict) else 'JT_ARRAY'
            entries = value.items() if isinstance(value, dict) else [(None, item) for item in value]
            children = [add(item, name) for name, item in entries]
            if children:
                node['child'] = children[0]
                for left, right in zip(children, children[1:]):
                    nodes[left]['next'] = right
        elif isinstance(value, str):
            node.update(type='JT_STRING', text=value)
        elif isinstance(value, bool):
            node.update(type='JT_TRUE' if value else 'JT_FALSE')
        elif value is not None:
            node.update(type='JT_NUMBER', number='NAN' if value != value else repr(value))
        return index

    roots = [add(value) for value in FIXTURES]
    ptr = lambda n: 'NULL' if n is None else '&nodes['+str(n)+']'
    string = lambda s: 'NULL' if s is None else json.dumps(s)
    lines = ['static cJSON nodes['+str(len(nodes))+'];', 'static cJSON nodes[] = {']
    lines += ['{'+','.join([ptr(n['next']), ptr(n['child']), n['type'], string(n['text']),
                          n['number'], string('value' if n['key'] == 'value_duplicate' else n['key'])])+'},' for n in nodes]
    lines += ['};', 'static cJSON *roots[] = {'+','.join(ptr(n) for n in roots)+'};']
    lines += ['static const char *json_fixtures[] = {'+
              ','.join(json.dumps(json.dumps(value, separators=(',', ':')).replace('"value_duplicate":', '"value":')+('trailing' if i==16 else ' {}' if i==17 else '')) for i,value in enumerate(FIXTURES))+'};']
    return '\n'.join(lines)


HARNESS = r'''
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdarg.h>
#include "cJSON.h"
static void *test_calloc(size_t,size_t);
static void test_free(void *);
#define calloc test_calloc
#define free test_free
#include "pocket_usage_client.c"
#undef calloc
#undef free
static int fixture_index,http_calls,live_allocations,fail_allocate,fail_http,last_target;
static int usage_logs;
static char usage_log[256];
void test_log(const char *tag,const char *format,...){
 assert(!strcmp(tag,"pocket_usage")&&!strstr(format,"%s"));
 va_list args;va_start(args,format);vsnprintf(usage_log,sizeof usage_log,format,args);va_end(args);usage_logs++;
 assert(!strstr(usage_log,"https")&&!strstr(usage_log,"fixture")&&!strstr(usage_log,"token")&&!strstr(usage_log,"source")&&!strstr(usage_log,"value=")&&!strchr(usage_log,'{'));
}
static time_t clock_now=1735689600;
static void *test_calloc(size_t count,size_t bytes){if(fail_allocate)return NULL;void *p=calloc(count,bytes);assert(p);live_allocations++;return p;}
static void test_free(void *p){if(p){live_allocations--;free(p);}}
time_t test_time(time_t *out){if(out)*out=clock_now;return clock_now;}
#ifndef TEST_REAL_CJSON
cJSON *cJSON_Parse(const char *text){(void)text;return roots[fixture_index];}
void cJSON_Delete(cJSON *json){(void)json;}
cJSON *cJSON_GetObjectItemCaseSensitive(const cJSON *obj,const char *key){for(cJSON *node=obj?obj->child:NULL;node;node=node->next)if(node->string && !strcmp(node->string,key))return node;return NULL;}
cJSON *cJSON_GetArrayItem(const cJSON *obj,int index){cJSON *node=obj?obj->child:NULL;while(node && index-->0)node=node->next;return node;}
int cJSON_GetArraySize(const cJSON *obj){int size=0;for(cJSON *n=obj?obj->child:NULL;n;n=n->next)size++;return size;}
int cJSON_IsObject(const cJSON *v){return v && v->type==JT_OBJECT;}
int cJSON_IsArray(const cJSON *v){return v && v->type==JT_ARRAY;}
int cJSON_IsString(const cJSON *v){return v && v->type==JT_STRING;}
int cJSON_IsNumber(const cJSON *v){return v && v->type==JT_NUMBER;}
int cJSON_IsNull(const cJSON *v){return v && v->type==JT_NULL;}
#endif
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t capacity,pocket_http_result *result){
 (void)origin;(void)token;(void)ca;assert(!body);http_calls++;last_target=strstr(path,"/linux/")?1:0;
 assert(!strcmp(path,last_target?"/targets/linux/usage":"/targets/windows/usage"));
 if(fail_http)return ESP_FAIL;
 size_t length=strlen(json_fixtures[fixture_index]);assert(length+9<capacity);memcpy(out,json_fixtures[fixture_index],length+1);
 if(fixture_index==18){memcpy(out+length+1,"garbage",8);length+=8;}
 result->status=200;result->length=length;return ESP_OK;
}
void pocket_http_close(void){}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 pocket_usage_snapshot out;char response[8192];pocket_link_config link={0};strcpy(link.origin,"https://fixture.invalid");
 pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_UNFETCHED,"initial usage must be unfetched");
 pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);
 CHECK(http_calls==1 && last_target==0,"usage polling is not target scoped");
 CHECK(usage_logs==1 && !strcmp(usage_log,"target=0 http=200 decoded=1 p0=3 m0=1 p1=3 m1=1 p2=3 m2=2"),"safe decoded Usage observation missing");
 CHECK(out.providers[0].availability==POCKET_USAGE_AVAILABLE && out.providers[0].metrics[0].has_value && out.providers[0].metrics[0].value==0,"genuine zero was lost");
 CHECK(out.providers[2].metric_count==2 && out.providers[2].metrics[0].unit==POCKET_USAGE_USD_CREDIT && out.providers[2].metrics[0].category==POCKET_USAGE_INCLUDED_CREDIT && out.providers[2].metrics[1].category==POCKET_USAGE_PURCHASED_CREDIT,"included and purchased credit merged");
 pocket_usage_poll(0,&link,response,sizeof(response));CHECK(http_calls==1,"usage polled faster than its bounded cadence");
 pocket_usage_read(1,&out);CHECK(out.providers[0].availability==POCKET_USAGE_UNFETCHED,"windows usage leaked into linux");
 clock_now+=31;fixture_index=1;pocket_usage_poll(1,&link,response,sizeof(response));pocket_usage_read(1,&out);
 CHECK(last_target==1 && out.providers[0].availability==POCKET_USAGE_PARTIAL && !out.providers[0].metrics[1].has_value,"missing metric was not preserved as unknown");
 for(int i=2;i<10;i++){clock_now+=31;fixture_index=i;pocket_usage_poll(1,&link,response,sizeof(response));pocket_usage_read(1,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED,"malformed usage became available");CHECK(out.providers[0].metrics[0].has_value && out.providers[0].metrics[0].availability==POCKET_USAGE_STALE,"failed refresh erased or freshened cached data");}
 for(int i=12;i<14;i++){clock_now+=31;fixture_index=i;pocket_usage_poll(1,&link,response,sizeof(response));pocket_usage_read(1,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED,"duplicate fields or excessive JSON nesting were accepted");}
 clock_now+=31;fixture_index=10;pocket_usage_poll(1,&link,response,sizeof(response));pocket_usage_read(1,&out);
 CHECK(out.providers[0].availability==POCKET_USAGE_NOT_CONFIGURED && out.providers[0].metric_count==0,"legacy not-configured route was not accepted");
 clock_now+=31;fixture_index=11;pocket_usage_poll(1,&link,response,sizeof(response));pocket_usage_read(1,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED && !out.providers[0].metric_count,"invalid root did not fail closed");
 clock_now+=31;fixture_index=0;pocket_usage_poll(0,&link,response,sizeof(response));fail_http=1;clock_now+=31;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);
 CHECK(out.providers[0].availability==POCKET_USAGE_FAILED && out.providers[0].metrics[0].availability==POCKET_USAGE_STALE,"transport failure presented fresh values");
 fail_http=0;fail_allocate=1;clock_now+=31;pocket_usage_poll(0,&link,response,sizeof(response));CHECK(live_allocations==0,"allocation failure leaked temporary state");fail_allocate=0;
 pocket_usage_disconnected(0);pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_UNAVAILABLE,"offline bridge was confused with unconfigured account");
 fixture_index=0;strcpy(link.origin,"https://second-fixture.invalid");pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_STALE && out.providers[0].metric_count==1,"changed endpoint did not refresh or old timestamps became fresh");
 clock_now+=31;fixture_index=14;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_STALE && out.providers[0].metrics[0].has_value && !out.providers[0].metrics[0].has_fetched_at,"known but untimed observation was erased or presented fresh");
 clock_now+=31;fixture_index=15;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability!=POCKET_USAGE_FAILED && out.providers[0].metrics[0].window_minutes==0,"unknown zero-duration window rejected the valid report");
 clock_now+=31;fixture_index=16;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED,"trailing non-JSON data was accepted");
 clock_now+=31;fixture_index=17;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED,"multiple JSON roots were accepted");
 clock_now+=31;fixture_index=18;pocket_usage_poll(0,&link,response,sizeof(response));pocket_usage_read(0,&out);CHECK(out.providers[0].availability==POCKET_USAGE_FAILED,"embedded NUL hid trailing HTTP payload bytes");
 clock_now+=31;fixture_index=0;pocket_usage_poll(0,&link,response,sizeof response);
 int before_http=http_calls,before_logs=usage_logs;
 pocket_usage_force(0);pocket_usage_poll(0,&link,response,sizeof response);
 CHECK(http_calls==before_http+1 && usage_logs==before_logs,"forced refresh failed or identical observation repeated its log");
 pocket_usage_poll(0,&link,response,sizeof response);CHECK(http_calls==before_http+1,"force bypass became continuous polling");
 fail_http=1;pocket_usage_force(0);pocket_usage_poll(0,&link,response,sizeof response);
 CHECK(strstr(usage_log,"http=0 decoded=0 p0=6")!=NULL,"failed Usage observation was hidden");
 pocket_usage_disconnected(0);CHECK(strstr(usage_log,"http=0 decoded=0 p0=1")!=NULL,"unavailable and failed Usage observations merged");
 CHECK(live_allocations==0,"usage snapshot leaked temporary memory");puts("PASS: usage decoding, null/zero, target isolation, cadence, cached failures and bounded allocation");return 0;
}
'''


def compile_fixture(temp, source, compiler, real_cjson=None):
    headers = {'esp_err.h': VOICE_HEADERS['esp_err.h'], 'time.h': VOICE_HEADERS['time.h'], 'esp_log.h': VOICE_HEADERS['esp_log.h'],
               'freertos/FreeRTOS.h': VOICE_HEADERS['freertos/FreeRTOS.h']}
    if not real_cjson:
        headers['cJSON.h'] = CJSON_HEADER
    for name, value in headers.items():
        path = temp/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
    # The production source sees SDK headers. Static trees substitute only the
    # third-party cJSON parser in self-contained host CI; local verification can
    # also link the exact cached SDK cJSON parser against these same JSON bytes.
    generated = trees()
    if real_cjson:
        generated = '\n'.join(line for line in generated.splitlines()
                              if line.startswith('static const char *json_fixtures'))
    harness = temp/'usage_client.c'
    harness.write_text('#include "cJSON.h"\n#include <math.h>\n#include <stddef.h>\n'+generated+'\n'+HARNESS, encoding='utf-8')
    binary = temp/('usage-client.exe' if os.name == 'nt' else 'usage-client')
    command = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-D_CRT_SECURE_NO_WARNINGS',
               '-I'+str(temp), '-I'+str(source/'main'), '-I'+str(source/'core')]
    if real_cjson:
        command += ['-DTEST_REAL_CJSON', '-I'+str(real_cjson), str(real_cjson/'cJSON.c')]
    command += [str(harness), '-o', str(binary)]
    if os.name != 'nt':
        command += ['-lm']
    return subprocess.run(command, capture_output=True, text=True), binary


class FirmwareUsageClientTests(unittest.TestCase):
    def test_bounded_target_scoped_usage_client(self):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1]/'firmware'
        with tempfile.TemporaryDirectory() as directory:
            compiled, binary = compile_fixture(pathlib.Path(directory), source, compiler)
            self.assertEqual(compiled.returncode, 0, compiled.stdout+compiled.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertIn('PASS:', run.stdout)
