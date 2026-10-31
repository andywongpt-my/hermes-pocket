/* MIT. Cooperative network/capture pump; playback progresses independently in DMA. */
#include "pocket_live_worker.h"
#include <string.h>

_Static_assert(sizeof(pocket_live_worker) <= 7600, "Live worker memory budget exceeded");

pocket_live_worker_result pocket_live_worker_run(pocket_live_worker *w,
    const pocket_live_worker_ops *o, const char *chat_id) {
    pocket_live_worker_result r = {.reason=PLW_INVALID_ARGUMENT, .cleanup_complete=true};
    if (!w || !o || !o->connect || !o->send || !o->receive || !o->audio_start ||
        !o->capture || !o->submit || !o->rendered || !o->audio_stop || !o->close ||
        !o->now_ms || !o->stopped) return r;
    void *v = o->context;
    if (o->stopped(v)) { r.reason=PLW_STOPPED_BEFORE_CONNECT; return r; }
    memset(w,0,sizeof *w);
    pocket_live_client_init(&w->client);
    if (!pocket_live_client_begin(&w->client,chat_id,(char *)w->transmit,sizeof w->transmit)) return r;
    bool audio=false, stopping=false;
    uint64_t began=o->now_ms(v), stop_at=0, last=began;
    r.reason=PLW_CONNECT_FAILED;
    if (!o->connect(v)) goto cleanup;
    if (o->stopped(v)) { r.reason=PLW_STOPPED_BEFORE_CONNECT; goto cleanup; }
    r.reason=PLW_SEND_FAILED;
    if (!o->send(v,false,w->transmit,strlen((char *)w->transmit))) goto cleanup;
    for (;;) {
        uint64_t now=o->now_ms(v);
        if (now<last) { r.reason=PLW_PROTOCOL_ERROR; break; }
        last=now;
        if (!stopping && (o->stopped(v) || now-began>=300000)) {
            stopping=true; stop_at=now;
            if (audio) {
                if (!o->audio_stop(v)) { r.reason=PLW_CLEANUP_FAILED; r.cleanup_complete=false; break; }
                audio=false;
            }
            pocket_live_client_stop(&w->client);
            static const uint8_t stop[]="{\"type\":\"stop\"}";
            if (!o->send(v,false,stop,sizeof stop-1)) { r.reason=PLW_SEND_FAILED; break; }
        }
        if (stopping && now-stop_at>=3000) { r.reason=PLW_FINALIZE_TIMEOUT; break; }
        if (!stopping && w->client.phase==POCKET_LIVE_STARTING && now-began>=10000) {
            r.reason=PLW_START_TIMEOUT; break;
        }
        pocket_live_worker_event e={0};
        if (!o->receive(v,&e,w->receive,sizeof w->receive)) { r.reason=PLW_CONNECTION_LOST; break; }
        if (e.kind==PLW_LOST) { r.reason=PLW_CONNECTION_LOST; break; }
        if (e.kind==PLW_ERROR) { r.reason=PLW_REMOTE_ERROR; break; }
        if (e.kind==PLW_CLOSED) {
            r.terminal_received=true; r.finalized=e.finalized; r.backend_pending=e.backend_pending;
            r.playback_pending=r.playback_pending||e.playback_pending;
            r.reason=e.finalized?PLW_STOPPED:PLW_UNCONFIRMED;
            break;
        }
        if (e.kind==PLW_READY) {
            if (stopping || !pocket_live_client_ready(&w->client)) { r.reason=PLW_PROTOCOL_ERROR; break; }
            audio=true; /* Partial setup also needs cleanup. */
            if (!o->audio_start(v)) { r.reason=PLW_AUDIO_ERROR; break; }
        } else if (e.kind==PLW_AUDIO) {
            if (!stopping && (e.bytes>sizeof w->receive ||
                !pocket_live_client_speaker(&w->client,w->receive,e.bytes))) {
                r.reason=PLW_PROTOCOL_ERROR; break;
            }
            /* In-flight speech after local stop is deliberately not played. */
            if (stopping) r.playback_pending=true;
        } else if (e.kind!=PLW_NONE) { r.reason=PLW_PROTOCOL_ERROR; break; }
        if (!audio || stopping || o->stopped(v)) continue;
        const uint8_t *pcm=NULL; size_t bytes=0;
        if (pocket_live_client_playback_peek(&w->client,&pcm,&bytes)) {
            if (!o->submit(v,w->client.submitted_samples,pcm,bytes) ||
                !pocket_live_client_playback_submitted(&w->client)) { r.reason=PLW_AUDIO_ERROR; break; }
        }
        uint32_t played=0;
        if (!o->rendered(v,&played) || !pocket_live_client_rendered(&w->client,played)) {
            r.reason=PLW_AUDIO_ERROR; break;
        }
        size_t received=0, capacity=sizeof w->capture-w->captured;
        if (!o->capture(v,w->capture+w->captured,capacity,&received) || received>capacity || received%2) {
            r.reason=PLW_AUDIO_ERROR; break;
        }
        w->captured+=received;
        if (o->stopped(v)) continue;
        if (w->captured==sizeof w->capture) {
            size_t packed=0;
            if (!pocket_live_client_microphone(&w->client,w->capture,sizeof w->capture,
                w->transmit,sizeof w->transmit,&packed)) { r.reason=PLW_PROTOCOL_ERROR; break; }
            if (!o->send(v,true,w->transmit,packed)) { r.reason=PLW_SEND_FAILED; break; }
            w->captured=0;
        }
    }
cleanup:
    r.playback_pending = r.playback_pending || w->client.speaker.next_sample_offset>w->client.rendered_samples;
    pocket_live_client_stop(&w->client);
    if (audio && !o->audio_stop(v)) { r.reason=PLW_CLEANUP_FAILED; r.cleanup_complete=false; }
    if (!o->close(v)) { r.reason=PLW_CLEANUP_FAILED; r.cleanup_complete=false; }
    pocket_live_client_closed(&w->client);
    return r;
}
