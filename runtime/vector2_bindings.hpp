#pragma once

struct lua_State;

// Installs the immutable Vector2 datatype into an owned Luau state.
void install_vector2_bindings(lua_State* L);
bool read_vector2(lua_State* L, int index, double& x, double& y);
int push_vector2(lua_State* L, double x, double y);

// Engine-produced vectors preserve IEEE non-finite values without changing user constructor validation.
int push_engine_vector2(lua_State* L, double x, double y);
