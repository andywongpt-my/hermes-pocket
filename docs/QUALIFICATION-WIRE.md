# Qualification HPQ1 wire version 1

This is separate from production Live v1/v2. It does not grant physical authority.
All integers are little-endian; PCM is signed 16-bit little-endian. The core has no
IDF, allocation, network or file dependency. A host ARM plus fresh physical
confirmation is required outside the core. One persistent core accepts one run.

## Header and framing

Fixed 32-byte Python layout `<4sBBBBQIIHHI>`:

| Offset | Field | Requirement |
|---:|---|---|
| 0 | magic[4] | `HPQ1` |
| 4 | version u8 | 1 |
| 5 | kind u8 | listed below |
| 6 | profile u8 | 1 SILENT; 2 AUDIO; 3 reserved, rejected |
| 7 | flags u8 | 0 |
| 8 | host run nonce u64 | nonzero, fresh one-use value |
| 16 | sequence u32 | strict per-direction sequence, no wrap |
| 20 | capture sample offset u32 | PCM only; other kinds 0 |
| 24 | payload bytes u16 | at most 1024 |
| 26 | reserved u16 | 0 |
| 28 | CRC u32 | CRC32 IEEE of header bytes 0..27, then payload |

CRC is `zlib.crc32(payload, zlib.crc32(header[:28]))`. There is no padding after
the header. Maximum framed stream is 3 MiB; maximum raw PCM is 2,304,000 bytes.
Before the first magic, at most 16 KiB of boot prefix noise may be counted and
discarded with a rolling magic matcher, not retained. After framed parsing begins,
corruption is terminal: never scan for another magic or silently resynchronize.
Host parser scratch is at most 8 KiB. Ordinary console/ROM/panic contamination
invalidates the run; it is not a recoverable missing frame.

## Host controls

All have offset 0 and the same nonce/profile. Host sequence starts at 0 for ARM
and then advances exactly once for each KEEPALIVE/STOP; no retry or ARM replay.

- **1 ARM**, 16 bytes `<4I>`: `24000, target_frames, 1024, 3145728`, where target
  is 1000 SILENT or 2400 AUDIO. Values are exact; no downgrade negotiation.
- **2 KEEPALIVE**, empty payload, sequence >0.
- **3 STOP**, empty payload, sequence >0.

No ARMACK. Heartbeats continue while waiting for physical confirmation. The
adapter requests Stop after at most 500 ms without valid matching host heartbeat.
The collector's entire Stage A deadline is at most 45 seconds. Missing host,
queue loss, stale generation, expired arm or invalid control aborts admission/run.

## Device records

Device sequence starts at 0 for START, independently of host control sequence.
START is emitted only after physical confirmation when the finite runner is
invoked, before muted codec setup. PCM records then advance the same sequence;
TERMINAL follows cleanup. The last PCM is **never** a success receipt.

### 16 START — 48 bytes, `<12I>`

`epoch, rate, channels, pairs_per_frame, target_frames, source_sample_cap,
stimulus_sample_cap, peak_cap, requested_volume, startup_budget_ms,
acquisition_budget_ms, waveform_id`.

Epoch is nonzero. Fixed values: rate 24000, channels 2, pairs 240, startup 2000.
SILENT: target 1000, source/stimulus/peak/volume/waveform 0, acquisition 10250.
AUDIO: target 2400, source 576000, stimulus 144000, peak 512, requested volume
0..5, acquisition 24250, waveform 1. Volume is requested percent, not measured
SPL or proof of actual unmute; saved mute must remain respected.

### 17 PCM — 992 bytes

First 32 bytes `<8I>`:
`epoch, original_capture_sequence, observed_elapsed_ms, source_submitted_samples,
dma_consumed_samples, queue_frames_observed, reserved0, reserved0`.
Then 960 bytes: 240 interleaved pairs `[microphone LEFT, DAC-reference RIGHT]`.
Header offset equals original capture sequence times 240. Capture epoch/sequence/
offset must remain contiguous, with no silent dropping. Unknown queue occupancy
is `UINT32_MAX`, never fabricated zero; known occupancy is 0..8. DMA consumption
is flow credit, bounded by source already accepted, never actual audible playout.

Observed time is sampled by the owner task after capture polling, relative to
successful setup return. It is **not** an ISR timestamp, true-clock certificate,
or proof that capture offset and submitted source time coincide.

### 18 TERMINAL — 48 bytes, `<12I>`

