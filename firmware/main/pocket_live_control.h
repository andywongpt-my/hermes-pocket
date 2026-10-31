/* MIT. Exact flat v1 server controls; no allocation retained after return. */
#ifndef POCKET_LIVE_CONTROL_H
#define POCKET_LIVE_CONTROL_H
#include "pocket_live_worker.h"
bool pocket_live_control(const char *json,size_t bytes,pocket_live_worker_event *out);
#endif
