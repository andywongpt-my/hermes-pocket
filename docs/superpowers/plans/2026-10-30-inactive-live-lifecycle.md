# Inactive Live lifecycle implementation plan

> **For agentic workers:** Use test-driven subagent implementation, independent review and final aggregate verification. No commit/push is authorized.

**Goal:** Complete testable session ownership/configuration/audio lifecycle components without enabling the device's Live entry.

**Architecture:** The parent chose inactive, test-injected lifecycle development rather than another physical experiment or an activation bypass. A fixed-size validated config snapshot feeds an adapter-agnostic, single-owner controller. A persistent internal IDF context and separate BSP Live lease support it; no normal UI invokes the path and the public device gate remains unchanged.

**Tech stack:** C11, existing ESP-IDF/BSP interfaces, Python unittest native-C harnesses, ASan/UBSan where available. No new hardware, provider request, key, installed service or device action.

**Spec:** This bounded design implements the parent's 2026-10-30 instruction to implement/test the inactive lifecycle while preserving hard real-device refusal. Alternative of weakening the diagnostic lease was rejected: the silent diagnostic must continue to refuse all playback/unmute. Alternative of wiring normal UI now was rejected: physical acceptance is unresolved and Andy's remembered 1000 count is not a pass.

## Global constraints

- `pocket_live_v2_gate.c` remains byte-for-byte hard-refused. No config boolean certifies physical acceptance.
- Preserve legacy voice, current normal startup, diagnostic silence and recovery artifacts.
- Runtime lifecycle operations are serialized by the existing owner; only Stop is cross-task atomic. No new task or active normal-app caller in this continuation.
- Snapshot host/token/CA/chat/target/epoch/user volume without borrowed lifetimes, credentials in logs, credential discovery or NVS writes.
- Incomplete cleanup retains context/config/ownership; no automatic reconnect, resubmit, retry session or task deletion.
- Software deadlines cannot preempt stuck SDK/codec operations; no hardware or acoustic guarantee.

## Review focus

1. Caller mutation, unterminated input, origin parser ambiguity and failed admission leaving secrets.
2. Stale Stop/epoch/repeated dispatch and late queue admission after expiry.
3. Partial audio initialization, foreign ownership and restoration failures falsely releasing the owner.
4. DNS callbacks outliving close and scrub/free while SDK still owns context.
5. Silent diagnostic or legacy audio bypassing the new lease; checked output enable before reference-ready or above frozen user intent.

## Tasks and ownership

### 1. Frozen config (resolve_pocket_audio)
- [ ] Add failing native tests for strict HTTPS DNS/IPv4 origin + optional port/root slash, token32..128, bounded CA, chat1..64 identifier, target0/1, nonzero epoch, volume0..100.
- [ ] Implement `pocket_live_session_config_snapshot`, `valid`, `clear` in `firmware/main/pocket_live_session_config.{h,c}`. Fixed arrays: host193, token129, ca_pem2049, chat_id65. Failure clears output; clear uses volatile wipe.
- [ ] Test malformed/unterminated boundaries, no pointer borrowing, max sizes and secret clearing under sanitizers.

### 2. Persistent controller/internal IDF adapter (stream_worker)
- [ ] Add red tests for reserve/conflict, Stop-before-dispatch, admission expiry, once-only execute, monotonic epochs and retained cleanup ownership.
- [ ] Implement `pocket_live_session.{h,c}` and internal persistent IDF prepare/get-ops/cleanup/scrub. Construction is memory-only; no normal-app task/caller.
- [ ] Test DNS/transport pending cleanup, explicit cleanup-only reap, state/result reporting and safe scrubbing. Keep public gate unchanged.

### 3. Separate BSP Live lease (audit_render)
- [ ] Add red real-BSP/fake-driver tests, retaining existing silent diagnostic tests.
- [ ] Implement `bsp_audio_live_begin(epoch, user_volume, cleanup_required)`, `bsp_audio_live_enable_output(epoch)`, `bsp_audio_live_end(epoch)`.
- [ ] Begin stays checked-muted. Enable is explicit after reference-ready; stale epoch/foreign owner rejected. End mutes, stops, restores saved format, verifies sleep, then restores only cached normal volume/mute intent.
- [ ] Test cold/asleep/awake entry; diagnostic/legacy exclusion; partial begin and sticky failed cleanup; sanitizer checks.

### 4. Paired wrapper and compile-only integration (resolve_pocket_audio)
- [ ] Update tests before adapting `pocket_live_v2_audio.{h,c}` to the separate lease, frozen volume and cleanup-required tracking.
- [ ] Enable output only for a validated speaker submission, not construction or audio start. Unknown begin rejection does not release another owner.
- [ ] Add new sources only to existing default-off paired compile block; no main/UI/gate activation.

### 5. Review and evidence
- [ ] Independently review lifecycle/lease boundaries and preserve source/test receipts.
- [ ] Coordinate cross-language harness and deadline fixes with build_pocket_cloud; do not duplicate its files.
- [ ] Run focused native/sanitizer tests plus the repository suite with available real dependencies, reporting exact failures/skips rather than masking them.
- [ ] Verify public gate unchanged and no active startup callers; document remaining physical and deployment gates.
