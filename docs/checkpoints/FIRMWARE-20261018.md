# Current publication verification: 2026-10-15

The integrated controls, debug and voice timing candidate was built from firmware
source `3a80286b83f4d04b42cc02c9aab8f1f6076afa61` and app-only flashed at `0x10000`.
Application size: 1,814,304 bytes. Application SHA256:
`63dd20c697c468830918db24d054fdc4f38da6934c4ce5f50f2276e23710ca57`.
Independent application and retained bootloader verification passed; the retained
partition table was unchanged. NVS, pairing, other partitions and security settings
were not written. Physical voice improvement and a complete microphone/Hermes/
playback round trip remain unverified.

- The final offline suite ran 172 unittest checks with zero failures/errors,
  five Windows/environment skips, a passing simulator and zero guarded external
  network attempts. Skips were the separate LVGL suite and four Linux-only
  containment/symlink checks. PR CI runs the Linux and firmware checks again.
- ESP-IDF 5.5.3 compilation/linking passed, with exact source/test manifest and
  compiled control/debug symbols verified. SDK settings are unchanged. Static
  BSS grew by 112 bytes relative to the preceding deployed firmware; the
  four-frame capture queue and 22,000-byte audio budget remain unchanged.
- Physical boot ran 10:16:44-10:18:14 UTC.
  The expected ELF reached `app_main`; 6 authenticated Linux HTTPS status
  cycles passed across 57.3 seconds, with backend availability and
  STT readiness observed. The bounded window had no Linux failure events,
  unexpected resets, fatal errors or Wi-Fi drops. Minimum post-request internal
  free heap was 45,120 bytes; minimum largest block was
  32,768 bytes. This establishes idle readiness.
- The device received Linux Usage HTTP 200 and decoded all three providers as not_configured with zero metrics.
  Real quotas, resets and allowance alerts remain unavailable until supported feeds are configured.
- The preceding firmware's owner recording still filled its four-frame queue:
  capture reason 2, 14,336 PCM bytes, 389 ms capture and 246 ms maximum upload.
  Its first-response timing used a later origin than the outer request duration;
  that gap did not uniquely identify a network wait. An earlier recording reached
  1,175 ms maximum upload. Neither failure is relabeled a successful recording.
- Successful reused audio chunks now avoid full heap scans and per-chunk
  diagnostics. New connections, automatic reconnects, begin/end and failure
  boundaries retain available heap/TLS diagnostics. Flags distinguish skipped
  pre-request measurements from measured zero. No audio frames are silently
  dropped and uncertain submissions are not retried.
- One deferred numeric upload summary reports the slowest request's connect,
  headers, first response, body, finish and perform-end offsets from a common
  HTTP-entry origin. Microsecond costs distinguish pre/post heap work, SDK
  perform, application callback, wrapper logging/JSON and deferred-summary output.
  SDK perform still includes transport/TLS/body processing, scheduler preemption
  and SDK event posting; callback time does not isolate event-queue wait. Wrapper
  post time excludes the caller's JSON deletion and next-frame preparation.
  The summary runs after capture/provider handling, never per successful chunk.
- Native regressions inject heap, body-wait, logging and JSON costs independently;
  preserve reconnect/error diagnostics; and keep slow diagnostics out of
  continuous PCM capture. They cover sustainable cadence, bounded jitter and an
  explicit abort without finish on a 1,175 ms stall. The formatted numeric record
  and strict new/legacy collector schemas also pass. Independent review is clear.
  These are implementation checks, not physical RF/voice performance evidence.
- The source package includes the Linux bridge's one-line TCP_NODELAY setting.
  A real loopback socket check observed the option change from 0 to 1 on health,
  status, Usage and unauthenticated responses, preserving 200/401 behavior. The
  Linux owner reported deployment and an isolated header-to-body wait reduction
  from 40.894 ms to 0.046 ms; this does not establish device improvement. The
  existing three-second socket timeout, TLS/hostname verification and auth remain.
- Existing client TCP_NODELAY, capture-only awake Wi-Fi and exact prior power-mode
  restoration remain. Restoration failure blocks finish and retains bounded
  recovery. Cancellation, volume, target preferences, task browsing, reminders,
  wake/setup handling and phase-based audio cleanup are retained and source-tested.
- The unchanged UI previously rendered all 42 states at 240x320 with cached LVGL
  9.5 and the device's 24 KiB pool. CI renders this publication again. Physical
  button/display/battery acceptance and saved target across reboot remain open.
  Tasks are limited to twelve device-scoped Pocket tasks, with a 768-byte UTF-8
  device preview and 8000-byte serialized host response bound. Todo has no
  supported read-only source. Real Usage feeds remain unconfigured.
- Target-scoped TLS/verify/heap, status, capture reason/queue and Usage decode
  diagnostics contain numeric metadata only. No credentials, audio, transcripts,
  reply bodies or actual account allowance values are logged. Validation did not
  start a recording or spend model/speech allowance. No credentials, provider
  permission, router/Tailscale, authentication or TLS policy changed here.
- Publication uses the existing feature PR. Firmware flash and source publication
  are separate from any remote bridge deployment. Main merge is not approved.

This source package contains no binaries, private deployment receipts, raw logs,
runtime configurations or provider credentials. Actions associated with the PR
head provide the exact publication's Linux/offline and firmware CI evidence.

## Historical records

The snapshots below describe earlier milestones; current status above supersedes them.

# Current integrated candidate: 2026-10-14

86 offline checks PASS on cloud Python 3.12. Included: full-size PCM HTTP
upload, asynchronous transcription/chat admission, task result retrieval,
speech playback chunks and HTTP keep-alive, with fake providers only.
ESP-IDF 5.5.3 ESP32-C3 cloud compilation passed. No flash or physical acceptance.
No paid speech/model invocation and no provider account usage fetch performed.

