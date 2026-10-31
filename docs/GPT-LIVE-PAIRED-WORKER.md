# Paired-reference Live v2: firmware source checkpoint

The v2 public entry returns `ESP_ERR_NOT_SUPPORTED` with
`PLW_DEVICE_VALIDATION_REQUIRED`. This is a **physical-acceptance gate**, not a
claim that digital reference source is unavailable. No setting/boolean attests
that acoustics have been tested. The optional normal-app caller uses a separate
always-false admission query and cannot execute this internal path. V1 remains blocked
because DMA completion cannot establish its actual-played counter.

## Source path

`pocket_live_v2_stream` implements version-2 framing without changing v1. Every
microphone packet contains 240 interleaved mic-left/DAC-reference-right int16LE
pairs at native 24 kHz: 960-byte payload plus 16-byte header, 10 ms. Sequence and
sample offset advance by one packet and 240 microphone samples. Field12 is an
observed **DMA-consumed mono speaker-source watermark**, used only as flow credit.
It is not a sample clock alignment, analog playout position or audible completion.
Speaker packets contain 1–480 mono samples and zero in field12.

The exact start control is:

```json
{"type":"start","version":2,"rate":24000,"channels":2,"format":"pcm_s16le","reference":"es8311_dac_right","chat_id":"chat_one"}
```

The authenticated route remains `/targets/<target>/live`; host hello-version
admission chooses the strict v2 adapter. Cross-version packets/controls fail.
Ready requires version2, rate24000 and `reference:"es8311_dac_right"`. Closed
requires the normal finalization/pending/reason fields plus
`actual_playout:"unknown"`. The C control validator rejects duplicate/extra keys,
escaped aliases, fractional rates/versions, missing/incorrect reference and a
false claim of known actual playout.

`pocket_live_v2_worker` uses the existing bounded mono speaker FIFO but **never
calls the v1 rendered-counter API**. Capture metadata comes from the BSP's owned
paired-RX queue, not network-task timestamps. Epoch, sequence, offsets and credit
are checked before upload. Busy submission retains the original frame; it is
copied/credited exactly once when space exists. Actual-playout-known is always
false; any speaker audio keeps playback-pending conservative even after DMA credit.

`pocket_live_v2_audio` now uses the separate Live begin/enable/end lease plus
paired reference-poll and render-submit/status APIs. Begin handles cold/asleep/awake
entry and remains muted; only a valid speaker submission may explicitly enable
output, with a fresh Stop check after register I/O and before PCM admission. No ordinary `i2s_channel_read` is used: that driver API may abandon
an old buffer when its queue is nearly full, before overflow notification. The
BSP callback copies complete RX frames into eight owned slots (80 ms), terminates
on queue/descriptor/discontinuity failure, and pairs mic/reference in the same RX
serial frame. It does not infer TX/RX ISR alignment. Codec/reference filter and
acoustic delay still need measurement; same-frame pairing does not mean zero delay.
Paired BSP suppresses unused TX-span queueing; the adapter's optional defensive
span drain does not form its AEC reference.

Audio setup/cleanup requires exclusive ownership but no longer assumes an
already-awake codec. The separate lease preserves normal format/volume/mute intent
and keeps the silent diagnostic no-output lease distinct. An acquired partial
start must be ended; unknown foreign ownership is never appropriated. Cleanup
mutes, stops reference mode, restores the saved format, verifies sleep, then
restores cached intent only. Failed cleanup remains quarantined, not advertised
as released ownership. See [inactive audio lease](GPT-LIVE-INACTIVE-AUDIO-LEASE.md).

## Network and build isolation

`pocket_live_v2_idf` now provides a persistent internal context constructor and
one-shot execute helper composing the paired worker with the deadline-aware
network binding described in [network notes](GPT-LIVE-NETWORK.md). It is private
and compile-checked, not exposed through the physical gate. The optional
[normal-app caller](GPT-LIVE-APP-CALLER.md) is default off and separately refuses
before Live setup; its build option does not permit physical admission. The
inactive [session controller](GPT-LIVE-SESSION-LIFECYCLE.md) validates copied
configuration, reserves one owner and retains context through pending cleanup. Pending DNS callbacks
and incomplete teardown require retained owner storage; `cleanup_complete=false`
is not permission to free/reuse context or delete a task inside SDK code. Stop
never claims to cancel Hermes work, and absent terminal status means backend state
unknown (`terminal_received=false`).

`CONFIG_POCKET_LIVE_PAIRED_COMPILE` is off by default. It depends on experimental
BSP callback mode and selects the inactive worker plus SDK `LWIP_SO_LINGER` support
for the experimental build only. TCP transport logs are compiled out because the
pinned SDK otherwise logs authenticated Upgrade bytes on some failures. No running
network/security/service configuration was changed. Default legacy behavior and
16-kHz endpoints remain intact.

## Smallest future no-key bench acceptance

These are **proposed checks, not performed operations**. They need separately
authorized device/bench timing; they require no provider account, API key, paid
session or new hardware.

1. Capture a short local paired stream while the same board plays deterministic
   low-level PCM. Verify 24k native rate, mic/reference lane ordering, pair counts,
   reference correlation with playback and no reference substitution by mic noise.
   Do not assume reference amplitude or codec/filter delay is identical to source.
2. Run the real Speex processor offline on those paired samples. Measure far-end
   residual and near-end/double-talk behavior; verify the microphone is never
   muted to simulate cancellation. Declare quality targets before the run and
   retain measured results, not synthetic pass labels.
3. Use an isolated synthetic local transport, not GPT-Live, to introduce bounded
   jitter and queue pressure. Verify contiguous frames or explicit terminal failure,
   never silent dropping, replay or unlimited queues. Check runtime free heap,
   largest allocation block and stack high-water marks under concurrent audio/TLS.
4. Stop during startup, streaming and partial writes; measure audio-stop and cleanup
   separately. A pending DNS/tcpip cleanup is reported and quarantined, never called
   complete. Confirm no new session can reuse an unresolved owner context.
5. Restore legacy16k and volume, verify codec reference routing is disabled, and
   repeat capture/playback/stop. No flash/settings write is implied by these checks.

Source tests/builds cannot establish any of those physical outcomes. Their
results must be recorded before replacing the physical gate with an audited,
explicitly activated device path.
