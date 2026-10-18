"""Firmware selector against the fixed v2 contract, with SDK cJSON and no I/O."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

CATALOG = {
    'version': 2, 'default_id': 'default', 'models': [
        {'id': 'default', 'name': 'Follow default', 'verified': True},
        {'id': 'opus', 'name': 'Claude Opus', 'verified': True},
        {'id': 'glm_flash', 'name': 'GLM 5.3 Flash', 'verified': False},
    ],
}

HARNESS = r'''
#include "pocket_models_client.h"
#include "pocket_http.h"
#include "cJSON.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#undef assert
#define assert(x) do { if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 77;} } while(0)
static const char *wire;
static unsigned gets,http_status=200,refresh_during_get;
static int http_error;
static unsigned target_under_test=1;
void test_log(const char *tag,const char *format,...){(void)tag;(void)format;}
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,
 const char *body,char *output,size_t capacity,pocket_http_result *result){
 (void)origin;(void)token;(void)ca;
 if(body || strcmp(path,target_under_test?"/targets/linux/models":"/targets/windows/models"))abort();
 gets++;memset(result,0,sizeof *result);result->status=(int)http_status;
 if(refresh_during_get){refresh_during_get=0;pocket_models_refresh(target_under_test);}
 if(http_error)return http_error;
 size_t length=strlen(wire);if(length>=capacity)return ESP_ERR_INVALID_SIZE;
 memcpy(output,wire,length+1);result->length=length;return ESP_OK;
}
static char response[8192];
static pocket_link_config link;
static int fetch(const char *catalog){
 wire=catalog;pocket_models_refresh(target_under_test);
 return pocket_models_poll(target_under_test,&link,response,sizeof response);
}
int main(int argc,char **argv){
 if(argc!=3)return 2;
 const char *scenario=argv[1];const char *catalog=argv[2];
 char body[128],frozen[128];pocket_models_snapshot s;
 if(!strcmp(scenario,"legacy")){
  assert(pocket_models_voice_body(1,POCKET_MODEL_DEFAULT,body,sizeof body));
  assert(!strcmp(body,"{\"chat_id\":\"pocket\"}"));
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,sizeof body));
  assert(!pocket_models_voice_body(1,99,body,sizeof body));
  assert(!pocket_models_voice_body(2,POCKET_MODEL_DEFAULT,body,sizeof body));
 }else if(!strcmp(scenario,"valid")){
  assert(fetch(catalog));pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_READY && s.available_mask==7 && s.verified_mask==3 && gets==1);
  assert(!pocket_models_poll(1,&link,response,sizeof response) && gets==1);
  assert(pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,sizeof body));
  assert(!strcmp(body,"{\"version\":2,\"chat_id\":\"pocket\",\"model_choice\":\"opus\"}"));
  memcpy(frozen,body,strlen(body)+1);
  assert(pocket_models_voice_body(1,POCKET_MODEL_GLM_FLASH,body,sizeof body));
  assert(!strcmp(body,"{\"version\":2,\"chat_id\":\"pocket\",\"model_choice\":\"glm_flash\"}"));
  assert(!strcmp(frozen,"{\"version\":2,\"chat_id\":\"pocket\",\"model_choice\":\"opus\"}"));
  assert(pocket_models_voice_body(1,POCKET_MODEL_DEFAULT,body,sizeof body));
  assert(!strcmp(body,"{\"version\":2,\"chat_id\":\"pocket\",\"model_choice\":\"default\"}"));
  pocket_models_read(0,&s);assert(s.status==POCKET_MODELS_UNFETCHED && !s.available_mask);
 }else if(!strcmp(scenario,"bounds")){
  assert(fetch(catalog));memset(body,'x',sizeof body);
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,8) && !body[0]);
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,NULL,128));
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,0));
 }else if(!strcmp(scenario,"partial")){
  assert(fetch(catalog));pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_READY && s.available_mask==1);
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,sizeof body));
  assert(pocket_models_voice_body(1,POCKET_MODEL_DEFAULT,body,sizeof body));
 }else if(!strcmp(scenario,"refresh-race")){
  refresh_during_get=1;assert(fetch(catalog));pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_LOADING && !s.available_mask);
  assert(pocket_models_poll(1,&link,response,sizeof response));pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_READY && s.available_mask==7 && gets==2);
 }else if(!strcmp(scenario,"disconnected")){
  assert(fetch(catalog));pocket_models_disconnected(1);pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_OFFLINE && !s.available_mask);
  assert(!pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,sizeof body));
  assert(pocket_models_voice_body(1,POCKET_MODEL_DEFAULT,body,sizeof body));
 }else if(!strcmp(scenario,"unsupported") || !strcmp(scenario,"auth") || !strcmp(scenario,"transport")){
  assert(fetch(catalog));
  if(!strcmp(scenario,"unsupported"))http_status=404;
  if(!strcmp(scenario,"auth"))http_status=401;
  if(!strcmp(scenario,"transport"))http_error=ESP_FAIL;
  assert(fetch(catalog));pocket_models_read(1,&s);
  assert(!s.available_mask && !pocket_models_voice_body(1,POCKET_MODEL_OPUS,body,sizeof body));
  assert(pocket_models_voice_body(1,POCKET_MODEL_DEFAULT,body,sizeof body));
  if(http_status==404)assert(s.status==POCKET_MODELS_UNSUPPORTED);
  if(http_status==401)assert(s.status==POCKET_MODELS_AUTH_REQUIRED);
  if(http_error)assert(s.status==POCKET_MODELS_OFFLINE);
 }else{
  assert(fetch(catalog));pocket_models_read(1,&s);
  assert(s.status==POCKET_MODELS_FAILED && !s.available_mask);
  assert(!pocket_models_voice_body(1,POCKET_MODEL_GLM_FLASH,body,sizeof body));
 }
 puts("PASS: bounded selector, v2 voice body, explicit fallback policy");return 0;
}
'''


class FirmwareModelSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_firmware_voice import HEADERS
        compiler=shutil.which('clang') or shutil.which('gcc')
        cached=Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:raise unittest.SkipTest('host C compiler unavailable')
        cls.root=Path(__file__).resolve().parents[1]
        cjson=Path(os.environ.get('POCKET_CJSON_DIR',cls.root.parent/'HPsdk/idf-5.5.3/components/json/cJSON'))
        if not (cjson/'cJSON.c').is_file():raise unittest.SkipTest('cached SDK cJSON unavailable')
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        directory=Path(cls.temp.name)
        for name in ('esp_err.h','esp_log.h','freertos/FreeRTOS.h'):
            path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(HEADERS[name],encoding='utf-8')
        with (directory/'esp_err.h').open('a') as stream:stream.write('\n#define ESP_ERR_INVALID_SIZE 0x104\n')
        (directory/'selector.c').write_text(HARNESS,encoding='utf-8')
        cls.binary=directory/('selector.exe' if os.name=='nt' else 'selector')
        result=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic',
            '-I'+str(directory),'-I'+str(cls.root/'firmware/main'),'-I'+str(cls.root/'firmware/core'),'-I'+str(cjson),
            str(directory/'selector.c'),str(cls.root/'firmware/main/pocket_models_client.c'),
            str(cls.root/'firmware/core/pocket_model.c'),str(cjson/'cJSON.c'),'-o',str(cls.binary)],capture_output=True,text=True,timeout=60)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    def check(self,scenario,catalog=None):
        text=json.dumps(CATALOG if catalog is None else catalog,separators=(',',':')) if not isinstance(catalog,str) else catalog
        result=subprocess.run([str(self.binary),scenario,text],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_catalog_and_frozen_v2_begin(self):self.check('valid')
    def test_legacy_default_requires_explicit_default(self):self.check('legacy')
    def test_body_boundaries(self):self.check('bounds')
    def test_missing_option_is_not_silently_defaulted(self):self.check('partial',{**CATALOG,'models':CATALOG['models'][:1]})
    def test_refresh_during_get_does_not_publish_stale_catalog(self):self.check('refresh-race')
    def test_disconnect_blocks_explicit_selection(self):self.check('disconnected')
    def test_endpoint_auth_and_transport_failures(self):
        for scenario in ('unsupported','auth','transport'):
            with self.subTest(scenario=scenario):self.check(scenario)
    def test_invalid_catalog_is_rejected(self):
        invalid=[{**CATALOG,'version':1},{**CATALOG,'version':True},{**CATALOG,'default_id':'opus'},
            {**CATALOG,'models':CATALOG['models']+CATALOG['models'][:1]},
            {**CATALOG,'models':[CATALOG['models'][0],{'id':'unrecognized','name':'Other','verified':True}]},
            {**CATALOG,'models':[CATALOG['models'][0],{**CATALOG['models'][1],'verified':'true'}]},
            '{"version":2,"version":1,"default_id":"default","models":[]}',
            json.dumps(CATALOG)+' trailing', '{"padding":"'+'x'*1100+'"}',
            '{"nested":'+('['*20)+'0'+(']'*20)+'}']
        for catalog in invalid:
            with self.subTest(catalog_type=type(catalog).__name__):self.check('invalid',catalog)
