#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <array>

namespace world_bindings {

// World-owned Debris queue. Its entries outlive the source thread that queued
// them, and identify tombstoned-capable nodes by stable world ID.
struct ScheduledDebrisItem {
    std::string instance_id;
    std::chrono::steady_clock::time_point deadline;
    uint64_t sequence = 0;
};

struct PlayerMovementModes {
    std::array<std::vector<int>,4> values;
    std::array<bool,4> registered{};
};
struct ClientServiceState {
    std::unordered_map<std::string,PlayerMovementModes> player_movement_modes;
    std::unordered_map<std::string,std::string> player_mice;
    std::vector<ScheduledDebrisItem> debris;
    uint64_t next_debris_sequence = 1;
};

}  // namespace world_bindings
