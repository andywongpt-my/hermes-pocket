# Numeric-only diagnostic implementation plan

> **For agentic workers:** Execute assigned independent tasks, then independent whole-change review.

**Goal:** Explain endpoint clipping distributions without recording/exporting PCM or changing audio settings.
**Architecture:** Fixed per-lane total/early/late accumulators in the existing finite runner; read-only rotating UI; pinned real codec source with fake control bus regression.
**Tech Stack:** C11, Python unittest, ESP-IDF 5.5.3 / ESP32-C3, LVGL 9.5.0.
**Spec:** ../specs/2026-10-31-numeric-observations.md

## Global constraints
No device access/flash. Unchanged gain/ADC_SYNC. Preserve normal default-off operation, Stop, one-attempt/boot, cleanup quarantine, no export, and [0x10000,0x271000) app envelope.

## Review focus
Empty/partial capture; window boundary at sample 2400; rails spanning frame/window boundaries or alternating signs; signed endian edges/DC/RMS overflow; terminal cleanup failures and narrow UI worst-case text.

## Tasks
- [x] Core: add failing tests for new snapshot fields, all boundary cases, start/middle/end synthetic ambiguity, and preserved 12 failure paths. Implement fixed observations, run GCC/Clang/sanitizer tests.
- [x] UI: test numbered panes, no-rail sentinel, worst-case values and truncation safety before implementation; preserve old format wrapper/summary and add bounded page function; verify synthetic screenshots.
- [x] Codec (independent): correct inaccurate analog-PGA-only comment, add pinned driver traces and mutation regressions without touching gain writes; document exact register limitations.
- [x] Verification: focused/full aggregate host runs with explicit failures/skips; actual enabled and default-off C3 builds; hashes, recovery/sector bounds, compile/stack/call-path checks and independent review.
- [x] Package source patch/full source, app-only candidate and evidence; checksum/contents verification, persistent Library upload, report actual limits.

Verification completed with 833/841 passing and eight retained historical containment exceptions, not an all-green suite. See packaged TEST-RESULTS.md. Full package integrity/upload is the final delivery step.
