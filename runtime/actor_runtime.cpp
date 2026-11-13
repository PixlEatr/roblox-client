#include "actor_runtime.hpp"

#include "authored_bindings.hpp"
#include "closure_bindings.hpp"
#include "physical_properties.hpp"
#include "protected_error_trace.hpp"
#include "random_compat.hpp"
#include "script_execution.hpp"
#include "source_scheduler.hpp"
#include "world_bindings.hpp"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <lualib.h>
#include <stdexcept>
#include <utility>

namespace {
using Json=nlohmann::json;
constexpr size_t kMaximumActorVMs=64;
void remove_global(lua_State* L,const char* name) {
    lua_pushnil(L);lua_setglobal(L,name);
}

bool is_actor(const world_bindings::Node& node) {
    return node.class_name=="Actor";
}
std::string actor_local_prefix(const std::string& id) {
    static const char* hex="0123456789abcdef";
    std::string prefix="local:actor:";
    for(unsigned char byte:id){prefix.push_back(hex[byte>>4]);prefix.push_back(hex[byte&15]);}
    prefix.push_back(':');
    return prefix;
}
uint64_t actor_request_id(const Json& value) {
    if(!value.is_number_integer())throw std::runtime_error("RemoteFunction request ID must be an integer");
    if(value.is_number_unsigned())return value.get<uint64_t>();
    const int64_t signed_value=value.get<int64_t>();
    if(signed_value<0)throw std::runtime_error("RemoteFunction request ID must be nonnegative");
    return static_cast<uint64_t>(signed_value);
}
size_t actor_json_row_bytes(const Json& rows) {
    size_t total=0;
    if(!rows.is_array())return total;
    for(const auto& row:rows) {
        const size_t size=row.dump().size();
        if(size>std::numeric_limits<size_t>::max()-total)
            throw std::runtime_error("Actor host I/O byte count overflow");
        total+=size;
    }
    return total;
}
}

struct ActorRuntime::ActorVM {
    std::string id;
    std::shared_ptr<const Json> snapshot_owner;
    std::unique_ptr<world_bindings::World> world;
    lua_State* main=nullptr;
    lua_State* environment=nullptr;
    std::unique_ptr<SourceScheduler> scheduler;
    std::unique_ptr<ScriptExecution> scripts;
    bool initial_ready=true;
    size_t root_mutation_cursor=0;
    Json pending_root_mutations=Json::array();

    ActorVM(ActorRuntime* runtime_owner,const std::string& actor_id,lua_Alloc allocator,void* allocator_data,
            void (*interrupt)(lua_State*,int),const SourceBindings& bindings,
            const std::shared_ptr<shared_table_bindings::SharedTablePool>& shared_table_pool,
            std::shared_ptr<const Json> snapshot,uint64_t generation)
        :id(actor_id),snapshot_owner(std::move(snapshot)),world(std::make_unique<world_bindings::World>(world_bindings::parse_snapshot(snapshot_owner))) {
        if(!snapshot_owner)throw std::runtime_error("Actor VM requires a snapshot owner");
        // Attributes stay in Rust. Fork each node's copy-on-write store instead
        // of retaining another JSON copy in the Actor construction snapshot.
        for(auto& [node_id,node]:world->nodes) {
            const auto source=runtime_owner->root_world_->nodes.find(node_id);
            if(source!=runtime_owner->root_world_->nodes.end()&&source->second.class_name==node.class_name)
                node.attributes=source->second.attributes;
        }
        const Json& snapshot_view=*snapshot_owner;
        world->persistent=true;
        world->actor_runtime=runtime_owner;
        world->actor_id=id;
        world->connection_generation=generation;
        world->custom_asset_registry=runtime_owner->custom_asset_registry_;
        if(snapshot_view.contains("tween_state"))
            world_bindings::restore_tweens(*world,snapshot_view.at("tween_state"));
        world->local_id_prefix=actor_local_prefix(id);
        world->shared_table_pool=shared_table_pool;
        main=lua_newstate(allocator,allocator_data);
        if(!main)throw std::runtime_error("cannot allocate isolated Actor Luau VM");
        try {
            auto* callbacks=lua_callbacks(main);
            callbacks->userdata=allocator_data;
            callbacks->interrupt=interrupt;
            install_protected_error_trace(main);
            luaL_openlibs(main);
            for(const char* name:{"require","loadstring","dofile","loadfile","io","debug"})remove_global(main,name);
            lua_getglobal(main,"os");
            if(lua_istable(main,-1)){lua_pushnil(main);lua_setfield(main,-2,"exit");}
            lua_pop(main,1);
            install_random_compat(main);
            install_authored_bindings(main);
            world_bindings::install(main,*world);
            world->mutations=Json::array();world->mutation_bytes=0;
            luaL_sandbox(main);
            environment=lua_newthread(main);
            world->actor_environment=environment;
            luaL_sandboxthread(environment);
            lua_setsafeenv(environment,LUA_GLOBALSINDEX,false);
            install_source_bindings(environment,&bindings,world->script_thread_integrity.get(),world->protected_scripts.get());
            shared_table_bindings::install(environment,shared_table_pool,world.get());
            auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
            scheduler=std::make_unique<SourceScheduler>(main,deadline,true);
            scheduler->install(environment);
            world->scheduler=scheduler.get();
            scripts=std::make_unique<ScriptExecution>(environment,*world,*scheduler,snapshot_view,id);
            luaL_sandboxthread(environment);
            lua_setsafeenv(environment,LUA_GLOBALSINDEX,false);
            initial_ready=snapshot_view.value("initial_replication_complete",true);
        } catch(...) {
            lua_close(main);main=nullptr;environment=nullptr;throw;
        }
    }
    ~ActorVM(){if(main)lua_close(main);}

