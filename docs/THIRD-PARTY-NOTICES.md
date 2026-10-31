# Third-party notices

The optional native-24-kHz host AEC wrapper uses the separately installed
SpeexDSP 1.2.1 public C ABI (BSD-3-Clause). The library is not bundled or
automatically loaded; its notice is retained in `licenses/SpeexDSP-BSD.txt`.
See `GPT-LIVE-AEC.md` for the pinned source archive and synchronization gate.

The board support package under firmware/components/bsp derives from
FoloToy/ai-passport commit a9320ab6eaa21b90759a4a22d1039092385f24dd,
with local Hermes Pocket modifications including display rounding, battery,
audio and sleep/wake support. It must not be described as an unmodified upstream copy.
Its MIT license and copyright notice are retained in that directory. Firmware
sdkconfig.defaults, partitions.csv and dependencies.lock originate from the same
revision. The application UI and pocket model are new Hermes Pocket code; the
upstream demonstration UI is not reused.

Firmware builds resolve ESP-IDF 5.5.3 and the pinned managed components recorded
in firmware/dependencies.lock. Their own license files must accompany redistribution
where required. This publication package contains source only, without SDK,
managed-component distributions or compiled binaries. Audit and include their
required notices separately when distributing binaries. The core/mock Python
bridge requires only Python's standard library. No Hermes Agent source is bundled; HTTP contracts are implemented
independently against the documented API.

## Integrated candidate additions

The live Windows transport optionally uses websockets 16.0 (BSD-3-Clause).
Speech decoding invokes a separately installed FFmpeg executable; FFmpeg is not
bundled. Its own distribution/build license must be observed by the installer.
The core/mock Python implementation remains standard-library only.

LVGL's bundled 16 px common CJK subset is enabled without modifying the font.
It derives from Adobe Source Han Sans SC, SIL Open Font License 1.1; see
licenses/SourceHanSans-OFL.txt. Not all Chinese characters are included.
Source Han Sans license text is retained in this source package. Any separate
binary release must include the required SDK/component license and notice files.
