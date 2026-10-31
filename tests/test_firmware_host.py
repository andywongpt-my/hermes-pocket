import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ACTIONS_FIXTURE = r'''
#include "pocket_reader.h"
#include "pocket_actions.h"
#include "pocket_tasks_client.h"
#include "pocket_models_client.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static pocket_tasks_snapshot snapshots[2];
static unsigned refreshes,opens,gets,forces,disconnects;
static unsigned model_refreshes,model_gets,model_disconnects;
static bool pending_models[2];
static int64_t poll_clock_ms=1000;
int64_t esp_timer_get_time(void){return poll_clock_ms*1000;}
static char wire[8192];
static bool active_reader;
static unsigned reader_gets;
bool pocket_reader_active(void){return active_reader;}
void pocket_reader_read(pocket_reader_snapshot *out,char *text,size_t cap){(void)text;(void)cap;memset(out,0,sizeof *out);out->active=active_reader;out->target=1;}
bool pocket_reader_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){assert(active_reader&&target==1&&link&&response==wire&&capacity==8192);reader_gets++;return true;}
static bool pending_list[2],pending_view[2];
void pocket_usage_force(unsigned target){assert(target<2);forces++;}
void pocket_tasks_refresh(unsigned target){assert(target<2);refreshes++;pending_list[target]=true;pending_view[target]=false;snapshots[target].list_status=POCKET_TASKS_LOADING;}
void pocket_tasks_open(unsigned target){assert(target<2);opens++;pending_view[target]=true;snapshots[target].view_status=POCKET_TASKS_LOADING;}
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out){assert(target<2);*out=snapshots[target];}
bool pocket_tasks_active(void){for(unsigned t=0;t<2;t++)for(unsigned i=0;i<snapshots[t].count;i++)if(snapshots[t].entries[i].state<=POCKET_TASK_STATE_STOPPING)return true;return false;}
void pocket_tasks_disconnected(unsigned target){assert(target<2);disconnects++;pending_list[target]=pending_view[target]=false;snapshots[target].list_status=POCKET_TASKS_OFFLINE;}
void pocket_models_refresh(unsigned target){assert(target<2);model_refreshes++;pending_models[target]=true;}
void pocket_models_disconnected(unsigned target){assert(target<2);model_disconnects++;pending_models[target]=false;}
bool pocket_models_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 assert(target<2&&link&&response==wire&&capacity==8192);
 if(!pending_models[target]){return false;}
 pending_models[target]=false;model_gets++;return true;
}
bool pocket_tasks_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity){
 assert(target<2&&link&&response==wire&&capacity==8192);
 if(pending_list[target]){pending_list[target]=false;pending_view[target]=true;gets++;snapshots[target].count=1;snapshots[target].list_status=POCKET_TASKS_READY;snapshots[target].view_status=POCKET_TASKS_LOADING;return true;}
 if(pending_view[target]){pending_view[target]=false;gets++;snapshots[target].view_status=POCKET_TASKS_READY;return true;}
 return false;
}
int main(void){
 pocket_link_config link={0};pocket_actions_init();
 assert(pocket_actions_poll_target(1)==1);
 assert(!pocket_actions_request(0,POCKET_TOOL_TODO_UNAVAILABLE));assert(!gets&&!forces&&!refreshes);
 assert(pocket_actions_request(1,POCKET_TOOL_STATUS));assert(pocket_actions_poll_target(0)==1);assert(pocket_actions_poll_target(0)==0);
 assert(pocket_actions_request(0,POCKET_TOOL_USAGE)&&forces==1);assert(pocket_actions_poll_target(1)==0);
 pocket_actions_view(POCKET_TASKS,0);assert(refreshes==1&&!gets);
 pocket_actions_view(POCKET_TASKS,0);assert(refreshes==1);
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==1&&opens==0);
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==2&&opens==0);
 snapshots[0].entries[0].state=POCKET_TASK_STATE_RUNNING;
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==3&&opens==1);
 snapshots[0].entries[0].state=POCKET_TASK_STATE_COMPLETED;
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==3&&opens==1);
 snapshots[0].entries[0].state=POCKET_TASK_STATE_UNKNOWN;
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==3);
 snapshots[0].entries[0].state=POCKET_TASK_STATE_AWAITING_APPROVAL;
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==4&&opens==2);
 pocket_actions_view(POCKET_PAGE_COUNT,0);pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==4);
 pocket_actions_view(POCKET_TASKS,1);assert(refreshes==2);assert(pocket_actions_poll_target(0)==1);
 pocket_actions_idle(1,&link,wire,sizeof wire);assert(gets==5);
 pocket_actions_disconnected(1);assert(disconnects==1);
 pocket_actions_idle(1,&link,wire,sizeof wire);assert(refreshes==3&&gets==6);
 assert(!pocket_actions_request(2,POCKET_TOOL_STATUS));assert(!pocket_actions_request(0,(pocket_tool)-1));pocket_actions_idle(2,&link,wire,sizeof wire);pocket_actions_disconnected(2);
 assert(gets==6&&disconnects==1);
 pocket_actions_view(POCKET_MODELS,1);pocket_actions_view(POCKET_MODELS,1);
 assert(model_refreshes==1 && pocket_actions_poll_target(0)==1);
 pocket_actions_idle(1,&link,wire,sizeof wire);assert(model_gets==1 && gets==6 && model_disconnects==1);
 pocket_actions_screen_off(true);assert(pocket_actions_poll_ms()==5000);
 for(unsigned t=0;t<2;t++)snapshots[t].entries[0].state=POCKET_TASK_STATE_COMPLETED;
 assert(pocket_actions_poll_ms()==15000);unsigned before=gets;
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==before+1);
 unsigned background_refreshes=refreshes;
 assert(snapshots[0].view_status==POCKET_TASKS_LOADING);
 pocket_actions_idle(0,&link,wire,sizeof wire);
 assert(gets==before+2 && refreshes==background_refreshes && snapshots[0].view_status==POCKET_TASKS_READY);
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==before+2 && refreshes==background_refreshes);

 poll_clock_ms+=120000;pocket_actions_idle(0,&link,wire,sizeof wire);
 assert(gets==before+3 && refreshes==background_refreshes+1);
 pocket_actions_idle(0,&link,wire,sizeof wire);assert(gets==before+4);
 /* Actual status/Usage gates retain monotonic age while checks are skipped. */
 pocket_actions_view(POCKET_STATUS,1);
 assert(pocket_actions_poll_due(0)&&pocket_actions_poll_due(1));
 assert(!pocket_actions_poll_due(0)&&!pocket_actions_poll_due(1));
 poll_clock_ms+=29999;assert(!pocket_actions_poll_due(1));
 poll_clock_ms++;assert(pocket_actions_poll_due(1)&&!pocket_actions_poll_due(0));
 poll_clock_ms+=89999;assert(!pocket_actions_poll_due(0));
 poll_clock_ms++;assert(pocket_actions_poll_due(0));
 assert(pocket_actions_usage_due(0));poll_clock_ms+=299999;assert(!pocket_actions_usage_due(0));
 poll_clock_ms++;assert(pocket_actions_usage_due(0));
 assert(!pocket_actions_poll_due(2)&&!pocket_actions_usage_due(2));
 poll_clock_ms=1;assert(pocket_actions_poll_due(0)&&pocket_actions_usage_due(0));
 snapshots[1].entries[0].state=POCKET_TASK_STATE_RUNNING;assert(pocket_actions_poll_ms()==5000);
 assert(pocket_actions_poll_due(0)&&pocket_actions_poll_due(0));
 assert(pocket_actions_usage_due(0)&&pocket_actions_usage_due(0));
 snapshots[1].entries[0].state=POCKET_TASK_STATE_UNKNOWN;assert(pocket_actions_poll_ms()==5000);
 snapshots[1].entries[0].state=POCKET_TASK_STATE_COMPLETED;pocket_actions_screen_off(false);assert(pocket_actions_poll_ms()==5000);
 assert(pocket_actions_poll_target(0)==1 && pocket_actions_poll_due(1) && pocket_actions_usage_due(0));
 active_reader=true;unsigned old_gets=gets,old_models=model_gets;
 assert(pocket_actions_poll_target(0)==1);pocket_actions_idle(1,&link,wire,sizeof wire);
 assert(reader_gets==1&&gets==old_gets&&model_gets==old_models);
 active_reader=false;
 puts("pocket actions: PASS");return 0;
}
'''

