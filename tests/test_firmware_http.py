"""Execute the real C transport against deterministic SDK boundary fixtures."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


HEADERS = {
    'esp_heap_caps.h': '#pragma once\n#include <stddef.h>\n#define MALLOC_CAP_INTERNAL 1\n#define MALLOC_CAP_8BIT 2\nsize_t heap_caps_get_free_size(unsigned);\nsize_t heap_caps_get_largest_free_block(unsigned);\n',
    'time.h': '#pragma once\n#include <stdint.h>\ntypedef int64_t time_t;\ntime_t pocket_test_time(time_t *);\n#define time pocket_test_time\n',
    'esp_err.h': '''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_SIZE 0x104
''',
    'esp_http_client.h': '''#pragma once
#include <stdbool.h>
#include "esp_err.h"
typedef struct mock_client *esp_http_client_handle_t;
typedef struct { int event_id; void *user_data; void *data; int data_len; } esp_http_client_event_t;
enum { HTTP_EVENT_ON_DATA, HTTP_METHOD_GET, HTTP_METHOD_POST };
typedef struct {
 const char *url, *cert_pem;
 int timeout_ms, buffer_size, buffer_size_tx;
 bool disable_auto_redirect, keep_alive_enable, skip_cert_common_name_check;
 esp_err_t (*event_handler)(esp_http_client_event_t *);
 esp_err_t (*crt_bundle_attach)(void *);
 void *user_data;
} esp_http_client_config_t;
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *);
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t);
esp_err_t esp_http_client_set_url(esp_http_client_handle_t,const char *);
esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t,void *);
esp_err_t esp_http_client_set_method(esp_http_client_handle_t,int);
esp_err_t esp_http_client_set_header(esp_http_client_handle_t,const char *,const char *);
esp_err_t esp_http_client_set_post_field(esp_http_client_handle_t,const char *,int);
esp_err_t esp_http_client_perform(esp_http_client_handle_t);
int esp_http_client_get_status_code(esp_http_client_handle_t);
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t,int *,int *);
''',
    'esp_crt_bundle.h': '#pragma once\n#include "esp_err.h"\nesp_err_t esp_crt_bundle_attach(void *);\n',
    'esp_log.h': '''#pragma once
void test_log(const char *,const char *,...);
#define ESP_LOGW(tag,...) test_log(tag,__VA_ARGS__)
#define ESP_LOGI(tag,...) test_log(tag,__VA_ARGS__)
''',
}

HARNESS = r'''
#include "pocket_http.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
struct mock_client { bool active; void *user_data; };
static struct mock_client mock;
static int failure, verify_flags, reads, performs, cleanups, logs;
static bool custom_ca;
static time_t epoch;
static char diagnostic[256], log_tag[32];
static const char token[]="SYNTHETIC_PRIVATE_TOKEN_0123456789abcdef";
time_t pocket_test_time(time_t *out) { if(out)*out=epoch;return epoch; }
size_t heap_caps_get_free_size(unsigned caps) { (void)caps;return 64000; }
size_t heap_caps_get_largest_free_block(unsigned caps) { (void)caps;return 48000; }
void test_log(const char *tag,const char *fmt,...) {
 va_list args;va_start(args,fmt);vsnprintf(diagnostic,sizeof(diagnostic),fmt,args);va_end(args);
 snprintf(log_tag,sizeof(log_tag),"%s",tag);logs++;
}
esp_err_t esp_crt_bundle_attach(void *unused) { (void)unused;return ESP_OK; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t *cfg) {
 assert(cfg->disable_auto_redirect && !cfg->skip_cert_common_name_check);
 assert(custom_ca ? cfg->cert_pem && !cfg->crt_bundle_attach : !cfg->cert_pem && cfg->crt_bundle_attach);
 mock.active=true;mock.user_data=cfg->user_data;return &mock;
}
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t c) { assert(c->active);c->active=false;cleanups++;return ESP_OK; }
esp_err_t esp_http_client_set_url(esp_http_client_handle_t c,const char *value) { (void)value;assert(c->active);return ESP_OK; }
esp_err_t esp_http_client_set_user_data(esp_http_client_handle_t c,void *value) { assert(c->active);c->user_data=value;return ESP_OK; }
esp_err_t esp_http_client_set_method(esp_http_client_handle_t c,int value) { (void)value;assert(c->active);return ESP_OK; }
esp_err_t esp_http_client_set_header(esp_http_client_handle_t c,const char *name,const char *value) {
 assert(c->active);if(!strcmp(name,"Authorization"))assert(!strncmp(value,"Bearer ",7) && !strcmp(value+7,token));return ESP_OK;
}
esp_err_t esp_http_client_set_post_field(esp_http_client_handle_t c,const char *value,int size) { (void)value;(void)size;assert(c->active);return ESP_OK; }
esp_err_t esp_http_client_perform(esp_http_client_handle_t c) { assert(c->active);performs++;return failure?ESP_FAIL:ESP_OK; }
int esp_http_client_get_status_code(esp_http_client_handle_t c) { assert(c->active);return failure?0:200; }
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t c,int *code,int *flags) {
 assert(c->active);reads++;*code=0x2700;*flags=verify_flags;return 0x801a;
}
static void reset(bool ca,time_t now,int error,int flags) {
 pocket_http_close();memset(&mock,0,sizeof(mock));failure=error;verify_flags=flags;custom_ca=ca;epoch=now;
 reads=performs=cleanups=logs=0;diagnostic[0]=log_tag[0]=0;
}
#define CHECK(condition,message) do { if(!(condition)){puts("FAIL: " message);return 1;} } while(0)
int main(void) {
 char out[64]="old response";pocket_http_result result;
 reset(true,1735689600,1,8);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA","SYNTHETIC_PRIVATE_BODY",out,sizeof(out),&result)==ESP_FAIL,"original transport error changed");
 CHECK(reads==1,"TLS verification flags were not retrieved before cleanup");
 CHECK(performs==1 && cleanups==1 && out[0]==0 && result.length==0,"failure cleanup or no-retry contract changed");
 CHECK(logs==1 && !strcmp(log_tag,"pocket_tls"),"missing single diagnostic record");
 CHECK(!strcmp(diagnostic,"target=1 http=-1 tls=0x801a mbedtls=0x2700 verify=0x8 ca=1 utc_ready=1 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000"),"diagnostic must contain only the approved numeric fields and booleans");
 reset(false,0,1,0x200);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/windows/status",token,"",NULL,out,sizeof(out),&result)==ESP_FAIL,"bundle failure changed");
 CHECK(!strcmp(diagnostic,"target=0 http=-1 tls=0x801a mbedtls=0x2700 verify=0x200 ca=0 utc_ready=0 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000"),"target, bundle, or UTC attribution lost");
 reset(true,1735689600,0,0);
 CHECK(pocket_http_json("https://synthetic-private-host.invalid","/targets/linux/status",token,"SYNTHETIC_PUBLIC_CA",NULL,out,sizeof(out),&result)==ESP_OK,"success changed");
 CHECK(logs==1 && reads==0 && cleanups==0 && result.status==200,"status success must preserve the client and TLS error state");
 CHECK(!strcmp(log_tag,"pocket_tls_heap") && !strcmp(diagnostic,"target=1 http=200 free_before=64000 largest_before=48000 free_after=64000 largest_after=48000"),"successful status heap evidence must be numeric-only");
 pocket_http_close();puts("PASS: numeric-only TLS diagnostics survive cleanup with target/CA/UTC attribution");return 0;
}
'''


class FirmwareHTTPTests(unittest.TestCase):
    def test_failure_diagnostics_are_safe_and_survive_cleanup(self):
        compiler = shutil.which('gcc') or shutil.which('clang')
        cached_clang = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'LLVM/bin/clang.exe'
        if not compiler and cached_clang.is_file():
            compiler = str(cached_clang)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1] / 'firmware/main'
        with tempfile.TemporaryDirectory() as temporary:
            temp = pathlib.Path(temporary)
            for name, content in HEADERS.items():
                (temp / name).write_text(content, encoding='utf-8')
            harness = temp / 'test_http.c'
            harness.write_text(HARNESS, encoding='utf-8')
            binary = temp / ('test-http.exe' if os.name == 'nt' else 'test-http')
            compile_result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic', '-I'+str(temp), '-I'+str(source), str(source/'pocket_http.c'), str(harness), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('PASS:', run.stdout)
