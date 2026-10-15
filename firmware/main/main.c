/* Hermes Pocket MIT. Private bridge UI and bounded voice client. */
#include "pocket_model.h"
#include "pocket_setup.h"
#include "pocket_voice.h"
#include "pocket_ui.h"
#include "bsp_i2c.h"
#include "bsp_display.h"
#include "bsp_button.h"
#include "bsp_battery.h"
#include "bsp_pins.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_err.h"
#include "esp_log.h"
#include <stdatomic.h>

typedef struct { bsp_btn_t button; bsp_btn_ev_t event; } input;
static QueueHandle_t events;
static pocket_model model;
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
static void render(void) {
 pocket_setup_state setup;pocket_setup_read(&setup);
 static pocket_voice_state voice;pocket_voice_read(&voice);
 const pocket_ui_view view={
  .page=model.page,.target=model.target,
  .battery_percent=atomic_load_explicit(&battery_percent,memory_order_relaxed),
  .setup_active=setup.active,.wifi_connected=setup.wifi_connected,
  .online=voice.online[model.target],.speech_ready=voice.speech_ready[model.target],
  .voice_here=voice.target==model.target,.recording=voice.recording,.playing=voice.playing,.busy=voice.busy,
  .ssid=setup.ssid,.password=setup.password,.message=voice.message,.text=voice.text,.reply=voice.reply};
 /* Log the exact selected view only when its connection indicators change. */
 static unsigned previous=~0u;
 const unsigned connection=(view.target<<3)|((unsigned)view.wifi_connected<<2)|((unsigned)view.online<<1)|(unsigned)view.speech_ready;
 if(connection!=previous){
  previous=connection;
  ESP_LOGI("pocket_view","target=%u wifi=%u online=%u speech_ready=%u",view.target,
           (unsigned)view.wifi_connected,(unsigned)view.online,(unsigned)view.speech_ready);
 }
 pocket_ui_render(&view);
}
static void redraw(void) {
 if(!screen_off && bsp_lvgl_lock(1000)){render();bsp_lvgl_unlock();}
}
static void set_awake(bool awake) {
 if(bsp_lvgl_lock(1000)){pocket_ui_set_awake(awake);bsp_lvgl_unlock();}
}
static void enter_screen_off(void) {
 for(unsigned i=0;i<BSP_BTN_COUNT;i++)discard_gesture[i]=true;
 screen_off=true;set_awake(false);
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
   set_awake(true);redraw();bsp_display_backlight(65);
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
 pocket_ui_create();
 render();bsp_lvgl_unlock();bsp_display_backlight(65);
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
