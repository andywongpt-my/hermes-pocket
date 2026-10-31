/* INTERNAL experimental binding, not a runtime authorization boundary.
 * Only the inactive/gated worker may call. No default listener/task/key lookup.
 * Caller owns this zero-initialized storage exclusively through close completion.
 */
#ifndef POCKET_LIVE_NETWORK_IDF_H
#define POCKET_LIVE_NETWORK_IDF_H
#include "pocket_live_network.h"
#include "esp_transport.h"
typedef enum { NET_IDLE,NET_DNS,NET_TLS,NET_UPGRADE,NET_OPEN,NET_FAILED,NET_CLOSING,NET_CLOSED } pocket_live_network_phase;
typedef struct {
 const char *host,*token,*ca_pem,*path;
 unsigned port;
 bool (*stopped)(void *);
 void *stop_context;
} pocket_live_network_config;
typedef struct {
 pocket_live_network_phase phase;
 char host[193],address[48],auth[136],ca[2049],path[40];
 unsigned port;
 uint32_t dns_token;
 bool (*stopped)(void *);
 void *stop_context;
 pocket_live_network_deadline deadline;
 esp_transport_handle_t tls,shim,ws;
 uint8_t mutable_wire[1024];
 bool cleanup_complete,finalizing;
 uint64_t finalize_end;
} pocket_live_network_context;
typedef struct {int opcode;bool fin;size_t payload_bytes;} pocket_live_network_message;
/* Begin submits async DNS without an authenticated network write. A pending
 * canceled DNS callback retains the one global slot; no admission until settled. */
bool pocket_live_network_open_begin(pocket_live_network_context *,const pocket_live_network_config *);
/* Pump with a task yield between 0 results. One total 10-second startup budget.
 * TLS goes to numeric IP; SNI and cert-name stay original host. No reconnect. */
int pocket_live_network_open_step(pocket_live_network_context *);
/* The exact canonical text stop control alone enters read-only finalization
 * (3-second total budget), permitting its send and final reads after soft Stop.
 * No later sends, retries, or recovery from an interrupted partial write. */
bool pocket_live_network_send(pocket_live_network_context *,bool,const uint8_t *,size_t);
/* 0 idle/handled control; >0 one chunk; -1 terminal. Caller bounds and validates
 * frame/fragment assembly using metadata (valid only for positive returns).
 * Capacity must be 16..1024 bytes: SDK header scratch needs at least four. */
int pocket_live_network_read(pocket_live_network_context *,uint8_t *,size_t,pocket_live_network_message *);
/* Owner only. Do not free/reuse storage until return. If RTOS/TCPIP stalls, a
 * controller must report pending cleanup and retain storage, never kill owner.
 * No hard realtime guarantee follows from poll deadlines or SO_LINGER(0). */
bool pocket_live_network_close(pocket_live_network_context *);
#endif
