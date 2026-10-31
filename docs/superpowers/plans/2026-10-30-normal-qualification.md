# Normal Pocket qualification implementation plan

> **For agentic workers:** Use subagent-driven implementation, TDD, independent review. No commits, hardware, provider calls or installation.

**Goal:** Prepare bounded local audio/transport qualification in normal firmware, never production Live admission.

**Architecture:** Stage A uses a pure framed protocol/core plus the existing voice owner, native paired BSP wrapper, persistent qualification-only USB driver and fresh physical confirmation. Host collection/analysis stays local. Stage B uses an explicit private test route and immutable in-memory test config, with the real worker/network/audio under an independent 60-second deadline. Normal production gates remain byte-identical and default builds unchanged.

**Tech Stack:** Existing C11/FreeRTOS/IDF5.5.3, USB Serial/JTAG, LVGL9.5, Python unittest, pinned native SpeexDSP1.2.1.

**Spec:** `../specs/2026-10-30-normal-qualification-design.md`; parent approved cloud implementation at10:20UTC. Old post-caller source checkpoint remains immutable.

## Global constraints

- Default OFF, finite one attempt per boot; separate explicit host request and fresh physical confirmation. No automatic retry/reconnect or production credentials/settings fallback.
- Silent1000frames/10s; audio2400frames/24s, acquisition24.25s, at most3x2s non-tone windows, PCMpeak512, volume≤5%, never overrides savedmute. Cloudtests generate bytes only, never play them.
- Frame payload≤1024bytes, wire≤3MiB, rawhostPCM≤2,304,000bytes, hostscratch≤8KiB, hostA≤45s. Heartbeatloss≤500ms requestsStop. Any later buttonpress/queue loss Stop before UIqueue.
- Explicit terminal cleanup receipt, retained ownership on failed/unknown cleanup, no new session before verified release. No hard realtime/SPL assurance from software caps.
- No source partition table/bootloader/NVS/security install; original recovery retained. Future flash/capture/output/export/endpoints need specificapproval.

## Review focus

1. Queued or stale ARM/physical events and input loss must never bypass confirmation.
2. Partial USB writes, console contamination, missing heartbeat and RXoverflow must not become a pass or silent data drop.
3. Stop inside speakerenable, setup, output/backpressure or cleanup must retain safe ownership and never resubmit PCM.
4. Host malformed length/CRC/sequence/terminal and retention flags must not grow memory or write sensitive/audio data bydefault.
5. Qualification privateendpoint/path/deadline must not invoke production routes, savedkeys, paidproviders or change readiness.

## Task1 — pure wire/controller/core (stream_worker)

Files: new `firmware/core/pocket_qualification_protocol.{c,h}`, `pocket_qualification.{c,h}`; matching native tests/fixtures. Publish exact shared wire schema before host implementation proceeds.

Interfaces: protocol encodes/validates fixed framed records with runnonce,kind,profile,sequence,offset,payloadCRC; core consumes injected begin/capture/submit/end/emit/stop/clock/idle callbacks, produces terminal result and numeric snapshot. No IDF, allocation, file/network or device dependencies. Parent normal glue supplies ownership/physical/heartbeat predicates.

- [x] Write/run failing tests for framingcaps/CRC, exact1000/2400 counts, 6snonzero cap, PCM≤512/ramping, stopped/expired/clockregression, busywrite/submit, discontinuity, acquiredpartialstart and failedcleanup.
- [x] Implement minimal core/protocol and publish fixed header/schema and signatures to parent + hostagent.
- [x] Native tests plus ASan/UBSan, C-emitted records parsed by real Pythoncollector. Never claim syntheticmeansphysical.

## Task2 — local host collector/analysis and TLS harness (build_pocket_cloud)

Files: new `pocket/qualification.py`, `pocket/qualification_analysis.py`, `pocket/qualification_tls.py` and focused tests/fixtures; final names may be split for one responsibility but coordinate exports.

Interfaces: consume exact Task1 records; expose offline parser/analyzer with explicit nativeSpeexpath and safe numeric report. Collector uses injected/fake serial in cloud; explicit real-port opt-in only in futureCLI. TLS harness accepts only qualificationroute and synthetic bounded messages, defaultloopback, explicit local certificate inputs, no upstream provider imports/config discovery.

- [x] Fail first for malformed/saturated/missingterminal streams, allcaps, nonce/replay/inputloss, no rawbydefault, freshprivatepath/nooverwrite and serial no-control-line-toggle behavior.
- [x] Implement bounded RAM parser, collector and numeric-only analysis with usableinterval/SNR/adaptation logic, sourcecorrelation distinct from actualpairedref.
- [x] Real nativeSpeex synthetic fixtures and localTLS tests, no device/audioout/provider request. Label every automaticmetric appropriately.

## Task3 — USB binding and normal-app admission/UI (resolve_pocket_audio)

Files: new `firmware/main/pocket_qualification_usb.{c,h}`, normal qualification owner/glue, guarded edits voice/main/UI/CMake/Kconfig/defaults; tests for nativefakeUSB, actualinput, LVGL.

Interfaces: persistentboot USB init installs knownowneddriver+VFSdriver once; raw completeframe write with zero driverwait and outer bounded deadline; nonblocking boundedcontrol parser. Parentvoiceowner composes Task1 core with existing `pocket_live_v2_audio` and static Stop/generation/heartbeat state. Hostnonce never authorizes audio without physicaltwo-stepconfirm on Diagnostics.

