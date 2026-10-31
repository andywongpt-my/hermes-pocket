# Stage B transient USB configuration and numeric observation

`pocket.qualification_tls_control` is an inactive, explicit host helper. It never
opens/enumerates/resets a serial device, controls DTR/RTS, opens a TLS socket,
discovers credentials, reads configuration files, changes services/settings,
retains PCM, invokes a provider, or starts an experiment automatically.

The caller supplies an already-open, independently identity-verified transport
with bounded cancellation-cooperative `read(maximum)` and `write(bytes)` methods.
Transport lifetime and control-line safety remain the caller's responsibility.
This cloud implementation is tested only using synthetic fake USB transports.
Nothing here authorizes using a physical device or provisioning a reachable host.

## API

- `TLSConfig(host, port, token, ca_pem, duration_ms=60000)` is frozen and
  repr-redacted. The host must be canonical numeric RFC1918 IPv4, port 1..65535,
  token 32..128 URL-safe ASCII characters, duration 1..60000 ms, and custom public
  PEM CA at most 768 bytes. PEM outline validation is **not** certificate trust,
  signature, chain, or hostname validation; the actual TLS client must do that.
  Oversized certificates must be rejected, never truncated.
- `arm_tls(config, nonce=..., profile=1)` returns a `qualification.Record` whose
  payload is excluded from repr. `qualification.encode` supplies HPQ1 framing and
  CRC. This pure constructor performs no device/network action.
- `TLSCollector(transport, approved_identity, config, capture_approved=True,
  profile=1, nonce=..., deadline_seconds=75)` performs one attempt. Identity must
  exactly match `transport.identity`; no guessing or retry. Default profile 1 is
  SILENT. Profile 2 AUDIO additionally requires `audible_approved=True` as distinct
  explicit authority; zero requested volume never implicitly changes the profile.
  Device-side fresh physical confirmation is independently required.
- `await collector.run()` returns `TLSObservation`; `.report()` is numeric-only.
  `numeric_json(report)` enforces a fixed numeric field allowlist. There are no
  file-writing or raw-retention APIs.

ARM_TLS carries transient host/token/CA over the explicitly authorized USB link;
it does not create an endpoint or grant network-listening authority. The loopback
host fixture remains intentionally unreachable by a physical device. Explicit
future endpoint, certificate, device identity and local capture authority are
separate prerequisites.

## Lifetime and safety semantics

Authoritative wire layout: [`QUALIFICATION-WIRE.md`](QUALIFICATION-WIRE.md).
Host direction sends kind 4 ARM_TLS at sequence 0 and bounded KEEPALIVE/STOP
controls thereafter. Device direction must be exactly kind 19 TLS_START sequence
0 and kind 20 TLS_TERMINAL sequence 1, matching nonce/profile/epoch and native
24 kHz paired format. No USB PCM or production Live route is accepted.

The total host observation deadline is at most **75 seconds from ARM**, not from
START: up to 10s admission/physical arm, 60s device run, 3s finalization, 2s slack.
Receiving START never refreshes it. This does **not** extend the device's original
physical-request deadline of <=60s. A trusted event-loop clock bounds operations
independently of the injected clock; injected regression aborts, and a stalled
injected clock cannot prevent real-time expiry. Time is reserved for one bounded
STOP attempt inside the observation budget. Control writes have <=50ms timeout;
heartbeats are scheduled at 10ms checkpoints using trusted loop time; a full
read quantum is at most 40ms, including while
waiting for physical confirmation. The collector stops early rather than start
a read without its full quantum inside the observation budget. It never cancels
a healthy poll merely to reach a heartbeat tick. No failed/partial control write
is retried.
Software deadlines cannot preempt blocking callbacks, a hung event loop or SDK.

Malformed, oversized, discontinuous, wrong-profile/nonce/epoch, corrupt or missing
records fail incomplete. The live collector stops at the exact validated terminal
boundary: normal console can resume after checked device cleanup/drain. Any
same-read remainder is discarded without parsing/logging/retaining it, and no
further read occurs. `wire_bytes` counts the 208 framed bytes for a complete two-
record receipt; prefix bytes are separate, and resumed console is excluded.
Offline Parser/Observation validation stays strict; this live boundary never
permits skipping corruption before the terminal. Parser memory/wire/prefix caps
are inherited from HPQ1. No resynchronization follows corruption. Cancellation attempts bounded Stop
and propagates cancellation; device heartbeat/physical Stop remain independent.

`terminal_received` means a valid USB terminal receipt. `tls_terminal_received`
means the device reported the synthetic TLS peer's terminal. `protocol_complete` requires
checked terminal/finalization and verified audio/network cleanup, with no pending
backend, source rejection or clock error; neither
is physical acceptance. Quarantine remains an incomplete receipt, never a passing
cleanup. Unknown metrics/times retain `UINT32_MAX`, not invented zero. Reports
always set `actual_playout_known=0` and `physical_acceptance=0`; DMA credit is not
an acoustic timestamp. Remote/backend state is meaningful only with its reported
TLS terminal receipt.

Config/ARM references are dropped when no longer needed. Python immutable object,
caller/transport copies, OS buffers, swap and crash dumps are not securely erased
by dropping references. Never log the outgoing packet or retain real credentials.
The public certificate, address, token, arbitrary device text and PCM never enter
numeric reports. Tests use synthetic configuration and no production credentials.

`protocol_complete` does not establish requested run-duration coverage. Worker
reason zero means stopped, not acoustic success. The raw deadline flag is retained. A normal deadline-triggered Stop can be
protocol-complete when its terminal/finalization and audio/network cleanup are
verified. The deadline flag alone is neither failure nor physical acceptance.
Observed Stop/elapsed times remain separate coverage facts; no minimum capture
count or full-duration coverage is invented.
