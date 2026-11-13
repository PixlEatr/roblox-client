// Per-coroutine profiling spans for ordinary Luau engine scripts.
// Durations use steady-clock wall time (including time suspended at yields),
// not CPU time. State is stored in a weak-key registry table and never keeps
// coroutine objects alive. Host diagnostics may inspect the private registry
// record; no additional debug inspection API is exposed to scripts.
namespace {
using ProfilerClock = std::chrono::steady_clock;
constexpr int kProfilerMaxDepth = 128;
constexpr size_t kProfilerMaxLabelBytes = 256;
constexpr int kProfilerMaxLabelsPerThread = 64;
char kProfilerRegistryKey;

void profiler_push_record(lua_State* L) {
    lua_pushlightuserdata(L, &kProfilerRegistryKey);
    lua_rawget(L, LUA_REGISTRYINDEX);
    if (!lua_istable(L, -1)) {
        lua_pop(L, 1);
        lua_newtable(L);
        lua_newtable(L);
        lua_pushliteral(L, "k");
        lua_setfield(L, -2, "__mode");
        lua_setmetatable(L, -2);
        lua_pushlightuserdata(L, &kProfilerRegistryKey);
        lua_pushvalue(L, -2);
        lua_rawset(L, LUA_REGISTRYINDEX);
    }
    const int records = lua_gettop(L);
    lua_pushthread(L);
    const int thread = lua_gettop(L);
    lua_pushvalue(L, thread);
    lua_rawget(L, records);
    if (!lua_istable(L, -1)) {
        lua_pop(L, 1);
        lua_newtable(L);
        lua_newtable(L);
        lua_setfield(L, -2, "stack");
        lua_newtable(L);
        lua_setfield(L, -2, "by_label");
        lua_pushinteger(L, 0);
        lua_setfield(L, -2, "label_count");
        lua_pushinteger(L, 0);
        lua_setfield(L, -2, "completed");
        lua_pushnumber(L, 0);
        lua_setfield(L, -2, "elapsed_seconds");
        lua_pushinteger(L, 0);
        lua_setfield(L, -2, "overflow_completed");
        lua_pushnumber(L, 0);
        lua_setfield(L, -2, "overflow_elapsed_seconds");
        lua_pushvalue(L, thread);
        lua_pushvalue(L, -2);
        lua_rawset(L, records);
    }
    lua_remove(L, thread);
    lua_remove(L, records);
}

int profiler_profilebegin(lua_State* L) {
    size_t length = 0;
    const char* label = luaL_checklstring(L, 1, &length);
    if (length > kProfilerMaxLabelBytes) {
        return luaL_error(L, "profile label exceeds %d bytes", int(kProfilerMaxLabelBytes)), 0;
    }
    profiler_push_record(L);
    const int record = lua_gettop(L);
    lua_getfield(L, record, "stack");
    const int stack = lua_gettop(L);
    const size_t depth = lua_objlen(L, stack);
    if (depth >= kProfilerMaxDepth) {
        return luaL_error(L, "profile nesting exceeds %d spans", kProfilerMaxDepth), 0;
    }
    lua_createtable(L, 2, 0);
    lua_pushlstring(L, label, length);
    lua_rawseti(L, -2, 1);
    const double now = std::chrono::duration<double>(ProfilerClock::now().time_since_epoch()).count();
    lua_pushnumber(L, now);
    lua_rawseti(L, -2, 2);
    lua_rawseti(L, stack, int(depth + 1));
    return 0;
}

int profiler_profileend(lua_State* L) {
    profiler_push_record(L);
    const int record = lua_gettop(L);
    lua_getfield(L, record, "stack");
    const int stack = lua_gettop(L);
    const size_t depth = lua_objlen(L, stack);
    if (depth == 0) {
        return luaL_error(L, "profileend called without a matching profilebegin"), 0;
    }
    lua_rawgeti(L, stack, int(depth));
    const int span = lua_gettop(L);
    lua_rawgeti(L, span, 1);
    const int label = lua_gettop(L);
    lua_rawgeti(L, span, 2);
    const double started = lua_tonumber(L, -1);
    lua_pop(L, 1);
    const double ended = std::chrono::duration<double>(ProfilerClock::now().time_since_epoch()).count();
    const double elapsed = std::max(0.0, ended - started);

    lua_getfield(L, record, "completed");
    const lua_Integer completed = lua_tointeger(L, -1);
    lua_pop(L, 1);
    lua_pushinteger(L, completed < 0x7fffffff ? completed + 1 : 0x7fffffff);
    lua_setfield(L, record, "completed");
    lua_getfield(L, record, "elapsed_seconds");
    const double total = lua_tonumber(L, -1);
    lua_pop(L, 1);
    lua_pushnumber(L, total + elapsed);
    lua_setfield(L, record, "elapsed_seconds");

    lua_getfield(L, record, "by_label");
    const int by_label = lua_gettop(L);
    lua_pushvalue(L, label);
    lua_rawget(L, by_label);
    if (!lua_istable(L, -1)) {
        lua_pop(L, 1);
        lua_getfield(L, record, "label_count");
        const lua_Integer label_count = lua_tointeger(L, -1);
        lua_pop(L, 1);
        if (label_count < kProfilerMaxLabelsPerThread) {
            lua_newtable(L);
            lua_pushinteger(L, 0);
            lua_setfield(L, -2, "completed");
            lua_pushnumber(L, 0);
            lua_setfield(L, -2, "elapsed_seconds");
            lua_pushvalue(L, label);
            lua_pushvalue(L, -2);
            lua_rawset(L, by_label);
            lua_pushinteger(L, label_count + 1);
            lua_setfield(L, record, "label_count");
        }
    }
    // A thread may use arbitrarily many labels. Retain only the first bounded
    // set; keep accurate all-label totals in the constant-size overflow bucket.
    lua_pushvalue(L, label);
    lua_rawget(L, by_label);
    if (lua_istable(L, -1)) {
        const int stats = lua_gettop(L);
        lua_getfield(L, stats, "completed");
        const lua_Integer label_completed = lua_tointeger(L, -1);
        lua_pop(L, 1);
        lua_pushinteger(L, label_completed < 0x7fffffff ? label_completed + 1 : 0x7fffffff);
        lua_setfield(L, stats, "completed");
        lua_getfield(L, stats, "elapsed_seconds");
        const double label_elapsed = lua_tonumber(L, -1);
        lua_pop(L, 1);
        lua_pushnumber(L, label_elapsed + elapsed);
        lua_setfield(L, stats, "elapsed_seconds");
    } else {
        lua_pop(L, 1);
        lua_getfield(L, record, "overflow_completed");
        const lua_Integer overflow_completed = lua_tointeger(L, -1);
        lua_pop(L, 1);
        lua_pushinteger(L, overflow_completed < 0x7fffffff ? overflow_completed + 1 : 0x7fffffff);
        lua_setfield(L, record, "overflow_completed");
        lua_getfield(L, record, "overflow_elapsed_seconds");
        const double overflow_elapsed = lua_tonumber(L, -1);
        lua_pop(L, 1);
        lua_pushnumber(L, overflow_elapsed + elapsed);
        lua_setfield(L, record, "overflow_elapsed_seconds");
    }

    lua_pushnil(L);
    lua_rawseti(L, stack, int(depth));
    return 0;
}

void install_profiler_compat(lua_State* L, int debug_table) {
    debug_table = lua_absindex(L, debug_table);
    lua_pushcfunction(L, profiler_profilebegin, "debug.profilebegin");
    lua_setfield(L, debug_table, "profilebegin");
    lua_pushcfunction(L, profiler_profileend, "debug.profileend");
    lua_setfield(L, debug_table, "profileend");
}
}
