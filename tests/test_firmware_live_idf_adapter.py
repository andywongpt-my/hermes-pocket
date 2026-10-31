"""Actual inactive IDF adapter against fake public transport/audio boundaries."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

STUB=r'''
#ifndef STUB
#define STUB
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_TIMEOUT 0x107
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_NOT_SUPPORTED 0x106
typedef void *esp_transport_handle_t;
typedef int ws_transport_opcodes_t;
enum {WS_TRANSPORT_OPCODES_BINARY=2,WS_TRANSPORT_OPCODES_TEXT=1,WS_TRANSPORT_OPCODES_FIN=128,WS_TRANSPORT_OPCODES_CLOSE=8,WS_TRANSPORT_OPCODES_PING=9,WS_TRANSPORT_OPCODES_PONG=10};
typedef struct {const char *ws_path,*auth;bool propagate_control_frames;} esp_transport_ws_config_t;
typedef struct {uint32_t epoch,accepted,dma_completed,dma_samples;bool active,failed;} bsp_audio_render_status_t;
typedef struct {uint32_t epoch,dma_start,source_start;uint16_t source_samples;} bsp_audio_render_span_t;
esp_transport_handle_t esp_transport_ssl_init(void);
void esp_transport_ssl_set_cert_data(esp_transport_handle_t,const char*,int);
void esp_transport_ssl_crt_bundle_attach(esp_transport_handle_t,esp_err_t(*)(void*));
esp_err_t esp_crt_bundle_attach(void*);
esp_transport_handle_t esp_transport_ws_init(esp_transport_handle_t);
esp_err_t esp_transport_ws_set_config(esp_transport_handle_t,const esp_transport_ws_config_t*);
int esp_transport_connect(esp_transport_handle_t,const char*,int,int);
int esp_transport_ws_get_upgrade_request_status(esp_transport_handle_t);
int esp_transport_ws_send_raw(esp_transport_handle_t,ws_transport_opcodes_t,const char*,int,int);
int esp_transport_read(esp_transport_handle_t,char*,int,int);
int esp_transport_poll_read(esp_transport_handle_t,int);
int esp_transport_ws_get_read_opcode(esp_transport_handle_t);
int esp_transport_ws_get_read_payload_len(esp_transport_handle_t);
bool esp_transport_ws_get_fin_flag(esp_transport_handle_t);
int esp_transport_close(esp_transport_handle_t);
int esp_transport_get_socket(esp_transport_handle_t);
esp_err_t esp_transport_destroy(esp_transport_handle_t);
int64_t esp_timer_get_time(void);
esp_err_t bsp_audio_set_format(uint32_t,uint8_t,uint8_t);
esp_err_t bsp_audio_render_start(uint32_t);
esp_err_t bsp_audio_read_bounded(void*,size_t,size_t*,uint32_t);
esp_err_t bsp_audio_render_submit(uint32_t,uint32_t,const int16_t*,size_t);
esp_err_t bsp_audio_render_status(bsp_audio_render_status_t*);
esp_err_t bsp_audio_render_poll(bsp_audio_render_span_t*);
esp_err_t bsp_audio_render_stop_output(void);
esp_err_t bsp_audio_sleep(void);
#endif
'''
HARNESS=r'''
#define fcntl fake_fcntl
#include "pocket_live_idf.c"
#include <stdarg.h>
#include <assert.h>
static int poll_value,read_value,read_calls,opcode=2,payload=20;
static int fd_value=3,flags_value=0,set_value=0,set_flags=0;
int esp_transport_get_socket(esp_transport_handle_t t){(void)t;return fd_value;}
static int cert_length;
esp_transport_handle_t esp_transport_ssl_init(void){return (void*)1;}
void esp_transport_ssl_set_cert_data(esp_transport_handle_t t,const char*p,int n){(void)t;assert(p);cert_length=n;}
void esp_transport_ssl_crt_bundle_attach(esp_transport_handle_t t,esp_err_t(*f)(void*)){(void)t;(void)f;}
esp_err_t esp_crt_bundle_attach(void*p){(void)p;return 0;}
esp_transport_handle_t esp_transport_ws_init(esp_transport_handle_t t){assert(t);return (void*)2;}
esp_err_t esp_transport_ws_set_config(esp_transport_handle_t t,const esp_transport_ws_config_t*c){assert(t&&c&&c->auth);return 0;}
int esp_transport_connect(esp_transport_handle_t t,const char*h,int p,int ms){assert(t&&h&&p==443&&ms==1000);return 0;}
int esp_transport_ws_get_upgrade_request_status(esp_transport_handle_t t){assert(t);return 101;}
bool pocket_clock_ready(void){return true;}
static bool stopped_(void*p){(void)p;return false;}

int fake_fcntl(int fd,int op,...){assert(fd==3);if(op==F_GETFL)return flags_value;assert(op==F_SETFL);va_list a;va_start(a,op);set_flags=va_arg(a,int);va_end(a);return set_value;}
int esp_transport_poll_read(esp_transport_handle_t t,int timeout){(void)t;assert(timeout>0&&timeout<=10);return poll_value;}
int esp_transport_read(esp_transport_handle_t t,char*p,int n,int timeout){(void)t;(void)timeout;read_calls++;assert(n>=read_value);if(read_value>0)memset(p,1,(size_t)read_value);return read_value;}
int esp_transport_ws_get_read_opcode(esp_transport_handle_t t){(void)t;return opcode;}
int esp_transport_ws_get_read_payload_len(esp_transport_handle_t t){(void)t;return payload;}
bool esp_transport_ws_get_fin_flag(esp_transport_handle_t t){(void)t;return true;}
bool pocket_live_control(const char*p,size_t n,pocket_live_worker_event*e){(void)p;(void)n;(void)e;return false;}
int main(void){idf_context c={.ws=(void*)1};pocket_live_worker_event e={0};uint8_t out[1024];
 assert(make_nonblocking(c.ws));assert(set_flags&O_NONBLOCK);
 fd_value=-1;assert(!make_nonblocking(c.ws));fd_value=3;
 flags_value=-1;assert(!make_nonblocking(c.ws));flags_value=0;
 set_value=-1;assert(!make_nonblocking(c.ws));set_value=0;
 char ca[2049];memset(ca,'A',2048);ca[2048]=0;
 pocket_live_idf_config config={.host="example.invalid",.token="synthetic_token_000000000000000000",.ca_pem=ca,.port=443,.epoch=1,.stopped=stopped_};
 c.config=&config;assert(connect_(&c));assert(cert_length==2048);
 /* Idle poll must not call read: metadata from prior frames is stale. */
 poll_value=0;read_value=0;assert(receive_(&c,&e,out,sizeof out));assert(e.kind==PLW_NONE&&read_calls==0);
 /* Readable frame header then first-payload timeout must fail, not reparse. */
 poll_value=1;assert(!receive_(&c,&e,out,sizeof out));
 /* An internally handled empty ping is not an audio payload timeout. */
 opcode=9;payload=0;assert(receive_(&c,&e,out,sizeof out));
 opcode=2;payload=20;read_value=10;
 assert(receive_(&c,&e,out,sizeof out)&&c.used==10&&e.kind==PLW_NONE);
 poll_value=0;read_value=0;assert(receive_(&c,&e,out,sizeof out)&&c.used==10);
 poll_value=1;read_value=10;assert(receive_(&c,&e,out,sizeof out)&&e.kind==PLW_AUDIO&&e.bytes==20&&c.used==0);
 return 0;}
'''
class FirmwareLiveIDFAdapterTests(unittest.TestCase):
    def test_idle_poll_vs_partial_payload_timeout(self):
        root=Path(__file__).resolve().parents[1];cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'stub.h').write_text(STUB)
            for name in ['esp_err.h','bsp_audio.h','esp_transport.h','esp_transport_ssl.h','esp_transport_ws.h','esp_crt_bundle.h','esp_timer.h']:
                (p/name).write_text('#include "stub.h"\n')
            (p/'test.c').write_text(HARNESS)
            cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-O2','-ffunction-sections','-fdata-sections',
                '-I'+str(p),'-I'+str(root/'firmware/main'),'-I'+str(root/'firmware/core'),str(p/'test.c'),'-Wl,--gc-sections','-o',str(p/'test')]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
