# Current publication verification: 2026-10-15

- Local offline run: 107 unittest checks, zero failures/errors, with two existing host-compiler/LVGL skips. The new native HTTP/voice C regressions passed with a cached compiler.
- Strict C11 setup-interface address regression test passed.
- Node setup-page validation/initialization checks passed.
- Synthetic bridge simulator completed, with zero duplicate dispatches and three replayed events.
- No real speech/model calls or provider account usage reads were performed by these host checks.
- Current animated firmware and idle-audio memory fix passed ESP-IDF compilation, app-only flash at 0x10000 and independent app/bootloader verification. The partition table was unchanged. Boot reached app_main, CW2017 was detected, and no panic or driver errors were observed. Settings partitions were untouched.
- The physical device reported authenticated Linux status HTTP 200 with the backend available over certificate- and hostname-validated HTTPS. Internal free heap before TLS was 90,584 bytes, with an 81,920-byte largest block; after TLS, 58,112 bytes and a 47,104-byte largest block remained. No TLS error was observed in this bounded boot check. No TLS/cipher/trust/auth configuration was changed.
- Idle audio allocation had caused TLS big-number allocation failure. Audio queues, buffers and the recorder task now wait until a held talk request passes STT checks; regressions cover allocation/task failure and cleanup, including retaining a queue still used by a stalled recorder.
- Verified application source: `77fbcc7cf436e1c609e73a5b96951b7b2eeea7a8`; application binary SHA256: `de491dcf2f24e95c5642f9d4a6a81d9b5c253fc5b5dc65b6d98a5eb7a670e35d`.
- Voice remains off/unverified: the checked bridge reports STT unavailable. Live text-task execution and the router Tailscale setup remain unfinished.
- This source package contains no firmware binaries. Host checks are not proof of the physical UI, battery accuracy, successful provisioning or end-to-end Hermes voice operation. Those acceptance checks remain pending.
- Reviewed Linux voice transport: `pocket/linux_voice.py`, SHA256 `833f733fd732820fdd7d0ddcf19b83586628e1951338d072a080e9e73de31202`.

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

No claim is made that all implementation-plan steps 1–4 are finished: the durable
core, mock transport and simulator are delivered; production transport and real event
consumption still need work. Full plan/design documents describe the larger target.

Subsequent transport checks cover HTTPS-only origins, operation/header allowlists,
response-size and JSON validation, redacted errors and non-secret representations.
These use injected synthetic responses, not a real TLS server or account.

## Two-target routing increment — 2026-10-14

48 unittest methods passed, including 8 new loopback-only routing tests: target
listing, mandatory explicit selection, independent same-ID submissions/results,
scoped cancellation, event/device isolation, unknown-target rejection, no failover
on backend error, and rejection of shared databases. Earlier 40 tests still pass.
No real server, account, speech service or hardware was contacted.


## Chat/audio/device-model increment — 2026-10-14

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


## Firmware setup preview build recovery — 2026-10-14 08:55 UTC

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
