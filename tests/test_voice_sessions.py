"""Real C worker across terminal paths and manual retries, without global resets."""
import unittest

import test_firmware_voice as baseline


def session_harness():
    text = baseline.HARNESS

    def replace_once(anchor, replacement):
        nonlocal text
        assert text.count(anchor) == 1, anchor
        text = text.replace(anchor, replacement)

    replace_once('static int status_shape;', r'''
static int status_shape;
static int session_case,session_attempt;
static bool session_retry,first_clean,late_resumed;
static unsigned session_random;
static int first_finish,first_abort,first_chunks,first_reads,first_writes;
static char first_message[128],begin_paths[2][193];
''')
    replace_once('memset(p,0,size);', 'memset(p,(int)++session_random,size);')
    replace_once('if(queue==commands){if(!command_reads)', r'''
if(queue==commands && session_case && command_reads>0){
 if(session_case==5 && command_reads==1){
  /* Resume the actual recorder after the network cleanup deadline. */
  assert(state.recording && audio_queue && voice_reserved && !held);
  record_active=1;record(NULL);record_active=0;late_resumed=true;
  command_reads++;return pdFALSE;
 }
 if(session_attempt==0){
  snprintf(first_message,sizeof first_message,"%s",state.message);
  first_finish=finish_calls;first_abort=abort_calls;first_chunks=chunk_attempts;
  first_reads=capture_reads;first_writes=playback_calls;
  first_clean=!state.busy&&!state.recording&&!state.playing&&!audio_queue&&!queue_live&&!voice_reserved&&!transport_open;
  if(session_retry && first_clean){
   session_attempt=1;mode=8;
   pocket_voice_begin_model(1,POCKET_MODEL_DEFAULT);
   assert(state.busy && voice_reserved && held && !cancelled);
   *(unsigned *)out=1;command_reads++;return pdTRUE;
  }
 }
 longjmp(stopped,1);
}
if(queue==commands){if(!command_reads)''')
    replace_once('(void)priority;(void)handle;if(!strcmp(name,"pocket_record")){', r'''
if(session_attempt==0 && (session_case==1 || session_case==2) && !strcmp(name,"pocket_record"))pocket_voice_release();
(void)priority;(void)handle;if(!strcmp(name,"pocket_record")){''')
    replace_once('return fixture_item(value,key);', r'''
 if(!session_attempt && !strcmp(key,"backend")){
  static cJSON unavailable={"unavailable",0,0,2};
  if(session_case==3)return &unavailable;
  if(session_case==4)return NULL;
 }
 return fixture_item(value,key);''')
    replace_once('if(strstr(path,"/abort"))abort_calls++;', r'''
 if(strstr(path,"/abort"))abort_calls++;
 bool begin_request=body && strstr(path,"/audio/") && !strstr(path,"/chunk") && !strstr(path,"/finish") && !strstr(path,"/abort");
 if(session_case && begin_request)snprintf(begin_paths[session_attempt],sizeof begin_paths[session_attempt],"%s",path);
 if((session_case==2 && strstr(path,"/abort")) || (session_case==10 && session_attempt==1 && begin_request)){
  memcpy(out,"{}",3);result->status=session_case==2?401:409;result->length=2;return ESP_OK;
 }
 if(session_case==7 && session_attempt==0 && strstr(path,"/status")){
  transport_failures++;transport_open=false;out[0]=0;result->status=-1;result->length=0;return ESP_FAIL;
 }''')
    replace_once('int main(int argc,char **argv) {', r'''
int main(int argc,char **argv) {
 if(argc>1 && !strncmp(argv[1],"session",7)){
  int which=atoi(argv[1]+7);
  const int modes[]={0,8,8,8,8,7,7,8,16,17,18,19,36};
  CHECK(which>=1 && which<=12,"bad session fixture");
  reset(modes[which]);session_case=which;session_retry=which>=3 && which!=6;
  int ended=setjmp(stopped);if(!ended)network(NULL);
  if(which==6){
   CHECK(state.busy && state.recording && audio_queue && voice_reserved,"unfinished recorder lost its resource ownership");
   CHECK(!finish_calls && !playback_calls && !late_resumed,"unfinished recorder submitted or played audio");
  }else{
   CHECK(first_clean,"terminal session did not release busy/queue/lease/socket ownership");
   CHECK(!state.busy&&!state.recording&&!state.playing&&!audio_queue&&!queue_live&&!voice_reserved&&!transport_open,"worker retained ownership after retry");
   CHECK(!json_live && !audio_bytes && live_allocations()==2,"session retained transient allocations");
   if(which==1){
    CHECK(!first_reads && !first_chunks && !first_finish && first_abort==1,"empty release submitted audio or failed to abort");
    CHECK(!strstr(first_message,"Listening") && !strstr(first_message,"Connecting") && (strstr(first_message,"hold")||strstr(first_message,"Hold")),"zero-frame release retained progress instead of an actionable terminal prompt");
   }else if(which==2){
    CHECK(!first_reads&&!first_finish&&first_abort==1&&strstr(first_message,"Pairing"),"empty release hid abort authorization failure");
   }else{
    CHECK(session_attempt==1 && status_calls==2,"manual retry did not perform a fresh eligibility check in the same worker");
    if(which==3 || which==4){
     CHECK(!first_reads&&!first_chunks&&!first_finish&&!first_abort,"unavailable or malformed backend metadata admitted audio work");
     CHECK(first_message[0] && !strstr(first_message,"Connecting"),"invalid eligibility retained progress");
    }
    if(which==5)CHECK(late_resumed&&!first_finish&&first_abort==1,"late recorder did not recover safely before retry");
    if(which==7)CHECK(!first_finish&&!first_abort&&!first_reads&&strstr(first_message,"Connection"),"failed status submitted work or lost its error");
    if(which==8)CHECK(first_chunks==1&&!first_finish&&first_abort==1&&strstr(first_message,"Connection"),"failed upload was resent or its failure was hidden");
    if(which==9)CHECK(!first_finish&&first_abort==1&&strstr(first_message,"discarded"),"pre-finish cancellation crossed the submission handoff");
    if(which==10){
     CHECK(first_finish==1&&!first_abort&&strstr(first_message,"may continue"),"post-finish cancellation falsely claimed upstream discard");
     CHECK(finish_calls==1 && abort_calls==0 && strstr(state.message,"attention"),"manual retry conflict resubmitted or aborted existing provider work");
    }else{
     CHECK(!strcmp(state.message,"Reply ready") && codec_sleeping && codec_drains>0,"manual retry did not reach drained playback and terminal sleep");
     CHECK(finish_calls==(which>=11?2:1),"finish was omitted or automatically repeated");
    }
    if(which==11)CHECK(first_finish==1&&first_writes==1&&strstr(first_message,"stopped"),"playback cancellation did not stop the first turn");
    if(which==12)CHECK(first_finish==1&&first_writes==1&&strstr(first_message,"stopped"),"failed playback drain claimed success");
    if(begin_paths[0][0] && begin_paths[1][0])CHECK(strcmp(begin_paths[0],begin_paths[1]),"manual retry reused the previous capture identity");
   }
  }
  session_case=0;reset(0);puts("PASS: actual worker preserves terminal feedback, ownership and manual-retry boundaries");return 0;
 }
''')
    return text


