/* MIT. Compile-checked IDF 5.5.3 adapter contract; NOT activated by the gate.
 * The single owner serializes TLS/WebSocket, finite-requested RX waits and DMA
 * queueing. DNS/handshake/cleanup deadlines are NOT established by this prototype.
 * Missing capture/reference timeline transport prevents exposing these ops.
 * TCP transport logging is compiled out by the opt-in CMake rule because IDF
 * prints the complete authenticated Upgrade request on some error paths. */
#include "pocket_live_idf.h"
#include "pocket_live_control.h"
#include "pocket_clock.h"
#include "bsp_audio.h"
#include "esp_transport.h"
#include "esp_transport_ssl.h"
#include "esp_transport_ws.h"
#include "esp_crt_bundle.h"
#include "esp_timer.h"
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include <fcntl.h>

typedef struct {
    const pocket_live_idf_config *config;
    esp_transport_handle_t tls,ws;
    uint8_t message[1024],send_copy[1024];
    size_t used,expected;
    int opcode;
    bool audio;
    /* Retained without interpretation: not sufficient to align microphone. */
    bsp_audio_render_span_t spans[32];
    unsigned span_count;
} idf_context;
static bool clean(const char *s,size_t low,size_t high,bool hostname) {
    if(!s)return false;
    size_t i=0;
    for(;i<=high&&s[i];i++) {
        unsigned char c=(unsigned char)s[i];
        if(c<33||c>126)return false;
        if(hostname&&!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||
            (c>='0'&&c<='9')||c=='.'||c=='-'))return false;
    }
    return i>=low&&i<=high;
}
static bool make_nonblocking(esp_transport_handle_t transport) {
    int fd=esp_transport_get_socket(transport);
    if(fd<0)return false;
    int flags=fcntl(fd,F_GETFL,0);
    return flags>=0&&fcntl(fd,F_SETFL,flags|O_NONBLOCK)==0;
}
static bool connect_(void *v) {
    idf_context *c=v;const pocket_live_idf_config *p=c->config;
    if(!p||!clean(p->host,1,192,true)||!clean(p->token,32,128,false)||
        !p->ca_pem||strlen(p->ca_pem)>2048||!p->port||p->port>65535||
        p->target>1||!p->epoch||!p->stopped||!pocket_clock_ready())return false;
    c->tls=esp_transport_ssl_init();if(!c->tls)return false;
    if(p->ca_pem[0])esp_transport_ssl_set_cert_data(c->tls,p->ca_pem,(int)strlen(p->ca_pem));
    else esp_transport_ssl_crt_bundle_attach(c->tls,esp_crt_bundle_attach);
    /* No hostname bypass, custom common name, redirect, retry or credential lookup. */
    c->ws=esp_transport_ws_init(c->tls);if(!c->ws)return false;
    char auth[136];int n=snprintf(auth,sizeof auth,"Bearer %s",p->token);
    if(n<0||(size_t)n>=sizeof auth)return false;
    esp_transport_ws_config_t cfg={.ws_path=p->target?"/targets/linux/live":"/targets/windows/live",
        .auth=auth,.propagate_control_frames=false};
    esp_err_t err=esp_transport_ws_set_config(c->ws,&cfg);
    memset(auth,0,sizeof auth);
    /* Requested timeout is NOT an end-to-end startup bound: DNS and repeated
     * handshake reads remain unbounded by the pump deadline. Activation blocked. */
    return err==ESP_OK&&esp_transport_connect(c->ws,p->host,(int)p->port,1000)==0&&
        esp_transport_ws_get_upgrade_request_status(c->ws)==101&&make_nonblocking(c->tls);
}
static bool send_(void *v,bool binary,const uint8_t *data,size_t bytes) {
    idf_context *c=v;
    if(!c->ws||!data||!bytes||bytes>sizeof c->send_copy)return false;
    /* IDF masks the supplied buffer in place, including a failed-write path. */
    memcpy(c->send_copy,data,bytes);
    ws_transport_opcodes_t opcode=(ws_transport_opcodes_t)((binary?WS_TRANSPORT_OPCODES_BINARY:WS_TRANSPORT_OPCODES_TEXT)|WS_TRANSPORT_OPCODES_FIN);
    return esp_transport_ws_send_raw(c->ws,opcode,(char *)c->send_copy,(int)bytes,20)==(int)bytes;
}
static bool receive_(void *v,pocket_live_worker_event *event,uint8_t *out,size_t capacity) {
    idf_context *c=v;event->kind=PLW_NONE;
    if(!c->ws||c->used>=sizeof c->message)return false;
    int ready=esp_transport_poll_read(c->ws,2);
    if(ready<0)return false;
    if(!ready)return true;
    int n=esp_transport_read(c->ws,(char *)c->message+c->used,(int)(sizeof c->message-c->used),2);
    if(n<0)return false;
    int opcode=(int)esp_transport_ws_get_read_opcode(c->ws);
    if(opcode==WS_TRANSPORT_OPCODES_CLOSE){event->kind=PLW_LOST;return true;}
    if(!n) {
        /* A readable frame whose first payload read timed out is already reset
         * by IDF. Never treat it as idle and reparse payload as a new header.
         * Internally handled ping/pong alone may legitimately return zero. */
        int length=esp_transport_ws_get_read_payload_len(c->ws);
        return c->used==0&&length>=0&&length<=125&&esp_transport_ws_get_fin_flag(c->ws)&&
            (opcode==WS_TRANSPORT_OPCODES_PING||opcode==WS_TRANSPORT_OPCODES_PONG);
    }
    if(c->used==0) {
        int length=esp_transport_ws_get_read_payload_len(c->ws);
        if(length<=0||(size_t)length>sizeof c->message||!esp_transport_ws_get_fin_flag(c->ws)||
            (opcode!=WS_TRANSPORT_OPCODES_BINARY&&opcode!=WS_TRANSPORT_OPCODES_TEXT))return false;
        c->expected=(size_t)length;c->opcode=opcode;
    }
    if((size_t)n>c->expected-c->used)return false;
    c->used+=(size_t)n;
    if(c->used<c->expected)return true;
    bool ok;
    if(c->opcode==WS_TRANSPORT_OPCODES_BINARY) {
        ok=c->used<=capacity;
        if(ok){memcpy(out,c->message,c->used);event->kind=PLW_AUDIO;event->bytes=c->used;}
    } else ok=pocket_live_control((char *)c->message,c->used,event);
    c->used=c->expected=0;return ok;
}
static bool audio_start(void *v) {
    idf_context *c=v;c->audio=true;
    return bsp_audio_set_format(24000,16,1)==ESP_OK&&bsp_audio_render_start(c->config->epoch)==ESP_OK;
}
static bool capture(void *v,uint8_t *pcm,size_t capacity,size_t *received) {
    (void)v;
    esp_err_t e=bsp_audio_read_bounded(pcm,capacity,received,2);
    return e==ESP_OK||e==ESP_ERR_TIMEOUT;
}
static bool submit(void *v,uint32_t offset,const uint8_t *pcm,size_t bytes) {
    idf_context *c=v;if(!pcm||!bytes||bytes>960||bytes%2)return false;
    /* Avoid alignment assumptions on the wire receive buffer. Native ESP is LE. */
    int16_t copy[480];memcpy(copy,pcm,bytes);
    return bsp_audio_render_submit(c->config->epoch,offset,copy,bytes/2)==ESP_OK;
}
static bool rendered(void *v,uint32_t *played) {
    idf_context *c=v;bsp_audio_render_status_t s;
    if(bsp_audio_render_status(&s)!=ESP_OK||s.failed||!s.active||s.epoch!=c->config->epoch)return false;
    for(;;) {
        bsp_audio_render_span_t span;esp_err_t e=bsp_audio_render_poll(&span);
        if(e==ESP_ERR_NOT_FOUND)break;
        if(e!=ESP_OK||c->span_count==32)return false;
        c->spans[c->span_count++]=span;
    }
    /* No consumer exists yet; bounded retained spans intentionally fail closed
     * rather than silently discard evidence and invent reference alignment. */
    (void)played;
    /* GDMA EOF is not I2S pin/codec playout. Never map dma_completed to the
     * protocol played_samples field; serializer/FIFO proof is still missing. */
    return false;
}
static bool audio_stop(void *v) {
    idf_context *c=v;
    if(!c->audio)return true;
    esp_err_t e=bsp_audio_render_stop_output();
    esp_err_t sleep=bsp_audio_sleep();
    if(e==ESP_OK&&sleep==ESP_OK)c->audio=false;
    return e==ESP_OK&&sleep==ESP_OK;
}
static bool close_(void *v) {
    idf_context *c=v;bool ok=true;
    if(c->ws){ok=esp_transport_close(c->ws)==0;esp_transport_destroy(c->ws);c->ws=NULL;}
    if(c->tls){esp_transport_destroy(c->tls);c->tls=NULL;}
    return ok;
}
static uint64_t now(void *v){(void)v;return (uint64_t)(esp_timer_get_time()/1000);}
static bool stopped(void *v){idf_context*c=v;return c->config->stopped(c->config->stop_context);}
/* Deliberately private: compiler checks every adapter operation in opt-in builds,
 * but no entry can obtain/use this table until the missing reference contract is
 * implemented and separately reviewed. It is not a production diagnostic hook. */
static const pocket_live_worker_ops operations __attribute__((used))={
    NULL,connect_,send_,receive_,audio_start,capture,submit,rendered,audio_stop,close_,now,stopped
};
