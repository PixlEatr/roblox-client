#pragma once

#include <lua.h>

struct lua_State;

// Installs the ordinary immutable Color3 value API into an owned Luau state.
void install_color3_bindings(lua_State* L);

// Interoperability helpers for sibling native datatypes and world serialization.
// read_color3 returns false for values whose metatable is not this Color3 type.
bool read_color3(lua_State* L, int index, double& r, double& g, double& b);
// Pushes a finite three-component Color3 userdata; returns the Lua result count.
int push_color3(lua_State* L, double r, double g, double b);
