#pragma once

#include "json.hpp"

struct lua_State;

namespace legacy_value_bindings {
void install(lua_State* L);
bool is_value(lua_State* L, int index);
bool capture_value(lua_State* L, int index, nlohmann::json& value);
bool push_value(lua_State* L, const nlohmann::json& value);
} // namespace legacy_value_bindings
