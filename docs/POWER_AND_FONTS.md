# Screen standby, audio power and Chinese text

The screen turns off after 90 seconds without a button event. Voice work,
phone setup, a held button and an unfinished wake gesture inhibit this timer.
The first wake gesture keeps the existing behavior: it wakes the screen and
is consumed through release, so waking with OK cannot start recording.
The existing manual Screen off action uses the same wake guard.

This is display standby. The processor and network worker remain available;
remote tasks are not stopped. While the screen is off and no cached task is
active, the worker waits 15 seconds between alternating target polls instead
of 5 seconds. It also refreshes the polled target's task list in the background.
A known nonterminal or unknown task keeps the 5-second wait, including when
its last observation is offline. List and selected-view requests alternate so
the selected task's backend state continues to advance. Nonselected cached
tasks conservatively keep fast polling until a later observation resolves them.
A newly created remote task can take about
30 seconds plus request time to be discovered while both targets are idle.
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
No battery-life claim follows from these changes: actual battery drain needs
a controlled measurement, and codec power behavior across a physical voice
round trip requires an owner-triggered recording.
