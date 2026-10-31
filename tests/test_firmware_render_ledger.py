"""Host verification of callback-owned DMA accounting, not acoustic validation."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "bsp_audio_render.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
int main(void) {
 bsp_audio_render_t s;
 uint8_t buffers[6][960]; int16_t pcm[480];
 for(unsigned i=0;i<480;i++)pcm[i]=(int16_t)(i+1);
 assert(bsp_audio_render_begin(&s,1,2));
 assert(bsp_audio_render_enqueue(&s,1,0,pcm,480));
 assert(s.accepted==480 && s.dma_completed==0);
 for(unsigned i=0;i<6;i++) {
  memset(buffers[i],0x7f,960);
  assert(bsp_audio_render_complete(&s,1,buffers[i],960));
  assert(s.dma_completed==0);
 }
 assert(buffers[0][0]==1 && buffers[0][2]==1);
 assert(buffers[2][0]==0 && buffers[2][959]==0);
 assert(bsp_audio_render_complete(&s,1,buffers[0],960));
 assert(s.dma_completed==240);
 assert(bsp_audio_render_complete(&s,1,buffers[1],960));
 assert(s.dma_completed==480);
 for(unsigned i=2;i<6;i++)assert(bsp_audio_render_complete(&s,1,buffers[i],960));
 for(unsigned i=0;i<6;i++)assert(bsp_audio_render_complete(&s,1,buffers[i],960));
 assert(s.dma_completed==480); /* idle ring never acknowledges new source */
 assert(!bsp_audio_render_enqueue(&s,1,0,pcm,480));
 assert(bsp_audio_render_enqueue(&s,1,480,pcm,480));
 bsp_audio_render_stop(&s);
 assert(s.dma_completed==480 && !s.active);
 assert(!bsp_audio_render_complete(&s,1,buffers[0],960));
 assert(!bsp_audio_render_enqueue(&s,1,960,pcm,480));
 assert(bsp_audio_render_begin(&s,2,1));
 assert(!bsp_audio_render_complete(&s,1,buffers[0],480));
 assert(bsp_audio_render_enqueue(&s,2,0,pcm,480));
 for(unsigned i=0;i<4;i++)assert(bsp_audio_render_enqueue(&s,2,480*(i+1),pcm,480)==(i<3));
 assert(s.accepted==1920); /* fixed bounded queue */
 assert(bsp_audio_render_complete(&s,2,buffers[0],480));
 assert(!bsp_audio_render_complete(&s,2,buffers[0],480)); /* early repeat */
 assert(s.failed && s.dma_completed==0);
 assert(bsp_audio_render_begin(&s,3,2));
 assert(!bsp_audio_render_complete(&s,3,buffers[0],480)); /* layout mismatch */
 assert(s.failed);
 assert(bsp_audio_render_begin(&s,4,2));
 for(unsigned i=0;i<6;i++)assert(bsp_audio_render_complete(&s,4,buffers[i],960));
 assert(!bsp_audio_render_complete(&s,4,buffers[1],960)); /* skipped descriptor */
 assert(s.failed);
 assert(bsp_audio_render_begin(&s,5,2));
 s.accepted=UINT32_MAX-100;
 assert(!bsp_audio_render_enqueue(&s,5,s.accepted,pcm,480));
 return 0;
}
'''

class RenderLedgerTests(unittest.TestCase):
    def test_real_source_offsets_follow_completed_descriptor_identity(self):
        cc=shutil.which('gcc')
        if not cc: self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            d=pathlib.Path(d)
            (d/'main.c').write_text(HARNESS)
            r=subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-pedantic',
                '-I'+str(ROOT/'firmware/components/bsp/include'),str(d/'main.c'),
                str(ROOT/'firmware/components/bsp/src/bsp_audio_render.c'),'-o',str(d/'test')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            r=subprocess.run([str(d/'test')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
