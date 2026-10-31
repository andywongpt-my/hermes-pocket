# Default-off normal-app Live caller implementation plan

> **For agentic workers:** TDD, independent review, actual LVGL renders and target builds. No commits or device/provider operations.

**Goal:** Implement normal-app caller/admission/controls for eventual GPT-Live while keeping both default legacy behavior and current physical refusal intact.

**Architecture:** Parent approved a default-off caller option. It reuses the existing voice command queue and worker, copied request/configuration, one persistent heap bundle, and the verified inactive lifecycle. A separate source-hard readiness accessor always returns false; neither the build option nor settings attest physical acceptance. No real call can pass that accessor in this checkpoint. Native fake-boundary tests exercise the otherwise-inactive path.

**Tech stack:** Existing C11/FreeRTOS/BSP interfaces, actual LVGL9.5 render fixtures, Python unittest native harnesses, pinned ESP-IDF5.5.3.

**Spec:** Parent's 2026-10-30 instruction to implement normal-app activation caller off by default. Disabled behavior/recovery stays intact; no flash or paid calls. An active-by-flag bypass was rejected. Reusing the silent diagnostic lease was rejected. A second audio task was rejected.

## Global constraints

- `CONFIG_POCKET_LIVE_APP_CALLER` defaults off, depends on the existing paired compile option.
- Existing public device gates remain byte-identical. New caller readiness accessor is source-hard false and separate, not runtime/config truth.
- Enabled mode routes Chat to Live only; no silent legacy fallback or legacy model-choice claim. Disabled paths/rendering remain unchanged.
- Initial fresh OK press starts; release/held LONG/delayed CLICK do not Stop or restart. Any later physical press latches Stop before UI queueing. Wake gesture only wakes.
- Freeze target/user volume/original request time. Reject stale/replayed timestamps and dispatch after original10s deadline; no auto retry/reconnect/new session.
- Read existing bridge config under exclusive setup/voice lease, keep chat_id `pocket` (existing device-scoped namespace), no new provider key lookup.
- Same existing worker owns allocation, private sentinel3, I/O, cleanup and deallocation. No large UI stack allocation/new task.
- Incomplete cleanup retains bundle/lease/busy and blocks background polling/admission. Cleanup-only periodic progress at most4Hz only for known-owned, audio-clean network teardown; audio/unknown faults stay quarantined.
- Manual Screen Off/wake/navigation remains usable during quarantine. This is standby, not physical power-off.

## Review focus

1. Gate refusal before config/allocator/reservation/I/O; compile option cannot bypass it.
2. Duplicate/stale/queued/wake input, input loss, Stop during preparation/output and initial gesture release.
3. Shared worker sentinel routing before all legacy provider/model HTTP; no legacy fallback when enabled.
4. Partial allocation/prepare/start/cleanup, late DNS callbacks and freeing only scrubbed, released storage.
5. Honest UI about unavailable/pending/backend unknown and unchanged disabled render/control behavior.

## Tasks

- [x] stream_worker: implement/test small pending admission state, same-worker app bundle/caller, source-hard gate and lifecycle error paths. Own new app module plus voice.c/.h; do not edit worker/host harness.
- [x] resolve_pocket_audio: implement/test main callback/input routing, freshness and wake/Screen Off behavior. Add option/CMake only after red tests.
- [x] bounded_transport: add view fields and mode-specific Chat/Models presentation, preserve legacy renders; actual LVGL snapshots and visual QA.
- [x] independent review: callback/ownership/scrub/cleanup admission plus gate/legacy-isolation tests.
- [x] audit_build: fresh default, compile-only paired, caller-enabled (still hard-refused) target builds and source identity; no install artifact.
- [x] build_pocket_cloud: full aggregate with restored native dependencies: 702 tests, 694 pass, zero skips, the same 8 procfs-dependent failures/errors as baseline.

Artifact handoff: preserve this frozen source and verification receipts in a separate post-caller source-only checkpoint, retaining the verified pre-caller baseline. The external archive receipt records the final Library identity and hash.

## Review correction

Independent review reproduced a queued-start race: a later physical press could
arrive before the UI had admitted the initiating event. The final implementation
carries a monotonically increasing callback generation with the queued event and
checks it under the same lock that establishes session ownership. Any subsequent
press or queue loss invalidates a queued start or latches Stop on an owned one.
The generation saturates rather than wraps; no timestamp equality or synthetic
legacy entry can fabricate physical admission. Actual main-input and caller tests
both preserve the failing reproduction and verify the correction.
