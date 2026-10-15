/* Hermes Pocket MIT. Render firmware/main/pocket_ui.c headlessly to BMP files.
 * Build against LVGL 9.5 with tests/lv_conf.h; see firmware/README.md. */
#include "pocket_ui.h"
#include "lvgl.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

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
 pocket_ui_render(&view);advance(ms);return save(directory,name);
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
 v=base;v.page=POCKET_SCREEN_OFF;failed|=shot(out,"screen",v,600);
 v=base;v.setup_active=true;v.ssid="HermesPocket-4F2A";v.password="cat-nap-4821";failed|=shot(out,"setup",v,240);
 return failed;
}
