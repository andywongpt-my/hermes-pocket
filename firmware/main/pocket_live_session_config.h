/* MIT. Inactive Live configuration snapshot; no I/O or device admission. */
#ifndef POCKET_LIVE_SESSION_CONFIG_H
#define POCKET_LIVE_SESSION_CONFIG_H
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    char host[193], token[129], ca_pem[2049], chat_id[65];
    unsigned port, target, volume;
    uint32_t epoch;
} pocket_live_session_config;

/* Strict HTTPS origin only, optional port and one root slash. No paths,
 * userinfo, URL escapes, IPv6 or insecure fallback. CA contents are preserved,
 * not cryptographically validated here; empty CA selects the existing SDK trust
 * bundle policy. volume is frozen user intent, never permission to increase it.
 * Failure clears output. Inputs may alias output; no input pointer is retained.
 */
bool pocket_live_session_config_snapshot(pocket_live_session_config *out,
    const char *origin, const char *token, const char *ca_pem,
    const char *chat_id, unsigned target, uint32_t epoch, unsigned volume);
bool pocket_live_session_config_valid(const pocket_live_session_config *);
void pocket_live_session_config_clear(pocket_live_session_config *);
#endif