class VoiceSessionTests(unittest.TestCase):
    def exercise(self, case):
        original = baseline.HARNESS
        baseline.HARNESS = session_harness()
        try:
            baseline.FirmwareVoiceTests()._exercise([f'session{case}'])
        finally:
            baseline.HARNESS = original

    def test_zero_frame_release_ends_progress_without_submission(self):
        self.exercise(1)

    def test_zero_frame_abort_authorization_error_stays_visible(self):
        self.exercise(2)

    def test_unavailable_backend_rejects_capture_then_accepts_manual_retry(self):
        self.exercise(3)

    def test_missing_backend_metadata_rejects_capture_then_accepts_manual_retry(self):
        self.exercise(4)

    def test_late_recorder_completion_recovers_and_accepts_manual_retry(self):
        self.exercise(5)

    def test_unfinished_recorder_keeps_ownership_and_never_submits(self):
        self.exercise(6)

    def test_status_connection_failure_recovers_on_manual_retry(self):
        self.exercise(7)

    def test_upload_connection_failure_is_not_resent_on_manual_retry(self):
        self.exercise(8)

    def test_pre_finish_cancel_discards_then_allows_manual_retry(self):
        self.exercise(9)

    def test_post_finish_cancel_keeps_uncertainty_and_retry_conflict(self):
        self.exercise(10)

    def test_playback_cancel_releases_resources_for_next_turn(self):
        self.exercise(11)

    def test_playback_drain_failure_recovers_on_next_turn(self):
        self.exercise(12)


if __name__ == '__main__':
    unittest.main()
