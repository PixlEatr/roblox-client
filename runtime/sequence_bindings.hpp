#pragma once

struct lua_State;

// Installs immutable NumberSequence and NumberSequenceKeypoint values.
void install_sequence_bindings(lua_State* L);
