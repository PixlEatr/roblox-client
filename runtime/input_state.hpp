#pragma once
#include <array>
#include <map>
#include <string>
#include <vector>
#include <cstdint>
#include <set>
#include <chrono>
#include "json.hpp"
#include <lua.h>
namespace world_bindings {
// Backend identities remain independent of script-writable InputObject fields.
struct InputContact {
    std::string node; int type=22,key=0; bool synthetic=false;
    std::array<float,3> position{};
};
struct VirtualInputWaiter { lua_State* thread; int reference; uint64_t epoch; };
struct VirtualDeviceInput {
    std::string node;
    std::array<float,3> position{};
    int state=2;
};
struct VirtualInputTape {
    bool recording=false,playing=false,replaying=false;
    unsigned internal_gui=0;
    std::chrono::steady_clock::time_point started{},play_started{};
    nlohmann::json events=nlohmann::json::array(),play_events=nlohmann::json::array();
    size_t bytes=0,cursor=0;
    double duration=0,play_duration=0;
    std::array<float,2> initial_mouse{};
    uint64_t next_epoch=0;
    std::string additional_state,play_additional,last_error,theme;
    nlohmann::json markers=nlohmann::json::array();
    std::map<std::string,std::string> owned_contacts;
    std::set<int> owned_gamepads;
    std::set<std::pair<int,int>> owned_gamepad_buttons;
    std::string owned_gui_pointer;
};
struct InputState {
    std::map<std::string,InputContact> contacts;
    std::map<int,std::string> continuous;
    int last_type=22;
    std::array<float,2> location{},pending_delta{},frame_delta{};
    bool virtual_mouse=false;
    std::string virtual_hover;
    std::map<std::string,std::string> virtual_targets;
    std::vector<VirtualInputWaiter> virtual_waiters;
    uint64_t dispatch_epoch=0;
    std::map<int,std::map<int,VirtualDeviceInput>> gamepads;
    std::map<int,int> gamepad_types;
    std::set<int> navigation_gamepads;
    std::map<int,VirtualDeviceInput> sensors;
    std::array<double,9> sensor_rotation{1,0,0,0,1,0,0,0,1};
    std::set<int> ignored_types;
    VirtualInputTape tape;
};
}
