/* Hermes Pocket MIT. Private bridge UI and bounded voice client. */
#include "pocket_model.h"
#include "pocket_setup.h"
#include "pocket_voice.h"
#include "pocket_ui.h"
#include "pocket_usage_client.h"
#include "pocket_preferences.h"
#include "pocket_tasks_client.h"
#include "pocket_actions.h"
#include "pocket_reminders.h"
#include "esp_timer.h"
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
#include <time.h>
#include <stdio.h>

typedef struct { bsp_btn_t button; bsp_btn_ev_t event; } input;
static QueueHandle_t events;
static pocket_model model;
static bool battery_ready,screen_off,wake_gesture;
static bool target_save_failed;
static atomic_int battery_percent=ATOMIC_VAR_INIT(-1);
static unsigned held_buttons;
static bool discard_gesture[BSP_BTN_COUNT];
static pocket_voice_state voice;
static pocket_reminders_state reminders;
static char alert_notes[2][96];
static bool alert_pending[2];

static const char *task_phase(const pocket_tasks_snapshot *tasks){
 static const char *names[]={"UNKNOWN","QUEUED","DISPATCHING","QUEUED UPSTREAM","RUNNING","NEEDS APPROVAL","STOPPING","COMPLETED","FAILED","CANCELLED"};
 if(!tasks->count || tasks->selected>=tasks->count)return "UNKNOWN";
 const pocket_task_state phase=tasks->entries[tasks->selected].state;
 return phase>=POCKET_TASK_STATE_UNKNOWN&&phase<=POCKET_TASK_STATE_CANCELLED?names[phase]:"UNKNOWN";
}
static const char *task_status(const pocket_tasks_snapshot *tasks){
 if(tasks->list_status==POCKET_TASKS_READY && !tasks->count)return "No tasks";
 const pocket_tasks_status status=tasks->list_status==POCKET_TASKS_READY?tasks->view_status:tasks->list_status;
 switch(status){
 case POCKET_TASKS_LOADING:return "Loading";case POCKET_TASKS_READY:return "Fetched";
 case POCKET_TASKS_OFFLINE:return "Offline / cached";case POCKET_TASKS_FAILED:return "Failed / cached";
 case POCKET_TASKS_AUTH_REQUIRED:return "Login required";default:return "Not fetched";
 }
}
static void reminder_note(const pocket_reminder_event *event,char *out,size_t capacity){
 static const char *names[]={"Codex","Claude","Ollama"};
 const char *name=event->provider<3?names[event->provider]:"Usage";
 if(event->kind==POCKET_REMINDER_RESET_REFRESH)snprintf(out,capacity,"Last: %s reset time reached.\nRefresh needed.",name);
 else if(event->unit==POCKET_USAGE_USD_CREDIT)snprintf(out,capacity,"Last: %s %s\n$%.2f USD; floor $%.2f USD",name,event->category==POCKET_USAGE_PURCHASED_CREDIT?"purchased":"included",event->value,event->threshold);
 else snprintf(out,capacity,"Last: %s %.1f%% %s\nLimit %.1f%% %s",name,event->value,event->unit==POCKET_USAGE_PERCENT_USED?"used":"left",event->threshold,event->unit==POCKET_USAGE_PERCENT_USED?"used":"left");
}

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
 pocket_voice_read(&voice);
 static pocket_usage_snapshot usage;static pocket_reminder_event event;
 const int64_t now=(int64_t)time(NULL);
 const pocket_reminder_config reminder_config={.percent_used_threshold=model.alert_threshold,.usd_floor_enabled=model.usd_floor_enabled,.usd_floor=1};
 /* Other target first, selected target last: one reused snapshot also supplies
  * the Usage view. Only one safe note per target is retained. */
 for(unsigned step=0;step<2;step++){
  const unsigned target=(model.target+1+step)%2;pocket_usage_read(target,&usage);
  if(pocket_reminders_evaluate(&reminders,&usage,now,target,&reminder_config,&event,1)){
   reminder_note(&event,alert_notes[target],sizeof alert_notes[target]);alert_pending[target]=true;
  }
 }
 if(!setup.active && model.page==POCKET_ALERTS)alert_pending[model.target]=false;
 bool not_configured=true;for(unsigned p=0;p<3;p++)if(usage.providers[p].availability!=POCKET_USAGE_NOT_CONFIGURED)not_configured=false;
 pocket_actions_view(setup.active?POCKET_PAGE_COUNT:model.page,model.target);
 static pocket_tasks_snapshot tasks;
 if(!setup.active && model.page==POCKET_TASKS)pocket_tasks_read(model.target,&tasks);
 const pocket_task_state phase=tasks.count&&tasks.selected<tasks.count?tasks.entries[tasks.selected].state:POCKET_TASK_STATE_UNKNOWN;
 const bool selected_task=tasks.count&&tasks.selected<tasks.count;
 if(!model.audio_edit)model.volume=voice.volume;
 const pocket_ui_view view={
  .page=model.page,.target=model.target,.usage=&usage,.usage_window=model.usage_window,.now=now,
  .tool=model.tool,.volume=voice.volume,.audio_edit=model.audio_edit,.alert_threshold=model.alert_threshold,.usd_floor_enabled=model.usd_floor_enabled,
  .alert_note=alert_notes[model.target][0]?alert_notes[model.target]:not_configured?"No configured Usage feeds.":"No reminder yet.",
  .alert_pending=alert_pending[model.target],
  .task_count=tasks.count,.task_selected=tasks.selected,.task_phase=task_phase(&tasks),.task_status=tasks.count&&voice.busy?"Paused / cached":task_status(&tasks),.task_result=tasks.result,
  .task_approval=selected_task&&phase==POCKET_TASK_STATE_AWAITING_APPROVAL,.task_unknown=selected_task&&phase==POCKET_TASK_STATE_UNKNOWN,.task_has_result=tasks.has_result,.task_truncated=tasks.truncated,
  .uptime_ms=esp_timer_get_time()/1000,.diagnostics=&voice.diagnostics[model.target],.target_save_failed=target_save_failed,
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
static void handle_input(input value){
 if(consume_wake_gesture(value))return;
 /* RELEASE is cleanup for an existing hold, including while setup is visible. */
 if(value.button==BSP_BTN_OK && value.event==BSP_BTN_RELEASE)pocket_voice_release();
 pocket_setup_state setup;pocket_setup_read(&setup);if(setup.active)return;
 pocket_voice_read(&voice);
 if(value.button==BSP_BTN_UP && value.event==BSP_BTN_PRESS && voice.recording){
  pocket_voice_cancel();discard_gesture[BSP_BTN_UP]=discard_gesture[BSP_BTN_OK]=true;redraw();return;
 }
 if(value.button==BSP_BTN_OK && value.event==BSP_BTN_PRESS && voice.busy){
  pocket_voice_cancel();discard_gesture[BSP_BTN_OK]=true;redraw();return;
 }
 if(value.button==BSP_BTN_OK && value.event==BSP_BTN_PRESS && model.page==POCKET_CHAT){pocket_voice_begin(model.target);return;}
 if(value.event==BSP_BTN_LONG && value.button==BSP_BTN_OK){
  if(model.page==POCKET_STATUS)pocket_setup_request();
  else if(model.page==POCKET_TASKS)pocket_tasks_refresh(model.target);
  else if(model.page==POCKET_TOOLS)(void)pocket_actions_request(model.target,model.tool);
  else if(model.page==POCKET_ALERTS)model.usd_floor_enabled=!model.usd_floor_enabled;
  else return;
  discard_gesture[BSP_BTN_OK]=true;redraw();return;
 }
 if(value.event!=BSP_BTN_CLICK)return;
 if(value.button==BSP_BTN_OK && model.page==POCKET_SCREEN_OFF){enter_screen_off();return;}
 if(value.button==BSP_BTN_OK && model.page==POCKET_STATUS){
  if(pocket_select_from_home(&model,1-model.target,false,voice.busy))target_save_failed=pocket_preferences_save_target(model.target)!=ESP_OK;
 }else if(value.button==BSP_BTN_OK && model.page==POCKET_TASKS)pocket_tasks_next(model.target);
 else if(value.button==BSP_BTN_OK){
  if(model.page==POCKET_AUDIO && !model.audio_edit)model.volume=voice.volume;
  pocket_press(&model,POCKET_OK);
 }else if(value.button==BSP_BTN_UP || value.button==BSP_BTN_DOWN){
  const unsigned before=model.volume;
  pocket_press(&model,value.button==BSP_BTN_UP?POCKET_UP:POCKET_DOWN);
  if(model.volume!=before)pocket_voice_set_volume(model.volume);
 }
 redraw();
}
void app_main(void) {
 pocket_init(&model);pocket_actions_init();events=xQueueCreate(16,sizeof(input));
 if(!events)return;
 ESP_ERROR_CHECK(bsp_i2c_init());
 ESP_ERROR_CHECK(bsp_display_init());
 if(!bsp_lvgl_init() || !bsp_lvgl_lock(1000))return;
 pocket_ui_create();
 render();bsp_lvgl_unlock();bsp_display_backlight(65);
 if(pocket_setup_init()!=ESP_OK) { /* Keep offline UI available; never erase NVS. */ }
 pocket_select(&model,pocket_preferences_load_target());
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
  handle_input(value);
 }
}
