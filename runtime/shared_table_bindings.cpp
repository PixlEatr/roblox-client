#include "shared_table_bindings.hpp"
#include "world_bindings.hpp"
#include <lualib.h>

#include <cmath>
#include <new>
#include <optional>
#include <stdexcept>
#include <unordered_set>
#include <utility>

namespace shared_table_bindings {
namespace {
using Json=nlohmann::json;
constexpr const char* kMeta="Session.SharedTable";
constexpr size_t kMaxObjects=65536;
constexpr size_t kMaxEntries=65536;
constexpr size_t kMaxStringBytes=1024*1024;
constexpr size_t kMaxPoolBytes=64*1024*1024;
constexpr size_t kDataOverhead=256;
constexpr int kMaxNesting=64;
struct Handle {std::shared_ptr<Data> data;};

SharedTablePool* pool(lua_State* L) {
    return static_cast<SharedTablePool*>(lua_touserdata(L,lua_upvalueindex(1)));
}
world_bindings::World* world(lua_State* L) {
    return static_cast<world_bindings::World*>(lua_touserdata(L,lua_upvalueindex(2)));
}
Handle* read(lua_State* L,int index) {
    if(!lua_isuserdata(L,index)||!lua_getmetatable(L,index))return nullptr;
    luaL_getmetatable(L,kMeta);bool same=lua_rawequal(L,-1,-2)!=0;lua_pop(L,2);
    return same?static_cast<Handle*>(lua_touserdata(L,index)):nullptr;
}
bool has_meta(lua_State* L,int index,const char* name) {
    if(!lua_getmetatable(L,index))return false;luaL_getmetatable(L,name);
    bool same=lua_rawequal(L,-1,-2)!=0;lua_pop(L,2);return same;
}
std::shared_ptr<Data> checked(lua_State* L,int index) {
    auto* value=static_cast<Handle*>(luaL_checkudata(L,index,kMeta));return value->data;
}
size_t value_memory(const Value& value) {
    if(auto p=std::get_if<std::string>(&value.value))return sizeof(Value)+p->size();
    if(auto p=std::get_if<Json>(&value.value))return sizeof(Value)+p->dump().size();
    return sizeof(Value);
}
size_t entry_memory(const Key& key,const Value& value) {
    return sizeof(Key)+32+(key.numeric?0:key.text.size())+value_memory(value);
}
void set_entry_locked(Data& data,const Key& key,std::optional<Value> value) {
    auto found=data.entries.find(key);
    size_t before=found==data.entries.end()?0:entry_memory(found->first,found->second);
    size_t after=value?entry_memory(key,*value):0;
    if(!data.owner)throw std::runtime_error("SharedTable session pool has expired");
    if(value) {
        if(found==data.entries.end()&&data.entries.size()>=kMaxEntries)throw std::runtime_error("SharedTable entry limit exceeded");
        data.owner->adjust_memory(after,before);
        data.entries[key]=std::move(*value);
    } else if(found!=data.entries.end()){data.owner->adjust_memory(0,before);data.entries.erase(found);}
    data.accounted_bytes=data.accounted_bytes-before+after;++data.revision;
}
void push(lua_State* L,const std::shared_ptr<Data>& data) {
    auto* value=static_cast<Handle*>(lua_newuserdatadtor(L,sizeof(Handle),
        [](lua_State*,void* raw){static_cast<Handle*>(raw)->~Handle();}));
    new(value)Handle{data};luaL_getmetatable(L,kMeta);lua_setmetatable(L,-2);
}
bool key_from_lua(lua_State* L,int index,Key& key) {
    if(lua_type(L,index)==LUA_TSTRING) {
        size_t size=0;const char* text=lua_tolstring(L,index,&size);
        if(size>kMaxStringBytes)return false;key.text.assign(text,size);return true;
    }
    if(lua_type(L,index)!=LUA_TNUMBER)return false;
    double n=lua_tonumber(L,index);
    if(!std::isfinite(n)||n<0||n>=4294967296.0||std::floor(n)!=n)return false;
    key.numeric=true;key.number=static_cast<uint32_t>(n);return true;
}
void push_key(lua_State* L,const Key& key) {
    if(key.numeric)lua_pushnumber(L,key.number);else lua_pushlstring(L,key.text.data(),key.text.size());
}
Value capture_value(lua_State* L,int index,int depth=0,
        std::unordered_map<const void*,std::shared_ptr<Data>>* seen=nullptr) {
    if(depth>kMaxNesting)throw std::runtime_error("SharedTable nested value limit exceeded");
    index=lua_absindex(L,index);
    switch(lua_type(L,index)) {
        case LUA_TBOOLEAN:return Value{bool(lua_toboolean(L,index))};
        case LUA_TNUMBER:return Value{double(lua_tonumber(L,index))};
        case LUA_TSTRING:{size_t size=0;const char* text=lua_tolstring(L,index,&size);if(size>kMaxStringBytes)throw std::runtime_error("SharedTable string limit exceeded");return Value{std::string(text,size)};}
        case LUA_TVECTOR:{const auto* vector=lua_tovector(L,index);std::array<double,4> value{};for(int i=0;i<LUA_VECTOR_SIZE;++i)value[size_t(i)]=vector[i];return Value{value};}
        case LUA_TUSERDATA:{
            auto* nested=read(L,index);if(nested){if(pool(L)->find(nested->data->id)!=nested->data)throw std::runtime_error("SharedTable belongs to a different session pool");return Value{nested->data};}
            if(has_meta(L,index,"WorldSnapshot.Instance")||has_meta(L,index,"WorldSnapshot.InstanceHandle"))
                throw std::runtime_error("Instances cannot be stored in a SharedTable");
            if(!world(L))throw std::runtime_error("serializable datatypes require a World context");
            Json encoded=world_bindings::capture_value(L,*world(L),index);
            static const std::unordered_set<std::string> allowed={
                "$enum","$cframe","$catalog_search_params","$path_waypoint","$font",
                "$axes","$faces","$vector2int16","$vector3int16","$region3int16",
                "$ray","$color3","$vector2","$udim","$udim2","$number_range",
                "$rect","$brickcolor","$datetime","$number_keypoint","$number_sequence",
                "$color_keypoint","$color_sequence","$physical_properties","$rotation_curve_key",
                "$security_capabilities","$content"
            };
            if(!encoded.is_object()||encoded.size()!=1||!allowed.count(encoded.begin().key()))
                throw std::runtime_error("userdata is not an allowed serializable SharedTable datatype");
            return Value{std::move(encoded)};
        }
        case LUA_TTABLE:{
            if(!seen)throw std::runtime_error("plain tables are supported only in SharedTable.new(table)");
            const void* identity=lua_topointer(L,index);auto found=seen->find(identity);
            if(found!=seen->end())return Value{found->second};
            auto data=pool(L)->create();seen->emplace(identity,data);
            lua_pushnil(L);while(lua_next(L,index)) {
                Key key;if(!key_from_lua(L,-2,key))throw std::runtime_error("invalid SharedTable table key");
                Value element=capture_value(L,-1,depth+1,seen);
                {std::lock_guard<std::mutex> lock(data->mutex);set_entry_locked(*data,key,std::move(element));}lua_pop(L,1);
            }
            return Value{std::move(data)};
        }
        default:throw std::runtime_error("unsupported SharedTable value type");
    }
}
void push_value(lua_State* L,world_bindings::World* world,const Value& value) {
    if(auto p=std::get_if<bool>(&value.value))lua_pushboolean(L,*p);
    else if(auto p=std::get_if<double>(&value.value))lua_pushnumber(L,*p);
    else if(auto p=std::get_if<std::string>(&value.value))lua_pushlstring(L,p->data(),p->size());
    else if(auto p=std::get_if<std::array<double,4>>(&value.value)) {
#if LUA_VECTOR_SIZE == 4
        lua_pushvector(L,(*p)[0],(*p)[1],(*p)[2],(*p)[3]);
#else
        lua_pushvector(L,(*p)[0],(*p)[1],(*p)[2]);
#endif
    } else if(auto p=std::get_if<Json>(&value.value)) {
        if(!world)throw std::runtime_error("serializable datatypes require a World context");
        world_bindings::push_serialized_value(L,*world,*p);
    }
    else push(L,std::get<std::shared_ptr<Data>>(value.value));
}
void require_mutable(const Data& data){if(!data.owner)throw std::runtime_error("SharedTable session pool has expired");if(data.frozen)throw std::runtime_error("attempt to modify a frozen SharedTable");}
int index(lua_State* L) {
    auto data=checked(L,1);Key key;if(!key_from_lua(L,2,key))return luaL_error(L,"SharedTable keys must be strings or uint32 integers"),0;
    try{std::optional<Value> value;
        {std::lock_guard<std::mutex> lock(data->mutex);auto it=data->entries.find(key);if(it!=data->entries.end())value=it->second;}
        if(value)push_value(L,world(L),*value);else lua_pushnil(L);return 1;
    }catch(const std::exception& e){return luaL_error(L,"SharedTable read: %s",e.what()),0;}
}
int newindex(lua_State* L) {
    auto data=checked(L,1);Key key;if(!key_from_lua(L,2,key))return luaL_error(L,"SharedTable keys must be strings or uint32 integers"),0;
    try {
        Value value;if(lua_type(L,3)!=LUA_TNIL)value=capture_value(L,3);
        std::lock_guard<std::mutex> lock(data->mutex);require_mutable(*data);
        std::optional<Value> next;if(lua_type(L,3)!=LUA_TNIL)next=std::move(value);
        set_entry_locked(*data,key,std::move(next));return 0;
    }catch(const std::exception& e){return luaL_error(L,"SharedTable write: %s",e.what()),0;}
}
int equal(lua_State* L){auto* a=read(L,1);auto* b=read(L,2);lua_pushboolean(L,a&&b&&a->data==b->data);return 1;}
int iterator_next(lua_State* L) {
    luaL_checktype(L,1,LUA_TTABLE);
    if(lua_isnil(L,2))lua_pushnil(L);else lua_pushvalue(L,2);
    if(lua_next(L,1))return 2;return 0;
}
int iterator(lua_State* L) {
    try{auto data=checked(L,1);lua_createtable(L,0,0);int snapshot=lua_gettop(L);
        {std::lock_guard<std::mutex> lock(data->mutex);for(const auto& [key,value]:data->entries){push_key(L,key);push_value(L,world(L),value);lua_rawset(L,snapshot);}}
        lua_pushcfunction(L,iterator_next,"SharedTable iterator");lua_insert(L,snapshot);lua_pushnil(L);return 3;
    }catch(const std::exception& e){return luaL_error(L,"SharedTable iteration: %s",e.what()),0;}
}
std::shared_ptr<Data> clone_data(SharedTablePool& table_pool,const std::shared_ptr<Data>& source,
        bool deep,bool frozen,std::unordered_map<uint64_t,std::shared_ptr<Data>>& seen,int depth=0) {
    if(depth>kMaxNesting)throw std::runtime_error("SharedTable clone nesting limit exceeded");
    if(deep){auto found=seen.find(source->id);if(found!=seen.end())return found->second;}
    auto copy=table_pool.create();if(deep)seen[source->id]=copy;
    std::map<Key,Value> values;{std::lock_guard<std::mutex> lock(source->mutex);values=source->entries;}
    if(deep)for(auto& [key,value]:values)if(auto nested=std::get_if<std::shared_ptr<Data>>(&value.value))
        value=Value{clone_data(table_pool,*nested,true,frozen,seen,depth+1)};
    {std::lock_guard<std::mutex> lock(copy->mutex);for(auto& [key,value]:values)set_entry_locked(*copy,key,std::move(value));copy->frozen=frozen;}
    return copy;
}
int construct(lua_State* L) {
    if(lua_gettop(L)>1)return luaL_error(L,"SharedTable.new expects an optional table"),0;
    try {
        auto data=pool(L)->create();
        if(lua_gettop(L)==1) {
            if(!lua_istable(L,1))return luaL_argerror(L,1,"table expected"),0;
            std::unordered_map<const void*,std::shared_ptr<Data>> seen;seen.emplace(lua_topointer(L,1),data);
            lua_pushnil(L);while(lua_next(L,1)) {
                Key key;if(!key_from_lua(L,-2,key))throw std::runtime_error("invalid SharedTable table key");
                auto value=capture_value(L,-1,0,&seen);{std::lock_guard<std::mutex> lock(data->mutex);set_entry_locked(*data,key,std::move(value));}lua_pop(L,1);
            }
        }
        push(L,data);return 1;
    }catch(const std::exception& e){return luaL_error(L,"SharedTable.new: %s",e.what()),0;}
}
int clear(lua_State* L){auto data=checked(L,1);try{std::lock_guard<std::mutex> lock(data->mutex);require_mutable(*data);data->owner->adjust_memory(0,data->accounted_bytes);data->accounted_bytes=0;data->entries.clear();++data->revision;return 0;}catch(const std::exception& e){return luaL_error(L,"SharedTable.clear: %s",e.what()),0;}}
int clone_common(lua_State* L,bool frozen) {
    auto data=checked(L,1);bool deep=lua_gettop(L)>=2&&lua_toboolean(L,2);
    try{std::unordered_map<uint64_t,std::shared_ptr<Data>> seen;push(L,clone_data(*pool(L),data,deep,frozen,seen));return 1;}
    catch(const std::exception& e){return luaL_error(L,"SharedTable.clone: %s",e.what()),0;}
}
int clone(lua_State* L){return clone_common(L,false);}int clone_and_freeze(lua_State* L){return clone_common(L,true);}
int is_frozen(lua_State* L){auto data=checked(L,1);std::lock_guard<std::mutex> lock(data->mutex);lua_pushboolean(L,data->frozen);return 1;}
int size(lua_State* L){auto data=checked(L,1);std::lock_guard<std::mutex> lock(data->mutex);lua_pushnumber(L,double(data->entries.size()));return 1;}
int increment(lua_State* L) {
    auto data=checked(L,1);Key key;if(!key_from_lua(L,2,key))return luaL_argerror(L,2,"SharedTable key expected"),0;
    if(lua_type(L,3)!=LUA_TNUMBER)return luaL_argerror(L,3,"number expected"),0;
    double delta=lua_tonumber(L,3);try{std::lock_guard<std::mutex> lock(data->mutex);require_mutable(*data);
        auto found=data->entries.find(key);if(found==data->entries.end())throw std::runtime_error("increment key must exist");
        auto* current=std::get_if<double>(&found->second.value);if(!current)throw std::runtime_error("increment value must be a number");
        double original=*current,result=original+delta;
        found->second=Value{result};++data->revision;lua_pushnumber(L,original);return 1;
    }catch(const std::exception& e){return luaL_error(L,"SharedTable.increment: %s",e.what()),0;}
}
int update(lua_State* L) {
    auto data=checked(L,1);Key key;if(!key_from_lua(L,2,key))return luaL_argerror(L,2,"SharedTable key expected"),0;luaL_checktype(L,3,LUA_TFUNCTION);
    try{for(int attempt=0;attempt<64;++attempt){std::optional<Value> old;uint64_t revision;
        {std::lock_guard<std::mutex> lock(data->mutex);require_mutable(*data);revision=data->revision;auto found=data->entries.find(key);if(found!=data->entries.end())old=found->second;}
        lua_pushvalue(L,3);if(old)push_value(L,world(L),*old);else lua_pushnil(L);if(lua_pcall(L,1,1,0)!=LUA_OK)return lua_error(L),0;
        std::optional<Value> next;if(lua_type(L,-1)!=LUA_TNIL)next=capture_value(L,-1);lua_pop(L,1);
        std::lock_guard<std::mutex> lock(data->mutex);require_mutable(*data);if(data->revision!=revision)continue;
        set_entry_locked(*data,key,std::move(next));return 0;
    }throw std::runtime_error("concurrent SharedTable update retry limit exceeded");
    }catch(const std::exception& e){return luaL_error(L,"SharedTable.update: %s",e.what()),0;}
}
} // namespace

bool Key::operator<(const Key& other) const {
    if(numeric!=other.numeric)return numeric<other.numeric;
    return numeric?number<other.number:text<other.text;
}
std::shared_ptr<Data> SharedTablePool::create() {
    std::lock_guard<std::mutex> lock(mutex_);if(objects_.size()>=kMaxObjects)throw std::runtime_error("SharedTable session object limit exceeded");
    if(used_bytes_>kMaxPoolBytes-kDataOverhead)throw std::runtime_error("SharedTable session memory limit exceeded");
    auto data=std::make_shared<Data>();data->id=next_id_++;if(data->id==0)throw std::runtime_error("SharedTable session id exhausted");
    data->owner=this;used_bytes_+=kDataOverhead;objects_.emplace(data->id,data);return data;
}
SharedTablePool::~SharedTablePool() {
    std::unordered_map<uint64_t,std::shared_ptr<Data>> objects;
    {std::lock_guard<std::mutex> lock(mutex_);objects.swap(objects_);registered_.clear();used_bytes_=0;registered_bytes_=0;}
    // Clear every node before releasing the pool to break recursive graphs.
    for(auto& [id,data]:objects){(void)id;std::lock_guard<std::mutex> lock(data->mutex);data->entries.clear();data->accounted_bytes=0;data->owner=nullptr;}
}
std::shared_ptr<Data> SharedTablePool::find(uint64_t id) const {
    std::lock_guard<std::mutex> lock(mutex_);auto found=objects_.find(id);return found==objects_.end()?nullptr:found->second;
}
std::shared_ptr<Data> SharedTablePool::get_registered(const std::string& name) {
    if(name.size()>kMaxStringBytes)throw std::runtime_error("SharedTableRegistry name limit exceeded");
    std::lock_guard<std::mutex> lock(mutex_);auto found=registered_.find(name);
    if(found!=registered_.end())return found->second;
    if(objects_.size()>=kMaxObjects)throw std::runtime_error("SharedTable session object limit exceeded");
    size_t registration_cost=sizeof(std::string)+name.size()+32;
    if(used_bytes_>kMaxPoolBytes-kDataOverhead-registration_cost)throw std::runtime_error("SharedTable session memory limit exceeded");
    auto data=std::make_shared<Data>();data->id=next_id_++;
    if(data->id==0)throw std::runtime_error("SharedTable session id exhausted");
    data->owner=this;used_bytes_+=kDataOverhead+registration_cost;registered_bytes_+=registration_cost;
    objects_.emplace(data->id,data);registered_.emplace(name,data);return data;
}
void SharedTablePool::set_registered(const std::string& name,const std::shared_ptr<Data>& value) {
    if(name.size()>kMaxStringBytes)throw std::runtime_error("SharedTableRegistry name limit exceeded");
    std::lock_guard<std::mutex> lock(mutex_);
    if(!value){auto old=registered_.find(name);if(old!=registered_.end()){
        size_t cost=sizeof(std::string)+name.size()+32;registered_.erase(old);used_bytes_-=cost;registered_bytes_-=cost;}return;}
    auto found=objects_.find(value->id);
    if(found==objects_.end()||found->second!=value)throw std::runtime_error("SharedTable belongs to a different session pool");
    auto old=registered_.find(name);size_t old_cost=old==registered_.end()?0:sizeof(std::string)+name.size()+32;
    size_t new_cost=old==registered_.end()?sizeof(std::string)+name.size()+32:old_cost;
    size_t next=used_bytes_-old_cost;
    if(next>kMaxPoolBytes-new_cost)throw std::runtime_error("SharedTable session memory limit exceeded");
    used_bytes_=next+new_cost;registered_bytes_=registered_bytes_-old_cost+new_cost;registered_[name]=value;
}
void SharedTablePool::adjust_memory(size_t add,size_t release) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(release>used_bytes_)throw std::runtime_error("SharedTable session memory accounting error");
    size_t next=used_bytes_-release;if(add>kMaxPoolBytes-next)throw std::runtime_error("SharedTable session memory limit exceeded");used_bytes_=next+add;
}
void install(lua_State* L,const std::shared_ptr<SharedTablePool>& shared_pool,world_bindings::World* local_world) {
    if(!shared_pool)throw std::invalid_argument("SharedTablePool is required");
    auto context=[&](lua_CFunction function,const char* name) {
        lua_pushlightuserdata(L,shared_pool.get());lua_pushlightuserdata(L,local_world);
        lua_pushcclosure(L,function,name,2);
    };
    luaL_newmetatable(L,kMeta);
    context(index,"SharedTable.__index");lua_setfield(L,-2,"__index");
    context(newindex,"SharedTable.__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushcfunction(L,equal,"SharedTable.__eq");lua_setfield(L,-2,"__eq");
    context(iterator,"SharedTable.__iter");lua_setfield(L,-2,"__iter");
    lua_pushliteral(L,"SharedTable");lua_setfield(L,-2,"__type");lua_pushliteral(L,"SharedTable");lua_setfield(L,-2,"__metatable");lua_setreadonly(L,-1,true);lua_pop(L,1);
    lua_newtable(L);context(construct,"new");lua_setfield(L,-2,"new");
    context(clear,"clear");lua_setfield(L,-2,"clear");
    context(clone,"clone");lua_setfield(L,-2,"clone");
    context(clone_and_freeze,"cloneAndFreeze");lua_setfield(L,-2,"cloneAndFreeze");
    context(increment,"increment");lua_setfield(L,-2,"increment");
    context(is_frozen,"isFrozen");lua_setfield(L,-2,"isFrozen");
    context(size,"size");lua_setfield(L,-2,"size");
    context(update,"update");lua_setfield(L,-2,"update");
    lua_setreadonly(L,-1,true);lua_setglobal(L,"SharedTable");
}
bool is_shared_table(lua_State* L,int index){return read(L,index)!=nullptr;}
bool capture_reference(lua_State* L,int index,const SharedTablePool& shared_pool,nlohmann::json& encoded) {
    auto* value=read(L,index);if(!value)return false;
    if(shared_pool.find(value->data->id)!=value->data)throw std::runtime_error("SharedTable belongs to a different session pool");
    encoded=nlohmann::json{{"$shared_table_ref",value->data->id}};return true;
}
bool push_reference(lua_State* L,const nlohmann::json& encoded,const SharedTablePool& shared_pool) {
    if(!encoded.is_object()||encoded.size()!=1||!encoded.contains("$shared_table_ref")||!encoded["$shared_table_ref"].is_number_unsigned())return false;
    auto data=shared_pool.find(encoded["$shared_table_ref"].get<uint64_t>());if(!data)throw std::runtime_error("SharedTable reference is outside this session pool");push(L,data);return true;
}
std::shared_ptr<Data> get_data(lua_State* L,int index,const SharedTablePool& shared_pool) {
    auto* value=read(L,index);if(!value)throw std::runtime_error("SharedTable expected");
    if(shared_pool.find(value->data->id)!=value->data)throw std::runtime_error("SharedTable belongs to a different session pool");
    return value->data;
}
void push_table(lua_State* L,const std::shared_ptr<Data>& data,const SharedTablePool& shared_pool) {
    if(!data||shared_pool.find(data->id)!=data)throw std::runtime_error("SharedTable belongs to a different session pool");
    push(L,data);
}
} // namespace shared_table_bindings
