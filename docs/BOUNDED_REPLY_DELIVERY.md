# Recorded Usage and full replies

Usage separates recorded Pocket activity from account quota. Main and auxiliary
requests and token categories have their own pages. The scope is all recorded
Pocket runs, with source read time, last observation and coverage shown separately.
Unknown counters remain unknown; an observed zero remains zero. Input and cache
counts can overlap and are never added into a claimed total. Account quota and
reset time remain unknown unless the existing quota source provides them.

On Tasks, choose a completed task. On Tools, choose **Read full reply** and hold
OK. UP/DOWN reads through the stored reply; OK closes the reader. The whole close
gesture is consumed before Chat can start recording. A standby wake gesture only
wakes the screen.

The reader negotiates `reply_delivery.text_pages` version 1. It uses opaque
previous/next cursors bound to the selected target, task and result revision.
Only one 768-byte UTF-8 block occupies the existing Tasks result cache. Local
screens fit the actual CJK font, with fixed byte offsets and no history stack.
Nonprinting control characters appear as whitespace; source bytes and cursor
accounting are preserved. A changed revision requires closing and reopening.
The stored result limit is 65,536 UTF-8 bytes. Existing font coverage is retained;
unsupported glyphs, including some emoji, still use the font's placeholder.

Reply speech negotiates `reply_delivery.speech_segments` version 2. The firmware
creates one pinned job and consumes its indexed chunks in order across up to
twelve 20-second segments, with a 240-second decoded-audio limit. Provider input
limits remain 4,096 UTF-8 bytes and 3 MiB encoded audio. Over-limit replies fail
explicitly rather than being truncated; stored text remains readable. Legacy
speech endpoints and their 30-second limit remain available on older bridges.

The existing serial worker, 8,192-byte HTTP scratch and 4,096-byte playback frame
are reused. Volume is applied before every write, including zero for mute.
Successful EOF keeps playback active until seven fresh TX DMA completions cover
the six-descriptor ring plus a guard. This wait is limited to 250 ms and checks
cancellation every RTOS tick. Failures and cancellation stop local audio before
best-effort remote cleanup. Late generations cannot resume playback, and the
firmware never automatically resubmits a synthesis request.

The frozen protocol is `pocket-long-reply-018`, contract version 2, SHA-256
`017786c30b566d8e4647209dda28e7e0082b16fca3d9810f4f406cf0a9771246`.
TLS, pairing, NVS, selected model and the existing capture/power profile are
preserved. Verification uses synthetic providers and PCM; no microphone, model
or paid-provider canary is part of this delivery.
