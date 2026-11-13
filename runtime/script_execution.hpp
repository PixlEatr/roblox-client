#pragma once
#include "world_bindings.hpp"
#include "source_scheduler.hpp"
#include "closure_bindings.hpp"
#include "sha256.hpp"
#include <Luau/Compiler.h>
#include <lualib.h>
#include <cstring>
#include <cstddef>
#include <optional>
#include <unordered_set>
#include <vector>

// A root-derived source snapshot shared by Actor prepares in one update.
// Validation is lazy so the existing source-admission error still occurs at
// the first update_world_sources call, after earlier Actor preparation work.
class LazyValidatedRootSourceCatalog {
    using Json=world_bindings::Json;
    const Json* catalog_;
    mutable std::optional<size_t> validated_serialized_size_;
public:
    explicit LazyValidatedRootSourceCatalog(const Json& catalog):catalog_(&catalog){}
    LazyValidatedRootSourceCatalog(Json&&)=delete;

    bool has_entries() const noexcept { return catalog_->is_object()&&!catalog_->empty(); }
    const Json& checked_value() const {
        if(!validated_serialized_size_) {
            if(!catalog_->is_object()||catalog_->size()>4096)
                throw std::runtime_error("invalid root script source snapshot");
            const size_t serialized_size=catalog_->dump().size();
            if(serialized_size>4*1024*1024)
                throw std::runtime_error("invalid root script source snapshot");
            validated_serialized_size_=serialized_size;
        }
        return *catalog_;
    }
};

// Source/catalog loading belongs to the persistent VM, independent of movement.
class ScriptExecution {
    using Json=world_bindings::Json;
    lua_State* environment;
    world_bindings::World& world;
    SourceScheduler& scheduler;
    Json catalog=Json::object(),local_sources=Json::object();
    Json builtin_scripts=Json::array();
    std::string actor_id;
    int interface_ref=LUA_NOREF;
    bool initial_ready=true;

