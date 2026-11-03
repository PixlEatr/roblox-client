#include "closure_bindings.hpp"

#include <lualib.h>

namespace {

// Forward arguments and return all values through Luau's public yieldable-call
// API. The continuation is required even when the wrapped function returns
// synchronously; it also resumes a yielded Luau function without losing values.
int call_continuation(lua_State* L, int status) {
    if (status != LUA_OK) {
        // This path is reached when a wrapped function errors after yielding.
        // Preserve its error object so the caller's pcall sees the failure.
        if (lua_gettop(L) == 0) lua_pushliteral(L, "wrapped function failed after yielding");
        lua_error(L);
        return 0;
    }
    return lua_gettop(L);
}

int call_wrapped_function(lua_State* L) {
    const int argument_count = lua_gettop(L);
    lua_pushvalue(L, lua_upvalueindex(1));
    lua_insert(L, 1);
    return lua_callyieldable(L, argument_count, LUA_MULTRET);
}

int clonefunction(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    if (!lua_isLfunction(L, 1)) {
        luaL_error(L, "clonefunction only supports Luau closures in this runtime");
        return 0;
    }

    lua_clonefunction(L, 1);

    // lua_clonefunction initializes the new function with the current thread's
    // global environment. Preserve the source closure's environment instead.
    lua_getfenv(L, 1);
    lua_setfenv(L, -2);
    return 1;
}

int newcclosure(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    const char* name = luaL_optstring(L, 2, "newcclosure");

    // Wrapping an existing native function would add a redundant trampoline
    // and alter its debug name, so preserve its identity as native executors do.
    if (lua_iscfunction(L, 1)) {
        lua_pushvalue(L, 1);
        return 1;
    }

    lua_pushvalue(L, 1);
    lua_pushcclosurek(L, call_wrapped_function, name, 1, call_continuation);

    // Keep the visible environment consistent with the wrapped closure.
    lua_getfenv(L, 1);
    lua_setfenv(L, -2);
    return 1;
}

int isnewcclosure(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    const bool result = lua_iscfunction(L, 1) && lua_tocfunction(L, 1) == call_wrapped_function;
    lua_pushboolean(L, result);
    return 1;
}

int getupvalue(lua_State* L) {
    luaL_checktype(L, 1, LUA_TFUNCTION);
    const int index = luaL_checkinteger(L, 2);
    if (!lua_getupvalue(L, 1, index))
        lua_pushnil(L);
    return 1;
}

} // namespace

void install_closure_bindings(lua_State* L) {
    lua_pushcfunction(L, clonefunction, "clonefunction");
    lua_setglobal(L, "clonefunction");
    lua_pushcfunction(L, clonefunction, "clonefunc");
    lua_setglobal(L, "clonefunc");
    lua_pushcfunction(L, newcclosure, "newcclosure");
    lua_setglobal(L, "newcclosure");
    lua_pushcfunction(L, isnewcclosure, "isnewcclosure");
    lua_setglobal(L, "isnewcclosure");
    lua_pushcfunction(L, getupvalue, "getupvalue");
    lua_setglobal(L, "getupvalue");
}
