"""Sample-pair copying and discontinuity guards, no acoustic claims."""
import pathlib, shutil, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
HARNESS=r'''
#include "bsp_audio_capture.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
int main(void) {
 bsp_audio_capture_t s; bsp_audio_capture_frame_t out;
 uint8_t buffers[6][960];
 for(unsigned d=0;d<6;d++)for(unsigned i=0;i<960;i++)buffers[d][i]=(uint8_t)(i+d);
 assert(bsp_audio_capture_begin(&s,7));
 for(unsigned i=0;i<6;i++) {
  assert(bsp_audio_capture_complete(&s,7,buffers[i],960,10000*(i+1),i*240));
  assert(bsp_audio_capture_poll(&s,&out));
  assert(out.epoch==7 && out.sequence==i && out.sample_offset==240*i && out.dma_consumed==240*i);
  assert(memcmp(out.pcm,buffers[i],960)==0);
 }
 assert(!bsp_audio_capture_poll(&s,&out));
 assert(bsp_audio_capture_complete(&s,7,buffers[0],960,70000,1440));
 memset(buffers[0],0,960);assert(bsp_audio_capture_poll(&s,&out));
 assert(out.pcm[1]==1); /* callback copies, not a DMA pointer */
 assert(!bsp_audio_capture_complete(&s,7,buffers[2],960,80000,1680));
 assert(s.failed && !s.active); /* missing callback is never contiguous */
 assert(bsp_audio_capture_begin(&s,8));
 assert(!bsp_audio_capture_complete(&s,7,buffers[0],960,90000,0));
 assert(s.active && !s.failed); /* stale epoch rejected, no mutation */
 assert(!bsp_audio_capture_complete(&s,8,buffers[0],480,90000,0));
 assert(s.failed);
 assert(bsp_audio_capture_begin(&s,9));
 for(unsigned i=0;i<BSP_CAPTURE_QUEUE_FRAMES;i++)assert(bsp_audio_capture_complete(&s,9,buffers[i%6],960,10000*(i+1),0));
 assert(!bsp_audio_capture_complete(&s,9,buffers[BSP_CAPTURE_QUEUE_FRAMES%6],960,10000*(BSP_CAPTURE_QUEUE_FRAMES+1),0));
 assert(s.failed); /* bounded queue: no silent discard */
 assert(bsp_audio_capture_begin(&s,10));
 assert(bsp_audio_capture_complete(&s,10,buffers[0],960,10000,0));
 assert(!bsp_audio_capture_complete(&s,10,buffers[1],960,60000,0)); /* full-ring IRQ gap guard */
 assert(s.failed);
 assert(bsp_audio_capture_begin(&s,11));
 assert(bsp_audio_capture_complete(&s,11,buffers[0],960,10000,0));
 assert(!bsp_audio_capture_complete(&s,11,buffers[0],960,20000,0)); /* premature duplicate */
 assert(bsp_audio_capture_begin(&s,12));s.samples=UINT32_MAX-100;
 assert(!bsp_audio_capture_complete(&s,12,buffers[0],960,10000,0));
 assert(bsp_audio_capture_begin(&s,13));bsp_audio_capture_stop(&s);
 assert(!bsp_audio_capture_complete(&s,13,buffers[0],960,10000,0));
 assert(!bsp_audio_capture_poll(&s,&out));
 return 0;
}
'''
class ReferenceCaptureTests(unittest.TestCase):
 def test_pairs_are_owned_bounded_and_fail_on_discontinuity(self):
  cc=shutil.which('gcc')
  if not cc:self.skipTest('GCC unavailable')
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'t.c').write_text(HARNESS)
   r=subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic','-I'+str(ROOT/'firmware/components/bsp/include'),str(p/'t.c'),str(ROOT/'firmware/components/bsp/src/bsp_audio_capture.c'),'-o',str(p/'t')],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'t')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
