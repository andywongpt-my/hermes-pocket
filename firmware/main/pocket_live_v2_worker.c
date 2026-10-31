/* MIT. Cooperative network/capture pump; playback progresses independently in DMA. */
#include "pocket_live_v2_worker.h"
#include <string.h>
#include <stdio.h>

_Static_assert(sizeof(pocket_live_v2_worker) <= 8600, "Live worker memory budget exceeded");

/* Recheck after every potentially bounded operation, before admitting its result
 * to the next operation. These checks cannot preempt a misbehaving binding. */
typedef struct {
    uint64_t began, last, stop_at;
    bool audio, stopping;
} worker_clock;

static bool sample_clock(const pocket_live_v2_worker_ops *o, worker_clock *c,
    pocket_live_v2_worker_result *r) {
    uint64_t now=o->now_ms(o->context);
    if (now<c->last) { r->reason=PLW_PROTOCOL_ERROR; return false; }
    c->last=now;
    if (c->stopping && now-c->stop_at>=3000) {
        r->reason=PLW_FINALIZE_TIMEOUT; return false;
    }
    return true;
}

static bool checkpoint(pocket_live_v2_worker *w, const pocket_live_v2_worker_ops *o,
    worker_clock *c, pocket_live_v2_worker_result *r) {
    if (!sample_clock(o,c,r)) return false;
    if (!c->stopping && (o->stopped(o->context) || c->last-c->began>=300000)) {
        c->stopping=true; c->stop_at=c->last;
        if (c->audio) {
            if (!o->audio_stop(o->context)) {
                r->reason=PLW_CLEANUP_FAILED; r->cleanup_complete=false; return false;
            }
            c->audio=false;
        }
        pocket_live_client_stop(&w->speaker);
        if (!sample_clock(o,c,r)) return false;
        static const uint8_t stop[]="{\"type\":\"stop\"}";
        if (!o->send(o->context,false,stop,sizeof stop-1)) {
            r->reason=PLW_SEND_FAILED; return false;
        }
        if (!sample_clock(o,c,r)) return false;
    }
    if (!c->stopping && w->speaker.phase==POCKET_LIVE_STARTING && c->last-c->began>=10000) {
        r->reason=PLW_START_TIMEOUT; return false;
    }
    return true;
}

