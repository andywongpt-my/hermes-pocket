# Screen standby, audio power and Chinese text

The screen turns off after 90 seconds without a button event. Voice work,
phone setup, a held button and an unfinished wake gesture inhibit this timer.
The first wake gesture keeps the existing behavior: it wakes the screen and
is consumed through release, so waking with OK cannot start recording.
The existing manual Screen off action uses the same wake guard.

This is display standby. The processor and network worker remain available;
remote tasks are not stopped. The backlight goes dark, the existing LVGL task
and tick stop under the display mutex, and ST7789 enters Sleep In. The exact
installed driver waits 100 ms after Sleep In and Sleep Out. Wake restores the
panel and timers, renders the current view and drains its final SPI transfer
before restoring the backlight. The first gesture is still consumed. A failed
wake stays dark and can be retried by another gesture. The BSP rejects task
suspension until that exact LVGL task has rendered, and never suspends itself.

While the screen is off and no cached task is active, the worker retains its
15-second wait, but checks the selected target at most every 30 seconds and the
inactive target at most every 120 seconds. Idle Usage checks are gated to five
minutes; background task-list discovery to two minutes. A pending selected-view
request still finishes before another list refresh. Skipped status checks keep
their previous timestamp. Wake prioritizes the selected target on the next
worker iteration; an already running request retains its bounded timeout.
Home qualifies observations at least 45 seconds old as `STATUS STALE`, with
cached Hermes/Voice rows, rather than treating old success as current readiness.
A known nonterminal or unknown task keeps the 5-second wait, including when
its last observation is offline. List and selected-view requests alternate so
the selected task's backend state continues to advance. Nonselected cached
tasks conservatively keep fast polling until a later observation resolves them.
A newly created remote task can take about two minutes on the selected target
or up to four minutes on an inactive target, plus request and scheduler time,
to be discovered while both targets are idle and dark. The longer inactive
bound includes alternating a list refresh with its queued selected-view GET.
Queued voice commands wake the worker immediately; an already running request
retains its existing bounded timeout. Each idle cycle closes its HTTP client.
The defaults are named constants in `firmware/core/pocket_power.h`.

After recording has finished and its queue has been released, the codec and
I2S are put to sleep before transcription/model/speech waiting. They wake for
recording and when playback chunks are ready, then sleep on normal completion,
cancel and failure paths. A recorder that fails to stop retains ownership and
the existing restart-required fault; its audio resources are not closed from
another task. Sleep/wake failures produce numeric diagnostics. Microphone
capture is still initiated only by the owner's talk gesture. The capture
queue, scratch buffer and Wi-Fi power-save/restore strategy are unchanged.

The original 16 px CJK font remains primary. A constant 2 bpp Pocket CJK
fallback adds missing characters from GB2312 levels 1/2, Big5 common level 1
and explicit punctuation. See `firmware/fonts/README.md` for exact coverage,
upstream OFL licensing and reproducible generation. Latin text and existing
glyph metrics retain their original font. Voice state and display byte limits
now stop at valid UTF-8 boundaries rather than splitting a codepoint. Text
caps are unchanged; the small display still clips or ellipsizes long replies.

CPU frequency, PM/tickless configuration, TLS trust and hostname validation,
authentication, server budgets, pairing/NVS and partition layout are unchanged.
ADC-button polling remains at 5 ms, and CPU power management is not enabled.
Those need a separate wake-safe design before processor sleep is introduced.
No battery-life claim follows from these changes: actual battery drain needs
a controlled measurement, and codec power behavior across a physical voice
round trip requires an owner-triggered recording.

For the minimum physical comparison, unplug USB, leave Home untouched until
the screen sleeps, and record starting/ending SOC and elapsed time over twenty
minutes without voice. Repeat under the same network and battery conditions.
Battery-side current logging across awake and dark periods can separate true
load from a gauge/profile issue; USB charging current cannot isolate that load.
Before delivery acceptance, verify repeated UP/DOWN/OK wakes, a failed/retried
wake if diagnostics permit, MYT clock catch-up, authenticated Funnel polling,
and preserved active-task/voice cancellation. These remain physical checks;
native tests and build success do not establish runtime or endurance savings.
