#include "pocket_diagnostics.h"
#include <inttypes.h>
#include <stdio.h>

void pocket_diagnostics_format(const pocket_diagnostics *value,int64_t now_ms,bool paused,
 char *title,size_t title_capacity,char *body,size_t body_capacity){
 const pocket_diagnostics empty={0};const pocket_diagnostics *d=value?value:&empty;
 const bool stale=now_ms<d->checked_ms || now_ms-d->checked_ms>=45000;
 /* ESP-IDF 5.5.3 esp_tls_errors.h: 0x8001..08 include DNS/socket/TCP FIN.
  * Only handshake or mbedTLS/verification evidence attributes this to TLS. */
 const bool tls=d->tls_code || d->verify_flags || d->tls_error==0x8009 || (d->tls_error>=0x8010 && d->tls_error<=0x801d);
 const char *state=!d->checked?"NOT CHECKED":!d->wifi?"WI-FI OFFLINE":!d->utc_ready?"WAITING FOR TIME":
  !d->configured?"SETUP REQUIRED":paused?"POLLING PAUSED":stale?"STATUS STALE":
  d->transport_error?(tls?"TLS FAILED":"CONNECTION FAILED"):
  d->http_status==401 || d->http_status==403?"LOGIN REQUIRED":d->http_status!=200?"HTTP FAILED":
  !d->response_valid?"INVALID RESPONSE":!d->backend_available?"HERMES UNAVAILABLE":"HERMES AVAILABLE";
 if(title && title_capacity)snprintf(title,title_capacity,"%s",state);
 if(!body || !body_capacity)return;
 if(!d->checked){snprintf(body,body_capacity,"Waiting for a status poll.\nNo extra requests are made.");return;}
 if(!d->attempted){
  snprintf(body,body_capacity,"Wi-Fi %s\nSecure time %s\nLink %s\nHTTP not attempted\nVoice not checked\nHeap not measured",
   d->wifi?"connected":"offline",d->utc_ready?"ready":"pending",d->wifi&&d->utc_ready?"missing":"not checked");return;
 }
 const int64_t age=now_ms>=d->checked_ms?(now_ms-d->checked_ms)/1000:0;
 char capabilities[32]="Voice not checked",heap[48]="Heap not measured";
 if(d->response_valid)snprintf(capabilities,sizeof(capabilities),"STT %s / TTS %s",d->stt_known?(d->stt?"yes":"no"):"?",d->tts_known?(d->tts?"yes":"no"):"?");
 if(d->heap_measured)snprintf(heap,sizeof(heap),"Heap %u / %u",d->free_heap,d->largest_block);
 snprintf(body,body_capacity,
  "HTTP %d / ESP %d\nTLS %x / MB %x\nVerify %x / CA %s\n%s\n%s\nAge %" PRId64 "s (last poll)",
  d->http_status,d->transport_error,(unsigned)d->tls_error,(unsigned)d->tls_code,(unsigned)d->verify_flags,
  d->custom_ca?"custom":"bundle",capabilities,heap,age);
}
