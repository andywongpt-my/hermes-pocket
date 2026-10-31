/* MIT. Private, inactive network prototype for pinned IDF 5.5.3.
 * No default activation or public device entry. Internal symbols remain gated until
 * integrated under the physical-release gate and independently reviewed.
 * tcp_transport must be compiled with LOG_LOCAL_LEVEL=ESP_LOG_NONE: upstream
 * websocket errors can print the authenticated Upgrade request.
 */
#include "pocket_live_network_idf.h"
#include "pocket_clock.h"
#include "esp_transport.h"
#include "esp_transport_ssl.h"
#include "esp_transport_ws.h"
#include "esp_crt_bundle.h"
#include "esp_timer.h"
#include "esp_tls_errors.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/dns.h"
#include "lwip/tcpip.h"
#include "lwip/sockets.h"
#include <fcntl.h>
#include <stdio.h>
#include <string.h>

typedef pocket_live_network_config net_config;
typedef pocket_live_network_context net_context;

/* Callback userdata is static and never points to a caller or network task.
 * A canceled query keeps this one admission slot until its actual callback
 * settles. No queued callback or resolver timer can touch a reused stack. */
static portMUX_TYPE dns_lock=portMUX_INITIALIZER_UNLOCKED;
static pocket_live_network_dns dns_slot;
static uint32_t dns_callback_token;

