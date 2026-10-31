# Normal-firmware offline SILENT diagnostic

`CONFIG_POCKET_OFFLINE_DIAGNOSTIC` is default-off and depends on the paired BSP
callback mode. This is an opt-in diagnostic inside the **normal application**, not
an installed image, a GPT-Live activation, an offline voice replacement, or the
older standalone USB/tone bench launcher. No hardware operation was performed in
its source/offline tests. Normal startup, network behavior outside this attempt,
and documented Screen Off standby/wake remain separate existing behavior.

## Admission and ownership

Diagnostics LONG OK arms once per boot for 10 seconds. The UI passes a fresh clock
sample and current callback event counter, not the delayed LONG event's timestamp.
A later physical OK PRESS must first be observed by the button callback and then
match its exact timestamp/counter at main-queue admission. CLICK, release of the
held LONG gesture, stale input, wake input and counter exhaustion cannot start it.
Other buttons cancel an arm; any physical PRESS while queued/starting/running
latches Stop before the main queue. Input queue loss fails closed.

The existing voice command queue receives private sentinel 2; public normal voice
entry still admits only targets 0/1. The same existing worker branches on 2 before
any provider/body/config/index/HTTP operation. It holds the existing setup voice
lease and busy state, invalidates idle reader work and pauses its normal poll loop.
No additional audio worker or codec owner exists. A previously in-flight idle HTTP
request may finish before the queued diagnostic dispatches: dispatch revalidates
the original 10-second arm deadline, so delayed work cannot later start capture.
This is not a promise to forcibly cancel an already-running normal request.

## Finite silent acquisition

The BSP diagnostic bracket acquires its internal silent lease before hardware I/O,
initializes or wakes with true mute retained, and enters paired 24 kHz capture.
The runner never submits PCM output or changes volume. It accepts exactly 1000
owned 240-pair frames, validating epoch, sequence and sample offset. Both lanes are
signed 16-bit little-endian microphone-left / internal-DAC-reference-right samples; hardware lane
identity and acoustic properties are still unvalidated.

The internal numeric snapshot contains per-lane signed integer mean, integer RMS,
absolute peak, clipping count, sample/frame counts and cleanup status. It is
updated at most every 10 frames. The screen displays frame count, MIC/REF
RMS/peak/clips, cleanup status and code; mean and sample count are computed but
not displayed. This does not show acoustic quality, exact clock timing or a
waveform. There is
**no raw USB export, audio file, transcript, provider call, diagnostic network
request, diagnostic NVS write or automatic tone**. The normal image is not wholly
offline: ordinary background activity can occur before and after the diagnostic.
Raw runner and adapter stack
frames are explicitly overwritten on success and error paths; this is not a
forensic whole-device-memory erasure guarantee.

Startup has a 2-second observed software deadline. The 1000-frame/10-second sample
window has a 10.25-second acquisition deadline, including 250 ms scheduling allowance.
Fresh clock checks catch regression and expired budgets around operations. These
checks cannot preempt a stuck serialized SDK/I2C operation and are not a hardware
watchdog or PA kill guarantee.

## Cleanup and normal behavior

Every acquired BSP diagnostic bracket is ended, including partially failed begin.
The bracket keeps hardware muted while stopping, restoring saved normal format,
and verifying sleep; only then can cached normal volume/mute intent be restored
in software for the next normal wake. The runner publishes terminal summary only
after that cleanup attempt.

A canceled attempt that never began is reported as cleanup not attempted, not as
verified restoration; its untouched worker lease can be released. BSP begin
rejection without an acquired bracket does not end foreign ownership: it reports
unavailable and conservatively quarantines the voice worker.

Verified cleanup releases the setup/voice lease and normal worker polling may
resume. Cleanup failure retains busy state, ownership and audio-unavailable status;
there is no automatic retry, new voice admission or background polling from this
worker while quarantined. The UI can still navigate and use documented Screen Off
standby; it must not claim physical power removal or successful audio restoration.
Low software volume, true-mute register readback and source tests do not establish
SPL, physical silence, capture clock correctness, AEC quality or measured stop time.

## Evidence

`test_firmware_offline_diagnostic.py` compiles the actual pure runner/controller;
`test_firmware_offline_voice.py` compiles the actual feature-gated voice-owner block
with fake SDK/BSP boundaries and checks sentinel ordering in the worker. Separate
UI/input rendering and BSP lease tests exercise their own implementation paths.
A real IDF build verifies compilation/linkage only. Physical acceptance remains
separately authorized and unexecuted.
