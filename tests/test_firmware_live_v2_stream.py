"""Paired-capture v2 wire semantics; no microphone, speaker or provider I/O."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
HARNESS=r'''
#include "pocket_live_v2_stream.h"
#include <assert.h>
#include <string.h>
int main(void){
 uint8_t pcm[960]={0x34,0x12,0xcc,0xed}, wire[976];size_t n=0;
 pocket_live_v2_packet p={1,0,0,0,pcm,sizeof pcm},d={0};
 assert(pocket_live_v2_pack(&p,wire,sizeof wire,&n)==POCKET_LIVE_V2_OK&&n==976);
 assert(!memcmp(wire,"PL\x02\x01",4)&&wire[16]==0x34&&wire[18]==0xcc);
 assert(pocket_live_v2_unpack(wire,n,&d)==POCKET_LIVE_V2_OK&&d.pcm_bytes==960&&d.kind==1);
 pocket_live_v2_state s; pocket_live_v2_init(&s,1);
 assert(pocket_live_v2_accept(&s,&d,480)==POCKET_LIVE_V2_OK&&s.next_sample_offset==240);
 assert(pocket_live_v2_accept(&s,&d,480)==POCKET_LIVE_V2_OUT_OF_ORDER);
 p.sequence=1;p.sample_offset=240;p.dma_consumed=480;
 assert(pocket_live_v2_accept(&s,&p,479)==POCKET_LIVE_V2_BAD_CREDIT&&s.next_sequence==1);
 assert(pocket_live_v2_accept(&s,&p,480)==POCKET_LIVE_V2_OK&&s.dma_consumed==480);
 p.sequence=2;p.sample_offset=480;p.dma_consumed=479;
 assert(pocket_live_v2_accept(&s,&p,480)==POCKET_LIVE_V2_BAD_CREDIT);
 p.dma_consumed=480;p.pcm_bytes=956;
 assert(pocket_live_v2_pack(&p,wire,sizeof wire,&n)==POCKET_LIVE_V2_INVALID&&n==0);
 p.pcm_bytes=960;p.sample_offset=UINT32_MAX-239;
 assert(pocket_live_v2_pack(&p,wire,sizeof wire,&n)==POCKET_LIVE_V2_INVALID);
 p=(pocket_live_v2_packet){2,7,480,0,pcm,4};
 assert(pocket_live_v2_pack(&p,wire,sizeof wire,&n)==POCKET_LIVE_V2_OK&&n==20);
 const uint8_t expected[]={0x50,0x4c,2,2,0,0,0,7,0,0,1,0xe0,0,0,0,0,0x34,0x12,0xcc,0xed};
 assert(!memcmp(expected,wire,n));
 pocket_live_v2_init(&s,2);p.sequence=0;p.sample_offset=0;
 assert(pocket_live_v2_accept(&s,&p,0)==POCKET_LIVE_V2_OK&&s.next_sample_offset==2);
 p.dma_consumed=1;assert(pocket_live_v2_pack(&p,wire,sizeof wire,&n)==POCKET_LIVE_V2_INVALID);
 wire[2]=1;assert(pocket_live_v2_unpack(wire,20,&d)==POCKET_LIVE_V2_INVALID);
 return 0;
}
'''
class FirmwareLiveV2StreamTests(unittest.TestCase):
    def test_paired_offsets_dma_credit_and_version_isolation(self):
        root=Path(__file__).resolve().parents[1];source=root/'firmware/core/pocket_live_v2_stream.c'
        self.assertTrue(source.exists(),'v2 codec missing')
        cc=shutil.which('gcc')
        if not cc:self.skipTest('GCC unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(HARNESS)
            r=subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic','-O2',
                '-I'+str(root/'firmware/core'),str(p/'test.c'),str(source),'-o',str(p/'test')],capture_output=True,text=True,timeout=20)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
