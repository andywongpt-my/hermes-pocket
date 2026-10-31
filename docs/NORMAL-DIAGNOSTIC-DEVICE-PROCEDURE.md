# Normal firmware: minimum silent diagnostic trial

**Cloud-built candidate, not a tested device release.** The separate standalone
bench image is superseded and must not be used for this trial. This procedure
does not authorize installation or a physical trial; those require separate
approval against the exact new candidate identity and intended device.

## Before a device operation

1. Verify the candidate ZIP/app hashes against its final manifest. Confirm the
   intended device is the previously identified ESP32-C3 revision 1.1 with 8 MiB
   flash, secure boot and flash encryption disabled, and factory app interval
   `[0x10000, 0x310000)`. Do not infer the rest of its partition table from this
   source tree. Stop if the device identity or these facts differ or are uncertain.
2. Have the **device-proven original recovery app** and a previously verified,
   compatible app-only restoration procedure available. A newly compiled default
   image is not proven recovery. Have an independent physical recovery/reset
   method that does not depend on this application's UI. Screen Off is standby,
   not demonstrated power removal. If recovery is unavailable, do not install.
3. Approve only the exact application and its documented sector-rounded interval
   at `0x10000`. Preserve the installed bootloader, partition table and settings.
   No whole-chip erase, generated partition image, combined image, NVS replacement
   or security change belongs to this trial. This document supplies no flashing
   command and no automated device operation.
4. No new key is needed. Do not configure a provider, use Chat/voice, change
   volume, or export audio during the trial. The normal application retains
   ordinary networking and settings behavior before/after the diagnostic; it is
   **not an entirely offline or persistent-write-free image**. The diagnostic
   itself contains no provider/network request or settings write.

## Minimum controls and acquisition observations

Use a quiet setting with the speaker away from ears. Have the approved recovery
method ready; do not rely on software mute or standby as an acoustic safety kill.

1. After separately approved app-only installation, check normal page navigation
   and Screen Off/wake. The complete wake gesture must only wake, not start a
   recording or diagnostic. Stop if the normal controls do not behave correctly.
2. Navigate to Diagnostics. Hold OK until **SILENT: ARMED**, release it, then use
   a **new OK press within 10 seconds**. Holding the old press or a delayed click
   must not start capture. There is only one attempt per boot, including an
   expired/canceled arm; there is no automatic retry.
3. First trial: after capture starts, press any button once to stop. Return to
   Diagnostics if navigation changed pages. Record the terminal title, frames,
   MIC/REF RMS and peak, clipping counts, cleanup label and numeric code. Expected
   controlled stop is **STOPPED / ERROR**, code `1`, cleanup **checked** if audio
   began. Cancellation before the BSP begin operation instead says **not started**.
   Once a bracket was acquired, cleanup must be **checked** even if zero frames
   were captured. Failure
   to stop, unexpected sound, heat, loss of controls, **FAILED**, or **unavailable**
   means stop the experiment and use the approved recovery procedure; do not
   repeatedly retry. Software deadlines cannot preempt a stuck SDK/codec call.
4. Only after the stop trial is accepted and a further run is approved, manually
   reboot using the known method. Repeat arming once and let the finite run end.
   Expected completion is **SILENT: FINISHED**, `Frames 1000 / 1000`, cleanup
   **checked**, code `0`. The nominal sample window is 10 seconds; startup and
   cleanup add time. Record observed elapsed time, the same numeric fields,
   whether any sound occurred, and whether navigation and Screen Off/wake still
   work. Do not reset a failed cleanup merely to conceal the failure and retry.

The screen shows numeric summaries only. No raw microphone/reference recording,
USB audio export, transcript or tone is produced. Mean and sample counts exist
in the internal summary but are not displayed. Local temporary PCM is overwritten
where owned by the diagnostic; this is not a whole-memory erasure guarantee.

## What a passing trial does and does not establish

It can establish basic on-device control, no detected paired-frame discontinuity and finite
capture/cleanup behavior for this exact build. Screen labels and register checks
alone cannot prove actual silence, physical power-off, lane mapping, acoustic
echo cancellation, actual speaker playout timing, double-talk quality or latency.
Full-duplex GPT-Live remains gated off. Legacy voice and recovery are retained
until a replacement has separate physical acceptance.

Code key: `0` completed; `1` stopped; `2` arm expired; `3` start failed;
`4` capture failed; `5` frame discontinuity; `6` clock fault; `7` startup budget;
`8` acquisition budget; `9` cleanup failed; `10` admission failed. A code alone is
not success: require the terminal phase and cleanup label as well.