    void prepare(const world_bindings::ValidatedDeltaView* delta,const Json& source_catalog,
                 const LazyValidatedRootSourceCatalog& root_sources,
                 bool ready,const Json& root_mutations) {
        Json animation_responses=Json::array();
        const Json* delta_json=delta?&delta->value():nullptr;
        const bool has_animation_responses=delta_json&&delta_json->contains("animation_responses");
        if(delta)
            world_bindings::update_snapshot(environment,*world,*delta,true,&animation_responses);
        if(root_mutations.is_array()&&!root_mutations.empty())
            world_bindings::apply_local_mutations(environment,*world,root_mutations);
        // The root world owns camera replacement. Finish applying its ordered
        // mutations before deriving a fallback in this Actor replica, or the
        // temporary Actor-local camera would be journaled back to the root.
        world_bindings::refresh_camera(*world);
        if(has_animation_responses) {
            if(!animation_responses.is_array())
                throw std::runtime_error("Actor animation responses must be an array");
            Json subscribed=Json::array();
            for(const auto& response:animation_responses) {
                if(response.is_object()&&response.contains("reference")
                   &&response["reference"].is_string()
                   &&!world->animation.requested.count(response["reference"].get<std::string>()))
                    continue;
                subscribed.push_back(response);
            }
            // The root may fetch one asset for several VM-local consumers.
            // Only deliver it to this Actor if it has a matching request; the
            // existing completion path still validates the complete payload.
            world_bindings::complete_animation_responses(*world,subscribed);
        }
        if(source_catalog.is_object()&&!source_catalog.empty())scripts->update(source_catalog);
        if(root_sources.has_entries())scripts->update_world_sources(root_sources);
        initial_ready=ready;
        scripts->set_initial_ready(ready);
    }
    Json reconcile_scripts() {
        scheduler->begin_frame();
        return scripts->reconcile();
    }
    Json step(Json status,Clock::time_point deadline,
              const std::function<void(Clock::time_point)>& set_interrupt_deadline) {
        scheduler->step(deadline,[&](){world_bindings::dispatch_events(environment,*world);},256,
                        set_interrupt_deadline,
                        [&](){world_bindings::progress_invocations(environment,*world);});
        Json mutations=std::move(world->mutations);
        world->mutations=Json::array();
        world->mutation_bytes=0;
        Json diagnostics{{"actor_id",id},{"script_runtime",status},
                         {"runtime_instance_generation",world->connection_generation},
                         {"pending_tasks",scheduler->pending()},
                         {"vm_instantiated",true},
                         {"task_errors",scheduler->errors()},
                         {"mutations",std::move(mutations)},
                         {"output",std::move(world->output)},{"output_truncated",world->output_truncated},
                         {"unbridged_host_io",{{"chat_requests",world->chat_requests.size()},
                             {"text_service_requests",world->text_service_requests.size()},
                             {"remote_client_replies",world->remote_client_replies.size()}}},
                         {"runtime_thread_integrity",world->script_thread_report()},
                         {"runtime_protected_integrity",world->protected_script_report()}};
        world->output.clear();world->output_truncated=false;
        return diagnostics;
    }
    void capture_root_mutations(const world_bindings::World& root_world,bool include_existing=false) {
        if(root_world.mutations.size()<root_mutation_cursor)root_mutation_cursor=0;
        const size_t start=include_existing?0:root_mutation_cursor;
        if(!pending_root_mutations.is_array())pending_root_mutations=Json::array();
        if(!include_existing)for(size_t i=start;i<root_world.mutations.size();++i) {
            const auto& mutation=root_world.mutations[i];
            if(mutation.is_object()&&mutation.value("actor_origin",std::string())==id)continue;
            pending_root_mutations.push_back(mutation);
        }
        root_mutation_cursor=root_world.mutations.size();
    }
};

