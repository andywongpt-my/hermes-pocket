/* MIT. Paired-capture v2 worker; DMA consumption is flow credit, NOT playout. */
#ifndef POCKET_LIVE_V2_WORKER_H
#define POCKET_LIVE_V2_WORKER_H
#include "pocket_live_worker.h"
#include "pocket_live_v2_stream.h"
typedef struct {
    uint32_t epoch,sequence,sample_offset,dma_consumed;
    uint8_t pcm[960]; /*240 interleaved native24k micL/refR pairs, int16LE.*/
} pocket_live_v2_capture;
typedef struct {
    pocket_live_worker_reason reason;
    bool finalized,backend_pending,playback_pending,cleanup_complete,terminal_received;
    bool actual_playout_known; /*Always false: this protocol does not measure it.*/
} pocket_live_v2_worker_result;
typedef struct {
    void *context;
    bool (*connect)(void *);
    bool (*send)(void *,bool binary,const uint8_t *,size_t);
    /*Typed READY/CLOSED events require strict v2 control validation.*/
    bool (*receive)(void *,pocket_live_worker_event *,uint8_t *,size_t);
    bool (*audio_start)(void *);
    /*Nonblocking owned RX queue poll:1=exact frame,0=none,-1=terminal failure.
     * Never a pointer into DMA. Sequence/epoch/gaps/overflow must be explicit.*/
    int (*capture)(void *,pocket_live_v2_capture *);
    /*-1=terminal,0=busy without copying,1=entire frame copied once.*/
    int (*submit)(void *,uint32_t offset,const uint8_t *,size_t);
    bool (*audio_stop)(void *);
    bool (*close)(void *);
    uint64_t (*now_ms)(void *);
    bool (*stopped)(void *);
} pocket_live_v2_worker_ops;
typedef struct {
    pocket_live_client speaker; /*Bounded FIFO reused; its rendered field is unused.*/
    pocket_live_v2_state microphone;
    pocket_live_v2_capture capture;
    uint8_t receive[1024],transmit[976];
} pocket_live_v2_worker;
/*One owner, no allocation. Same software deadlines/conditional bounded-op
 * contract as v1. No retry, reconnect, resampling, real-playout assertion, or
 * automatic route/device activation. Epoch must be nonzero and match RX queue.*/
pocket_live_v2_worker_result pocket_live_v2_worker_run(pocket_live_v2_worker *,
    const pocket_live_v2_worker_ops *,const char *chat_id,uint32_t epoch);
#endif
