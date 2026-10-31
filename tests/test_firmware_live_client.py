"""Offline tests for the inactive Live client core, not a device/WSS worker."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HARNESS = r'''
#include "pocket_live_client.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
 assert(argc==2); int test=atoi(argv[1]);
 pocket_live_client c; pocket_live_client_init(&c);
 char hello[256]; uint8_t pcm[960]={1,2}, wire[976]; size_t size=0;
 const uint8_t *play=NULL; size_t bytes=0;
 if(test==1){
  assert(pocket_live_client_begin(&c,"chat_1",hello,sizeof hello));
  assert(!strcmp(hello,"{\"type\":\"start\",\"version\":1,\"rate\":24000,\"channels\":1,\"format\":\"pcm_s16le\",\"chat_id\":\"chat_1\"}"));
  assert(c.phase==POCKET_LIVE_STARTING && c.queued_frames==0);
  assert(!pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
  assert(!pocket_live_client_begin(&c,"chat_1",hello,sizeof hello));
  assert(pocket_live_client_ready(&c));
  assert(pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
  assert(size==976 && wire[3]==1 && wire[7]==0 && wire[15]==0);
 }else if(test==2){
  const char *bad[]={"", "a\"b", "a/b", "a b", "a\nb", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
  for(size_t i=0;i<sizeof bad/sizeof bad[0];i++){
   assert(!pocket_live_client_begin(&c,bad[i],hello,sizeof hello));assert(c.phase==POCKET_LIVE_IDLE);
  }
  memset(hello,0x5a,sizeof hello);
  assert(!pocket_live_client_begin(&c,"chat",hello,5));assert(c.phase==POCKET_LIVE_IDLE);
  assert(!pocket_live_client_begin(NULL,"chat",hello,sizeof hello));
  assert(!pocket_live_client_begin(&c,NULL,hello,sizeof hello));
 }else{
  assert(pocket_live_client_begin(&c,"chat",hello,sizeof hello));assert(pocket_live_client_ready(&c));
  pocket_live_packet p={2,0,0,0,pcm,sizeof pcm};
  if(test==3){
   for(unsigned i=0;i<4;i++){
    p.sequence=i;p.sample_offset=i*480;pcm[0]=(uint8_t)i;
    assert(pocket_live_pack(&p,wire,sizeof wire,&size)==POCKET_LIVE_OK);
    assert(pocket_live_client_speaker(&c,wire,size));
   }
   assert(c.queued_frames==4 && c.submitted_samples==0 && c.rendered_samples==0);
   for(unsigned i=0;i<4;i++){
    assert(pocket_live_client_playback_peek(&c,&play,&bytes));assert(bytes==960 && play[0]==i);
    assert(pocket_live_client_playback_submitted(&c));assert(c.rendered_samples==0);
   }
   assert(c.submitted_samples==1920 && !c.queued_frames);
   assert(!pocket_live_client_playback_submitted(&c));
  }else if(test==4){
   for(unsigned i=0;i<5;i++){
    p.sequence=i;p.sample_offset=i*480;assert(pocket_live_pack(&p,wire,sizeof wire,&size)==POCKET_LIVE_OK);
    assert(pocket_live_client_speaker(&c,wire,size)==(i<4));
   }
   assert(c.phase==POCKET_LIVE_FAILED);assert(!pocket_live_client_playback_peek(&c,&play,&bytes));
   assert(!pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
  }else if(test==5){
   assert(pocket_live_pack(&p,wire,sizeof wire,&size)==POCKET_LIVE_OK);
   assert(pocket_live_client_speaker(&c,wire,size));assert(pocket_live_client_playback_submitted(&c));
   assert(!pocket_live_client_rendered(&c,481) && c.rendered_samples==0);
   assert(pocket_live_client_rendered(&c,240));assert(!pocket_live_client_rendered(&c,239));
   assert(pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
   pocket_live_packet d;assert(pocket_live_unpack(wire,size,&d)==POCKET_LIVE_OK);
   assert(d.played_samples==240 && d.sequence==0 && d.sample_offset==0);
   assert(pocket_live_client_rendered(&c,480));
   assert(pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
   assert(pocket_live_unpack(wire,size,&d)==POCKET_LIVE_OK);
   assert(d.played_samples==480 && d.sequence==1 && d.sample_offset==480);
  }else if(test==6){
   assert(pocket_live_pack(&p,wire,sizeof wire,&size)==POCKET_LIVE_OK);
   assert(pocket_live_client_speaker(&c,wire,size));
   assert(!pocket_live_client_speaker(&c,wire,size));assert(c.phase==POCKET_LIVE_FAILED);
   pocket_live_client_closed(&c);assert(c.phase==POCKET_LIVE_FAILED);
  }else if(test==7){
   assert(pocket_live_pack(&p,wire,sizeof wire,&size)==POCKET_LIVE_OK);
   assert(pocket_live_client_speaker(&c,wire,size));
   pocket_live_client_stop(&c);assert(c.phase==POCKET_LIVE_STOPPING);
   assert(!pocket_live_client_playback_peek(&c,&play,&bytes));
   assert(!pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
   assert(!pocket_live_client_ready(&c));
   pocket_live_client_closed(&c);assert(c.phase==POCKET_LIVE_CLOSED);
   assert(!pocket_live_client_begin(&c,"chat",hello,sizeof hello));
  }else if(test==8){
   assert(!pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,1,&size));
   assert(c.microphone.next_sequence==0);
   assert(pocket_live_client_microphone(&c,pcm,sizeof pcm,wire,sizeof wire,&size));
   assert(wire[7]==0);
   assert(sizeof c <= 4200);
  }else assert(0);
 }
 return 0;
}
'''

class FirmwareLiveClientTests(unittest.TestCase):
    def run_case(self, case):
        compiler = shutil.which('gcc')
        if not compiler:
            self.skipTest('GCC unavailable')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'client.c').write_text(HARNESS)
            binary = work / 'client'
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wconversion', '-Wshadow', '-pedantic', '-O2',
                '-I' + str(root / 'firmware/core'), '-I' + str(root / 'firmware/main'),
                str(work / 'client.c'), str(root / 'firmware/core/pocket_live_stream.c'),
                str(root / 'firmware/main/pocket_live_client.c'), '-o', str(binary)],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary), str(case)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_handshake_and_readiness_gate(self): self.run_case(1)
    def test_chat_id_injection_and_capacity_are_rejected(self): self.run_case(2)
    def test_fifo_and_submission_do_not_claim_rendering(self): self.run_case(3)
    def test_backpressure_fails_closed(self): self.run_case(4)
    def test_measured_rendered_count_is_separate_and_bounded(self): self.run_case(5)
    def test_replay_fails_session(self): self.run_case(6)
    def test_stop_closure_and_no_implicit_restart(self): self.run_case(7)
    def test_failed_pack_keeps_sequence_and_memory_is_bounded(self): self.run_case(8)
