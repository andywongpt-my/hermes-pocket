# Stage B synthetic loopback TLS fixture (not physical qualification)

`pocket.qualification_tls.QualificationTLSServer` is an explicitly invoked async
context manager. It does not import production server, device bridge, provider,
settings, credential, SDK, or audio-output modules. There is no CLI/autostart.
Only a caller-created `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` and a caller-supplied
32–128 character URL-safe synthetic token are accepted. No certificate/key/token
is found, generated, saved, or provisioned by the fixture. Tests create a temporary
self-signed certificate/key with OpenSSL and remove their temporary directory.

```python
async with QualificationTLSServer(ssl_context=test_server_context,
        token=ephemeral_synthetic_token, deadline_seconds=30) as fixture:
    # Explicit test client trusts test certificate and verifies TLS identity.
    # Connect to wss://127.0.0.1:<fixture.port>/qualification/v2/live
    # Authorization: Bearer <ephemeral_synthetic_token>
    await fixture.wait_closed()
    numeric_report = fixture.summary()
```

Only literal loopback `127.0.0.1` (default) or `::1` is allowed. The exact route is
`/qualification/v2/live` (no query, production route, redirects, or fallback).
Wrong path/auth fail before admission. At most one authenticated attempt is
admitted; exit, disconnect, Stop, invalid stream or expiry closes the listener.
Reentry is prohibited. Future device-reachable endpoint/certificate/configuration
requires separate approval and is **not supported** by this loopback-only fixture.
Client-side trust and hostname verification must remain enabled.

## Wire and limits

Start uses the actual worker PLv2 fields: `type=start`, `version=2`, `rate=24000`,
`channels=2`, `format=pcm_s16le`, `reference=es8311_dac_right`, and exact
`chat_id=qualification`. Ready/closed/error exactly
match the actual C worker's strict control parser. Duplicate JSON keys fail.

Binary header is network-order `!2sBBIII`: magic `PL`, version 2, kind 1 capture
or kind 2 speaker, sequence, source sample offset, cumulative DMA credit.
Capture must be exactly 240 interleaved native 24 kHz PCM16 mic-left/right-reference
pairs, 960 payload bytes / 976 wire bytes. Capture sequences and offsets are exact;
credit must be monotonic and no greater than transmitted speaker samples.
The fixture validates/discards pairs, never substitutes source for reference.

- Independent overall requested deadline >0 and <=60 seconds, including idle
  startup; bounded close grace is reserved. Software timeouts cannot preempt a
  stalled event loop/OS. No repeat, reconnect, or expiry extension.
- Capture at most 6,000 frames / 5,856,000 application wire bytes.
- Speaker at most 144,000 samples (six seconds), 300 full-size frames /
  292,800 application wire bytes, including zeros. Lower caps are accepted.
- Every output sample is zero. **No audible mode exists.** Device test volume must
  be zero. This is a silent flow/credit test, not a 60-second continuous output or
  acoustic/AEC experiment. Any future nonzero capability needs separate explicit
  audible authorization, approved bounded pattern, <=512 digital peak and <=6s.
- Speaker source pending window <=1,920 samples; packets <=480 mono samples.
  Receive remains responsive to Stop when credit is exhausted.
- WebSocket messages <=1,024 bytes, queue high-water four frames, write high-water
  4,096 bytes; TLS/WebSocket/OS buffers add library-dependent overhead. Limits above
  count application messages, not TLS/TCP framing/handshake bytes. No unbounded
  application PCM list/queue/history; incoming PCM is not retained or logged.

Closed `finalized=true` means the peer requested Stop and the synthetic protocol
was finalized, **not** device audio cleanup or physical acceptance. On expiry or
protocol failure no passing terminal is fabricated. `actual_playout` is always
`unknown`; summary `actual_playout_known` and `physical_acceptance` are always 0.
Summary is a fixed numeric allowlist; no PCM, addresses, tokens, arbitrary peer
strings or transcript. Dropping token/PCM references does not promise secure RAM,
swap or crash-dump erasure. No automatic file or raw retention exists.

## Verification

`python -m unittest tests.test_qualification_tls -v` tests real verified loopback
TLS startup, continuous paired flow, bounded output, exhausted credit + Stop,
wrong route/auth/certificate, disconnect, malformed/oversized frames, capture caps,
strict start, independent deadline and deterministic listener shutdown. The C
worker test requires explicit `POCKET_CJSON_DIR` pointing at existing pinned local
cJSON sources and GCC; nothing is downloaded. It compiles a tiny new wrapper around the **unchanged** existing
`tests/fixtures/live_v2_worker_harness.c` (only adapting its hardcoded chat ID to
`qualification`), transports actual C IPC over verified WSS,
and checks continuous 1,300-frame capture and Stop under four-packet queue pressure.
All PCM is synthetic, no device/audio I/O or paid/provider request occurs.
