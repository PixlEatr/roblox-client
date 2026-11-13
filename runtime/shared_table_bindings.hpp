#pragma once

#include "json.hpp"
#include <lua.h>

#include <array>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <variant>

namespace world_bindings {struct World;}

namespace shared_table_bindings {
struct Data;
struct Key {
    bool numeric=false;
    uint32_t number=0;
    std::string text;
    bool operator<(const Key& other) const;
};
struct Value {
    std::variant<bool,double,std::string,std::array<double,4>,nlohmann::json,std::shared_ptr<Data>> value;
};
struct Data {
    uint64_t id=0;
    class SharedTablePool* owner=nullptr;
    mutable std::mutex mutex;
    std::map<Key,Value> entries;
    bool frozen=false;
    uint64_t revision=0;
    size_t accounted_bytes=0;
};
class SharedTablePool {
public:
    SharedTablePool()=default;
    ~SharedTablePool();
    std::shared_ptr<Data> create();
    std::shared_ptr<Data> find(uint64_t id) const;
    std::shared_ptr<Data> get_registered(const std::string& name);
    void set_registered(const std::string& name,const std::shared_ptr<Data>& value);
    void adjust_memory(size_t add,size_t release=0);
private:
    mutable std::mutex mutex_;
    uint64_t next_id_=1;
    std::unordered_map<uint64_t,std::shared_ptr<Data>> objects_;
    std::unordered_map<std::string,std::shared_ptr<Data>> registered_;
    size_t used_bytes_=0;
    size_t registered_bytes_=0;
};

// Install the SharedTable library and its userdata type into one Luau VM.
// Each VM receives wrappers from the same session pool, never cross-VM Lua refs.
void install(lua_State* state,const std::shared_ptr<SharedTablePool>& pool,
             world_bindings::World* world);
bool is_shared_table(lua_State* state,int index);
bool capture_reference(lua_State* state,int index,const SharedTablePool& pool,
                       nlohmann::json& encoded);
bool push_reference(lua_State* state,const nlohmann::json& encoded,
                    const SharedTablePool& pool);
std::shared_ptr<Data> get_data(lua_State* state,int index,const SharedTablePool& pool);
void push_table(lua_State* state,const std::shared_ptr<Data>& data,const SharedTablePool& pool);
} // namespace shared_table_bindings
