#pragma once
#include "pocket_offline_diagnostic.h"
void pocket_offline_diagnostic_format(const pocket_offline_diagnostic_snapshot *,char *,size_t,char *,size_t,char *,size_t);
/* Automatic read-only panes: no input/ownership transition. The legacy formatter
 * remains the totals view. Timing on rail panes is sample-domain only. */
#define POCKET_DIAGNOSTIC_PAGES 5u
#define POCKET_DIAGNOSTIC_PAGE_MS 5000u
void pocket_offline_diagnostic_format_page(const pocket_offline_diagnostic_snapshot *,unsigned,
 char *,size_t,char *,size_t,char *,size_t);
