# Current publication verification: 2026-10-15

The combined controls, debug and voice transport candidate was built from source
`cc444950435a7af61fffeb1380df8b4001d8fcb8` and app-only flashed at `0x10000`.
Application size: 1,813,216 bytes. Application SHA256:
`4c45a917619bd7ece4d91cd11b1d59b27aa97eef914ca998b2000058523933fa`.
Independent application and retained bootloader verification passed; the retained
partition table was unchanged. NVS, pairing, other partitions and security settings
were not written. This candidate is not yet a physically verified voice fix.

- The final offline suite ran 170 unittest checks with zero failures/errors and
  five Windows/environment skips. The simulator passed and the guarded runner
  recorded zero external-network attempts. Skips were the separate LVGL suite
  and four Linux-only process-tree/symlink checks; Linux CI covers those cases.
- The unchanged UI had previously rendered all 42 firmware states at 240x320
  using cached LVGL 9.5, the device's 24 KiB pool and assertions enabled.
  CI renders the current source again. Host rendering does not establish physical
  display/button acceptance.
- ESP-IDF 5.5.3 compilation and linking passed. The exact source manifest,
  normalized tested source and compiled control/debug symbols were checked.
  SDK configuration, TLS certificate/hostname validation, authentication,
  timeouts, submission retry policy and the 22,000-byte audio budget are unchanged.
- The physical boot window was 09:10:59-09:12:29 UTC on 2026-10-15.
  The expected ELF identity reached `app_main`; 5 Linux HTTPS status
  cycles passed across 46.8 seconds. Backend availability and STT readiness
  were observed. There were zero Linux failure events, unexpected resets,
  fatal errors or Wi-Fi drops in this bounded window. Minimum observed
  post-request internal free heap was 45,192 bytes; the minimum largest
  block was 32,768 bytes. This is idle readiness, not an audio stress test.
- This bounded boot did not record a successful Usage decode. The preceding controls build established device Usage HTTP 200 and not_configured parsing; that source is unchanged, but this is not a new physical Usage acceptance.
  Real quotas, resets and quota alerts remain unavailable until supported feeds are configured.
- On the preceding controls build, an owner-triggered recording explicitly failed
  because its four-frame queue filled. The safe diagnostic recorded a 1,175 ms
  maximum upload against about 256 ms of queued PCM. The exact cause of that
  physical upload delay remains unproven; the chunk handler's synthetic timing
  does not measure the device's network path.
- The candidate enables TCP_NODELAY on each connected HTTP socket and requests
  an awake Wi-Fi radio only during capture/upload. It restores the exact previous
  power mode before finish/STT/provider waits and on failures. Failed restoration
  preserves the original mode for bounded worker recovery and blocks finish.
  It keeps the four-frame queue and visible discard behavior without silently
  dropping frames or retrying uncertain submissions.
- Native worker regressions cover continuous synthetic 16 kHz PCM, a 200 ms
  upload jitter, radio get/set/restore failures and eventual restoration. An
  injected 1,175 ms stall still explicitly aborts without finish; it is not
  relabeled a success. These checks do not prove real radio throughput.
  Independent review findings were fixed and re-reviewed with no remaining
  must-fix findings.
- Safe numeric diagnostics retain target-scoped TLS/verify/heap, capture reason,
  queue and request-stage timings, status and Usage decode counts. Slow chunk
  logs additionally report connect, headers-sent and first-response elapsed
  times plus TCP_NODELAY state. The headers-to-response span includes request
  body transmission and server wait; it does not isolate either cost.
  No credentials, audio, transcripts, reply bodies or real allowance values
  are logged. No recording or paid/model canary was triggered by validation.
- Main input/wake/setup/long-release, target selection, task generation/refresh,
  typed Usage reminders, cancellation, volume and phase-based audio memory
  cleanup regressions remain included. Task browsing is limited to twelve
  device-scoped Pocket tasks, with a 768-byte UTF-8 device preview and an
  8000-byte serialized host response bound. Todo has no supported read-only source.
- The checked Linux service advertises STT/TTS. A successful owner-recorded
  microphone/Hermes/playback round trip and physical upload improvement on this
  candidate remain unverified. Physical button/volume/battery/display acceptance
  and target selection across a physical reboot remain separate checks.
- The narrow serialized task-preview/null-result host fix is source-tested;
  this firmware flash does not deploy the separate host bridge. No account feed,
  credential, provider permission, router/Tailscale or TLS policy was changed.
  Publication remains on the existing feature PR; main merge is not approved.

This source package contains no firmware binaries, deployment receipts, raw logs,
runtime configurations or provider credentials. Actions records provide the
published commit's Linux/offline and firmware CI evidence.

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
