import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ACTIONS_FIXTURE = r'''
#include "pocket_actions.h"
#include "pocket_tasks_client.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static pocket_tasks_snapshot snapshots[2];
static unsigned refreshes,opens,gets,forces,disconnects;
static bool pending_list[2],pending_view[2];
static char wire[8192];
void pocket_usage_force(unsigned target){assert(target<2);forces++;}
void pocket_tasks_refresh(unsigned target){assert(target<2);refreshes++;pending_list[target]=true;snapshots[target].list_status=POCKET_TASKS_LOADING;}
void pocket_tasks_open(unsigned target){assert(target<2);opens++;pending_view[target]=true;snapshots[target].view_status=POCKET_TASKS_LOADING;}
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out){assert(target<2);*out=snapshots[target];}
void pocket_tasks_disconnected(unsigned target){assert(target<2);disconnects++;pending_list[target]=pending_view[target]=false;snapshots[target].list_status=POCKET_TASKS_OFFLINE;}
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
 puts("pocket actions: PASS");return 0;
}
'''

INPUT_FIXTURE = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "main.c"
static pocket_setup_state hardware_setup;
static pocket_voice_state hardware_voice;
static pocket_usage_snapshot hardware_usage[2];
static pocket_tasks_snapshot hardware_tasks[2];
static pocket_ui_view rendered;
static unsigned begins,cancels,volume_sets,refreshes,nexts,tool_runs,setups,saves;
void test_log(const char *tag,const char *format,...){(void)tag;(void)format;}
time_t test_time(time_t *out){if(out)*out=1800000000;return 1800000000;}
int64_t esp_timer_get_time(void){return 1000000;}
esp_err_t bsp_i2c_init(void){return ESP_OK;}
esp_err_t bsp_display_init(void){return ESP_OK;}
void *bsp_lvgl_init(void){return (void*)1;}
bool bsp_lvgl_lock(int timeout){(void)timeout;return false;}
void bsp_lvgl_unlock(void){}
void bsp_display_backlight(unsigned value){(void)value;}
esp_err_t bsp_battery_init(void){return ESP_OK;}
int bsp_battery_soc(void){return 76;}
esp_err_t bsp_button_init(bsp_btn_cb_t callback,void *context){(void)callback;(void)context;return ESP_OK;}
int bsp_button_read_mv(void){return 3300;}
QueueHandle_t xQueueCreate(unsigned count,unsigned item){(void)count;(void)item;return (void*)1;}
BaseType_t xQueueSend(QueueHandle_t queue,const void *value,TickType_t timeout){(void)queue;(void)value;(void)timeout;return pdTRUE;}
BaseType_t xQueueReceive(QueueHandle_t queue,void *value,TickType_t timeout){(void)queue;(void)value;(void)timeout;return pdFALSE;}
BaseType_t xTaskCreate(void (*entry)(void*),const char *name,unsigned stack,void *arg,unsigned priority,TaskHandle_t *handle){(void)entry;(void)name;(void)stack;(void)arg;(void)priority;(void)handle;return pdPASS;}
void vTaskDelay(TickType_t ticks){(void)ticks;}
void pocket_setup_read(pocket_setup_state *out){*out=hardware_setup;}
esp_err_t pocket_setup_init(void){return ESP_OK;}
void pocket_setup_request(void){setups++;}
void pocket_voice_read(pocket_voice_state *out){*out=hardware_voice;}
esp_err_t pocket_voice_init(void){return ESP_OK;}
void pocket_voice_begin(unsigned target){assert(target<2);begins++;hardware_voice.busy=hardware_voice.recording=true;}
void pocket_voice_cancel(void){cancels++;hardware_voice.busy=hardware_voice.recording=hardware_voice.playing=false;}
void pocket_voice_release(void){hardware_voice.recording=false;}
void pocket_voice_set_volume(unsigned value){hardware_voice.volume=value>100?100:value;volume_sets++;}
void pocket_usage_read(unsigned target,pocket_usage_snapshot *out){assert(target<2);*out=hardware_usage[target];}
void pocket_tasks_read(unsigned target,pocket_tasks_snapshot *out){assert(target<2);*out=hardware_tasks[target];}
void pocket_tasks_refresh(unsigned target){assert(target<2);refreshes++;}
void pocket_tasks_next(unsigned target){assert(target<2);nexts++;}
void pocket_actions_init(void){}
void pocket_actions_view(pocket_page page,unsigned target){(void)page;assert(target<2);}
bool pocket_actions_request(unsigned target,pocket_tool tool){assert(target<2);if(tool==POCKET_TOOL_TODO_UNAVAILABLE)return false;tool_runs++;return true;}
unsigned pocket_preferences_load_target(void){return 0;}
esp_err_t pocket_preferences_save_target(unsigned target){assert(target<2);saves++;return ESP_OK;}
void pocket_ui_create(void){}
void pocket_ui_render(const pocket_ui_view *view){rendered=*view;}
void pocket_ui_set_awake(bool awake){(void)awake;}
static void reset(void){
 pocket_init(&model);memset(&hardware_setup,0,sizeof hardware_setup);memset(&hardware_voice,0,sizeof hardware_voice);hardware_voice.volume=60;
 memset(discard_gesture,0,sizeof discard_gesture);held_buttons=0;screen_off=wake_gesture=false;
 memset(&reminders,0,sizeof reminders);memset(alert_notes,0,sizeof alert_notes);memset(&rendered,0,sizeof rendered);
 memset(alert_pending,0,sizeof alert_pending);
 memset(hardware_usage,0,sizeof hardware_usage);memset(hardware_tasks,0,sizeof hardware_tasks);
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
 reset();render();assert(!rendered.alert_pending&&!strcmp(rendered.alert_note,"No configured Usage feeds."));
 fresh_usage(0);hardware_usage[0].providers[0].metrics[0].expires_at=1800000000;render();assert(!rendered.alert_pending);
 hardware_usage[0].providers[0].metrics[0].expires_at=1800000060;render();assert(rendered.alert_pending&&strstr(rendered.alert_note,"85.0% used"));
 model.page=POCKET_USAGE;render();assert(rendered.alert_pending);
 model.target=1;render();assert(!rendered.alert_pending);fresh_usage(1);render();assert(rendered.alert_pending);
 model.target=0;model.page=POCKET_AUDIO;click(BSP_BTN_DOWN);assert(model.page==POCKET_ALERTS);
 hardware_setup.active=true;render();hardware_setup.active=false;model.page=POCKET_STATUS;render();assert(rendered.alert_pending);
 model.page=POCKET_ALERTS;render();assert(!rendered.alert_pending&&strstr(rendered.alert_note,"85.0% used"));
 model.page=POCKET_STATUS;render();assert(!rendered.alert_pending);model.target=1;render();assert(rendered.alert_pending);
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
        headers['esp_err.h']+='\n#define ESP_ERROR_CHECK(value) ((void)(value))\n'
        headers.update({
            'bsp_i2c.h':'#pragma once\n#include "esp_err.h"\nesp_err_t bsp_i2c_init(void);\n',
            'bsp_display.h':'#pragma once\n#include <stdbool.h>\n#include "esp_err.h"\nesp_err_t bsp_display_init(void);\nvoid *bsp_lvgl_init(void);\nbool bsp_lvgl_lock(int);\nvoid bsp_lvgl_unlock(void);\nvoid bsp_display_backlight(unsigned);\n',
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
            result=subprocess.run([compiler,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+directory,'-I'+str(root/'core'),'-I'+str(root/'main'),str(source),str(root/'core/pocket_model.c'),str(root/'core/pocket_reminders.c'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
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
            self.assertIn('PASS',subprocess.check_output([str(binary)],text=True))

    def test_ui_snapshots(self):
        # Renders firmware/main/pocket_ui.c with the same 24 KB LVGL pool as the device;
        # an out-of-memory assert hangs, so the timeout catches pool regressions.
        compiler=shutil.which('gcc');lvgl=os.environ.get('LVGL_DIR')
        if not compiler or not lvgl:self.skipTest('host C compiler or LVGL_DIR (LVGL 9.5 source) unavailable')
        root=pathlib.Path(__file__).resolve().parents[1]/'firmware'
        include=['-DLV_CONF_INCLUDE_SIMPLE','-I'+str(root/'tests'),'-I'+lvgl,'-I'+str(root/'main'),'-I'+str(root/'core')]
        names=['home','home-alert','home-offline','chat-ready','chat-listening','chat-thinking','chat-speaking','chat-error','chat-offline','usage',
               'usage-available','usage-alert','usage-next-window','usage-null-zero','usage-stale-partial-failed','home-after-usage',
               'diag-unchecked','diag-ready','diag-tls','diag-login','diag-paused','home-save-failed',
               'tasks-unfetched','tasks-running','tasks-approval','tasks-unknown','tasks-result-utf8','tasks-truncated','tasks-offline',
               'tools-status','tools-usage','tools-tasks','tools-todo','audio','audio-edit','audio-muted',
               'alerts-default','alerts-usd','alerts-off','alerts-note','screen','setup']
        with tempfile.TemporaryDirectory() as temp:
            out=os.environ.get('POCKET_SNAPSHOT_DIR') or temp
            subprocess.run([compiler,'-c','-std=c11','-O1','-Wall','-Wextra','-Werror',*include,str(root/'main/pocket_ui.c'),str(root/'core/pocket_usage.c'),str(root/'core/pocket_diagnostics.c'),str(root/'tests/ui_snapshot.c')],cwd=temp,check=True,capture_output=True)
            sources=sorted(str(p) for p in pathlib.Path(lvgl,'src').rglob('*.c'))
            binary=pathlib.Path(temp)/('ui-snapshot.exe' if os.name=='nt' else 'ui-snapshot')
            subprocess.run([compiler,'-std=c11','-O1','-w',*include,'pocket_ui.o','pocket_usage.o','pocket_diagnostics.o','ui_snapshot.o',*sources,'-lm','-o',str(binary)],cwd=temp,check=True,capture_output=True)
            subprocess.run([str(binary),out],check=True,capture_output=True,timeout=120)
            for name in names:
                image=pathlib.Path(out,name+'.bmp').read_bytes()
                self.assertEqual(len(image),54+240*320*3,name)
                self.assertGreater(len(set(image[54::3])),8,name+' rendered blank')
            # Values/windows and cached/error states must reach the rendered rows.
            self.assertNotEqual(pathlib.Path(out,'usage-available.bmp').read_bytes(),pathlib.Path(out,'usage-next-window.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-available.bmp').read_bytes(),pathlib.Path(out,'usage-null-zero.bmp').read_bytes())
            self.assertNotEqual(pathlib.Path(out,'usage-null-zero.bmp').read_bytes(),pathlib.Path(out,'usage-stale-partial-failed.bmp').read_bytes())