static uint64_t net_now(void){return (uint64_t)(esp_timer_get_time()/1000);}
static bool net_stop(const net_context*c){return !c->finalizing&&c->stopped&&c->stopped(c->stop_context);}
static uint32_t net_slice(net_context*c,unsigned wanted){
 uint64_t now=net_now();
 if(c->finalizing&&now>=c->finalize_end){c->deadline.failed=true;return 0;}
 uint32_t slice=pocket_live_network_deadline_slice(&c->deadline,now,wanted,net_stop(c));
 if(c->finalizing&&slice>c->finalize_end-now)slice=(uint32_t)(c->finalize_end-now);
 return slice;
}
static void dns_found(const char *name,const ip_addr_t *ip,void *argument){
 (void)name;
 char address[48];const char *text=NULL;
 if(ip)text=ipaddr_ntoa_r(ip,address,sizeof address);
 portENTER_CRITICAL(&dns_lock);
 uint32_t token=*(const uint32_t*)argument;
 (void)pocket_live_network_dns_complete(&dns_slot,token,text);
 portEXIT_CRITICAL(&dns_lock);
}
static void dns_begin_callback(void *argument){
 portENTER_CRITICAL(&dns_lock);
 uint32_t token=*(const uint32_t*)argument;
 bool run=pocket_live_network_dns_start(&dns_slot,token);
 portEXIT_CRITICAL(&dns_lock);
 if(!run){dns_found(NULL,NULL,argument);return;}
 ip_addr_t ip;
 err_t e=dns_gethostbyname_addrtype(dns_slot.host,&ip,dns_found,argument,LWIP_DNS_ADDRTYPE_IPV4);
 if(e==ERR_OK)dns_found(NULL,&ip,argument);
 else if(e!=ERR_INPROGRESS)dns_found(NULL,NULL,argument);
}
static bool clean_token(const char*s){
 if(!s)return false;
 size_t n=0;
 for(;n<=128&&s[n];n++)if((unsigned char)s[n]<33||(unsigned char)s[n]>126)return false;
 return n>=32&&n<=128;
}
static int shim_connect(esp_transport_handle_t t,const char*host,int port,int timeout){
 (void)timeout;net_context*c=esp_transport_get_context_data(t);
 return c&&c->phase==NET_UPGRADE&&host&&!strcmp(host,c->host)&&port==(int)c->port&&net_slice(c,1)?0:-1;
}
static int shim_poll(esp_transport_handle_t t,int requested,bool write){
 net_context*c=esp_transport_get_context_data(t);
 if(!c||requested<0)return -1;
 uint32_t slice=net_slice(c,requested>0?(unsigned)requested:1u);
 if(!slice)return -1;
 int r=write?esp_transport_poll_write(c->tls,(int)slice):esp_transport_poll_read(c->tls,(int)slice);
 if(!net_slice(c,1))return -1;
 return r;
}
static int shim_poll_read(esp_transport_handle_t t,int timeout){return shim_poll(t,timeout,false);}
static int shim_poll_write(esp_transport_handle_t t,int timeout){return shim_poll(t,timeout,true);}
static int shim_read(esp_transport_handle_t t,char*out,int bytes,int timeout){
 net_context*c=esp_transport_get_context_data(t);
 if(!c||!out||bytes<=0||timeout<0)return -1;
 for(;;){
  uint32_t slice=net_slice(c,timeout>0?(unsigned)timeout:1u);if(!slice)return -1;
  int r=esp_transport_read(c->tls,out,bytes,(int)slice);
  if(!net_slice(c,1))return -1;
  if(r>0)return r;
  if(r<0&&r!=ESP_TLS_ERR_SSL_WANT_READ&&r!=ESP_TLS_ERR_SSL_WANT_WRITE)return -1;
  /* Preserve a partially consumed TLS record/header until this ONE operation's
   * absolute deadline. Never return a false idle timeout to the WS parser. */
  vTaskDelay(1);
 }
}
static int shim_write(esp_transport_handle_t t,const char*data,int bytes,int timeout){
 net_context*c=esp_transport_get_context_data(t);
 if(!c||!data||bytes<0||timeout<0)return -1;
 int at=0;
 while(at<bytes){
  uint32_t slice=net_slice(c,timeout>0?(unsigned)timeout:1u);if(!slice)return -1;
  int r=esp_transport_write(c->tls,data+at,bytes-at,(int)slice);
  if(!net_slice(c,1))return -1;
  if(r>bytes-at)return -1;
  if(r>0){at+=r;continue;}
  if(r<0&&r!=ESP_TLS_ERR_SSL_WANT_READ&&r!=ESP_TLS_ERR_SSL_WANT_WRITE)return -1;
  /* This completes a single in-progress byte write, not a logical frame retry.
   * Same buffer/length are retained on WANT_*. Deadline failure poisons session. */
  vTaskDelay(1);
 }
 return at;
}
static int shim_close(esp_transport_handle_t t){
 net_context*c=esp_transport_get_context_data(t);
 /* Final cleanup owns TLS exactly once; ws_close must not free its parent. */
 return c?0:-1;
}
static int shim_destroy(esp_transport_handle_t t){(void)t;return 0;}
static bool socket_policy(esp_transport_handle_t tls){
 int fd=esp_transport_get_socket(tls);if(fd<0)return false;
 int flags=fcntl(fd,F_GETFL,0);
 if(flags<0||fcntl(fd,F_SETFL,flags|O_NONBLOCK)<0)return false;
#if CONFIG_LWIP_SO_LINGER
 struct linger linger={.l_onoff=1,.l_linger=0};
 return setsockopt(fd,SOL_SOCKET,SO_LINGER,&linger,sizeof linger)==0;
#else
 /* No unreviewed 20-second default TCP close wait in a selectable adapter. */
 return false;
#endif
}
bool pocket_live_network_open_begin(net_context*c,const net_config*cfg){
 if(!c||!cfg||c->phase!=NET_IDLE||!cfg->host||!clean_token(cfg->token)||!cfg->ca_pem||
    strlen(cfg->ca_pem)>2048||!cfg->path||strlen(cfg->path)>=sizeof c->path||
    !cfg->port||cfg->port>65535||!cfg->stopped||!pocket_clock_ready())return false;
 /* Paths are exact caller-authorized routes, never redirects or user URLs. */
 if(strcmp(cfg->path,"/targets/linux/live")&&strcmp(cfg->path,"/targets/windows/live"))return false;
 c->stopped=cfg->stopped;c->stop_context=cfg->stop_context;
 if(net_stop(c))return false;
 pocket_live_network_deadline_init(&c->deadline);
 if(!pocket_live_network_deadline_begin(&c->deadline,net_now(),10000))return false;
 portENTER_CRITICAL(&dns_lock);
 bool accepted=pocket_live_network_dns_begin(&dns_slot,cfg->host,&c->dns_token);
 if(accepted){dns_callback_token=c->dns_token;memcpy(c->host,dns_slot.host,sizeof c->host);}
 portEXIT_CRITICAL(&dns_lock);
 if(!accepted)return false;
 c->port=cfg->port;memcpy(c->ca,cfg->ca_pem,strlen(cfg->ca_pem)+1);
 memcpy(c->path,cfg->path,strlen(cfg->path)+1);
 (void)snprintf(c->auth,sizeof c->auth,"Bearer %s",cfg->token);
 c->phase=NET_DNS;
 if(tcpip_try_callback(dns_begin_callback,&dns_callback_token)!=ERR_OK){
  dns_found(NULL,NULL,&dns_callback_token);
  portENTER_CRITICAL(&dns_lock);pocket_live_network_dns_cancel(&dns_slot,c->dns_token);portEXIT_CRITICAL(&dns_lock);
  c->phase=NET_FAILED;return false;
 }
 return true;
}
/* 0=in progress, 1=ready, -1=terminal. No automatic restart is possible. */
int pocket_live_network_open_step(net_context*c){
 if(!c||c->phase<NET_DNS||c->phase>NET_UPGRADE)return -1;
 if(!net_slice(c,1))goto failed;
 if(c->phase==NET_DNS){
  portENTER_CRITICAL(&dns_lock);
  bool done=dns_slot.generation==c->dns_token&&dns_slot.completed;
  bool success=done&&pocket_live_network_dns_take(&dns_slot,c->dns_token,c->address,sizeof c->address);
  portEXIT_CRITICAL(&dns_lock);
  if(!done)return 0;
  if(!success)goto failed;
  c->tls=esp_transport_ssl_init();if(!c->tls)goto failed;
  if(c->ca[0])esp_transport_ssl_set_cert_data(c->tls,c->ca,(int)strlen(c->ca));
  else esp_transport_ssl_crt_bundle_attach(c->tls,esp_crt_bundle_attach);
  /* IDF esp_tls_mbedtls.c uses common_name in mbedtls_ssl_set_hostname:
   * this preserves BOTH original-host SNI and certificate-name verification,
   * while TCP connects to the already-resolved numeric address. */
  esp_transport_ssl_set_common_name(c->tls,c->host);
  c->phase=NET_TLS;
 }
 if(c->phase==NET_TLS){
  uint32_t slice=net_slice(c,2);if(!slice)goto failed;
  int progress=esp_transport_connect_async(c->tls,c->address,(int)c->port,(int)slice);
  /* Apply nonblocking/zero-linger as soon as the public socket exists,
   * including TCP/TLS handshake progress. A failure entirely inside the first
   * SDK call can still precede this policy and remains a cleanup limitation. */
  if(progress>=0&&esp_transport_get_socket(c->tls)>=0&&!socket_policy(c->tls))goto failed;
  if(!net_slice(c,1)||progress<0)goto failed;
  if(!progress)return 0;
  if(esp_transport_get_socket(c->tls)<0)goto failed;
  c->shim=esp_transport_init();if(!c->shim)goto failed;
  if(esp_transport_set_context_data(c->shim,c)!=ESP_OK||
     esp_transport_set_func(c->shim,shim_connect,shim_read,shim_write,shim_close,shim_poll_read,shim_poll_write,shim_destroy)!=ESP_OK)goto failed;
  c->ws=esp_transport_ws_init(c->shim);if(!c->ws)goto failed;
  esp_transport_ws_config_t config={.ws_path=c->path,.auth=c->auth,.propagate_control_frames=false};
  /* Authorization is first attached only AFTER verified TLS completes. */
  if(esp_transport_ws_set_config(c->ws,&config)!=ESP_OK)goto failed;
  c->phase=NET_UPGRADE;
 }
 if(c->phase==NET_UPGRADE){
  uint32_t slice=net_slice(c,50);if(!slice)goto failed;
  if(esp_transport_connect(c->ws,c->host,(int)c->port,(int)slice)!=0||
     esp_transport_ws_get_upgrade_request_status(c->ws)!=101||
     !pocket_live_network_deadline_finish(&c->deadline,net_now(),net_stop(c)))goto failed;
  c->phase=NET_OPEN;return 1;
 }
 return 0;
failed:
 portENTER_CRITICAL(&dns_lock);pocket_live_network_dns_cancel(&dns_slot,c->dns_token);portEXIT_CRITICAL(&dns_lock);
 c->phase=NET_FAILED;return -1;
}
bool pocket_live_network_send(net_context*c,bool binary,const uint8_t*data,size_t bytes){
 if(!c||c->phase!=NET_OPEN||!data||!bytes||bytes>sizeof c->mutable_wire||c->finalizing)return false;
 static const uint8_t stop_control[]="{\"type\":\"stop\"}";
 bool stopping=!binary&&bytes==sizeof stop_control-1&&!memcmp(data,stop_control,bytes);
 if(net_stop(c)&&!stopping){c->phase=NET_FAILED;return false;}
 uint64_t now=net_now();
 if(!pocket_live_network_deadline_begin(&c->deadline,now,100))return false;
 if(stopping){
  if(now>UINT64_MAX-3000){c->phase=NET_FAILED;return false;}
  c->finalizing=true;c->finalize_end=now+3000;
 }
 memcpy(c->mutable_wire,data,bytes);
 ws_transport_opcodes_t op=(ws_transport_opcodes_t)((binary?WS_TRANSPORT_OPCODES_BINARY:WS_TRANSPORT_OPCODES_TEXT)|WS_TRANSPORT_OPCODES_FIN);
 int n=esp_transport_ws_send_raw(c->ws,op,(char*)c->mutable_wire,(int)bytes,50);
 if(n!=(int)bytes||!net_slice(c,1)||!pocket_live_network_deadline_finish(&c->deadline,net_now(),net_stop(c))){c->phase=NET_FAILED;return false;}
 return true;
}
/* Raw bounded read, caller validates complete v2 control/audio frame shape.
 * Returns 0 idle/handled control, positive bytes, -1 terminal. The owning caller
 * must retain and bound partial application messages; no SDK state is reset. */
