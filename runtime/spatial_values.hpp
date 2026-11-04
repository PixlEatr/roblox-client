#pragma once

#include <lua.h>
#include <array>
#include "json.hpp"

namespace spatial_values {

void install_spatial_values(lua_State* state);
bool is_spatial_value(lua_State* state, int index);
bool capture_spatial_value(lua_State* state, int index, nlohmann::json& value);
bool push_spatial_value(lua_State* state, const nlohmann::json& value);
// Read the native value directly; never consult script-replaceable constructors.
bool read_region_bounds(lua_State* state, int index, std::array<double, 3>& minimum,
                        std::array<double, 3>& maximum);

}  // namespace spatial_values