ActorRuntime::ActorRuntime(lua_Alloc allocator,void* allocator_data,
                           void (*interrupt)(lua_State*,int),
                           const SourceBindings& bindings,world_bindings::World& root_world,
                           const Json& initial_snapshot,
                           std::shared_ptr<shared_table_bindings::SharedTablePool> shared_table_pool)
    :allocator_(allocator),allocator_data_(allocator_data),bindings_(bindings),
     shared_table_pool_(std::move(shared_table_pool)),
     custom_asset_registry_(root_world.custom_asset_registry),
     root_world_(&root_world) {
    interrupt_=interrupt;
    if(!allocator_||!interrupt_)throw std::invalid_argument("Actor runtime requires allocator and interrupt callbacks");
    if(!initial_snapshot.is_object())throw std::invalid_argument("Actor runtime snapshot must be an object");
    has_builtin_player_scripts_=initial_snapshot.contains("builtin_player_scripts");
    if(has_builtin_player_scripts_)builtin_player_scripts_=initial_snapshot.at("builtin_player_scripts");
    script_catalog_=initial_snapshot.value("script_sources",Json::object());
    // Use the same lazy discovery as later updates. Building a complete JSON
    // world here duplicates every node even when all Actors are dormant (or
    // there are none). sync_actors snapshots only when a VM actually needs it.
    sync_actors(root_world,script_catalog_);
}

ActorRuntime::~ActorRuntime()=default;

void ActorRuntime::instantiate_actor(world_bindings::World& root_world,const std::string& id,
                                     std::shared_ptr<const Json> snapshot) {
    if(actors_.find(id)!=actors_.end())return;
    // The resource limit applies to isolated VMs, not replicated Actor
    // Instances. Dormant Actors remain visible in actor_order_ without owning
    // a VM, so count only instantiated entries here.
    if(actors_.size()>=kMaximumActorVMs)throw std::runtime_error("Actor VM limit exceeded");
    if(next_actor_vm_generation_==std::numeric_limits<uint64_t>::max())
        throw std::runtime_error("Actor VM connection generation exhausted");
    const uint64_t generation=next_actor_vm_generation_++;
    auto actor=std::make_unique<ActorVM>(this,id,allocator_,allocator_data_,interrupt_,bindings_,shared_table_pool_,snapshot,generation);
    actor->root_mutation_cursor=root_world.mutations.size();
    actors_.emplace(id,std::move(actor));
}

