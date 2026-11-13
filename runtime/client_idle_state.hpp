#pragma once

#include <algorithm>
#include <cstdint>

namespace world_bindings {

// Client-local idle timing, expressed in monotonic milliseconds so it can be
// tested with a fake clock without sleeping. Activity stamps older than the
// latest accepted stamp are ignored; the poll clock itself never moves back.
struct ClientIdleState {
    static constexpr int64_t kFirstIdleMs = 120000;
    static constexpr int64_t kRepeatIdleMs = 30000;
    static constexpr int64_t kDisconnectMs = 1200000;

    int64_t last_activity_ms = 0;
    int64_t last_clock_ms = 0;
    int64_t next_idle_ms = kFirstIdleMs;
    bool initialized = false;
    bool disconnected = false;
    uint64_t notifications = 0;

    static bool counts_as_activity(int type,int phase,bool synthetic) {
        return !synthetic && phase!=3 && type!=9 &&
            ((type>=0&&type<=4)||type==7||type==8);
    }

    void start(int64_t now_ms) {
        last_activity_ms = last_clock_ms = now_ms;
        next_idle_ms = kFirstIdleMs;
        initialized = true;
        disconnected = false;
        notifications = 0;
    }
    bool activity(int64_t stamp_ms) {
        if (!initialized) { start(stamp_ms); return true; }
        if (disconnected || stamp_ms < last_activity_ms || stamp_ms < last_clock_ms) return false;
        last_activity_ms = last_clock_ms = stamp_ms;
        next_idle_ms = kFirstIdleMs;
        return true;
    }
    int64_t clamp_now(int64_t now_ms) {
        if (!initialized) start(now_ms);
        if (now_ms < last_clock_ms) now_ms = last_clock_ms;
        last_clock_ms = now_ms;
        return now_ms;
    }
    bool poll_idle(int64_t now_ms) {
        now_ms = clamp_now(now_ms);
        const int64_t elapsed = now_ms - last_activity_ms;
        if (elapsed < next_idle_ms || disconnected) return false;
        ++notifications;
        // Emit at most one late notification per host tick. This avoids a
        // burst if the process was paused, while preserving the 30s cadence.
        do { next_idle_ms += kRepeatIdleMs; } while (next_idle_ms <= elapsed);
        return true;
    }
    bool poll_disconnect(int64_t now_ms, bool enabled = true) {
        now_ms = clamp_now(now_ms);
        if (enabled && !disconnected && now_ms - last_activity_ms >= kDisconnectMs) {
            disconnected = true;
            return true;
        }
        return false;
    }
    int64_t elapsed_ms(int64_t now_ms) {
        return clamp_now(now_ms) - last_activity_ms;
    }
    double elapsed_seconds(int64_t now_ms) {
        return static_cast<double>(elapsed_ms(now_ms))/1000.0;
    }
};

} // namespace world_bindings
