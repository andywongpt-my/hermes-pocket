/* MIT. Project-local --wrap hooks; SDK files and I/O behavior stay intact. */
#include "pocket_http_timing.h"
#include "esp_http_client.h"
#include "esp_event.h"
#include "esp_transport.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdatomic.h>
#include <stddef.h>
static _Atomic(TaskHandle_t) owner;
static pocket_http_io_timing measured;
static bool body_phase;
static bool current(void){
 TaskHandle_t task=atomic_load_explicit(&owner,memory_order_acquire);
 return task && task==xTaskGetCurrentTaskHandle();
}
void pocket_http_timing_begin(bool enabled){
 atomic_store_explicit(&owner,NULL,memory_order_release);
 measured=(pocket_http_io_timing){0};body_phase=false;
 if(enabled)atomic_store_explicit(&owner,xTaskGetCurrentTaskHandle(),memory_order_release);
}
void pocket_http_timing_headers_sent(void){if(current())body_phase=true;}
void pocket_http_timing_end(pocket_http_io_timing *out){
 atomic_store_explicit(&owner,NULL,memory_order_release);
 if(out)*out=measured;
 measured=(pocket_http_io_timing){0};body_phase=false;
}
int __real_esp_transport_write(esp_transport_handle_t,const char *,int,int);
int __real_esp_transport_read(esp_transport_handle_t,char *,int,int);
esp_err_t __real_esp_event_post(esp_event_base_t,int32_t,const void *,size_t,TickType_t);
int __wrap_esp_transport_write(esp_transport_handle_t transport,const char *data,int length,int timeout){
 if(!current())return __real_esp_transport_write(transport,data,length,timeout);
 bool body=body_phase;int64_t began=esp_timer_get_time();
 int result=__real_esp_transport_write(transport,data,length,timeout);
 unsigned cost=(unsigned)(esp_timer_get_time()-began);measured.write_us+=cost;measured.write_calls++;
 if(body){measured.body_write_us+=cost;if(result>0)measured.body_bytes+=(unsigned)result;}
 return result;
}
int __wrap_esp_transport_read(esp_transport_handle_t transport,char *data,int length,int timeout){
 if(!current())return __real_esp_transport_read(transport,data,length,timeout);
 int64_t began=esp_timer_get_time();int result=__real_esp_transport_read(transport,data,length,timeout);
 measured.read_us+=(unsigned)(esp_timer_get_time()-began);measured.read_calls++;return result;
}
esp_err_t __wrap_esp_event_post(esp_event_base_t base,int32_t id,const void *data,size_t size,TickType_t timeout){
 if(base!=ESP_HTTP_CLIENT_EVENT || !current())return __real_esp_event_post(base,id,data,size,timeout);
 int64_t began=esp_timer_get_time();esp_err_t result=__real_esp_event_post(base,id,data,size,timeout);
 measured.event_post_us+=(unsigned)(esp_timer_get_time()-began);measured.event_posts++;return result;
}
