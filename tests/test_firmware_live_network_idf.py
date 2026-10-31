"""Actual experimental binding against fake public IDF/lwIP APIs; no sockets."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from tests.test_firmware_live_idf_adapter import STUB as BASE
EXTRA=r'''
#ifndef EXTRA
#define EXTRA
#define CONFIG_LWIP_SO_LINGER 1
#define ESP_TLS_ERR_SSL_WANT_READ -0x6900
#define ESP_TLS_ERR_SSL_WANT_WRITE -0x6880
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
typedef int err_t;
#define ERR_OK 0
#define ERR_INPROGRESS -5
#define LWIP_DNS_ADDRTYPE_IPV4 0
typedef struct {int x;} ip_addr_t;
typedef void(*dns_found_callback)(const char*,const ip_addr_t*,void*);
err_t dns_gethostbyname_addrtype(const char*,ip_addr_t*,dns_found_callback,void*,unsigned);
char *ipaddr_ntoa_r(const ip_addr_t*,char*,int);
err_t tcpip_try_callback(void(*)(void*),void*);
void vTaskDelay(unsigned);
void *esp_transport_get_context_data(esp_transport_handle_t);
esp_err_t esp_transport_set_context_data(esp_transport_handle_t,void*);
esp_transport_handle_t esp_transport_init(void);
typedef int(*connect_func)(esp_transport_handle_t,const char*,int,int);
typedef int(*read_func)(esp_transport_handle_t,char*,int,int);
typedef int(*write_func)(esp_transport_handle_t,const char*,int,int);
typedef int(*trans_func)(esp_transport_handle_t);
typedef int(*poll_func)(esp_transport_handle_t,int);
esp_err_t esp_transport_set_func(esp_transport_handle_t,connect_func,read_func,write_func,trans_func,poll_func,poll_func,trans_func);
void esp_transport_ssl_set_common_name(esp_transport_handle_t,const char*);
int esp_transport_connect_async(esp_transport_handle_t,const char*,int,int);
int esp_transport_poll_write(esp_transport_handle_t,int);
int esp_transport_write(esp_transport_handle_t,const char*,int,int);
struct linger {int l_onoff,l_linger;};
#define SOL_SOCKET 1
#define SO_LINGER 13
int fake_setsockopt(int,int,int,const void*,unsigned);
#endif
'''
HARNESS=r'''
#define fcntl fake_fcntl
#define setsockopt fake_setsockopt
#include "pocket_live_network_idf.c"
#include <assert.h>
#include <stdarg.h>
static uint64_t clock_ms;
static bool stopped_flag,stop_on_write;
static int tls_progress,auth_calls,write_limit=3,io_advance=1,read_value=1,wire_opcode=2;
static void(*queued)(void*);static void *queued_arg;
static dns_found_callback found_cb;static void *found_arg;
static net_context *current;
static char cert_name[193];static int cert_len=-1,socket_policies=0;
static connect_func sc;static read_func sr;static write_func sw;
int64_t esp_timer_get_time(void){return (int64_t)(clock_ms*1000);}
void vTaskDelay(unsigned n){clock_ms+=n;}
bool pocket_clock_ready(void){return true;}
static bool stop(void*p){(void)p;return stopped_flag;}
err_t tcpip_try_callback(void(*fn)(void*),void*a){queued=fn;queued_arg=a;return ERR_OK;}
err_t dns_gethostbyname_addrtype(const char*host,ip_addr_t*ip,dns_found_callback cb,void*a,unsigned type){(void)ip;(void)type;assert(!strcmp(host,"bridge.example"));found_cb=cb;found_arg=a;return ERR_INPROGRESS;}
char *ipaddr_ntoa_r(const ip_addr_t*p,char*out,int size){(void)p;assert(size>10);strcpy(out,"192.0.2.1");return out;}
esp_transport_handle_t esp_transport_ssl_init(void){return (void*)1;}
void esp_transport_ssl_set_cert_data(esp_transport_handle_t t,const char*p,int n){(void)t;assert(!strcmp(p,"CERT"));cert_len=n;}
void esp_transport_ssl_crt_bundle_attach(esp_transport_handle_t t,esp_err_t(*fn)(void*)){(void)t;(void)fn;}
esp_err_t esp_crt_bundle_attach(void*p){(void)p;return 0;}
void esp_transport_ssl_set_common_name(esp_transport_handle_t t,const char*n){(void)t;strcpy(cert_name,n);}
int esp_transport_connect_async(esp_transport_handle_t t,const char*host,int port,int timeout){(void)t;assert(!strcmp(host,"192.0.2.1")&&port==443&&timeout>0&&timeout<=2);clock_ms++;return tls_progress;}
int esp_transport_get_socket(esp_transport_handle_t t){(void)t;return 7;}
int fake_fcntl(int fd,int op,...){assert(fd==7);if(op==F_GETFL)return 0;assert(op==F_SETFL);va_list a;va_start(a,op);assert(va_arg(a,int)&O_NONBLOCK);va_end(a);return 0;}
int fake_setsockopt(int fd,int level,int op,const void*p,unsigned size){socket_policies++;assert(fd==7&&level==SOL_SOCKET&&op==SO_LINGER&&size==sizeof(struct linger));const struct linger*l=p;assert(l->l_onoff==1&&l->l_linger==0);return 0;}
esp_transport_handle_t esp_transport_init(void){return (void*)2;}
esp_err_t esp_transport_set_context_data(esp_transport_handle_t t,void*p){assert(t==(void*)2);current=p;return 0;}
void *esp_transport_get_context_data(esp_transport_handle_t t){assert(t==(void*)2);return current;}
esp_err_t esp_transport_set_func(esp_transport_handle_t t,connect_func a,read_func b,write_func c,trans_func d,poll_func e,poll_func f,trans_func g){(void)t;(void)d;(void)e;(void)f;(void)g;sc=a;sr=b;sw=c;return 0;}
esp_transport_handle_t esp_transport_ws_init(esp_transport_handle_t t){assert(t==(void*)2);return (void*)3;}
esp_err_t esp_transport_ws_set_config(esp_transport_handle_t t,const esp_transport_ws_config_t*c){assert(t==(void*)3&&tls_progress==1&&!strcmp(cert_name,"bridge.example"));assert(!strncmp(c->auth,"Bearer ",7));auth_calls++;return 0;}
int esp_transport_connect(esp_transport_handle_t t,const char*host,int port,int timeout){assert(t==(void*)3);assert(sc((void*)2,"wrong.example",port,timeout)<0);return sc((void*)2,host,port,timeout);}
int esp_transport_ws_get_upgrade_request_status(esp_transport_handle_t t){(void)t;return 101;}
int esp_transport_poll_read(esp_transport_handle_t t,int timeout){(void)t;(void)timeout;clock_ms++;return 1;}
int esp_transport_poll_write(esp_transport_handle_t t,int timeout){(void)t;(void)timeout;clock_ms++;return 1;}
int esp_transport_read(esp_transport_handle_t t,char*out,int n,int timeout){(void)t;(void)timeout;clock_ms+=(unsigned)io_advance;if(read_value>0){int k=read_value<n?read_value:n;memset(out,0,(size_t)k);return k;}return read_value;}
int esp_transport_write(esp_transport_handle_t t,const char*p,int n,int timeout){(void)t;(void)p;(void)timeout;clock_ms+=(unsigned)io_advance;if(stop_on_write)stopped_flag=true;return n<write_limit?n:write_limit;}
int esp_transport_ws_send_raw(esp_transport_handle_t t,ws_transport_opcodes_t op,const char*p,int n,int timeout){(void)t;(void)op;return sw((void*)2,p,n,timeout);}
int esp_transport_ws_get_read_opcode(esp_transport_handle_t t){(void)t;return wire_opcode;}
int esp_transport_ws_get_read_payload_len(esp_transport_handle_t t){(void)t;return 1;}
bool esp_transport_ws_get_fin_flag(esp_transport_handle_t t){(void)t;return true;}
esp_err_t esp_transport_destroy(esp_transport_handle_t t){(void)t;assert(current->phase==NET_CLOSING);return 0;}
int main(void){
 const net_config cfg={"bridge.example","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","CERT","/targets/linux/live",443,stop,0};
 net_context idle={0};assert(pocket_live_network_close(&idle)&&idle.cleanup_complete&&idle.phase==NET_CLOSED);
 net_context first={0},second={0};
 assert(pocket_live_network_open_begin(&first,&cfg));queued(queued_arg);
 stopped_flag=true;assert(pocket_live_network_open_step(&first)==-1);
 assert(!pocket_live_network_close(&first)&&!first.cleanup_complete&&first.phase==NET_CLOSING);
 /* Cancellation never frees callback userdata or admits a replacement. */
 stopped_flag=false;assert(!pocket_live_network_open_begin(&second,&cfg));
 ip_addr_t ip={0};found_cb(0,&ip,found_arg);
 assert(pocket_live_network_close(&first)&&first.cleanup_complete);
 assert(pocket_live_network_open_begin(&second,&cfg));queued(queued_arg);found_cb(0,&ip,found_arg);
 assert(pocket_live_network_open_step(&second)==0&&auth_calls==0&&cert_len==4&&socket_policies>0);
 tls_progress=1;assert(pocket_live_network_open_step(&second)==1&&auth_calls==1);
 assert(!strcmp(cert_name,"bridge.example"));
 /* Readable empty data frame cannot masquerade as idle. */
 net_context readctx=second;pocket_live_network_message meta;uint8_t out[32];
 read_value=0;assert(pocket_live_network_read(&readctx,out,sizeof out,&meta)<0);
 read_value=1;
 for(size_t size=1;size<4;size++)assert(pocket_live_network_read(&second,out,size,&meta)<0);
 uint8_t data[10]={0};assert(pocket_live_network_send(&second,true,data,sizeof data));
 /* Multiple short writes share one deadline, never renew it. */
 io_advance=40;write_limit=1;assert(!pocket_live_network_send(&second,true,data,sizeof data));assert(second.phase==NET_FAILED);
 assert(pocket_live_network_close(&second)&&second.cleanup_complete);
 net_context finishing={0};io_advance=1;write_limit=3;stopped_flag=false;
 assert(pocket_live_network_open_begin(&finishing,&cfg));queued(queued_arg);found_cb(0,&ip,found_arg);
 assert(pocket_live_network_open_step(&finishing)==1);
 stopped_flag=true;const uint8_t stop_control[]="{\"type\":\"stop\"}";
 assert(pocket_live_network_send(&finishing,false,stop_control,sizeof stop_control-1));
 assert(!pocket_live_network_send(&finishing,true,data,sizeof data));
 read_value=1;assert(pocket_live_network_read(&finishing,out,sizeof out,&meta)>0);
 clock_ms+=3000;assert(pocket_live_network_read(&finishing,out,sizeof out,&meta)<0);
 assert(pocket_live_network_close(&finishing));stopped_flag=false;
 net_context interrupted={0};stopped_flag=false;stop_on_write=false;
 assert(pocket_live_network_open_begin(&interrupted,&cfg));queued(queued_arg);found_cb(0,&ip,found_arg);
 assert(pocket_live_network_open_step(&interrupted)==1);stop_on_write=true;
 assert(!pocket_live_network_send(&interrupted,true,data,sizeof data)&&interrupted.phase==NET_FAILED);
 assert(!pocket_live_network_send(&interrupted,false,stop_control,sizeof stop_control-1));
 assert(pocket_live_network_close(&interrupted));stop_on_write=false;stopped_flag=false;
 /* Header/payload WANT_READ never becomes a successful idle return. */
 net_context third={0};current=&third;third.phase=NET_OPEN;third.tls=(void*)1;third.shim=(void*)2;third.stopped=stop;
 pocket_live_network_deadline_init(&third.deadline);assert(pocket_live_network_deadline_begin(&third.deadline,clock_ms,10));
 read_value=0;io_advance=3;assert(sr((void*)2,(char*)data,sizeof data,2)<0&&third.deadline.failed);
 return 0;
}
'''
class FirmwareLiveNetworkIDFTests(unittest.TestCase):
 def test_private_binding_dns_tls_auth_and_nonrenewable_io(self):
  root=Path(__file__).resolve().parents[1];cc=shutil.which('gcc')
  if not cc:self.skipTest('GCC unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'stub.h').write_text(BASE+EXTRA)
   for name in ['esp_transport.h','esp_transport_ssl.h','esp_transport_ws.h','esp_crt_bundle.h','esp_timer.h','esp_tls_errors.h','freertos/FreeRTOS.h','freertos/task.h','lwip/dns.h','lwip/tcpip.h','lwip/sockets.h']:
    f=p/name;f.parent.mkdir(exist_ok=True);f.write_text('#include "stub.h"\n')
   (p/'test.c').write_text(HARNESS)
   cmd=[cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2','-I'+str(p),'-I'+str(root/'firmware/main'),'-I'+str(root/'firmware/core'),str(p/'test.c'),str(root/'firmware/main/pocket_live_network.c'),'-o',str(p/'test')]
   r=subprocess.run(cmd,capture_output=True,text=True,timeout=20);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
