#pragma once

#include "json.hpp"
#include "source_bindings.hpp"
#include "shared_table_bindings.hpp"
#include <lua.h>
#include <chrono>
#include <cstdint>
#include <functional>
#include <deque>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace world_bindings { struct World; struct Connection; class ValidatedDeltaView; class SessionCustomAssetRegistry; }
class SourceScheduler;
class ScriptExecution;

// Owns one independent Luau global state for each replicated Actor. Actor
// state is deliberately constructed from a data snapshot: no registry refs,
// userdata, callbacks, or scheduler tasks cross the root VM boundary.
class ActorRuntime {
public:
    using Json=nlohmann::json;
    using Clock=std::chrono::steady_clock;
    ActorRuntime(lua_Alloc allocator,void* allocator_data,
                 void (*interrupt)(lua_State*,int),
                 const SourceBindings& bindings,world_bindings::World& root_world,
                 const Json& initial_snapshot,
                 std::shared_ptr<shared_table_bindings::SharedTablePool> shared_table_pool);
    ~ActorRuntime();
    ActorRuntime(const ActorRuntime&)=delete;
    ActorRuntime& operator=(const ActorRuntime&)=delete;

    // Apply the same data-only world update and script catalog used by the
    // root VM, then reconcile and advance every Actor state independently.
    Json update(const world_bindings::ValidatedDeltaView* delta,const Json& script_sources,bool initial_ready,
                Clock::time_point deadline,std::chrono::milliseconds minimum_step_budget,
                std::function<void(Clock::time_point)> set_interrupt_deadline={},
                Json* stage_timings=nullptr);
    void sync_actors(world_bindings::World& root_world,const Json& script_catalog);
    void capture_root_mutations(const world_bindings::World& root_world);
    void root_mutations_cleared();
    void enqueue_message(const std::string& target_actor,const std::string& topic,
                         const Json& arguments);
    Json drain_remote_server_io(world_bindings::World& root_world);
    Json route_remote_server_results(lua_State* root_environment,
                                    world_bindings::World& root_world,
                                    const Json& responses);
    struct ConnectionView {
        std::string owner_actor_id; // empty string identifies the root VM
        uint64_t connection_id=0;
        uint64_t generation=0;
        bool connected=false;
        bool enabled=false;
        bool lua_connection=false;
        bool native=false;
    };
    std::vector<ConnectionView> foreign_connections(const std::string& caller_actor_id,
                                                    const std::string& node_id,
                                                    const std::string& signal_key);
    bool connection_state(const std::string& owner_actor_id,uint64_t connection_id,
                          uint64_t generation,
                          ConnectionView& result) const;
    bool set_connection_enabled(const std::string& owner_actor_id,uint64_t connection_id,
                                uint64_t generation,bool enabled);
    bool disconnect_connection(const std::string& owner_actor_id,uint64_t connection_id,
                               uint64_t generation);
    // Count live Actor Instances, including dormant Actors without an isolated
    // VM. Tween journaling uses this topology count; VM allocation has its own
    // bounded count.
    size_t actor_count() const { return actor_order_.size(); }
    size_t dropped_messages() const { return dropped_messages_; }
    bool has_actor(const std::string& id) const;
    Json diagnostics() const;

private:
    struct ActorVM;
    lua_Alloc allocator_;
    void* allocator_data_;
    void (*interrupt_)(lua_State*,int);
    SourceBindings bindings_;
    std::shared_ptr<shared_table_bindings::SharedTablePool> shared_table_pool_;
    std::shared_ptr<world_bindings::SessionCustomAssetRegistry> custom_asset_registry_;
    bool has_builtin_player_scripts_=false;
    Json builtin_player_scripts_;
    world_bindings::World* root_world_=nullptr;
    Json script_catalog_=Json::object();
    Json root_world_sources_=Json::object();
    std::unordered_map<std::string,std::unique_ptr<ActorVM>> actors_;
    std::vector<std::string> actor_order_;
    // Monotonic within this ActorRuntime so a removed-and-recreated Actor ID
    // never reuses the generation held by an old connection proxy.
    uint64_t next_actor_vm_generation_=1;
    size_t step_actor_cursor_=0;
    struct QueuedMessage {std::string target,topic;Json arguments;size_t age=0;};
    std::deque<QueuedMessage> messages_;
    size_t dropped_messages_=0;
    struct RemoteServerRoute {std::string actor_id,node;uint64_t local_request=0;};
    std::unordered_map<uint64_t,RemoteServerRoute> remote_server_routes_;
    size_t remote_server_io_actor_cursor_=0;

    world_bindings::Connection* find_connection(const std::string& owner_actor_id,
                                                 uint64_t connection_id,
                                                 uint64_t generation) const;

    void instantiate_actor(world_bindings::World& root_world,const std::string& id,
                           std::shared_ptr<const Json> snapshot);
    Json snapshot_from_world(const world_bindings::World& world,const Json& script_catalog) const;
};
