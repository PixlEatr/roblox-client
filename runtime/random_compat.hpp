#pragma once

struct lua_State;

// Installs the standalone Random.new / NextInteger / NextNumber / Clone API.
// Seeded sequences preserve the user's former runtime implementation and its
// recorded Studio 0.742 controls. Compatibility with Roblox build 0.745 is
// UNVERIFIED; this module is an ordinary local API, not an engine environment.
// Missing/nil seeds use host /dev/urandom entropy, not a fixed fixture seed.
// Leaves the Luau stack at its original height. Install before sandboxing globals.
void install_random_compat(lua_State* L);
