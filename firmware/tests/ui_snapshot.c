/* Hermes Pocket MIT. Render firmware/main/pocket_ui.c headlessly to BMP files.
 * Build against LVGL 9.5 with tests/lv_conf.h; see firmware/README.md. */
#include "pocket_ui.h"
#include "lvgl.h"
#include "lv_font_pocket_cjk_16.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>
/* Include the real custom-drawn UI once to inspect its row strings/colors. */
#include "../main/pocket_ui.c"

#define W 240
#define H 320
#define RADIUS 30 /* BSP_LVGL_SCREEN_RADIUS: the panel shows black outside it. */
static uint16_t pixels_frame[W*H];
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
  const uint16_t c=outside(x,y)?0:pixels_frame[y*W+x];
  const uint8_t bgr[3]={(uint8_t)((c&31)*255/31),(uint8_t)(((c>>5)&63)*255/63),(uint8_t)((c>>11)*255/31)};
  fwrite(bgr,1,3,file);
 }
 printf("%s\n",path);return fclose(file)!=0;
}
static void fits_usage_line(const char *text,int width,int letter_space){
 lv_point_t size;lv_text_get_size(&size,text,&lv_font_montserrat_14,letter_space,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
 if(size.x>width)fprintf(stderr,"Usage width %ld exceeds %d px (spacing %d): %s\n",(long)size.x,width,letter_space,text);
 assert(size.x<=width);
}
static int fits_local_usage_line(const char *text,int width){
 lv_point_t size;lv_text_get_size(&size,text,&lv_font_montserrat_14,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);
 const int letter_space=size.x>width?-1:0;
 fits_usage_line(text,width,letter_space);return letter_space;
}
static void assert_usage_labels(pocket_ui_view view){
 bool dashboard=false;
 if(view.usage)for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++)dashboard|=view.usage->providers[i].local.present;
 const bool usage=dashboard&&!view.setup_active&&view.page==POCKET_USAGE;
 bool found_footer=false,found_badge=false;
 lv_obj_t *screen=lv_screen_active();
 for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++){
  lv_obj_t *child=lv_obj_get_child(screen,(int32_t)i);
  if(lv_obj_has_flag(child,LV_OBJ_FLAG_HIDDEN)||!lv_obj_check_type(child,&lv_label_class))continue;
  if(lv_obj_get_x(child)==18&&lv_obj_get_y(child)==273){
   found_footer=true;
   const bool home=!view.setup_active&&view.page==POCKET_STATUS;
   const lv_font_t *expected=usage||view.reader_active||home?&lv_font_montserrat_14:lv_obj_get_style_text_font(screen,LV_PART_MAIN);
   assert(lv_obj_get_style_text_font(child,LV_PART_MAIN)==expected);
   if(usage){
    const char *text=lv_label_get_text(child);fits_usage_line(text,204,0);
    assert(strstr(text,view.usage_window%POCKET_USAGE_DASHBOARD_PAGES<POCKET_USAGE_LOCAL_PAGES?"All recorded Pocket runs":"Account quota / resets"));
    assert(strstr(text,"UP/DN: pages"));
   }
   if(home&&!view.reader_active){
    const char *text=lv_label_get_text(child);fits_usage_line(text,204,0);
    assert(strstr(text,"MYT"));
    if(!view.clock_ready)assert(strstr(text,"Waiting for time (MYT)"));
    else if(view.now==1792139700)assert(strstr(text,"16 Oct 2026 16:35 MYT"));
    else if(view.now==1792139760)assert(strstr(text,"16 Oct 2026 16:36 MYT"));
    else if(view.now==1798732740)assert(strstr(text,"31 Dec 2026 23:59 MYT"));
    else if(view.now==1798732800)assert(strstr(text,"01 Jan 2027 00:00 MYT"));
   }
  }
  if(usage&&lv_obj_get_y(child)==60){
   const unsigned page=view.usage_window%POCKET_USAGE_DASHBOARD_PAGES;
   const char *expected=page<POCKET_USAGE_LOCAL_PAGES?pocket_usage_local_title(page):"ACCOUNT QUOTA";
   found_badge=true;assert(!strcmp(lv_label_get_text(child),expected));
   fits_usage_line(expected,180,lv_obj_get_style_text_letter_space(child,LV_PART_MAIN));
  }
 }
 assert(found_footer);if(usage)assert(found_badge);
 if(usage&&view.usage_window%POCKET_USAGE_DASHBOARD_PAGES<POCKET_USAGE_LOCAL_PAGES){
  const unsigned page=view.usage_window%POCKET_USAGE_DASHBOARD_PAGES;
  for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++){
   pocket_usage_display display;pocket_usage_local_format(&view.usage->providers[i].local,page,view.now,&display);
   fits_local_usage_line(display.value,180);fits_local_usage_line(display.detail,180);
   assert(!strstr(display.value,"...")&&!strstr(display.detail,"..."));
   if(page<7){assert(!strncmp(display.value,"Main ",5));assert(!strncmp(display.detail,"Aux ",4));}
   else if(page<13)assert(!strcmp(display.detail,page==12?"This provider":"Device/target"));
   else assert(!strcmp(display.detail,"Hermes runtime"));
  }
 }
}
static lv_obj_t *reader_body(void){
 lv_obj_t *screen=lv_screen_active();
 for(uint32_t i=0;i<lv_obj_get_child_count(screen);i++){
  lv_obj_t *child=lv_obj_get_child(screen,(int32_t)i);
  if(lv_obj_check_type(child,&lv_label_class) && !lv_obj_has_flag(child,LV_OBJ_FLAG_HIDDEN) && lv_obj_get_x(child)==20 && lv_obj_get_y(child)==96 && lv_obj_get_height(child)==166)return child;
 }
 assert(!"reader body missing");return NULL;
}
static void assert_reader_fits(void){
 lv_obj_t *body=reader_body();lv_point_t size;
 lv_text_get_size(&size,lv_label_get_text(body),lv_obj_get_style_text_font(body,LV_PART_MAIN),0,lv_obj_get_style_text_line_space(body,LV_PART_MAIN),200,LV_TEXT_FLAG_NONE);
 assert(size.y<=166);
}
static void assert_home_freshness(pocket_ui_view view){
 if(view.page!=POCKET_STATUS||view.setup_active||view.reader_active||!view.diagnostics||!view.diagnostics->checked)return;
 const int64_t age=view.uptime_ms-view.diagnostics->checked_ms;
 if(age<0||age>=45000){
  assert(!strcmp(row_items[1].value,"Cached")&&!strcmp(row_items[2].value,"Check status"));
  assert(row_items[1].dot==AMBER&&row_items[2].dot==AMBER);
  assert(!strcmp(lv_label_get_text(badge),view.alert_pending?"USAGE ALERT":"STATUS STALE"));
 }else if(view.online&&view.speech_ready){
  assert(!strcmp(row_items[1].value,"Available")&&!strcmp(row_items[2].value,"Ready"));
 }
}
static int shot(const char *directory,const char *name,pocket_ui_view view,uint32_t ms){
 pocket_ui_render(&view);advance(ms);
 assert_usage_labels(view);
 assert_home_freshness(view);
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
 lv_display_set_buffers(display,pixels_frame,NULL,sizeof pixels_frame,LV_DISPLAY_RENDER_MODE_DIRECT);
 lv_display_set_flush_cb(display,flush);
 pocket_ui_create();
 lv_font_glyph_dsc_t glyph={0};
 const lv_font_t *ui_font=lv_obj_get_style_text_font(lv_screen_active(),LV_PART_MAIN);
 assert(lv_font_get_glyph_dsc(ui_font,&glyph,0x8f49,0));
 assert(glyph.resolved_font==&lv_font_pocket_cjk_16);
 /* Bound every safe integer, including positive digit-pair kerning ("48"
  * advances 10 px). Tight spacing is local to strings that exceed their slot. */
 int digit_width=0;lv_point_t text_size;
 for(char digit='0';digit<='9';digit++)for(char next='0';next<='9';next++){
  assert(lv_font_get_glyph_dsc(&lv_font_montserrat_14,&glyph,(uint32_t)digit,(uint32_t)next));
  if(glyph.adv_w>digit_width)digit_width=glyph.adv_w;
 }
 lv_text_get_size(&text_size,"Main ",&lv_font_montserrat_14,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);assert(text_size.x+16*digit_width-20<=180);
 lv_text_get_size(&text_size,"Aux ",&lv_font_montserrat_14,0,0,LV_COORD_MAX,LV_TEXT_FLAG_NONE);assert(text_size.x+16*digit_width-19<=180);
 assert(fits_local_usage_line("Main 4444444444444444",180)==-1);
 assert(fits_local_usage_line("Main 4848484848484848",180)==-1);
 assert(fits_local_usage_line("Main 9007199254740991",180)==0);
 assert(fits_local_usage_line("Main Unknown",180)==0);
 assert(fits_local_usage_line("8888-04-04 04:40:40 UTC",180)==-1);
 assert(fits_local_usage_line("Clock unknown",114)==-1);
 assert(fits_local_usage_line("Clock mismatch",114)==-1);
 for(unsigned page=0;page<POCKET_USAGE_LOCAL_PAGES;page++)fits_usage_line(pocket_usage_local_title(page),180,0);
 fits_usage_line("All recorded Pocket runs\nOK: next  UP/DN: pages",204,0);
 const pocket_ui_view base={.page=POCKET_STATUS,.battery_percent=76,.wifi_connected=true,.online=true,.speech_ready=true,
  .ssid="",.password="",.message="",.text="",.reply=""};
 pocket_ui_view v;int failed=0;
 v=base;failed|=shot(out,"home",v,240);
 v.alert_pending=true;failed|=shot(out,"home-alert",v,240);
 v=base;v.online=false;v.speech_ready=false;v.battery_percent=12;failed|=shot(out,"home-offline",v,600);
 v=base;v.page=POCKET_CHAT;failed|=shot(out,"chat-ready",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=v.recording=true;v.message="Listening... release OK to send";failed|=shot(out,"chat-listening",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=true;v.message="Hermes is working...";v.text="What's the weather in Kuala Lumpur today?";failed|=shot(out,"chat-thinking",v,360);
 v=base;v.page=POCKET_CHAT;v.voice_here=v.busy=v.playing=true;v.message="Speaking (OK to stop)";v.text="What's the weather in Kuala Lumpur today?";
 v.reply="It's sunny and 31 degrees, with a chance of thunderstorms after 4pm. Bring an umbrella!";failed|=shot(out,"chat-speaking",v,240);
 v=base;v.page=POCKET_CHAT;v.voice_here=true;v.message="Wi-Fi offline; hold OK on Status";failed|=shot(out,"chat-error",v,240);
 v=base;v.page=POCKET_CHAT;v.online=false;failed|=shot(out,"chat-offline",v,600);
 v=base;v.page=POCKET_CHAT;v.voice_here=true;v.text="轉錄回覆：繁體中文";
 v.reply="耗電慢慢改善，請勿丟棄錄音。\n「連線已恢復」；準備就緒！";
 failed|=shot(out,"chat-traditional",v,240);
 v.text="转录回复：简体中文";v.reply="耗电慢慢改善，请勿丢弃录音。\n“连接已恢复”；准备就绪！";
 failed|=shot(out,"chat-simplified",v,240);
 char utf8_edge[304];for(unsigned i=0;i<303;i+=3)memcpy(utf8_edge+i,"測",3);utf8_edge[303]=0;
 v.text=v.reply=utf8_edge;failed|=shot(out,"chat-utf8-boundary",v,240);
 v=base;v.page=POCKET_MODELS;v.models_available_mask=7;v.models_verified_mask=3;v.models_status="Next turn; session only";
 failed|=shot(out,"models-default",v,240);
 v.model_candidate=POCKET_MODEL_OPUS;failed|=shot(out,"models-pending",v,240);
 v.model_choice=POCKET_MODEL_OPUS;failed|=shot(out,"models-opus",v,240);
 v.model_choice=v.model_candidate=POCKET_MODEL_GLM_FLASH;failed|=shot(out,"models-glm-unverified",v,240);
 v.models_available_mask=1;v.models_verified_mask=0;v.models_status="Bridge offline; choice kept";v.model_apply_failed=true;
 failed|=shot(out,"models-unavailable",v,240);
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
 v.alert_pending=true;usage.providers[0].metrics[0].value=85;failed|=shot(out,"usage-alert",v,240);
 v.alert_pending=false;usage.providers[0].metrics[0].value=26;
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
 /* Synthetic device/target history stays separate from account quota. */
 pocket_usage_snapshot local_usage={0};
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++){
  local_usage.providers[i].availability=POCKET_USAGE_NOT_CONFIGURED;
  pocket_usage_local *local=&local_usage.providers[i].local;
  *local=(pocket_usage_local){.present=true,.availability=POCKET_USAGE_AVAILABLE,
   .main_known=(1u<<POCKET_USAGE_COUNTER_COUNT)-1,.auxiliary_known=(1u<<POCKET_USAGE_COUNTER_COUNT)-1,
   .coverage_known=(1u<<POCKET_USAGE_COVERAGE_COUNT)-1,.has_fetched_at=true,.fetched_at=now-120,.has_last_seen=true,.last_seen=now-86400*365};
  for(unsigned j=0;j<POCKET_USAGE_COUNTER_COUNT;j++){local->main[j]=1200+i*100+j;local->auxiliary[j]=20+i*10+j;}
  for(unsigned j=0;j<POCKET_USAGE_COVERAGE_COUNT;j++)local->coverage[j]=j==POCKET_USAGE_PROVIDER_OBSERVED_RUNS?4+i:18-j;
 }
 const pocket_usage_snapshot local_available=local_usage;
 v=base;v.page=POCKET_USAGE;v.usage=&local_usage;v.now=now;
 failed|=shot(out,"usage-local-available",v,240);
 for(unsigned page=0;page<POCKET_USAGE_DASHBOARD_PAGES;page++){
  char name[48];snprintf(name,sizeof name,"usage-dashboard-%02u",page);v.usage_window=page;failed|=shot(out,name,v,240);
 }
 v.usage_window=POCKET_USAGE_DASHBOARD_PAGES;failed|=shot(out,"usage-local-cycle",v,240);
 v.usage_window=0;local_usage.providers[0].local.present=false;local_usage.providers[1].local.availability=POCKET_USAGE_PARTIAL;
 local_usage.providers[1].local.auxiliary_known=0;failed|=shot(out,"usage-local-partial",v,240);
 local_usage=local_available;
 local_usage.providers[0].local.main[POCKET_USAGE_API_CALLS]=local_usage.providers[0].local.auxiliary[POCKET_USAGE_API_CALLS]=0;
 local_usage.providers[0].local.auxiliary_known&=(uint8_t)~(1u<<POCKET_USAGE_API_CALLS);
 local_usage.providers[1].local.main[POCKET_USAGE_API_CALLS]=local_usage.providers[1].local.auxiliary[POCKET_USAGE_API_CALLS]=0;
 local_usage.providers[1].local.main_known&=(uint8_t)~(1u<<POCKET_USAGE_API_CALLS);
 failed|=shot(out,"usage-local-null-zero",v,240);
 local_usage=local_available;local_usage.providers[0].local.availability=POCKET_USAGE_STALE;
 local_usage.providers[1].local.availability=POCKET_USAGE_FAILED;local_usage.providers[2].local.availability=POCKET_USAGE_PARTIAL;
 failed|=shot(out,"usage-local-stale-failed",v,240);
 local_usage=local_available;
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++){
  local_usage.providers[i].local.main[POCKET_USAGE_API_CALLS]=POCKET_USAGE_COUNTER_MAX;
  local_usage.providers[i].local.auxiliary[POCKET_USAGE_API_CALLS]=UINT64_C(4294967297)+i;
 }
 failed|=shot(out,"usage-local-large",v,240);
 v.usage_window=7;failed|=shot(out,"usage-local-coverage-device",v,240);
 v.usage_window=12;failed|=shot(out,"usage-local-coverage-provider",v,240);
 v.usage_window=13;failed|=shot(out,"usage-local-fetched-utc",v,240);
 v.usage_window=14;failed|=shot(out,"usage-local-last-seen-utc",v,240);
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++)local_usage.providers[i].local.has_last_seen=false;
 failed|=shot(out,"usage-local-time-unknown",v,240);
 v.usage_window=0;v.now=0;failed|=shot(out,"usage-local-clock-unknown",v,240);
 v.now=now;
 for(unsigned i=0;i<POCKET_USAGE_PROVIDER_COUNT;i++)local_usage.providers[i].local.fetched_at=now+60;
 failed|=shot(out,"usage-local-clock-mismatch",v,240);
 v.usage_window=15;failed|=shot(out,"usage-local-quota-unknown",v,240);
 local_usage.providers[1].availability=POCKET_USAGE_AVAILABLE;local_usage.providers[1].metric_count=1;
 local_usage.providers[1].metrics[0]=metric(26,POCKET_USAGE_PERCENT_USED,POCKET_USAGE_SUBSCRIPTION,300,now);
 failed|=shot(out,"usage-local-quota-available",v,240);
 local_usage.providers[0].local.present=local_usage.providers[2].local.present=false;v.usage_window=0;
 failed|=shot(out,"usage-local-single-provider",v,240);
 v=base;failed|=shot(out,"home-after-local-usage",v,240);
 pocket_diagnostics diagnostics={0};
 v=base;v.page=POCKET_DIAGNOSTICS;v.diagnostics=&diagnostics;v.uptime_ms=12000;
 failed|=shot(out,"diag-unchecked",v,240);
 diagnostics=(pocket_diagnostics){.checked=true,.wifi=true,.utc_ready=true,.configured=true,.attempted=true,.heap_measured=true,
  .response_valid=true,.backend_available=true,.stt=true,.tts=true,.stt_known=true,.tts_known=true,.custom_ca=true,
  .http_status=200,.free_heap=53052,.largest_block=40960,.checked_ms=1000};
 failed|=shot(out,"diag-ready",v,240);
 diagnostics.transport_error=-1;diagnostics.http_status=0;diagnostics.tls_error=0x801a;diagnostics.tls_code=0x2700;diagnostics.verify_flags=8;
 failed|=shot(out,"diag-tls",v,240);
 diagnostics.transport_error=0;diagnostics.http_status=401;diagnostics.tls_error=diagnostics.tls_code=diagnostics.verify_flags=0;
 failed|=shot(out,"diag-login",v,240);
 v.busy=true;v.uptime_ms=61000;failed|=shot(out,"diag-paused",v,240);
 v=base;v.target_save_failed=true;failed|=shot(out,"home-save-failed",v,240);
 v=base;v.page=POCKET_TASKS;v.task_status="Not fetched";failed|=shot(out,"tasks-unfetched",v,240);
 v.task_count=2;v.task_selected=0;v.task_phase="RUNNING";v.task_status="Fetched";failed|=shot(out,"tasks-running",v,240);
 v.task_phase="NEEDS APPROVAL";v.task_approval=true;failed|=shot(out,"tasks-approval",v,240);
 v.task_phase="UNKNOWN";v.task_approval=false;v.task_unknown=true;failed|=shot(out,"tasks-unknown",v,240);
 v.task_unknown=false;v.task_phase="COMPLETED";v.task_has_result=true;v.task_result="Synthetic result: \xe4\xbd\xa0\xe5\xa5\xbd. The requested task completed.";
 failed|=shot(out,"tasks-result-utf8",v,240);
 char long_result[769];for(unsigned i=0;i<768;i+=3)memcpy(long_result+i,"\xe4\xbd\xa0",3);long_result[768]=0;
 v.task_result=long_result;v.task_truncated=true;failed|=shot(out,"tasks-truncated",v,240);
 v.task_status="Offline / cached";failed|=shot(out,"tasks-offline",v,240);
 v=base;v.page=POCKET_TOOLS;v.tool=POCKET_TOOL_STATUS;failed|=shot(out,"tools-status",v,240);
 v.tool=POCKET_TOOL_USAGE;failed|=shot(out,"tools-usage",v,240);
 v.tool=POCKET_TOOL_TASKS;failed|=shot(out,"tools-tasks",v,240);
 v.tool=POCKET_TOOL_READER;failed|=shot(out,"tools-reader",v,240);
 v.reader_open_failed=true;failed|=shot(out,"tools-reader-unavailable",v,240);v.reader_open_failed=false;
 char reader_raw[769],reader_expected[769],reader_rebuilt[769],last_screen[769];
 const char piece[]="\xe6\xb8\xac\xe8\xa9\xa6 A\xf0\x9f\x99\x82\n";
 unsigned fill=0;while(fill+sizeof(piece)-1<=768){memcpy(reader_raw+fill,piece,sizeof(piece)-1);fill+=(unsigned)sizeof(piece)-1;}
 while(fill<768)reader_raw[fill++]='x';
 reader_raw[768]=0;reader_raw[12]=0;
 memcpy(reader_expected,reader_raw,sizeof reader_raw);for(unsigned i=0;i<768;i++)if((unsigned char)reader_expected[i]<32&&reader_expected[i]!='\n')reader_expected[i]=' ';
 v.reader_active=v.reader_has_page=v.reader_navigation=v.reader_has_next=true;v.reader_generation=1;v.reader_target=1;
 v.reader_page=0;v.reader_page_count=2;v.reader_utf8_bytes=768;v.task_result=reader_raw;v.reader_status="Full reply";
 failed|=shot(out,"reader-unicode-first",v,240);unsigned rebuilt=0,screens=0;
 for(;;){
  const char *visible=lv_label_get_text(reader_body());size_t bytes=strlen(visible);assert_reader_fits();
  assert(bytes&&bytes<=768-rebuilt&&screens++<768);memcpy(reader_rebuilt+rebuilt,visible,bytes);rebuilt+=(unsigned)bytes;
  snprintf(last_screen,sizeof last_screen,"%s",visible);
  const bool fetch=pocket_ui_reader_step(1);
  if(fetch){assert(rebuilt==768);break;}
  pocket_ui_render(&v);advance(10);
 }
 reader_rebuilt[rebuilt]=0;assert(screens>1&&!memcmp(reader_rebuilt,reader_expected,769));
 failed|=shot(out,"reader-unicode-last",v,240);
 v.reader_generation++;v.reader_page=1;v.reader_has_prev=true;v.reader_has_next=false;v.reader_utf8_bytes=3;v.task_result="\xe5\xb0\xbe";
 failed|=shot(out,"reader-final-block",v,240);assert(!pocket_ui_reader_step(1));assert(pocket_ui_reader_step(-1));
 v.reader_generation++;v.reader_page=0;v.reader_has_prev=false;v.reader_has_next=true;v.reader_utf8_bytes=768;v.task_result=reader_raw;
 failed|=shot(out,"reader-previous-last",v,240);assert(!strcmp(lv_label_get_text(reader_body()),last_screen));
 assert(!pocket_ui_reader_step(-1));pocket_ui_render(&v);assert_reader_fits();assert(strcmp(lv_label_get_text(reader_body()),last_screen));
 v.reader_loading=true;v.reader_navigation=false;v.reader_status="Loading full reply...";
 failed|=shot(out,"reader-loading",v,240);assert(!pocket_ui_reader_step(1));
 v.reader_generation++;v.reader_page=0;v.reader_page_count=1;v.reader_utf8_bytes=0;v.reader_has_prev=v.reader_has_next=v.reader_loading=false;v.reader_navigation=true;v.task_result="";
 failed|=shot(out,"reader-empty",v,240);assert(!strcmp(lv_label_get_text(reader_body()),"Empty reply"));assert(!pocket_ui_reader_step(1)&&!pocket_ui_reader_step(-1));
 v.reader_navigation=false;v.reader_has_page=false;v.reader_status="Reply changed. Close and reopen.";
 failed|=shot(out,"reader-revision-changed",v,240);assert(!pocket_ui_reader_step(1));
 v=base;failed|=shot(out,"home-after-reader",v,240);
 v=base;v.page=POCKET_TOOLS;v.tool=POCKET_TOOL_TODO_UNAVAILABLE;failed|=shot(out,"tools-todo",v,240);
 v=base;v.page=POCKET_AUDIO;v.volume=60;failed|=shot(out,"audio",v,240);
 v.audio_edit=true;failed|=shot(out,"audio-edit",v,240);
 v.volume=0;failed|=shot(out,"audio-muted",v,240);
 v=base;v.page=POCKET_ALERTS;v.alert_threshold=80;v.alert_note="No configured Usage feeds.";failed|=shot(out,"alerts-default",v,240);
 v.usd_floor_enabled=true;failed|=shot(out,"alerts-usd",v,240);
 v.alert_threshold=0;v.usd_floor_enabled=false;failed|=shot(out,"alerts-off",v,240);
 v.alert_threshold=90;v.alert_note="Last: Codex 90.0% used\nLimit 90.0% used";failed|=shot(out,"alerts-note",v,240);
 v=base;v.page=POCKET_SCREEN_OFF;failed|=shot(out,"screen",v,600);
 v=base;v.setup_active=true;v.ssid="HermesPocket-4F2A";v.password="cat-nap-4821";failed|=shot(out,"setup",v,240);
 v.setup_message="Testing Wi-Fi; old networks retained";failed|=shot(out,"setup-wifi-testing",v,240);
 v.setup_message="Wi-Fi saved. Check Hermes on Status";failed|=shot(out,"setup-wifi-saved",v,240);
 v.setup_message="Join failed; restoring saved Wi-Fi";failed|=shot(out,"setup-wifi-failed",v,240);
 pocket_diagnostics cached={.checked=true,.checked_ms=1000,.backend_available=true};
 v=base;v.diagnostics=&cached;v.uptime_ms=45999;failed|=shot(out,"home-cache-fresh",v,240);
 v.uptime_ms=46000;failed|=shot(out,"home-cache-stale",v,240);
 v.uptime_ms=999;failed|=shot(out,"home-cache-backwards",v,240);
 v.online=v.speech_ready=false;failed|=shot(out,"home-cache-stale-offline",v,240);
 v=base;v.clock_ready=true;v.now=1792139700;failed|=shot(out,"home-clock-synced",v,240);
 v.wifi_connected=v.online=false;v.now+=60;failed|=shot(out,"home-clock-offline",v,240);
 v.now=1798732740;failed|=shot(out,"home-clock-year-end",v,240);
 v.now+=60;failed|=shot(out,"home-clock-new-year",v,240);
 v.wifi_connected=v.online=true;failed|=shot(out,"home-clock-reconnected",v,240);
 return failed;
}