`epoch, result_code, cleanup_disposition, capture_frames, source_submitted_samples,
stimulus_submitted_samples, last_dma_consumed, elapsed_observed_ms,
peak_submitted, wire_bytes_before_terminal, source_complete, reserved0`.

Cleanup: 0 not acquired, 1 checked end succeeded, 2 failed/unknown. Unknown begin
ownership must never be ended as if it were acquired. Source complete is a Boolean
about **submitted** source only, not audible drain. SILENT's source cap is zero.
Result codes: 0 complete, 1 stopped, 2 start failed, 3 startup timeout,
4 acquisition timeout, 5 clock regression, 6 capture error, 7 discontinuity,
8 submit error, 9 emit error, 10 cleanup error, 11 source incomplete,
12 invalid argument.

Complete requires exact target frames, cleanup 1 and source_complete 1. Counters
must match exported PCM and the defined caps. Terminal elapsed time is observed
**after checked end**, so may exceed acquisition budget due to cleanup; before
acquisition starts, it is relative to startup. A software deadline cannot preempt
a stalled SDK call. Missing or corrupt terminal means incomplete, not success.
Any partial emit is fatal and no subsequent terminal is appended to a corrupted
stream. Atomic no-byte backpressure may wait at most 20 observed milliseconds;
source submit uses the same busy bound and retains exactly the unaccepted frame.
Busy emit/submit also cap at 20 attempts; total capture polls cap at twice the
acquisition millisecond budget. These conservative iteration fallbacks ensure a
stalled injected clock cannot produce an endless loop; they never extend a
physical time or sample budget.

## Waveform 1, source reconstruction only

At 24 kHz, active source windows are `[96000,144000)`, `[240000,288000)` and
`[432000,480000)` — [4,6), [10,12), [18,20) seconds. All other offsets, including
all offsets >=576000, produce zero. There are at most 144000 scheduled stimulus
samples (6 seconds), including ramp endpoints. Accepted zero gaps/tail count
within the 576000-sample source cap. Captured right lane alone is AEC reference.

For unsigned 32-bit index `i` (every multiply/add wraps modulo 2^32):

```
x = i + 0x9e3779b9
x ^= x >> 16
x *= 0x7feb352d
x ^= x >> 15
x *= 0x846ca68b
x ^= x >> 16
white(i) = (x & 65535) - 32768
```

For an active global offset `t`, compute the sum of white(t-i), i=0..7, minus
that sum for i=8..15. Divide by 1024, truncating toward zero, to obtain `base`.
For local window offset `u`, gain=min(u,47999-u,240). Output is base*gain/240,
again truncating toward zero. This is a deterministic 16-tap difference of moving
averages with 10 ms endpoint ramps. It is band-shaped noise, not a single tone or
an ideal brick-wall spectrum; digital absolute amplitude is bounded by 512.
Quiet source limits do not imply a calibrated safe SPL or usable echo SNR.

`tests/fixtures/qualification_waveform_v1.json` contains synthetic cross-language
vectors. Only compare reconstructed source for correlation/delay diagnostics;
never feed it to Speex as a substitute for the actual paired reference.

## Stage B reserved qualification transport records

The same version 1 header supports kind 4 `ARM_TLS`, 19 `TLS_START`, and 20
`TLS_TERMINAL`. Profiles remain SILENT=1 and AUDIO=2. These records do not enable
the production Live gate. There are no USB PCM records in Stage B.

`ARM_TLS` is host sequence 0, offset 0: nine little-endian uint32 values,
followed by the exact host, token, and CA byte strings (no terminators):

1. 24000; 60000; 1440000; 1024; 3145728 (fixed budgets).
2. Port 1..65535; requested duration 1..60000 ms.
3. Host length in low 16 bits, token length in high 16 bits.
4. CA length in low 16 bits, high 16 bits zero.

Host length is 7..15, token length 32..128, and CA length 1..768; exact total
payload length is 36 plus these lengths, at most 947. No truncation or downgrade.
The framing codec checks budgets and lengths. The control layer additionally
checks canonical private IPv4, URL-safe token characters, and PEM certificate
outline; the TLS implementation must perform actual certificate validation.
The 768-byte CA limit is an explicit compatibility limit, not an invitation to
truncate a certificate. Credentials are RAM-only and must not be logged.

`TLS_START` is device sequence 0, offset 0, 12 uint32 values: epoch, 24000, 2,
240, requested duration, 6000 maximum capture frames, 1440000 maximum source
samples, stimulus cap (0 SILENT / 144000 AUDIO), requested volume 0..5,
dispatch delay ms, 10000 dispatch budget ms, reserved zero.

