# Normal UI offline diagnostic candidate — installation gate

This candidate supersedes the separate `hermes-pocket-bench` image for the current
experiment. Do not install that earlier standalone bench image. The new candidate
is the normal Hermes Pocket application with a default-off, manually armed,
bounded **SILENT** diagnostic enabled in this particular build. Legacy voice,
normal UI and existing standby/wake behavior remain. Existing software standby
is **not proof of real power-off**. No real-device validation has been performed
by the cloud build.

## App-only boundary

Only the factory app interval **[0x10000, 0x310000)** is confirmed in the supplied
history: 3 MiB. `firmware/partitions.csv` now bounds the build to that interval.
Its other rows are unverified placeholders; the CSV is NOT a verified copy of
the device's complete partition table. **Never install the generated partition
image, bootloader, combined image, or NVS data.** The candidate package contains
only an application binary and audit documentation.

`firmware/tools/check_app_bounds.py` is a host-only build dependency of `app` and
`ALL`. It rejects an empty app, an app larger than `0x300000`, any other app offset,
and any 4096-byte-sector-rounded erase end beyond `0x310000`. These checks do not
query a device, verify its installed layout, or authorize a flash operation.

## Required separate physical approval

Before any installation, identify the exact intended existing device and confirm
that its supplied facts still apply: ESP32-C3 revision 1.1, 8 MiB flash, secure boot
and flash encryption off, and the confirmed factory app slot above. These are
user-supplied facts, not cloud-probed facts. If any are uncertain, stop and request
separately authorized verification rather than guessing or changing security.

Record the candidate filename, byte count and SHA256, the exact app offset, and
the proposed app-only write/sector interval. Ensure a **device-proven original
recovery app** and a previously verified compatible restoration procedure are
available before approving the experiment. A newly compiled default image is not
a proven recovery app. Recovery must not require replacing bootloader, partition
table, settings/NVS, changing security, or whole-chip erase. If no proven recovery
exists, do not proceed. Approval for building this artifact is not approval to
flash, read back flash, or run the physical experiment. No executable flash
command is supplied here.

Preserve current settings and original firmware outside this diagnostic. Avoid
printing or exporting credentials or reading all flash merely to obtain a backup.
No provider credential is required for the silent diagnostic. Normal firmware
still contains its ordinary network, provider, and settings/NVS code: **this is
not a network-free or persistent-write-free whole image**. The diagnostic itself
must perform no HTTP/provider activity or settings writes.

## Physical trial scope, after approval only

Use the normal Diagnostics UI to arm the finite silent run exactly as described
in the diagnostic implementation/UI documentation. Begin with the speaker away
from ears; do not infer acoustic safety from digital silence, since startup or
codec faults still need observation. Confirm the local button stop path before
accepting a result; unexpected sound, UI failure, heat or inability to stop means
abort and restore via the approved recovery procedure. No tone, gain increase,
new hardware, provider call, recording export or automatic repeat is part of this
trial. Signal statistics and paired sample counts alone do not establish lane
mapping, acoustic cancellation, audible volume, latency, double-talk, real
power-off, or a working full-duplex Live voice path. Live stays inactive pending
its separately approved physical validation.

## Build receipt

Build evidence lives under `verification/offline-diagnostic/`. The candidate
configuration enables `POCKET_OFFLINE_DIAGNOSTIC` and `BSP_AUDIO_CALLBACK_RENDER`
(and its selected `I2S_ISR_IRAM_SAFE`), while both Live compile flags remain off.
`APP_REPRODUCIBLE_BUILD=y`, `APP_PROJECT_VER_FROM_CONFIG=y`, and
`APP_PROJECT_VER="offline-diag-1"` fix time/version metadata. Normal project name
is `hermes-pocket`. Exact SDK 5.5.3 and unchanged lockfile dependencies are used;
all five managed component content hashes were recalculated and match the lock.

For a fresh private source copy with the recorded SDK/toolchain and dependencies,
apply the recorded candidate sdkconfig, run `idf.py reconfigure`, then
`ninja -C build -j 8 all`. The guard runs as an `ALL`/`app` dependency. Build
outputs include SDK-generated bootloader/partition artifacts internally, but
**none may be included in the candidate package or installed**. Only the exact
verified app binary is eligible for the separate approval process.

The default-off regression retains the normal UI and omits all diagnostic
symbols: app 2,427,776 bytes; static D/IRAM 182,484 used / 138,812 remaining.
Candidate static D/IRAM is 196,452 used / 124,844 remaining. These are link-layout
numbers, **not measured runtime heap**. The existing voice owner task has a
12,288-byte configured stack. Compiler direct frame sizes on the capture chain
are 752 + 48 + 1,120 + 992 = 2,912 bytes, excluding deeper BSP/SDK calls and
interrupt overhead. This is neither a complete worst-case stack bound nor a
measured high-water mark. Audit-only stack objects are not linked into the image.

The RX/TX/overflow callbacks, render/capture completion loops, timer helper and
normal critical-section helpers reside in IRAM; callback state resides in DRAM.
Completion loops contain no out-of-line calls. The timer conversion indirect
call targets the pinned SDK IRAM conversion. This static inspection does not
prove ISR latency, overflow behavior on hardware, driver-fault paths or a
complete hard-real-time guarantee. No IRQ behavior changes were made for the
new task-side silent bracket.

Artifact identity and clean-rebuild equality are recorded in the final receipt;
do not substitute the initial/intermediate build hashes.


## Exact candidate identity (final)

- File: `hermes-pocket-normal-offline-diagnostic-20261029.bin`
- Length: **2,452,624 bytes** (`0x256c90`), within the **3,145,728-byte** slot.
- SHA256: `1d927b54a7269b33c4b5cd9e9cce858edb1ba052b9bb199995246261eca412b4`
- Application write begins at `0x10000`; file ends at `0x266c90` exclusive.
  Sector-rounded erase interval is **[0x10000, 0x267000)**, below `0x310000`.
- Image: ESP32-C3 ID5, minimum revision0.3 / maximum1.99; DIO80MHz, 8MiB;
  `hermes-pocket`, version `offline-diag-1`, IDF5.5.3, secure version0.
  Checksum and appended validation hash verified locally. Compile time blank
  under reproducible build mode. No device signing/encryption release supplied.
- A fresh empty-build-directory rebuild is byte-identical to the frozen first
  pass. All **132** firmware source-file hashes match source and both build copies.
  Source-manifest SHA256:
  `8077a9d529ba4132fa13b6844b4a1f550440f5d5414c98169dd24f0e9ddcd70f`.

These values identify the only candidate from this task. The standalone bench
binary, intermediate hashes, generated partition table and bootloader are not
substitutes. The final receipt is `verification/offline-diagnostic/build-summary.json`;
`image-info.log`, `final-equality.json` and the two full build logs retain checks.
