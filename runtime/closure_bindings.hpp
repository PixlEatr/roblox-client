#pragma once

#include <lua.h>

// Installs the ordinary closure helpers supported by this local Luau VM.
void install_closure_bindings(lua_State* L);
// Origin follows the compiled prototype, independent of mutable environments.
void mark_executor_closure(lua_State* L,int index);
bool is_executor_closure(lua_State* L,int index);
void push_closure_clone(lua_State* L,int index);