INPUT_FIXTURE = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "pocket_models_client.h"
#include "pocket_reader.h"
#include "main.c"
#include <setjmp.h>
static jmp_buf idle_done;
static bool simulate_idle,hardware_screen_off;
static bool panel_asleep,tick_asleep;
static unsigned display_fail,display_count,display_steps[1024];
static void display_step(unsigned step){assert(display_count<1024);display_steps[display_count++]=step;}

static int64_t clock_ms=1000;
static pocket_setup_state hardware_setup;
static pocket_voice_state hardware_voice;
static pocket_usage_snapshot hardware_usage[2];
static pocket_tasks_snapshot hardware_tasks[2];
static pocket_models_snapshot hardware_models[2];
static unsigned last_model;
static pocket_reader_snapshot reader_fake;
static unsigned reader_opens,reader_closes,reader_turns,reader_steps;
static bool reader_fetch;
static pocket_ui_view rendered;
static unsigned begins,cancels,volume_sets,refreshes,nexts,tool_runs,setups,saves,renders;
static time_t wall_seconds=1800000000;
void test_log(const char *tag,const char *format,...){(void)tag;(void)format;}
time_t test_time(time_t *out){if(out)*out=wall_seconds;return wall_seconds;}
int64_t esp_timer_get_time(void){return clock_ms*1000;}
esp_err_t bsp_i2c_init(void){return ESP_OK;}
esp_err_t bsp_display_init(void){return ESP_OK;}
void *bsp_lvgl_init(void){return (void*)1;}
bool bsp_lvgl_lock(int timeout){(void)timeout;return simulate_idle||reader_fake.active||screen_off;}
void bsp_lvgl_unlock(void){}
void bsp_display_backlight(unsigned value){display_step(value?10:1);}
esp_err_t bsp_display_set_sleep(bool asleep){display_step(asleep?4:5);if(display_fail==(asleep?4u:5u)){display_fail=0;return ESP_FAIL;}panel_asleep=asleep;return ESP_OK;}
esp_err_t bsp_lvgl_set_sleep(bool asleep){display_step(asleep?3:6);if(display_fail==(asleep?3u:6u)){display_fail=0;return ESP_FAIL;}tick_asleep=asleep;return ESP_OK;}
esp_err_t bsp_lvgl_repaint(void){display_step(9);if(display_fail==9){display_fail=0;return ESP_FAIL;}return ESP_OK;}