    static std::string decode_hex(const std::string& hex) {
        if(hex.size()%2)throw std::runtime_error("invalid bytecode encoding");
        std::string bytes;bytes.reserve(hex.size()/2);
        auto nibble=[](char c)->int{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;};
        for(size_t i=0;i<hex.size();i+=2){int a=nibble(hex[i]),b=nibble(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid bytecode encoding");bytes.push_back(char(a*16+b));}
        return bytes;
    }
    static void validate_original_bytecode(const Json& item) {
        if(!item.contains("original_bytecode_hex"))return;
        if(!item.contains("bytecode_hex")||!item["bytecode_hex"].is_string()
            ||!item.contains("original_sha256")||!item["original_sha256"].is_string()
            ||!item.contains("normalized_sha256")||!item["normalized_sha256"].is_string()
            ||!item["original_bytecode_hex"].is_string())
            throw std::runtime_error("invalid original bytecode metadata");
        const auto& original=item["original_bytecode_hex"].get_ref<const std::string&>();
        const auto& normalized=item["bytecode_hex"].get_ref<const std::string&>();
        if(original.empty()||original.size()>256*1024||original.size()%2
            ||normalized.empty()||normalized.size()>256*1024||normalized.size()%2)
            throw std::runtime_error("invalid original bytecode size");
        const auto bytes=decode_hex(original);
        if(source_crypto::hex(source_crypto::sha256(bytes))!=item["original_sha256"].get<std::string>())
            throw std::runtime_error("original bytecode digest mismatch");
        const auto executable=decode_hex(normalized);
        if(source_crypto::hex(source_crypto::sha256(executable))!=item["normalized_sha256"].get<std::string>())
            throw std::runtime_error("normalized bytecode digest mismatch");
    }
    static void validate_builtin_bytecode(const Json& item) {
        if(!item.is_object()||!item.contains("source")||!item["source"].is_string()
            ||item.value("bytecode_provenance","")!="pinned_builtin_luau_0_705"
            ||item.value("bytecode_compiler_commit","")!="93c83a46d7c6c1b040e58e82ef167340a7db1aef"
            ||item.value("bytecode_version",0)!=6||item.value("bytecode_type_version",0)!=3
            ||!item.contains("repository_path")||!item["repository_path"].is_string()
            ||item["repository_path"].get<std::string>().compare(0,22,"scripts/PlayerScripts/")!=0)
            throw std::runtime_error("invalid pinned builtin bytecode provenance");
        const auto source=item["source"].get<std::string>();
        const auto& encoded=item.at("bytecode_hex").get_ref<const std::string&>();
        if(encoded.size()>1024*1024)throw std::runtime_error("pinned builtin bytecode exceeds limit");
        const auto bytes=decode_hex(encoded);
        if(bytes.size()<2||static_cast<unsigned char>(bytes[0])!=6||static_cast<unsigned char>(bytes[1])!=3
            ||item.value("source_sha256","")!=source_crypto::hex(source_crypto::sha256(source))
            ||item.value("bytecode_sha256","")!=source_crypto::hex(source_crypto::sha256(bytes)))
            throw std::runtime_error("pinned builtin bytecode hash or header mismatch");
    }
    static bool has_builtin_bytecode(const Json& item) {
        return item.is_object()&&item.contains("bytecode_provenance");
    }

    std::string instance_id(lua_State* L,int index) {
        Json value=world_bindings::capture_value(L,world,index);
        if(!value.is_object()||!value.contains("$ref"))throw std::runtime_error("script Instance required");
        auto id=value.at("$ref").get<std::string>();auto it=world.nodes.find(id);
        if(it==world.nodes.end()||(it->second.class_name!="LocalScript"&&it->second.class_name!="ModuleScript"&&it->second.class_name!="Script"))
            throw std::runtime_error("script Instance required");
        return id;
    }
    std::string script_debug_path(const std::string& id) {
        std::vector<std::string> components;
        std::unordered_set<std::string> visited;
        std::string cursor=id;
        while(!cursor.empty()&&cursor!=world.game_id&&visited.insert(cursor).second) {
            auto node=world.nodes.find(cursor);
            if(node==world.nodes.end()||node->second.class_name=="DataModel")break;
            components.push_back(world_bindings::instance_display_name(world,cursor));
            std::string parent=node->second.parent;
            auto local=world.local_properties.find(cursor);
            if(local!=world.local_properties.end()&&local->second.is_object()) {
                auto property=local->second.find("Parent");
                if(property!=local->second.end()) {
                    if(property->is_object()&&property->contains("$ref")&&(*property)["$ref"].is_string())
                        parent=(*property)["$ref"].get<std::string>();
                    else parent.clear();
                }
            }
            cursor=std::move(parent);
        }
        std::string path;
        bool first=true;
        for(auto component=components.rbegin();component!=components.rend();++component) {
            if(!first)path.push_back('.');
            path+=*component;
            first=false;
        }
        return path;
    }
    static int bridge(lua_State* L) {
        auto& self=*static_cast<ScriptExecution*>(lua_touserdata(L,lua_upvalueindex(1)));
        try{return self.invoke(L,lua_tointeger(L,lua_upvalueindex(2)));}
        catch(const std::exception& error){luaL_error(L,"script runtime: %s",error.what());return 0;}
    }
    int invoke(lua_State* L,int operation) {
        if(operation==0) {
            for(auto& [id,node]:world.nodes) {
                if(node.script_state.code().is_object()&&node.script_state.code().read().value("authored_override",false))continue;
                if(local_sources.contains(id))node.script_state.code_mut()=local_sources[id];
                else if(catalog.contains(id))node.script_state.code_mut()=catalog[id];
                else if(!node.script_state.origin().empty()&&(!node.script_state.code().is_object()||(!node.script_state.code().contains("source")&&!node.script_state.code().contains("bytecode_hex")))) {
                    auto origin=world.nodes.find(node.script_state.origin());if(origin!=world.nodes.end()&&origin->second.script_state.code().is_object())node.script_state.code_mut()=origin->second.script_state.code();
                }
            }
            Json rows=world_bindings::script_nodes(world);lua_createtable(L,rows.size(),0);int table=lua_gettop(L),index=0;
            for(const auto& row:rows) {
                lua_createtable(L,0,8);auto id=row.at("id").get<std::string>();
                world_bindings::push_instance(L,world,id);lua_setfield(L,-2,"instance");
                lua_pushlstring(L,id.data(),id.size());lua_setfield(L,-2,"id");
                lua_pushboolean(L,row.at("destroyed").get<bool>());lua_setfield(L,-2,"destroyed");
                lua_pushboolean(L,row.value("destroy_cleanup_complete",false));lua_setfield(L,-2,"destroyCleanupComplete");
                for(const char* name:{"Disabled","Enabled"})if(row.contains(name)&&row[name].is_boolean()) {
                    lua_pushboolean(L,row[name].get<bool>());lua_setfield(L,-2,name);
                }
                auto node=world.nodes.find(id);
                if(node!=world.nodes.end())lua_pushinteger(L,static_cast<lua_Integer>(node->second.script_state.disabled_generation()));
                else lua_pushinteger(L,0);
                lua_setfield(L,-2,"disabledGeneration");
                if(row.contains("RunContext")) {
                    const Json* run_context=&row["RunContext"];
                    if(run_context->is_object()&&run_context->contains("$enum")
                        &&run_context->at("$enum").is_object()
                        &&run_context->at("$enum").contains("value"))run_context=&run_context->at("$enum").at("value");
                    if(run_context->is_number()){lua_pushnumber(L,run_context->get<double>());lua_setfield(L,-2,"RunContext");}
                }
                lua_rawseti(L,table,++index);
            }
            return 1;
        }
        if(operation==1) {
            auto id=instance_id(L,1);luaL_checktype(L,2,LUA_TTABLE);
            auto& node=world.nodes.at(id);
            const Json* item=node.script_state.code().is_object()&&node.script_state.code().read().value("authored_override",false)?&node.script_state.code().read():local_sources.contains(id)?&local_sources[id]:catalog.contains(id)?&catalog[id]:node.script_state.code().is_object()?&node.script_state.code().read():nullptr;
            if((!item||(!item->contains("source")&&!item->contains("bytecode_hex")))&&!node.script_state.origin().empty()) {
                std::string origin=node.script_state.origin();
                for(int depth=0;depth<64&&!origin.empty();++depth) {
                    auto it=world.nodes.find(origin);if(it==world.nodes.end())break;
                    const Json* inherited=local_sources.contains(origin)?&local_sources[origin]:catalog.contains(origin)?&catalog[origin]:it->second.script_state.code().is_object()?&it->second.script_state.code().read():nullptr;
                    if(inherited&&(inherited->contains("source")||inherited->contains("bytecode_hex"))){item=inherited;break;}
                    origin=it->second.script_state.origin();
                }
            }
            if(!item||(!item->contains("source")&&!item->contains("bytecode_hex"))) {
                lua_pushnil(L);auto reason=item?item->value("reason","code_unavailable"):"code_unavailable";
                lua_pushstring(L,reason.c_str());lua_pushboolean(L,0);return 3;
            }
            std::string code,source;
            if(item->contains("source")){
                source=item->at("source").get<std::string>();
                if(has_builtin_bytecode(*item)){validate_builtin_bytecode(*item);code=decode_hex(item->at("bytecode_hex").get<std::string>());}
                else code=Luau::compile(source);
            }
            else {
                const auto hex=item->at("bytecode_hex").get<std::string>();code.reserve(hex.size()/2);
                auto nibble=[](char c)->int{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;};
                for(size_t i=0;i<hex.size();i+=2){int a=nibble(hex[i]),b=nibble(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid bytecode encoding");code.push_back(char(a*16+b));}
            }
            auto name="="+script_debug_path(id);
            if(world.load_script(L,name.c_str(),code,source)!=0){lua_pushnil(L);lua_insert(L,-2);lua_pushboolean(L,1);return 3;}
            lua_pushvalue(L,2);lua_setfenv(L,-2);return 1;
        }
        if(operation==2) {luaL_checktype(L,1,LUA_TFUNCTION);std::string owner=luaL_checkstring(L,2);luaL_checktype(L,3,LUA_TTABLE);scheduler.spawn_owned(L,1,owner,3);return 1;}
        if(operation==3) {
            std::string owner=luaL_checkstring(L,1);
            lua_State* deferred_main=lua_isthread(L,2)?lua_tothread(L,2):nullptr;
            lua_pushboolean(L,world_bindings::stop_script_tasks(L,world,owner,deferred_main));return 1;
        }
        if(operation==4) {luaL_checktype(L,1,LUA_TTHREAD);lua_pushboolean(L,scheduler.completion(lua_tothread(L,1))!=LUA_YIELD);return 1;}
        if(operation==5) {
            auto id=instance_id(L,1);size_t length=0;const char* text=luaL_checklstring(L,2,&length);
            if(length>128*1024)throw std::runtime_error("script source exceeds 128 KiB");
            Json proposed=local_sources;proposed[id]=Json{{"source",std::string(text,length)}};
            if(proposed.dump().size()>4*1024*1024)throw std::runtime_error("local source catalog exceeds 4 MiB");
            local_sources=std::move(proposed);world.nodes.at(id).script_state.code_mut()=local_sources[id];return 0;
        }
        if(operation==6){push_method(L,"status");lua_call(L,0,1);return 1;}
        if(operation==7){auto id=instance_id(L,1);lua_pushlstring(L,id.data(),id.size());return 1;}
        if(operation==8){world_bindings::push_player_scripts(L,world);return 1;}
        if(operation==9){auto id=instance_id(L,1);auto& node=world.nodes.at(id);const Json* code=local_sources.contains(id)?&local_sources[id]:catalog.contains(id)?&catalog[id]:node.script_state.code().is_object()?&node.script_state.code().read():nullptr;
            bool ready=code&&(code->contains("source")||code->contains("bytecode_hex")||(code->value("reason","")!="verification_pending"&&code->value("reason","")!="code_unavailable"));lua_pushboolean(L,ready);return 1;}
        if(operation==10){lua_pushboolean(L,initial_ready);return 1;}
        if(operation==11){return world_bindings::clone_client_template(L);}
        if(operation==12) {
            lua_createtable(L,builtin_scripts.size(),0);int list=lua_gettop(L),index=0;
            for(const auto& entry:builtin_scripts) {
                lua_createtable(L,0,10);
                lua_createtable(L,entry.at("path").size(),0);int path=lua_gettop(L),part=0;
                for(const auto& segment:entry.at("path")){const auto& name=segment.get_ref<const std::string&>();lua_pushlstring(L,name.data(),name.size());lua_rawseti(L,path,++part);}
                lua_setfield(L,-2,"path");lua_pushstring(L,entry.at("class_name").get_ref<const std::string&>().c_str());lua_setfield(L,-2,"class_name");
                if(entry.contains("source")){const auto& source=entry.at("source").get_ref<const std::string&>();lua_pushlstring(L,source.data(),source.size());lua_setfield(L,-2,"source");}
                for(const char* field:{"repository_path","source_sha256","bytecode_hex","bytecode_sha256","bytecode_provenance","bytecode_compiler_commit"})if(entry.contains(field)){
                    const auto& value=entry.at(field).get_ref<const std::string&>();lua_pushlstring(L,value.data(),value.size());lua_setfield(L,-2,field);
                }
                for(const char* field:{"bytecode_version","bytecode_type_version"})if(entry.contains(field)){lua_pushinteger(L,entry.at(field).get<int>());lua_setfield(L,-2,field);}
                lua_rawseti(L,list,++index);
            }
            return 1;
        }
        if(operation==14) {
            auto id=instance_id(L,1);luaL_checktype(L,2,LUA_TTABLE);
            lua_getfield(L,2,"path");
            if(!lua_istable(L,-1))throw std::runtime_error("invalid builtin registration path");
            const int path_index=lua_absindex(L,-1);
            const int path_length=static_cast<int>(lua_objlen(L,path_index));
            const Json* trusted=nullptr;
            for(const auto& entry:builtin_scripts)if(entry.at("path").size()==static_cast<size_t>(path_length)) {
                bool match=true;
                for(int i=1;i<=path_length;++i){lua_rawgeti(L,path_index,i);size_t size=0;const char* part=lua_type(L,-1)==LUA_TSTRING?lua_tolstring(L,-1,&size):nullptr;
                    const auto& expected=entry.at("path").at(i-1).get_ref<const std::string&>();
                    match=part&&size==expected.size()&&std::memcmp(part,expected.data(),size)==0;lua_pop(L,1);if(!match)break;}
                if(match){trusted=&entry;break;}
            }
            lua_pop(L,1);
            if(!trusted||!has_builtin_bytecode(*trusted)||!trusted->contains("bytecode_hex"))throw std::runtime_error("builtin bytecode is not in the pinned bundle");
            validate_builtin_bytecode(*trusted);
            for(const char* field:{"class_name","source","repository_path","source_sha256","bytecode_hex","bytecode_sha256","bytecode_provenance","bytecode_compiler_commit"}) {
                lua_getfield(L,2,field);size_t size=0;const char* value=lua_type(L,-1)==LUA_TSTRING?lua_tolstring(L,-1,&size):nullptr;
                const bool matches=trusted->contains(field)&&value&&trusted->at(field).is_string()
                    &&size==trusted->at(field).get_ref<const std::string&>().size()
                    &&std::memcmp(value,trusted->at(field).get_ref<const std::string&>().data(),size)==0;
                lua_pop(L,1);if(!matches)throw std::runtime_error("builtin registration differs from pinned bundle");
            }
            for(const char* field:{"bytecode_version","bytecode_type_version"}) {
                lua_getfield(L,2,field);const bool matches=trusted->contains(field)&&lua_isnumber(L,-1)
                    &&lua_tointeger(L,-1)==trusted->at(field).get<int>();lua_pop(L,1);
                if(!matches)throw std::runtime_error("builtin registration differs from pinned bundle");
            }
            Json proposed=local_sources;proposed[id]=*trusted;
            if(proposed.dump().size()>4*1024*1024)throw std::runtime_error("local source catalog exceeds 4 MiB");
            local_sources=std::move(proposed);world.nodes.at(id).script_state.code_mut()=local_sources[id];return 0;
        }
        if(operation==13) {
            Json value=world_bindings::capture_value(L,world,1);
            if(!value.is_object()||!value.contains("$ref"))throw std::runtime_error("Instance required");
            const auto id=value.at("$ref").get<std::string>();
            if(world.nodes.find(id)==world.nodes.end())throw std::runtime_error("Instance required");
            lua_pushlstring(L,id.data(),id.size());return 1;
        }
        throw std::runtime_error("unknown script runtime operation");
    }
    void push_bridge(lua_State* L,int operation) {
        lua_pushlightuserdata(L,this);lua_pushinteger(L,operation);lua_pushcclosurek(L,bridge,"script runtime",2,nullptr);
    }
    void push_method(lua_State* L,const char* name){lua_getref(L,interface_ref);lua_getfield(L,-1,name);lua_remove(L,-2);}
public:
    ScriptExecution(lua_State* L,world_bindings::World& w,SourceScheduler& tasks,const Json& snapshot,
                    std::string scoped_actor_id={})
        :environment(L),world(w),scheduler(tasks),actor_id(std::move(scoped_actor_id)) {
        initial_ready=snapshot.value("initial_replication_complete",true);
        builtin_scripts=snapshot.value("builtin_player_scripts",Json::array());
        if(!builtin_scripts.is_array()||builtin_scripts.size()>256||builtin_scripts.dump().size()>2*1024*1024)throw std::runtime_error("invalid builtin player script bundle");
        for(const auto& entry:builtin_scripts) {
            if(!entry.is_object()||!entry.contains("path")||!entry["path"].is_array()||entry["path"].empty()||entry["path"].size()>16)throw std::runtime_error("invalid builtin script path");
            for(const auto& part:entry["path"])if(!part.is_string()||part.get_ref<const std::string&>().empty()||part.get_ref<const std::string&>().size()>128)throw std::runtime_error("invalid builtin script name");
            const auto cls=entry.value("class_name","");
            if(cls!="LocalScript"&&cls!="ModuleScript"&&cls!="Folder")throw std::runtime_error("invalid builtin script class");
            if(cls!="Folder"&&(!entry.contains("source")||!entry["source"].is_string()||entry["source"].get_ref<const std::string&>().size()>128*1024))throw std::runtime_error("invalid builtin script source");
            if(entry.contains("bytecode_hex"))validate_builtin_bytecode(entry);
        }
        Json snapshot_sources=snapshot.value("script_sources",Json::object());
        Json host_sources=Json::object();
        if(snapshot_sources.is_object())for(auto it=snapshot_sources.begin();it!=snapshot_sources.end();++it)
            if(it.value().is_object()&&!it.value().contains("bytecode_provenance")&&!it.value().contains("bytecode_compiler_commit"))
                host_sources[it.key()]=it.value();
        update(host_sources);
        update_world_sources(snapshot_sources);
        static constexpr const char* bootstrap=R"SCRIPTS(
local actorId, actorInstanceId, scan, compile, spawn, stop, ended, identify, playerScripts, codeReady, initialReady, cloneTemplate, builtinScripts, registerSource, registerBuiltinSource = ...
local executor = getfenv()
-- Copy the engine root once and make it the actual fallback for authored and
-- game scopes. The child environment's inherited table is the main VM's
-- ordinary globals; direct child entries also include executor-installed
-- APIs, so import only the runtime services game scripts need.
local base = {}
local inherited=getmetatable(executor).__index
for key,value in inherited do base[key]=value end
for _,key in {"task","wait","spawn","delay","time","elapsedTime","tick",
              "Wait","Spawn","Delay","ElapsedTime","ypcall","SharedTable"} do
    local value=rawget(executor,key)
    if value~=nil then base[key]=value end
end
-- debug.info and debug.traceback are the original Luau functions. Keep the
-- executor's inspection extensions in its own table.
local executorDebug=rawget(executor,"debug")
if type(executorDebug)=="table" then
    local engineDebug={}
    for _,key in {"info","traceback","profilebegin","profileend"} do
        local value=rawget(executorDebug,key)
        if type(value)=="function" then engineDebug[key]=value end
    end
    base.debug=engineDebug
end
base._G={};base.shared=shared
setmetatable(executor,{__index=base})
function getrenv() return base end
local modules, scripts = {}, {}
local copied = setmetatable({}, {__mode="k"})
local bootstrapErrors = {}
local builtinsInstalled = false
local function installBuiltins(target)
    if builtinsInstalled or not target then return end
    local paths, overridden = {}, {}
    for _, row in builtinScripts() do
        local path = row.path
        local root = path[1]
        if #path == 1 and target:FindFirstChild(root) then overridden[root] = true end
        if not overridden[root] then
            local parent = #path == 1 and target or paths[table.concat(path, "/", 1, #path-1)]
            assert(parent, "Builtin player script parent is missing")
            local instance = Instance.new(row.class_name)
            instance.Name = path[#path]
            if row.source then
                if row.bytecode_hex then registerBuiltinSource(instance,row)
                else registerSource(instance, row.source) end
            end
            instance.Parent = parent
            paths[table.concat(path, "/")] = instance
        end
    end
    builtinsInstalled = true
end
local function mirror(source, target)
    if not source or not target then return end
    local seen = copied[target]
    if not seen then seen = {}; copied[target] = seen end
    for _, child in source:GetChildren() do
        if not seen[child] then
            if target:FindFirstChild(child.Name) then seen[child] = true
            else
                -- Instance topology does not depend on source admission.
                -- Missing code remains blocked by compile/require; clones
                -- retain their origin so later verified code can reach them.
                local ok, clone = pcall(cloneTemplate, child)
                if ok then
                    seen[child] = true
                    if clone then clone.Parent = target end
                elseif not bootstrapErrors[child] then
                    bootstrapErrors[child] = tostring(clone)
                    warn("Client template: " .. tostring(clone))
                end
            end
        end
    end
end
local function prepareTemplates()
    -- Actor VMs consume the root World's already materialized topology and
    -- source data. Cloning client templates here would create a second set of
    -- PlayerScripts/UI instances with unrelated identities.
    if actorId then return end
    if not initialReady() then return end
    local players = game:FindService("Players")
    local player = players and players.LocalPlayer
    if not player then return end
    local starter = game:FindService("StarterPlayer")
    local scripts = playerScripts()
    mirror(starter and starter:FindFirstChild("StarterPlayerScripts"), scripts)
    installBuiltins(scripts)
    local ok, character = pcall(function()return player.Character end)
    if ok and character and character:IsDescendantOf(game) then
        mirror(starter and starter:FindFirstChild("StarterCharacterScripts"), character)
    end
    mirror(game:FindService("StarterGui"), player:FindFirstChildOfClass("PlayerGui"))
    mirror(game:FindService("StarterPack"), player:FindFirstChildOfClass("Backpack"))
end
local function environmentFor(instance)
    return setmetatable({script=instance}, {__index=base})
end
local function finishBroken(entry)
    if entry.state == "loading" and entry.thread and ended(entry.thread) then
        entry.state, entry.error, entry.thread = "failed", "Module task terminated before returning", nil
    end
end
function require(module)
    if typeof(module) ~= "Instance" or not module:IsA("ModuleScript") then
        error("require expects a ModuleScript Instance; asset IDs and file paths are unavailable", 2)
    end
    local moduleId = identify(module)
    local entry = modules[moduleId]
    if not entry then
        local scope = environmentFor(module)
        local fn, message, hasCode = compile(module, scope)
        while not fn and message == "verification_pending" do
            task.wait(0.005)
            if modules[moduleId] then return require(module) end
            fn, message, hasCode = compile(module, scope)
        end
        if not fn then
            if hasCode then modules[moduleId] = {state="failed", error=tostring(message), id=moduleId, instance=module} end
            error("Module code unavailable: " .. tostring(message), 2)
        end
        entry = {state="loading", id=moduleId,instance=module,scope=scope}
        modules[moduleId] = entry
        entry.thread = spawn(function()
            local result = table.pack(pcall(fn))
            if not result[1] then
                entry.state, entry.error = "failed", tostring(result[2])
            elseif result.n ~= 2 then
                entry.state, entry.error = "failed", "Module code must return exactly one value"
            else
                entry.state, entry.value = "loaded", result[2]
            end
        end, "module:" .. entry.id, scope)
    end
    while entry.state == "loading" do
        finishBroken(entry)
        if entry.state == "loading" then task.wait(0.001) end
    end
    if entry.state == "failed" then error(entry.error, 2) end
    return entry.value
end
base.require=require
executor.require=nil
function getscriptclosure(instance)
    local fn,message=compile(instance,environmentFor(instance));assert(fn,message);return fn
end
getscriptfunction=getscriptclosure
function getloadedmodules()
    local result={};for _,entry in modules do if entry.state=="loaded" then table.insert(result,entry.instance) end end;return result
end
function getrunningscripts()
    local result={};for instance,entry in scripts do
        local destroyedAlive=entry.diagnostic and entry.diagnostic.destroyed
            and entry.diagnostic.destroy_cleanup_complete and entry.thread and not ended(entry.thread)
        if entry.active and entry.thread and (not entry.diagnostic or not entry.diagnostic.destroyed or destroyedAlive) then
            table.insert(result,instance)
        end
    end
    for _,entry in modules do if entry.state=="loading" or entry.state=="loaded" then table.insert(result,entry.instance) end end
    return result
end
function getsenv(instance)
    local id=identify(instance)
    local entry=modules[id]
    if not entry then for _,candidate in scripts do if candidate.id==id then entry=candidate;break end end end
    assert(entry and entry.scope,"script has no instantiated environment");return entry.scope
end
local function read(instance, key)
    local ok, value = pcall(function() return instance[key] end)
    if ok then return value end
end
local function eligibility(row, player, character)
    local instance = row.instance
    if row.destroyed then return false, "destroyed" end
    if not instance:IsDescendantOf(game) then return false, "outside_game" end
    if row.Disabled == true then return false, "disabled" end
    if row.Enabled == false then return false, "enabled_false" end
    if actorId then
        if instance.ClassName ~= "LocalScript" and not (instance.ClassName == "Script" and row.RunContext == 2) then return false, "actor_class_or_runcontext" end
        local parent = instance.Parent
        while parent and parent ~= game do
            if parent.ClassName == "Actor" then
                if actorInstanceId(parent) == actorId then return true, "eligible" end
                return false, "different_actor"
            end
            parent = parent.Parent
        end
        return false, "no_actor_ancestor"
    end
    local scope = instance.Parent
    while scope and scope ~= game do
        if scope.ClassName == "Actor" then return false, "actor_owned" end
        scope = scope.Parent
    end
    if instance.ClassName == "Script" then
        if row.RunContext == 2 then return true, "eligible" end
        return false, "script_runcontext_not_client"
    end
    if instance.ClassName ~= "LocalScript" then return false, "unsupported_class" end
    local parent = instance.Parent
    while parent and parent ~= game do
        if parent.ClassName == "ReplicatedFirst" or parent == character then return true, "eligible" end
        if player and parent.Parent == player and (parent.ClassName == "PlayerScripts" or parent.ClassName == "PlayerGui" or parent.ClassName == "Backpack") then return true, "eligible" end
        parent = parent.Parent
    end
    return false, "local_script_outside_client_container"
end
local function continueDetachedMain(row, entry)
    if not entry or not entry.active or entry.state ~= "running" or not entry.thread then return false end
    if row.destroyed or row.Disabled == true or row.Enabled == false or ended(entry.thread) then return false end

    local instance = row.instance
    if instance.Parent ~= nil then return false end
    -- Roblox documents Parent=nil continuation on Script. Applying it to
    -- LocalScript relies on its documented Script inheritance, pending a
    -- native LocalScript lifecycle check. This keeps only an existing main
    -- thread alive in the VM that already owns it; it never starts detached code.
    if instance.ClassName == "LocalScript" then return true end
    return instance.ClassName == "Script" and row.RunContext == 2
end
local function status()
    local result = {local_scripts=0, running=0, completed=0, failed=0, blocked=0, stopped=0,
                    modules_loaded=0, modules_loading=0, modules_failed=0, details={}}
    result.bootstrap_errors = 0
    for _ in bootstrapErrors do result.bootstrap_errors += 1 end
    for instance, entry in scripts do
        result.local_scripts += 1
        if result[entry.state] ~= nil then result[entry.state] += 1 end
        if #result.details < 128 then
            local row=entry.diagnostic or {}
            table.insert(result.details, {id=entry.id, name=instance.Name, state=entry.state, error=entry.error,
                destroyed=row.destroyed, destroy_cleanup_complete=row.destroy_cleanup_complete,
                parent_id=row.parent_id, parent_id_status=row.parent_id_status,
                Disabled=row.Disabled, Disabled_present=row.Disabled_present,
                Enabled=row.Enabled, Enabled_present=row.Enabled_present,
                RunContext=row.RunContext, RunContext_present=row.RunContext_present,
                eligible=row.eligible, eligibility_reason=row.eligibility_reason,
                stop_reason=entry.stop_reason})
        end
    end
    for _, entry in modules do
        finishBroken(entry)
        local key = "modules_" .. entry.state
        result[key] = (result[key] or 0) + 1
    end
    return result
end
local function reconcile()
    -- Root template preparation can add cloned scripts, so it needs a census
    -- first. Actor VMs never prepare templates and expose no executor hooks;
    -- their sole census remains after the service/Character reads below.
    if not actorId then scan() end -- Refresh code retained by cloned scripts.
    prepareTemplates()
    -- Script starts are queued, so this context stays stable during the scan.
    -- Resolve it once: a missing Character can otherwise scan the whole world
    -- for every LocalScript while initial replication is still in progress.
    local players = game:FindService("Players")
    local player = players and players.LocalPlayer
    local character = player and read(player, "Character")
    local present, starts = {}, 0
    for _, row in scan() do
        local instance = row.instance
        if instance.ClassName ~= "ModuleScript" then
            present[instance] = true
            local entry = scripts[instance]
            local shouldRun, eligibilityReason = eligibility(row, player, character)
            local parentId, parentIdStatus = "unavailable", "unavailable"
            local parentOk, parent = pcall(function() return instance.Parent end)
            if parentOk and not parent then parentId, parentIdStatus = "nil", "nil" end
            if parentOk and parent then
                -- The shared Instance-ID bridge accepts service/container
                -- parents; identify intentionally requires a script Instance.
                local idOk, value = pcall(actorInstanceId, parent)
                if idOk then parentId, parentIdStatus = value, "known" end
            end
            local diagnostic={destroyed=row.destroyed, parent_id=parentId, parent_id_status=parentIdStatus,
                destroy_cleanup_complete=row.destroyCleanupComplete,
                Disabled=row.Disabled, Disabled_present=row.Disabled ~= nil,
                Enabled=row.Enabled, Enabled_present=row.Enabled ~= nil,
                RunContext=row.RunContext, RunContext_present=row.RunContext ~= nil,
                eligible=shouldRun,
                eligibility_reason=eligibilityReason}
            if entry then entry.diagnostic=diagnostic end
            local locallyDestroyedBoundary=row.destroyed and row.destroyCleanupComplete
            if locallyDestroyedBoundary then
                -- Local Destroy/ClearAllChildren already performed native's
                -- one-shot owner-list drain synchronously. Do not run another
                -- owner sweep here: a currently executing script main may
                -- have registered later waits/tasks after that boundary.
                if entry and entry.active and entry.thread and ended(entry.thread) then
                    entry.active, entry.pendingStop, entry.thread = false, nil, nil
                    entry.stop_reason = "destroyed"
                    if entry.state == "running" then entry.state = "stopped" end
                end
            else
            local detachedMainContinues = not shouldRun and continueDetachedMain(row, entry)
            local disabled = row.Disabled == true or row.Enabled == false
            local generation = row.disabledGeneration or 0
            if entry and entry.generation ~= generation then
                entry.pendingStop, entry.generation = true, generation
            end
            if entry and entry.active and entry.pendingStop then
                -- Re-enable before the old main reaches a real yield: let it
                -- finish its documented Disabled grace period, then one fresh
                -- top-level run may start below. Other eligibility loss and
                -- destruction still stop the owner immediately.
                local mayContinue = not row.destroyed and (disabled or shouldRun or detachedMainContinues)
                if stop("script:" .. entry.id, mayContinue and entry.thread or nil) then
                    entry.state = "running"
                else
                    entry.active, entry.pendingStop, entry.state, entry.thread = false, nil, "stopped", nil
                    entry.stop_reason = row.destroyed and "destroyed" or disabled and "disabled" or eligibilityReason or "lifecycle_stop"
                end
            end
            if entry and entry.active and not shouldRun and not detachedMainContinues and not entry.pendingStop then
                local deferred = stop("script:" .. entry.id, not row.destroyed and disabled and entry.thread or nil)
                if deferred then
                    entry.pendingStop, entry.state = true, "running"
                else
                    entry.active, entry.state, entry.thread = false, "stopped", nil
                    entry.stop_reason = row.destroyed and "destroyed" or disabled and "disabled" or eligibilityReason or "ineligible"
                end
            end
            if shouldRun and (not entry or not entry.active) and starts < 8 then
                entry = entry or {id=row.id}
                scripts[instance] = entry
                entry.diagnostic=diagnostic
                entry.generation, entry.pendingStop = generation, nil
                local scope=environmentFor(instance)
                local fn, message, hasCode = compile(instance, scope)
                if not fn then
                    entry.state, entry.error = hasCode and "failed" or "blocked", tostring(message)
                    if hasCode then entry.active = true; warn(instance.Name .. ": " .. tostring(message)); starts += 1 end
                else
                    entry.scope=scope
                    entry.active, entry.state, entry.error = true, "running", nil
                    starts += 1
                    entry.thread = spawn(function()
                        local ok, message = pcall(fn)
                        entry.state = ok and "completed" or "failed"
                        entry.error = not ok and tostring(message) or nil
                        if not ok then warn(instance.Name .. ": " .. tostring(message)) end
                    end, "script:" .. entry.id, scope)
                end
            elseif entry and entry.state == "running" and entry.thread and ended(entry.thread) then
                entry.state, entry.error = "failed", "Script task terminated before completing"
            end
            end
        end
    end
    for instance, entry in scripts do
        if not present[instance] and entry.active then
            stop("script:" .. entry.id)
            entry.active, entry.state, entry.thread = false, "stopped", nil
            entry.stop_reason = "removed_from_snapshot"
        end
    end
    return status()
end
return {reconcile=reconcile, status=status}
)SCRIPTS";
        std::string code=Luau::compile(bootstrap);
        if(world.load_script(L,"=script-runtime",code,bootstrap)!=0)throw std::runtime_error("cannot load script runtime");
        if(actor_id.empty())lua_pushnil(L);else lua_pushlstring(L,actor_id.data(),actor_id.size());
        for(int operation:{13,0,1,2,3,4,7,8,9,10,11,12,5,14})push_bridge(L,operation);
        if(lua_pcall(L,15,1,0)!=0)throw std::runtime_error(lua_tostring(L,-1));
        interface_ref=lua_ref(L,-1);lua_pop(L,1);
        for(const char* name:{"getrenv","getscriptclosure","getscriptfunction","getloadedmodules","getrunningscripts","getsenv"}) {
            lua_getglobal(L,name);mark_executor_closure(L,-1);lua_pop(L,1);
        }
        lua_getglobal(L,"client");lua_setreadonly(L,-1,false);
        push_bridge(L,5);lua_setfield(L,-2,"registerScriptSource");
        push_bridge(L,6);lua_setfield(L,-2,"scriptStatus");lua_setreadonly(L,-1,true);lua_pop(L,1);
        try {
            scheduler.set_lifecycle_cleanup(this,[this](const std::string& owner) {
                world_bindings::stop_script_tasks(environment,world,owner,scheduler.deferred_script_main(owner));
            },[this](const std::string& owner) {
                if(owner.rfind("script:",0)!=0)return false;
                const auto id=owner.substr(7);
                auto node=world.nodes.find(id);
                return node!=world.nodes.end()&&!node->second.destroyed;
            });
            world.script_disabled_transition=[this](lua_State* from,const std::string& id) {
                world_bindings::stop_script_tasks(from,world,"script:"+id,nullptr,true);
            };
            world.script_disabled_transition_owner=this;
            world.script_destroy_transition=[this](lua_State* from,const std::string& id) {
                world_bindings::stop_script_tasks(from,world,"script:"+id);
            };
            world.script_destroy_transition_owner=this;
        } catch(...) {
            scheduler.clear_lifecycle_cleanup(this);
            if(world.script_disabled_transition_owner==this) {
                world.script_disabled_transition={};
                world.script_disabled_transition_owner=nullptr;
            }
            if(world.script_destroy_transition_owner==this) {
                world.script_destroy_transition={};
                world.script_destroy_transition_owner=nullptr;
            }
            throw;
        }
    }
    ~ScriptExecution() {
        if(world.script_disabled_transition_owner==this) {
            world.script_disabled_transition={};
            world.script_disabled_transition_owner=nullptr;
        }
        scheduler.clear_lifecycle_cleanup(this);
        if(world.script_destroy_transition_owner==this) {
            world.script_destroy_transition={};
            world.script_destroy_transition_owner=nullptr;
        }
    }
    void update(const Json& incoming) {
        if(!incoming.is_object()||incoming.size()>4096||incoming.dump().size()>4*1024*1024)throw std::runtime_error("invalid script source catalog");
        for(auto it=incoming.begin();it!=incoming.end();++it) {
            const auto& entry=it.value();if(!entry.is_object())throw std::runtime_error("invalid script code entry");
            if(entry.contains("source")&&(!entry["source"].is_string()||entry["source"].get_ref<const std::string&>().size()>128*1024))throw std::runtime_error("invalid script source");
            if(entry.contains("bytecode_provenance")||entry.contains("bytecode_compiler_commit"))throw std::runtime_error("builtin bytecode metadata is reserved for the pinned player script bundle");
            if(entry.contains("bytecode_hex")) {
                if(!entry["bytecode_hex"].is_string())throw std::runtime_error("invalid script bytecode");
                const auto& hex=entry["bytecode_hex"].get_ref<const std::string&>();
                bool short_session_code=!entry.value("signature_verified",false)
                    &&entry.value("provenance","")=="session_small_bytecode"
                    &&entry.value("admission","")=="pinned_session_rsb_luau13_14_no_trailer_under_256"
                    &&hex.size()>=4&&hex.size()<512
                    &&(hex.compare(0,4,"0d03")==0||hex.compare(0,4,"0e03")==0);
                if((!entry.value("signature_verified",false)&&!short_session_code)
                   ||hex.size()>256*1024||hex.size()%2)
                    throw std::runtime_error("unverified or oversized script bytecode");
            }
            validate_original_bytecode(entry);
        }
        catalog=incoming;
        for(auto it=catalog.begin();it!=catalog.end();++it) {
            auto node=world.nodes.find(it.key());if(node!=world.nodes.end()&&!local_sources.contains(it.key()))node->second.script_state.code_mut()=it.value();
        }
    }
    // Root-derived code is already admitted and normalized by the owning root
    // World. Keep this bridge separate from update(), which accepts untrusted
    // host catalogs and deliberately rejects reserved builtin provenance.
    void update_world_sources(const Json& incoming) {
        const LazyValidatedRootSourceCatalog view(incoming);
        update_world_sources(view);
    }
    void update_world_sources(const LazyValidatedRootSourceCatalog& view) {
        const Json& incoming=view.checked_value();
        for(auto it=incoming.begin();it!=incoming.end();++it) {
            if(!it.value().is_object())continue;
            auto existing=catalog.find(it.key());
            const bool unchanged=existing!=catalog.end()&&existing.value()==it.value();
            // Every catalog entry is validated on first admission (host source
            // update or the first root snapshot). Rehashing identical original
            // and normalized bytecode in every Actor prepare is redundant.
            // Changed/new entries still validate before the node lookup, so a
            // changed entry with a missing Instance retains the same rejection.
            if(!unchanged)validate_original_bytecode(it.value());
            const auto node=world.nodes.find(it.key());
            if(node==world.nodes.end())continue;
            if(!unchanged)catalog[it.key()]=it.value();
            if(!local_sources.contains(it.key())&&node->second.script_state.code()!=it.value())
                node->second.script_state.code_mut()=it.value();
        }
    }
    void set_initial_ready(bool value){initial_ready=value;}
    Json reconcile() {
        push_method(environment,"reconcile");
        if(lua_pcall(environment,0,1,0)!=0){std::string error=lua_tostring(environment,-1);lua_pop(environment,1);throw std::runtime_error(error);}
        Json result=world_bindings::capture_value(environment,world,-1);lua_pop(environment,1);return result;
    }
};
