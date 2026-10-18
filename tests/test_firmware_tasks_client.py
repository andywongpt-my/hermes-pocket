"""Run the real task client and cached SDK cJSON against synthetic HTTP bytes."""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from test_firmware_voice import HEADERS


def row(name='task-a', state='running', seq=2):
    return dict(request_id=name, state=state, seq=seq)


def view(name='task-a', state='completed', seq=4, result=None, truncated=False):
    return dict(row(name, state, seq), result=result, truncated=truncated)


FIXTURES = [
    [row(), row('task-b', 'completed', 4)],
    view(state='running', seq=3),
    view(result='你'*257),
    view('task-b', result='done', truncated=True),
    [row('linux-only', seq=1)],
    view('linux-only', seq=2, result='Linux'),
    [],
    [row('id-'+str(i), seq=i+1) for i in range(12)],
    [row('id-'+str(i)) for i in range(13)],
    [row(), row()],
    [row(seq=True)],
    [row(seq=1.5)],
    [row(state='invented')],
    [row(name='../bad')],
    [row(name='x'*65)],
    {'tasks': []},
    view(name='wrong-task', result='wrong'),
    view(seq=1, result='old'),
    view(state='running', seq=4, result='regression'),
    view(result=123),
    view(result='a'*767+'你'),
    view(result=''),
    view(result='bad\x00hidden'),
]
RAW_FIXTURES = [json.dumps(value, separators=(',', ':')) for value in FIXTURES]
RAW_FIXTURES += [
    '[{"request_id":"task-a","state":"running","seq":2,"seq":3}]',
    json.dumps(FIXTURES[0])+' trailing',
    json.dumps(FIXTURES[0])+' []',
    '[{"request_id":"task-a","state":"running","seq":2,"extra":[[[[[[]]]]]]}]',
]


HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pocket_tasks_client.c"
static int fixture,http_calls,http_error,http_status=200,wire_mode,hook;
static char last_path[192];
esp_err_t pocket_http_json(const char *origin,const char *path,const char *token,const char *ca,const char *body,char *out,size_t capacity,pocket_http_result *result){
 (void)origin;(void)token;(void)ca;assert(body==NULL);assert(capacity<=8192);
 http_calls++;snprintf(last_path,sizeof last_path,"%s",path);
 if(hook==1){hook=0;pocket_tasks_next(0);}
 else if(hook==2){hook=0;pocket_tasks_refresh(0);}
 else if(hook==3){hook=0;pocket_tasks_reset(0);}
 else if(hook==4){hook=0;pocket_tasks_open(0);}
 if(http_error)return ESP_FAIL;
 result->status=http_status;
 if(wire_mode==1){result->length=capacity;return ESP_OK;}
 size_t length=strlen(fixtures[fixture]);assert(length+9<capacity);
 memcpy(out,fixtures[fixture],length+1);
 if(wire_mode==2){memcpy(out+length+1,"hidden",7);length+=7;}
 result->length=length;return ESP_OK;
}
void pocket_http_close(void){}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(int argc,char **argv){
 pocket_link_config link={0};strcpy(link.origin,"https://fixture.invalid");
 char response[8192];pocket_tasks_snapshot out;
 if(argc>1){
  bool selecting=!strcmp(argv[1],"list-next");
  pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);
  fixture=6;hook=selecting?1:4;pocket_tasks_refresh(0);
  int before=http_calls;pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
  CHECK(http_calls==before+1 && out.count==2 && out.list_status==POCKET_TASKS_LOADING,"in-flight list action failed to preserve cached rows or one-GET limit");
  CHECK(out.selected==(selecting?1u:0u),"in-flight list action lost selected task");
  fixture=0;before=http_calls;
  CHECK(pocket_tasks_poll(0,&link,response,sizeof response) && http_calls==before+1 && !strcmp(last_path,"/targets/windows/tasks"),"selection/open during list lost the pending refresh");
  pocket_tasks_read(0,&out);CHECK(out.list_status==POCKET_TASKS_READY && out.selected==(selecting?1u:0u),"list remained loading or refreshed another selected task");
  fixture=selecting?3:1;before=http_calls;pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
  CHECK(http_calls==before+1 && out.list_status==POCKET_TASKS_READY && out.view_status==POCKET_TASKS_READY,"selected view did not follow settled list");
  CHECK(!strcmp(last_path,selecting?"/targets/windows/tasks/task-b/view":"/targets/windows/tasks/task-a/view"),"settled list fetched the wrong selected ID");
  puts("PASS: selection/open during in-flight list preserves refresh and selected-ID isolation");return 0;
 }
 pocket_tasks_read(0,&out);CHECK(out.count==0 && out.list_status==POCKET_TASKS_UNFETCHED,"initial snapshot is not unfetched");
 CHECK(!pocket_tasks_active(),"empty cache invented an active task");
 CHECK(!pocket_tasks_poll(0,&link,response,sizeof response) && http_calls==0,"unsolicited HTTP request");
 pocket_tasks_refresh(0);CHECK(http_calls==0,"button request performed network IO");
 CHECK(pocket_tasks_poll(0,&link,response,sizeof response),"queued refresh not processed");
 CHECK(!strcmp(last_path,"/targets/windows/tasks"),"list route not target scoped");
 pocket_tasks_read(0,&out);CHECK(out.count==2 && out.selected==0 && out.list_status==POCKET_TASKS_READY,"list not published");
 CHECK(out.entries[0].seq==2 && out.entries[0].state==POCKET_TASK_STATE_RUNNING,"task state/sequence lost");
 out.entries[0].request_id[0]='X';pocket_tasks_read(0,&out);CHECK(!strcmp(out.entries[0].request_id,"task-a"),"snapshot read is not copied");
 fixture=1;CHECK(pocket_tasks_poll(0,&link,response,sizeof response),"selected view not queued after list");
 CHECK(!strcmp(last_path,"/targets/windows/tasks/task-a/view"),"raw or wrong task route used");
 pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_READY && !out.has_result && out.entries[0].seq==3,"null result confused with empty result");
 fixture=2;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
 CHECK(out.has_result && out.truncated && strlen(out.result)==768,"UTF-8 result limit not enforced");
 CHECK((unsigned char)out.result[765]==0xe4 && (unsigned char)out.result[767]==0xa0,"UTF-8 character was split");
 fixture=3;pocket_tasks_next(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
 CHECK(out.selected==1 && !strcmp(out.result,"done") && out.truncated,"selection or server truncation lost");
 fixture=4;pocket_tasks_refresh(1);pocket_tasks_poll(1,&link,response,sizeof response);
 CHECK(!strcmp(last_path,"/targets/linux/tasks"),"linux request used windows route");
 fixture=5;pocket_tasks_poll(1,&link,response,sizeof response);pocket_tasks_read(1,&out);
 CHECK(out.has_result && !strcmp(out.result,"Linux"),"linux view not published");
 pocket_tasks_read(0,&out);CHECK(out.count==2 && !out.has_result && !strcmp(out.entries[1].request_id,"task-b"),"single result cache or target isolation broken");
 pocket_tasks_next(0);fixture=2;pocket_tasks_poll(0,&link,response,sizeof response);
 for(int i=8;i<=15;i++){fixture=i;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.list_status==POCKET_TASKS_FAILED && out.count==2,"invalid list replaced cached snapshot");}
 for(int i=23;i<=26;i++){fixture=i;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.list_status==POCKET_TASKS_FAILED,"duplicate/trailing/deep JSON accepted");}
 for(int i=16;i<=19;i++){fixture=i;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_FAILED && out.entries[0].seq==4 && out.entries[0].state==POCKET_TASK_STATE_COMPLETED,"wrong ID/old sequence/terminal regression accepted");}
 fixture=20;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
 CHECK(out.has_result && out.truncated && strlen(out.result)==767,"partial UTF-8 suffix was copied");
 fixture=21;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);
 CHECK(out.has_result && !out.truncated && out.result[0]==0,"known empty result became missing");
 fixture=22;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_FAILED,"escaped NUL accepted");
 http_status=401;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_AUTH_REQUIRED,"auth error not explicit");http_status=200;
 http_error=1;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_FAILED && out.entries[0].state==POCKET_TASK_STATE_COMPLETED,"transport error invented task failure");http_error=0;
 for(wire_mode=1;wire_mode<=2;wire_mode++){pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.view_status==POCKET_TASKS_FAILED,"overflow or embedded NUL accepted");}wire_mode=0;
 fixture=2;hook=1;pocket_tasks_open(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.selected==1 && !out.has_result,"late selected response crossed task generation");
 fixture=3;pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(!strcmp(out.result,"done"),"new selected request was lost");
 fixture=6;hook=2;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.count==2 && out.list_status==POCKET_TASKS_LOADING,"late list crossed refresh generation");
 pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.count==0 && out.list_status==POCKET_TASKS_READY,"empty list not published");
 CHECK(!pocket_tasks_active(),"authoritative empty list kept fast polling active");
 fixture=7;pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);for(int i=0;i<25;i++)pocket_tasks_next(0);pocket_tasks_read(0,&out);CHECK(out.count==12 && out.selected==1,"selection escaped twelve rows");
 pocket_tasks_disconnected(0);pocket_tasks_read(0,&out);CHECK(out.list_status==POCKET_TASKS_OFFLINE && out.view_status==POCKET_TASKS_OFFLINE && out.count==12,"offline label or cache lost");
 CHECK(pocket_tasks_active(),"disconnect silently treated cached running tasks as finished");
 int calls=http_calls;CHECK(!pocket_tasks_poll(0,&link,response,sizeof response) && http_calls==calls,"offline cache caused automatic retry");
 fixture=4;strcpy(link.origin,"https://other-fixture.invalid");pocket_tasks_refresh(0);pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.count==1 && !strcmp(out.entries[0].request_id,"linux-only") && !out.has_result,"old endpoint data survived origin change");
 hook=3;fixture=5;pocket_tasks_poll(0,&link,response,sizeof response);pocket_tasks_read(0,&out);CHECK(out.count==0 && out.list_status==POCKET_TASKS_UNFETCHED,"late result survived reset");
 calls=http_calls;pocket_tasks_refresh(2);pocket_tasks_open(2);pocket_tasks_next(2);pocket_tasks_read(2,&out);CHECK(!out.count && !pocket_tasks_poll(2,&link,response,sizeof response) && http_calls==calls,"invalid target caused IO");
 CHECK(!pocket_tasks_poll(0,&link,response,8193),"oversized caller buffer accepted");
 printf("PASS: task list/view, UTF-8 bounds, isolation, generations, sequences, honest failures; static=%zu snapshot=%zu entry=%zu\n",sizeof(lists)+sizeof(detail)+sizeof(origins)+sizeof(tasks_lock),sizeof(pocket_tasks_snapshot),sizeof(pocket_task_entry));
 return 0;
}
'''


class FirmwareTasksClientTests(unittest.TestCase):
    def test_actual_tasks_client_with_cached_sdk_json(self):
        compiler=shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:self.skipTest('host C compiler unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]
        cjson=pathlib.Path(os.environ.get('POCKET_CJSON_DIR',root.parent/'HPsdk/idf-5.5.3/components/json/cJSON'))
        if not (cjson/'cJSON.c').is_file():self.skipTest('cached SDK cJSON unavailable; set POCKET_CJSON_DIR')
        with tempfile.TemporaryDirectory() as directory:
            temp=pathlib.Path(directory)
            for name in ('esp_err.h','freertos/FreeRTOS.h'):
                path=temp/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(HEADERS[name],encoding='utf-8')
            fixtures='static const char *fixtures[]={'+','.join(json.dumps(value) for value in RAW_FIXTURES)+'};\n'
            harness=temp/'tasks.c';harness.write_text(fixtures+HARNESS,encoding='utf-8')
            binary=temp/('tasks.exe' if os.name=='nt' else 'tasks')
            command=[compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-D_CRT_SECURE_NO_WARNINGS',
                     '-I'+str(temp),'-I'+str(root/'firmware/main'),'-I'+str(cjson),
                     str(harness),str(cjson/'cJSON.c'),'-o',str(binary)]
            if os.name!='nt':command+=['-lm']
            built=subprocess.run(command,capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            for case in ('','list-next','list-open'):
                with self.subTest(case=case or 'bounded-client'):
                    run=subprocess.run([str(binary)]+([case] if case else []),capture_output=True,text=True,timeout=10)
                    self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    self.assertIn('PASS:',run.stdout)
                    print(run.stdout.strip())
