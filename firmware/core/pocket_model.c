#include "pocket_model.h"
#include "pocket_usage.h"
#include <string.h>
void pocket_init(pocket_model *m) { memset(m,0,sizeof(*m));m->volume=60;m->alert_threshold=80; }
void pocket_connection(pocket_model *m,unsigned target,bool online) {
 if(target>=2)return;
 m->connected[target]=online;
 if(!online && m->recording && m->recording_target==target)m->recording=false;
}
bool pocket_select(pocket_model *m,unsigned target) {
 if(target>=2 || m->recording)return false;
 m->target=target;m->model_candidate=m->model_choice[target];m->model_apply_failed=false;m->playing=false;return true;
}
bool pocket_select_from_home(pocket_model *m,unsigned target,bool setup_active,bool voice_busy){
 return m->page==POCKET_STATUS && !setup_active && !voice_busy && pocket_select(m,target);
}
pocket_action pocket_press(pocket_model *m,pocket_button button) {
 if(button==POCKET_OK && m->playing){m->playing=false;return POCKET_STOP_PLAYBACK;}
 if(m->recording)return POCKET_NONE;
 if(m->page==POCKET_AUDIO && m->audio_edit && (button==POCKET_UP || button==POCKET_DOWN)){
  if(button==POCKET_UP)m->volume=m->volume>=95?100:m->volume+5;
  else m->volume=m->volume<=5?0:m->volume-5;
  return POCKET_NONE;
 }
 const pocket_page previous=m->page;
 if(button==POCKET_UP)m->page=(pocket_page)((m->page+POCKET_PAGE_COUNT-1)%POCKET_PAGE_COUNT);
 if(button==POCKET_DOWN)m->page=(pocket_page)((m->page+1)%POCKET_PAGE_COUNT);
 if(previous!=m->page && (previous==POCKET_MODELS || m->page==POCKET_MODELS)){m->model_candidate=m->model_choice[m->target];m->model_apply_failed=false;}
 /* Common cycle for four legacy quota windows and the 19-page dashboard. */
 if(button==POCKET_OK && m->page==POCKET_USAGE)m->usage_window=(m->usage_window+1)%POCKET_USAGE_SELECTOR_CYCLE;
 if(button==POCKET_OK && m->page==POCKET_TOOLS)m->tool=(pocket_tool)((m->tool+1)%POCKET_TOOL_COUNT);
 if(button==POCKET_OK && m->page==POCKET_AUDIO)m->audio_edit=!m->audio_edit;
 if(button==POCKET_OK && m->page==POCKET_MODELS){m->model_candidate=(m->model_candidate+1)%POCKET_MODEL_CHOICE_COUNT;m->model_apply_failed=false;}
 if(button==POCKET_OK && m->page==POCKET_ALERTS)m->alert_threshold=m->alert_threshold==80?90:m->alert_threshold==90?0:80;
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
bool pocket_model_apply_choice(pocket_model *m,unsigned available_mask,bool voice_busy){
 if(!m)return false;
 if(m->page!=POCKET_MODELS || m->target>1 || voice_busy || m->recording || m->submitting || m->playing ||
    m->model_candidate>=POCKET_MODEL_CHOICE_COUNT || !(available_mask&(1u<<m->model_candidate))){m->model_apply_failed=true;return false;}
 m->model_choice[m->target]=m->model_candidate;m->model_apply_failed=false;return true;
}
const char *pocket_model_choice_id(unsigned choice){
 static const char *ids[]={"default","opus","glm_flash"};return choice<POCKET_MODEL_CHOICE_COUNT?ids[choice]:NULL;
}
const char *pocket_model_choice_label(unsigned choice){
 static const char *labels[]={"Follow default","Claude Opus","GLM 5.3 Flash"};return choice<POCKET_MODEL_CHOICE_COUNT?labels[choice]:"Unknown model";
}
