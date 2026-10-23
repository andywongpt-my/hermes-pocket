# Wi-Fi profiles and MYT clock on delivered Funnel firmware

> **For agentic workers:** Use superpowers:executing-plans inline. Obtain an independent whole-change review. This phase is offline only; report the exact candidate and hardware plan before any new flash.

**Goal:** Resume the owner-approved five-network and date/time projects while preserving delivered Funnel firmware `225a4a2742da1db06adaab29979101c435970d36` and all existing functions.

**Architecture:** Reuse the earlier bounded Wi-Fi book/state machine, NVS profile adapter, separate pairing form, and clock formatter. Correct the idle disconnect boundary with an association pre-query; retain asynchronous barriers for an associated station. Retain the separate approved Linux public-link record, public CA selection, original Windows trust, and numeric-only diagnostics.

**Tech stack:** Existing ESP-IDF 5.5.3, cached normal SDK configuration, FreeRTOS, cJSON, LVGL, host Clang, and Node. No dependency installation or SDK edits.

**Spec:** Parent relay of owner “继续执行这些项目”, specifically multi-Wi-Fi, date/time and remaining quota. Firmware owns Wi-Fi/time; backend owns quota. The earlier approved design is documented in `2026-10-16-wifi-profiles.md` in the retained source checkpoint; its failed physical candidate must not be installed.

## Global constraints

- Start from exact delivered Funnel `225a4a2`, not failed `556e897` or the unfinished publication checkpoint.
- Five profiles; preserve the legacy network; save credentials only after a fresh association and IP event; no automatic eviction.
- Bounded cancel/failure/fallback; no scans or roaming while healthy; radio changes and voice/status polling must respect ownership.
- Provisioning requires the existing physical owner gesture, WPA2/PMF, nonce, AP-interface checks, bounded bodies and five-minute window. No automatic AP.
- Wi-Fi storage, original pairing, and Funnel override remain separate. Blank pairing fields preserve values; no secret-valued status/log output.
- Keep TLS certificate and hostname verification. Preserve the approved public endpoint build input and numeric endpoint/trust status proof.
- Compact Home footer, MYT UTC+8, minute updates, original `pool.ntp.org` SNTP callback, offline ticking after valid current-boot sync, honest unsynced message. No new timer/task/server or global timezone changes.
- Do not touch backend quota, server, unrelated SDK work or GitHub main. No real passwords, COM7 actions, flash, live provider/model calls, or credential extraction in this phase.

## Review focus

- An idle successful disconnect without an event must permit initial legacy connect; associated and in-flight cancellation must reject stale callbacks.
- Connection-tested saves, failure/cancel and corrupt/full storage must preserve prior profiles, pairing and public-link configuration.
- Linux public CA selection and original Windows private CA must survive Wi-Fi and pairing module integration, including blank pairing updates and manual target changes.
- Clock readiness must be established by the existing SNTP callback, remain honest before sync, tick offline, and respect standby rendering.
- Native fixtures must represent SDK idle success and separate fresh association/IP ordering rather than encoding the old faulty assumption.

## Task 1: Evidence and regression

- [x] Reproduce the idle-success failure against exact old `556e897` source with the corrected SDK fixture; record its expected failing assertion.
- [x] Review installed SDK contracts and the existing correction; cover any demonstrated stale-event gap.
- [x] Verify the delivered baseline tests and restore only exact committed public font bytes if CRLF checkout affects artifact hashes.

## Task 2: Integrate Wi-Fi, pairing and Funnel

Files: `firmware/core/pocket_wifi.{c,h}`, `firmware/main/pocket_wifi_store.{c,h}`, `pocket_pairing.{c,h}`, `pocket_setup.{c,h,html}`, generated setup page, `pocket_voice.{c,h}`, CMake, native/UI setup tests and existing Funnel preservation tests.

- [x] Reuse the earlier Wi-Fi implementation and corrected adapter in the isolated tree.
- [x] First demonstrate that preservation tests reject losing the public Linux override during this merge.
- [x] Integrate pairing reads with the approved public-link overlay and one-time migration without rewriting legacy settings.
- [x] Run profile/storage, setup, voice ownership, pairing and Funnel regressions, using synthetic credentials only.

## Task 3: Reuse and verify the MYT clock

Files: `firmware/core/pocket_clock.{c,h}`, `main.c`, `pocket_ui.{c,h}`, CMake, clock/host tests and snapshot fixture.

- [x] Reuse the formatter and current-boot readiness latch; wire the existing SNTP callback and Home footer only.
- [x] Run invalid/unsynced/offline/calendar-boundary/standby tests and inspect real LVGL renders; preserve other layouts.

## Task 4: Review, build and handoff

- [x] Obtain independent integrated review and fix material findings with regression tests.
- [x] Run the full offline suite and simulator; build with unchanged cached normal SDK config and the approved public endpoint build input.
- [x] Freeze source, app/ELF hashes, partition/memory margins and exact provenance; retain verified `225a4a2` rollback artifacts.
- [x] Report candidate readiness and a bounded app-only hardware acceptance plan. Do not flash until timing/scope are confirmed separately.
- [x] State physical checks still needed: existing-network boot/Funnel polling, clock sync and standby; owner-entered second network, bad password/cancel/fallback and reboot persistence. No new Wi-Fi password is entered by the agent.