esp_err_t bsp_battery_init(void){return ESP_OK;}
int bsp_battery_soc(void){return 76;}
esp_err_t bsp_button_init(bsp_btn_cb_t callback,void *context){(void)callback;(void)context;return ESP_OK;}
int bsp_button_read_mv(void){return 3300;}
QueueHandle_t xQueueCreate(unsigned count,unsigned item){(void)count;(void)item;return (void*)1;}
BaseType_t xQueueSend(QueueHandle_t queue,const void *value,TickType_t timeout){(void)queue;(void)value;(void)timeout;return pdTRUE;}
BaseType_t xQueueReceive(QueueHandle_t queue,void *value,TickType_t timeout){(void)queue;(void)value;if(simulate_idle){clock_ms+=timeout;if(clock_ms>=92000)longjmp(idle_done,1);}return pdFALSE;}
BaseType_t xTaskCreate(void (*entry)(void*),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *handle){(void)entry;(void)name;(void)stack;(void)arg;(void)priority;(void)handle;return pdPASS;}
void vTaskDelay(TickType_t ticks){(void)ticks;}
void pocket_setup_read(pocket_setup_state *out){*out=hardware_setup;}
esp_err_t pocket_setup_init(void){return ESP_OK;}
void pocket_setup_request(void){setups++;}
void pocket_voice_read(pocket_voice_state *out){*out=hardware_voice;}
esp_err_t pocket_voice_init(void){return ESP_OK;}
void pocket_voice_begin(unsigned target){assert(target<2);begins++;hardware_voice.busy=hardware_voice.recording=true;}
void pocket_voice_begin_model(unsigned target,unsigned choice){last_model=choice;pocket_voice_begin(target);}
void pocket_voice_cancel(void){cancels++;hardware_voice.busy=hardware_voice.recording=hardware_voice.playing=false;}
void pocket_voice_release(void){hardware_voice.recording=false;}
void pocket_voice_set_volume(unsigned value){hardware_voice.volume=value>100?100:value;volume_sets++;}
void pocket_usage_read(unsigned target,pocket_usage_snapshot *out){assert(target<2);*out=hardware_usage[target];}
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out){assert(target<2);*out=hardware_tasks[target];}
void pocket_tasks_refresh(unsigned target){assert(target<2);refreshes++;}
void pocket_tasks_next(unsigned target){assert(target<2);nexts++;}
void pocket_models_read(unsigned target,pocket_models_snapshot *out){assert(target<2);*out=hardware_models[target];}
void pocket_actions_init(void){}
void pocket_actions_screen_off(bool off){hardware_screen_off=off;}
void pocket_actions_view(pocket_page page,unsigned target){(void)page;assert(target<2);}
bool pocket_actions_request(unsigned target,pocket_tool tool){assert(target<2);if(tool==POCKET_TOOL_TODO_UNAVAILABLE)return false;tool_runs++;return true;}
unsigned pocket_preferences_load_target(void){return 0;}
esp_err_t pocket_preferences_save_target(unsigned target){assert(target<2);saves++;return ESP_OK;}
void pocket_ui_create(void){}
void pocket_ui_render(const pocket_ui_view *view){rendered=*view;renders++;display_step(8);}
void pocket_ui_set_awake(bool awake){display_step(awake?7:2);}
bool pocket_ui_reader_step(int direction){assert(direction==-1||direction==1);reader_steps++;return reader_fetch;}
bool pocket_reader_active(void){return reader_fake.active;}
bool pocket_reader_open(unsigned target){assert(target<2);reader_opens++;reader_fake=(pocket_reader_snapshot){.active=true,.has_page=true,.target=target,.status=POCKET_READER_READY,.page_count=2,.utf8_bytes=4};return true;}
void pocket_reader_close(void){reader_closes++;reader_fake.active=false;}
bool pocket_reader_turn(int direction){assert(direction==-1||direction==1);reader_turns++;return true;}
void pocket_reader_read(pocket_reader_snapshot *out,char *text,size_t capacity){*out=reader_fake;if(capacity>=5)memcpy(text,"safe",5);}

