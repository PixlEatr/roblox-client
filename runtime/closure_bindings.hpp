#pragma once

#include <lua.h>

// Installs the ordinary closure helpers supported by this local Luau VM.
void install_closure_bindings(lua_State* L);
