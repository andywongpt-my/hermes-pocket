/* MIT. Exact setup-interface match for native and IPv4-mapped sockets. */
#ifndef POCKET_SETUP_ADDRESS_H
#define POCKET_SETUP_ADDRESS_H
#include <stdbool.h>
#include <string.h>
#ifdef ESP_PLATFORM
#include "lwip/sockets.h"
#else
#include <sys/socket.h>
#include <netinet/in.h>
#endif
static inline bool pocket_setup_address_matches(const struct sockaddr_storage *local, socklen_t length) {
 if (!local || length < sizeof(struct sockaddr)) return false;
 if (local->ss_family == AF_INET) {
  if (length < sizeof(struct sockaddr_in)) return false;
  const struct sockaddr_in *v4=(const struct sockaddr_in*)local;
  return v4->sin_addr.s_addr == htonl(0xc0a80401);
 }
#if defined(AF_INET6)
 if (local->ss_family == AF_INET6) {
  if (length < sizeof(struct sockaddr_in6)) return false;
  const struct sockaddr_in6 *v6=(const struct sockaddr_in6*)local;
  static const unsigned char expected[16]={0,0,0,0,0,0,0,0,0,0,255,255,192,168,4,1};
  return memcmp(&v6->sin6_addr,expected,sizeof(expected)) == 0;
 }
#endif
 return false;
}
#endif
