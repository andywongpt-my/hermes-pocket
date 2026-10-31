/* MIT. Qualification-only HPQ1; never the production Live protocol. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define PQ_HEADER_BYTES 32u
#define PQ_MAX_PAYLOAD 1024u
#define PQ_MAX_RECORD (PQ_HEADER_BYTES+PQ_MAX_PAYLOAD)
#define PQ_MAX_WIRE_BYTES (3u*1024u*1024u)
#define PQ_PREFIX_NOISE_CAP 16384u
#define PQ_RATE 24000u
#define PQ_PAIRS 240u
#define PQ_PCM_BYTES 960u
#define PQ_CAPTURE_PAYLOAD_BYTES 992u
#define PQ_STATUS_PAYLOAD_BYTES 48u
#define PQ_QUEUE_UNKNOWN UINT32_MAX
typedef enum { PQ_SILENT=1,PQ_AUDIO=2 } pocket_qualification_profile;
typedef enum { PQ_ARM=1,PQ_KEEPALIVE=2,PQ_STOP=3,PQ_ARM_TLS=4,PQ_START=16,PQ_PCM=17,PQ_TERMINAL=18,PQ_TLS_START=19,PQ_TLS_TERMINAL=20 } pocket_qualification_kind;
typedef struct {
 uint64_t nonce;uint32_t sequence,sample_offset;
 uint8_t kind,profile;const uint8_t*payload;size_t bytes;
} pocket_qualification_packet;
typedef enum { PQ_WIRE_OK,PQ_WIRE_INCOMPLETE,PQ_WIRE_INVALID,PQ_WIRE_CRC } pocket_qualification_wire_result;
/* Header LE <4sBBBBQIIHHI>: HPQ1,version1,kind,profile,flags0,nonce,
 * sequence,offset,payload length,reserved0,CRC32 IEEE(header[0:28]+payload).
 * Entire record only; decoder never scans/resynchronizes. Caller owns payload.
 * Strict fixed kind lengths and ARM budgets; per-direction sequence/nonce
 * continuity and one-attempt admission are caller/controller responsibilities. */
size_t pocket_qualification_encode(const pocket_qualification_packet*,uint8_t*,size_t);
pocket_qualification_wire_result pocket_qualification_decode(const uint8_t*,size_t,pocket_qualification_packet*);
uint32_t pocket_qualification_get_u32(const uint8_t*);
void pocket_qualification_put_u32(uint8_t*,uint32_t);
uint32_t pocket_qualification_frames(uint8_t profile);
