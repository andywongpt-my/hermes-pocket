# Paired-reference firmware build verification — 29 October 2026

**Both actual ESP32-C3 builds passed. No hardware experiment was performed.**

This supersedes only the build numbers for the paired-reference continuation;
the preceding checkpoint's evidence remains unchanged. Toolchain and exact
dependency provenance are recorded in [the earlier build report](GPT-LIVE-CLOUD-BUILD.md):
official ESP-IDF 5.5.3, RISC-V GCC 14.2.0 (`esp-14.2.0_20251107`), CMake 3.30.2,
and the unchanged locked component hashes, including esp_codec_dev 1.6.2.

| Final configuration | App image | Static DRAM used | Static DRAM remaining |
| --- | ---: | ---: | ---: |
| Default, experimental features off | 2,427,616 bytes | 182,484 bytes | 138,812 bytes |
| All experimental compile options on | 2,440,720 bytes | 196,288 bytes | 125,008 bytes |

Both images fit the configured application partition with 71% free. The
experimental build selects:

```text
CONFIG_BSP_AUDIO_CALLBACK_RENDER=y
CONFIG_POCKET_LIVE_WORKER_COMPILE=y
CONFIG_POCKET_LIVE_PAIRED_COMPILE=y
CONFIG_I2S_ISR_IRAM_SAFE=y
CONFIG_LWIP_SO_LINGER=y
```

The v1/v2 public release gates remain refused. No UI invokes the new worker,
and no configuration flag constitutes physical acceptance or activation.

## Actual target structure sizes

These are RISC-V compiler DWARF sizes, extracted using the pinned target
`readelf`, not host `sizeof` estimates:

| Structure | Bytes | Meaning |
| --- | ---: | --- |
| `bsp_audio_capture_frame_t` | 976 | 960-byte paired PCM plus metadata |
| `bsp_audio_capture_t` | 7,864 | Eight frames (7,680 PCM bytes), descriptor/timing/queue state |
| `bsp_audio_render_t` | 3,956 | TX source queue and descriptor ledger |
| `pocket_live_v2_worker` | 6,900 | Caller-owned bounded worker state |
| `paired_idf_context` | 6,616 | Private adapter context including the next two rows and assembly buffers |
| `pocket_live_network_context` | 3,560 | Nested network ownership and bounded buffers |
| `pocket_live_v2_audio_context` | 984 | Nested paired audio wrapper/frame |
| `pocket_live_network_dns` | 252 | DNS ownership state |
| `pocket_live_network_deadline` | 24 | Deadline state, also nested in network context |

Do not add nested rows twice. Worker plus private adapter context would require
13,516 caller-owned bytes, **in addition to** their dependencies; this is not a
complete runtime allocation budget. Inactive worker/network functions and their
unreferenced data are linker-dead-stripped. All optional object bodies compile,
but the linked image does not demonstrate that an activated worker fits or runs.
TLS/driver/transport allocations, task stacks, fragmentation and concurrent
Wi-Fi/UI loads require measured acceptance. Static DRAM remaining is not free
runtime heap, nor a safe concurrency allowance.

## ISR compiler/linker inspection

The linked experimental ELF places TX, RX and overflow callbacks,
`bsp_audio_capture_complete` and `bsp_audio_render_complete` in `.iram0.text`.
Both capture/render completion bodies have no function calls in their target
disassembly. The RX callback's timer path is also audited: `esp_timer_get_time`
aliases `esp_timer_impl_get_time`; its direct helper
`systimer_hal_get_counter_value` and initialized indirect target
`systimer_ticks_to_us` are in IRAM. Timer state, reference-mode flag, capture
queue, render ledger and span/counter data are internal DRAM. Critical-section
helpers are in IRAM.

This establishes compiled placement/call targets, not actual ISR duration,
cache-off behavior, callback-gap detection completeness, acoustic alignment or
clock quality. A 960-byte ISR copy still needs real-device timing validation.

## Evidence and boundaries

The enclosing checkpoint's `verification/paired/` retains full build/size logs,
binary hashes, sdkconfigs, final 117-file firmware source-hash manifest, target
type-size evidence, and linked ISR disassembly/symbols. Late network edits were
content-resynced with fresh mtimes and rebuilt. The final default build was also
fully recompiled to avoid trusting stale incremental objects after preserved
copy timestamps. Both configurations were checked against the final source.

No dependency pin, runtime service, credential or deployment configuration was
changed. No provider request, device connection, flash, acoustic test, memory
measurement or physical Stop test occurred. DMA consumption remains flow credit,
not a claim of actual audible playback. See the separately approval-gated
[minimum no-provider bench plan](GPT-LIVE-PAIRED-BENCH-PLAN.md) for remaining work.
