from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
HARNESS=r'''
#include "pocket_qualification_control.h"
#include <assert.h>
#include <string.h>
#include <stdlib.h>
static uint32_t crc_(uint32_t c_,const uint8_t*p,size_t n){while(n--){c_^=*p++;for(unsigned i=0;i<8;i++)c_=(c_>>1)^((0u-(c_&1u))&0xedb88320u);}return c_;}
static pocket_qualification_control c;
static uint64_t now=100;
static size_t packet(uint8_t kind,uint8_t profile,uint64_t nonce,uint32_t seq,uint8_t*out){
 uint8_t budget[16];pocket_qualification_put_u32(budget,PQ_RATE);pocket_qualification_put_u32(budget+4,pocket_qualification_frames(profile));pocket_qualification_put_u32(budget+8,PQ_MAX_PAYLOAD);pocket_qualification_put_u32(budget+12,PQ_MAX_WIRE_BYTES);
 pocket_qualification_packet p={.kind=kind,.profile=profile,.nonce=nonce,.sequence=seq,.payload=budget,.bytes=kind==PQ_ARM?16:0};return pocket_qualification_encode(&p,out,PQ_MAX_RECORD);
}
static bool send_(uint8_t kind,uint64_t nonce,uint32_t seq){uint8_t b[PQ_MAX_RECORD];size_t n=packet(kind,PQ_SILENT,nonce,seq,b);assert(n);return pocket_qualification_control_feed(&c,b,n,now);}
static void start(void){assert(pocket_qualification_control_init(&c));assert(send_(PQ_ARM,123,0));now++;assert(send_(PQ_KEEPALIVE,123,1));}
static uint32_t hold(void){now++;uint32_t g=pocket_qualification_control_button(&c,true,true,false,now);now+=300;assert(send_(PQ_KEEPALIVE,123,2));assert(pocket_qualification_control_arm(&c,now,g,now));return g;}
static uint32_t confirm(void){pocket_qualification_control_button(&c,true,false,true,++now);return pocket_qualification_control_button(&c,true,true,false,++now);}
int main(int argc,char**argv){assert(argc==2);int m=atoi(argv[1]);
 if(m==0){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(pocket_qualification_control_dispatch(&c,now));assert(pocket_qualification_control_finish(&c,true,true,0));assert(c.state.phase==PQC_TERMINAL&&c.state.cleanup_verified);assert(!pocket_qualification_control_init(&c));assert(!send_(PQ_ARM,123,0));return 0;}
 if(m==1){assert(pocket_qualification_control_init(&c));uint8_t b[PQ_MAX_RECORD];size_t n=packet(PQ_ARM,PQ_AUDIO,123,0,b);for(size_t i=0;i<n;i++)assert(pocket_qualification_control_feed(&c,b+i,1,now));assert(c.state.profile==PQ_AUDIO);return 0;}
 if(m==2){assert(pocket_qualification_control_init(&c));uint8_t b[PQ_MAX_RECORD];size_t n=packet(PQ_ARM,PQ_SILENT,123,0,b);b[n-1]^=1;assert(!pocket_qualification_control_feed(&c,b,n,now));assert(c.state.phase==PQC_TERMINAL);assert(!send_(PQ_ARM,123,0));return 0;}
 if(m==3){assert(pocket_qualification_control_init(&c));uint8_t b[257]={0};assert(!pocket_qualification_control_feed(&c,b,sizeof b,now));return 0;}
 if(m==4){start();assert(!send_(PQ_KEEPALIVE,124,2));assert(c.state.reason==PQC_NONCE);return 0;}
 if(m==5){start();assert(!send_(PQ_KEEPALIVE,123,1));assert(c.state.reason==PQC_SEQUENCE);return 0;}
 if(m==6){start();now+=500;assert(!pocket_qualification_control_check(&c,now));assert(c.state.reason==PQC_HEARTBEAT_LOST);return 0;}
 if(m==7){assert(pocket_qualification_control_init(&c));uint32_t g=pocket_qualification_control_button(&c,true,true,false,now);assert(send_(PQ_ARM,123,0));assert(send_(PQ_KEEPALIVE,123,1));assert(!pocket_qualification_control_arm(&c,now,g,now));return 0;}
 if(m==8){start();uint32_t g=hold();g=pocket_qualification_control_button(&c,true,true,false,++now);assert(!pocket_qualification_control_admit(&c,now,g,now));return 0;}
 if(m==9||m==10){start();hold();uint32_t g=confirm();uint64_t t=now;if(m==9)pocket_qualification_control_button(&c,false,true,false,++now);else pocket_qualification_control_input_lost(&c);assert(!pocket_qualification_control_admit(&c,t,g,now));assert(c.state.stop);return 0;}
 if(m==11||m==12){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));if(m==11)pocket_qualification_control_button(&c,false,true,false,++now);else pocket_qualification_control_input_lost(&c);assert(c.state.stop&&c.state.owned);assert(!pocket_qualification_control_dispatch(&c,now));assert(pocket_qualification_control_finish(&c,false,false,42));assert(c.state.phase==PQC_QUARANTINED&&c.state.owned&&!c.state.cleanup_verified);return 0;}
 if(m==13){start();assert(!pocket_qualification_control_check(&c,now-1));assert(c.state.reason==PQC_CLOCK);return 0;}
 if(m==14){start();c.state.generation=UINT32_MAX-1;assert(pocket_qualification_control_button(&c,true,true,false,now)==UINT32_MAX);assert(c.state.stop);assert(pocket_qualification_control_button(&c,true,true,false,now)==UINT32_MAX);return 0;}
 if(m==15){start();assert(!send_(PQ_STOP,123,2));assert(c.state.reason==PQC_STOPPED);return 0;}
 if(m==16){start();uint8_t b[PQ_MAX_RECORD];size_t n=packet(PQ_KEEPALIVE,PQ_SILENT,123,2,b);c.state.wire_bytes=PQ_HOST_WIRE_MAX-(uint32_t)n+1;assert(!pocket_qualification_control_feed(&c,b,n,now));assert(c.state.reason==PQC_BUDGET);return 0;}
 if(m==17){start();for(unsigned i=0;i<33;i++){now+=300;if(now>=10100)break;assert(send_(PQ_KEEPALIVE,123,i+2));}now=10100;assert(!pocket_qualification_control_check(&c,now));assert(c.state.reason==PQC_EXPIRED);return 0;}
 if(m==18){assert(pocket_qualification_control_init(&c));assert(send_(PQ_ARM,123,0));uint32_t g=pocket_qualification_control_button(&c,true,true,false,++now);now++;assert(!pocket_qualification_control_arm(&c,now,g,now));return 0;}
 if(m==19){start();hold();uint32_t g=confirm();pocket_qualification_control_button(&c,true,false,true,++now);assert(pocket_qualification_control_admit(&c,now-1,g,now));assert(pocket_qualification_control_dispatch(&c,now));now+=500;assert(!pocket_qualification_control_check(&c,now)&&c.state.owned&&c.state.stop);return 0;}
 if(m==20){start();uint8_t b[PQ_MAX_RECORD], payload[PQ_STATUS_PAYLOAD_BYTES]={0};pocket_qualification_packet p={.kind=PQ_START,.profile=PQ_SILENT,.nonce=123,.payload=payload,.bytes=sizeof payload};size_t n=pocket_qualification_encode(&p,b,sizeof b);assert(n);assert(!pocket_qualification_control_feed(&c,b,n,now));assert(c.state.reason==PQC_WIRE);return 0;}
 if(m==21){start();assert(!send_(PQ_ARM,123,0));return 0;}
 if(m==22){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(pocket_qualification_control_dispatch(&c,now));uint32_t seq=3;while(now+300<45100){now+=300;assert(send_(PQ_KEEPALIVE,123,seq++));}now=45100;assert(!pocket_qualification_control_check(&c,now)&&c.state.reason==PQC_EXPIRED&&c.state.owned);return 0;}
 if(m==23){assert(pocket_qualification_control_init(&c));uint8_t b[80];size_t n=packet(PQ_ARM,PQ_SILENT,123,0,b);n+=packet(PQ_KEEPALIVE,PQ_SILENT,123,1,b+n);assert(n==80);assert(pocket_qualification_control_feed(&c,b,n,now));assert(c.heartbeat_seen&&c.used==0&&c.state.host_sequence==1);return 0;}
 if(m==24){start();assert(!pocket_qualification_control_finish(&c,true,true,0));assert(c.state.phase==PQC_HOST_ARMED);return 0;}
 if(m==25){start();uint32_t g=pocket_qualification_control_button(&c,true,true,false,++now);assert(!pocket_qualification_control_arm(&c,now-1,g,now));return 0;}
 if(m==26){assert(pocket_qualification_control_init(&c));now=UINT64_MAX-100;assert(!send_(PQ_ARM,123,0));assert(c.state.reason==PQC_CLOCK);return 0;}
 if(m==27){start();assert(!pocket_qualification_control_feed(&c,NULL,1,now));return 0;}
 if(m==28){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(send_(PQ_KEEPALIVE,123,3));assert(pocket_qualification_control_dispatch(&c,now));assert(!send_(PQ_STOP,123,4)&&c.state.owned);assert(pocket_qualification_control_finish(&c,true,true,7));assert(c.state.code==7&&c.state.reason==PQC_STOPPED);return 0;}
 if(m==29){start();uint32_t seq=2;while(c.state.wire_bytes+32<=PQ_HOST_WIRE_MAX)assert(send_(PQ_KEEPALIVE,123,seq++));assert(!send_(PQ_KEEPALIVE,123,seq));assert(c.state.reason==PQC_BUDGET&&c.state.wire_bytes<=PQ_HOST_WIRE_MAX);return 0;}
 if(m==30){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(pocket_qualification_control_finish(&c,true,false,9));assert(c.state.phase==PQC_TERMINAL&&!c.state.owned&&!c.state.cleanup_verified&&c.state.safe_release&&c.state.code==9);return 0;}
 if(m==31){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(!pocket_qualification_control_finish(&c,false,true,9));assert(c.state.phase==PQC_QUARANTINED&&c.state.owned&&!c.state.cleanup_verified&&!c.state.safe_release);return 0;}
 if(m==32){assert(pocket_qualification_control_init(&c));uint8_t b[PQ_MAX_RECORD];size_t n=packet(PQ_ARM,PQ_SILENT,123,0,b);pocket_qualification_put_u32(b+36,999);pocket_qualification_put_u32(b+28,crc_(crc_(UINT32_MAX,b,28),b+32,n-32)^UINT32_MAX);assert(!pocket_qualification_control_feed(&c,b,n,now)&&c.state.reason==PQC_WIRE);return 0;}
 if(m==33){assert(pocket_qualification_control_init(&c));uint8_t b[PQ_MAX_RECORD],payload[PQ_CAPTURE_PAYLOAD_BYTES]={0};pocket_qualification_packet p={.kind=PQ_PCM,.profile=PQ_SILENT,.nonce=123,.sequence=1,.payload=payload,.bytes=sizeof payload};size_t n=pocket_qualification_encode(&p,b,sizeof b);assert(n==1024);for(size_t i=0;i<n;i+=256){bool result=pocket_qualification_control_feed(&c,b+i,256,now);assert(result==(i+256<n));}assert(c.state.reason==PQC_WIRE&&c.used<=PQ_MAX_RECORD);return 0;}
 return 2;
}
'''
class ControlTests(unittest.TestCase):
 def test_native_control(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(HARNESS);core=ROOT/'firmware/core'
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-I',str(core),str(p/'test.c'),str(core/'pocket_qualification_control.c'),str(core/'pocket_qualification_protocol.c'),'-o',str(p/'test')],check=True)
   for mode in range(34):
    with self.subTest(mode=mode):subprocess.run([str(p/'test'),str(mode)],check=True)