static void reset(void){
 pocket_clock_reset();wall_seconds=1800000000;renders=0;display_count=display_fail=0;panel_asleep=tick_asleep=false;
 simulate_idle=hardware_screen_off=false;clock_ms=1000;memset(&reader_fake,0,sizeof reader_fake);reader_opens=reader_closes=reader_turns=reader_steps=0;reader_fetch=false;
 pocket_init(&model);memset(&hardware_setup,0,sizeof hardware_setup);memset(&hardware_voice,0,sizeof hardware_voice);hardware_voice.volume=60;
 memset(discard_gesture,0,sizeof discard_gesture);held_buttons=0;screen_off=wake_gesture=false;
 memset(&reminders,0,sizeof reminders);memset(alert_notes,0,sizeof alert_notes);memset(&rendered,0,sizeof rendered);
 memset(alert_pending,0,sizeof alert_pending);
 memset(hardware_usage,0,sizeof hardware_usage);memset(hardware_tasks,0,sizeof hardware_tasks);last_model=0;
 for(unsigned t=0;t<2;t++)hardware_models[t]=(pocket_models_snapshot){.status=POCKET_MODELS_READY,.available_mask=7,.verified_mask=3};
 for(unsigned t=0;t<2;t++)for(unsigned p=0;p<3;p++)hardware_usage[t].providers[p].availability=POCKET_USAGE_NOT_CONFIGURED;
 begins=cancels=volume_sets=refreshes=nexts=tool_runs=setups=saves=0;
}
static void event(bsp_btn_t button,bsp_btn_ev_t kind){handle_input((input){button,kind});}
static void click(bsp_btn_t button){event(button,BSP_BTN_PRESS);event(button,BSP_BTN_RELEASE);event(button,BSP_BTN_CLICK);}
static void fresh_usage(unsigned target){
 pocket_usage_provider *provider=&hardware_usage[target].providers[0];provider->availability=POCKET_USAGE_AVAILABLE;provider->metric_count=1;
 provider->metrics[0]=(pocket_usage_metric){.has_value=true,.value=85,.unit=POCKET_USAGE_PERCENT_USED,.category=POCKET_USAGE_SUBSCRIPTION,
  .availability=POCKET_USAGE_AVAILABLE,.window_minutes=300,.has_fetched_at=true,.fetched_at=1799999990,.has_expires_at=true,.expires_at=1800000060};
}
int main(void){
 /* Real main.c must sleep the panel/tick, then redraw+drain before lighting.
    First OK wake and failed/retried wakes must never start a recording. */
 reset();model.page=POCKET_CHAT;enter_screen_off();
 assert(screen_off && hardware_screen_off && panel_asleep && tick_asleep);
 assert(display_count==4 && display_steps[0]==1 && display_steps[1]==2 && display_steps[2]==3 && display_steps[3]==4);
 unsigned start=display_count;event(BSP_BTN_OK,BSP_BTN_PRESS);
 assert(!screen_off && !panel_asleep && !tick_asleep && !begins);
 const unsigned wake_steps[]={5,6,7,8,9,10};
 assert(display_count==start+6);for(unsigned i=0;i<6;i++)assert(display_steps[start+i]==wake_steps[i]);
 event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(!begins);
 for(unsigned failure=5;failure<=9;failure+=(failure==5?1:3)){
  reset();model.page=POCKET_CHAT;enter_screen_off();display_fail=failure;click(BSP_BTN_OK);
  assert(screen_off && hardware_screen_off && !begins && !wake_gesture);
  click(BSP_BTN_OK);assert(!screen_off && !hardware_screen_off && !panel_asleep && !tick_asleep && !begins);
 }
 reset();display_fail=4;enter_screen_off();assert(screen_off && hardware_screen_off);
 click(BSP_BTN_OK);assert(!screen_off && !hardware_screen_off && !panel_asleep && !tick_asleep && !begins);
 reset();simulate_idle=true;int idle_exit=setjmp(idle_done);if(!idle_exit)app_main();
 assert(screen_off && hardware_screen_off);click(BSP_BTN_OK);assert(!screen_off && !hardware_screen_off && !begins);
 for(unsigned blocked=0;blocked<3;blocked++){
  reset();simulate_idle=true;hardware_voice.busy=blocked==0;hardware_setup.active=blocked==1;held_buttons=blocked==2?1u:0u;
  int blocked_exit=setjmp(idle_done);if(!blocked_exit)app_main();assert(!screen_off && !hardware_screen_off);
 }
 reset();simulate_idle=true;clock_ms=80000;click(BSP_BTN_UP);clock_ms=169999;check_screen_idle();assert(!screen_off);
 clock_ms=170000;check_screen_idle();assert(screen_off);click(BSP_BTN_OK);assert(!screen_off && !begins);
 reset();model.page=POCKET_MODELS;click(BSP_BTN_OK);
 assert(model.model_candidate==POCKET_MODEL_OPUS && model.model_choice[0]==POCKET_MODEL_DEFAULT);
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(model.model_choice[0]==POCKET_MODEL_OPUS && model.model_candidate==POCKET_MODEL_OPUS);
 click(BSP_BTN_OK);assert(model.model_candidate==POCKET_MODEL_GLM_FLASH);
 click(BSP_BTN_UP);click(BSP_BTN_DOWN);assert(model.page==POCKET_MODELS && model.model_candidate==POCKET_MODEL_OPUS);
 hardware_models[0].available_mask=1;click(BSP_BTN_OK);
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(model.model_choice[0]==POCKET_MODEL_OPUS);
 model.page=POCKET_CHAT;event(BSP_BTN_OK,BSP_BTN_PRESS);assert(begins==1 && last_model==POCKET_MODEL_OPUS);
 reset();click(BSP_BTN_DOWN);assert(model.page==POCKET_CHAT);click(BSP_BTN_DOWN);assert(model.page==POCKET_USAGE);
 reset();model.page=POCKET_CHAT;hardware_voice.busy=hardware_voice.recording=true;
 event(BSP_BTN_UP,BSP_BTN_PRESS);assert(cancels==1&&model.page==POCKET_CHAT);
 event(BSP_BTN_UP,BSP_BTN_RELEASE);event(BSP_BTN_UP,BSP_BTN_CLICK);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(model.page==POCKET_CHAT&&!begins);event(BSP_BTN_OK,BSP_BTN_PRESS);assert(begins==1);
 for(unsigned page=0;page<POCKET_PAGE_COUNT;page++){
  reset();model.page=(pocket_page)page;hardware_voice.busy=hardware_voice.playing=true;
  event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
  assert(cancels==1&&model.page==(pocket_page)page&&!screen_off&&!nexts&&!tool_runs&&!setups&&!saves&&!model.audio_edit);
 }
 for(unsigned page=0;page<POCKET_PAGE_COUNT;page++){
  reset();model.page=(pocket_page)page;hardware_setup.active=true;
  click(BSP_BTN_UP);event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
  assert(model.page==(pocket_page)page&&!begins&&!cancels&&!volume_sets&&!refreshes&&!nexts&&!tool_runs&&!setups&&!saves&&!model.usd_floor_enabled);
 }
 reset();model.page=POCKET_TASKS;enter_screen_off();
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(!screen_off&&!wake_gesture&&!refreshes&&!nexts);click(BSP_BTN_OK);assert(nexts==1);
 reset();model.page=POCKET_TASKS;event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(refreshes==1&&!nexts);
 reset();model.page=POCKET_TOOLS;model.tool=POCKET_TOOL_USAGE;event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(tool_runs==1&&model.tool==POCKET_TOOL_USAGE);
 reset();model.page=POCKET_TOOLS;model.tool=POCKET_TOOL_READER;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(reader_opens==1&&reader_fake.active&&!tool_runs&&!begins&&model.tool==POCKET_TOOL_READER);
 click(BSP_BTN_DOWN);assert(reader_steps==1&&!reader_turns&&model.page==POCKET_TOOLS&&!begins);
 reader_fetch=true;click(BSP_BTN_UP);assert(reader_steps==2&&reader_turns==1&&model.page==POCKET_TOOLS);
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(!reader_fake.active&&reader_closes&&model.tool==POCKET_TOOL_READER&&!begins);
 model.page=POCKET_CHAT;event(BSP_BTN_OK,BSP_BTN_PRESS);assert(begins==1&&!reader_fake.active);
 reset();model.page=POCKET_AUDIO;click(BSP_BTN_OK);click(BSP_BTN_DOWN);assert(model.audio_edit&&model.volume==55&&hardware_voice.volume==55&&volume_sets==1);
 click(BSP_BTN_UP);assert(model.volume==60&&volume_sets==2);click(BSP_BTN_OK);click(BSP_BTN_DOWN);assert(model.page==POCKET_ALERTS);
 click(BSP_BTN_OK);assert(model.alert_threshold==90);event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(model.usd_floor_enabled&&model.alert_threshold==90);
 reset();click(BSP_BTN_OK);assert(model.target==1&&saves==1);
 /* Render uses actual task-client data: an empty or invalid selection is not
  * an unknown task, and cached work is visibly paused during voice activity. */
 reset();model.page=POCKET_TASKS;hardware_tasks[0].list_status=POCKET_TASKS_READY;render();
 assert(!rendered.task_count&&!rendered.task_unknown&&!rendered.task_approval&&!strcmp(rendered.task_status,"No tasks"));
 hardware_tasks[0].count=1;render();assert(rendered.task_unknown);
 hardware_tasks[0].entries[0].state=POCKET_TASK_STATE_AWAITING_APPROVAL;render();assert(rendered.task_approval&&!rendered.task_unknown);
 hardware_tasks[0].selected=1;render();assert(!rendered.task_unknown&&!rendered.task_approval);
 hardware_tasks[0].selected=0;hardware_tasks[0].view_status=POCKET_TASKS_READY;hardware_voice.busy=true;render();assert(!strcmp(rendered.task_status,"Paused / cached"));
 /* Only real fresh reminders set a pending indicator, independently per
  * target. Opening Alerts acknowledges it while the last safe note remains. */
 reset();render();assert(!rendered.alert_pending&&!strcmp(rendered.alert_note,"Account quota not configured."));
 fresh_usage(0);hardware_usage[0].providers[0].metrics[0].expires_at=1800000000;render();assert(!rendered.alert_pending);
 hardware_usage[0].providers[0].metrics[0].expires_at=1800000060;render();assert(rendered.alert_pending&&strstr(rendered.alert_note,"85.0% used"));
 model.page=POCKET_USAGE;render();assert(rendered.alert_pending);
 model.target=1;render();assert(!rendered.alert_pending);fresh_usage(1);render();assert(rendered.alert_pending);
 model.target=0;model.page=POCKET_AUDIO;click(BSP_BTN_DOWN);assert(model.page==POCKET_ALERTS);
 hardware_setup.active=true;render();hardware_setup.active=false;model.page=POCKET_STATUS;render();assert(rendered.alert_pending);
 model.page=POCKET_ALERTS;render();assert(!rendered.alert_pending&&strstr(rendered.alert_note,"85.0% used"));
 model.page=POCKET_STATUS;render();assert(!rendered.alert_pending);model.target=1;render();assert(rendered.alert_pending);
 /* Unsynced boot waits even with a plausible RTC value. Once notified,
    the same system clock keeps ticking offline. Standby never renders it. */
 reset();render();assert(!rendered.clock_ready);
 pocket_clock_note_sync(wall_seconds);render();assert(rendered.clock_ready&&rendered.now==wall_seconds);
 hardware_setup.wifi_connected=false;wall_seconds+=60;render();assert(rendered.clock_ready&&rendered.now==wall_seconds&&!rendered.wifi_connected);
 simulate_idle=true;enter_screen_off();unsigned before=renders;wall_seconds+=3600;redraw();assert(renders==before);
 screen_off=false;redraw();assert(renders==before+1&&rendered.now==wall_seconds&&rendered.clock_ready);
 puts("pocket primitive input: PASS");return 0;
}
'''

class FirmwareHostTests(unittest.TestCase):
    def test_primitive_input_routing(self):
        from test_firmware_voice import HEADERS
        compiler=os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:self.skipTest('host C compiler unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        headers={name:HEADERS[name] for name in ['esp_err.h','time.h','esp_log.h','esp_timer.h','freertos/FreeRTOS.h','freertos/task.h','freertos/queue.h']}
        headers['esp_err.h']+='\n#define ESP_ERROR_CHECK(value) ((void)(value))\n#define ESP_ERR_TIMEOUT 0x107\n'
        headers['esp_log.h']+='\n#define ESP_LOGW ESP_LOGI\n'
        headers.update({
            'bsp_i2c.h':'#pragma once\n#include "esp_err.h"\nesp_err_t bsp_i2c_init(void);\n',
            'bsp_display.h':'#pragma once\n#include <stdbool.h>\n#include "esp_err.h"\nesp_err_t bsp_display_init(void);\nvoid *bsp_lvgl_init(void);\nbool bsp_lvgl_lock(int);\nvoid bsp_lvgl_unlock(void);\nvoid bsp_display_backlight(unsigned);\nesp_err_t bsp_display_set_sleep(bool);\nesp_err_t bsp_lvgl_set_sleep(bool);\nesp_err_t bsp_lvgl_repaint(void);\n',
            'bsp_battery.h':'#pragma once\n#include "esp_err.h"\nesp_err_t bsp_battery_init(void);\nint bsp_battery_soc(void);\n',
            'bsp_pins.h':'#pragma once\n#define BSP_BTN_COUNT 3\n#define BSP_BTN_MV_RELEASED_MIN 3000\n',
            'bsp_button.h':'#pragma once\n#include "esp_err.h"\ntypedef enum {BSP_BTN_UP,BSP_BTN_DOWN,BSP_BTN_OK} bsp_btn_t;\ntypedef enum {BSP_BTN_PRESS,BSP_BTN_CLICK,BSP_BTN_DOUBLE,BSP_BTN_LONG,BSP_BTN_RELEASE} bsp_btn_ev_t;\ntypedef void (*bsp_btn_cb_t)(bsp_btn_t,bsp_btn_ev_t,void*);\nesp_err_t bsp_button_init(bsp_btn_cb_t,void*);\nint bsp_button_read_mv(void);\n',
        })
        with tempfile.TemporaryDirectory() as directory:
            temp=pathlib.Path(directory)
            for name,contents in headers.items():
                path=temp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(contents,encoding='utf-8')
            source=temp/'input.c';source.write_text(INPUT_FIXTURE,encoding='utf-8')
            binary=temp/('test-input.exe' if os.name=='nt' else 'test-input')
            result=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+directory,'-I'+str(root/'core'),'-I'+str(root/'main'),str(source),str(root/'core/pocket_model.c'),str(root/'core/pocket_reminders.c'),str(root/'core/pocket_clock.c'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('pocket primitive input: PASS',result.stdout)

    def test_read_only_actions(self):
        compiler=os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:self.skipTest('host C compiler unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        with tempfile.TemporaryDirectory() as temp:
            directory=pathlib.Path(temp)
            (directory/'esp_err.h').write_text('#pragma once\ntypedef int esp_err_t;\n',encoding='utf-8')
            (directory/'esp_timer.h').write_text('#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',encoding='utf-8')
            source=directory/'actions.c';source.write_text(ACTIONS_FIXTURE,encoding='utf-8')
            binary=directory/('test-actions.exe' if os.name=='nt' else 'test-actions')
            result=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+temp,'-I'+str(root/'core'),'-I'+str(root/'main'),str(root/'main/pocket_actions.c'),str(source),'-o',str(binary)],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('pocket actions: PASS',result.stdout)

    def test_model(self):
        compiler=os.environ.get('POCKET_HOST_CC') or shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        if not compiler:self.skipTest('host C compiler unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        with tempfile.TemporaryDirectory() as temp:
            binary=pathlib.Path(temp)/('test-model.exe' if os.name=='nt' else 'test-model')
            subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(root/'core'),str(root/'core/pocket_model.c'),str(root/'tests/test_model.c'),'-o',str(binary)],check=True,capture_output=True)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS',result.stdout)

    def test_ui_snapshots(self):
        # Renders firmware/main/pocket_ui.c with the same 24 KB LVGL pool as the device;
        # an out-of-memory assert hangs, so the timeout catches pool regressions.
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        compiler=shutil.which('gcc') or shutil.which('clang')
        cached=pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():compiler=str(cached)
        lvgl=os.environ.get('LVGL_DIR')
        cached_lvgl=root.parent.parent/'HPusage-build-20261015-001/firmware/cached_components/lvgl'
        if not lvgl and (cached_lvgl/'lvgl.h').is_file():lvgl=str(cached_lvgl)
        if not compiler or not lvgl:self.skipTest('host C compiler or LVGL_DIR (LVGL 9.5 source) unavailable')
        include=['-DLV_CONF_INCLUDE_SIMPLE','-I'+str(root/'tests'),'-I'+lvgl,'-I'+str(root/'main'),'-I'+str(root/'core'),'-I'+str(root/'fonts')]
        if os.name=='nt':include.append('-D_CRT_SECURE_NO_WARNINGS')
        names=['home','home-alert','home-offline','chat-ready','chat-listening','chat-thinking','chat-speaking','chat-error','chat-offline','usage',
               'usage-available','usage-alert','usage-next-window','usage-null-zero','usage-stale-partial-failed','home-after-usage',
               'diag-unchecked','diag-ready','diag-tls','diag-login','diag-paused','home-save-failed',
               'tasks-unfetched','tasks-running','tasks-approval','tasks-unknown','tasks-result-utf8','tasks-truncated','tasks-offline',
               'tools-status','tools-usage','tools-tasks','tools-todo','audio','audio-edit','audio-muted',
               'alerts-default','alerts-usd','alerts-off','alerts-note','screen','setup',
               'models-default','models-pending','models-opus','models-glm-unverified','models-unavailable',
               'chat-traditional','chat-simplified','chat-utf8-boundary']
        names += ['usage-local-available','usage-local-cycle','usage-local-partial','usage-local-null-zero',
                  'usage-local-stale-failed','usage-local-large','usage-local-coverage-device','usage-local-coverage-provider',
                  'usage-local-fetched-utc','usage-local-last-seen-utc','usage-local-time-unknown',
                  'usage-local-clock-unknown','usage-local-clock-mismatch','usage-local-quota-unknown',
                  'usage-local-quota-available','usage-local-single-provider','home-after-local-usage']
        names += [f'usage-dashboard-{page:02d}' for page in range(19)]
        names += ['tools-reader','tools-reader-unavailable','reader-unicode-first','reader-unicode-last','reader-final-block','reader-previous-last','reader-loading','reader-empty','reader-revision-changed','home-after-reader']
        names += ['setup-wifi-testing','setup-wifi-saved','setup-wifi-failed']
        names += ['home-clock-synced','home-clock-offline','home-clock-year-end','home-clock-new-year','home-clock-reconnected']
        names += ['home-cache-fresh','home-cache-stale','home-cache-backwards','home-cache-stale-offline']
        with tempfile.TemporaryDirectory() as temp:
            out=os.environ.get('POCKET_SNAPSHOT_DIR') or temp
            objects=[]
            for source in (root/'core/pocket_model.c',root/'core/pocket_usage.c',root/'core/pocket_diagnostics.c',root/'core/pocket_clock.c',root/'fonts/lv_font_pocket_cjk_16.c',root/'tests/ui_snapshot.c'):
                obj=pathlib.Path(temp,source.stem+'.obj');objects.append(str(obj))
                built=subprocess.run([compiler,'-c','-std=c11','-O1','-Wall','-Wextra','-Werror',*include,str(source),'-o',str(obj)],cwd=temp,capture_output=True,text=True,timeout=60)
                self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            sources=sorted(str(p) for p in pathlib.Path(lvgl,'src').rglob('*.c'))
            binary=pathlib.Path(temp)/('ui-snapshot.exe' if os.name=='nt' else 'ui-snapshot')
            arguments=['-std=c11','-O1','-w',*include,*objects,*sources,*([] if os.name=='nt' else ['-lm']),'-o',str(binary)]
            response=pathlib.Path(temp,'lvgl.rsp')
            response.write_text('\n'.join('"'+value.replace('\\','/').replace('"','\\"')+'"' for value in arguments),encoding='utf-8')
            built=subprocess.run([compiler,'@'+str(response)],cwd=temp,capture_output=True,text=True,timeout=600)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            rendered=subprocess.run([str(binary),out],capture_output=True,text=True,timeout=120)
            self.assertEqual(rendered.returncode,0,rendered.stdout+rendered.stderr)
            for name in names:
                image=pathlib.Path(out,name+'.bmp').read_bytes()
                self.assertEqual(len(image),54+240*320*3,name)
                self.assertGreater(len(set(image[54::3])),8,name+' rendered blank')
            # Values/windows and cached/error states must reach the rendered rows.
            self.assertNotEqual(pathlib.Path(out,'usage-available.bmp').read_bytes(),pathlib.Path(out,'usage-next-window.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-available.bmp').read_bytes(),pathlib.Path(out,'usage-null-zero.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-null-zero.bmp').read_bytes(),pathlib.Path(out,'usage-stale-partial-failed.bmp').read_bytes())
            for different in ['usage-local-partial','usage-local-null-zero','usage-local-stale-failed','usage-local-large',
                              'usage-local-clock-unknown','usage-local-clock-mismatch','usage-local-single-provider']:
                self.assertNotEqual(pathlib.Path(out,'usage-local-available.bmp').read_bytes(),pathlib.Path(out,different+'.bmp').read_bytes(),different)
            self.assertEqual(pathlib.Path(out,'usage-local-available.bmp').read_bytes(),pathlib.Path(out,'usage-local-cycle.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-local-coverage-device.bmp').read_bytes(),pathlib.Path(out,'usage-local-coverage-provider.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-local-quota-unknown.bmp').read_bytes(),pathlib.Path(out,'usage-local-quota-available.bmp').read_bytes())
            for page in range(14):
                self.assertNotEqual(pathlib.Path(out,f'usage-dashboard-{page:02d}.bmp').read_bytes(),pathlib.Path(out,f'usage-dashboard-{page+1:02d}.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'models-default.bmp').read_bytes(),pathlib.Path(out,'models-pending.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'models-pending.bmp').read_bytes(),pathlib.Path(out,'models-opus.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'models-opus.bmp').read_bytes(),pathlib.Path(out,'models-glm-unverified.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'models-glm-unverified.bmp').read_bytes(),pathlib.Path(out,'models-unavailable.bmp').read_bytes())
