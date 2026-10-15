/* Hermes Pocket, MIT. Pure bounded model shared by host tests and firmware. */
#ifndef POCKET_MODEL_H
#define POCKET_MODEL_H
#include <stdbool.h>
#include <stdint.h>
typedef enum { POCKET_STATUS, POCKET_CHAT, POCKET_USAGE, POCKET_DIAGNOSTICS, POCKET_SCREEN_OFF, POCKET_PAGE_COUNT } pocket_page;
typedef enum { POCKET_UP, POCKET_DOWN, POCKET_OK } pocket_button;
typedef enum { POCKET_NONE, POCKET_START_RECORDING, POCKET_SEND_AUDIO, POCKET_STOP_PLAYBACK } pocket_action;
typedef enum { POCKET_IDLE, POCKET_QUEUED, POCKET_RUNNING, POCKET_APPROVAL, POCKET_DONE, POCKET_FAILED, POCKET_CANCELLED } pocket_task;
typedef struct {
 pocket_page page;
 unsigned target, recording_target, usage_window;
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
#endif
