# Voice session reliability candidate, 2026-10-25

Status: local candidate built and reviewed; release blocked by failed verification. Nothing has been published or flashed.

Base: `e36365aee01d6418434faf590db425ac08f92cc1` on local branch `fix/voice-session-reliability-20261025`.

## Changes

The candidate includes the checkpoint's earlier Connecting/early-button-release correction and adds three bounded voice fixes in `firmware/main/pocket_voice.c`:

- Reject an admitted recording with no audio frames, abort the capture, and show actionable feedback instead of sending an empty finish.
- Validate active backend availability before allocating or posting audio.
- Retain capture queue and lease ownership while a recorder is still active after the cleanup deadline. The existing worker retries cleanup when the recorder finishes; a recorder that never returns remains fail-closed.

There is no automatic upload retry and no new persistent task. The production change is 17 added and 5 removed lines relative to the base.

`tests/test_voice_sessions.py` adds 12 host-compiled cases using the actual C workers with synthetic HTTP, codec, and RTOS adapters. They cover zero frames, metadata eligibility, delayed cleanup, cancellation, retry, upload/status failure, and playback failure. These passed. They are source-level evidence, not real-device voice acceptance; the delayed-cleanup case does not simulate an already-in-flight physical codec read returning after its deadline.

`tests/test_chunk_timing.py` synchronizes its partial-header fixture with the handler entering header parsing. Its original 65 ms header and 50 ms dispatch assertions remain intact. The corrected header case passed. The 13-test module still failed its separate saturated-logger serial assertion (41 observed, 1002 expected).

## Verification

| Check | Result |
| --- | --- |
| Existing voice/Connecting cases before this patch | 19 passed |
| New voice regressions after implementation/review | 12 passed |
| Initial full discovery without all cached SDK/compiler prerequisites | 414 tests; 1 failure, 2 errors, 16 skips |
| Exact initial three failures/errors rerun | 3 passed |
| Completed SDK-enabled full run | 470 tests; 29 failures, 15 errors, 10 skips; 2340.946 s |
| Simulator | Passed; synthetic only, no provider/device calls |
| Firmware candidate | Build succeeded; no flash |
| Independent patch review | No remaining Critical or Important finding in the scoped C change and regression cases |

The 56 additional full-run tests were existing methods in five classes previously skipped in `setUpClass`: audio drain 6, public link 13, setup 26, speech v2 6, and voice scratch 5. They became discoverable after enabling the existing cached SDK/compiler paths. No new 56-test implementation was added. Five class skips and one method skip were removed, taking skipped entries from 16 to 10.

The completed full run's 15 errors were synthetic resident-STT startup timeouts. Its 29 failure entries comprised one header timing fixture failure, one noisy-worker wall-time failure, and 27 runtime-usage/schema assertion entries, including subtests. All firmware C and new voice cases passed in that run. Passing those subsets does not make the full release check pass.

Six representative Python cases were compared with the unchanged base: each checkout produced 4 failures and 1 error. Relevant Python production and test sources were equal before the later header-fixture correction. Moving scratch to OS Temp and switching to the existing SDK Python did not resolve all failures. One separate device-equality diagnostic passed with approximately 16 ms reads; this demonstrates intermittence, not a fix.

The locked-database test remains unresolved: a requested 100 ms read timeout produced about 1 s elapsed against its unchanged 0.5 s assertion. Static inspection confirms a separate readonly connection receives the remaining timeout and resets `busy_timeout`; Store's separate 10 s connection timeout is not inherited. The last bounded diagnostic exceeded its 25 s child limit before emitting stage detail. Its private job was stopped and verified quiescent. No exception/code or plain-SQLite control result was captured, so the cause cannot yet be assigned to the driver, host, or collector. Existing synthetic scratch files were preserved because fixture cleanup after that termination could not be proven.

The noisy-worker latency and saturated-logger serial failures also remain unresolved. Assertions and production deadlines were not relaxed. No further full-suite run is justified until these roots are addressed.

## Build and rollback evidence

Candidate app: 2,428,656 bytes; SHA-256 `d7c9e2b6935e48ea75f444045ca61aa7fe636eece65cfe1be163518842d4a25a`.

ELF SHA-256: `2f8e3f8315570d348b4689939d2d91d28c97d3d0fb409aaca183e9191ea67d72`.

The app fits the established 3 MiB factory partition with 717,072 bytes spare. The existing build cache was used with all 85 firmware inputs pinned. Its original source contents and SDK configuration were restored and checked after the build; no components were downloaded or installed. This is not an independent clean-build or bit-for-bit reproducibility claim.

A known-good app and ELF are retained privately for rollback. Device enumeration matched the historical target, but no serial port was opened, no device identity/security/table read was performed, and no boot, flash, or physical voice acceptance was attempted. The reported installed firmware remains historical, not freshly read.

## Service observation and next gate

The private audit records an October 25 04:02 UTC read-only observation of an inactive Pocket backend, absent temporary proxy, private connection refusal, and public HTTP 502, with general Hermes running. This is later than the successful October 23 restoration. Subsequent October 25 05:40 UTC SSH, Tailscale, and public HTTPS checks timed out, so current service state is unverified. No service or network configuration was changed during this iteration.

Before an app-only flash, resolve or substantiate the baseline verification failures in a functioning test environment, rerun the applicable full check to a genuine pass, bind fresh review/build/test receipts, and identify/read the physical target and its existing app/security/table using the established guarded procedure. Preserve Wi-Fi, pairing, NVS, bootloader, and partition table. A successful flash must then be followed by boot/connection and physical voice acceptance. Paid model calls and server changes remain outside this iteration's authorization.
