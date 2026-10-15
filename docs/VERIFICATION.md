# Current publication verification: 2026-10-15

The combined everyday-controls and debug firmware was built from source
`fb12abd0f46e26b6b3cee9983491c9e5e2c42457` and app-only flashed at `0x10000`.
Application size: 1,811,248 bytes. Application SHA256:
`c4bbb374d23cdfd904bce7c5ed667118e46c91c396fe8008406f3593baf163bf`. Independent application and retained bootloader
verification passed; the retained partition table was unchanged. NVS, pairing,
other partitions and security settings were not written.

- The final offline suite ran 168 unittest checks with zero failures/errors and
  five Windows/environment skips. The simulator passed; the guarded runner
  recorded zero external-network attempts. Skips were the separate LVGL suite
  and four Linux-only process-tree/symlink checks. GitHub's Linux jobs cover the
  latter and render the UI using the pinned LVGL version.
- A separate cached LVGL 9.5 build rendered all 42 firmware UI states at 240x320,
  with the device's 24 KiB LVGL pool and assertions enabled. Representative
  task/UTF-8/truncation, diagnostic, alert and audio controls were visually checked.
  This is host rendering, not physical display/button acceptance.
- ESP-IDF 5.5.3 compilation and linking passed. The exact source manifest,
  normalized tested source and compiled control/debug symbols were checked.
  SDK configuration and certificate/hostname/auth behavior were unchanged.
- The physical boot window was 08:00:01-08:01:31 UTC on 2026-10-15.
  The expected ELF identity reached `app_main`; 6 Linux HTTPS status
  cycles passed across 58.6 seconds. Backend availability and STT
  readiness were observed. There were zero observed Linux failure events,
  unexpected resets, fatal errors or Wi-Fi drops during this bounded window.
  Minimum observed post-request internal free heap was 45,268 bytes;
  the minimum largest block was 32,768 bytes.
  The selected Home target remained Windows, which was offline; Linux readiness
  was measured independently. Home short OK selects Linux when that is intended.
- The actual device received Linux Usage HTTP 200 and decoded it successfully.
  All three provider states were `not_configured` with zero metrics. This proves
  transport/parser integration; real quotas, resets and quota alerts remain
  unavailable until supported feeds are configured.
- Debug diagnostics remain in the combined application: target-scoped connection,
  transport/TLS/verify codes and heap observations; voice capture reason/error,
  duration/read/upload timing and queue peak; request stage/error/HTTP/duration;
  and numeric Usage HTTP/decode/availability/metric counts. These diagnostic
  messages contain no credentials, audio, transcription/reply bodies or real
  allowance values. No recording or paid/model canary was triggered for validation.
- Native tests cover actual main input handlers, wake/setup/long-release gesture
  isolation, volume and cancellation handoff, task target/generation/sequence and
  refresh races, fresh typed reminder metrics and reset transitions, idle HTTP
  connection release, and active capture memory/cleanup. Task previews keep a
  768-byte UTF-8 device bound and an 8000-byte serialized host response bound;
  oversized metadata is rejected without changing identifiers or stored results.
- The host source includes bounded Hermes speech-command workers and their
  synthetic regression tests. The inspected Linux service advertises STT/TTS;
  no successful owner-recorded end-to-end microphone/Hermes/playback round trip
  has been established. The earlier capture failure's specific cause is still
  unconfirmed; the numeric debug paths are available for the next owner recording.
- Task browsing is limited to the latest twelve device-scoped Pocket tasks.
  Live task execution, physical button/volume/battery/display acceptance and
  target recovery across a physical selection/reboot remain unverified.
  Todo has no supported read-only source. Volume/alerts/deduplication are
  session-local; the selected target uses a separate validated NVS preference.
- The narrow serialized task-preview/null-result host fix is source-tested here;
  this firmware flash does not deploy or restart the separate host bridge.
  Host deployment is a separate service-version gate. No account feed,
  credential, provider permission, router/Tailscale or TLS policy was changed.

This source package contains no firmware binaries, deployment receipts, raw logs,
runtime configurations or provider credentials. Repository Actions records are
the evidence for each published commit's Linux/offline and firmware CI checks.

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
