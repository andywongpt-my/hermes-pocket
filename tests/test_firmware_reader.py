"""Exercise the actual bounded reader using offline HTTP bytes and SDK cJSON."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from test_firmware_voice import HEADERS


REVISION = '1' * 64
CURSOR = 'opaque_NEXT-0123456789'
PREVIOUS = 'opaque_PREV-9876543210'
MAX_CURSOR = 'A'*128
LONG_ID = 't'*64


def page(text='中'*254+'🙂ab', **changes):
    value = dict(version=1, task_id='task-a', result_revision=REVISION,
                 page=0, page_count=2, utf8_bytes=len(text.encode('utf-8')),
                 total_utf8_bytes=781, text=text, prev=None, next=CURSOR, eof=False)
    value.update(changes)
    return value


FIXTURES = {
    'list': [dict(request_id='task-a', state='completed', seq=2),
             dict(request_id='task-b', state='completed', seq=4)],
    'view': dict(request_id='task-a', state='completed', seq=2,
                 result='legacy', truncated=False),
    'running': [dict(request_id='task-a', state='running', seq=2)],
    'first': page(),
    'last': page('last\x00\x01\t\r\n🙂', page=1, prev=PREVIOUS, next=None, eof=True),
    'back': page(),
    'empty': page('', page_count=1, total_utf8_bytes=0, next=None, eof=True),
    'boundary': page('中'*254+'🙂'+'ab', page_count=1,
                     total_utf8_bytes=768, next=None, eof=True),
    'ascii': page('a'*768, page_count=86, total_utf8_bytes=65536),
    'ascii_last': page('z'*256, page=85, page_count=86,
                       total_utf8_bytes=65536, prev=PREVIOUS, next=None, eof=True),
    'wrong_id': page(task_id='task-b'),
    'changed': page(result_revision='2'*64, page=1, prev=PREVIOUS, next=None, eof=True),
    'wrong_version': page(version=2),
    'fraction': page(page=0.5),
    'boolean_number': page(page=True),
    'bad_count': page(page_count=0),
    'large_count': page(page_count=65537),
    'bad_page': page(page=2),
    'bad_size': page(utf8_bytes=1),
    'large_text': page('a'*769, total_utf8_bytes=1500),
    'large_total': page(total_utf8_bytes=65537),
    'small_total': page(total_utf8_bytes=4),
    'bad_prev': page(prev=PREVIOUS),
    'no_next': page(next=None),
    'bad_eof': page(eof=True),
    'bad_revision': page(result_revision='A'*64),
    'long_cursor': page(next='x'*129),
    'bad_cursor': page(next='bad&query=x'),
    'empty_cursor': page(next=''),
    'unknown_key': page(extra=1),
    'empty_nonfinal': page('', total_utf8_bytes=1000),
    'short_nonfinal': page('a', total_utf8_bytes=1500),
    'nul_id': page(task_id='task-a\x00hidden'),
    'nul_cursor': page(next='abc\x00hidden'),
    'controls': page('\x00\x01\b\f\n\r\t\x1b\x7f中🙂', page_count=1,
                     total_utf8_bytes=16, next=None, eof=True),
}
RAW = {name: json.dumps(value, separators=(',', ':'), ensure_ascii=True)
       for name, value in FIXTURES.items()}
RAW['raw_unicode'] = json.dumps(FIXTURES['boundary'], separators=(',', ':'), ensure_ascii=False)
RAW['duplicate'] = RAW['first'].replace('"version":1', '"version":1,"version":1')
RAW['trailing'] = RAW['first'] + ' []'
RAW['bad_escape'] = RAW['first'].replace('"text":"', '"text":"\\q')
RAW['bad_surrogate'] = RAW['first'].replace('"text":"', '"text":"\\ud800')
RAW['lone_low_surrogate'] = RAW['first'].replace('"text":"', '"text":"\\udc00')
RAW['bad_number'] = RAW['first'].replace('"page":0', '"page":00')
RAW['exponent'] = RAW['first'].replace('"page":0', '"page":0e0')
RAW['nested'] = RAW['first'].replace('"page":0', '"page":{"x":0}')
RAW['nul_key'] = RAW['first'].replace('"version":1', '"version\\u0000hidden":1')
RAW['missing'] = RAW['first'].replace('"eof":false', '"oops":false')
RAW['trailing_comma_without_close'] = RAW['first'][:-1] + ','
RAW['trailing_comma'] = RAW['first'][:-1] + ',}'
RAW['missing_close'] = RAW['first'][:-1]
RAW['extra_close'] = RAW['first'] + '}'
RAW['bad_last_total'] = json.dumps(page('x'*14, page=1, prev=PREVIOUS, next=None, eof=True), separators=(',', ':'))
RAW['bad_count_turn'] = json.dumps(page('x'*13, page=1, page_count=3, prev=PREVIOUS), separators=(',', ':'))
RAW['bad_page_turn'] = json.dumps(page('x'*13, page=0), separators=(',', ':'))
RAW['max_cursor'] = json.dumps(page(next=MAX_CURSOR), separators=(',', ':'))
RAW['long_list'] = json.dumps([dict(request_id=LONG_ID, state='completed', seq=2)], separators=(',', ':'))
RAW['long_view'] = json.dumps(dict(request_id=LONG_ID, state='completed', seq=2, result='legacy', truncated=False), separators=(',', ':'))
RAW['long_first'] = json.dumps(page(task_id=LONG_ID, next=MAX_CURSOR), separators=(',', ':'))
RAW['long_last'] = json.dumps(dict(FIXTURES['last'], task_id=LONG_ID), separators=(',', ':'))
for index in range(86):
    text = chr(33+index % 80) * (768 if index < 85 else 256)
    RAW['full_'+str(index)] = json.dumps(page(text, page=index, page_count=86,
                                           total_utf8_bytes=65536,
                                           prev=PREVIOUS if index else None,
                                           next=CURSOR if index<85 else None,
                                           eof=index==85), separators=(',', ':'))


HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_reader.h"
#include "pocket_tasks_client.c"
static const char *wire;
static int calls,http_status=200,http_error,hook,wire_mode;
static char path_seen[384];
static pocket_link_config link;
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t capacity,pocket_http_result *result){
 (void)origin;(void)token;(void)ca;assert(!body);assert(capacity<=8192);
 calls++;snprintf(path_seen,sizeof path_seen,"%s",path);
 int action=hook;hook=0;
 if(action==1)pocket_reader_close();
 if(action==2)pocket_tasks_next(0);
 if(action==3)pocket_tasks_reset(0);
 if(action==4)pocket_reader_disconnected(0);
 if(action==5)pocket_reader_available(0,false);
 if(action==6){pocket_reader_close();pocket_reader_open(0);}
 if(action==7){pocket_reader_available(1,true);pocket_reader_open(1);}
 if(action==8)strcpy(link.origin,"https://changed.invalid");
 if(action==9){pocket_reader_available(0,true);pocket_reader_open(0);pocket_reader_close();}
 if(http_error)return ESP_FAIL;
 result->status=http_status;
 if(wire_mode==1){result->length=capacity;return ESP_OK;}
 size_t length=strlen(wire);assert(length+9<capacity);memcpy(out,wire,length+1);
 if(wire_mode==2){out[length]=0;memcpy(out+length+1,"hidden",7);length+=7;}
 if(wire_mode==3){memset(out,' ',8001);out[8001]=0;length=8001;}
 if(wire_mode==4){char *s=strstr(out,"\\u4e2d");assert(s);s[0]=(char)0xc0;s[1]=(char)0x80;}
 if(wire_mode==5){memset(out+length,' ',8000-length);out[8000]=0;length=8000;}
 result->length=length;return ESP_OK;
}
void pocket_http_close(void){}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
static char response[8192],text[769];
static pocket_reader_snapshot out;
static void seed(unsigned target){
 wire=fx_list;pocket_tasks_refresh(target);assert(pocket_tasks_poll(target,&link,response,sizeof response));
 wire=fx_view;assert(pocket_tasks_poll(target,&link,response,sizeof response));
 pocket_reader_available(target,true);
}
static bool fetch(const char *fixture){wire=fixture;return pocket_reader_poll(out.target,&link,response,sizeof response);}
int main(int argc,char **argv){
 strcpy(link.origin,"https://reader-fixture.invalid");seed(0);pocket_reader_read(&out,text,sizeof text);
 CHECK(!pocket_reader_active() && !out.active && out.status==POCKET_READER_CLOSED,"reader did not start closed");
 if(argc>1 && !strcmp(argv[1],"capability")){
  pocket_reader_available(0,false);CHECK(!pocket_reader_open(0),"legacy backend opened reader");
  pocket_reader_available(0,true);pocket_tasks_reset(0);CHECK(!pocket_reader_open(0),"missing task opened reader");
  wire=fx_running;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);
  pocket_reader_available(0,true);CHECK(!pocket_reader_open(0),"noncompleted task opened reader");
  CHECK(!pocket_reader_open(2) && !pocket_reader_turn(1),"invalid target/closed reader queued work");
 }else if(argc>1 && !strcmp(argv[1],"invalid")){
  CHECK(argc>2,"missing fixture");wire=fixtures[atoi(argv[2])];pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);
  CHECK(fetch(wire),"bad response request not processed");pocket_reader_read(&out,text,sizeof text);
  CHECK(out.status==POCKET_READER_FAILED && !out.has_page && !out.has_prev && !out.has_next && text[0]==0,"malformed or inconsistent page accepted");
 }else if(argc>1 && !strcmp(argv[1],"late")){
  CHECK(argc>2,"missing hook");seed(1);pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);
  uint32_t generation=out.generation;int action=atoi(argv[2]);hook=action;fetch(fx_first);pocket_reader_read(&out,text,sizeof text);
  CHECK(!out.has_page && out.generation!=generation,"late reader page crossed generation/origin");
  if(action==6)CHECK(out.status==POCKET_READER_LOADING,"reopened reader lost pending work");
 }else if(argc>1 && !strcmp(argv[1],"ownership")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_first);
  pocket_reader_read(&out,text,sizeof text);CHECK(out.has_page,"page did not publish");
  pocket_tasks_snapshot legacy;pocket_tasks_read(0,&legacy);CHECK(!legacy.has_result,"reader bytes leaked as legacy result");
  pocket_tasks_refresh(1);int before=calls;wire=fx_list;CHECK(!pocket_tasks_poll(1,&link,response,sizeof response) && calls==before,"background request ran while reader owned cache");
  pocket_reader_read(&out,text,sizeof text);CHECK(out.has_page && out.utf8_bytes==768 && (unsigned char)text[0]==0xe4,"background work overwrote page");
  pocket_reader_close();CHECK(pocket_tasks_poll(1,&link,response,sizeof response),"paused tasks did not resume");
  pocket_tasks_reset(0);seed(0);pocket_tasks_open(0);hook=9;wire=fx_view;
  CHECK(pocket_tasks_poll(0,&link,response,sizeof response),"inflight legacy GET missing");pocket_tasks_read(0,&legacy);
  CHECK(!legacy.has_result,"legacy response survived reader open/close generation");
 }else if(argc>1 && !strcmp(argv[1],"errors")){
  for(int code=0;code<5;code++){
   pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);
   http_status=code==0?401:code==1?403:code==2?409:code==3?404:416;fetch(fx_first);pocket_reader_read(&out,text,sizeof text);
   CHECK(!out.has_page,"HTTP error published page");
   CHECK(out.status==(code<2?POCKET_READER_AUTH_REQUIRED:code==2?POCKET_READER_REVISION_CHANGED:POCKET_READER_FAILED),"HTTP error status not explicit");
  }http_status=200;
  for(wire_mode=1;wire_mode<=4;wire_mode++){
   pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_first);pocket_reader_read(&out,text,sizeof text);
   CHECK(out.status==POCKET_READER_FAILED && !out.has_page,"wire overflow/NUL/invalid UTF8 accepted");
  }wire_mode=0;
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);http_error=1;fetch(fx_first);pocket_reader_read(&out,text,sizeof text);
  CHECK(out.status==POCKET_READER_FAILED,"transport failure not explicit");
 }else if(argc>1 && !strcmp(argv[1],"limits")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);wire_mode=5;fetch(fx_max_cursor);wire_mode=0;
  pocket_reader_read(&out,text,sizeof text);CHECK(out.has_page && out.utf8_bytes==768,"exact 8000-byte response rejected");
  char guarded[3]={'x','y','z'};pocket_reader_read(&out,guarded,2);CHECK(!out.has_page && !out.utf8_bytes && !guarded[0] && guarded[1]=='y' && guarded[2]=='z',"short read buffer copied partial page or overflowed");
  pocket_reader_read(&out,text,sizeof text);CHECK(out.has_page && out.utf8_bytes==768,"short read damaged stored page");
  CHECK(pocket_reader_turn(1),"128-byte cursor turn missing");fetch(fx_last);
  const char *query=strstr(path_seen,"?cursor=");CHECK(query && strlen(query+8)==128 && !strcmp(query+8,max_cursor),"128-byte cursor changed or truncated");
  pocket_reader_close();pocket_tasks_reset(0);wire=fx_long_list;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);
  wire=fx_long_view;pocket_tasks_poll(0,&link,response,sizeof response);pocket_reader_available(0,true);CHECK(pocket_reader_open(0),"64-byte cached task could not open");
  pocket_reader_read(&out,text,sizeof text);fetch(fx_long_first);CHECK(pocket_reader_turn(1),"long task next missing");fetch(fx_long_last);pocket_reader_read(&out,text,sizeof text);
  char expected[256];snprintf(expected,sizeof expected,"/targets/windows/tasks/%s/result/pages?cursor=%s",long_id,max_cursor);
  CHECK(out.has_page && strlen(out.task_id)==64 && !strcmp(path_seen,expected) && strlen(path_seen)<256,"maximum task/cursor route truncated");
  int before=calls;CHECK(!pocket_reader_poll(0,&link,response,8193) && !pocket_reader_poll(2,&link,response,sizeof response) && calls==before,"invalid poll capacity/target performed IO");
 }else if(argc>1 && !strcmp(argv[1],"metadata-turn")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_first);
  CHECK(pocket_reader_turn(1),"next not queued");fetch(fixtures[atoi(argv[2])]);pocket_reader_read(&out,text,sizeof text);
  CHECK(out.status==POCKET_READER_FAILED && !out.has_page,"inconsistent navigation metadata accepted");
 }else if(argc>1 && !strcmp(argv[1],"full-result")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);unsigned total=0;
  for(unsigned i=0;i<86;i++){
   if(i)CHECK(pocket_reader_turn(1),"64KiB next missing");fetch(full_pages[i]);pocket_reader_read(&out,text,sizeof text);
   CHECK(out.has_page && out.page==i && out.page_count==86 && out.total_utf8_bytes==65536,"64KiB page rejected");
   CHECK(out.utf8_bytes==(i<85?768u:256u),"64KiB page length changed");
   for(unsigned j=0;j<out.utf8_bytes;j++)CHECK(text[j]==(char)(33+i%80),"64KiB reconstruction changed a byte");
   total+=out.utf8_bytes;
  }
  CHECK(total==65536 && out.eof && !pocket_reader_turn(1),"64KiB result truncated or extended");
  for(unsigned i=85;i>0;i--){CHECK(pocket_reader_turn(-1),"64KiB previous missing");fetch(full_pages[i-1]);}
  pocket_reader_read(&out,text,sizeof text);CHECK(out.has_page && out.page==0,"64KiB backward navigation failed");
 }else if(argc>1 && !strcmp(argv[1],"revision")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_first);
  CHECK(pocket_reader_turn(1),"next not queued");fetch(fx_changed);pocket_reader_read(&out,text,sizeof text);
  CHECK(out.status==POCKET_READER_REVISION_CHANGED && !out.has_page && !out.has_next && !out.has_prev,"changed result revision mixed pages");
 }else if(argc>1 && !strcmp(argv[1],"empty")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_empty);pocket_reader_read(&out,text,sizeof text);
  CHECK(out.has_page && out.utf8_bytes==0 && out.total_utf8_bytes==0 && out.page_count==1 && out.eof && !out.has_next && !out.has_prev,"empty result became missing");
 }else if(argc>1 && !strcmp(argv[1],"controls")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_controls);pocket_reader_read(&out,text,sizeof text);
  static const unsigned char expected[]={0,1,8,12,10,13,9,27,127,0xe4,0xb8,0xad,0xf0,0x9f,0x99,0x82};
  CHECK(out.has_page && out.utf8_bytes==sizeof expected && !memcmp(text,expected,sizeof expected) && text[sizeof expected]==0,"escaped controls/NUL/Unicode changed");
 }else if(argc>1 && !strcmp(argv[1],"raw-unicode")){
  pocket_reader_open(0);pocket_reader_read(&out,text,sizeof text);fetch(fx_raw_unicode);pocket_reader_read(&out,text,sizeof text);
  CHECK(out.has_page && out.utf8_bytes==768 && !memcmp(text+762,"\xf0\x9f\x99\x82" "ab",6),"raw UTF8 boundary changed");
 }else{
  int before=calls;CHECK(pocket_reader_open(0) && calls==before,"open performed HTTP or failed");
  pocket_reader_read(&out,text,sizeof text);CHECK(out.active && out.status==POCKET_READER_LOADING && !out.has_page,"open not loading");
  CHECK(!pocket_reader_turn(1) && !pocket_reader_poll(1,&link,response,sizeof response),"loading/wrong target fetched");
  CHECK(fetch(fx_first) && calls==before+1,"first GET missing");pocket_reader_read(&out,text,sizeof text);
  CHECK(!strcmp(path_seen,"/targets/windows/tasks/task-a/result/pages"),"first GET did not use exact cursorless route");
  CHECK(out.has_page && out.status==POCKET_READER_READY && out.page==0 && out.page_count==2 && out.utf8_bytes==768 && out.total_utf8_bytes==781 && !out.has_prev && out.has_next,"first metadata incorrect");
  CHECK(!strcmp(out.task_id,"task-a") && !strcmp(out.result_revision,"1111111111111111111111111111111111111111111111111111111111111111"),"identity/revision missing");
  CHECK(!memcmp(text+762,"\xf0\x9f\x99\x82" "ab",6),"Chinese/emoji boundary corrupted");
  text[0]='X';pocket_reader_read(&out,text,sizeof text);CHECK((unsigned char)text[0]==0xe4,"caller copy mutated cache");
  before=calls;CHECK(!fetch(fx_first) && calls==before && !pocket_reader_turn(-1) && !pocket_reader_turn(0),"unsolicited or invalid-direction GET");
  CHECK(pocket_reader_turn(1) && !pocket_reader_turn(1) && calls==before,"turn duplicated work or performed IO");fetch(fx_last);pocket_reader_read(&out,text,sizeof text);
  CHECK(!strcmp(path_seen,"/targets/windows/tasks/task-a/result/pages?cursor=opaque_NEXT-0123456789"),"next cursor not echoed verbatim");
  static const unsigned char last[]={108,97,115,116,0,1,9,13,10,0xf0,0x9f,0x99,0x82};
  CHECK(out.has_page && out.page==1 && out.eof && out.has_prev && !out.has_next && out.utf8_bytes==sizeof last && !memcmp(text,last,sizeof last),"last text/control metadata incorrect");
  CHECK(!pocket_reader_turn(1) && pocket_reader_turn(-1),"page end navigation incorrect");fetch(fx_back);pocket_reader_read(&out,text,sizeof text);
  CHECK(!strcmp(path_seen,"/targets/windows/tasks/task-a/result/pages?cursor=opaque_PREV-9876543210") && out.page==0,"previous cursor not echoed");
  for(int i=0;i<120;i++){pocket_reader_turn(1);fetch(fx_last);pocket_reader_turn(-1);fetch(fx_back);}
  CHECK(sizeof reader<=512 && sizeof out<200 && sizeof detail.result==769,"reader memory grew beyond metadata and one shared page");
  pocket_reader_close();pocket_reader_read(&out,text,sizeof text);CHECK(!out.active && !out.has_page && !pocket_reader_active() && text[0]==0,"close did not invalidate page");
 }
 printf("PASS: %s; reader_metadata=%zu shared_page=%zu snapshot=%zu\n",argc>1?argv[1]:"pagination",sizeof reader,sizeof detail.result,sizeof out);return 0;
}
'''


class FirmwareReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parents[1]
        cls.compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not cls.compiler and cached.is_file():
            cls.compiler = str(cached)
        cls.cjson = pathlib.Path(os.environ.get('POCKET_CJSON_DIR', cls.root.parent/'HPsdk/idf-5.5.3/components/json/cJSON'))

    def test_actual_reader_against_frozen_contract(self):
        self.assertTrue((self.root/'firmware/main/pocket_reader.h').is_file(),
                        'bounded reader public API has not been implemented')
        if not self.compiler or not (self.cjson/'cJSON.c').is_file():
            self.skipTest('host compiler or cached SDK cJSON unavailable')
        with tempfile.TemporaryDirectory(prefix='reader-', dir=self.root.parent) as directory:
            temp = pathlib.Path(directory)
            for name in ('esp_err.h', 'freertos/FreeRTOS.h'):
                path = temp/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(HEADERS[name], encoding='utf-8')
            fixture_text = ''.join('static const char fx_'+name+'[]='+json.dumps(value, ensure_ascii=False)+';\n'
                                   for name, value in RAW.items())
            fixture_text += 'static const char *fixtures[]={'+','.join('fx_'+name for name in RAW)+'};\n'
            fixture_text += 'static const char *full_pages[]={'+','.join('fx_full_'+str(index) for index in range(86))+'};\n'
            fixture_text += 'static const char max_cursor[]='+json.dumps(MAX_CURSOR)+';\n'
            fixture_text += 'static const char long_id[]='+json.dumps(LONG_ID)+';\n'
            harness = temp/'reader.c'
            harness.write_text(fixture_text+HARNESS, encoding='utf-8')
            binary = temp/('reader.exe' if os.name=='nt' else 'reader')
            command = [self.compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                       '-D_CRT_SECURE_NO_WARNINGS', '-I'+str(temp), '-I'+str(self.root/'firmware/main'),
                       '-I'+str(self.cjson), str(harness), str(self.cjson/'cJSON.c'), '-o', str(binary)]
            if os.name != 'nt':
                command += ['-lm']
            built = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            cases = [[], ['capability'], ['ownership'], ['errors'], ['revision'], ['empty'],
                     ['controls'], ['raw-unicode'], ['full-result'], ['limits']]
            cases += [['late', str(hook)] for hook in range(1, 9)]
            valid = {'list', 'view', 'running', 'first', 'last', 'back', 'empty', 'boundary',
                     'ascii', 'ascii_last', 'controls', 'raw_unicode', 'max_cursor',
                     'long_list', 'long_view', 'long_first', 'long_last'}
            navigation = {'bad_last_total', 'bad_count_turn', 'bad_page_turn'}
            cases += [['metadata-turn', str(index)] for index, name in enumerate(RAW) if name in navigation]
            cases += [['invalid', str(index)] for index, name in enumerate(RAW)
                      if name not in valid | navigation and not name.startswith('full_')]
            all_passed = True
            pagination_output = ''
            for case in cases:
                with self.subTest(case=case or ['pagination']):
                    run = subprocess.run([str(binary)]+case, capture_output=True, text=True, timeout=10)
                    all_passed = all_passed and run.returncode == 0
                    if not case:
                        pagination_output = run.stdout.strip()
                    self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                    self.assertIn('PASS:', run.stdout)
            if all_passed:
                print(f'PASS: actual reader, {len(cases)} offline scenarios, cached SDK cJSON; {pagination_output}')
