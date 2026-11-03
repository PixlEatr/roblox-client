#pragma once
#include <array>
#include <map>
#include <string>
namespace world_bindings {
// Backend identities remain independent of script-writable InputObject fields.
struct InputContact { std::string node; int type=22,key=0; };
struct InputState {
    std::map<std::string,InputContact> contacts;
    std::map<int,std::string> continuous;
    int last_type=22;
    std::array<float,2> location{},pending_delta{},frame_delta{};
};
}
