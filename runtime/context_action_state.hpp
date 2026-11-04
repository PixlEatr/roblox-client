#pragma once
#include <lua.h>
#include <string>
#include <vector>
#include <deque>
#include <memory>
#include <cstdint>
namespace world_bindings {
struct ContextHotkey {std::string kind,text;int value=0;};
struct ContextActionBinding {
    std::string name,owner;int callback=LUA_NOREF,priority=2000,identity=8;
    uint64_t sequence=0;bool active=true,touch_button=false;
    std::vector<ContextHotkey> inputs;
    nlohmann::json metadata=nlohmann::json::object();
};
struct ContextInput {
    std::string node;int type=22,key=0,state=0;bool force=false;
    std::vector<std::shared_ptr<ContextActionBinding>> targets;size_t index=0;
};
struct ContextActionState {
    std::vector<std::shared_ptr<ContextActionBinding>> bindings,retired;
    std::deque<ContextInput> pending;
    uint64_t next_sequence=1;
    int dispatcher=LUA_NOREF;lua_State* thread=nullptr;bool active=false;
    size_t target_count=0;
};
}
