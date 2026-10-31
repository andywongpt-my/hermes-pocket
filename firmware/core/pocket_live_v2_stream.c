#include "pocket_live_v2_stream.h"
#include <string.h>
static uint32_t samples(const pocket_live_v2_packet *p){return (uint32_t)(p->pcm_bytes/(p->kind==1?4u:2u));}
static bool valid(const pocket_live_v2_packet *p){
    return p&&p->pcm&&(p->kind==1||p->kind==2)&&p->pcm_bytes>=2&&p->pcm_bytes<=960&&
        p->pcm_bytes%2==0&&(p->kind!=1||p->pcm_bytes==960)&&(p->kind!=2||!p->dma_consumed)&&
        p->sequence!=UINT32_MAX&&p->sample_offset<=UINT32_MAX-samples(p);
}
static void put(uint8_t*p,uint32_t n){p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;}
static uint32_t get(const uint8_t*p){return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];}
pocket_live_v2_result pocket_live_v2_pack(const pocket_live_v2_packet*p,uint8_t*out,size_t cap,size_t*written){
    if(written)*written=0;
    if(!out||!written||!valid(p)||cap<16+p->pcm_bytes)return POCKET_LIVE_V2_INVALID;
    memmove(out+16,p->pcm,p->pcm_bytes);
    out[0]='P';out[1]='L';out[2]=2;out[3]=p->kind;
    put(out+4,p->sequence);put(out+8,p->sample_offset);put(out+12,p->dma_consumed);
    *written=16+p->pcm_bytes;return POCKET_LIVE_V2_OK;
}
pocket_live_v2_result pocket_live_v2_unpack(const uint8_t*wire,size_t bytes,pocket_live_v2_packet*out){
    if(!wire||!out||bytes<18||bytes>976||wire[0]!='P'||wire[1]!='L'||wire[2]!=2)return POCKET_LIVE_V2_INVALID;
    pocket_live_v2_packet p={wire[3],get(wire+4),get(wire+8),get(wire+12),wire+16,bytes-16};
    if(!valid(&p))return POCKET_LIVE_V2_INVALID;
    *out=p;return POCKET_LIVE_V2_OK;
}
void pocket_live_v2_init(pocket_live_v2_state*s,uint8_t kind){if(s)*s=(pocket_live_v2_state){.kind=kind};}
pocket_live_v2_result pocket_live_v2_accept(pocket_live_v2_state*s,const pocket_live_v2_packet*p,uint32_t sent){
    if(!s||!valid(p)||s->kind!=p->kind)return POCKET_LIVE_V2_INVALID;
    if(p->sequence!=s->next_sequence||p->sample_offset!=s->next_sample_offset)return POCKET_LIVE_V2_OUT_OF_ORDER;
    if(p->kind==1&&(p->dma_consumed<s->dma_consumed||p->dma_consumed>sent))return POCKET_LIVE_V2_BAD_CREDIT;
    s->next_sequence++;s->next_sample_offset+=samples(p);s->dma_consumed=p->dma_consumed;return POCKET_LIVE_V2_OK;
}
