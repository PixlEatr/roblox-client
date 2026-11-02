#include "engine_bindings.hpp"

#include <lua.h>
#include <lualib.h>

#include <cstring>
#include <memory>
#include <unordered_map>
#include <utility>

namespace {
constexpr const char* kInstanceMeta = "OfflineRuntime.Instance";
struct Service {
    std::string name;
    std::string class_name;
    std::string job_id;
    Service* parent = nullptr;
    bool is_run_service = false;
    bool has_is_studio = false;
    bool is_studio = true;
};
struct Runtime {
    LocalEngineContext config;
    Service game{"Game", "DataModel", "", nullptr, false, false, true};
    // Checked in Studio 0.745: Name and ClassName differ here.
    Service settings{"GlobalBasicSettings", "UserSettings", "", nullptr, false, false, true};
    std::unordered_map<std::string, std::unique_ptr<Service>> services;
};
struct Handle { Service* value; };
struct ContextHolder { Runtime* value; };

Runtime* runtime(lua_State* L) {
    auto* holder = static_cast<ContextHolder*>(lua_touserdata(L, lua_upvalueindex(1)));
    return holder ? holder->value : nullptr;
}
Handle* handle(lua_State* L, int index) {
    return static_cast<Handle*>(luaL_checkudata(L, index, kInstanceMeta));
}
void push_service(lua_State* L, Service* value) {
    lua_pushlightuserdata(L, value);
    lua_rawget(L, LUA_REGISTRYINDEX);
    if (lua_isuserdata(L, -1)) return;
    lua_pop(L, 1);
    auto* h = static_cast<Handle*>(lua_newuserdatatagged(L, sizeof(Handle), 0));
    h->value = value;
    luaL_getmetatable(L, kInstanceMeta);
    lua_setmetatable(L, -2);
    lua_pushlightuserdata(L, value);
    lua_pushvalue(L, -2);
    lua_rawset(L, LUA_REGISTRYINDEX);
}
void destroy_context(lua_State*, void* userdata) {
    auto* holder = static_cast<ContextHolder*>(userdata);
    if (holder && holder->value) { delete holder->value; holder->value = nullptr; }
}
int identify(lua_State* L) {
    auto* a = static_cast<Handle*>(lua_touserdata(L, 1));
    auto* b = static_cast<Handle*>(lua_touserdata(L, 2));
    bool a_valid = false, b_valid = false;
    if (lua_getmetatable(L, 1)) {
        luaL_getmetatable(L, kInstanceMeta);
        a_valid = lua_rawequal(L, -1, -2) != 0;
        lua_pop(L, 2);
    }
    if (lua_getmetatable(L, 2)) {
        luaL_getmetatable(L, kInstanceMeta);
        b_valid = lua_rawequal(L, -1, -2) != 0;
        lua_pop(L, 2);
    }
    lua_pushboolean(L, a_valid && b_valid && a && b && a->value == b->value);
    return 1;
}
int tostring_service(lua_State* L) {
    auto* h = handle(L, 1);
    if (!h || !h->value) { luaL_error(L, "invalid local Instance"); return 0; }
    lua_pushlstring(L, h->value->name.data(), h->value->name.size());
    return 1;
}
int is_a(lua_State* L) {
    auto* h = handle(L, 1);
    const char* class_name = luaL_checkstring(L, 2);
    Service* self = h->value;
    bool result = self->class_name == class_name || std::strcmp(class_name, "Instance") == 0
        || std::strcmp(class_name, "Object") == 0;
    if (self->class_name == "DataModel" && std::strcmp(class_name, "ServiceProvider") == 0) result = true;
    if (self->class_name == "UserSettings" &&
        (std::strcmp(class_name, "GenericSettings") == 0 || std::strcmp(class_name, "ServiceProvider") == 0)) result = true;
    if (self->class_name == "Workspace" &&
        (std::strcmp(class_name, "WorldRoot") == 0 || std::strcmp(class_name, "Model") == 0
         || std::strcmp(class_name, "PVInstance") == 0)) result = true;
    lua_pushboolean(L, result);
    return 1;
}
int is_studio(lua_State* L) {
    auto* h = handle(L, 1);
    if (!h || !h->value || !h->value->is_run_service)
        { luaL_error(L, "IsStudio is only available on the local RunService fixture"); return 0; }
    if (!h->value->has_is_studio)
        { luaL_error(L, "RunService:IsStudio has no value in this local fixture"); return 0; }
    lua_pushboolean(L, h->value->is_studio);
    return 1;
}
int is_running(lua_State* L) {
    auto* h = handle(L, 1);
    if (!h || !h->value || !h->value->is_run_service)
        { luaL_error(L, "IsRunning is only available on the local RunService fixture"); return 0; }
    lua_pushboolean(L, true);
    return 1;
}
int get_service(lua_State* L) {
    auto* self = handle(L, 1);
    if (!self || !self->value || self->value->class_name != "DataModel")
        { luaL_error(L, "GetService requires the local DataModel fixture"); return 0; }
    const char* name = luaL_checkstring(L, 2);
    Runtime* ctx = runtime(L);
    auto it = ctx->services.find(name);
    if (it == ctx->services.end()) {
        luaL_error(L, "service '%s' is not enabled in this local fixture", name);
        return 0;
    }
    push_service(L, it->second.get());
    return 1;
}
int find_service(lua_State* L) {
    auto* self = handle(L, 1);
    if (!self || !self->value || self->value->class_name != "DataModel") {
        luaL_error(L, "FindService requires the local DataModel fixture");
        return 0;
    }
    const char* name = luaL_checkstring(L, 2);
    Runtime* ctx = runtime(L);
    auto it = ctx->services.find(name);
    if (it == ctx->services.end()) lua_pushnil(L);
    else push_service(L, it->second.get());
    return 1;
}
int find_first_child(lua_State* L) {
    auto* self = handle(L, 1);
    const char* name = luaL_checkstring(L, 2);
    if (self && self->value && self->value->class_name == "DataModel") {
        Runtime* ctx = runtime(L);
        auto it = ctx->services.find(name);
        if (it != ctx->services.end()) push_service(L, it->second.get());
        else lua_pushnil(L);
        return 1;
    }
    lua_pushnil(L);
    return 1;
}
int get_children(lua_State* L) {
    auto* self = handle(L, 1);
    lua_newtable(L);
    if (self && self->value && self->value->class_name == "DataModel") {
        Runtime* ctx = runtime(L);
        int index = 1;
        for (auto& [name, value] : ctx->services) {
            (void)name;
            push_service(L, value.get());
            lua_rawseti(L, -2, index++);
        }
    }
    return 1;
}
int readonly(lua_State* L) {
    luaL_error(L, "local Instance properties are read-only");
    return 0;
}
void push_bound_method(lua_State* L, lua_CFunction fn, const char* name) {
    lua_pushvalue(L, lua_upvalueindex(1));
    lua_pushcclosurek(L, fn, name, 1, nullptr);
}
int index_service(lua_State* L) {
    auto* h = handle(L, 1);
    if (!h || !h->value) { luaL_error(L, "invalid local Instance"); return 0; }
    const char* key = luaL_checkstring(L, 2);
    Service* self = h->value;
    if (std::strcmp(key, "Name") == 0) lua_pushlstring(L, self->name.data(), self->name.size());
    else if (std::strcmp(key, "ClassName") == 0) lua_pushlstring(L, self->class_name.data(), self->class_name.size());
    else if (std::strcmp(key, "JobId") == 0 && self->class_name == "DataModel") {
        if (self->job_id.empty()) lua_pushnil(L);
        else lua_pushlstring(L, self->job_id.data(), self->job_id.size());
    }
    else if (std::strcmp(key, "Parent") == 0) {
        if (self->parent) push_service(L, self->parent); else lua_pushnil(L);
    } else if (std::strcmp(key, "GetService") == 0) push_bound_method(L, get_service, "GetService");
    else if (std::strcmp(key, "FindService") == 0) push_bound_method(L, find_service, "FindService");
    else if (std::strcmp(key, "FindFirstChild") == 0) push_bound_method(L, find_first_child, "FindFirstChild");
    else if (std::strcmp(key, "GetChildren") == 0) push_bound_method(L, get_children, "GetChildren");
    else if (std::strcmp(key, "IsA") == 0) lua_pushcfunction(L, is_a, "IsA");
    else if (std::strcmp(key, "IsStudio") == 0 && self->is_run_service) lua_pushcfunction(L, is_studio, "IsStudio");
    else if (std::strcmp(key, "IsRunning") == 0 && self->is_run_service) lua_pushcfunction(L, is_running, "IsRunning");
    else lua_pushnil(L);
    return 1;
}
int make_settings(lua_State* L) {
    if (lua_gettop(L) != 0) { luaL_error(L, "UserSettings takes no arguments"); return 0; }
    Runtime* ctx = runtime(L);
    push_service(L, &ctx->settings);
    return 1;
}
}