ActorRuntime::Json ActorRuntime::snapshot_from_world(const world_bindings::World& world,
                                                     const Json& script_catalog) const {
    Json snapshot{{"version",1},{"sequence",world.sequence},{"job_id",world.job_id},
                  {"local_player",world.local_player},{"services",Json::object()},
                  {"class_parents",world.class_parents},{"defaults",world.defaults},
                  {"default_origins",world.default_origins},{"class_events",world.class_events},
                  {"class_event_signatures",world.class_event_signatures},
                  {"class_property_types",world.class_property_types},
                  {"initial_replication_complete",world.initial_replication_complete},
                  {"lightweight_assets",world.lightweight_assets},
                  {"chat_enabled",world.chat_enabled},{"nodes",Json::array()},
                  {"tween_state",world_bindings::snapshot_tweens(world)},
                  {"script_sources",Json::object()}};
    if(!world.session_stats.empty())snapshot["session_stats"]=world.session_stats;
    // Actor worlds are reconstructed from data snapshots, so copy the local
    // Player:GetMouse singleton identity alongside the Instances. The cache
    // cannot be inferred from class/name: a cloned PlayerMouse may look the
    // same while still being a distinct Instance.
    if(!world.client_services.player_mice.empty()) {
        Json player_mice=Json::object();
        for(const auto& [player_id,mouse_id]:world.client_services.player_mice) {
            const auto player=world.nodes.find(player_id);
            const auto mouse=world.nodes.find(mouse_id);
            if(player_id==world.local_player&&player!=world.nodes.end()
               &&!player->second.destroyed&&player->second.class_name=="Player"
               &&mouse!=world.nodes.end()&&!mouse->second.destroyed
               &&mouse->second.class_name=="PlayerMouse")
                player_mice[player_id]=mouse_id;
        }
        if(!player_mice.empty())snapshot["client_service_state"]={{"player_mice",std::move(player_mice)}};
    }
    if(!world.input_capabilities.empty())snapshot["input_capabilities"]=world.input_capabilities;
    if(!world.viewport.empty())snapshot["viewport"]=world.viewport;
    if(!world.display.empty())snapshot["display"]=world.display;
    for(const auto& [name,id]:world.services)snapshot["services"][name]=id;
    for(const auto& id:world.order) {
        auto found=world.nodes.find(id);
        if(found==world.nodes.end())continue;
        const auto& node=found->second;
        std::string name=node.name;
        Json properties=node.properties;
        auto local=world.local_properties.find(id);
        if(local!=world.local_properties.end()&&local->second.is_object()) {
            for(auto it=local->second.begin();it!=local->second.end();++it)
                if(it.key()!="Parent")properties[it.key()]=it.value();
            auto override_name=local->second.find("Name");
            if(override_name!=local->second.end()&&override_name->is_string())
                name=override_name->get<std::string>();
        }
        std::string parent=node.parent;
        auto parent_override=world.local_properties.find(id);
        if(parent_override!=world.local_properties.end()&&parent_override->second.is_object()
           &&parent_override->second.contains("Parent")) {
            const Json& value=parent_override->second.at("Parent");
            if(value.is_null())parent.clear();
            else if(value.is_object()&&value.size()==1&&value.contains("$ref")&&value["$ref"].is_string())
                parent=value["$ref"].get<std::string>();
        }
        Json record{{"id",node.id},{"class_name",node.class_name},{"name",std::move(name)},
                    {"parent",node.destroyed?Json(nullptr):parent.empty()?Json(nullptr):Json(parent)},
                    {"properties",std::move(properties)},
                    {"collection_tags_known",node.collection_tags_known}};
        // This is an internal Actor construction snapshot. Empty storage and
        // parser-default flags need no per-node JSON allocation. Attribute
        // maps and their knowledge flag are forked directly from the root.
        if(!node.origins.is_object()||!node.origins.empty())record["origins"]=node.origins;
        if(!node.revisions.is_object()||!node.revisions.empty())record["property_revisions"]=node.revisions;
        if(node.destroyed)record["destroyed"]=true;
        if(!node.server_owned)record["server_owned"]=false;
        if(node.constructor_defaults_known)record["constructor_defaults_known"]=true;
        if(!node.collection_tags.empty())record["collection_tags"]=node.collection_tags;
        snapshot["nodes"].push_back(std::move(record));
        if(node.script_state.code().is_object())snapshot["script_sources"][id]=node.script_state.code();
    }
    if(script_catalog.is_object())
        for(auto it=script_catalog.begin();it!=script_catalog.end();++it) {
            auto node=world.nodes.find(it.key());
            if(node!=world.nodes.end()&&node->second.script_state.code().is_object())continue;
            snapshot["script_sources"][it.key()]=it.value();
        }
    if(has_builtin_player_scripts_)
        snapshot["builtin_player_scripts"]=builtin_player_scripts_;
    return snapshot;
}

