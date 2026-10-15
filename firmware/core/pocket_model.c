#include "pocket_model.h"
#include "pocket_usage.h"
#include <string.h>
void pocket_init(pocket_model *m) { memset(m,0,sizeof(*m)); }
void pocket_connection(pocket_model *m,unsigned target,bool online) {
 if(target>=2)return;
 m->connected[target]=online;
 if(!online && m->recording && m->recording_target==target)m->recording=false;
}
bool pocket_select(pocket_model *m,unsigned target) {
 if(target>=2 || m->recording)return false;
 m->target=target; m->playing=false; return true;
}
bool pocket_select_from_home(pocket_model *m,unsigned target,bool setup_active,bool voice_busy){
 return m->page==POCKET_STATUS && !setup_active && !voice_busy && pocket_select(m,target);
}
pocket_action pocket_press(pocket_model *m,pocket_button button) {
 if(button==POCKET_OK && m->playing){m->playing=false;return POCKET_STOP_PLAYBACK;}
 if(m->recording)return POCKET_NONE;
 if(button==POCKET_UP)m->page=(pocket_page)((m->page+POCKET_PAGE_COUNT-1)%POCKET_PAGE_COUNT);
 if(button==POCKET_DOWN)m->page=(pocket_page)((m->page+1)%POCKET_PAGE_COUNT);
 /* 12 is the common cycle for all supported counts (one through four). */
 if(button==POCKET_OK && m->page==POCKET_USAGE)m->usage_window=(m->usage_window+1)%12;
 if(button==POCKET_OK && m->page==POCKET_CHAT && m->connected[m->target] && !m->submitting) {
  m->recording=true;m->recording_target=m->target;return POCKET_START_RECORDING;
 }
 return POCKET_NONE;
}
pocket_action pocket_release(pocket_model *m,pocket_button button) {
 if(button==POCKET_OK && m->recording) {
  m->recording=false;
  if(m->connected[m->recording_target]) {m->submitting=true;return POCKET_SEND_AUDIO;}
 }
 return POCKET_NONE;
}
void pocket_submitted(pocket_model *m,unsigned target) {
 if(target==m->recording_target)m->submitting=false;
}
bool pocket_update(pocket_model *m,unsigned target,uint32_t generation,uint32_t sequence,pocket_task state) {
 if(target>=2 || generation!=m->generation[target] || state<POCKET_IDLE || state>POCKET_CANCELLED || sequence<=m->sequence[target])return false;
 if(m->task[target]>=POCKET_DONE)return false;
 m->sequence[target]=sequence;m->task[target]=state;return true;
}
void pocket_new_task(pocket_model *m,unsigned target) {
 if(target<2){m->generation[target]++;m->sequence[target]=0;m->task[target]=POCKET_IDLE;}
}
