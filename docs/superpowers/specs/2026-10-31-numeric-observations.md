# Numeric-only silent diagnostic observations

Authorized continuation: correct gain documentation, regress pinned codec register writes, add bounded numeric observations, build/test; no device access or flashing. Do not change gain or ADC_SYNC.

Keep total frames/samples/RMS/peak/endpoint clips/DC mean, including startup. Add early first 2400 accepted samples per lane (nominal 100 ms at requested 24 kHz) versus late remainder statistics, separate positive/negative endpoint counts, zero-based first/last endpoint sample index (UINT32_MAX means absent), and longest contiguous endpoint run (either sign). DC means are integer truncations toward zero; RMS includes DC. Timing is sample-domain, not measured wall clock. No warm-up exclusion, sample history, raw export, provider call, persistence or new input gesture.

Use fixed accumulators with bounded counts (1000 frames × 240 samples). Existing Stop, admission, ownership/quarantine and cleanup remain unchanged. Keep summary plus four rotating read-only MIC/REF window/rail panes, 5 s each, numbered; every press still stops an active attempt. All panes retain cleanup/code, full totals remain visible on summary. Default-off builds retain ordinary UI. Source-only hardware gain documentation correction; verify exact esp_codec_dev 1.6.2 behavior without changing it.

Recovery app envelope [0x10000,0x271000); app-only candidate with independent sector-rounded assertion. No bootloader, partition, NVS, raw recovery or flashing scripts in candidate. Host fakes validate logic, not microphone integrity, physical silence, sample timing or device registers.