void ActorRuntime::sync_actors(world_bindings::World& root_world,const Json& script_catalog) {
    if(script_catalog.is_object()&&!script_catalog.empty())script_catalog_=script_catalog;
    root_world_sources_=Json::object();
    std::unordered_set<std::string> live_actor_ids;
    for(const auto& [id,node]:root_world.nodes)
        if(is_actor(node)&&!node.destroyed)live_actor_ids.insert(id);
    const std::vector<std::string> previous_order=actor_order_;
    for(auto it=actors_.begin();it!=actors_.end();) {
        if(live_actor_ids.count(it->first)){++it;continue;}
        it=actors_.erase(it); // lua_close releases every Actor-local waiter/reference.
    }
    for(const auto& removed:previous_order)if(live_actor_ids.find(removed)==live_actor_ids.end()) {
        for(auto message=messages_.begin();message!=messages_.end();) {
            if(message->target==removed){++dropped_messages_;message=messages_.erase(message);}else ++message;
        }
        for(auto route=remote_server_routes_.begin();route!=remote_server_routes_.end();) {
            if(route->second.actor_id==removed)route=remote_server_routes_.erase(route);else ++route;
        }
    }
    std::vector<std::string> live_order;
    live_order.reserve(live_actor_ids.size());
    std::unordered_set<std::string> ordered_live_ids;
    for(const auto& id:previous_order) {
        if(live_actor_ids.find(id)==live_actor_ids.end())continue;
        live_order.push_back(id);
        ordered_live_ids.insert(id);
    }
    for(const auto& id:root_world.order)
        if(live_actor_ids.find(id)!=live_actor_ids.end()
           &&ordered_live_ids.insert(id).second)live_order.push_back(id);
    const std::unordered_set<std::string> previous_ids(previous_order.begin(),previous_order.end());
    actor_order_=std::move(live_order);
    const auto possible_owners=world_bindings::actors_that_may_own_client_work(root_world);

    std::shared_ptr<const Json> current_snapshot;
    for(const auto& id:actor_order_) {
        const bool newly_discovered=previous_ids.find(id)==previous_ids.end();
        const bool needs_vm=possible_owners.find(id)!=possible_owners.end();
        auto found=actors_.find(id);
        if(found==actors_.end()&&needs_vm) {
            if(!current_snapshot)
                current_snapshot=std::make_shared<const Json>(snapshot_from_world(root_world,script_catalog_));
            // Whether the Actor itself is new or was dormant, this snapshot
            // already contains the current root topology and source state.
            // Its cursor begins after those mutations to avoid replaying them.
            instantiate_actor(root_world,id,current_snapshot);
        } else if(found!=actors_.end()&&!newly_discovered) {
            found->second->capture_root_mutations(root_world,false);
        }
    }
    // Only instantiated VMs consume this catalog. Dormant Actor topology and
    // worlds without Actors must not copy every script's bytecode each tick.
    // Build after discovery so a newly activated Actor sees the current root.
    if(!actors_.empty())
        for(const auto& [id,node]:root_world.nodes)
            if(!node.destroyed&&node.script_state.code().is_object()&&
               (node.script_state.code().contains("source")||node.script_state.code().contains("bytecode_hex")))
                root_world_sources_[id]=node.script_state.code();
    if(!actor_order_.empty())step_actor_cursor_%=actor_order_.size();
    else step_actor_cursor_=0;
}

void ActorRuntime::capture_root_mutations(const world_bindings::World& root_world) {
    for(const auto& id:actor_order_) {
        auto found=actors_.find(id);
        if(found!=actors_.end())found->second->capture_root_mutations(root_world,false);
    }
}

void ActorRuntime::root_mutations_cleared() {
    for(auto& [id,actor]:actors_)actor->root_mutation_cursor=0;
}

void ActorRuntime::enqueue_message(const std::string& target_actor,const std::string& topic,
                                   const Json& arguments) {
    if(topic.size()>256)throw std::runtime_error("Actor message id exceeds 256 bytes");
    if(!arguments.is_array()||arguments.size()>64||arguments.dump().size()>64*1024)
        throw std::runtime_error("Actor message arguments exceed the data-only payload limit");
    if(messages_.size()>=4096)throw std::runtime_error("Actor message queue is full");
    messages_.push_back(QueuedMessage{target_actor,topic,arguments});
}

