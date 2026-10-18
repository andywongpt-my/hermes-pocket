# Five Saved Wi-Fi Networks Implementation Plan

Historical implementation plan. The first candidate failed reconnect and was
rolled back. This source checkpoint has no corrected hardware acceptance or
current device-operation permission; see ../../CHECKPOINT-STATUS.md.

> **For agentic workers:** Use superpowers:executing-plans to implement inline; obtain a fresh whole-change review before flashing.

**Goal:** Retain five owner-entered Wi-Fi networks, test additions before saving, reconnect to known networks, and preserve Hermes pairing.

**Architecture:** A host-testable bounded profile book and connection state machine own selection, attempt deadlines, rollback and save admission. ESP adapters serialize scans and connection changes in the setup worker; separate NVS records store profiles without changing the legacy pairing blob. The phone page submits Wi-Fi and pairing as separate actions.

**Tech Stack:** Existing ESP-IDF 5.5.3, FreeRTOS, NVS, cJSON, embedded HTML/JavaScript, host Clang tests. No new dependencies.

**Spec:** Owner-approved design and flash authority in this conversation, 2026-10-16 15:22 MYT.

## Global Constraints

- Base 2eabc4600d6dc6fc2c2033870ab7121b4e3b0bb4; retain voice/fulltext/Usage/font/power behavior.
- Five profiles maximum; never automatically evict. Legacy import is read-only until an explicit owner action persists a profile book.
- A new/updated password is persisted only after a fresh association and IP event for its SSID. Failure/cancel/save failure preserves the prior book and restores known networking.
- No roaming/scanning while healthy, and no setup or radio changes while active voice owns the device.
- Provisioning requires a physical owner gesture, WPA2/PMF, setup-interface checks, nonce, a five-minute window and bounded requests. No automatic unsecured AP.
- Wi-Fi-only changes never write the pairing record. Blank pairing fields preserve stored fields; secrets are never returned by status APIs or logs.
- Tests use synthetic credentials. App-only flash preserves bootloader/partition table/NVS/security, followed by bounded existing-network checks. No new-network or provider canary.

## Review Focus

- Stale IP events cannot validate a new candidate; disconnect barrier and fresh association/IP counters are required.
- A full/corrupt profile store cannot silently overwrite another profile or re-import explicitly forgotten legacy data.
- Storage failure cannot replace the valid older profile book; dual records retain a last valid copy.
- Setup expiry, HTTP retries and cancellation cannot commit an untested candidate.
- Pairing-only and Wi-Fi-only writes remain separate, including legacy v1 and v2 records.

### Task 1: Profile book, NVS adapter and bounded connection engine

Files: create `firmware/core/pocket_wifi.{c,h}`, `firmware/main/pocket_wifi_store.{c,h}`, `firmware/main/pocket_pairing.{c,h}`, `tests/test_firmware_wifi.py`.

Interfaces: `pocket_wifi_book` holds five profiles; `pocket_wifi_ops` supplies serialized connect/disconnect/scan/save; `pocket_wifi_tick` consumes fresh event counters and a voice-busy flag. Store load distinguishes absent, valid and corrupt records; pairing adapter preserves legacy binary layout.

- [x] Write and run real-C tests for legacy import, capacity, upsert/forget, known-network ranking, no healthy roaming, fresh-event save, authentication/DHCP timeout, rollback/cancel, voice pause, NVS failure and record recovery. Observe failure before implementation.
- [x] Implement the core and storage/pairing adapters; run the focused offline tests to green.

### Task 2: Owner-operated setup integration and phone page

Files: modify `firmware/main/pocket_setup.{c,h,html}`, generated `pocket_setup_page.h`, `pocket_voice.{c,h}`, `main.c`, `pocket_ui.{c,h}`, `firmware/main/CMakeLists.txt`; update setup UI and integration tests.

Interfaces: authenticated AP-only status, scan, join, connect-saved, forget, cancel and pairing routes; no secret-valued GET field. Worker queue serializes all radio operations; status separates connection progress, join result and Hermes reachability.

- [x] Add failing UI/integration tests for separate payloads, polling, full-store feedback, explicit forgetting, expired/cancelled setup, no secret echo, pairing preservation, no boot AP, and radio ownership.
- [x] Implement the flow, regenerate HTML header, and run focused plus full offline tests and simulator. Inspect actual LVGL renders where UI changes.

### Task 3: Review, build, app-only deployment and bounded verification

Files: new workspace evidence stage `HPwifi-profiles-evidence-20261016-001`; documentation `docs/WIFI_NETWORKS.md`.

- [x] Obtain an independent review of the complete diff and fix material findings with regression tests.
- [ ] Freeze the source/commit; build only with the cached normal SDK configuration and verify app partition margin and memory changes.
- [ ] Confirm idle using bounded passive diagnostics; app-only flash/readback under existing owner authority, preserving original bootloader/table and never reading NVS.
- [ ] Verify the exact app boots and reconnects to the current network with Linux HTTP200, no AP automatically opened, no fatal/memory failures, and standby polling. Release COM7 and record limitations: physical new-network entry and fallback need owner-operated testing.
