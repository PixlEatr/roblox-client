// Client-local engine singleton helpers and SharedTableRegistry service
// methods. Include in world_bindings.cpp after push_node and Node/World
// declarations; install the globals beside make_user_settings and dispatch
// missing_engine_global_index from instance_index.
#include "shared_table_bindings.hpp"
constexpr const char* kGlobalSettingsNodeId="local:engine:GlobalSettings";
constexpr const char* kPluginManagerNodeId="local:engine:PluginManager";

Node* ensure_engine_global_node(World& world,const char* id,const char* class_name) {
    auto current=world.nodes.find(id);
    if(current!=world.nodes.end()&&!current->second.destroyed)return &current->second;
    if(world.nodes.size()>=kMaxNodes+1000)throw std::runtime_error("engine global node limit exceeded");
    Node node;node.id=id;node.class_name=class_name;node.name=class_name;
    node.server_owned=false;node.attributes.set_known(true);node.collection_tags_known=true;
    node.origins=Json{{"Name","local"},{"ClassName","local"},{"Parent","local"}};
    auto [it,inserted]=world.nodes.insert_or_assign(node.id,std::move(node));
    if(inserted)world.order.push_back(id);
    return &it->second;
}
int make_global_settings(lua_State* L) {
    if(lua_gettop(L)!=0)return luaL_error(L,"settings takes no arguments"),0;
    auto* world=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    try{push_node(L,*world,ensure_engine_global_node(*world,kGlobalSettingsNodeId,"GlobalSettings"));return 1;}
    catch(const std::exception& e){return luaL_error(L,"settings unavailable: %s",e.what()),0;}
}
int make_plugin_manager(lua_State* L) {
    if(lua_gettop(L)!=0)return luaL_error(L,"PluginManager takes no arguments"),0;
    auto* world=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    try{push_node(L,*world,ensure_engine_global_node(*world,kPluginManagerNodeId,"PluginManager"));return 1;}
    catch(const std::exception& e){return luaL_error(L,"PluginManager unavailable: %s",e.what()),0;}
}
int global_settings_get_flag(lua_State* L) {
    auto* self=checked_handle(L,1);
    if(self->node->class_name!="GlobalSettings")return luaL_error(L,"GlobalSettings:GetFFlag requires GlobalSettings"),0;
    luaL_checkstring(L,2);
    return luaL_error(L,"engine flag values are unavailable in this local runtime"),0;
}
int global_settings_get_variable(lua_State* L) {
    auto* self=checked_handle(L,1);
    if(self->node->class_name!="GlobalSettings")return luaL_error(L,"GlobalSettings:GetFVariable requires GlobalSettings"),0;
    luaL_checkstring(L,2);
    return luaL_error(L,"engine variable values are unavailable in this local runtime"),0;
}
int plugin_manager_security_method(lua_State* L) {
    auto* self=checked_handle(L,1);
    if(self->node->class_name!="PluginManager")return luaL_error(L,"PluginManager method requires PluginManager"),0;
    return luaL_error(L,"PluginSecurity is unavailable in this local runtime"),0;
}
int shared_table_registry_get(lua_State* L) {
    auto* self=checked_handle(L,1);
    if(self->node->class_name!="SharedTableRegistry")return luaL_error(L,"GetSharedTable requires SharedTableRegistry"),0;
    if(!self->world->shared_table_pool)return luaL_error(L,"SharedTableRegistry session pool unavailable"),0;
    const char* name=luaL_checkstring(L,2);
    try{auto data=self->world->shared_table_pool->get_registered(name);
        shared_table_bindings::push_table(L,data,*self->world->shared_table_pool);return 1;
    }catch(const std::exception& e){return luaL_error(L,"GetSharedTable: %s",e.what()),0;}
}
int shared_table_registry_set(lua_State* L) {
    auto* self=checked_handle(L,1);
    if(self->node->class_name!="SharedTableRegistry")return luaL_error(L,"SetSharedTable requires SharedTableRegistry"),0;
    if(!self->world->shared_table_pool)return luaL_error(L,"SharedTableRegistry session pool unavailable"),0;
    const char* name=luaL_checkstring(L,2);
    try{std::shared_ptr<shared_table_bindings::Data> data;
        if(lua_type(L,3)!=LUA_TNIL)data=shared_table_bindings::get_data(L,3,*self->world->shared_table_pool);
        self->world->shared_table_pool->set_registered(name,data);return 0;
    }catch(const std::exception& e){return luaL_error(L,"SetSharedTable: %s",e.what()),0;}
}
bool missing_engine_global_index(lua_State* L,World&,Node& node,const std::string& key) {
    if(node.class_name=="GlobalSettings") {
        if(key=="GetFFlag"){lua_pushcfunction(L,global_settings_get_flag,"GetFFlag");return true;}
        if(key=="GetFVariable"){lua_pushcfunction(L,global_settings_get_variable,"GetFVariable");return true;}
    }
    if(node.class_name=="PluginManager"&&
       (key=="CreatePlugin"||key=="ExportPlace"||key=="ExportSelection")) {
        lua_pushcfunction(L,plugin_manager_security_method,key.c_str());return true;
    }
    if(node.class_name=="SharedTableRegistry") {
        if(key=="GetSharedTable"){lua_pushcfunction(L,shared_table_registry_get,"GetSharedTable");return true;}
        if(key=="SetSharedTable"){lua_pushcfunction(L,shared_table_registry_set,"SetSharedTable");return true;}
    }
    return false;
}
