/* MIT. Inactive network ownership model. Caller serializes DNS state access.
 * One live callback slot, never reclaimed/reused until completion, even on Stop.
 * Deadline state belongs to a single TLS/network owner, not its DNS callback. */
#ifndef POCKET_LIVE_NETWORK_H
#define POCKET_LIVE_NETWORK_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct {
 uint32_t generation;
 bool busy,started,completed,cancelled,success;
 char host[193],address[48];
} pocket_live_network_dns;
void pocket_live_network_dns_init(pocket_live_network_dns *);
bool pocket_live_network_dns_begin(pocket_live_network_dns *,const char *,uint32_t *);
bool pocket_live_network_dns_start(pocket_live_network_dns *,uint32_t);
void pocket_live_network_dns_cancel(pocket_live_network_dns *,uint32_t);
bool pocket_live_network_dns_complete(pocket_live_network_dns *,uint32_t,const char *);
bool pocket_live_network_dns_take(pocket_live_network_dns *,uint32_t,char *,size_t);
typedef struct {uint64_t end,last;bool active,failed;} pocket_live_network_deadline;
void pocket_live_network_deadline_init(pocket_live_network_deadline *);
bool pocket_live_network_deadline_begin(pocket_live_network_deadline *,uint64_t,uint32_t);
/* Returns 1..50ms, or zero terminal expiry/stop/error. An active operation cannot
 * be renewed by a nested SDK read/write/poll. Check again after every SDK call. */
uint32_t pocket_live_network_deadline_slice(pocket_live_network_deadline *,uint64_t,uint32_t,bool);
bool pocket_live_network_deadline_finish(pocket_live_network_deadline *,uint64_t,bool);
#endif