ActorRuntime::Json ActorRuntime::update(const world_bindings::ValidatedDeltaView* delta,const Json& script_sources,
                                       bool initial_ready,Clock::time_point deadline,
                                       std::chrono::milliseconds minimum_step_budget,
                                       std::function<void(Clock::time_point)> set_interrupt_deadline,
                                       Json* stage_timings) {
    Json results=Json::array();
    double prepare_ms=0.0;
    // Root sources are immutable for this update and Actors prepare serially.
    // The view checks the serialized-size admission rule on the first actual
    // consumer, then shares that checked size across the remaining Actors.
    const LazyValidatedRootSourceCatalog root_source_view(root_world_sources_);
    for(const auto& id:actor_order_) {
        auto found=actors_.find(id);
        if(found==actors_.end())continue;
        try {
            const auto prepare_started=Clock::now();
            found->second->prepare(delta,script_sources,root_source_view,initial_ready,
                                   found->second->pending_root_mutations);
            prepare_ms+=std::chrono::duration<double,std::milli>(Clock::now()-prepare_started).count();
            found->second->pending_root_mutations=Json::array();
        } catch(const std::exception& error) {
            throw std::runtime_error("root-to-Actor update failed for "
                +id+": "+error.what());
        }
    }
    double step_ms=0.0;
    // Reconciliation discovers and compiles runnable scripts but is itself
    // non-yielding host work. Finish it before reserving the shared execution
    // slice, just as preparation must finish before a VM can observe the world.
    std::vector<Json> statuses(actor_order_.size());
    const auto reconcile_started=Clock::now();
    for(size_t index=0;index<actor_order_.size();++index) {
        auto found=actors_.find(actor_order_[index]);
        if(found!=actors_.end())statuses[index]=found->second->reconcile_scripts();
    }
    const double reconcile_ms=std::chrono::duration<double,std::milli>(Clock::now()-reconcile_started).count();
    step_ms+=reconcile_ms;
    const size_t count=actor_order_.size();
    std::vector<Json> ordered_results(count);
    // Dormant topology rows do not have a VM to step. Build their stable
    // diagnostics before granting the shared VM execution slice.
    for(size_t index=0;index<count;++index) {
        const auto& id=actor_order_[index];
        if(actors_.find(id)!=actors_.end())continue;
        ordered_results[index]=Json{{"actor_id",id},{"vm_instantiated",false},
            {"state","dormant"},
            {"script_runtime",{{"local_scripts",0},{"running",0},{"completed",0},
                {"failed",0},{"blocked",0},{"stopped",0},{"modules_loaded",0},
                {"modules_loading",0},{"modules_failed",0},{"details",Json::array()},
                {"bootstrap_errors",0}}},
            {"pending_tasks",0},{"task_errors",Json::array()},
            {"mutations",Json::array()},{"output",""},{"output_truncated",false}};
    }
    // One bounded reserve for the entire Actor lane, never one per VM. A
    // short recurring request otherwise expires during synchronization and
    // leaves every Actor's ready queue permanently unserviced.
    const auto reserve=std::clamp(minimum_step_budget,std::chrono::milliseconds(0),
                                 std::chrono::milliseconds(5));
    const auto execution_deadline=std::max(deadline,Clock::now()+reserve);
    size_t start=0;
    bool selected_vm=false;
    for(size_t offset=0;offset<count;++offset) {
        const size_t index=(step_actor_cursor_+offset)%count;
        if(actors_.find(actor_order_[index])==actors_.end())continue;
        start=index;
        selected_vm=true;
        break;
    }
    step_actor_cursor_=selected_vm?(start+1)%count:0;
    for(size_t offset=0;offset<count;++offset) {
        const size_t index=(start+offset)%count;
        const auto& id=actor_order_[index];
        auto found=actors_.find(id);
        if(found==actors_.end())continue;
        std::unordered_set<std::string> blocked_topics;
        for(auto message=messages_.begin();message!=messages_.end();) {
            if(message->target!=id){++message;continue;}
            if(blocked_topics.count(message->topic)){++message;continue;}
            if(world_bindings::enqueue_actor_message(*found->second->world,id,message->topic,message->arguments))
                message=messages_.erase(message);
            else {blocked_topics.insert(message->topic);++message;}
        }
        const auto step_started=Clock::now();
        Json item=found->second->step(std::move(statuses[index]),execution_deadline,set_interrupt_deadline);
        step_ms+=std::chrono::duration<double,std::milli>(Clock::now()-step_started).count();
        item["world_revision"]=found->second->world->sequence;
        ordered_results[index]=std::move(item);
    }
    // Keep diagnostic and root mutation application order stable even though
    // the first scheduled VM rotates to prevent a busy Actor starving peers.
    for(auto& item:ordered_results)if(!item.is_null())results.push_back(std::move(item));
    for(auto message=messages_.begin();message!=messages_.end();) {
        if(++message->age>120){++dropped_messages_;message=messages_.erase(message);}else ++message;
    }
    if(stage_timings) {
        *stage_timings=Json{{"actor_count",actor_order_.size()},
                            {"prepare_ms",prepare_ms},{"reconcile_ms",reconcile_ms},
                            {"step_ms",step_ms}};
    }
    return results;
}

world_bindings::Connection* ActorRuntime::find_connection(const std::string& owner_actor_id,
                                                         uint64_t connection_id,
                                                         uint64_t generation) const {
    world_bindings::World* owner=nullptr;
    if(owner_actor_id.empty())owner=root_world_;
    else {
        auto actor=actors_.find(owner_actor_id);
        if(actor!=actors_.end())owner=actor->second->world.get();
    }
    if(!owner||!connection_id)return nullptr;
    for(const auto& connection:owner->connections)
        if(connection&&connection->id==connection_id&&connection->generation==generation)
            return connection.get();
    return nullptr;
}

bool ActorRuntime::connection_state(const std::string& owner_actor_id,uint64_t connection_id,
                                    uint64_t generation,ConnectionView& result) const {
    auto* connection=find_connection(owner_actor_id,connection_id,generation);
    if(!connection)return false;
    result.owner_actor_id=owner_actor_id;
    result.connection_id=connection_id;
    result.generation=generation;
    result.connected=connection->connected;
    result.enabled=connection->connected&&connection->enabled;
    result.lua_connection=connection->native_kind==world_bindings::Connection::NativeKind::None
        &&(connection->signal_waiter?connection->creator_ref!=LUA_NOREF:
           connection->callback!=LUA_NOREF&&connection->callback!=LUA_REFNIL);
    result.native=connection->native_kind!=world_bindings::Connection::NativeKind::None;
    return true;
}

