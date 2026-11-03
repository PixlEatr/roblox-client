#pragma once

#include <lua.h>
#include <string>
#include <functional>

namespace movement_bindings {
// Called by the Instance method wrapper after resolving the actual self, so a
// copied method acts on the receiver supplied at call time.
// Resolve camera state only for a validated camera-relative request, before
// later script statements can change that camera. The callback returns JSON.
void enqueue(lua_State* L, const char* kind, const std::string& instance_id,
             const std::function<std::string()>& camera_provider = {});

// Queue a bounded local impulse. The controller resolves ownership, alive and
// anchoring state and requires received assembly mass before changing velocity.
void enqueue_impulse(lua_State* L, const std::string& instance_id, bool at_position = false,
                     const std::string& assembly_root_id = "");

// Preserve assignment order relative to impulses. Throws before the caller
// commits the property if the vector or queue capacity is invalid.
void enqueue_linear_velocity(lua_State* L, const std::string& instance_id, int value_index,
                             const std::string& assembly_root_id);

// Queue a typed local Humanoid state request; the prediction controller decides
// whether the requested physical state is supported by current evidence.
void enqueue_state(lua_State* L, const std::string& instance_id,
                   int state_value, const std::string& state_name);
void enqueue_state_enabled(lua_State* L, const std::string& instance_id,
                           int state_value, const std::string& state_name, bool enabled);

// Coalesce/cancel a Humanoid jump within a scheduler turn. These throw on
// capacity errors before modifying the caller's property state.
void request_jump(lua_State* L, const std::string& instance_id, bool requested, int mutation_cursor = -1);
void tag_latest_mutation_cursor(lua_State* L, int mutation_cursor);
void finish_jump(lua_State* L, const std::string& instance_id, const std::string& settings_json);

// Return and clear bounded movement intents accumulated by local Luau scripts.
// This adapter never changes the World or sends network traffic.
std::string drain(lua_State* L);
}
