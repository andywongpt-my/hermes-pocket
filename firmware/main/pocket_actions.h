/* MIT. UI queues read-only work; the existing idle worker performs it. */
#pragma once
#include "pocket_model.h"
#include "pocket_setup.h"
#include <stddef.h>

void pocket_actions_init(void);
void pocket_actions_screen_off(bool off);
unsigned pocket_actions_poll_ms(void);
void pocket_actions_view(pocket_page page,unsigned target);
bool pocket_actions_request(unsigned target,pocket_tool tool);
unsigned pocket_actions_poll_target(unsigned fallback);
/* Gates idle work using monotonic time. Skipped checks retain their real age. */
bool pocket_actions_poll_due(unsigned target);
bool pocket_actions_usage_due(unsigned target);
/* Only after reachable status/Usage and while voice is not pending. */
void pocket_actions_idle(unsigned target,const pocket_link_config *link,char *response,size_t capacity);
void pocket_actions_disconnected(unsigned target);
