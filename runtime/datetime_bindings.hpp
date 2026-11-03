#pragma once

struct lua_State;

// Installs a bounded UTC DateTime subset for the owned Luau VM.
void install_datetime_bindings(lua_State* L);
