#pragma once
#include <lua.h>
#include "protected_script_string.hpp"

// Transfer immutable storage by value. The pair's real copy operation checks
// handle identity; the loader consumes the bytes owned by that checked copy.
inline int load_protected_script(lua_State* state,const char* name,ProtectedScriptPayload payload) {
    const auto& bytes=payload.bytecode();
    return luau_load(state,name,bytes.data(),bytes.size(),0);
}
