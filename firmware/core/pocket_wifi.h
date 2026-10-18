/* MIT. Bounded credentials and connection policy; no SDK calls or logging. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { POCKET_WIFI_CAPACITY=5, POCKET_WIFI_SCAN_MAX=20, POCKET_WIFI_CONNECT_MS=20000 };
typedef struct { char ssid[33],password[64]; } pocket_wifi_profile;
typedef struct {
 uint32_t version,sequence;
 uint8_t count,last,reserved[2];
 pocket_wifi_profile profiles[POCKET_WIFI_CAPACITY];
} pocket_wifi_book;
typedef struct { char ssid[33];int rssi;bool open; } pocket_wifi_visible;
typedef enum { POCKET_WIFI_REASON_OTHER,POCKET_WIFI_REASON_NOT_FOUND,POCKET_WIFI_REASON_AUTH } pocket_wifi_reason;
typedef struct {
 bool connected;uint32_t disconnected,associated,got_ip,association_ip;
 pocket_wifi_reason reason;char ssid[33];
} pocket_wifi_snapshot;
typedef enum {
 POCKET_WIFI_DISCONNECTED,POCKET_WIFI_CONNECTING,POCKET_WIFI_GETTING_IP,POCKET_WIFI_CONNECTED,
 POCKET_WIFI_NOT_FOUND,POCKET_WIFI_AUTH_FAILED,POCKET_WIFI_NO_IP,POCKET_WIFI_CONNECTION_FAILED
} pocket_wifi_connection;
typedef enum {
 POCKET_WIFI_JOIN_IDLE,POCKET_WIFI_JOIN_TESTING,POCKET_WIFI_JOIN_SAVED,POCKET_WIFI_JOIN_FAILED,
 POCKET_WIFI_JOIN_CANCELLED,POCKET_WIFI_JOIN_FULL,POCKET_WIFI_JOIN_SAVE_FAILED,POCKET_WIFI_JOIN_FORGOTTEN
} pocket_wifi_join_result;
typedef struct {
 void *context;
 /* disconnect: 0 awaits a fresh event, 1 already disconnected, -1 failure. */
 int (*disconnect)(void *);
 bool (*connect)(void *,const pocket_wifi_profile *);
 size_t (*scan)(void *,pocket_wifi_visible *,size_t);
 bool (*save)(void *,const pocket_wifi_book *);
} pocket_wifi_ops;
typedef struct {
 pocket_wifi_book book;
 pocket_wifi_ops ops;
 pocket_wifi_profile attempt;
 pocket_wifi_connection connection;
 pocket_wifi_join_result join_result;
 unsigned phase,tried,backoff_ms;
 uint32_t disconnect_base,association_base,ip_base;
 int64_t deadline_ms,retry_ms;
 int selected;
 bool testing;
} pocket_wifi_engine;

bool pocket_wifi_profile_valid(const pocket_wifi_profile *profile);
void pocket_wifi_book_init(pocket_wifi_book *book);
bool pocket_wifi_book_valid(const pocket_wifi_book *book);
bool pocket_wifi_book_import(pocket_wifi_book *book,const pocket_wifi_profile *legacy);
int pocket_wifi_book_find(const pocket_wifi_book *book,const char *ssid);
int pocket_wifi_book_put(pocket_wifi_book *book,const pocket_wifi_profile *profile);
int pocket_wifi_choose(const pocket_wifi_book *,const pocket_wifi_visible *,size_t,unsigned tried);
void pocket_wifi_init(pocket_wifi_engine *,const pocket_wifi_book *,const pocket_wifi_ops *);
void pocket_wifi_tick(pocket_wifi_engine *,int64_t now_ms,const pocket_wifi_snapshot *,bool voice_busy);
bool pocket_wifi_join(pocket_wifi_engine *,const pocket_wifi_profile *,int64_t,const pocket_wifi_snapshot *);
bool pocket_wifi_select(pocket_wifi_engine *,const char *ssid,int64_t,const pocket_wifi_snapshot *);
bool pocket_wifi_forget(pocket_wifi_engine *,const char *ssid,int64_t,const pocket_wifi_snapshot *);
void pocket_wifi_cancel(pocket_wifi_engine *,int64_t,const pocket_wifi_snapshot *);
bool pocket_wifi_attempting(const pocket_wifi_engine *);