void install_local_engine(lua_State* L, const LocalEngineContext& config) {
    auto* ctx = new Runtime();
    ctx->config = config;
    ctx->game.job_id = config.job_id;
    const std::set<std::string> builtins = {
        "Workspace", "Players", "RunService", "Lighting", "ReplicatedStorage",
        "ReplicatedFirst", "StarterGui", "StarterPlayer", "SoundService",
        "TweenService", "UserInputService", "HttpService", "CollectionService",
        "ContextActionService", "GuiService", "Teams", "TextService", "CoreGui"
    };
    for (const std::string& name : (builtins)) {
        if (config.restrict_services && !config.services.count(name)) continue;
        auto service = std::make_unique<Service>();
        service->name = name;
        service->class_name = name;
        service->parent = &ctx->game;
        service->is_run_service = name == "RunService";
        if (service->is_run_service && config.is_studio) {
            service->has_is_studio = true;
            service->is_studio = *config.is_studio;
        }
        ctx->services.emplace(name, std::move(service));
    }
    // Keep this alive until lua_close. Each worker gets a fresh state.
    auto* holder = static_cast<ContextHolder*>(lua_newuserdatadtor(L, sizeof(ContextHolder), destroy_context));
    holder->value = ctx;
    luaL_newmetatable(L, kInstanceMeta);
    lua_pushvalue(L, -2);
    lua_pushcclosurek(L, index_service, "__index", 1, nullptr); lua_setfield(L, -2, "__index");
    lua_pushcfunction(L, readonly, "__newindex"); lua_setfield(L, -2, "__newindex");
    lua_pushcfunction(L, tostring_service, "__tostring"); lua_setfield(L, -2, "__tostring");
    lua_pushcfunction(L, identify, "__eq"); lua_setfield(L, -2, "__eq");
    lua_pushliteral(L, "Instance"); lua_setfield(L, -2, "__type");
    lua_pushliteral(L, "offline Instance"); lua_setfield(L, -2, "__metatable");
    lua_pop(L, 1);
    push_service(L, &ctx->game); lua_setglobal(L, "game");
    auto workspace = ctx->services.find("Workspace");
    if (workspace != ctx->services.end()) {
        push_service(L, workspace->second.get());
        lua_setglobal(L, "workspace");
    }
    lua_pushvalue(L, -1);
    lua_pushcclosurek(L, make_settings, "UserSettings", 1, nullptr);
    lua_setglobal(L, "UserSettings");
    lua_pop(L, 1);
}
