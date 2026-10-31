# Windows SILENT qualification host implementation plan

**Goal:** Source-prepare a no-port preflight and explicit one-shot SILENT launcher,
without changing firmware or accessing any device.
**Architecture:** Existing HPQ1 Collector and retained-owner serial transport;
small immutable-artifact preflight plus orchestration/signal/report boundary.
**Stack:** CPython 3.11/3.12, pinned pyserial 3.5 for a future approved Windows run;
standard library for preflight and cloud fake tests. No dependency installation.
**Spec:** ../specs/2026-10-30-windows-qualification-design.md, parent approved11:50UTC.

## Constraints/review focus
- Existing candidate and all firmware files stay byte-identical.
- No actual COM discovery/open, flashing, playback, real capture, keys or providers.
- Exact app/ZIP/recovery hashes; canonical explicit COM port; no inferred identity.
- Stop must not cancel healthy in-flight I/O; no overlapped close or uncertain retry.
- Default no I/O. SILENT only, <=45s collection, RAM PCM wiped; numeric fresh-file
  summary only. Approval and platform evidence are separate from file preflight.
- Console close/process termination cannot be made safe by software claim; retained
  hung owners must remain visible and no cleanup success may be fabricated.

## Task1 — serial/collector lifecycle (build_pocket_cloud)
- [x] Read pinned Win32 backend; reproduce independent defects before fixing.
- [x] TDD explicit 8N1/no-flow-control setup and optional Collector stop_requested
  callback, default None; existing callers unchanged. No cancel of normal reads.
- [x] Exercise pre-ARM stop, read-boundary Stop, delayed/failed open/close and
  missing/late terminal with fake drivers; never claim physical reset behavior.

## Task2 — no-port preflight (resolve_pocket_audio)
Files: pocket/qualification_preflight.py, tests/test_qualification_preflight.py.
- [x] Write failing tests for exact local file hashes/sizes, bounded reads,
  canonical COM1..COM256, path/argument privacy and incompatible environment.
- [x] Implement preflight(candidate_zip,recovery_file,port,identity): fixed numeric
  facts only; no serial import, subprocess, discovery, network or file writes.
  Separate environment compatibility from physical platform validation.
- [x] Verify against the actual immutable candidate and synthetic-only recovery
  fixture (explicit test substitution, not real AW bytes); keep actual AW recovery
  identity fixed in production constants and report provenance honestly.

## Task3 — one-shot launcher (resolve_pocket_audio)
Files: pocket/qualification_windows.py, tests/test_qualification_windows.py,
requirements-qualification.txt, docs/qualification-windows.md.
- [x] Failing tests first: default/help/preflight has no port effects, missing
  approvals refuse before adapter, no AUDIO/TLS/raw flags, fixed error output.
- [x] Compose one adapter + Collector, cooperative Ctrl+C latch, checked close,
  retained pending/unknown cleanup states, bounded numeric report and raw wipe.
  Keep a pending owner alive rather than automatically exiting the process.
- [x] Test actual protocol synthetic complete/Stop/corruption streams, healthy
  delayed polls, pending I/O/close, repeated signals, no second run or close retry.
- [x] Document exact AW no-port checks and separately approved original-firmware
  open/close validation needed before any flash proposal; no flash commands.

## Task4 — independent review, aggregate and preservation
- [x] Review complete diff and pin defects with red/green tests.
- [x] Full restored-dependency suite; name the eight procfs baseline exceptions.
- [ ] Verify all firmware/candidate bytes unchanged, freeze source and preserve
  separately named checkpoint/host handoff with hashes and updated recovery history.

## Final cloud status, 2026-10-30 12:27 UTC

Parent approved the distinct no-read/no-write platform open/close probe at 12:10 UTC.
It reuses the same existing scope acknowledgements without pretending platform
behavior was already verified. These encode one agreed scope, not repeated user
confirmation requests. Probe API results never assert physical acceptance.

- Final full suite: **833 tests, 825 passed, 5 failures + 3 errors, zero skips**,
  286.084 seconds. Exact eight procfs child-enumeration exceptions match the prior
  verified checkpoint; no containment check was weakened and no new failure arose.
  All 536 source files remained unchanged throughout this run. This plan-status
  note is the sole documentation-only update afterward.
- Final Windows/preflight/serial subset: **40 tests passed**, 4.551 seconds.
  Launcher 16 includes actual synthetic 1,000-frame HPQ1, probe-only CLI, signals,
  pending/unknown cleanup and broken-output ownership. Eight preflight tests include
  exact artifact identities, Windows path/drive boundaries and no-port behavior.
  Fake-Win32 execution of the actual pinned pyserial methods reproduces its
  suppressed-open-cleanup and unchecked-close-return limitations.
- Independent review: no remaining Important finding in reviewed scopes; six
  separate path/read probes and two actual-wrapper output/ownership probes pass.
  Reproduced reserved-device aliases and broken-output-before-guard defects were
  fixed red-to-green. Final portability test uses mocked lstat instead of requiring
  real Windows symlink creation privileges. No native Windows/serial action ran.
- Every 154 firmware file and the existing app binary remain byte-identical. This
  is host-only preparation; no new image/build/flash is needed or authorized here.
  New source checkpoint preservation follows this freeze; its Library ID/archive
  hash will be in the external handoff receipt rather than a self-reference.

The historical full-window recovery hash is now recorded, but actual recovery
bytes were not read in cloud. Next: AW no-port preflight and fake checks using its
exact existing interpreter; then one separately approved original-firmware probe
with operator-observed reset/disconnect/UI behavior. The probe records API/hold
facts only and may retain an unknown owner indefinitely. Only reviewed actual
platform evidence can justify later SILENT capture/flash proposals. Acoustic,
full-duplex, real-device TLS and production-provider acceptance remain separate;
production gates stay false. No device operation, package installation, settings
change, real capture/output, credential or provider operation occurred.
