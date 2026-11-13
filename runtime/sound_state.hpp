#pragma once
#include <chrono>
#include <string>
#include "audio_asset.hpp"
struct SoundPlayback {
    std::string asset;
    std::string pending_action;
    std::shared_future<audio_asset::Info> decoding;
    double position=0,duration=0,requested_position=0;
    bool loaded=false,playing=false,attempted=false;
    unsigned loops=0;
    std::chrono::steady_clock::time_point previous{};
};
