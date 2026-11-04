#pragma once
#include <lua.h>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace world_bindings {
struct RenderStepBinding {
    std::string name,owner;
    int priority=0,callback=LUA_NOREF,identity=8;
    uint64_t sequence=0;
    bool active=true;
};
struct RenderStepState {
    std::vector<std::shared_ptr<RenderStepBinding>> bindings,group;
    size_t group_index=0;
    int priority=0,dispatcher=LUA_NOREF;
    bool has_priority=false,active=false;
    double dt=0;
    uint64_t next_sequence=1;
    lua_State* thread=nullptr;
};
}
