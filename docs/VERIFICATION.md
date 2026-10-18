# Source checkpoint verification: 18 October 2026

This verifies a source checkpoint, not a device release or new live deployment.
No serial/device, service, network-setting or paid-provider action was performed.
Exact numeric receipts are in [CHECKPOINT-CHECKS.json](CHECKPOINT-CHECKS.json).

| Check | Actual result |
| --- | --- |
| Consolidated root suite | 427 tests: 419 passed, 8 Linux-only skips, zero failures/errors. |
| Root simulator | Passed with synthetic providers. |
| Isolated quota020 backend | 385 tests: 372 passed, 13 skips, zero failures/errors; simulator passed. |
| Claude producer mock suite | 77 tests: 55 passed, 22 Linux PTY/timezone skips, zero failures/errors. No native client was invoked. |
| Wi-Fi adapter | 24 tests passed, including idle-success/no-event and transient-query recovery. Exact old `556e897` source fails the new idle-event regression as expected. |
| UI | 104 synthetic 240x320 snapshots rendered and validated by the root suite. |
| ESP-IDF 5.5.3 | Cached normal-profile compile/link passed for all 83 firmware source files; SDK configuration unchanged; no dependency downloads. |
| Publication privacy | Complete proposed source and staged contents inspected; credentials/private runtime data and non-source artifacts excluded. |

The new application is 2422960 bytes, SHA-256 `38248585ade1b91b804c405d273e2997a08f902efa9419a35ba815805adcb5d2`.
It fits the previously recorded 3 MiB application limit. The cached build's
generated partition table is not the installed device layout and is not published
or flashed. The new application binary and ELF also remain local.

## Explicit failures, omissions and limits

An attempted combined producer/operations run reported 128 test cases, 15 errors
and 22 skips. One import error is the missing optional `httpx` dependency for
Ollama tests; remaining errors require acceptance/manifest fixtures intentionally
excluded from publication. The separately rerun Claude suite is green. These
archived operations are not a verified or deployable bundle, and no private
receipt is fabricated to make them pass. The Codex implementation is missing;
its pending contract suite is preserved but not run or counted as passing.

Initial sandbox-restricted attempts could not open synthetic loopback sockets
or execute all cached tools. An initial local guard also interfered with asyncio
imports; that test-harness error was corrected before the authoritative reruns
above. The source product was not changed to hide those failures. All completed
authoritative suites used a guard rejecting external endpoints/live clients and
recorded zero such attempts. No environment/model workaround or install was used.

Linux-specific FIFO, symlink, process containment, private permissions, PTY and
timezone checks were not rerun on Linux in this Windows publication task. The
quota snapshot's small gcc-only firmware smoke/UI tests skip on Windows; the
complete root native/device-source checks and snapshots ran with cached Clang.
Remote CI has not been observed at commit time.

Delivered/restored firmware remains recorded at `2eabc460`; the first Wi-Fi/clock
candidate `556e897` failed physical reconnect and was rolled back. The corrected
source has no physical acceptance. The numeric driver return was not observed in
that failed physical build; the SDK boundary defect is proven by synthetic C,
not relabeled as an observed physical return value.

Root backend is previously deployed bridge018R2. quota020 remains isolated,
undeployed and inactive, without verified usable provider quota feeds. Tailscale
Funnel is not configured. No new device or live bridge claim is made.

The source-only reverse patch in `checkpoints/firmware-delivered-2eabc/` recovers
the original delivered firmware directory without publishing private worktree
history. Its index application check passed; restored firmware was not rebuilt
or flashed during publication. Source provenance is in
[SOURCE-PROVENANCE.json](SOURCE-PROVENANCE.json).
