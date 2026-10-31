# Silent numeric observations (version offline-obs-1)

This extends the existing finite, silent, offline diagnostic. It does not change
codec gain, ADC_SYNC, audio format, mute/zero-output behavior, Stop, admission,
one-attempt-per-boot policy, ownership/quarantine, network policy or recovery
layout. No PCM is retained as history, exported or written to persistent storage.
The existing one-frame buffer is still wiped before reuse and on exit.

## Reading the five panes

After arming, panes automatically rotate every five seconds using uptime. Pane
numbers are in the heading. There is no new key gesture: any press during the
active attempt still requests Stop; the existing UP/DOWN navigation and screen
off remain available. Idle/armed instructions do not rotate. A full rotation
takes 25 seconds; this view does not require another capture or reset.

1. **Totals:** original frames (maximum 1000), both lanes' RMS, peak, endpoint
   counts and cleanup/code. Startup samples remain included.
2. **MIC windows:** all-sample DC mean, early and late sample counts, RMS (`R`),
   peak (`P`), DC mean and endpoint counts (`Rails`).
3. **REF windows:** same fields for the reference lane.
4. **MIC rails:** positive (+32767) and negative (-32768) endpoint counts, first
   and last endpoint sample positions, and longest contiguous endpoint run.
5. **REF rails:** same fields for the reference lane.

Early is accepted sample indices **[0,2400)**; late is **[2400,N)**. At the requested
24 kHz, 2400 samples nominally correspond to 100 ms. No real clock frequency or
wall-clock sample arrival time is measured by these fields. `N` is the accepted
per-lane sample total, not frames; 1000 frames × 240 samples = 240000 samples.
Partial captures can have fewer than 2400 early samples and zero late samples.
No warm-up is discarded. `none` means no endpoint was observed; an endpoint at
sample zero is displayed as `0`, not `none`. A contiguous run includes either
rail sign and can cross frame/window boundaries. First/last positions are
zero-based sample indices, **not milliseconds**. All panes retain cleanup/code.

DC means use signed integer division (truncate toward zero); RMS is integer
floor square root of mean square and includes DC. Peak 32768 is the valid absolute
magnitude of signed -32768. Endpoint counts are not a complete analog-distortion
detector. These observations distinguish some distributions, not every waveform
or physical cause. A zero reference remains compatible with muted, zero-filled
output; it does not establish lane mapping, acoustic silence or AEC quality.

## Implementation bounds

- Two lanes; at most 240000 accepted samples per lane; no dynamic allocation,
  ring of observations, PCM history or extra worker.
- Fixed 64-bit sums/square sums and fixed 32-bit counters/positions/runs. The
  snapshot is 164 bytes (was 52). Maximum absolute sum is 7,864,320,000 and square
  sum 257,698,037,760,000, both within the chosen 64-bit types.
- Window RMS/means are calculated only immediately before the existing periodic
  publications and terminal publication: at most 101 summaries for a full run.
  Terminal partial frames already accepted are summarized even when their count
  is not divisible by 10. Unaccepted/stale/discontinuous frames remain excluded.
- Existing 2-second startup and 10.25-second acquisition guards remain observed
  software deadlines, not preemption of stuck SDK calls. No new hardware timing
  or maximum Stop-latency guarantee is claimed.

## Regression coverage

`test_offline_observations` compiles the actual core runner: three different
synthetic distributions reproduce the observed 882-frame/RMS 2683/peak 32768/1030
endpoint totals, but the new fields distinguish early, late and isolated rails.
It also covers empty and partial captures, the 2400-sample boundary, asymmetric
signed/little-endian values, runs crossing frame/window boundaries, positive and
negative full-scale 240000-sample bounds, and published DC means. Existing core
and voice tests retain the failure/Stop/cleanup/admission paths.

`test_offline_observations_ui` checks format, no-endpoint sentinel, cleanup,
Stop hints and tiny buffers. `test_offline_observations_render` uses actual LVGL
9.5.0 and production UI, checks 120 phase/pane/cleanup combinations for body,
badge/footer bounds, saves ten synthetic example/max images and also exercises
legacy snapshots. Those maximal independent values are display stress cases,
not a physical waveform.

`test_codec_register_trace` verifies all 97 files of pinned esp_codec_dev 1.6.2,
executes the actual ES8311 driver with a fake register bus for both audio modes,
and rejects five behavioral mutations plus altered dependency bytes. See
[PINNED-CODEC-REGISTER-TRACE.md](PINNED-CODEC-REGISTER-TRACE.md) for scope/provenance.
This tests the existing surprising gain behavior, not a proposed gain fix.

Run focused host tests from the source root (no device access):

```sh
POCKET_CODEC_DIR=/path/to/exact/esp_codec_dev \
PYTHONPATH=tests python3 -m unittest -v \
  test_codec_register_trace test_offline_observations \
  test_firmware_offline_diagnostic test_firmware_offline_voice \
  test_offline_diagnostic_ui test_offline_observations_ui
LVGL_DIR=/path/to/exact/lvgl-9.5.0 PYTHONPATH=tests \
  python3 -m unittest -v test_offline_observations_render
```

The candidate bundle includes the actual sdkconfig, build/toolchain provenance,
full aggregate logs (including unrelated failures), independent review and an
offline `verify_bundle.py`. Firmware builds are host-side only. The older
partition-slot checker alone permits a larger envelope; release additionally
asserts sector-rounded app end <=0x271000. No flashing or device experiment is
authorized or performed by this work.
