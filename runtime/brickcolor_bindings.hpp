#pragma once

struct lua_State;

// Installs the documented BrickColor constructors and a deliberately small,
// explicit palette. Unsupported palette entries fail instead of being guessed.
void install_brickcolor_bindings(lua_State* L);