`TLS_TERMINAL` is device sequence 1, offset 0, 24 uint32 values: epoch,
worker reason, cleanup disposition, capture frames, capture samples, last DMA
credit, source accepted, source rejected boolean, deadline expired boolean,
clock failed boolean, Stop observed ms, audio stop begin ms, audio stop end ms,
audio clean boolean, network clean boolean, terminal received boolean,
finalized boolean, backend pending boolean, minimum free heap, minimum largest
block, minimum task stack, monitor count, elapsed ms, reserved zero.
Times are relative to the original physical request. Unknown times/metrics use
UINT32_MAX, never fabricated zero. Cleanup values remain 0 not acquired, 1
verified, 2 unknown. A terminal may report quarantine with ownership retained;
later cleanup must not rewrite that history or automatically retry a session.
None of these fields constitutes an acoustic pass.

## Normal-app Stage A owner binding

`CONFIG_POCKET_QUALIFICATION` includes a private sentinel 4 branch in the existing
voice worker, before legacy HTTP/configuration handling. It does not create a
second audio task. The UI callback records physical generations immediately;
main alone reads bounded USB control chunks. Admission reserves the existing
setup/voice lease, closes reader admission, and freezes volume to zero for
SILENT or min(current user volume, 5) for AUDIO. The audio adapter preserves
saved mute intent and does not enable output at construction or begin.

The persistent runner/audio bundle is allocated only after fresh physical
admission and dispatch validation. Startup first obtains the quiet USB export
lease and drains old console output for at most two observed seconds / 2000
iterations. No BSP operation precedes that barrier. The core distinguishes
known untouched begin rejection from acquired cleanup and foreign/unknown
ownership. Only an acquired bracket is ended. Stop before BSP acquisition is
not falsely reported as a verified hardware restore.

After audio cleanup, terminal TX drain is bounded to 100 observed ms / 100
iterations and does not wait for another heartbeat. Queue drain is **not host
receipt**. A missing or undrained terminal is an experiment/transport failure,
but verified-clean audio can still release its owner. Unknown/failed audio
cleanup retains the bundle and setup lease, keeps USB logging quiet, marks audio
unavailable, and never automatically retries I2C cleanup. Copied PCM is scrubbed;
this is not a claim that every hardware DMA residual is erased. Screen Off is
still the normal documented standby action, not a physical power cut.

The shared dispatcher selects a distinct Stage B bundle for ARM_TLS, never a
Stage A fallback. Stage B constructs an HTTPS origin only from the one-use RAM
request, with fixed chat `qualification` and target 0. It never reads saved
settings. Profiles are explicit, including AUDIO at requested volume zero.
Only START and TERMINAL records are emitted on USB, never Stage B PCM. Numeric
heap/stack observations do not perform I/O; the free-heap low-water value can
include the earlier boot history, and unknown metrics remain UINT32_MAX.

A Stage B terminal is an immutable historical outcome. Known-clean audio with
pending network cleanup retains its owner/configuration/storage and quiet USB,
while the same worker attempts cleanup only at most four times per second.
It never starts another session or resends terminal history. Audio or unknown
cleanup failure is permanently quarantined rather than retried. Final verified
cleanup scrubs owned storage before releasing the normal voice lease.
Production Live admission remains unconditionally false. Normal background activity can occur
before/after qualification; Stage A itself does not read saved credentials,
issue provider/network requests, write NVS, or persist captured audio.

Stage B `network_clean` is copied from the actual transport cleanup state,
not inferred from absence of a pending flag. A clean session that never attempted
BSP audio reports cleanup 0 and does not label the UI hardware cleanup checked.
A verified audio restore requires an attempted audio bracket and confirmed clean
end. On pinned ESP-IDF/RISC-V, `min_task_stack` is bytes; unavailable metrics
remain UINT32_MAX. Timestamp and generation for physical admission are sampled
together under the voice mutex and returned to the input queue, so crossing a
millisecond boundary cannot invalidate a legitimate matching event.

Before a clean Stage B terminal is serialized, owned transport teardown and
scrubbing must already succeed. A scrub refusal changes aggregate cleanup to
unknown/failure and retains the owner, even if the numeric audio/network clean
flags were independently true. Retired qualification packet buffers are
volatile-scrubbed after the worker returns, including pending/unknown cleanup
paths: worker capture/receive/transmit/speaker storage and IDF message/chunk/
mutable-send storage. Numeric microphone counters and necessary cleanup/DNS/
configuration ownership remain intact. BSP/SDK DMA memory is not covered by this
claim; failed codec cleanup remains quarantined.
