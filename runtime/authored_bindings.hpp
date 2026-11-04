#pragma once
#include <lua.h>
#include <array>
#include "json.hpp"

// Ordinary value types/helpers for user-authored source; never installed in the
// separate server-issued bytecode execution path.
void install_authored_bindings(lua_State* L);
bool capture_authored_value(lua_State* L,int index,nlohmann::json& value);
bool push_authored_value(lua_State* L,const nlohmann::json& value);
bool is_authored_value(lua_State* L,int index);
bool read_authored_ray(lua_State* L,int index,std::array<double,3>& origin,
                       std::array<double,3>& direction);
// Legacy Font enum conversion for synchronized text-object properties.
nlohmann::json authored_font_from_enum(int value);
int authored_font_to_enum(const nlohmann::json& value);
