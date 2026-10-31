from pathlib import Path
import subprocess,tempfile,unittest
from test_qualification_control import ROOT,HARNESS
# Reuse the actual A harness utilities, replacing only main for B cases.
PRE=HARNESS[:HARNESS.index('int main(int argc')]
POST=r'''
static uint8_t record[PQ_MAX_RECORD];
static size_t tls_packet(const char*host,const char*token,const char*ca,uint32_t duration){
 uint8_t payload[PQ_MAX_PAYLOAD]={0};size_t hn=strlen(host),tn=strlen(token),cn=strlen(ca);
 uint32_t words[9]={24000,60000,1440000,1024,3145728,8443,duration,(uint32_t)hn|((uint32_t)tn<<16),(uint32_t)cn};
 for(unsigned i=0;i<9;i++)pocket_qualification_put_u32(payload+4*i,words[i]);
 memcpy(payload+36,host,hn);memcpy(payload+36+hn,token,tn);memcpy(payload+36+hn+tn,ca,cn);
 pocket_qualification_packet p={.kind=PQ_ARM_TLS,.profile=PQ_SILENT,.nonce=123,.payload=payload,.bytes=36+hn+tn+cn};return pocket_qualification_encode(&p,record,sizeof record);
}
static bool feed_record(size_t n){assert(n);for(size_t i=0;i<n;){size_t k=n-i;if(k>256)k=256;if(!pocket_qualification_control_feed(&c,record+i,k,now))return false;i+=k;}return true;}
static const char* token="abcdefghijklmnopqrstuvwxyz123456";
static const char* ca="-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n";
static void tls_start(uint32_t duration){assert(pocket_qualification_control_init(&c));assert(feed_record(tls_packet("192.168.1.1",token,ca,duration)));assert(c.state.tls&&c.tls_config_ready);assert(send_(PQ_KEEPALIVE,123,1));}
static bool zero(const void*v,size_t n){const unsigned char*p=v;for(size_t i=0;i<n;i++)if(p[i])return false;return true;}
int main(int argc,char**argv){assert(argc==2);int m=atoi(argv[1]);
 if(m==0){tls_start(60000);assert(zero(c.buffer,sizeof c.buffer));hold();uint32_t g=confirm();uint64_t request=now;assert(pocket_qualification_control_admit(&c,now,g,now));pocket_qualification_tls_config cfg;assert(pocket_qualification_control_take_tls_config(&c,&cfg));assert(!strcmp(cfg.host,"192.168.1.1")&&!strcmp(cfg.token,token)&&!strcmp(cfg.ca,ca)&&cfg.port==8443&&cfg.duration_ms==60000);assert(zero(&c.tls_config,sizeof c.tls_config)&&zero(c.buffer,sizeof c.buffer));assert(!pocket_qualification_control_take_tls_config(&c,&cfg)&&zero(&cfg,sizeof cfg));assert(pocket_qualification_control_dispatch(&c,now));assert(c.state.deadline_ms==request+60000&&c.state.request_ms==request&&c.state.dispatch_deadline_ms==10100);return 0;}
 if(m==1){tls_start(60000);pocket_qualification_tls_config cfg;memset(&cfg,7,sizeof cfg);assert(!pocket_qualification_control_take_tls_config(&c,&cfg)&&zero(&cfg,sizeof cfg)&&c.tls_config_ready);assert(!send_(PQ_STOP,123,2));assert(zero(&c.tls_config,sizeof c.tls_config)&&zero(c.buffer,sizeof c.buffer));assert(!pocket_qualification_control_init(&c));return 0;}
 if(m==2){tls_start(60000);now+=500;assert(!pocket_qualification_control_check(&c,now));assert(zero(&c.tls_config,sizeof c.tls_config)&&zero(c.buffer,sizeof c.buffer));return 0;}
 if(m==3){tls_start(60000);assert(!send_(PQ_KEEPALIVE,123,1));assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==4){tls_start(60000);hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(pocket_qualification_control_dispatch(&c,now));uint64_t end=now+60000;uint32_t seq=3;while(now+300<end){now+=300;assert(send_(PQ_KEEPALIVE,123,seq++));}now=end;assert(!pocket_qualification_control_check(&c,now)&&c.state.reason==PQC_EXPIRED&&c.state.owned);assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==5){tls_start(1);hold();uint32_t g=confirm();uint64_t press=now;now++;assert(!pocket_qualification_control_admit(&c,press,g,now));assert(c.state.reason==PQC_EXPIRED&&!c.state.owned);return 0;}
 if(m==6){assert(pocket_qualification_control_init(&c));assert(!feed_record(tls_packet("8.8.8.8",token,ca,60000)));assert(zero(&c.tls_config,sizeof c.tls_config)&&zero(c.buffer,sizeof c.buffer));return 0;}
 if(m==7){assert(pocket_qualification_control_init(&c));assert(!feed_record(tls_packet("192.168.01.1",token,ca,60000)));return 0;}
 if(m==8){assert(pocket_qualification_control_init(&c));assert(!feed_record(tls_packet("192.168.1.1","abcdefghijklmnopqrstuvwxyz12345=",ca,60000)));return 0;}
 if(m==9){assert(pocket_qualification_control_init(&c));assert(!feed_record(tls_packet("192.168.1.1",token,"not-a-certificate",60000)));return 0;}
 if(m==10){tls_start(60000);hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));assert(!pocket_qualification_control_take_tls_config(&c,&c.tls_config));assert(c.tls_config_ready);assert(pocket_qualification_control_finish(&c,true,false,9));assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==11){tls_start(60000);hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));pocket_qualification_control_input_lost(&c);pocket_qualification_tls_config cfg;assert(!pocket_qualification_control_take_tls_config(&c,&cfg));assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==12){tls_start(60000);hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));uint8_t b[PQ_MAX_RECORD];size_t n=packet(PQ_KEEPALIVE,PQ_SILENT,123,3,b);assert(pocket_qualification_control_feed(&c,b,5,now));pocket_qualification_tls_config cfg;assert(!pocket_qualification_control_take_tls_config(&c,&cfg)&&c.used==5&&c.tls_config_ready);assert(pocket_qualification_control_feed(&c,b+5,n-5,now));assert(pocket_qualification_control_take_tls_config(&c,&cfg));return 0;}
 if(m==13||m==14){assert(pocket_qualification_control_init(&c));char cert[770];const char*prefix="-----BEGIN CERTIFICATE-----\n",*suffix="\n-----END CERTIFICATE-----\n";size_t a=strlen(prefix),b=strlen(suffix);memcpy(cert,prefix,a);memset(cert+a,'A',768-a-b);memcpy(cert+768-b,suffix,b+1);size_t n=tls_packet("172.31.255.255",token,cert,60000);assert(n);if(m==13){assert(feed_record(n));assert(strlen(c.tls_config.ca)==768);}else{pocket_qualification_put_u32(record+32+32,769);record[n++]=0;record[24]=(uint8_t)(n-32);record[25]=(uint8_t)((n-32)>>8);pocket_qualification_put_u32(record+28,crc_(crc_(UINT32_MAX,record,28),record+32,n-32)^UINT32_MAX);assert(!feed_record(n));assert(zero(c.buffer,sizeof c.buffer));}return 0;}
 if(m>=15&&m<=18){assert(pocket_qualification_control_init(&c));size_t n=tls_packet("10.0.0.1",token,ca,60000);unsigned offset=m==15?4:m==16?8:m==17?20:24;uint32_t value=m==15?60001:m==16?1440001:m==17?65536:60001;pocket_qualification_put_u32(record+32+offset,value);pocket_qualification_put_u32(record+28,crc_(crc_(UINT32_MAX,record,28),record+32,n-32)^UINT32_MAX);assert(!feed_record(n));assert(zero(&c.tls_config,sizeof c.tls_config)&&zero(c.buffer,sizeof c.buffer));return 0;}
 if(m==19){assert(pocket_qualification_control_init(&c));size_t n=tls_packet("192.168.1.1",token,ca,60000);record[n-1]^=1;assert(!feed_record(n));assert(zero(c.buffer,sizeof c.buffer)&&zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==20){tls_start(60000);assert(!pocket_qualification_control_check(&c,now-1));assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==21){assert(pocket_qualification_control_init(&c));assert(!feed_record(tls_packet("172.32.0.1",token,ca,60000)));return 0;}
 if(m==22){tls_start(60000);assert(!feed_record(tls_packet("10.0.0.1",token,ca,60000)));assert(zero(&c.tls_config,sizeof c.tls_config));return 0;}
 if(m==23){start();hold();uint32_t g=confirm();assert(pocket_qualification_control_admit(&c,now,g,now));pocket_qualification_tls_config cfg;assert(!pocket_qualification_control_take_tls_config(&c,&cfg)&&!c.state.tls);return 0;}
 if(m==24||m==25){assert(pocket_qualification_control_init(&c));size_t n=tls_packet("192.168.1.1",token,ca,60000);assert(n>100);assert(pocket_qualification_control_feed(&c,record,100,now));assert(c.used==100&&c.state.phase==PQC_IDLE);if(m==25){now+=499;assert(pocket_qualification_control_feed(&c,record+100,1,now));now++;}else now+=500;assert(!pocket_qualification_control_check(&c,now));assert(c.state.phase==PQC_TERMINAL&&zero(c.buffer,sizeof c.buffer)&&zero(&c.tls_config,sizeof c.tls_config));return 0;}
 return 2;
}
'''
TLS_HARNESS=PRE+POST
class TLSControlTests(unittest.TestCase):
 def test_tls_config_control(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);(p/'test.c').write_text(TLS_HARNESS);core=ROOT/'firmware/core'
   subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-I',str(core),str(p/'test.c'),str(core/'pocket_qualification_control.c'),str(core/'pocket_qualification_protocol.c'),'-o',str(p/'test')],check=True)
   for m in range(26):
    with self.subTest(mode=m):subprocess.run([str(p/'test'),str(m)],check=True)
