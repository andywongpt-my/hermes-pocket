/* Hermes Pocket MIT. Render firmware/main/pocket_ui.c headlessly to BMP files.
 * Build against LVGL 9.5 with tests/lv_conf.h; see firmware/README.md. */
#include "pocket_ui.h"
#include "lvgl.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>

#define W 240
#define H 320
#define RADIUS 30 /* BSP_LVGL_SCREEN_RADIUS: the panel shows black outside it. */
static uint16_t frame[W*H];
static uint32_t now_ms;
static uint32_t tick(void){return now_ms;}
static void flush(lv_display_t *display,const lv_area_t *area,uint8_t *pixels){(void)area;(void)pixels;lv_display_flush_ready(display);}
static void advance(uint32_t ms){for(uint32_t t=0;t<ms;t+=10){now_ms+=10;lv_timer_handler();}}
static bool outside(int x,int y){
 const int cx=x<RADIUS?RADIUS:x>=W-RADIUS?W-RADIUS-1:x,cy=y<RADIUS?RADIUS:y>=H-RADIUS?H-RADIUS-1:y;
 return (x-cx)*(x-cx)+(y-cy)*(y-cy)>RADIUS*RADIUS;
}
static int save(const char *directory,const char *name){
 lv_obj_invalidate(lv_screen_active());lv_refr_now(NULL);
 char path[512];snprintf(path,sizeof path,"%s/%s.bmp",directory,name);
 FILE *file=fopen(path,"wb");if(!file)return 1;
 const uint32_t row=W*3,size=54+row*H;uint8_t header[54]={'B','M'};
 #define PUT32(o,v) do{header[o]=(uint8_t)(v);header[o+1]=(uint8_t)((v)>>8);header[o+2]=(uint8_t)((v)>>16);header[o+3]=(uint8_t)((v)>>24);}while(0)
 PUT32(2,size);PUT32(10,54);PUT32(14,40);PUT32(18,W);PUT32(22,H);header[26]=1;header[28]=24;PUT32(34,row*H);
 fwrite(header,1,sizeof header,file);
 for(int y=H-1;y>=0;y--)for(int x=0;x<W;x++){
  const uint16_t c=outside(x,y)?0:frame[y*W+x];
  const uint8_t bgr[3]={(uint8_t)((c&31)*255/31),(uint8_t)(((c>>5)&63)*255/63),(uint8_t)((c>>11)*255/31)};
  fwrite(bgr,1,3,file);
 }
 printf("%s\n",path);return fclose(file)!=0;
}
static int shot(const char *directory,const char *name,pocket_ui_view view,uint32_t ms){
 pocket_ui_render(&view);advance(ms);
 /* Expanded Usage rows must fit inside the card. Returning Home restores the
  * compact dimensions, with no collision with the badge or bottom controls. */
 if(!view.setup_active&&(view.page==POCKET_USAGE||view.page==POCKET_STATUS)){
  bool found=false;lv_obj_t *screen=lv_screen_active();
  for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++){
   lv_obj_t *child=lv_obj_get_child(screen,(int32_t)i);
   if(lv_obj_has_flag(child,LV_OBJ_FLAG_HIDDEN)||lv_obj_get_x(child)!=20||lv_obj_get_width(child)!=200)continue;
   const bool usage=view.page==POCKET_USAGE;
   assert(lv_obj_get_y(child)==(usage?94:182));assert(lv_obj_get_height(child)==(usage?168:76));found=true;
  }
  assert(found);
 }
 return save(directory,name);
}
static pocket_usage_metric metric(double value,pocket_usage_unit unit,pocket_usage_category category,unsigned minutes,int64_t now){
 return (pocket_usage_metric){.has_value=true,.value=value,.unit=unit,.category=category,.window_minutes=minutes,
  .has_fetched_at=true,.fetched_at=now-120,.has_resets_at=true,.resets_at=now+7200,
  .has_expires_at=true,.expires_at=now+60,.availability=POCKET_USAGE_AVAILABLE};
}
int main(int argc,char **argv){
 const char *out=argc>1?argv[1]:".";
 lv_init();lv_tick_set_cb(tick);
 lv_display_t *display=lv_display_create(W,H);
 lv_display_set_color_format(display,LV_COLOR_FORMAT_RGB565);
 lv_display_set_buffers(display,frame,NULL,sizeof frame,LV_DISPLAY_RENDER_MODE_DIRECT);
 lv_display_set_flush_cb(display,flush);
 pocket_ui_create();
 const pocket_ui_view base={.page=POCKET_STATUS,.battery_percent=76,.wifi_connected=true,.online=true,.speech_ready=true,
  .ssid="",.password="",.message="",.text="",.reply=""};
 pocket_ui_view v;int failed=0;
 v=base;failed|=shot(out,"home",v,240);
 v=base;v.online=false;v.speech_ready=false;v.battery_percent=12;failed|=shot(out,"home-offline",v,600);
 v=base;v.page=POCKET_CHAT;failed|=shot(out,"chat-ready",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=v.recording=true;v.message="Listening... release OK to send";failed|=shot(out,"chat-listening",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=true;v.message="Hermes is working...";v.text="What's the weather in Kuala Lumpur today?";failed|=shot(out,"chat-thinking",v,360);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=v.playing=true;v.message="Speaking (OK to stop)";v.text="What's the weather in Kuala Lumpur today?";
 v.reply="It's sunny and 31 degrees, with a chance of thunderstorms after 4pm. Bring an umbrella!";failed|=shot(out,"chat-speaking",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=true;v.message="Wi-Fi offline; hold OK on Status";failed|=shot(out,"chat-error",v,240);
 v=base;v.page=POCKET_CHAT;v.online=false;failed|=shot(out,"chat-offline",v,600);
 v=base;v.page=POCKET_USAGE;v.battery_percent=-1;failed|=shot(out,"usage",v,240);
 /* Fully synthetic account samples: no private account or provider reads. */
 const int64_t now=1800000000;pocket_usage_snapshot usage={0};
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++)usage.providers[i].availability=POCKET_USAGE_AVAILABLE;
 usage.providers[0].metric_count=2;
 usage.providers[0].metrics[0]=metric(26,POCKET_USAGE_PERCENT_USED,POCKET_USAGE_SUBSCRIPTION,300,now);
 usage.providers[0].metrics[1]=metric(72.5,POCKET_USAGE_PERCENT_REMAINING,POCKET_USAGE_SUBSCRIPTION,10080,now);
 usage.providers[1].metric_count=1;
 usage.providers[1].metrics[0]=metric(0,POCKET_USAGE_PERCENT_REMAINING,POCKET_USAGE_SUBSCRIPTION,10080,now);
 usage.providers[2].metric_count=2;
 usage.providers[2].metrics[0]=metric(12.34,POCKET_USAGE_USD_CREDIT,POCKET_USAGE_INCLUDED_CREDIT,43200,now);
 usage.providers[2].metrics[1]=metric(45.67,POCKET_USAGE_USD_CREDIT,POCKET_USAGE_PURCHASED_CREDIT,0,now);
 v=base;v.page=POCKET_USAGE;v.usage=&usage;v.now=now;failed|=shot(out,"usage-available",v,240);
 v.usage_window=1;failed|=shot(out,"usage-next-window",v,240);
 v.usage_window=0;usage.providers[0].metrics[0].value=0;usage.providers[1].metrics[0].has_value=false;
 usage.providers[2].metric_count=0;usage.providers[2].availability=POCKET_USAGE_NOT_CONFIGURED;
 failed|=shot(out,"usage-null-zero",v,240);
 usage.providers[0].metrics[0].value=83;usage.providers[0].metrics[0].expires_at=now;
 usage.providers[1].availability=POCKET_USAGE_PARTIAL;usage.providers[1].metrics[0].has_value=true;usage.providers[1].metrics[0].value=1.25;
 usage.providers[2].availability=POCKET_USAGE_FAILED;usage.providers[2].metric_count=1;
 usage.providers[2].metrics[0]=metric(3.4,POCKET_USAGE_USD_CREDIT,POCKET_USAGE_PURCHASED_CREDIT,0,now);
 failed|=shot(out,"usage-stale-partial-failed",v,240);
 v=base;failed|=shot(out,"home-after-usage",v,240);
 pocket_diagnostics diagnostics={0};
 v=base;v.page=POCKET_DIAGNOSTICS;v.diagnostics=&diagnostics;v.uptime_ms=12000;
 failed|=shot(out,"diag-unchecked",v,240);
 diagnostics=(pocket_diagnostics){.checked=true,.wifi=true,.utc_ready=true,.configured=true,.attempted=true,.heap_measured=true,
  .response_valid=true,.backend_available=true,.stt=true,.tts=true,.custom_ca=true,
  .http_status=200,.free_heap=53052,.largest_block=40960,.checked_ms=1000};
 failed|=shot(out,"diag-ready",v,240);
 diagnostics.transport_error=-1;diagnostics.http_status=0;diagnostics.tls_error=0x801a;diagnostics.tls_code=0x2700;diagnostics.verify_flags=8;
 failed|=shot(out,"diag-tls",v,240);
 diagnostics.transport_error=0;diagnostics.http_status=401;diagnostics.tls_error=diagnostics.tls_code=diagnostics.verify_flags=0;
 failed|=shot(out,"diag-login",v,240);
 v.busy=true;v.uptime_ms=61000;failed|=shot(out,"diag-paused",v,240);
 v=base;v.target_save_failed=true;failed|=shot(out,"home-save-failed",v,240);
 v=base;v.page=POCKET_SCREEN_OFF;failed|=shot(out,"screen",v,600);
 v=base;v.setup_active=true;v.ssid="HermesPocket-4F2A";v.password="cat-nap-4821";failed|=shot(out,"setup",v,240);
 return failed;
}
