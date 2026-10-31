# Cloud firmware build verification — 29 October 2026

> This document records the earlier audio checkpoint builds. For the later paired-reference builds and current acceptance status, see the checkpoint-root `CLOUD-RESULTS.md` and `verification/paired/` receipts.

**Actual ESP-IDF compilation passed. This is not a physical duplex release.**

The official ESP-IDF `v5.5.3` checkout was fetched at
`2c211b236707889e8400c4dc5644dd5c4ee071e0`, with every recursive submodule
at its recorded commit and a clean SDK checkout. The compiler was
`riscv32-esp-elf-gcc 14.2.0` (`esp-14.2.0_20251107`), CMake 3.30.2,
Python 3.12.14, and the official ESP-IDF 5.5 Python constraints.

All five managed components were independently content-hash checked against
the unchanged `firmware/dependencies.lock`: button 4.2.0, cmake_utilities 1.1.1,
esp_codec_dev 1.6.2, esp_lvgl_port 2.9.0, and LVGL 9.5.0. In particular the
codec hash is `4779f31a3ba3f9b38aee1afe6ccb9ba8f6108dc3a6e77d39d7d714e9a215371b`.
No dependency pin was relaxed to obtain the builds.

| Variant | Result | App binary bytes | Static DRAM used / remaining |
| --- | --- | ---: | ---: |
| Input checkpoint baseline | Passed | 2,427,552 | 182,484 / 138,812 |
| Updated source, default options off | Passed | 2,427,600 | 182,484 / 138,812 |
| Experimental render + inactive worker compile options on | Passed | 2,431,680 | 187,820 / 133,476 |

Each image fits the configured app partition with 71% free. Static linker
space is **not runtime free heap**, nor evidence that concurrent TLS, capture,
playout, Wi-Fi and UI fit under load.

## Experimental compile configuration

The non-default verification selected:

```text
CONFIG_BSP_AUDIO_CALLBACK_RENDER=y
CONFIG_POCKET_LIVE_WORKER_COMPILE=y
CONFIG_I2S_ISR_IRAM_SAFE=y
```

The public Live entry remains hard-refused. Optional worker/control/adapter
sources were compiled, but unused functions are linker-dead-stripped. This
does not establish an activated or linked end-to-end worker. The build's
`tcp_transport` compile commands include `LOG_LOCAL_LEVEL=ESP_LOG_NONE` to
avoid the pinned WebSocket transport's authenticated-upgrade logging path.

The linked ELF places `audio_tx_sent`, `audio_rx_overflow`,
`bsp_audio_render_complete`, `vPortEnterCritical`, and `vPortExitCritical` in
`.iram0.text`. The render ledger, span queue/counters, and DMA counter are in
internal DRAM. The compiled render-completion ISR body has no function-call
relocations; its task-side initializer separately calls `memset`. These are
compiler/linker checks, not interrupt-latency or physical cache-off tests.

The first experimental compile exposed one misleading-indentation error in
an earlier adapter snapshot. Splitting the declaration, without relaxing
warnings, corrected it; the final build passed. An earlier local configuration
script failed to enable the dependent worker option; that run is explicitly
not counted as experimental coverage. The final sdkconfig confirms all three
settings above.

## Evidence and limits

The enclosing checkpoint's `verification/` directory retains baseline,
default and experimental build/size logs, binary hashes (not flash images),
source hashes, dependency content hashes, SDK submodule commits, sdkconfigs,
and render object/ELF inspection results. Builds occurred in a separate copy;
generated dependencies, toolchains and firmware images are not project source.

No device was connected, flashed, measured or acoustically tested. DMA EOF
does not prove serializer completion or analog playout, and it must not be
used as a truthful v1 `played_samples` acknowledgment. Synchronized microphone
and render timelines, runtime memory, hardware clock/echo/double-talk, jitter,
and teardown remain release gates. No credentials, paid calls, deployments,
service changes or repository publication were performed.