Windows Desktop compatibility is source-based and fixture-checked, not a live
authenticated run. Linux STT remains disabled. Deployment authentication, private
TLS reachability and actual microphone/speaker behavior are not verified.
The firmware is an integrated candidate, not a full ready-to-use V1 release.

## Historical foundation results (superseded where noted above)

# Verification: foundation snapshot, 2026-10-14

This is an initial tested development increment, not the full V1 release.

Passed on cloud Linux with Python 3.12.14:

- Initial milestone: 40 unittest methods, including actual loopback HTTP requests
- Synthetic HTTP simulator: one task, duplicate retry admitted once, result retrieval after device disconnect, cursor event replay, honest unavailable usage
- Python compilation and git whitespace check
- Concurrent admission through multiple SQLite connections
- Restart handling for ambiguous and undispatched admission, preserving unknown rather than replaying
- Device ownership separation, duplicate-content conflict, request limits and malformed JSON
- Pending stop distinguished from confirmed cancellation
- Hermes capability/request/status contract tests with synthetic responses

Red/green development included initial missing-module failures, followed by explicit
behavioral regressions for acceptance incorrectly labeled running, the real upstream
waiting_for_approval status, and queued admission stranded after a crash. Those failed
before their production fixes. Test method count excludes inherited duplicate cases.

Not implemented or verified: physical firmware/build/flash, microphone/speaker, live
Hermes validation or upstream SSE events, production pairing/TLS/rate limits,
STT/TTS services, actual provider allowance reads, account auth, real server restart
reconciliation, Windows behavior. The development HTTP server is loopback-only and
mock-only, not production hardened. User/server credentials were never used. No paid
model calls or Codex development task were launched.

The default fake server loses its upstream run map on process restart. Persistent
SQLite history survives, but unfinished fake runs cannot then be fetched; this is
reported as unavailable, not success. Production backend recovery is later work.

No claim is made that all implementation-plan steps 1â€“4 are finished: the durable
core, mock transport and simulator are delivered; production transport and real event
consumption still need work. Full plan/design documents describe the larger target.

Subsequent transport checks cover HTTPS-only origins, operation/header allowlists,
response-size and JSON validation, redacted errors and non-secret representations.
These use injected synthetic responses, not a real TLS server or account.

## Two-target routing increment â€” 2026-10-14

48 unittest methods passed, including 8 new loopback-only routing tests: target
listing, mandatory explicit selection, independent same-ID submissions/results,
scoped cancellation, event/device isolation, unknown-target rejection, no failover
on backend error, and rejection of shared databases. Earlier 40 tests still pass.
No real server, account, speech service or hardware was contacted.


## Chat/audio/device-model increment â€” 2026-10-14

77 host test methods pass. The suite includes the previous bridge tests plus chat
continuity/restart/isolation/ambiguous admission, actual HTTP chat routing, bounded
audio capture and provider error redaction, supplied usage snapshot parsers and
staleness, bounded upstream chat context, and a compiled C11 device-model test.
The C test has assertions for page navigation, target switching, press/release,
repeated buttons, disconnect while recording, playback-stop isolation, monotonic
updates, terminal immutability and old-task generation rejection. Compiler flags:
-std=c11 -Wall -Wextra -Werror -pedantic.

Build: BLOCKED / NOT COMPLETED. The official ESP-IDF v5.5.3 tag was checked out;
the tag object b31fcc7a314a44ad992b58f589f7d1d8a4fadff6 resolves to commit
2c211b236707889e8400c4dc5644dd5c4ee071e0. Toolchain installation began in a separate
workspace directory. Git submodule fetches and pip dependency downloads returned
proxy HTTP 403 errors. Several SDK submodules are not yet at the pinned revision.
Do not use this incomplete SDK checkout to claim a verified build. No private
network, account, server or physical device was accessed.

Host tests: PASS (77 methods). Device tests: NOT RUN.
Unverified: complete firmware compile/link/size, LVGL rendering and physical button
behavior, actual audio, Wi-Fi/provisioning, production bridge transport and auth,
real Hermes servers, STT/TTS compatibility and actual provider usage sources.

The firmware UI is an offline implementation, not an operational voice assistant.
Audio and usage modules are tested libraries, not deployed account integrations.


## Firmware setup preview build recovery â€” 2026-10-14 08:55 UTC

The earlier build blocker is resolved. The cloud cache was placed in the writable
workspace and required Python dependencies installed without the optional pip
upgrade. Required ESP32-C3 SDK dependencies fetched successfully. The unused
OpenThread submodule remains off-pin, hence IDF's dirty version suffix; OpenThread
is not enabled for this target.

ESP-IDF compile/link: PASS. App binary 1,250,656 bytes (0x131560); 85% of app
partition remains free. Bootloader and partition checks PASS. Full merged image:
1,316,192 bytes, SHA256 43d917d12cd6ebbd2faa9a1899fc48fca47d9461a6f97f28ea3034f4144cf6ac.
Merged bootloader, partition and application ranges were compared byte-for-byte
against their source binaries; ZIP CRC and packaged full.bin checksum verified.

The source now includes phone-assisted configuration and Wi-Fi joining. These
were compilation-checked, not hardware-tested. The preceding 77 host test methods
were passed before provisioning was added; they are not evidence of provisioning
runtime behavior. No additional discretionary tests were run after the owner
requested to handle testing. The firmware still does not invoke Hermes or provide
live audio/usage. It is labeled setup/UI preview, not a completed V1 release.
