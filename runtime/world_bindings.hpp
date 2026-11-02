#pragma once

#include <lua.h>
#include "json.hpp"

#include <string>
#include <unordered_map>
#include <vector>

namespace world_bindings {
using Json = nlohmann::json;

struct Node {
    std::string id;
    std::string class_name;
    std::string name;
    std::string parent;
    Json properties = Json::object();
    Json origins = Json::object();
};

struct World {
    std::unordered_map<std::string, Node> nodes;
    std::vector<std::string> order;
    std::unordered_map<std::string, std::string> services;
    std::string local_player;
    std::string job_id;
    std::string game_id;
    Json class_parents = Json::object();
    Json defaults = Json::object();
    Json default_origins = Json::object();
    double sequence = 0;
    std::unordered_map<std::string, Json> local_properties;
    Json mutations = Json::array();
    size_t mutation_bytes = 0;
    std::string output;
    bool output_truncated = false;
};

// Parses a bounded version-1 server snapshot. Missing properties stay unknown.
World parse_snapshot(const std::string& json_text);

// Installs a local read/write simulation over the snapshot in a fresh Luau VM.
// Mutations are retained in World::mutations and are never sent to a server.
void install(lua_State* L, World& world);
Json capture_value(lua_State* L, World& world, int index);
}
