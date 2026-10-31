/* MIT. TEST ONLY: actual v2 worker/control/wire, bounded pipe transport and
 * synthetic co-captured mic/DAC audio. No hardware timing/playout claims.
 * Synthetic 10 ms audio ticks are paced at >=10 ms wall time. */
#define _POSIX_C_SOURCE 200809L
#include "pocket_live_v2_worker.h"
#include "pocket_live_v2_control.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

enum { RING=1920, HISTORY=1024, FRAMES=1300 };
typedef struct {
    pocket_live_v2_worker worker;
    uint8_t input[1027]; size_t input_used;
    int16_t source[RING],history[HISTORY];
    size_t head,count;
    uint32_t frames,consumed,submitted,received;
    unsigned busy,max_queued,max_pending;
    uint64_t next_capture;
    bool stop_credit,ahead_credit,active;
} fixture;
static uint64_t now_ms(void *v) {
    (void)v; struct timespec t;
    if(clock_gettime(CLOCK_MONOTONIC,&t)!=0)return 0;
    return (uint64_t)t.tv_sec*1000u+(uint64_t)t.tv_nsec/1000000u;
}
static bool write_all(const uint8_t *data,size_t bytes) {
    uint64_t deadline=now_ms(NULL)+1000u;
    while(bytes) {
        if(now_ms(NULL)>=deadline)return false;
        struct pollfd fd={STDOUT_FILENO,POLLOUT,0};
        int polled=poll(&fd,1,10);
        if(polled<0&&errno==EINTR)continue;
        if(polled<0||(fd.revents&(POLLERR|POLLHUP|POLLNVAL)))return false;
        if(!polled)continue;
        ssize_t n=write(STDOUT_FILENO,data,bytes);
        if(n<0&&(errno==EINTR||errno==EAGAIN))continue;
        if(n<=0)return false;
        data+=(size_t)n;bytes-=(size_t)n;
    }
    return true;
}
static bool record(uint8_t kind,const uint8_t *data,size_t bytes) {
    if(bytes>1024u)return false;
    uint8_t header[3]={kind,(uint8_t)(bytes>>8),(uint8_t)bytes};
    return write_all(header,sizeof header)&&write_all(data,bytes);
}
static bool send_frame(void *v,bool binary,const uint8_t *data,size_t bytes) {
    (void)v; return record(binary?'B':'T',data,bytes);
}
static bool connect_pipe(void *v) {(void)v;return true;}
static void observe(fixture *f) {
    unsigned queued=f->worker.speaker.queued_frames;
    if(queued>f->max_queued)f->max_queued=queued;
    f->received=f->worker.speaker.speaker.next_sample_offset;
    uint32_t pending=f->received-f->consumed;
    if(pending>f->max_pending)f->max_pending=pending;
}
static bool receive_frame(void *v,pocket_live_worker_event *event,uint8_t *data,size_t capacity) {
    fixture *f=v;observe(f);
    /* Read only enough for one record; a partial pipe record survives polling. */
    size_t needed=3u;
    if(f->input_used>=3u)needed=3u+((size_t)f->input[1]<<8)+f->input[2];
    if(needed>sizeof f->input)return false;
    if(f->input_used<needed) {
        struct pollfd fd={STDIN_FILENO,POLLIN,0};
        int polled=poll(&fd,1,1);
        if(polled<0&&errno==EINTR)return true;
        if(polled<0||(fd.revents&(POLLERR|POLLNVAL)))return false;
        if(!polled)return true;
        ssize_t n=read(STDIN_FILENO,f->input+f->input_used,needed-f->input_used);
        if(n<0&&(errno==EAGAIN||errno==EINTR))return true;
        if(n<=0) {event->kind=PLW_LOST;return true;}
        f->input_used+=(size_t)n;
        if(f->input_used<3u)return true;
        needed=3u+((size_t)f->input[1]<<8)+f->input[2];
        if(needed>sizeof f->input)return false;
        if(f->input_used<needed)return true;
    }
    size_t bytes=needed-3u;
    if(f->input[0]=='B') {
        if(bytes>capacity)return false;
        memcpy(data,f->input+3,bytes);event->kind=PLW_AUDIO;event->bytes=bytes;
    } else if(f->input[0]=='T') {
        if(!pocket_live_v2_control((const char *)f->input+3,bytes,event))return false;
    } else return false;
    f->input_used=0;return true;
}
static bool audio_start(void *v) {fixture *f=v;f->active=true;return true;}
static bool audio_stop(void *v) {fixture *f=v;f->active=false;return true;}
static bool close_pipe(void *v) {(void)v;return true;}
static int16_t decode_sample(const uint8_t *p) {
    uint16_t bits=(uint16_t)((uint16_t)p[0]|(uint16_t)((uint16_t)p[1]<<8));
    int32_t value=bits<=32767u?(int32_t)bits:(int32_t)bits-65536;
    return (int16_t)value;
}
static void encode_sample(uint8_t *p,int16_t sample) {
    uint16_t bits=(uint16_t)sample;p[0]=(uint8_t)bits;p[1]=(uint8_t)(bits>>8);
}
static int submit(void *v,uint32_t offset,const uint8_t *pcm,size_t bytes) {
    fixture *f=v;observe(f);
    if(!f->active||offset!=f->submitted||bytes==0u||bytes>960u||bytes%2u)return -1;
    size_t samples=bytes/2u;
    if(f->stop_credit||f->max_queued<4u||f->frames<12u||samples>RING-f->count) {f->busy++;return 0;}
    for(size_t i=0;i<samples;i++)f->source[(f->head+f->count+i)%RING]=decode_sample(pcm+2u*i);
    f->count+=samples;f->submitted+=(uint32_t)samples;return 1;
}
static int capture(void *v,pocket_live_v2_capture *out) {
    fixture *f=v;observe(f);
    uint64_t now=now_ms(v);
    if(now<f->next_capture)return 0;
    f->next_capture=now+10u;
    out->epoch=1;out->sequence=f->frames;out->sample_offset=f->frames*240u;
    for(uint32_t i=0;i<240u;i++) {
        uint32_t sample=f->frames*240u+i;
        int16_t reference=0;
        if(f->count) {reference=f->source[f->head];f->head=(f->head+1u)%RING;f->count--;f->consumed++;}
        f->history[sample%HISTORY]=reference;
        double echo=sample>=260u?.55*f->history[(sample-120u)%HISTORY]+.18*f->history[(sample-260u)%HISTORY]:0.;
        int near=f->frames>=1100u?(int)(1500.*sin(2.*3.14159265358979323846*719.*sample/24000.)):0;
        int16_t microphone=(int16_t)((int)echo+near);
        encode_sample(out->pcm+4u*i,microphone);encode_sample(out->pcm+4u*i+2u,reference);
    }
    out->dma_consumed=f->ahead_credit&&f->submitted?f->submitted+1u:f->consumed;
    f->frames++;return 1;
}
static bool stopped(void *v) {
    fixture *f=v;observe(f);
    return f->stop_credit?(f->frames>=20u&&f->received>=1920u):f->frames>FRAMES;
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    fixture f={0};
    if(!strcmp(argv[1],"stop-credit"))f.stop_credit=true;
    else if(!strcmp(argv[1],"ahead-credit"))f.ahead_credit=true;
    else if(strcmp(argv[1],"normal"))return 2;
    if(fcntl(STDIN_FILENO,F_SETFL,O_NONBLOCK)<0||fcntl(STDOUT_FILENO,F_SETFL,O_NONBLOCK)<0)return 2;
    pocket_live_v2_worker_ops ops={&f,connect_pipe,send_frame,receive_frame,audio_start,capture,submit,audio_stop,close_pipe,now_ms,stopped};
    pocket_live_v2_worker_result r=pocket_live_v2_worker_run(&f.worker,&ops,"chat_one",1);
    char report[1024];
    int n=snprintf(report,sizeof report,
        "{\"reason\":%d,\"finalized\":%s,\"terminal_received\":%s,\"cleanup_complete\":%s,"
        "\"actual_playout_known\":%s,\"backend_pending\":%s,\"playback_pending\":%s,\"busy_submits\":%u,\"max_queued\":%u,"
        "\"max_source_pending\":%u,\"submitted_samples\":%u,\"received_samples\":%u}",
        (int)r.reason,r.finalized?"true":"false",r.terminal_received?"true":"false",r.cleanup_complete?"true":"false",
        r.actual_playout_known?"true":"false",r.backend_pending?"true":"false",r.playback_pending?"true":"false",f.busy,f.max_queued,f.max_pending,
        f.submitted,f.received);
    if(n<0||(size_t)n>=sizeof report||!record('R',(const uint8_t *)report,(size_t)n))return 2;
    return 0;
}
