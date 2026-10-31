"""Actual allocation-free C qualification core/protocol, no device operations."""
from pathlib import Path
import subprocess,tempfile,unittest,struct,zlib
HARNESS=r'''
#include "pocket_qualification.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef struct {uint64_t now;unsigned begins,ends,captures,emits,submits,accepted,wireseq,wirebytes;int mode;bool stop;uint32_t busy_offset;int16_t busy_pcm[240];bool have_busy;}fake;
static bool begin(void*v,uint32_t epoch,unsigned volume,pocket_qualification_begin_ownership*acquired){fake*f=v;assert(epoch==7&&volume<=5);f->begins++;*acquired=f->mode==4?PQ_BEGIN_UNKNOWN:PQ_BEGIN_ACQUIRED;if(f->mode==27){*acquired=PQ_BEGIN_UNTOUCHED;f->stop=true;return false;}if(f->mode==3)f->now+=2000;if(f->mode==2)f->stop=true;return f->mode!=4&&f->mode!=5;}
static int capture(void*v,pocket_qualification_capture*p){fake*f=v;if(f->mode==22)return 0;f->now+=10;if(f->mode==6)return -1;if(f->mode==12)f->now=0;*p=(pocket_qualification_capture){.epoch=7,.sequence=f->captures,.sample_offset=f->captures*240,.dma_consumed=f->accepted,.queue_frames=PQ_QUEUE_UNKNOWN};if(f->mode==10)p->sequence++;if(f->mode==13)p->dma_consumed=f->accepted+1;f->captures++;if(f->mode==25)f->stop=true;return 1;}
static int submit(void*v,uint32_t off,const int16_t*p,size_t n){fake*f=v;f->submits++;assert(off==f->accepted&&n==240);for(unsigned i=0;i<240;i++)assert(p[i]==pocket_qualification_source_sample(off+i)&&p[i]>=-512&&p[i]<=512);if(f->mode==9&&f->submits<4){if(f->have_busy){assert(f->busy_offset==off&&!memcmp(f->busy_pcm,p,480));}else{f->have_busy=true;f->busy_offset=off;memcpy(f->busy_pcm,p,480);}return 0;}if(f->mode==14){f->stop=true;return 0;}if(f->mode==15||f->mode==23)return 0;f->accepted+=240;return 1;}
static bool end(void*v,uint32_t epoch){fake*f=v;assert(epoch==7);f->ends++;if(f->mode==17)f->now+=500;if(f->mode==18)f->now=0;return f->mode!=11;}
static int emit(void*v,const uint8_t*p,size_t n){fake*f=v;pocket_qualification_packet packet;assert(pocket_qualification_decode(p,n,&packet)==PQ_WIRE_OK);assert(packet.nonce==123&&packet.sequence==f->wireseq);if(packet.kind==PQ_START)assert(!f->begins);if(packet.kind==PQ_TERMINAL)assert(f->ends==1||f->mode==4||f->mode==16||f->mode==27);if(f->mode==7&&packet.kind==PQ_PCM)return -1;if((f->mode==8||f->mode==19)&&packet.kind==PQ_PCM)return 0;if(f->mode==26&&packet.kind==PQ_PCM)f->stop=true;f->wireseq++;f->wirebytes+=(unsigned)n;f->emits++;return 1;}
static bool stopped(void*v){return ((fake*)v)->stop;}
static uint64_t now(void*v){return ((fake*)v)->now;}
static void idle(void*v){fake*f=v;if(f->mode!=19&&f->mode!=22&&f->mode!=23)f->now++;}
int main(int argc,char**argv){assert(argc==2);int mode=atoi(argv[1]);
 if(mode==20){for(uint32_t i=0;i<1440000;i++){int x=pocket_qualification_source_sample(i);assert(x>=-512&&x<=512);if(!pocket_qualification_source_window(i))assert(x==0);}for(uint32_t i=96000;i<96020;i++)printf("%d\n",pocket_qualification_source_sample(i));return 0;}
 if(mode==24){uint8_t in[1100]={0};pocket_qualification_packet p;for(size_t n=0;n<32;n++)assert(pocket_qualification_decode(in,n,&p)==PQ_WIRE_INCOMPLETE);uint32_t x=7;for(unsigned k=0;k<2000;k++){for(size_t i=0;i<sizeof in;i++){x^=x<<13;x^=x>>17;x^=x<<5;in[i]=(uint8_t)x;}size_t n=x%sizeof in;pocket_qualification_wire_result r=pocket_qualification_decode(in,n,&p);if(r==PQ_WIRE_OK)assert(p.payload==in+32&&p.bytes+32==n&&p.bytes<=1024);}return 0;}
 if(mode==21){uint8_t arm[16],out[PQ_MAX_RECORD];pocket_qualification_put_u32(arm,24000);pocket_qualification_put_u32(arm+4,1000);pocket_qualification_put_u32(arm+8,1024);pocket_qualification_put_u32(arm+12,3145728);pocket_qualification_packet p={.nonce=0x1122334455667788ull,.kind=PQ_ARM,.profile=PQ_SILENT,.payload=arm,.bytes=16};size_t n=pocket_qualification_encode(&p,out,sizeof out);assert(n==48);for(size_t i=0;i<n;i++)printf("%02x",out[i]);puts("");pocket_qualification_packet q;assert(pocket_qualification_decode(out,n,&q)==PQ_WIRE_OK);out[9]^=1;assert(pocket_qualification_decode(out,n,&q)==PQ_WIRE_CRC);assert(pocket_qualification_decode(out,n-1,&q)==PQ_WIRE_INCOMPLETE);p.profile=3;assert(!pocket_qualification_encode(&p,out,sizeof out));p.profile=1;p.nonce=0;assert(!pocket_qualification_encode(&p,out,sizeof out));return 0;}
 fake f={.now=100,.mode=mode};if(mode==16)f.stop=true;
 pocket_qualification q={0};pocket_qualification_config c={.nonce=123,.epoch=7,.profile=(mode==1||mode==9||mode==14||mode==15||mode==23||mode==25||mode==26)?PQ_AUDIO:PQ_SILENT,.volume=(mode==1||mode==9||mode==14||mode==15||mode==23||mode==25||mode==26)?5:0};
 pocket_qualification_ops o={&f,begin,capture,submit,end,emit,stopped,now,idle};pocket_qualification_result r=pocket_qualification_run(&q,&c,&o);
 assert(r.wire_bytes<=PQ_MAX_WIRE_BYTES&&r.capture_frames<=pocket_qualification_frames(c.profile)&&r.source_samples<=576000&&r.stimulus_samples<=144000&&r.peak<=512);
 if(mode==0||mode==1||mode==9||mode==17){assert(r.code==PQ_COMPLETE&&r.cleanup==PQ_CLEANUP_VERIFIED&&r.terminal_emitted&&r.source_complete);assert(r.capture_frames==(c.profile==PQ_AUDIO?2400:1000));assert(r.source_samples==(c.profile==PQ_AUDIO?576000:0));assert(r.stimulus_samples==(c.profile==PQ_AUDIO?144000:0));}
 else assert(r.code!=PQ_COMPLETE);
 if(mode==17)assert(r.elapsed_ms==10500);
 if(mode==18)assert(r.code==PQ_CLOCK_REGRESSION);
 if(mode==27)assert(!f.ends&&r.cleanup==PQ_NOT_ACQUIRED&&r.code==PQ_STOPPED);
 if(mode==4)assert(!f.ends&&r.cleanup==PQ_CLEANUP_UNKNOWN);
 if(mode==11)assert(r.cleanup==PQ_CLEANUP_UNKNOWN);
 if(mode==7)assert(!r.terminal_emitted);
 if(mode==16)assert(!f.begins&&!f.ends);
 if(mode==2||mode==3)assert(!f.captures&&!f.submits);
 if(mode==25||mode==26)assert(!f.submits&&r.code==PQ_STOPPED);
 assert(pocket_qualification_run(&q,&c,&o).code==PQ_INVALID_ARGUMENT);return 0;}
'''
def source_sample(t):
 starts=(96000,240000,432000);local=next((t-s for s in starts if s<=t<s+48000),None)
 if local is None:return 0
 def white(i):
  x=(i+0x9e3779b9)&0xffffffff;x^=x>>16;x=(x*0x7feb352d)&0xffffffff;x^=x>>15;x=(x*0x846ca68b)&0xffffffff;x^=x>>16;return (x&65535)-32768
 def trunc(a,b):return (abs(a)//b)*(-1 if a<0 else 1)
 base=trunc(sum(white(t-i) for i in range(8))-sum(white(t-i) for i in range(8,16)),1024)
 return trunc(base*min(local,47999-local,240),240)
class QualificationCoreTests(unittest.TestCase):
 def test_actual_c(self):
  root=Path(__file__).resolve().parents[1];core=root/'firmware/core'
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(HARNESS)
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(core),str(p/'test.c'),str(core/'pocket_qualification.c'),str(core/'pocket_qualification_protocol.c'),'-o',str(p/'test')],check=True)
   for mode in list(range(20))+[20,21,22,23,24,25,26,27]:
    with self.subTest(mode=mode):
     out=subprocess.check_output([str(p/'test'),str(mode)],text=True,timeout=3)
     if mode==20:self.assertEqual(list(map(int,out.split())),[source_sample(t) for t in range(96000,96020)])
     if mode==21:
      b=bytes.fromhex(out.strip());self.assertEqual(struct.unpack_from('<I',b,28)[0],zlib.crc32(b[32:],zlib.crc32(b[:28])));self.assertEqual(struct.unpack('<4sBBBBQIIHHI',b[:32])[:-1],(b'HPQ1',1,1,1,0,0x1122334455667788,0,0,16,0))
