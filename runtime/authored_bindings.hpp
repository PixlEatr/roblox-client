#pragma once
#include <lua.h>
#include "json.hpp"

// Ordinary value types/helpers for user-authored source; never installed in the
// separate server-issued bytecode execution path.
void install_authored_bindings(lua_State* L);
bool capture_authored_value(lua_State* L,int index,nlohmann::json& value);
bool push_authored_value(lua_State* L,const nlohmann::json& value);
bool is_authored_value(lua_State* L,int index);