- [x] Red tests for offbuildidentitybehavior, foreigndriver/installfail, binarynewlinepreservation, logsuppression, partialwrite/backpressure, controlCRC/heartbeat and no destructive/uninstall path.
- [x] Red actualmain/voice tests for host+physicalarm, timeout/nonce/saturation, stalequeuedstart, Stopbeforenetworkqueue, ScreenOff/wake and unknowncleanupquarantine.
- [x] Implement defaultoff binding and UI stating captureexport/outputprofile. Preserve normalcontrols and originalsilentdiagnostic; mutuallyexclusive diagnosticoptions if needed to avoid gestureambiguity.
- [x] Native/sanitizer regressions and actualLVGL snapshots/inspection. Use exactSDKdriver source to justify assumptions, not runtime claims.

## Task4 — inactive StageB actual transport composition (separate owned child)

Files: new qualification transport/controller module; narrowly guarded IDF internal privateconstructor if required; do not modify publicgate/admission files.

Interfaces: explicit validated numericRFC1918IPv4, customCA, transienttoken, fixed `/qualification/v2/live`, no savedconfiglookup. Freshphysicalowner plus immutable≤60sdeadline, actualv2worker/audio/network, max5% outputcap and planned totaloutput/sourcebudgets. Cleanup network-onlyprogress only afteraudioverifiedclean; retainonunknown.

- [x] Red tests for public/ambiguous/privatehost boundaries, absentCA, wrongpath, no prodcredentialfallback, capindependentofworker300s, stopduringbackpressure, malformed/oversizedspeakerframes and cleanupquarantine.
- [x] Implement opt-in internalcomposition preserving ordinaryconstructor/gates. No endpoint/config provision or real networksetting changes.
- [x] ActualC→loopbackTLS synthetichost integration where feasible; IDFfake+targetcompile explicitly distinguished from physicalTLSrun.

## Task5 — verification/artifact handoff

- [x] Independentfresh review of protocol/USB/admission/privacy/transport, reproducefixanyImportant findings.
- [x] Exactdefault + qualification + combinedallowedflag C3 builds, warnings, sourceidentity,3MiBbound, staticmemory/stack caveats; disabled paths test beforeandafter.
- [x] Fullaggregate with allrestoreddeps; report knownbaseline8procfsfailures separately fromnewerrors. Preserve unmodifiedworkingrecovery and publicgate hashes.
- [ ] New source-onlyLibrarycheckpoint with verifieddelta and receipts; separately named exactcandidate only if parent requests it. Provide finitefutureapproval checklist and measured-vs-untestedlimits, no flashcommands/autodeploy.

## Final cloud verification, 2026-10-30 11:38 UTC

Implementation and independent reviews are closed with no remaining Important
finding within their reviewed scopes. Final actual-formatter LVGL rendering and
visual inspection passed after two real title/footer clipping defects were fixed.
The production gate/admission, source partition CSV and dependency lock remain
byte-identical to the verified post-caller checkpoint.

- Full frozen-code aggregate: **790 tests; 782 pass, 5 failures and 3 errors,
  zero skips**, 294.904 seconds. The eight exceptions are the existing procfs
  speech-supervisor environment baseline, not a claim that all tests passed.
  See the final aggregate named-outcome comparison in checkpoint evidence.
- Final host qualification focus: **74 pass**, zero skips; native and ASan/UBSan
  controller/core/USB/voice/transport fixtures pass separately. C encoders and
  workers exercise real local Python framing, loopback TLS and pinned native
  Speex without any device or provider operation.
- Default, APP, QUAL and QUAL+APP target builds pass without compiler warnings;
  all remain below the 3 MiB application bound. Exact qualification-only candidate
  (APP=n, OFFLINE=n) is 2,494,160 bytes and matches a fresh-directory clean build.
  SHA256: `c8f58b769bf6004435da7f8f45bf545f4d6f8cd6cfaedfdcd6819377d05a063d`.
  Final firmware manifest covers 154 exact files. Header is ESP32-C3, revision
  range 0.3–1.99, DIO/80 MHz/8 MiB; checksum and appended digest verified.
- Application bytes are [0x10000,0x270ed0); erase interval is
  [0x10000,0x271000), 609 sectors. **Installation is blocked**: the historically
  preserved interval ends at 0x267000, leaving a 40,960-byte preservation gap.
  No readback, flash, serial open or recovery operation was performed.

The final source-only checkpoint and separate candidate ZIP are being frozen and
preserved by the packaging owner; their archive hashes/Library IDs belong in the
external final handoff receipt, not a self-referential source manifest. This plan
update is a documentation-only delta after the code aggregate; firmware/test code
was unchanged during that run.

Remaining acceptance is explicit, not a code gate override: actual supported
Windows/USB behavior and recovery availability; local capture/export approval;
physical lane/clock/reference and acoustic/near-end evidence; real-device TLS,
heap/stack and Stop/cleanup measurements; and any later provider authorization.
The host fixture is loopback-only and SILENT-only, so a reviewed device-reachable
endpoint is still future work. Current quiet synthetic ERLE (1.006/1.699/2.729 dB)
is below the proposed 10 dB target; no physical amplitude/window increase was made.
The normal app retains legacy Chat/background/settings behavior; only the scoped
qualification path avoids providers/settings writes. Production Live stays false.
