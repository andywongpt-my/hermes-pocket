/* Hermes Pocket, MIT. Pure bounded model shared by host tests and firmware. */
#ifndef POCKET_MODEL_H
#define POCKET_MODEL_H
#include <stdbool.h>
#include <stdint.h>
typedef enum { POCKET_STATUS, POCKET_CHAT, POCKET_USAGE, POCKET_DIAGNOSTICS, POCKET_TASKS, POCKET_TOOLS, POCKET_AUDIO, POCKET_ALERTS, POCKET_SCREEN_OFF, POCKET_MODELS, POCKET_PAGE_COUNT } pocket_page;

typedef enum { POCKET_MODEL_DEFAULT, POCKET_MODEL_OPUS, POCKET_MODEL_GLM_FLASH, POCKET_MODEL_CHOICE_COUNT } pocket_model_choice;
typedef enum { POCKET_TOOL_STATUS, POCKET_TOOL_USAGE, POCKET_TOOL_TASKS, POCKET_TOOL_READER, POCKET_TOOL_TODO_UNAVAILABLE, POCKET_TOOL_COUNT } pocket_tool;
typedef enum { POCKET_UP, POCKET_DOWN, POCKET_OK } pocket_button;
typedef enum { POCKET_NONE, POCKET_START_RECORDING, POCKET_SEND_AUDIO, POCKET_STOP_PLAYBACK } pocket_action;
typedef enum { POCKET_IDLE, POCKET_QUEUED, POCKET_RUNNING, POCKET_APPROVAL, POCKET_DONE, POCKET_FAILED, POCKET_CANCELLED } pocket_task;
typedef struct {
 pocket_page page;
 unsigned target, recording_target, usage_window;
 pocket_tool tool;
 unsigned volume,alert_threshold;
 unsigned model_choice[2],model_candidate; /* Per target, current device session. */
 bool audio_edit,usd_floor_enabled,model_apply_failed;
 bool connected[2], recording, submitting, playing;
 uint32_t sequence[2], generation[2];
 pocket_task task[2];
} pocket_model;
void pocket_init(pocket_model *m);
void pocket_connection(pocket_model *m,unsigned target,bool online);
bool pocket_select(pocket_model *m,unsigned target);
bool pocket_select_from_home(pocket_model *m,unsigned target,bool setup_active,bool voice_busy);
pocket_action pocket_press(pocket_model *m,pocket_button button);
pocket_action pocket_release(pocket_model *m,pocket_button button);
void pocket_submitted(pocket_model *m,unsigned target);
bool pocket_update(pocket_model *m,unsigned target,uint32_t generation,uint32_t sequence,pocket_task state);
void pocket_new_task(pocket_model *m,unsigned target);
bool pocket_model_apply_choice(pocket_model *m,unsigned available_mask,bool voice_busy);
const char *pocket_model_choice_id(unsigned choice);
const char *pocket_model_choice_label(unsigned choice);
#endif
