#pragma once

#include <lua.h>
#include "json.hpp"

// Numeric PhysicalProperties constructor subset for owned Luau VMs.
void install_physical_properties(lua_State* L);
bool is_physical_properties(lua_State* L, int index);
bool capture_physical_properties(lua_State* L, int index, nlohmann::json& value);
bool push_physical_properties_json(lua_State* L, const nlohmann::json& value);
