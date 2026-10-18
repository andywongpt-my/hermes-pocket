#include "pocket_clock.h"
#include <stdatomic.h>

static atomic_bool synchronized_clock = ATOMIC_VAR_INIT(false);

#define MIN_UTC_SECONDS INT64_C(1704067200)
#define MAX_UTC_SECONDS INT64_C(253402300799)
#define MYT_OFFSET_SECONDS INT64_C(28800)
#define DAY_SECONDS INT64_C(86400)

static bool valid_utc(int64_t utc_seconds) {
    return utc_seconds >= MIN_UTC_SECONDS && utc_seconds <= MAX_UTC_SECONDS;
}

static bool leap_year(unsigned year) {
    return year % 4u == 0u && (year % 100u != 0u || year % 400u == 0u);
}

static void waiting_text(char *out, size_t cap) {
    static const char waiting[] = "Waiting for time (MYT)";
    if (!out || cap == 0) return;
    size_t i = 0;
    while (waiting[i] != '\0' && i + 1 < cap) {
        out[i] = waiting[i];
        ++i;
    }
    out[i] = '\0';
}

static void two_digits(char *out, unsigned value) {
    out[0] = (char)('0' + value / 10u);
    out[1] = (char)('0' + value % 10u);
}

void pocket_clock_reset(void) {
    atomic_store_explicit(&synchronized_clock, false, memory_order_relaxed);
}

void pocket_clock_note_sync(int64_t utc_seconds) {
    if (valid_utc(utc_seconds))
        atomic_store_explicit(&synchronized_clock, true, memory_order_relaxed);
}

bool pocket_clock_ready(void) {
    return atomic_load_explicit(&synchronized_clock, memory_order_relaxed);
}

bool pocket_clock_format(int64_t utc_seconds, bool synchronized,
                         char *out, size_t cap) {
    static const char months[12][4] = {
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    };
    static const unsigned char month_days[12] = {
        31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
    };
    waiting_text(out, cap);
    if (!out || cap < 22 || !synchronized || !valid_utc(utc_seconds) ||
        utc_seconds > INT64_MAX - MYT_OFFSET_SECONDS)
        return false;
    const int64_t local_seconds = utc_seconds + MYT_OFFSET_SECONDS;
    if (local_seconds > MAX_UTC_SECONDS) return false;

    /* Gregorian calendars repeat every 400 years (146097 days). Skipping
     * complete cycles bounds the remaining year walk to at most 399 steps. */
    uint32_t days = (uint32_t)((local_seconds - MIN_UTC_SECONDS) / DAY_SECONDS);
    unsigned year = 2024u + (days / 146097u) * 400u;
    days %= 146097u;
    for (;;) {
        const unsigned length = leap_year(year) ? 366u : 365u;
        if (days < length) break;
        days -= length;
        ++year;
    }
    unsigned month = 0;
    while (month < 11u) {
        const unsigned length = month_days[month] +
            ((month == 1u && leap_year(year)) ? 1u : 0u);
        if (days < length) break;
        days -= length;
        ++month;
    }
    const unsigned day_seconds = (unsigned)(local_seconds % DAY_SECONDS);
    two_digits(out, (unsigned)days + 1u);
    out[2] = ' ';
    for (unsigned i = 0; i < 3u; ++i) out[3u + i] = months[month][i];
    out[6] = ' ';
    out[7] = (char)('0' + year / 1000u);
    out[8] = (char)('0' + year / 100u % 10u);
    out[9] = (char)('0' + year / 10u % 10u);
    out[10] = (char)('0' + year % 10u);
    out[11] = ' ';
    two_digits(out + 12, day_seconds / 3600u);
    out[14] = ':';
    two_digits(out + 15, day_seconds / 60u % 60u);
    out[17] = ' ';
    out[18] = 'M';
    out[19] = 'Y';
    out[20] = 'T';
    out[21] = '\0';
    return true;
}
