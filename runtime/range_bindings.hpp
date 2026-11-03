#pragma once

struct lua_State;

// Installs the standalone NumberRange and Rect datatypes into an owned Luau VM.
void install_range_bindings(lua_State* L);
