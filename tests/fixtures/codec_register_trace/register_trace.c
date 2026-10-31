#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "es8311_codec.h"
#include "codec_ref_mgr.h"
#undef assert
#define assert(condition) do { if (!(condition)) { fprintf(stderr, "CHECK FAILED line %d: %s\n", __LINE__, #condition); exit(3); } } while (0)
static unsigned char regs[256];
static int refs[CODEC_REF_STAGE_NUM];
int codec_ref_acquire(const audio_codec_ctrl_info_t*i,codec_ref_stage_t s){assert(i);return ++refs[s];}
int codec_ref_release(const audio_codec_ctrl_info_t*i,codec_ref_stage_t s){assert(i&&refs[s]);return --refs[s];}
static bool opened(const audio_codec_ctrl_if_t*c){return c!=NULL;}
static int readreg(const audio_codec_ctrl_if_t*c,int reg,int rl,void*d,int dl){assert(c&&rl==1&&dl==1);*(unsigned char*)d=regs[reg];return 0;}
static int writereg(const audio_codec_ctrl_if_t*c,int reg,int rl,void*d,int dl){assert(c&&rl==1&&dl==1);regs[reg]=*(unsigned char*)d;if(reg==0x14||reg==0x16||reg==0x17||reg==0x44)printf("write reg%02x=%02x\n",reg,regs[reg]);return 0;}
static int info(const audio_codec_ctrl_if_t*c,audio_codec_ctrl_info_t*i){assert(c);*i=(audio_codec_ctrl_info_t){.type=AUDIO_CODEC_CTRL_I2C,.i2c={.addr=0x30,.port=0}};return 0;}
int main(void){
 const audio_codec_ctrl_if_t ctrl={.is_open=opened,.read_reg=readreg,.write_reg=writereg,.get_info=info};
 for(unsigned paired=0;paired<2;paired++){
 memset(regs,0,sizeof regs);memset(refs,0,sizeof refs);printf("mode=%s\n",paired?"paired":"legacy");
 es8311_codec_cfg_t cfg={.ctrl_if=&ctrl,.codec_mode=ESP_CODEC_DEV_WORK_MODE_BOTH,.pa_pin=-1,.use_mclk=true,.no_dac_ref=!paired};
 const audio_codec_if_t*c=es8311_codec_new(&cfg);assert(c);assert(regs[0x16]==0x24);assert(regs[0x44]==(paired?0x58:0x08));
 esp_codec_dev_sample_info_t fs={.bits_per_sample=16,.channel=paired?2:1,.channel_mask=paired?3:1,.sample_rate=paired?24000:16000};
 assert(c->set_fs(c,&fs)==0);assert(c->enable(c,true)==0);assert(regs[0x14]==0x1A&&regs[0x17]==0xBF);
 assert(c->set_mic_gain(c,30.0f)==0);assert(regs[0x16]==0x05);assert(regs[0x14]==0x1A&&regs[0x17]==0xBF);
 printf("PASS final reg14=%02x reg16=%02x reg17=%02x route=%02x\n",regs[0x14],regs[0x16],regs[0x17],regs[0x44]);
 assert(c->close(c)==0);free((void*)c);
 }
 return 0;
}
