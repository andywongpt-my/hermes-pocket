/* MIT. Fixed v2 option IDs; GETs reuse the existing idle worker and buffer. */
#pragma once
#include "pocket_model.h"
#include "pocket_setup.h"
#include <stddef.h>

typedef enum {
 POCKET_MODELS_UNFETCHED,POCKET_MODELS_LOADING,POCKET_MODELS_READY,
 POCKET_MODELS_UNSUPPORTED,POCKET_MODELS_OFFLINE,POCKET_MODELS_FAILED,POCKET_MODELS_AUTH_REQUIRED
} pocket_models_status;
typedef struct {pocket_models_status status;unsigned available_mask,verified_mask;} pocket_models_snapshot;
void pocket_models_refresh(unsigned target);
void pocket_models_read(unsigned target,pocket_models_snapshot *out);
void pocket_models_disconnected(unsigned target);
bool pocket_models_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity);
/* Default alone can use the legacy endpoint. Explicit choices fail closed. */
bool pocket_models_voice_body(unsigned target,unsigned choice,char *out,size_t capacity);
