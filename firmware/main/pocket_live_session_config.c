#include "pocket_live_session_config.h"
#include <stddef.h>
#include <string.h>

void pocket_live_session_config_clear(pocket_live_session_config *config) {
    if (!config) return;
    volatile unsigned char *p = (volatile unsigned char *)config;
    for (size_t i = 0; i < sizeof *config; ++i) p[i] = 0;
}
static bool length(const char *s, size_t cap, size_t *n) {
    if (!s) return false;
    for (size_t i = 0; i < cap; ++i) if (!s[i]) { *n = i; return true; }
    return false;
}
static bool alpha(unsigned char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}
static bool digit(unsigned char c) { return c >= '0' && c <= '9'; }
static bool host_valid(const char *host) {
    size_t n = 0, label = 0;
    bool numeric = true;
    if (!length(host, 193, &n) || !n) return false;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)host[i];
        if (c == '.') {
            if (!label || host[i - 1] == '-') return false;
            label = 0;
        } else {
            if (!alpha(c) && !digit(c) && c != '-') return false;
            if ((!label && c == '-') || ++label > 63) return false;
            if (!digit(c)) numeric = false;
        }
    }
    if (!label || host[n - 1] == '-') return false;
    if (!numeric) return true;
    /* Reject ambiguous dotted numeric names and octal-looking IPv4 components. */
    unsigned parts = 0, value = 0, count = 0;
    for (size_t i = 0; i <= n; ++i) {
        if (i == n || host[i] == '.') {
            if (!count || value > 255 || ++parts > 4) return false;
            value = count = 0;
        } else {
            if (count && !value) return false;
            value = value * 10u + (unsigned)(host[i] - '0');
            if (++count > 3) return false;
        }
    }
    return parts == 4;
}
static bool text_valid(const char *s, size_t capacity, size_t minimum, bool ca, bool chat) {
    size_t n = 0;
    if (!length(s, capacity, &n) || n < minimum) return false;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)s[i];
        if (chat) {
            if (!alpha(c) && !digit(c) && c != '_' && c != '-') return false;
        } else if (ca) {
            if (!(c >= 32 && c <= 126) && c != '\n' && c != '\r' && c != '\t') return false;
        } else if (c < 33 || c > 126) return false;
    }
    return true;
}
bool pocket_live_session_config_valid(const pocket_live_session_config *c) {
    return c && c->port && c->port <= 65535 && c->target <= 1 && c->epoch && c->volume <= 100 &&
        host_valid(c->host) && text_valid(c->token, sizeof c->token, 32, false, false) &&
        text_valid(c->ca_pem, sizeof c->ca_pem, 0, true, false) &&
        text_valid(c->chat_id, sizeof c->chat_id, 1, false, true);
}
bool pocket_live_session_config_snapshot(pocket_live_session_config *out,
    const char *origin, const char *token, const char *ca_pem,
    const char *chat_id, unsigned target, uint32_t epoch, unsigned volume) {
    if (!out) return false;
    pocket_live_session_config next = {0};
    bool ok = false;
    size_t n = 0, tn = 0, cn = 0, in = 0;
    if (!length(origin, 193, &n) || n < 9 || memcmp(origin, "https://", 8) ||
        !length(token, sizeof next.token, &tn) || !length(ca_pem, sizeof next.ca_pem, &cn) ||
        !length(chat_id, sizeof next.chat_id, &in)) goto done;
    if (origin[n - 1] == '/') --n;
    size_t host_end = 8;
    while (host_end < n && origin[host_end] != ':') ++host_end;
    size_t host_n = host_end - 8;
    if (!host_n || host_n >= sizeof next.host) goto done;
    memcpy(next.host, origin + 8, host_n);
    next.port = 443;
    if (host_end < n) {
        size_t at = host_end + 1;
        if (at == n || n - at > 5 || origin[at] == '0') goto done;
        next.port = 0;
        for (; at < n; ++at) {
            if (!digit((unsigned char)origin[at])) goto done;
            next.port = next.port * 10u + (unsigned)(origin[at] - '0');
        }
    }
    memcpy(next.token, token, tn);
    memcpy(next.ca_pem, ca_pem, cn);
    memcpy(next.chat_id, chat_id, in);
    next.target = target; next.epoch = epoch; next.volume = volume;
    ok = pocket_live_session_config_valid(&next);
done:
    pocket_live_session_config_clear(out);
    if (ok) *out = next;
    pocket_live_session_config_clear(&next);
    return ok;
}
