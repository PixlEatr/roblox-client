#pragma once
#include "json.hpp"
#include <chrono>
#include <map>
#include <string>
#include <unordered_set>

namespace world_bindings {
// Local playback timeline only. Joint evaluation and network replication are
// deliberately separate; these records do not represent server acknowledgement.
struct AnimationTrackState {
    std::string animator, animation, reference;
    bool playing=false, active=false, looped=false, loop_override=false;
    bool priority_override=false, start_pending=false;
    int priority=1000;
    double position=0, speed=1, weight=0, target=0, fade_from=0;
    double fade_duration=0, fade_elapsed=0;
    std::chrono::steady_clock::time_point updated=std::chrono::steady_clock::now();
};
struct AnimationState {
    static constexpr size_t track_limit=256;
    static constexpr size_t intent_limit=1024;
    // std::map preserves track references while proxy Animator nodes are added.
    std::map<std::string,AnimationTrackState> tracks;
    std::map<std::string,nlohmann::json> clips;
    std::map<std::string,std::string> errors;
    std::unordered_set<std::string> requested;
    nlohmann::json requests=nlohmann::json::array();
    nlohmann::json intents=nlohmann::json::array();
    uint64_t next_intent_sequence=1;
    size_t clip_bytes=0;
};
}
