# Normal-firmware silent diagnostic implementation plan

> Execute with test-driven changes, independent review and source-matched build receipts. No commits, hardware access, provider calls, deployment or flashing.

**Goal:** A finite UI-driven silent diagnostic in normal Pocket firmware, not the superseded standalone bench application.

**Approved approach:** Andy chose normal firmware. Parent explicitly approved a UI-driven SILENT diagnostic with summaries, no tone and no raw capture/export. Preserve the documented Screen-off standby/wake and ordinary behavior; no verified extra physical power-cut behavior is available.

**Immediate input:** Library `private-source-checkpoint (identifier omitted for public publication)`, source ZIP SHA256 `1c30b99f415daa52ead994431e1ba4251f580eac415c4c3e6e563caef952f6fb`, all439source entries verified. Superseded separate bench image must not be installed.

## Architecture and boundaries

- Feature default off; candidate explicitly enables `POCKET_OFFLINE_DIAGNOSTIC` and callback BSP support. Normal `app_main`, display/battery/buttons/logging/network initialization remain the normal application, never the standalone bench loop.
- Diagnostics page long OK arms for10seconds; a fresh physical OK PRESS after arming starts. Callback event provenance prevents held/stale/delayed events from starting. One attempt per boot. Any active physical press stops through a bounded callback signal before the main input queue.
- Use the existing voice worker/command queue and setup voice lease, with a private sentinel branch before HTTP/provider logic. No second audio owner. Pause that worker's normal HTTP polling during acquisition; do not reconfigure Wi-Fi or the service.
- Finite native24k paired capture:1000frames ×240pairs / nominal10seconds. Checked hardware mute, no source submission, no tone. Two-second observed startup plus10.25-second acquisition deadline. Retain only bounded temporary PCM and numeric mean/RMS/peak/clipping/frame/discontinuity summaries; no raw USB/file/provider export or transcript.
- BSP begin/end owns a silent lease, snapshots prior software volume/mute-latch state, asserts mute before init/wake/recreation, and rejects audio submission/unmute while active. Restore prior software state only after checked stop/format/sleep; do not audibly unmute at diagnostic end. Failed cleanup keeps audio unavailable/owned; no false recovery or automatic retry.
- Normal user volume, target, pairing/NVS and backend task semantics remain unchanged. Manual Screen Off and wake must stay usable even if diagnostic cleanup is pending/failed. Standby does not cut physical power and does not cancel Hermes jobs.
- Enforce actual factory app allocation [0x10000,0x310000),3MiB. Source build CSV uses that app cap; no partition-table/bootloader/NVS image is delivered or installed. Other installed partitions are not inferred from source defaults.
- Candidate is normal firmware: it retains normal networking/persistence facilities. Only the diagnostic path is offline/no-provider/no-settings-write; never reuse the standalone image's whole-program no-network claim.

## Independently reviewable work

1. BSP silent lease and mute restoration with red/green tests for cold/initialized/asleep start, rejection, failure/rollback, no unmute or submissions and subsequent normal voice semantics.
2. Pure bounded summary runner and voice-owner integration: timing/counters/overflow/cancel, queue/admission/cleanup ownership, HTTP/provider exclusion, no raw export.
3. Normal UI/input wiring with fresh-event tokens, gesture isolation, safe arm expiry, normal controls and manual standby/wake preservation even on audio faults.
4. Actual partition/image cap guard, tests, default-off and enabled normal builds, clean rebuild, exact source/image hashes and resource evidence.
5. Independent review and full offline regression; report unchanged environment failures without weakening containment.
6. Separate new source checkpoint and app-only normal diagnostic candidate, clearly superseding standalone bench. Provide exact operation/rollback prerequisites; no flash action implied.

## Physical acceptance still missing

A cloud build does not prove boot behavior, power-button function, actual silence/pops, microphone/reference identity, clock accuracy, runtime memory, Stop latency or acoustics. The first normal diagnostic only reports digital continuity and lane levels; full AEC/double-talk and Live task/UI activation remain separate gates. GPT-Live remains the intended sole voice path, but existing working voice/recovery stays intact until replacement validation.
