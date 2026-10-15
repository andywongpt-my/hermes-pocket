/* MIT. Called only by the existing serialized network worker; no task/client. */
#pragma once
#include "pocket_setup.h"
#include "pocket_usage.h"
#include <stddef.h>

void pocket_usage_read(unsigned target,pocket_usage_snapshot *out);
void pocket_usage_disconnected(unsigned target);
void pocket_usage_poll(unsigned target,const pocket_link_config *link,char *response,size_t capacity);