std::vector<ActorRuntime::ConnectionView> ActorRuntime::foreign_connections(
    const std::string& caller_actor_id,const std::string& node_id,
    const std::string& signal_key) {
    std::vector<ConnectionView> result;
    auto append=[&](world_bindings::World* owner,const std::string& owner_actor_id) {
        if(!owner||owner_actor_id==caller_actor_id)return;
        world_bindings::prune_signal_waiters(*owner);
        for(auto it=owner->connections.rbegin();it!=owner->connections.rend();++it) {
            const auto& connection=*it;
            if(!connection||!connection->signal||!connection->signal->node
               ||connection->signal->node->id!=node_id||connection->signal->key!=signal_key
               ||connection->auto_disconnected||connection->manually_disconnected)continue;
            // We already hold this exact Connection. Build the proxy record
            // directly instead of rescanning the owning World for each match.
            ConnectionView view;
            view.owner_actor_id=owner_actor_id;
            view.connection_id=connection->id;
            view.generation=connection->generation;
            view.connected=connection->connected;
            view.enabled=connection->connected&&connection->enabled;
            view.lua_connection=connection->native_kind==world_bindings::Connection::NativeKind::None
                &&(connection->signal_waiter?connection->creator_ref!=LUA_NOREF:
                   connection->callback!=LUA_NOREF&&connection->callback!=LUA_REFNIL);
            view.native=connection->native_kind!=world_bindings::Connection::NativeKind::None;
            result.push_back(std::move(view));
        }
    };
    // Root and Actor signals have separate registries over shared Instance IDs.
    // Include only subscriptions from other VMs; the caller's World is scanned
    // directly by getconnections and must not be duplicated here.
    append(root_world_,"");
    for(auto it=actor_order_.rbegin();it!=actor_order_.rend();++it) {
        auto actor=actors_.find(*it);
        if(actor!=actors_.end())append(actor->second->world.get(),*it);
    }
    return result;
}

bool ActorRuntime::set_connection_enabled(const std::string& owner_actor_id,
                                          uint64_t connection_id,uint64_t generation,
                                          bool enabled) {
    auto* connection=find_connection(owner_actor_id,connection_id,generation);
    if(!connection||connection->auto_disconnected||connection->manually_disconnected)return false;
    connection->enabled=enabled;
    return true;
}

bool ActorRuntime::disconnect_connection(const std::string& owner_actor_id,
                                          uint64_t connection_id,uint64_t generation) {
    auto* connection=find_connection(owner_actor_id,connection_id,generation);
    if(!connection)return false;
    connection->connected=false;
    connection->manually_disconnected=true;
    if(connection->signal_waiter&&connection->signal) {
        auto& world=*connection->signal->world;
        world_bindings::prune_signal_waiters(world,connection->signal);
    }
    return true;
}

bool ActorRuntime::has_actor(const std::string& id) const {
    return std::find(actor_order_.begin(),actor_order_.end(),id)!=actor_order_.end();
}

ActorRuntime::Json ActorRuntime::diagnostics() const {
    Json result={{"actors",Json::array()},{"dropped_messages",dropped_messages_},
                 {"queued_messages",messages_.size()}};
    for(const auto& id:actor_order_) {
        auto found=actors_.find(id);
        const bool instantiated=found!=actors_.end();
        result["actors"].push_back(Json{{"actor_id",id},{"isolated_vm",instantiated},
                                        {"vm_instantiated",instantiated}});
    }
    return result;
}

