#pragma once
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <thread>

// The local DataModel/VM is confined to its creating OS thread. Native
// 10482a4e4 compares the DataModel owner with the current OS thread; the
// 1014adf74 load path records bit 0x10 on mismatch. Check before touching
// the VM here so an invalid caller cannot race an existing Lua operation.
class ScriptThreadIntegrity {
public:
    struct Snapshot {uint64_t checks; uint64_t failures;};
    void check_load_thread() {
        const bool matches = std::this_thread::get_id() == owner_;
        {
            std::lock_guard<std::mutex> guard(mutex_);
            ++checks_;
            if(!matches)++failures_;
        }
        if(!matches)throw std::runtime_error("script load attempted outside the DataModel owner thread");
    }
    Snapshot snapshot() const {
        std::lock_guard<std::mutex> guard(mutex_);
        return {checks_,failures_};
    }
private:
    const std::thread::id owner_ = std::this_thread::get_id();
    mutable std::mutex mutex_;
    uint64_t checks_=0,failures_=0;
};
