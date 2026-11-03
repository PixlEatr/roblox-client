#pragma once

#include <lua.h>
#include "json.hpp"

namespace spatial_values {

void install_spatial_values(lua_State* state);
bool is_spatial_value(lua_State* state, int index);
bool capture_spatial_value(lua_State* state, int index, nlohmann::json& value);
bool push_spatial_value(lua_State* state, const nlohmann::json& value);

}  // namespace spatial_values
