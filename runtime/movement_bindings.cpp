#include "movement_bindings.hpp"

#include "json.hpp"
#include <lualib.h>

#include <cmath>
#include <algorithm>
#include <cstring>
#include <new>
#include <stdexcept>
#include <string>
#include <vector>
#include <unordered_map>

namespace movement_bindings {
namespace {
constexpr const char* kInstanceMeta = "WorldSnapshot.Instance";
constexpr size_t kMaxQueued = 256;
constexpr size_t kMaxInstanceId = 1024;
char kContextKey;

struct Intent {
    uint64_t sequence;
    std::string kind;
    std::string instance_id;
    double vector[3];
    bool relative_to_camera;
    double point[3]{};
    bool has_point = false;
    int state_value = -1;
    std::string state_name;
    bool active = true;
    nlohmann::json jump_settings = nlohmann::json::object();
    nlohmann::json camera = nlohmann::json::object();
    std::string assembly_root_id;
    int mutation_cursor = -1;
};
struct Context {
    uint64_t next_sequence = 1;
    std::vector<Intent> intents;
    std::unordered_map<std::string, size_t> pending_jumps;
};

void destroy_context(lua_State*, void* storage) {
    static_cast<Context*>(storage)->~Context();
}

Context* context(lua_State* L, bool create) {
    lua_pushlightuserdata(L, &kContextKey);
    lua_rawget(L, LUA_REGISTRYINDEX);
    if (lua_isuserdata(L, -1)) {
        auto* value = static_cast<Context*>(lua_touserdata(L, -1));
        lua_pop(L, 1);
        return value;
    }
    lua_pop(L, 1);
    if (!create) return nullptr;

    auto* value = static_cast<Context*>(lua_newuserdatadtor(L, sizeof(Context), destroy_context));
    new (value) Context();
    lua_pushlightuserdata(L, &kContextKey);
    lua_pushvalue(L, -2);
    lua_rawset(L, LUA_REGISTRYINDEX);
    lua_pop(L, 1);
    return value;
}

void enqueue_impl(lua_State* L, const char* kind, const std::string& id,
                  const std::function<std::string()>& camera_provider) {
    luaL_checkudata(L, 1, kInstanceMeta);
    if (!kind || id.empty() || id.size() > kMaxInstanceId ||
        (std::strcmp(kind, "Move") != 0 && std::strcmp(kind, "MoveTo") != 0)) {
        luaL_error(L, "movement method has an invalid receiver or method");
        return;
    }
    if (!lua_isvector(L, 2)) {
        luaL_error(L, "%s expects a Vector3", kind);
        return;
    }
    const LUA_VECTOR_TYPE* vector = lua_tovector(L, 2);
    double components[3] = {vector[0], vector[1], vector[2]};
    for (double component : components) {
        if (!std::isfinite(component) || std::abs(component) > 1.0e6) {
            luaL_error(L, "movement vector components must be finite and bounded");
            return;
        }
    }
    bool relative = false;
    if (std::strcmp(kind, "Move") == 0) {
        if (lua_gettop(L) >= 3) {
            if (!lua_isboolean(L, 3)) {
                luaL_error(L, "Move camera-relative argument must be a boolean");
                return;
            }
            relative = lua_toboolean(L, 3) != 0;
        } else {
            // Humanoid:Move defaults relativeToCamera to false.
            relative = false;
        }
        if (lua_gettop(L) > 3) {
            luaL_error(L, "Move accepts at most a direction and camera-relative flag");
            return;
        }
    } else if (lua_gettop(L) != 2) {
        luaL_error(L, "MoveTo currently accepts a single target Vector3");
        return;
    }

    try {
        Context* state = context(L, true);
        if (state->intents.size() >= kMaxQueued) {
            luaL_error(L, "local movement intent queue is full");
            return;
        }
        Intent intent{state->next_sequence, kind, id,
                      {components[0], components[1], components[2]}, relative};
        if(relative && camera_provider)
            intent.camera=nlohmann::json::parse(camera_provider());
        state->intents.push_back(std::move(intent));
        ++state->next_sequence;
    } catch (const std::exception& error) {
        luaL_error(L, "could not queue movement intent: %s", error.what());
    }
}
} // namespace

void enqueue(lua_State* L, const char* kind, const std::string& instance_id,
             const std::function<std::string()>& camera_provider) {
    enqueue_impl(L, kind, instance_id, camera_provider);
}

void enqueue_linear_velocity(lua_State* L, const std::string& id, int index, const std::string& root_id) {
    if (id.empty() || id.size() > kMaxInstanceId || !lua_isvector(L, index))
        throw std::runtime_error("AssemblyLinearVelocity requires a BasePart and Vector3");
    const LUA_VECTOR_TYPE* vector = lua_tovector(L, index);
    for (int i=0; i<3; ++i)
        if (!std::isfinite(vector[i]) || std::abs(vector[i]) > 1.0e6)
            throw std::runtime_error("velocity components must be finite and bounded");
    Context* state = context(L, true);
    if (state->intents.size() >= kMaxQueued)
        throw std::runtime_error("local movement intent queue is full");
    Intent intent{state->next_sequence, "SetLinearVelocity", id,
                  {vector[0], vector[1], vector[2]}, false};
    intent.assembly_root_id=root_id;
    state->intents.push_back(std::move(intent));
    ++state->next_sequence;
}

void enqueue_impulse(lua_State* L, const std::string& id, bool at_position, const std::string& root_id) {
    luaL_checkudata(L, 1, kInstanceMeta);
    if (id.empty() || id.size() > kMaxInstanceId) {
        luaL_error(L, "ApplyImpulse has an invalid receiver");
        return;
    }
    if (lua_gettop(L) != (at_position ? 3 : 2) || !lua_isvector(L, 2) ||
        (at_position && !lua_isvector(L, 3))) {
        luaL_error(L, at_position ? "ApplyImpulseAtPosition expects impulse and position Vector3 values"
                                  : "ApplyImpulse expects a single Vector3 impulse");
        return;
    }
    const LUA_VECTOR_TYPE* vector = lua_tovector(L, 2);
    double components[3] = {vector[0], vector[1], vector[2]};
    for (double component : components) {
        if (!std::isfinite(component) || std::abs(component) > 1.0e6) {
            luaL_error(L, "impulse components must be finite and bounded");
            return;
        }
    }
    double point[3]{};
    if (at_position) {
        const LUA_VECTOR_TYPE* position = lua_tovector(L, 3);
        for (int i = 0; i < 3; ++i) {
            point[i] = position[i];
            if (!std::isfinite(point[i]) || std::abs(point[i]) > 1.0e6) {
                luaL_error(L, "impulse position components must be finite and bounded");
                return;
            }
        }
    }
    try {
        Context* state = context(L, true);
        if (state->intents.size() >= kMaxQueued) {
            luaL_error(L, "local movement intent queue is full");
            return;
        }
        Intent intent{state->next_sequence++, "Impulse", id,
                      {components[0], components[1], components[2]}, false};
        intent.assembly_root_id=root_id;
        if (at_position) { intent.has_point = true; std::copy(point, point + 3, intent.point); }
        state->intents.push_back(std::move(intent));
    } catch (const std::exception& error) {
        luaL_error(L, "could not queue impulse intent: %s", error.what());
    }
}

void enqueue_state(lua_State* L, const std::string& id, int value, const std::string& name) {
    luaL_checkudata(L, 1, kInstanceMeta);
    if (id.empty() || id.size() > kMaxInstanceId || value < 0 || value > 18 ||
        name.empty() || name.size() > 64) {
        luaL_error(L, "ChangeState has an invalid state or receiver");
        return;
    }
    try {
        Context* state = context(L, true);
        if (state->intents.size() >= kMaxQueued) {
            luaL_error(L, "local movement intent queue is full");
            return;
        }
        Intent intent{state->next_sequence++, "ChangeState", id, {0, 0, 0}, false};
        intent.state_value = value;
        intent.state_name = name;
        state->intents.push_back(std::move(intent));
    } catch (const std::exception& error) {
        luaL_error(L, "could not queue state intent: %s", error.what());
    }
}

void enqueue_state_enabled(lua_State* L, const std::string& id, int value,
                           const std::string& name, bool enabled) {
    luaL_checkudata(L, 1, kInstanceMeta);
    if (id.empty() || id.size() > kMaxInstanceId || value < 0 || value > 18 ||
        name.empty() || name.size() > 64) {
        luaL_error(L, "SetStateEnabled has an invalid state or receiver");
        return;
    }
    try {
        Context* state = context(L, true);
        if (state->intents.size() >= kMaxQueued) {
            luaL_error(L, "local movement intent queue is full");
            return;
        }
        Intent intent{state->next_sequence++, "SetStateEnabled", id, {0, 0, 0}, false};
        intent.state_value = value;
        intent.state_name = name;
        intent.active = true;
        intent.jump_settings = nlohmann::json{{"enabled", enabled}};
        state->intents.push_back(std::move(intent));
    } catch (const std::exception& error) {
        luaL_error(L, "could not queue state-enabled intent: %s", error.what());
    }
}

void tag_latest_mutation_cursor(lua_State* L,int cursor) {
    Context* state=context(L,false);
    if(state&&!state->intents.empty())state->intents.back().mutation_cursor=cursor;
}

void request_jump(lua_State* L, const std::string& id, bool requested, int cursor) {
    if(id.empty()||id.size()>kMaxInstanceId)throw std::runtime_error("invalid jump receiver");
    Context* state=context(L,requested);
    if(!state)return;
    auto found=state->pending_jumps.find(id);
    if(found!=state->pending_jumps.end()) {
        state->intents.at(found->second).active=requested;
        return;
    }
    if(!requested)return;
    if(state->intents.size()>=kMaxQueued)throw std::runtime_error("local movement intent queue is full");
    size_t index=state->intents.size();
    state->intents.push_back(Intent{state->next_sequence++,"Jump",id,{0,0,0},false});
    state->intents.back().mutation_cursor=cursor;
    state->pending_jumps.emplace(id,index);
}

void finish_jump(lua_State* L,const std::string& id,const std::string& settings_json) {
    Context* state=context(L,false);if(!state)return;
    auto found=state->pending_jumps.find(id);if(found==state->pending_jumps.end())return;
    state->intents.at(found->second).jump_settings=nlohmann::json::parse(settings_json);
    state->pending_jumps.erase(found);
}

std::string drain(lua_State* L) {
    Context* state = context(L, false);
    nlohmann::json output = nlohmann::json::array();
    if (!state) return output.dump();
    for (const auto& intent : state->intents) {
        if(!intent.active)continue;
        const auto start=output.size();
        // Every branch carries the same per-response mutation prefix. This
        // preserves script order without exporting a second topology model.
        auto tag=[&](){if(output.size()>start&&intent.mutation_cursor>=0)
            output.back()["mutation_cursor"]=intent.mutation_cursor;};
        if(intent.kind=="ChangeState") {
            output.push_back({{"sequence",intent.sequence},{"kind",intent.kind},
                              {"instance_id",intent.instance_id},
                              {"state",intent.state_name},{"state_value",intent.state_value}});
            tag();
            continue;
        }
        if(intent.kind=="SetStateEnabled") {
            output.push_back({{"sequence",intent.sequence},{"kind",intent.kind},
                              {"instance_id",intent.instance_id},
                              {"state",intent.state_name},{"state_value",intent.state_value},
                              {"enabled",intent.jump_settings.value("enabled", true)}});
            tag();
            continue;
        }
        if(intent.kind=="Jump") {
            output.push_back({{"sequence",intent.sequence},{"kind",intent.kind},
                              {"instance_id",intent.instance_id},{"jump_settings",intent.jump_settings}});
            tag();
            continue;
        }
        output.push_back({{"sequence", intent.sequence},
                          {"kind", intent.kind},
                          {"instance_id", intent.instance_id},
                          {"vector", {intent.vector[0], intent.vector[1], intent.vector[2]}},
                          {"relative_to_camera", intent.relative_to_camera}});
        if (intent.has_point)
            output.back()["position"] = {intent.point[0], intent.point[1], intent.point[2]};
        if(intent.kind=="Impulse"||intent.kind=="SetLinearVelocity")
            output.back()["assembly_root_id"]=intent.assembly_root_id;
        if(intent.relative_to_camera && !intent.camera.empty())
            output.back()["camera"]=intent.camera;
        tag();
    }
    state->intents.clear();
    state->pending_jumps.clear();
    return output.dump();
}
} // namespace movement_bindings
