/* Hermes Pocket MIT. Private bridge UI and bounded voice client. */
#include "pocket_model.h"
#include "pocket_setup.h"
#include "pocket_voice.h"
#include "bsp_i2c.h"
#include "bsp_display.h"
#include "bsp_button.h"
#include "bsp_battery.h"
#include "bsp_pins.h"
#include "lvgl.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_err.h"
#include <stdatomic.h>

typedef struct { bsp_btn_t button; bsp_btn_ev_t event; } input;
static QueueHandle_t events;
static pocket_model model;
static lv_obj_t *title,*body,*footer,*avatar,*eye_left,*eye_right,*mouth,*badge,*battery,*off_option;
static unsigned animation_tick;
static bool battery_ready,screen_off,wake_gesture;
static atomic_int battery_percent=ATOMIC_VAR_INIT(-1);
static unsigned held_buttons;
static bool discard_gesture[BSP_BTN_COUNT];

static void refresh_battery(void) {
 /* CW2017 supplies SOC directly; never substitute voltage or a guessed 100%. */
 const int percent=battery_ready?bsp_battery_soc():-1;
 atomic_store_explicit(&battery_percent,percent,memory_order_relaxed);
}
static void monitor_battery(void *unused) {
 (void)unused;
 for(;;){
  vTaskDelay(pdMS_TO_TICKS(30000));
  /* A bounded init retry can take seconds. Keep it off the live input task. */
  if(!battery_ready)battery_ready=bsp_battery_init()==ESP_OK;
  refresh_battery();
 }
}
static lv_obj_t *shape(lv_obj_t *parent,int x,int y,int width,int height,uint32_t color,int radius) {
 lv_obj_t *obj=lv_obj_create(parent);lv_obj_remove_style_all(obj);lv_obj_set_pos(obj,x,y);lv_obj_set_size(obj,width,height);
 lv_obj_set_style_bg_color(obj,lv_color_hex(color),0);lv_obj_set_style_bg_opa(obj,LV_OPA_COVER,0);
 lv_obj_set_style_radius(obj,radius,0);lv_obj_remove_flag(obj,LV_OBJ_FLAG_SCROLLABLE);return obj;
}
static void draw_cat_ears(lv_event_t *event) {
 /* Draw directly into LVGL's existing buffer, without an image/layer allocation. */
 static const int ears[4][3][2]={
  {{18,8},{43,34},{16,43}},{{74,8},{49,34},{76,43}},
  {{22,18},{35,33},{21,37}},{{70,18},{57,33},{71,37}}
 };
 lv_area_t area;lv_obj_get_coords(lv_event_get_target(event),&area);
 lv_draw_triangle_dsc_t triangle;lv_draw_triangle_dsc_init(&triangle);
 for(unsigned i=0;i<4;i++){
  triangle.color=lv_color_hex(i<2?0xF2CAA1:0xD98F8F);
  for(unsigned j=0;j<3;j++){
   triangle.p[j].x=area.x1+ears[i][j][0];triangle.p[j].y=area.y1+ears[i][j][1];
  }
  lv_draw_triangle(lv_event_get_layer(event),&triangle);
 }
}
static void create_avatar(lv_obj_t *screen) {
 avatar=shape(screen,74,57,92,82,0x1A3040,20);
 /* Original cartoon cat: pointed ears, whiskers, pink nose and mint collar. */
 lv_obj_add_event_cb(avatar,draw_cat_ears,LV_EVENT_DRAW_MAIN,NULL);
 shape(avatar,16,26,60,43,0xF2CAA1,22);
 shape(avatar,24,65,18,10,0xF2CAA1,5);
 shape(avatar,50,65,18,10,0xF2CAA1,5);
 shape(avatar,30,68,32,5,0x8FE3CB,2);
 eye_left=shape(avatar,31,41,5,7,0x172B39,2);
 eye_right=shape(avatar,56,41,5,7,0x172B39,2);
 shape(avatar,43,52,7,5,0xD98F8F,2);
 mouth=shape(avatar,40,59,13,3,0x925D54,2);
 shape(avatar,5,48,19,2,0xF4F1E9,1);
 shape(avatar,7,55,17,2,0xF4F1E9,1);
 shape(avatar,68,48,19,2,0xF4F1E9,1);
 shape(avatar,68,55,17,2,0xF4F1E9,1);
}
static void render(void) {
 const char *names[]={"HOME","CHAT","USAGE","SCREEN"};
 lv_label_set_text_fmt(title,"%s / %s",names[model.page],model.target==0?"WIN":"LINUX");
 const int percent=atomic_load_explicit(&battery_percent,memory_order_relaxed);
 if(percent>=0)lv_label_set_text_fmt(battery,"%d%%",percent);
 else lv_label_set_text(battery,"N/A");
 lv_obj_set_style_text_color(battery,lv_color_hex(percent>=0 && percent<=15?0xF3A88D:0xB9CBD6),0);
 pocket_setup_state setup;pocket_setup_read(&setup);
 static pocket_voice_state voice;pocket_voice_read(&voice);
 bool show_avatar=!setup.active && (model.page==POCKET_STATUS || model.page==POCKET_CHAT);
 if(show_avatar)lv_obj_remove_flag(avatar,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(avatar,LV_OBJ_FLAG_HIDDEN);
 if(!setup.active && model.page==POCKET_SCREEN_OFF)lv_obj_remove_flag(off_option,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(off_option,LV_OBJ_FLAG_HIDDEN);
 animation_tick++;
 int eye_height=animation_tick%10==0?2:7;
 lv_obj_set_height(eye_left,eye_height);lv_obj_set_height(eye_right,eye_height);
 lv_obj_set_height(mouth,voice.recording?7:voice.playing?(animation_tick%2?8:4):3);
 lv_obj_set_pos(body,20,show_avatar?173:81);lv_obj_set_size(body,200,show_avatar?92:181);
 lv_obj_set_y(badge,show_avatar?146:56);
 if(setup.active) {
  lv_label_set_text(title,"PHONE SETUP");lv_label_set_text(badge,"SETUP / 5 MIN");
  lv_label_set_text_fmt(body,"1. Join this Wi-Fi\n%s\nPassword: %s\n\n2. Open on your phone\nhttp://192.168.4.1",setup.ssid,setup.password);
  lv_label_set_text(footer,"Finish setup on your phone");return;
 }
 if(model.page==POCKET_CHAT){
  lv_label_set_text(badge,voice.target!=model.target?"READY TO CHAT":voice.recording?"LISTENING":voice.playing?"SPEAKING":voice.busy?"THINKING":"LET'S TALK");
  if(voice.target!=model.target){lv_label_set_text(body,"Your private companion\nHold OK to start talking.");}
  else lv_label_set_text_fmt(body,"%s\n%.100s\n%.220s",voice.message,voice.text,voice.reply);
  lv_label_set_text(footer,voice.busy?"OK: stop\nUP/DOWN: pages":"Hold OK: talk\nUP/DOWN: pages");return;
 }
 if(model.page==POCKET_STATUS){
  lv_label_set_text(badge,voice.online[model.target]?"HERMES ONLINE":"HERMES POCKET");
  lv_label_set_text_fmt(body,"Wi-Fi: %s\nHermes: %s\nVoice: %s",setup.wifi_connected?"Connected":"Offline",voice.online[model.target]?"Available":"Offline",voice.speech_ready[model.target]?"Ready":"Needs setup");
  lv_label_set_text(footer,"OK: agent  Hold: setup\nUP/DOWN: pages");return;
 }
 if(model.page==POCKET_SCREEN_OFF){
  lv_label_set_text(badge,"SCREEN STANDBY");
  lv_obj_set_size(body,200,132);
  lv_label_set_text(body,"Turn off the display.\nWi-Fi stays on.\n\nAny button wakes it.\nWake press only wakes.");
  lv_label_set_text(footer,"OK: screen off\nUP/DOWN: pages");return;
 }
 lv_label_set_text(badge,"ACCOUNT OVERVIEW");
 lv_label_set_text(body,"Codex        Not configured\n\nClaude       Not configured\n\nOllama       Not configured\n\nUsage connections coming next.");
 lv_label_set_text(footer,"UP/DOWN: change page");
}
static void redraw(void) {
 if(!screen_off && bsp_lvgl_lock(1000)){render();bsp_lvgl_unlock();}
}
static void enter_screen_off(void) {
 for(unsigned i=0;i<BSP_BTN_COUNT;i++)discard_gesture[i]=true;
 screen_off=true;
 /* Reversible screen standby. Do not call the terminal deep-sleep BSP APIs. */
 bsp_display_backlight(0);
}
static bool consume_wake_gesture(input value) {
 if((unsigned)value.button>=BSP_BTN_COUNT)return true;
 const unsigned bit=1u<<(unsigned)value.button;
 if(value.event==BSP_BTN_PRESS)held_buttons|=bit;
 else if(value.event==BSP_BTN_RELEASE)held_buttons&=~bit;
 if(screen_off){
  /* Ignore stale CLICK/RELEASE events queued before standby. */
  if(value.event==BSP_BTN_PRESS){
   screen_off=false;wake_gesture=true;
   redraw();bsp_display_backlight(65);
  }
  return true;
 }
 if(wake_gesture){
  /* A long hold, multiple held keys, and their releases all belong to wake. */
  if(value.event==BSP_BTN_RELEASE && !held_buttons)wake_gesture=false;
  return true;
 }
 if(discard_gesture[value.button]){
  /* SINGLE_CLICK is delayed until after RELEASE. Only a new PRESS re-arms. */
  if(value.event!=BSP_BTN_PRESS)return true;
  discard_gesture[value.button]=false;
 }
 return false;
}
static void on_button(bsp_btn_t button,bsp_btn_ev_t event,void *unused) {
 (void)unused;input value={button,event};
 if(events)xQueueSend(events,&value,0); /* Never block timer callback. */
}
void app_main(void) {
 pocket_init(&model);events=xQueueCreate(16,sizeof(input));
 if(!events)return;
 ESP_ERROR_CHECK(bsp_i2c_init());
 ESP_ERROR_CHECK(bsp_display_init());
 if(!bsp_lvgl_init() || !bsp_lvgl_lock(1000))return;
 lv_obj_t *screen=lv_obj_create(NULL);
 lv_obj_set_style_bg_color(screen,lv_color_hex(0x101C25),0);
 lv_obj_set_style_text_color(screen,lv_color_hex(0xF4F1E9),0);
 lv_obj_set_style_text_font(screen,&lv_font_source_han_sans_sc_16_cjk,0);
 lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
 shape(screen,10,49,220,218,0x1A2C39,16);
 shape(screen,16,44,208,2,0x3C5968,1);
 title=lv_label_create(screen);lv_obj_set_pos(title,18,16);lv_obj_set_size(title,143,20);lv_label_set_long_mode(title,LV_LABEL_LONG_DOT);
 lv_obj_set_style_text_color(title,lv_color_hex(0x8FE3CB),0);
 lv_obj_t *battery_outline=shape(screen,165,21,12,8,0x101C25,1);
 lv_obj_set_style_border_width(battery_outline,1,0);lv_obj_set_style_border_color(battery_outline,lv_color_hex(0xB9CBD6),0);
 shape(screen,177,23,2,4,0xB9CBD6,0);
 battery=lv_label_create(screen);lv_obj_set_pos(battery,182,16);lv_obj_set_size(battery,44,20);
 lv_label_set_long_mode(battery,LV_LABEL_LONG_CLIP);lv_obj_set_style_text_align(battery,LV_TEXT_ALIGN_RIGHT,0);
 create_avatar(screen);
 badge=lv_label_create(screen);lv_obj_set_pos(badge,18,146);lv_obj_set_size(badge,204,20);lv_label_set_long_mode(badge,LV_LABEL_LONG_DOT);
 lv_obj_set_style_text_align(badge,LV_TEXT_ALIGN_CENTER,0);
 lv_obj_set_style_text_color(badge,lv_color_hex(0x8FE3CB),0);
 body=lv_label_create(screen);lv_obj_set_pos(body,20,173);lv_obj_set_width(body,200);lv_obj_set_height(body,92);lv_label_set_long_mode(body,LV_LABEL_LONG_WRAP);
 off_option=shape(screen,30,222,180,34,0x8FE3CB,10);
 lv_obj_t *off_label=lv_label_create(off_option);lv_label_set_text(off_label,"OK: Screen off");lv_obj_center(off_label);
 lv_obj_set_style_text_color(off_label,lv_color_hex(0x101C25),0);
 footer=lv_label_create(screen);lv_obj_set_pos(footer,18,273);lv_obj_set_size(footer,204,40);lv_label_set_long_mode(footer,LV_LABEL_LONG_CLIP);
 /* The 16px CJK font has a 20px line height: reserve two complete lines. */
 lv_obj_set_style_text_line_space(footer,0,0);
 lv_obj_set_style_text_color(footer,lv_color_hex(0xB9CBD6),0);
 render();lv_screen_load(screen);bsp_lvgl_unlock();bsp_display_backlight(65);
 if(pocket_setup_init()!=ESP_OK) { /* Keep offline UI available; never erase NVS. */ }
 if(pocket_voice_init()!=ESP_OK){ /* Offline UI remains available. */ }
 /* Complete blocking startup before callbacks can queue physical gestures. */
 battery_ready=bsp_battery_init()==ESP_OK;refresh_battery();
 if(xTaskCreate(monitor_battery,"pocket_battery",3072,NULL,1,NULL)!=pdPASS){
  /* Never present an indefinitely stale reading when monitoring is unavailable. */
  atomic_store_explicit(&battery_percent,-1,memory_order_relaxed);
 }
 redraw();
 ESP_ERROR_CHECK(bsp_button_init(on_button,NULL));
 input value;
 for(;;) {
  if(xQueueReceive(events,&value,pdMS_TO_TICKS(500))!=pdTRUE){
   /* Recover safely if a full queue lost RELEASE: delayed actions stay blocked. */
   if(wake_gesture && bsp_button_read_mv()>=BSP_BTN_MV_RELEASED_MIN){held_buttons=0;wake_gesture=false;}
   redraw();continue;
  }
  if(consume_wake_gesture(value))continue;
  if(value.button==BSP_BTN_OK && value.event==BSP_BTN_RELEASE)pocket_voice_release();
  if(value.button==BSP_BTN_OK && value.event==BSP_BTN_PRESS && model.page==POCKET_CHAT){pocket_voice_begin(model.target);continue;}
  if(value.event==BSP_BTN_LONG && value.button==BSP_BTN_OK && model.page==POCKET_STATUS){pocket_setup_request();continue;}
  if(value.event!=BSP_BTN_CLICK)continue;
  pocket_setup_state current_setup;pocket_setup_read(&current_setup);
  if(value.button==BSP_BTN_OK && model.page==POCKET_SCREEN_OFF && !current_setup.active){enter_screen_off();continue;}
  if(value.button==BSP_BTN_OK && model.page==POCKET_STATUS){pocket_voice_state current;pocket_voice_read(&current);if(!current.busy)pocket_select(&model,1-model.target);}
  else if(value.button==BSP_BTN_UP)pocket_press(&model,POCKET_UP);
  else if(value.button==BSP_BTN_DOWN)pocket_press(&model,POCKET_DOWN);
  redraw();
 }
}
