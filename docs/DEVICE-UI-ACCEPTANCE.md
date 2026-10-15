# Cat, battery and screen standby: owner acceptance

Status: ESP-IDF build, app-only flash at 0x10000 and independent verification
passed. Boot reached app_main and detected CW2017, with no observed panic or
driver errors. Settings partitions were untouched. The physical acceptance
checks below, live text tasks and end-to-end voice validation remain pending.

The animated UI and idle-audio memory fix are now flashed and boot-verified.
Authenticated Linux status returned HTTP 200 with the backend available over
verified HTTPS; STT remains unavailable. The checks below still require physical
acceptance and are not established by flash/boot or status connectivity alone.

## Display

- Home, Chat and Screen show the animated tabby cat. Compare its moods with the
  CI `ui-snapshots` artifact: idle on Home/Chat, ears up while recording, a
  thought bubble while waiting, a chattering mouth during playback, and asleep
  with drooped ears when Hermes is offline (Chat then shows HERMES OFFLINE) or
  on the Screen page. Holding OK still wakes it into the listening mood.
- Confirm the animation looks smooth and that buttons, recording and playback
  stay responsive while it runs. It redraws only the cat area, about 8 times a
  second, and stops while the screen is off.
- Confirm the colored badge stays on one line and does not overlap the status rows.
- Chat shows what you said on the first line of the bubble, then the reply. A
  progress or error message fills the bubble until a reply arrives.
- Confirm the header, percentage and two footer lines fit the 240 × 320 screen.
- Home shows Wi-Fi, Hermes and Voice states independently. Wi-Fi connected does
  not imply the Hermes bridge or speech service is ready.
- Confirm phone setup still shows the temporary network, password and setup URL.

## Battery

- Compare the percentage with the CW2017 reading if a hardware diagnostic is
  available. The UI reads its SOC, not a voltage-to-percentage approximation.
- Leave the screen awake for at least 30 seconds to observe a later reading.
  Charging/discharging may take longer to change the gauge's percentage.
- A missing gauge, initialization failure, failed read or out-of-range SOC must
  display `N/A`, never an invented `100%`.
- If an initialization failure or first-SOC timeout is transient, the background
  retry should restore a valid percentage after the gauge recovers. Navigation
  and wake gestures must remain responsive during retry attempts.
- Press and release buttons repeatedly during startup. Gestures completed before
  button initialization must not be replayed afterward or start recording. The
  first potentially slow gauge initialization runs before button callbacks.
- If the battery monitor task cannot be allocated, confirm the display remains
  usable and shows `N/A` instead of presenting a permanently stale percentage.
- The existing BSP's 500 mAh cell profile is unchanged. A different battery may
  need board-specific profile work before the gauge can be considered accurate.

## Standby and button safety

- From Home, UP opens Screen; DOWN cycles through Chat, Usage, then Screen.
- Click OK on Screen off. The backlight should go dark. Wi-Fi and an existing
  request remain active; this is screen standby, not a deep-sleep power mode.
- Wake separately with UP, DOWN and OK. Each first press must restore the same
  Screen page without changing a page/agent, recording, entering setup or
  turning the display off again.
- Repeat with a long hold, followed by release. The long event, release and
  delayed click must have no additional action.
- Repeat with rapid taps and overlapping button holds. Release all held buttons
  before the next intentional press; queued wake clicks must remain harmless.
- After releasing the wake key, confirm a fresh UP/DOWN press navigates and a
  fresh OK click on Screen off enters standby again.
- Repeat several sleep/wake cycles, including while a request is already
  running. Waking should not submit audio or cancel that request.