int pocket_live_network_read(net_context*c,uint8_t*out,size_t capacity,pocket_live_network_message *meta){
 if(!c||c->phase!=NET_OPEN||!out||!meta||capacity<16||capacity>1024||
    !pocket_live_network_deadline_begin(&c->deadline,net_now(),50))return -1;
 int available=esp_transport_poll_read(c->ws,1);
 int n=available>0?esp_transport_read(c->ws,(char*)out,(int)capacity,10):available;
 if(available>0){
  int opcode=(int)esp_transport_ws_get_read_opcode(c->ws);
  int length=esp_transport_ws_get_read_payload_len(c->ws);
  if(opcode==WS_TRANSPORT_OPCODES_CLOSE)n=-1;
  else if(!n&&!(length>=0&&length<=125&&esp_transport_ws_get_fin_flag(c->ws)&&
          (opcode==WS_TRANSPORT_OPCODES_PING||opcode==WS_TRANSPORT_OPCODES_PONG)))n=-1;
 }
 if(n>0){
  int length=esp_transport_ws_get_read_payload_len(c->ws);
  if(length<0)n=-1;
  else {meta->opcode=(int)esp_transport_ws_get_read_opcode(c->ws);meta->fin=esp_transport_ws_get_fin_flag(c->ws);meta->payload_bytes=(size_t)length;}
 }
 if(n<0||!net_slice(c,1)||!pocket_live_network_deadline_finish(&c->deadline,net_now(),net_stop(c))){c->phase=NET_FAILED;return -1;}
 return n;
}
bool pocket_live_network_close(net_context*c){
 if(!c)return false;
 if(c->phase==NET_IDLE){c->cleanup_complete=true;c->phase=NET_CLOSED;return true;}
 if(c->phase==NET_CLOSED)return c->cleanup_complete;
 c->phase=NET_CLOSING;c->cleanup_complete=false;
 portENTER_CRITICAL(&dns_lock);pocket_live_network_dns_cancel(&dns_slot,c->dns_token);portEXIT_CRITICAL(&dns_lock);
 /* Single owner only. No task deletion, cross-task TLS free, or timeout-based
  * premature resource reuse. A controller must retain this context until this
  * function returns; if the scheduler/TCPIP task stalls, status stays CLOSING.
  * SO_LINGER(0) avoids waiting for remote ACK, not a hard realtime guarantee. */
 bool ok=true;
 if(c->ws){ok=esp_transport_destroy(c->ws)==ESP_OK;c->ws=NULL;}
 if(c->shim){if(esp_transport_destroy(c->shim)!=ESP_OK)ok=false;c->shim=NULL;}
 if(c->tls){if(esp_transport_destroy(c->tls)!=ESP_OK)ok=false;c->tls=NULL;}
 memset(c->auth,0,sizeof c->auth);
 portENTER_CRITICAL(&dns_lock);
 bool dns_pending=dns_slot.busy&&dns_slot.generation==c->dns_token;
 portEXIT_CRITICAL(&dns_lock);
 c->cleanup_complete=ok&&!dns_pending;
 c->phase=c->cleanup_complete?NET_CLOSED:NET_CLOSING;
 return c->cleanup_complete;
}
