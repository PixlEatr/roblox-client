#pragma once

struct lua_State;

// Installs standalone UDim and UDim2 value constructors into a Luau state.
// Conversion into GuiObject properties is handled by the world binding layer.
void install_udim_bindings(lua_State* L);
