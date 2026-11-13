#pragma once

struct lua_State;

// Installs bounded DateTime construction, conversion, and Rust-backed formatting.
void install_datetime_bindings(lua_State* L);
