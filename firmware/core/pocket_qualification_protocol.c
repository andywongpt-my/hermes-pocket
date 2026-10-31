#include "pocket_qualification_protocol.h"
#include <string.h>
uint32_t pocket_qualification_get_u32(const uint8_t*p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
void pocket_qualification_put_u32(uint8_t*p,uint32_t x){for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(x>>(i*8));}
uint32_t pocket_qualification_frames(uint8_t profile){return profile==PQ_SILENT?1000u:profile==PQ_AUDIO?2400u:0u;}
static uint32_t crc(uint32_t c,const uint8_t*p,size_t n){while(n--){c^=*p++;for(unsigned i=0;i<8;i++)c=(c>>1)^((0u-(c&1u))&0xedb88320u);}return c;}
/* Structural only. The control layer validates private IPv4, token grammar,
 * PEM outline and owns/wipes copied credentials before transport admission. */
static bool tls_arm(const pocket_qualification_packet*p){
 if(p->sequence||p->sample_offset||p->bytes<36)return false;
 const uint8_t*b=p->payload;
 uint32_t lens=pocket_qualification_get_u32(b+28),calen=pocket_qualification_get_u32(b+32);
 unsigned host=lens&65535u,token=lens>>16;
 uint32_t port=pocket_qualification_get_u32(b+20),duration=pocket_qualification_get_u32(b+24);
 return pocket_qualification_get_u32(b)==24000&&pocket_qualification_get_u32(b+4)==60000&&
 pocket_qualification_get_u32(b+8)==1440000&&pocket_qualification_get_u32(b+12)==PQ_MAX_PAYLOAD&&
 pocket_qualification_get_u32(b+16)==PQ_MAX_WIRE_BYTES&&port>=1&&port<=65535&&duration>=1&&duration<=60000&&
 host>=7&&host<=15&&token>=32&&token<=128&&calen>=1&&calen<=768&&p->bytes==36u+host+token+calen;
}
static bool valid(const pocket_qualification_packet*p){
 if(!p||!p->nonce||!pocket_qualification_frames(p->profile)||p->sequence==UINT32_MAX||p->bytes>PQ_MAX_PAYLOAD||(p->bytes&&!p->payload))return false;
 switch(p->kind){
 case PQ_ARM_TLS:return tls_arm(p);
 case PQ_TLS_START:return !p->sequence&&!p->sample_offset&&p->bytes==48;
 case PQ_TLS_TERMINAL:return p->sequence==1&&!p->sample_offset&&p->bytes==96;
 case PQ_ARM:return !p->sequence&&!p->sample_offset&&p->bytes==16&&pocket_qualification_get_u32(p->payload)==PQ_RATE&&pocket_qualification_get_u32(p->payload+4)==pocket_qualification_frames(p->profile)&&pocket_qualification_get_u32(p->payload+8)==PQ_MAX_PAYLOAD&&pocket_qualification_get_u32(p->payload+12)==PQ_MAX_WIRE_BYTES;
 case PQ_KEEPALIVE:case PQ_STOP:return p->sequence&&!p->sample_offset&&!p->bytes;
 case PQ_START:return !p->sequence&&!p->sample_offset&&p->bytes==PQ_STATUS_PAYLOAD_BYTES;
 case PQ_PCM:return p->sequence&&p->sample_offset%PQ_PAIRS==0&&p->sample_offset<pocket_qualification_frames(p->profile)*PQ_PAIRS&&p->bytes==PQ_CAPTURE_PAYLOAD_BYTES;
 case PQ_TERMINAL:return p->sequence&&!p->sample_offset&&p->bytes==PQ_STATUS_PAYLOAD_BYTES;
 default:return false;
 }
}
size_t pocket_qualification_encode(const pocket_qualification_packet*p,uint8_t*out,size_t capacity){
 if(!out||!valid(p)||capacity<PQ_HEADER_BYTES+p->bytes)return 0;
 memcpy(out,"HPQ1",4);out[4]=1;out[5]=p->kind;out[6]=p->profile;out[7]=0;
 for(unsigned i=0;i<8;i++)out[8+i]=(uint8_t)(p->nonce>>(8*i));
 pocket_qualification_put_u32(out+16,p->sequence);pocket_qualification_put_u32(out+20,p->sample_offset);
 out[24]=(uint8_t)p->bytes;out[25]=(uint8_t)(p->bytes>>8);out[26]=out[27]=0;
 if(p->bytes)memcpy(out+32,p->payload,p->bytes);
 pocket_qualification_put_u32(out+28,crc(crc(UINT32_MAX,out,28),p->payload,p->bytes)^UINT32_MAX);
 return PQ_HEADER_BYTES+p->bytes;
}
pocket_qualification_wire_result pocket_qualification_decode(const uint8_t*in,size_t n,pocket_qualification_packet*out){
 if(!out)return PQ_WIRE_INVALID;
 memset(out,0,sizeof *out);
 if(!in)return PQ_WIRE_INVALID;
 if(n<PQ_HEADER_BYTES)return PQ_WIRE_INCOMPLETE;
 if(memcmp(in,"HPQ1",4)||in[4]!=1||in[7]||in[26]||in[27])return PQ_WIRE_INVALID;
 size_t bytes=(size_t)in[24]|((size_t)in[25]<<8);
 if(bytes>PQ_MAX_PAYLOAD)return PQ_WIRE_INVALID;
 if(n<PQ_HEADER_BYTES+bytes)return PQ_WIRE_INCOMPLETE;
 if(n!=PQ_HEADER_BYTES+bytes)return PQ_WIRE_INVALID;
 uint32_t check=crc(crc(UINT32_MAX,in,28),in+32,bytes)^UINT32_MAX;
 if(check!=pocket_qualification_get_u32(in+28))return PQ_WIRE_CRC;
 pocket_qualification_packet p={.kind=in[5],.profile=in[6],.sequence=pocket_qualification_get_u32(in+16),.sample_offset=pocket_qualification_get_u32(in+20),.payload=in+32,.bytes=bytes};
 for(unsigned i=0;i<8;i++)p.nonce|=(uint64_t)in[8+i]<<(i*8);
 if(!valid(&p))return PQ_WIRE_INVALID;
 *out=p;return PQ_WIRE_OK;
}