ActorRuntime::Json ActorRuntime::drain_remote_server_io(world_bindings::World& root_world) {
    Json events=Json::array(),calls=Json::array();
    const size_t event_limit=root_world.remote_server_events.size()<512
        ?512-root_world.remote_server_events.size():0;
    const size_t call_limit=root_world.remote_server_calls.size()<512
        ?512-root_world.remote_server_calls.size():0;
    constexpr size_t byte_limit=256*1024;
    size_t event_bytes=root_world.remote_server_event_bytes;
    size_t call_bytes=root_world.remote_server_call_bytes;
    const size_t count=actor_order_.size();
    const size_t start=count?remote_server_io_actor_cursor_%count:0;
    for(size_t offset=0;offset<count;++offset) {
        const auto& id=actor_order_[(start+offset)%count];
        auto found=actors_.find(id);if(found==actors_.end())continue;
        auto& world=*found->second->world;
        if(world.remote_server_events.is_array()) {
            Json deferred=Json::array();
            bool blocked=false;
            for(auto& event:world.remote_server_events) {
                const size_t size=event.dump().size();
                if(!blocked&&events.size()<event_limit&&event_bytes<=byte_limit&&size<=byte_limit-event_bytes) {
                    events.push_back(std::move(event));event_bytes+=size;
                } else {blocked=true;deferred.push_back(std::move(event));}
            }
            world.remote_server_events=std::move(deferred);
            world.remote_server_event_bytes=actor_json_row_bytes(world.remote_server_events);
        }
        if(world.remote_server_calls.is_array()) {
            Json deferred=Json::array();
            bool blocked=false;
            for(auto& call:world.remote_server_calls) {
                if(!call.is_object()||!call.contains("request_id")||!call["request_id"].is_number_integer()
                   ||!call.contains("node")||!call["node"].is_string())
                    throw std::runtime_error("malformed Actor RemoteFunction call");
                Json forwarded=call;
                const uint64_t local_request=actor_request_id(call["request_id"]);
                const uint64_t maximum=uint64_t(std::numeric_limits<int64_t>::max());
                if(blocked||calls.size()>=call_limit||root_world.next_remote_server_request>=maximum) {
                    blocked=true;deferred.push_back(std::move(call));continue;
                }
                const uint64_t public_request=root_world.next_remote_server_request;
                forwarded["request_id"]=public_request;
                const size_t size=forwarded.dump().size();
                if(call_bytes>byte_limit||size>byte_limit-call_bytes) {
                    blocked=true;deferred.push_back(std::move(call));continue;
                }
                ++root_world.next_remote_server_request;
                remote_server_routes_[public_request]=RemoteServerRoute{
                    id,call["node"].get<std::string>(),local_request};
                calls.push_back(std::move(forwarded));call_bytes+=size;
            }
            world.remote_server_calls=std::move(deferred);
            world.remote_server_call_bytes=actor_json_row_bytes(world.remote_server_calls);
        }
    }
    if(count)remote_server_io_actor_cursor_=(start+1)%count;
    // A waiter disappears when its RemoteFunction is destroyed or its task is
    // cancelled. Drop its route so the host stops carrying that request.
    for(auto route=remote_server_routes_.begin();route!=remote_server_routes_.end();) {
        auto actor=actors_.find(route->second.actor_id);
        bool active=false;
        if(actor!=actors_.end())for(const auto& waiter:actor->second->world->remote_server_waiters)
            if(waiter.request==route->second.local_request&&waiter.node==route->second.node){active=true;break;}
        if(active)++route;else route=remote_server_routes_.erase(route);
    }
    Json active=Json::array();
    for(const auto& [request,route]:remote_server_routes_)active.push_back(request);
    return Json{{"remote_server_events",std::move(events)},
                {"remote_server_calls",std::move(calls)},
                {"active_remote_server_requests",std::move(active)}};
}

ActorRuntime::Json ActorRuntime::route_remote_server_results(
        lua_State* root_environment,world_bindings::World& root_world,const Json& responses) {
    (void)root_environment;
    if(!responses.is_array()||responses.size()>128)
        throw std::runtime_error("invalid RemoteFunction response batch");
    Json root_responses=Json::array();
    std::unordered_map<std::string,Json> actor_responses;
    std::unordered_map<std::string,std::vector<uint64_t>> routed_ids;
    for(const auto& response:responses) {
        if(!response.is_object()||!response.contains("request_id")||!response["request_id"].is_number_integer()) {
            root_responses.push_back(response);continue;
        }
        const uint64_t request=actor_request_id(response["request_id"]);
        auto route=remote_server_routes_.find(request);
        if(route==remote_server_routes_.end()) {
            root_responses.push_back(response);continue;
        }
        auto actor=actors_.find(route->second.actor_id);
        if(actor==actors_.end()) {
            remote_server_routes_.erase(route);continue;
        }
        if(!response.contains("node")||!response["node"].is_string()
           ||response["node"].get<std::string>()!=route->second.node)
            continue; // Keep the owner route active; never complete a mismatched waiter.
        Json local=response;
        local["request_id"]=route->second.local_request;
        actor_responses[route->second.actor_id].push_back(std::move(local));
        routed_ids[route->second.actor_id].push_back(request);
    }
    for(auto& [actor_id,batch]:actor_responses) {
        auto actor=actors_.find(actor_id);
        if(actor!=actors_.end())
            world_bindings::complete_remote_server(actor->second->environment,*actor->second->world,batch);
        for(uint64_t id:routed_ids[actor_id])remote_server_routes_.erase(id);
    }
    (void)root_world;
    return root_responses;
}
