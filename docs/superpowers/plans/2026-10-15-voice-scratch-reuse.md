# Voice scratch reuse implementation plan

> **For agentic workers:** Use superpowers:executing-plans for the authorized inline implementation. Independent reviewers inspect the final change.

**Goal:** Remove the redundant body and network consumer allocations from normal firmware without changing voice behavior.

**Architecture:** The network task retains its existing 8192-byte allocation. A borrowed consumer frame uses its aligned beginning; encoding completes into the last 2816 bytes before synchronous HTTP receives a chunk ACK into the first 5376 bytes. Cleanup wipes and nulls these aliases without freeing the task's scratch.

**Tech Stack:** ESP-IDF 5.5.3, C11, FreeRTOS, cached cJSON and Mbed TLS, native Python/C tests.

**Spec:** Parent delegation in this task; scope recorded in `HPvoice-scratch-evidence-20261015-001/scope.json` beside the worktree.

## Global constraints

- Base normal firmware is `30863f27e9aaf35bfaa6c40b69d14bb8af4f7909`.
- Preserve producer frame, queue depth4, recorder stack6144,16000Hz16bit mono and all user voice behavior.
- Keep POST/output disjoint; all other response capacities remain8192.
- No device/microphone/provider access, flash, fourth online test, push or merge.

## Review focus

- Maximum PCM/base64 and worst sequence prefix fit without overlap.
- HTTP clears output before transmission; no asynchronous borrower survives.
- ACK parsing completes before frame reuse; JSON allocations are released.
- Abort/failure and consecutive captures never free borrowed scratch.
- A stalled producer retains its queue while network scratch can be reused.

### Task 1: Test, implement and validate one allocation-lifetime change

- [ ] Add actual-worker regressions in `tests/test_firmware_voice_scratch.py`, using cached real JSON/Base64 with local RTOS/audio/HTTP fixtures. Under a producer-only allocation budget, require a complete2048-byte upload; cover ACK overwrite/invalid ACK/transport failure/overflow, two captures, failure recovery and stalled queue ownership.
- [ ] Run targeted unittest discovery and record the expected old-implementation failures before editing production C.
- [ ] Modify only `firmware/main/pocket_voice.c`: use disjoint borrowed scratch regions, chunk-only bounded output, and non-owning cleanup; assert layout bounds.
- [ ] Run targeted tests then complete offline discovery and simulator; preserve baseline and red/green receipts.
- [ ] Build the normal SDK profile from frozen source; verify image/ELF hashes, C3 frame size, source/config identity and retained partition fit. Obtain independent implementation review.
- [ ] Commit only implementation and relevant tests locally; report4868 requested allocation bytes removed and physical voice still unverified.
