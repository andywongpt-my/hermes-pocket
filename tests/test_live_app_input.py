"""Actual normal main input with Live option enabled; fake worker boundary only."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_firmware_host as host
import test_offline_diagnostic_input as diag

EXTRA = r'''
static input pending_input;
static bool queue_fail,live_owned,live_cleanup,live_stop_seen,admit=true;
static unsigned live_begins,live_stops,live_lost;
static uint32_t live_generation;
static uint64_t received_ms;
bool pocket_live_v2_device_admission_allowed(void){return false;}
bool pocket_voice_live_begin(unsigned target,uint64_t ms,uint32_t generation){assert(target==model.target);if(!generation||generation==UINT32_MAX||generation!=live_generation)return false;live_begins++;received_ms=ms;if(!admit)return false;live_owned=true;hardware_voice.busy=true;return true;}
uint32_t pocket_voice_live_button(bool press){if(press){if(live_generation!=UINT32_MAX)live_generation++;if(live_owned){live_stops++;live_stop_seen=true;}}return live_generation;}
void pocket_voice_live_input_lost(void){live_lost++;if(live_generation!=UINT32_MAX)live_generation++;live_stop_seen=true;}
bool pocket_voice_live_active(void){return live_owned;}
bool pocket_voice_live_cleanup_pending(void){return live_cleanup;}
'''
MAIN = r'''
int main(void){
 (void)fresh_usage;(void)click;
 reset();events=(void*)1;model.page=POCKET_CHAT;
 event(BSP_BTN_OK,BSP_BTN_PRESS);assert(live_begins==1&&!begins&&received_ms==(uint64_t)clock_ms);
 event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(live_begins==1&&!begins&&live_stops==0&&hardware_voice.busy);
 queue_fail=true;on_button(BSP_BTN_DOWN,BSP_BTN_PRESS,0);assert(live_stops==1&&live_lost==1);queue_fail=false;
 /* All future cleanup states retain manual standby/wake and navigation. */
 model.page=POCKET_SCREEN_OFF;live_cleanup=true;live_owned=true;hardware_voice.busy=true;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(screen_off&&hardware_screen_off);unsigned before=live_begins;
 model.page=POCKET_CHAT;event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(!screen_off&&live_begins==before);
 /* A stale queued press cannot start, nor can its delayed click fall back. */
 reset();events=(void*)1;live_owned=live_cleanup=live_stop_seen=false;model.page=POCKET_CHAT;live_begins=0;
 clock_ms++;on_button(BSP_BTN_OK,BSP_BTN_PRESS,0);input old=pending_input;clock_ms+=1001;handle_input(old);assert(!live_begins&&!begins);
 /* Refusal consumes the whole gesture even with old model connectivity state. */
 reset();events=(void*)1;live_owned=false;admit=false;model.page=POCKET_CHAT;model.connected[0]=true;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(live_begins==1&&!begins&&!model.recording&&!hardware_voice.busy);
 /* Reader/wake/setup still own their original gestures. */
 reset();events=(void*)1;live_owned=false;admit=true;model.page=POCKET_CHAT;live_begins=0;reader_fake.active=true;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(!live_begins&&reader_closes==1);
 hardware_setup.active=true;event(BSP_BTN_OK,BSP_BTN_PRESS);assert(!live_begins);hardware_setup.active=false;
 /* A later physical press cancels a still-queued start before ownership exists. */
 reset();events=(void*)1;live_owned=false;live_begins=0;model.page=POCKET_CHAT;
 clock_ms++;on_button(BSP_BTN_OK,BSP_BTN_PRESS,0);input queued=pending_input;
 clock_ms++;on_button(BSP_BTN_DOWN,BSP_BTN_PRESS,0);handle_input(queued);
 assert(!live_begins&&!hardware_voice.busy);
 /* Queue loss also invalidates already-queued starts, without an active owner. */
 reset();events=(void*)1;live_owned=false;live_begins=0;model.page=POCKET_CHAT;
 clock_ms++;on_button(BSP_BTN_OK,BSP_BTN_PRESS,0);queued=pending_input;
 queue_fail=true;clock_ms++;on_button(BSP_BTN_DOWN,BSP_BTN_RELEASE,0);queue_fail=false;
 handle_input(queued);assert(!live_begins&&!hardware_voice.busy);
 /* Legacy model overrides are not silently applied to the Live backend. */
 reset();events=(void*)1;model.page=POCKET_MODELS;model.model_candidate=1;live_owned=false;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 assert(model.model_candidate==1&&model.model_choice[0]==0&&!begins);
 /* View reports compiled mode and actual hard refusal, not fabricated ready. */
 simulate_idle=true;render();assert(rendered.live_mode&&!rendered.live_available);
 puts("pocket primitive input: PASS");return 0;
}
'''

class LiveAppInputTests(unittest.TestCase):
    def run_fixture(self, combined=False):
        original = host.INPUT_FIXTURE
        fixture = original[:original.index('int main(void){')]
        pos = fixture.index('BaseType_t xQueueSend(')
        extra = EXTRA
        if combined:
            extra += diag.EXTRA.replace('static input pending_input;', '').replace('static bool queue_fail,diag_owned;', 'static bool diag_owned;')
        fixture = fixture[:pos] + extra + fixture[pos:]
        fixture = fixture.replace('(void)queue;(void)value;(void)timeout;return pdTRUE;', '(void)queue;(void)timeout;pending_input=*(const input*)value;if(live_owned&&pending_input.event==BSP_BTN_PRESS)assert(live_stop_seen);return queue_fail?pdFALSE:pdTRUE;')
        fixture = fixture.replace('handle_input((input){button,kind});', 'clock_ms++;live_stop_seen=false;on_button(button,kind,0);if(!queue_fail)handle_input(pending_input);')
        host.INPUT_FIXTURE = '#define CONFIG_POCKET_LIVE_APP_CALLER 1\n' + ('#define CONFIG_POCKET_OFFLINE_DIAGNOSTIC 1\n' if combined else '') + fixture + MAIN
        try:
            host.FirmwareHostTests('test_primitive_input_routing').test_primitive_input_routing()
        finally:
            host.INPUT_FIXTURE = original

    def test_live_input_without_silent_diagnostic(self):
        self.run_fixture()

    def test_live_and_silent_options_do_not_duplicate_queue_or_lose_stop(self):
        self.run_fixture(combined=True)
