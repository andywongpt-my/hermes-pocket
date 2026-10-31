# Pinned ES8311 gain characterization (no hardware)

The BSP's existing `set_in_gain(30)` call is **not solely analog PGA control**.
The unchanged esp_codec_dev 1.6.2 implementation first writes ADC register 16=24
hex during constructor initialization, then its gain callback writes the whole
register 16=05 hex. Start independently sets register 14=1A and register 17=BF.
This behavior applies to both legacy 16 kHz / 16-bit / mono and paired 24 kHz / 16-bit / stereo;
reference routing register 44 is 08 vs 58, respectively.

The manufacturer's ES8311 register definitions identify register 14's low nibble A
as 30 dB analog PGA, register 16 scale 5 as 30 dB ADC scale, register 17 BF as 0 dB and
register 16 bit 5 as ADC_SYNC. Thus the gain call also clears ADC_SYNC from its
initial state. This source fact does **not** prove the cause of observed endpoint
samples, authorize gain/ADC_SYNC changes, or establish an appropriate new value.
No runtime gain, clock, format or register behavior is changed by this work.

## Portable characterization

```sh
POCKET_CODEC_DIR=/path/to/extracted/esp_codec_dev python3 -m unittest discover -s tests -p test_codec_register_trace.py -v
```

Use `CC` for a GCC/Clang-compatible host compiler if needed. Without an explicit
path, the test checks firmware/managed_components/espressif__esp_codec_dev, then
the existing HPsdk component-cache convention. Missing optional dependencies are
reported as skips; an invalid explicit path or wrong component bytes fails.
There is no automatic download, dependency installation or device access.

The test compiles the entire actual pinned ES8311 driver and volume helper, not a
copied implementation of gain conversion. Only control registers, reference
counts, SDK logging/errors and absent hardware interfaces are faked. Both mode
paths assert constructor gain state, final gain state, and reference routing.
Five temporary driver mutations verify the harness rejects wrong initialization,
analog PGA, ADC volume, gain/ADC_SYNC and reference-routing writes. A separate
mutation verifies dependency identity rejection. The deliberately mutated copies
exist only in temporary test directories; real component/firmware files are not
modified. The fixture uses explicit failed checks, not removable NDEBUG asserts.

This characterizes the codec callback invoked by the public input-gain API, not
the outer esp_codec_dev wrapper, complete BSP, real I2C/I2S, analog sound, clock
timing or a root cause. The upstream source has a signedness warning in get_coeff;
it is not patched or treated as hardware evidence. Host stdlib declarations are
supplied via compiler `-include stdlib.h` because the fake SDK log header does not
provide SDK-transitive includes. No future driver behavior is implied by this
version-specific characterization; an intentional dependency change requires
reviewing the pin and behavioral expectations together.

## Provenance

Official registry index:
https://components-file.espressif.com/components/espressif/esp_codec_dev.json

Exact archive:
https://components-file.espressif.com/components/espressif/esp_codec_dev/1.6.2/espressif__esp_codec_dev-v1.6.2.zip

Official checksums:
https://components-file.espressif.com/components/espressif/esp_codec_dev/1.6.2/CHECKSUMS.json

The registry component hash is
`4779f31a3ba3f9b38aee1afe6ccb9ba8f6108dc3a6e77d39d7d714e9a215371b`, matching
firmware/dependencies.lock. Metadata pins esp-adf commit
`af7b72fb2b73d4f513d3cede01c13518ab216735`, components/esp_codec_dev.
Downloaded archive 229,506 bytes, SHA256
`50b5d2b7687fd7f6e93fda110aecf00cfe12e2bd34d77b90a01efb6dd904d88d`.
The archive hash is a local receipt, not the registry's aggregate component hash.

tests/fixtures/codec_register_trace retains the official 97-file checksum manifest
and registry/download provenance. Each test run verifies all 97 source files and
the manifest's pinned SHA256, rather than trusting a version string alone.
The external dependency remains external; its full source is not vendored here.

Primary manufacturer register reference (mirror):
https://files.waveshare.com/wiki/common/ES8311.DS.pdf, pages 20–21.
