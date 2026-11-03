#pragma once
#include "json.hpp"
#include <chrono>
#include <string>

namespace world_bindings {
struct TweenPlaybackState {
    std::string target;
    nlohmann::json goals, starts;
    double duration=1, delay=0, elapsed=0;
    int style=3, direction=1, repeats=0, state=0;
    bool reverses=false;
    std::chrono::steady_clock::time_point updated{};
};
}
