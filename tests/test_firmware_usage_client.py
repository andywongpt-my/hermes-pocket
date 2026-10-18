"""Execute the actual bounded Usage client at the SDK/JSON-tree boundary."""
import copy
import json
import os
import pathlib
import re
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

COUNTERS = ('runs', 'api_calls', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')
COVERAGE = ('owned_runs', 'observed_runs', 'missing_runs', 'unattributed_runs', 'unmapped_observations', 'provider_observed_runs')


def local_reports():
    rows = [dict(provider=p, availability='not_configured', value=None, fetched_at=None, source=None, metrics=[]) for p in PROVIDERS]
    for row in rows:
        row['local_usage'] = dict(scope='pocket_runs', period='all_recorded', source='Hermes runtime',
            availability='partial', fetched_at=NOW-10, last_seen=NOW-100,
            main=dict(zip(COUNTERS, (4, 5000000000, 9007199254740991, 0, None, 8, 9))),
            auxiliary=dict(zip(COUNTERS, (2, 7, 0, 64, 1, 0, None))),
            coverage=dict(zip(COVERAGE, (5, 4, 1, 0, 0, 4))))
    codex=rows[0]['local_usage'];codex.update(availability='unavailable', last_seen=None)
    codex['main']=dict.fromkeys(COUNTERS);codex['auxiliary']=dict.fromkeys(COUNTERS)
    codex['coverage']['provider_observed_runs']=0
    return rows


LOCAL_START=len(FIXTURES)
FIXTURES.append(local_reports())
item=local_reports();item[1]['local_usage'].update(availability='stale',fetched_at=NOW-3600);FIXTURES.append(item)
item=local_reports()
for row in item:
    row['local_usage'].update(availability='unavailable', fetched_at=None, last_seen=None,
        main=dict.fromkeys(COUNTERS),auxiliary=dict.fromkeys(COUNTERS),coverage=dict.fromkeys(COVERAGE))
FIXTURES.append(item)
item=local_reports()
for row in item:
    row.update(availability='available',metrics=[metric(i) for i in range(4)])
    row['local_usage'].update(availability='available',main=dict.fromkeys(COUNTERS,9007199254740991),auxiliary=dict.fromkeys(COUNTERS,9007199254740991))
    row['local_usage']['coverage']=dict(zip(COVERAGE,(9007199254740991,9007199254740991,0,0,0,9007199254740991)))
assert len(json.dumps(item,separators=(',',':')).encode())<8000
FIXTURES.append(item)
for bad in (-1, 1.5, True, '123', 9007199254740992, 1e100):
    item=local_reports();item[1]['local_usage']['main']['api_calls']=bad;FIXTURES.append(item)
for field,bad in (('scope','all_sessions'),('period','daily'),('source','other runtime'),('availability','not_configured'),('fetched_at',2.5),('last_seen',-1)):
    item=local_reports();item[1]['local_usage'][field]=bad;FIXTURES.append(item)
item=local_reports();item[1]['local_usage']['main']['api_calls_duplicate']=3;FIXTURES.append(item)
LOCAL_END=len(FIXTURES)

RAW_BYTES = {}


def append_wire_fixture(rows, edits):
    """Keep the raw numeric spelling independent of Python/cJSON double rounding."""
    parts = [json.dumps(row, separators=(',', ':')).encode('utf-8') for row in rows]
    claude = next(i for i, row in enumerate(rows) if row['provider'] == 'claude')
    for before, after in edits:
        assert before in parts[claude]
        parts[claude] = parts[claude].replace(before, after, 1)
    RAW_BYTES[len(FIXTURES)] = b'[' + b','.join(parts) + b']'
    FIXTURES.append(rows)


LEXEME_START = len(FIXTURES)
for group, key, rounded, raw, escaped in (
    ('main', 'api_calls', 9007199254740990, b'9007199254740990.5', False),
    ('main', 'api_calls', 9007199254740991, b'9007199254740991.1', True),
    ('auxiliary', 'runs', 0, b'-0', False),
    ('coverage', 'owned_runs', 1, b'1e0', False),
    ('coverage', 'observed_runs', 1, b'1.0', False),
    (None, 'fetched_at', 253402300799, b'253402300798.999999999999', False),
    (None, 'last_seen', 0, b'0e0', False),
):
    item = local_reports()
    item[1].update(availability='available', metrics=[metric(2.5)])
    target = item[1]['local_usage']
    if group:
        target = target[group]
    target[key] = rounded  # The static tree mirrors the already-rounded cJSON number.
    token = b'"' + key.encode() + b'":'
    edits = [(token + str(rounded).encode(), token + raw)]
    if escaped:
        edits += [(b'"local_usage":', b'"local_\\u0075sage":'),
                  (b'"main":', b'"m\\u0061in":'),
                  (b'"api_calls":', b'"\\u0061pi_calls":')]
    append_wire_fixture(item, edits)
LEXEME_END = len(FIXTURES)

# A lexical guard must walk structure, use decoded keys, and ignore future fields.
item = local_reports()
item[1].update(availability='available', metrics=[metric(2.5)])
item[1]['future'] = [{'local_usage': {'main': {'api_calls': 1.5}}}, -0.0]
local = item[1]['local_usage']
local['future'] = [{'api_calls': 1e100}, None, 'quote " slash \\ local_usage main api_calls']
local['main']['future'] = {'api_calls': 1.5}
local['auxiliary']['future'] = -0.0
local['coverage']['future'] = 1e100
item = [item[1], item[2], item[0]]  # Wire order need not equal provider order.
LEXEME_COMPAT = len(FIXTURES)
append_wire_fixture(item, [(b'"local_usage":', b'"local_\\u0075sage":'),
                           (b'"main":', b'"m\\u0061in":'),
                           (b'"api_calls":5000000000', b'"\\u0061pi_calls":5000000000'),
                           (b'"fetched_at":1735689590', b'"fetched_\\u0061t":1735689590')])

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
                          n['number'], string(n['key'][:-10] if n['key'] and n['key'].endswith('_duplicate') else n['key'])])+'},' for n in nodes]
    lines += ['};', 'static cJSON *roots[] = {'+','.join(ptr(n) for n in roots)+'};']
    lines += [f'enum {{ LEXEME_START={LEXEME_START},LEXEME_END={LEXEME_END},LEXEME_COMPAT={LEXEME_COMPAT} }};']
    # Byte arrays avoid ISO C's 4095-character string-literal minimum while
    # exercising the real bounded HTTP payload, including the >4KiB report.
    for i,value in enumerate(FIXTURES):
        encoded=(re.sub(r'"([a-z_]+)_duplicate":',r'"\1":',json.dumps(value,separators=(',',':')))+
                 ('trailing' if i==16 else ' {}' if i==17 else '')).encode('utf-8')
        encoded = RAW_BYTES.get(i, encoded)
        lines.append('static const char json_fixture_'+str(i)+'[] = {'+','.join(map(str,encoded))+',0};')
    lines += ['static const char *json_fixtures[] = {'+','.join('json_fixture_'+str(i) for i in range(len(FIXTURES)))+'};']
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
 CHECK(usage_logs==1 && !strcmp(usage_log,"target=0 http=200 decoded=1 p0=3 m0=1 p1=3 m1=1 p2=3 m2=2 l0=0 l1=0 l2=0"),"safe decoded Usage observation missing");
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
 fail_http=0;fixture_index=19;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(out.providers[1].local.present,"local_usage was silently ignored");
 CHECK(out.providers[1].availability==POCKET_USAGE_NOT_CONFIGURED && !out.providers[1].metric_count,"runtime counters were substituted into account quota");
 CHECK(out.providers[1].local.main[POCKET_USAGE_API_CALLS]==UINT64_C(5000000000) && out.providers[1].local.main[POCKET_USAGE_INPUT_TOKENS]==POCKET_USAGE_COUNTER_MAX,"large exact counters were narrowed or rounded");
 CHECK(out.providers[1].local.main_known==0x6f && out.providers[1].local.auxiliary_known==0x3f,"null and observed zero were merged");
 CHECK(out.providers[1].local.auxiliary[POCKET_USAGE_API_CALLS]==7 && out.providers[1].local.main[POCKET_USAGE_OUTPUT_TOKENS]==0,"main and auxiliary counters were merged");
 CHECK(out.providers[0].local.availability==POCKET_USAGE_UNAVAILABLE && !out.providers[0].local.main_known && !out.providers[0].local.auxiliary_known,"missing Codex observations invented zero usage");
 CHECK(out.providers[0].local.coverage_known==0x3f && out.providers[0].local.coverage[POCKET_USAGE_PROVIDER_OBSERVED_RUNS]==0,"observed zero coverage became unknown");
 fail_http=1;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(out.providers[1].local.availability==POCKET_USAGE_FAILED && out.providers[1].local.fetched_at==1735689590 && out.providers[1].local.main[POCKET_USAGE_API_CALLS]==UINT64_C(5000000000),"failed refresh erased or freshened runtime history");
 fail_http=0;fixture_index=20;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(out.providers[1].local.availability==POCKET_USAGE_STALE && out.providers[1].local.fetched_at==1735686000,"server stale observation was presented as fresh");
 pocket_usage_disconnected(1);pocket_usage_read(1,&out);
 CHECK(out.providers[1].local.availability==POCKET_USAGE_STALE && out.providers[1].local.main_known==0x6f,"offline runtime counters lost their cached/stale state");
 fixture_index=21;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(!out.providers[1].local.main_known && !out.providers[1].local.coverage_known && !out.providers[1].local.has_fetched_at,"unavailable null source became an observed zero/time");
 fixture_index=22;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(out.providers[2].metric_count==4 && out.providers[2].local.main[6]==POCKET_USAGE_COUNTER_MAX && out.providers[2].local.auxiliary[6]==POCKET_USAGE_COUNTER_MAX,"bounded maximal quota/runtime response was rejected or truncated");
 for(int i=23;i<36;i++){
  fixture_index=i;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
  CHECK(out.providers[1].availability==POCKET_USAGE_NOT_CONFIGURED && out.providers[1].local.availability==POCKET_USAGE_FAILED && !out.providers[1].local.main_known,"malformed runtime data contaminated quota or became a valid counter");
  CHECK(out.providers[2].local.availability==POCKET_USAGE_PARTIAL,"one malformed provider erased independent runtime rows");
 }
 int lexeme_failures=0;
 for(int i=LEXEME_START;i<LEXEME_END;i++){
  fixture_index=i;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
  CHECK(out.providers[1].metric_count==1 && out.providers[1].metrics[0].has_value && out.providers[1].metrics[0].value==2.5,"local numeric spelling rejected valid fractional account quota");
  CHECK(out.providers[2].local.availability==POCKET_USAGE_PARTIAL && out.providers[0].local.availability==POCKET_USAGE_UNAVAILABLE,"local numeric spelling erased another provider");
  if(out.providers[1].local.availability!=POCKET_USAGE_FAILED || out.providers[1].local.main_known || out.providers[1].local.auxiliary_known || out.providers[1].local.coverage_known || out.providers[1].local.has_fetched_at || out.providers[1].local.has_last_seen){
   printf("FAIL: noncanonical local numeric token accepted at fixture %d\n",i);lexeme_failures++;
  }
 }
 CHECK(!lexeme_failures,"raw local numeric tokens were rounded into observed integers");
 fixture_index=LEXEME_COMPAT;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(out.providers[1].local.availability==POCKET_USAGE_PARTIAL && out.providers[1].local.main[POCKET_USAGE_API_CALLS]==UINT64_C(5000000000) && out.providers[1].local.main_known==0x6f,"escaped keys or unknown additive fields changed valid local counters");
 CHECK(out.providers[1].local.has_fetched_at && out.providers[1].local.fetched_at==1735689590 && out.providers[1].metrics[0].value==2.5,"wire traversal changed timestamps or legacy fractional quota");
 fixture_index=10;pocket_usage_force(1);pocket_usage_poll(1,&link,response,sizeof response);pocket_usage_read(1,&out);
 CHECK(!out.providers[0].local.present && !out.providers[1].local.present,"legacy server retained another response's runtime counters");
 CHECK(live_allocations==0,"usage snapshot leaked temporary memory");printf("SIZE snapshot=%zu local=%zu\n",sizeof(out),sizeof(pocket_usage_local));puts("PASS: usage decoding, null/zero, exact large local counters, target isolation, stale compatibility and bounded allocation");return 0;
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
                              if line.startswith(('static const char *json_fixtures','static const char json_fixture_','enum { LEXEME_')))
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
    def test_local_and_legacy_usage_with_real_cached_json(self):
        compiler=shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        source=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        sdk_json=source.parent.parent/'HPsdk/idf-5.5.3/components/json/cJSON'
        if not compiler or not (sdk_json/'cJSON.c').is_file():self.skipTest('cached SDK cJSON or host compiler unavailable')
        with tempfile.TemporaryDirectory() as directory:
            compiled,binary=compile_fixture(pathlib.Path(directory),source,compiler,real_cjson=sdk_json)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
            self.assertIn('PASS:',run.stdout)

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