pocket_live_v2_worker_result pocket_live_v2_worker_run(pocket_live_v2_worker *w,
    const pocket_live_v2_worker_ops *o, const char *chat_id,uint32_t epoch) {
    pocket_live_v2_worker_result r = {.reason=PLW_INVALID_ARGUMENT, .cleanup_complete=true};
    if (!w || !epoch || !o || !o->connect || !o->send || !o->receive || !o->audio_start ||
        !o->capture || !o->submit || !o->audio_stop || !o->close ||
        !o->now_ms || !o->stopped) return r;
    void *v = o->context;
    if (o->stopped(v)) { r.reason=PLW_STOPPED_BEFORE_CONNECT; return r; }
    memset(w,0,sizeof *w);
    pocket_live_client_init(&w->speaker);
    if (!pocket_live_client_begin(&w->speaker,chat_id,(char *)w->transmit,sizeof w->transmit)) return r;
    pocket_live_v2_init(&w->microphone,1);
    int hello=snprintf((char *)w->transmit,sizeof w->transmit,
        "{\"type\":\"start\",\"version\":2,\"rate\":24000,\"channels\":2,"
        "\"format\":\"pcm_s16le\",\"reference\":\"es8311_dac_right\",\"chat_id\":\"%s\"}",chat_id);
    if(hello<0||(size_t)hello>=sizeof w->transmit)return r;
    worker_clock c={0};
    c.began=o->now_ms(v); c.last=c.began;
    r.reason=PLW_CONNECT_FAILED;
    if (!o->connect(v)) goto cleanup;
    if (!sample_clock(o,&c,&r)) goto cleanup;
    if (o->stopped(v)) { r.reason=PLW_STOPPED_BEFORE_CONNECT; goto cleanup; }
    if (c.last-c.began>=10000) { r.reason=PLW_START_TIMEOUT; goto cleanup; }
    r.reason=PLW_SEND_FAILED;
    if (!o->send(v,false,w->transmit,strlen((char *)w->transmit))) goto cleanup;
    for (;;) {
        if (!checkpoint(w,o,&c,&r)) break;
        pocket_live_worker_event e={0};
        if (!o->receive(v,&e,w->receive,sizeof w->receive)) { r.reason=PLW_CONNECTION_LOST; break; }
        if (!checkpoint(w,o,&c,&r)) break;
        if (e.kind==PLW_LOST) { r.reason=PLW_CONNECTION_LOST; break; }
        if (e.kind==PLW_ERROR) { r.reason=PLW_REMOTE_ERROR; break; }
        if (e.kind==PLW_CLOSED) {
            r.terminal_received=true; r.finalized=e.finalized; r.backend_pending=e.backend_pending;
            r.playback_pending=r.playback_pending||e.playback_pending;
            r.reason=e.finalized?PLW_STOPPED:PLW_UNCONFIRMED;
            break;
        }
        if (e.kind==PLW_READY) {
            if (c.stopping) continue; /* Late READY cannot activate audio after Stop. */
            if (!pocket_live_client_ready(&w->speaker)) { r.reason=PLW_PROTOCOL_ERROR; break; }
            c.audio=true; /* Partial setup also needs cleanup. */
            if (!o->audio_start(v)) { r.reason=PLW_AUDIO_ERROR; break; }
            if (!checkpoint(w,o,&c,&r)) break;
        } else if (e.kind==PLW_AUDIO) {
            if (!c.stopping) {
                pocket_live_v2_packet packet;
                if(e.bytes>sizeof w->receive||pocket_live_v2_unpack(w->receive,e.bytes,&packet)!=POCKET_LIVE_V2_OK||packet.kind!=2) {
                    r.reason=PLW_PROTOCOL_ERROR;break;
                }
                /*Reuse only validated mono FIFO/storage; no v1 rendering API.*/
                w->receive[2]=1;
                if(!pocket_live_client_speaker(&w->speaker,w->receive,e.bytes)) {
                    r.reason=PLW_PROTOCOL_ERROR;break;
                }
                r.playback_pending=true; /*Actual audible playout stays unknown.*/
            }
            /* In-flight speech after local stop is deliberately not played. */
            if (c.stopping) r.playback_pending=true;
        } else if (e.kind!=PLW_NONE) { r.reason=PLW_PROTOCOL_ERROR; break; }
        if (!checkpoint(w,o,&c,&r)) break;
        if (!c.audio || c.stopping) continue;
        const uint8_t *pcm=NULL; size_t bytes=0;
        if (pocket_live_client_playback_peek(&w->speaker,&pcm,&bytes)) {
            int submitted=o->submit(v,w->speaker.submitted_samples,pcm,bytes);
            if(submitted<0||submitted>1||(submitted==1&&!pocket_live_client_playback_submitted(&w->speaker))) {
                r.reason=PLW_AUDIO_ERROR;break;
            }
        }
        if (!checkpoint(w,o,&c,&r)) break;
        if (c.stopping) continue;
        int captured=o->capture(v,&w->capture);
        if(captured<0||captured>1) { r.reason=PLW_AUDIO_ERROR;break; }
        if (!checkpoint(w,o,&c,&r)) break;
        if(!captured||c.stopping)continue;
        pocket_live_v2_packet packet={1,w->capture.sequence,w->capture.sample_offset,
            w->capture.dma_consumed,w->capture.pcm,sizeof w->capture.pcm};
        pocket_live_v2_state next=w->microphone;
        size_t packed=0;
        if(w->capture.epoch!=epoch||
            pocket_live_v2_accept(&next,&packet,w->speaker.submitted_samples)!=POCKET_LIVE_V2_OK||
            pocket_live_v2_pack(&packet,w->transmit,sizeof w->transmit,&packed)!=POCKET_LIVE_V2_OK) {
            r.reason=PLW_AUDIO_ERROR;break;
        }
        if (!checkpoint(w,o,&c,&r)) break;
        if(c.stopping)continue;
        if(!o->send(v,true,w->transmit,packed)) { r.reason=PLW_SEND_FAILED;break; }
        w->microphone=next;
    }
cleanup:
    r.playback_pending = r.playback_pending || w->speaker.speaker.next_sample_offset>0;
    pocket_live_client_stop(&w->speaker);
    if (c.audio && !o->audio_stop(v)) { r.reason=PLW_CLEANUP_FAILED; r.cleanup_complete=false; }
    if (!o->close(v)) { r.reason=PLW_CLEANUP_FAILED; r.cleanup_complete=false; }
    pocket_live_client_closed(&w->speaker);
    return r;
}
