/* MIT. Serial V2 reply delivery; HTTP/PCM buffers remain caller-owned. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "pocket_setup.h"

typedef struct cJSON cJSON;
bool pocket_delivery_text_pages(const cJSON *status);
bool pocket_delivery_speech_segments(const cJSON *status);

typedef struct {
 void *context;
 bool (*cancelled)(void *);
 bool (*start)(void *);
 bool (*write)(void *,const void *,size_t);
 bool (*drain)(void *);
 void (*stop)(void *);
 void (*message)(void *,const char *);
} pocket_speech_io;

/* Call from the existing serial network worker. target: 0 Windows, 1 Linux.
 * response requires 8192 bytes and pcm 4096; larger buffers do not raise limits.
 * All callbacks except message are required. stop also cleans a failed start.
 * cancelled must reflect local invalidation independently of HTTP completion.
 * Success means all matched PCM was written and drain completed before stop. */
bool pocket_speech_v2_play(const pocket_link_config *link,unsigned target,
 const char *task_id,const char *request_id,char *response,size_t capacity,
 unsigned char *pcm,size_t pcm_capacity,const pocket_speech_io *io);
