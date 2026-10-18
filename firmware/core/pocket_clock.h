#ifndef POCKET_CLOCK_H
#define POCKET_CLOCK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Reset once at boot. Invalid or repeated synchronization reports do not
 * clear a previously valid boot-session synchronization latch. */
void pocket_clock_reset(void);
void pocket_clock_note_sync(int64_t utc_seconds);
bool pocket_clock_ready(void);

/* Format a validated UTC epoch as DD Mon YYYY HH:MM MYT using fixed UTC+8.
 * No timezone globals, DST, SDK, clock reads, or sync-latch changes occur.
 * UTC input must be in years 2024..9999 and its MYT date must remain <=9999.
 * A valid display needs capacity >=22. On failure write the waiting message,
 * truncated safely if necessary; a non-NULL output is always NUL terminated
 * when capacity >0. NULL or zero-capacity output returns false without writes. */
bool pocket_clock_format(int64_t utc_seconds, bool synchronized,
                         char *out, size_t cap);

#endif
