#include "build_version.hpp"

// Roblox's deprecated version() returns a string. This host has no Roblox
// engine build metadata, so expose our Cargo package version truthfully.
int runtime_build_version(lua_State* L) {
    if (lua_gettop(L) != 0)
        return luaL_error(L, "version takes no arguments"), 0;
    lua_pushstring(L, ROVE_BUILD_VERSION);
    return 1;
}

void install_runtime_version(lua_State* L) {
    lua_pushcfunction(L, runtime_build_version, "version");
    lua_setglobal(L, "version");
    // Capitalized alias retained for callers that use the legacy spelling.
    lua_pushcfunction(L, runtime_build_version, "Version");
    lua_setglobal(L, "Version");
}
