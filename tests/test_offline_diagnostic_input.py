"""Actual main.c input routes, with fake voice/IDF boundaries only."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_firmware_host as host
EXTRA=r'''
static input pending_input;
static bool queue_fail,diag_owned;
static unsigned diag_arms,diag_starts,diag_stops,diag_lost;
static uint64_t arm_ms,token_ms;static uint32_t arm_nonce,token_nonce;
static pocket_offline_diagnostic_snapshot diag_view;
bool pocket_voice_diagnostic_arm(uint64_t now,uint32_t nonce){
 assert(now==(uint64_t)clock_ms);assert(nonce==atomic_load(&diagnostic_event_nonce));
 if(diag_arms||hardware_voice.busy)return false;
 diag_arms++;arm_ms=now;arm_nonce=nonce;diag_view.phase=PDIAG_ARMED;return true;
}
void pocket_voice_diagnostic_button(bool ok,bool press,uint64_t ms,uint32_t nonce){
 if(press&&diag_owned){diag_stops++;return;}
 if(ok&&press&&diag_view.phase==PDIAG_ARMED&&ms>arm_ms&&nonce>arm_nonce){token_ms=ms;token_nonce=nonce;}
}
bool pocket_voice_diagnostic_begin(uint64_t ms,uint32_t nonce){
 if(diag_view.phase!=PDIAG_ARMED||!token_nonce||ms!=token_ms||nonce!=token_nonce)return false;
 diag_starts++;diag_owned=true;diag_view.phase=PDIAG_QUEUED;hardware_voice.busy=true;return true;
}
void pocket_voice_diagnostic_input_lost(void){diag_lost++;token_nonce=0;}
void pocket_voice_diagnostic_read(pocket_offline_diagnostic_snapshot*out){*out=diag_view;}
bool pocket_voice_diagnostic_active(void){return diag_owned;}
'''
MAIN=r'''
int main(void){
 (void)fresh_usage;(void)click;
 reset();events=(void*)1;atomic_store(&diagnostic_event_nonce,0);model.page=POCKET_DIAGNOSTICS;
 event(BSP_BTN_OK,BSP_BTN_PRESS);event(BSP_BTN_OK,BSP_BTN_LONG);
 assert(diag_arms==1&&diag_starts==0&&begins==0);
 event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(diag_starts==0);
 event(BSP_BTN_OK,BSP_BTN_PRESS);assert(diag_starts==1&&begins==0);
 /* Physical stop happens even if the UI input queue cannot accept an event. */
 queue_fail=true;unsigned before=diag_stops;on_button(BSP_BTN_DOWN,BSP_BTN_PRESS,0);
 assert(diag_stops==before+1&&diag_lost==1);queue_fail=false;
 /* Diagnostic cleanup retains its voice lease, yet manual Screen off works. */
 model.page=POCKET_SCREEN_OFF;hardware_voice.busy=true;diag_owned=true;
 event(BSP_BTN_OK,BSP_BTN_PRESS);assert(!screen_off);
 event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);assert(screen_off&&hardware_screen_off);
 /* A Screen off wake cannot create a diagnostic confirmation token. */
 diag_owned=false;diag_view.phase=PDIAG_ARMED;token_nonce=0;event(BSP_BTN_OK,BSP_BTN_PRESS);assert(token_nonce==0);event(BSP_BTN_OK,BSP_BTN_RELEASE);event(BSP_BTN_OK,BSP_BTN_CLICK);
 /* Arm snapshots current callback nonce, never a delayed LONG's old nonce. */
 reset();events=(void*)1;diag_owned=false;diag_arms=diag_starts=0;diag_view.phase=PDIAG_IDLE;model.page=POCKET_DIAGNOSTICS;
 clock_ms++;on_button(BSP_BTN_OK,BSP_BTN_LONG,0);input delayed=pending_input;
 clock_ms++;on_button(BSP_BTN_UP,BSP_BTN_PRESS,0);uint32_t newest=atomic_load(&diagnostic_event_nonce);
 clock_ms++;handle_input(delayed);assert(arm_nonce==newest&&arm_ms==(uint64_t)clock_ms);
 handle_input((input){.button=BSP_BTN_OK,.event=BSP_BTN_CLICK,.timestamp_ms=clock_ms,.nonce=newest});assert(!diag_starts);
 diag_arms=0;diag_view.phase=PDIAG_IDLE;clock_ms++;on_button(BSP_BTN_OK,BSP_BTN_LONG,0);delayed=pending_input;clock_ms+=1001;discard_gesture[BSP_BTN_OK]=false;handle_input(delayed);assert(diag_arms==0);
 atomic_store(&diagnostic_event_nonce,UINT32_MAX);unsigned lost=diag_lost;on_button(BSP_BTN_OK,BSP_BTN_PRESS,0);assert(diag_lost==lost+1&&atomic_load(&diagnostic_event_nonce)==UINT32_MAX);assert(pending_input.nonce==UINT32_MAX&&pending_input.button==BSP_BTN_OK);
 puts("pocket primitive input: PASS");return 0;
}
'''
class OfflineDiagnosticInputTests(unittest.TestCase):
 def test_actual_input_arm_nonce_callback_stop_and_screen_off(self):
  original=host.INPUT_FIXTURE
  fixture=original[:original.index('int main(void){')]
  pos=fixture.index('BaseType_t xQueueSend(')
  fixture=fixture[:pos]+EXTRA+fixture[pos:]
  fixture=fixture.replace('(void)queue;(void)value;(void)timeout;return pdTRUE;','(void)queue;(void)timeout;pending_input=*(const input*)value;return queue_fail?pdFALSE:pdTRUE;')
  fixture=fixture.replace('handle_input((input){button,kind});','clock_ms++;on_button(button,kind,0);if(!queue_fail)handle_input(pending_input);')
  host.INPUT_FIXTURE='#define CONFIG_POCKET_OFFLINE_DIAGNOSTIC 1\n'+fixture+MAIN
  try:host.FirmwareHostTests('test_primitive_input_routing').test_primitive_input_routing()
  finally:host.INPUT_FIXTURE=original
