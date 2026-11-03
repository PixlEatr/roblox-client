#pragma once

#include <lua.h>

namespace json_bindings {
// Pushes an ordinary HttpService JSON method for the requested method name.
// The resulting closure expects the HttpService instance at stack index 1.
bool push_method(lua_State* L, const char* name);
}
