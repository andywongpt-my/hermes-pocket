"""Actual main input routing for default-off local qualification; fake owner only."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_firmware_host as host
import test_live_app_input as live
EXTRA=r'''
static input pending_input;
static bool queue_fail,qarmed,qowned,qlocked,qstopped;
static uint32_t qgeneration;
static unsigned qarms,qbegins,qlost,qstops;
bool pocket_qualification_usb_init(void){return true;}
void pocket_voice_qualification_poll(void){}
uint32_t pocket_voice_qualification_button(bool ok,bool press,bool release,uint64_t *event_ms){(void)ok;(void)release;clock_ms++;*event_ms=(uint64_t)clock_ms;if(press){qgeneration++;if(qowned){qstopped=true;qstops++;}}return qgeneration;}
bool pocket_voice_qualification_arm(uint64_t ms,uint32_t gen){assert(ms==(uint64_t)clock_ms&&gen==qgeneration);qarms++;qarmed=true;return true;}
bool pocket_voice_qualification_begin(uint64_t ms,uint32_t gen){assert(ms==(uint64_t)clock_ms&&gen==qgeneration);if(!qarmed)return false;qbegins++;qowned=true;hardware_voice.busy=true;return true;}
void pocket_voice_qualification_input_lost(void){qlost++;qstopped=true;}
bool pocket_voice_qualification_active(void){return qowned;}
bool pocket_voice_qualification_locked(void){return qlocked;}
void pocket_voice_qualification_format(char*t,size_t ts,char*d,size_t ds,char*h,size_t hs){snprintf(t,ts,"LOCAL TEST");snprintf(d,ds,"Mic/ref sent to USB");snprintf(h,hs,"Hold OK, release, press");}
'''
MAIN=r'''
int main(void){
 (void)fresh_usage;(void)click;
 reset();events=(void*)1;model.page=POCKET_DIAGNOSTICS;
 event(BSP_BTN_OK,BSP_BTN_PRESS);assert(!qbegins&&!begins);
 event(BSP_BTN_OK,BSP_BTN_LONG);assert(qarms==1&&!qbegins);
 event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(!qbegins);
 event(BSP_BTN_OK,BSP_BTN_PRESS);assert(qbegins==1&&!begins&&hardware_voice.busy);
 event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(!qstops);
 queue_fail=true;on_button(BSP_BTN_DOWN,BSP_BTN_PRESS,0);assert(qstopped&&qstops==1&&qlost==1);queue_fail=false;
 model.page=POCKET_SCREEN_OFF;qlocked=qowned=hardware_voice.busy=true;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(screen_off&&hardware_screen_off);
 unsigned before=qarms;model.page=POCKET_DIAGNOSTICS;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(!screen_off&&qarms==before&&qbegins==1);
 /* Other normal paths retain behavior when no qualification owner is active. */
 reset();events=(void*)1;qowned=qarmed=qlocked=false;model.page=POCKET_CHAT;
 event(BSP_BTN_OK,BSP_BTN_PRESS);
#if CONFIG_POCKET_LIVE_APP_CALLER
 assert(!begins&&live_begins==1&&qbegins==1);
#else
 assert(begins==1&&qbegins==1);
#endif
 simulate_idle=true;render();assert(rendered.qualification_mode&&!rendered.qualification_locked);
 puts("pocket primitive input: PASS");return 0;
}
'''
class QualificationInputTests(unittest.TestCase):
    def run_fixture(self,combined=False):
        original=host.INPUT_FIXTURE
        fixture=original[:original.index('int main(void){')]
        pos=fixture.index('BaseType_t xQueueSend(')
        extra=EXTRA
        if combined:
            extra+=live.EXTRA.replace('static input pending_input;','').replace('static bool queue_fail,live_owned,','static bool live_owned,')
        fixture=fixture[:pos]+extra+fixture[pos:]
        fixture=fixture.replace('(void)queue;(void)value;(void)timeout;return pdTRUE;', '(void)queue;(void)timeout;pending_input=*(const input*)value;if(qowned&&pending_input.event==BSP_BTN_PRESS)assert(qstopped);return queue_fail?pdFALSE:pdTRUE;')
        fixture=fixture.replace('rendered=*view;renders++;display_step(8);', 'assert(view->qualification_mode&&!strcmp(view->qualification_title,"LOCAL TEST"));rendered=*view;renders++;display_step(8);')
        fixture=fixture.replace('handle_input((input){button,kind});','clock_ms++;qstopped=false;on_button(button,kind,0);if(!queue_fail)handle_input(pending_input);')
        host.INPUT_FIXTURE='#define CONFIG_POCKET_QUALIFICATION 1\n'+('#define CONFIG_POCKET_LIVE_APP_CALLER 1\n' if combined else '')+fixture+MAIN
        try:host.FirmwareHostTests('test_primitive_input_routing').test_primitive_input_routing()
        finally:host.INPUT_FIXTURE=original

    def test_actual_normal_input_keeps_confirmation_stop_and_standby(self):
        self.run_fixture()

    def test_qualification_and_live_caller_callbacks_share_one_queue(self):
        self.run_fixture(combined=True)
