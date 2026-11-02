#pragma once

#include <set>
#include <string>
#include <optional>

struct lua_State;

struct LocalEngineContext {
    std::set<std::string> services;
    bool restrict_services = false;
    std::string job_id;
    std::optional<bool> is_studio;
};

// Installs the native API subset from explicit context metadata. Offline mode
// uses fixtures; session mode receives the issued job UUID and this client's
// non-Studio execution mode. This is not a complete Roblox engine.
void install_local_engine(lua_State* L, const LocalEngineContext& context);
