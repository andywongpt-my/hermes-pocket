"""Native checks of project-local HTTP I/O wrappers; no sockets or RTOS tasks."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HEADERS = {
    'esp_http_client.h': '#pragma once\n#include "esp_event.h"\n',
    'freertos/FreeRTOS.h': '#pragma once\n#include <stdint.h>\ntypedef unsigned TickType_t;\n',
    'freertos/task.h': '#pragma once\ntypedef void *TaskHandle_t;\nTaskHandle_t xTaskGetCurrentTaskHandle(void);\n',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'esp_transport.h': '#pragma once\ntypedef void *esp_transport_handle_t;\n',
    'esp_event.h': '#pragma once\n#include <stddef.h>\n#include <stdint.h>\n#include "freertos/FreeRTOS.h"\ntypedef int esp_err_t;\ntypedef const char *esp_event_base_t;\nextern const esp_event_base_t ESP_HTTP_CLIENT_EVENT;\n',
}

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pocket_http_timing.h"
#include "esp_transport.h"
#include "esp_event.h"
#include "freertos/task.h"
int __wrap_esp_transport_write(esp_transport_handle_t,const char *,int,int);
int __wrap_esp_transport_read(esp_transport_handle_t,char *,int,int);
esp_err_t __wrap_esp_event_post(esp_event_base_t,int32_t,const void *,size_t,TickType_t);
static const char http_base[]="fixture-http",other_base[]="fixture-other";
const esp_event_base_t ESP_HTTP_CLIENT_EVENT=http_base;
static TaskHandle_t current_task=(void *)(uintptr_t)1;
static int64_t clock_us;
static unsigned timer_calls,real_writes,real_reads,real_posts,write_delay,read_delay,post_delay;
static int forced_write,forced_read,forced_post;
static const char payload[]="synthetic";
static char response[8];
TaskHandle_t xTaskGetCurrentTaskHandle(void){return current_task;}
int64_t esp_timer_get_time(void){timer_calls++;return clock_us;}
int __real_esp_transport_write(esp_transport_handle_t t,const char *data,int len,int timeout){assert(t==(void *)(uintptr_t)7 && data==payload && len>=0 && timeout==15000);real_writes++;clock_us+=write_delay;return forced_write?forced_write:len;}
int __real_esp_transport_read(esp_transport_handle_t t,char *data,int len,int timeout){assert(t==(void *)(uintptr_t)7 && data==response && len==8 && timeout==15000);real_reads++;clock_us+=read_delay;data[0]='x';return forced_read?forced_read:1;}
esp_err_t __real_esp_event_post(esp_event_base_t base,int32_t id,const void *data,size_t len,TickType_t timeout){assert((base==http_base || base==other_base) && id==42 && data==payload && len==sizeof payload && timeout==2000);real_posts++;clock_us+=post_delay;return forced_post;}
static int write_bytes(int bytes){return __wrap_esp_transport_write((void *)(uintptr_t)7,payload,bytes,15000);}
static int read_response(void){return __wrap_esp_transport_read((void *)(uintptr_t)7,response,sizeof response,15000);}
static int post(esp_event_base_t base){return __wrap_esp_event_post(base,42,payload,sizeof payload,2000);}
static void reset(void){pocket_http_timing_end(NULL);current_task=(void *)(uintptr_t)1;clock_us=0;timer_calls=real_writes=real_reads=real_posts=write_delay=read_delay=post_delay=0;forced_write=forced_read=forced_post=0;memset(response,0,sizeof response);}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 for(unsigned test=0;test<3;test++){
  reset();pocket_http_timing_begin(true);write_delay=3000;CHECK(write_bytes(300)==300,"header result changed");pocket_http_timing_headers_sent();
  write_delay=test==1?662000:0;post_delay=test==0?662000:0;read_delay=test==2?662000:0;
  CHECK(post(http_base)==0 && write_bytes(2756)==2756 && read_response()==1,"underlying I/O behavior changed");
  pocket_http_io_timing io;pocket_http_timing_end(&io);
  CHECK(io.write_us==(test==1?665000:3000) && io.body_write_us==(test==1?662000:0),"body write delay was attributed to another phase");
  CHECK(io.read_us==(test==2?662000:0) && io.event_post_us==(test==0?662000:0),"read/event delay not separated");
  CHECK(io.write_calls==2 && io.read_calls==1 && io.event_posts==1 && io.body_bytes==2756,"numeric counts or acknowledged body bytes changed");
  CHECK(clock_us==665000 && timer_calls==8 && real_writes==2 && real_reads==1 && real_posts==1,"I/O repeated or extra timer calls introduced");
 }
 reset();write_delay=5;read_delay=7;post_delay=11;CHECK(write_bytes(9)==9 && read_response()==1 && post(http_base)==0,"inactive forwarding changed");CHECK(!timer_calls,"inactive calls were timed");
 pocket_http_timing_begin(true);current_task=(void *)(uintptr_t)2;pocket_http_timing_headers_sent();CHECK(write_bytes(9)==9 && read_response()==1 && post(http_base)==0,"another task changed behavior");
 current_task=(void *)(uintptr_t)1;CHECK(post(other_base)==0,"non-HTTP event changed behavior");CHECK(!timer_calls,"other task/base was timed");
 CHECK(write_bytes(100)==100,"owner header forwarding failed");pocket_http_io_timing io;pocket_http_timing_end(&io);
 CHECK(io.write_calls==1 && io.write_us==5 && !io.body_bytes && !io.body_write_us && !io.read_calls && !io.event_posts,"cross-task event or marker contaminated stats");
 unsigned before=timer_calls;CHECK(write_bytes(9)==9 && post(http_base)==0,"end did not restore transparent forwarding");CHECK(timer_calls==before,"end left timing active");
 reset();pocket_http_timing_begin(false);pocket_http_timing_headers_sent();CHECK(write_bytes(9)==9 && post(http_base)==0,"disabled forwarding changed");pocket_http_timing_end(&io);CHECK(!timer_calls && !io.write_calls && !io.event_posts,"non-chunk trace remained enabled");
 reset();pocket_http_timing_begin(true);pocket_http_timing_headers_sent();write_delay=100;forced_write=1000;CHECK(write_bytes(2756)==1000,"partial result changed");forced_write=756;CHECK(write_bytes(756)==756,"partial tail result changed");forced_write=-1;CHECK(write_bytes(9)==-1,"write error changed");forced_read=-5;CHECK(read_response()==-5,"read error changed");forced_post=-6;CHECK(post(http_base)==-6,"post error changed");pocket_http_timing_end(&io);
 CHECK(io.body_bytes==1756 && io.write_calls==3 && io.body_write_us==300 && io.read_calls==1 && io.event_posts==1,"partial/error stats lost or bytes invented");CHECK(real_writes==3 && real_reads==1 && real_posts==1,"wrapper retried an uncertain operation");
 puts("PASS: independent write/read/post delays, task isolation, no-op forwarding and original results");return 0;
}
'''

class FirmwareHTTPTimingTests(unittest.TestCase):
    def test_task_scoped_io_phases_and_transparent_forwarding(self):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'LLVM/bin/clang.exe'
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1] / 'firmware/main'
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            for name, data in HEADERS.items():
                path = temp / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(data, encoding='utf-8')
            harness = temp / 'timing.c'
            harness.write_text(HARNESS, encoding='utf-8')
            binary = temp / ('timing.exe' if os.name == 'nt' else 'timing')
            compiled = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(temp), '-I'+str(source), str(source/'pocket_http_timing.c'), str(harness), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS:', result.stdout)