/* MIT. Bounded, read-only full-text reader; network work belongs to idle poll. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "pocket_setup.h"

#define POCKET_READER_PAGE_BYTES 768
#define POCKET_READER_CURSOR_BYTES 128
#define POCKET_READER_MAX_RESULT_BYTES 65536

typedef enum {
 POCKET_READER_CLOSED, POCKET_READER_LOADING, POCKET_READER_READY,
 POCKET_READER_OFFLINE, POCKET_READER_FAILED, POCKET_READER_AUTH_REQUIRED,
 POCKET_READER_REVISION_CHANGED
} pocket_reader_status;

typedef struct {
 bool active,has_page,has_prev,has_next,eof;
 unsigned target,page,page_count,utf8_bytes,total_utf8_bytes;
 uint32_t generation;
 pocket_reader_status status;
 char task_id[65],result_revision[65];
} pocket_reader_snapshot;

/* Explicit negotiated capability only. Disabling it invalidates an open reader. */
void pocket_reader_available(unsigned target,bool available);
/* Open the cached selected COMPLETED task. All action functions only queue work. */
bool pocket_reader_open(unsigned target);
void pocket_reader_close(void);
/* Exactly -1 (previous) or +1 (next); only the returned opaque cursor is echoed. */
bool pocket_reader_turn(int direction);
/* Copy exact raw page bytes, including embedded NUL, then a trailing NUL.
 * Use utf8_bytes, never strlen. A text capacity below PAGE_BYTES+1 copies no
 * page and reports has_page=false/utf8_bytes=0 in that particular snapshot. */
void pocket_reader_read(pocket_reader_snapshot *out,char *text,size_t capacity);
/* At most one GET, using the existing caller-owned <=8192-byte HTTP buffer. */
bool pocket_reader_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity);
bool pocket_reader_active(void);
void pocket_reader_disconnected(unsigned target);
