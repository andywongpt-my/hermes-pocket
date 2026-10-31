# Pocket audio continuation implementation plan

> Delegated execution: local source, public dependency retrieval, builds and offline tests only. No key, paid call, service change, deployment, publication or flash.

**Goal:** Replace the checkpoint's unsupported audio prerequisites with source-audited, tested components where feasible, and preserve explicit physical release gates.

**Architecture:** A callback-owned I2S render ledger associates actual completed DMA buffers with source samples. A pinned SpeexDSP 1.2.1 host engine consumes genuinely synchronized microphone/reference PCM rather than deriving timing from queued bytes. Firmware streaming remains separately opt-in and must not displace current 16 kHz voice. The existing GPT-Live/Hermes adapter is retained.

**Spec:** `../specs/2026-10-29-gpt-live-design.md`, extended by the owner's delegated request to investigate/implement truthful sample accounting, supported AEC, firmware streaming and a complete build without new hardware.

## Constraints and review focus

- Native 24 kHz mono PCM16 only; no relabeling or guessed resampling.
- A completed source count is not a complete capture-aligned render reference. Silence placement, underruns and format/cancel epochs must remain explicit.
- No microphone mute gate represented as AEC; missing synchronization fails closed.
- No recycled descriptor, startup idle completion, format change or partial write may advance a false rendered count.
- Capture/playback/stop and disposal cannot race unboundedly; the legacy mode remains unchanged by default.
- Host synthetic tests and compiler success never establish real acoustics, codec clock stability, heap headroom, Wi-Fi performance or provider access.

## Tasks

- [x] Materialize the confirmed checkpoint and verify its SHA-256 before reading source.
- [x] Audit actual IDF public callback API and obtain exact codec source; evaluate official supported AEC APIs and licensing.
- [x] Write red tests for bounded descriptor/source accounting, then implement default-off BSP API and verify with fake public hardware boundary.
- [x] Write red tests for a memory-safe SpeexDSP C-ABI wrapper, exact format validation, ownership and actual synthetic echo cancellation/double-talk; implement and test against the locally built pinned library.
- [x] Implement a bounded inactive streaming worker and compile-only IDF adapter; the real-device entry remains hard-refused because actual playout/capture reference cannot be supplied honestly.
- [x] Obtain exact pinned SDK/components/toolchain in the workspace and run a full no-flash ESP-IDF build; report exact unresolved failures.
- [x] Independently review changes, run affected and complete repository checks, record all failures/skips and source hashes, and save a source checkpoint with no binaries or credentials.

The implementation chooses SpeexDSP's documented synchronous `speex_echo_cancellation` API rather than its soundcard FIFO assumptions. It does not add a guessed adapter that translates v1 cumulative counts into aligned echo reference. The development dependency is built only under the workspace; nothing is installed into a running service.
