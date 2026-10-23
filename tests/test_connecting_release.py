"""Run the real C worker; only hardware/HTTP boundaries use the existing offline harness."""
import unittest
import test_firmware_voice as baseline

def regression_harness():
    text = baseline.HARNESS
    text = text.replace('static int status_shape;', 'static int status_shape;\nstatic int connecting_release_scenario;')
    anchor = 'if(body&&strstr(path,"/audio/")&&!strstr(path,"/chunk")&&!strstr(path,"/finish")&&!strstr(path,"/abort"))snprintf(submitted_begin,sizeof submitted_begin,"%s",body);'
    assert text.count(anchor) == 1
    replacement = anchor + r'''
 if(connecting_release_scenario && body && strstr(path,"/audio/") && !strstr(path,"/chunk") && !strstr(path,"/finish") && !strstr(path,"/abort")){
  /* User releases while the successful begin request is in flight. */
  if(connecting_release_scenario==2)pocket_voice_cancel();else pocket_voice_release();
 }
 if(connecting_release_scenario==3 && strstr(path,"/abort")){
  memcpy(out,"{}",3);result->status=401;result->length=2;return ESP_OK;
 }
'''
    text = text.replace(anchor, replacement)
    anchor = 'int main(int argc,char **argv) {'
    assert text.count(anchor) == 1
    cases = r'''
 if(argc>1 && !strncmp(argv[1],"connectrelease",14)){
  reset(4);connecting_release_scenario=atoi(argv[1]+14);
  CHECK(connecting_release_scenario>=1&&connecting_release_scenario<=3,"bad release fixture");
  int ended=setjmp(stopped);if(!ended)network(NULL);
  CHECK(!state.busy&&!state.recording&&!state.playing,"release did not finish the workflow");
  CHECK(!queue_live&&!audio_queue&&!voice_reserved&&!transport_open,"release retained capture/radio/socket ownership");
  CHECK(!recorder_tasks&&!capture_reads&&!chunk_attempts&&!finish_calls,"pre-recording release submitted audio or model work");
  if(connecting_release_scenario!=3)CHECK(abort_calls==1,"accepted upload was not aborted exactly once");
  CHECK(strcmp(state.message,"Connecting..."),"terminal early release leaves the LCD permanently showing Connecting");
  if(connecting_release_scenario==1)CHECK(strstr(state.message,"Hold")&&strstr(state.message,"OK"),"ordinary release did not restore an actionable talk prompt");
  if(connecting_release_scenario==2)CHECK(strstr(state.message,"discarded"),"explicit cancellation lost its discard acknowledgement");
  if(connecting_release_scenario==3)CHECK(strstr(state.message,"Pairing"),"abort authorization error was hidden by the release prompt");
  connecting_release_scenario=0;reset(0);puts("PASS: pre-recording release/cancel/error ends progress and preserves cleanup and no-submission semantics");return 0;
 }
'''
    return text.replace(anchor, anchor + cases)

class ConnectingReleaseTests(unittest.TestCase):
    def exercise(self, case):
        original = baseline.HARNESS
        baseline.HARNESS = regression_harness()
        try:
            baseline.FirmwareVoiceTests()._exercise([case])
        finally:
            baseline.HARNESS = original

    def test_release_while_begin_returns_clears_connecting_and_restores_talk(self):
        self.exercise('connectrelease1')

    def test_cancel_while_begin_returns_keeps_discard_feedback(self):
        self.exercise('connectrelease2')

    def test_abort_auth_failure_remains_visible_after_release(self):
        self.exercise('connectrelease3')

if __name__ == '__main__':
    unittest.main()
