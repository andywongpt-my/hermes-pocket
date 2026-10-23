# Pocket diagnostics, preferences and everyday controls

**Goal:** Deliver the six features Andy approved on 2026-10-15, reusing the existing authenticated Pocket bridge and keeping ESP32-C3 memory bounded.

**Architecture:** Keep one serial network worker, bounded response storage and the existing LVGL object tree. Store non-secret UI preferences separately from pairing configuration. Read task/Usage data only through existing device-scoped interfaces. All provider credentials stay on the Hermes host.

**Stack:** ESP-IDF 5.5.3, C11, LVGL, ESP NVS; Python standard-library bridge and native host fixtures.

**Current evidence:** Installed app `4defa5f`, bridge `voice-usage-20261015-003`. Nine Linux status HTTP 200 responses during 06:45:51–06:47:51 UTC; post-TLS free heap 53,052–53,056 bytes, largest block 40,960. Usage route returns HTTP 200 and three `not_configured` providers. Production parser plus SDK cJSON accepts that empty schema offline. Actual device Usage fetch/parser/display is not directly observable in current UART logs. Voice capture cause still awaits an owner-triggered diagnostic recording; no successful round trip is claimed.

## Constraints and review boundaries

- Preserve certificate/hostname verification, pairing/auth, server timeout, single-attempt POSTs and phase-based audio cleanup.
- No new credentials, account feeds, arbitrary commands, model canaries or paid calls. Real recording remains owner-triggered.
- Current physical app stays installed during source/test work. Review, build and app-only device validation precede any later deployment.
- Keep frozen source/artifacts and the published `main` unchanged. Current development starts from installed `4defa5f` in an isolated local branch.
- Do not confuse HTTP status, ESP transport return, ESP TLS error, mbedTLS error or certificate flags. Display only numeric diagnostics and known booleans; never endpoints, SSIDs, headers, response bodies or secrets.

## Stage 1: Connection diagnostics and last-target persistence

1. Add a small per-target status snapshot: Wi-Fi, UTC readiness, configured link, observed HTTP/transport/TLS results, response validity, backend and STT/TTS capability, internal free/largest heap, observation time. Snapshot only existing `/status` requests; viewing the page starts no work.
2. Add a diagnostic page after Usage, preserving Home→Chat→Usage navigation. Reuse current labels/canvases. Clearly distinguish unchecked, stale, Wi-Fi, time, setup, connection/TLS, authentication, HTTP, malformed response and backend states. Show exact numeric codes when useful; never infer DNS or a certificate cause without evidence.
3. Add `pocket_preferences.c/.h`: validated `u8` target 0/1 in separate `pocket_ui` namespace. Read after NVS initialization. Save only an accepted Home target change; skip identical values. Missing, invalid or failed reads fall back to Windows. Failed saves keep current selection and expose a local warning. Do not read, rewrite, migrate or erase the pairing blob.
4. Test first: native actual-C cases for stage precedence, target isolation, fresh/stale data, HTTP versus transport/TLS attribution, invalid/missing preference, duplicate suppression and NVS failures. Extend real worker/transport fixtures for end-to-end snapshot propagation. Render actual LVGL diagnostic states within the existing 24 KiB pool.
5. Run full offline suite and simulator, review the diff, then build with unchanged SDK configuration. Measure section growth and preserve the audio heap budget. This stage alone is the initial local implementation scope.

Review focus: no stale success presented as current, no credential-bearing state in diagnostics, no extra network/audio worker, no write on render/poll/boot, no flash during source verification.

## Stage 2: Volume and explicit audio controls

- Existing Chat OK press while busy cooperatively stops waiting/playback; OK release ends and sends recording. Preserve those gestures.
- Add an explicit discard-recording gesture and a final cancellation gate before `/finish`. Once submission occurred, state clearly that Hermes may continue. Never resend or imply remote cancellation.
- Hold only a desired bounded volume in UI state; apply codec changes in the existing audio/network owner, since BSP codec globals are not synchronized. Add user-triggered volume controls and an independent playback-stop action without freeing shared resources from the UI thread.
- Test capture cancellation before/after the commit boundary, blocking HTTP cancellation limits, playback stop and volume bounds. No buffer widening before the current recording failure is diagnosed.

## Stage 3: Pocket task progress and results

- Reuse `GET /targets/{target}/tasks` (latest 12 device-scoped Pocket tasks) and `GET /targets/{target}/tasks/{id}/view` (actual existing run state/result). Current voice path already uses `/view`.
- Use bounded paging/cache in the same idle worker, with target/task/generation/sequence isolation. Suspend browsing refresh during capture, waits and playback.
- Preserve queued/running/awaiting-approval/unknown/failed/cancelled/completed states. Approval stays in trusted Hermes UI. Keep cached data labeled and text UTF-8 intact.
- Bound the **serialized** response below the device's 8 KiB limit; the existing 3000-byte text prefix may expand under JSON escaping. Avoid raw 64 KiB task results and 100-turn chat transcripts. Show truncation explicitly.

## Stage 4: Safe user-triggered shortcuts

- “Check server” enqueues the current `/status` GET; “Pocket tasks” opens Stage 3. No automatic task submission.
- The inspected adapters expose no authoritative Hermes todo API. Keep Todo unavailable until an existing read-only source is identified; do not substitute a canned model prompt or pretend Pocket tasks are a global todo list.
- Test target selection, offline behavior, busy-state scheduling and that no POST/model/provider admission occurs.

## Stage 5: Usage threshold and reset reminders

- Evaluate only real, explicitly configured, fresh metrics already delivered by `/usage`. All current feeds are unconfigured, so reminders remain inactive with an honest explanation.
- Keep percent-used, percent-remaining and USD credits distinct. Deduplicate by target/provider/category/window/reset; suppress missing, stale, failed and clock-uncertain observations.
- Crossing a reset timestamp means a fresh observation is needed; it does not prove allowance was restored. Never invent zeros or replenish values locally.
- Add bounded local reminders after source validity, thresholds and controls have offline tests. No credential discovery, extra feeds or background provider calls are part of this stage.

## Delivery record

Each stage records source commit, tests, rendered UI states, SDK/config hashes and memory impact. Device flash/acceptance remains a separate reviewed step. Voice diagnosis and actual-device Usage state remain explicit gates until directly observed.
