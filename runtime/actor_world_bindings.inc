// Actor messaging bridges between independent Actor VMs using copied data.
// Bindings live in the destination World's normal Signal/Connection registry,
// so Disconnect, owner cleanup, creator environments, and scheduling all use
// the same path as ordinary RBXScriptSignals.
namespace {
constexpr size_t kActorTopicLimit=256;
constexpr size_t kActorMessageArgumentLimit=64;
constexpr size_t kActorMessageBytes=64*1024;
constexpr size_t kActorMessageItems=4096;

std::string actor_signal_key(const std::string& topic) {
    return "ActorMessage:"+topic;
}

Json capture_actor_message_value(lua_State* L,World& world,int index,size_t& bytes,
                                 size_t& items,std::unordered_set<const void*>& path,
                                 int depth=0) {
    if(depth>16||items==0)throw std::runtime_error("Actor message nesting or item limit exceeded");
    --items;index=lua_absindex(L,index);
    if(lua_type(L,index)==LUA_TSTRING) {
        size_t size=0;const char* value=lua_tolstring(L,index,&size);
        if(size>bytes)throw std::runtime_error("Actor message exceeds byte limit");bytes-=size;
        if(valid_utf8(value,size))return std::string(value,size);
        return Json{{"$bytes_hex",bytes_hex(value,size)}};
    }
    if(lua_type(L,index)==LUA_TBUFFER) {
        size_t size=0;const auto* value=static_cast<const char*>(lua_tobuffer(L,index,&size));
        if(size>bytes)throw std::runtime_error("Actor message buffer exceeds byte limit");bytes-=size;
        return Json{{"$buffer_hex",bytes_hex(value,size)}};
    }
    if(lua_type(L,index)==LUA_TUSERDATA&&world.shared_table_pool
        &&::shared_table_bindings::is_shared_table(L,index)) {
        Json encoded;if(!::shared_table_bindings::capture_reference(L,index,*world.shared_table_pool,encoded))
            throw std::runtime_error("Actor message SharedTable belongs to another session");
        return encoded;
    }
    if(lua_type(L,index)==LUA_TFUNCTION||lua_type(L,index)==LUA_TTHREAD)
        throw std::runtime_error("Actor messages cannot copy functions or threads");
    if(lua_type(L,index)==LUA_TTABLE) {
        const void* identity=lua_topointer(L,index);
        if(!path.insert(identity).second)throw std::runtime_error("cyclic Actor message tables are unsupported");
        const int base=lua_gettop(L);Json entries=Json::array();size_t count=0;
        try {
            lua_pushnil(L);
            while(lua_next(L,index)) {
                if(++count>kActorMessageItems)throw std::runtime_error("Actor message table item limit exceeded");
                Json key;
                if(lua_type(L,-2)==LUA_TSTRING) {
                    size_t size=0;const char* value=lua_tolstring(L,-2,&size);
                    if(size>bytes)throw std::runtime_error("Actor message key exceeds byte limit");bytes-=size;
                    key=valid_utf8(value,size)?Json(std::string(value,size)):Json{{"$bytes_hex",bytes_hex(value,size)}};
                } else if(lua_type(L,-2)==LUA_TNUMBER) {
                    const double number=lua_tonumber(L,-2);
                    if(!std::isfinite(number))throw std::runtime_error("Actor message numeric keys must be finite");
                    key=number;
                } else throw std::runtime_error("Actor message table keys must be strings or finite numbers");
                Json value=capture_actor_message_value(L,world,-1,bytes,items,path,depth+1);
                entries.push_back(Json::array({std::move(key),std::move(value)}));
                lua_pop(L,1);
            }
        } catch(...) {lua_settop(L,base);path.erase(identity);throw;}
        path.erase(identity);
        return Json{{"$remote_map",std::move(entries)}};
    }
    if(lua_type(L,index)==LUA_TNUMBER&&!std::isfinite(lua_tonumber(L,index)))
        throw std::runtime_error("Actor message numbers must be finite");
    return convert_lua_value(L,world,index,0,nullptr,&bytes);
}

bool actor_message_refs_ready(const World& world,const Json& value) {
    if(value.is_object()) {
        if(value.size()==1&&value.contains("$ref"))
            return value["$ref"].is_string()&&world.nodes.find(value["$ref"].get<std::string>())!=world.nodes.end();
        if(value.size()==1&&value.contains("$shared_table_ref"))
            return world.shared_table_pool&&value["$shared_table_ref"].is_number_unsigned()
                &&static_cast<bool>(world.shared_table_pool->find(value["$shared_table_ref"].get<uint64_t>()));
        for(auto it=value.begin();it!=value.end();++it)if(!actor_message_refs_ready(world,it.value()))return false;
    } else if(value.is_array()) {
        for(const auto& item:value)if(!actor_message_refs_ready(world,item))return false;
    }
    return true;
}

void push_actor_message_value(lua_State* L,World& world,const Json& value) {
    if(value.is_object()&&value.size()==1&&value.contains("$shared_table_ref")) {
        if(!world.shared_table_pool||!::shared_table_bindings::push_reference(L,value,*world.shared_table_pool))
            throw std::runtime_error("Actor message SharedTable ref is invalid for this session");
    } else if(value.is_object()&&value.size()==1&&value.contains("$remote_map")) {
        const auto& entries=value["$remote_map"];
        if(!entries.is_array()||entries.size()>kActorMessageItems)throw std::runtime_error("invalid Actor message map");
        lua_createtable(L,0,static_cast<int>(entries.size()));int table=lua_gettop(L);
        for(const auto& pair:entries) {
            if(!pair.is_array()||pair.size()!=2)throw std::runtime_error("invalid Actor message map entry");
            push_actor_message_value(L,world,pair[0]);push_actor_message_value(L,world,pair[1]);lua_rawset(L,table);
        }
    } else push_json(L,world,value);
}

std::string actor_binding_owner(World& world,Node& actor,lua_State* L) {
    if(!world.scheduler)return {};
    const auto owner=world.scheduler->owner(L);
    std::string script_id;
    if(owner.compare(0,7,"script:")==0||owner.compare(0,7,"module:")==0)script_id=owner.substr(7);
    else return {};
    Node* script=find_node(world,script_id);
    if(!script||script->destroyed||(script->class_name!="Script"&&script->class_name!="LocalScript"&&script->class_name!="ModuleScript"))return {};
    std::string parent=parent_id(world,*script);
    for(size_t depth=0;!parent.empty()&&depth<=world.nodes.size();++depth) {
        if(parent==actor.id)return owner;
        Node* node=find_node(world,parent);if(!node)break;
        parent=parent_id(world,*node);
    }
    return {};
}

int actor_send_message(lua_State* L) {
    auto* handle=checked_handle(L,1);World& world=*handle->world;Node& actor=*handle->node;
    if(actor.class_name!="Actor"||actor.destroyed||!world.actor_runtime)
        return luaL_error(L,"Actor:SendMessage requires a live Actor in an Actor VM"),0;
    size_t topic_size=0;const char* topic_text=luaL_checklstring(L,2,&topic_size);
    if(topic_size>kActorTopicLimit)return luaL_error(L,"Actor message topic exceeds 256 bytes"),0;
    const int count=lua_gettop(L)-2;
    if(count<0||size_t(count)>kActorMessageArgumentLimit)
        return luaL_error(L,"Actor message has too many arguments"),0;
    Json arguments=Json::array();size_t bytes=kActorMessageBytes,items=kActorMessageItems;
    std::unordered_set<const void*> path;
    try {
        for(int i=3;i<=lua_gettop(L);++i)
            arguments.push_back(capture_actor_message_value(L,world,i,bytes,items,path));
        world.actor_runtime->enqueue_message(actor.id,std::string(topic_text,topic_size),arguments);
        return 0;
    } catch(const std::exception& error) {
        return luaL_error(L,"Actor:SendMessage: %s",error.what()),0;
    }
}

int actor_bind_message(lua_State* L) {
    auto* handle=checked_handle(L,1);World& world=*handle->world;Node& actor=*handle->node;
    if(actor.class_name!="Actor"||actor.destroyed)return luaL_error(L,"BindToMessage requires a live Actor"),0;
    size_t size=0;const char* topic_text=luaL_checklstring(L,2,&size);luaL_checktype(L,3,LUA_TFUNCTION);
    if(size>kActorTopicLimit)return luaL_error(L,"Actor message topic exceeds 256 bytes"),0;
    const auto owner=actor_binding_owner(world,actor,L);
    if(owner.empty())return luaL_error(L,"Only a script descended from this Actor can bind its messages"),0;
    try {
        Signal& signal=signal_for(world,actor,actor_signal_key(std::string(topic_text,size)));
        const int stack_base=lua_gettop(L);
        push_signal(L,signal);
        lua_pushvalue(L,3);
        lua_pushboolean(L,false);lua_pushcclosurek(L,connect_signal,"BindToMessage",1,nullptr);
        lua_insert(L,stack_base+1);
        lua_call(L,2,1);
        lua_remove(L,3);lua_remove(L,2);lua_remove(L,1);
        return 1;
    } catch(const std::exception& error) {
        return luaL_error(L,"Actor:BindToMessage: %s",error.what()),0;
    }
}

int actor_bind_message_parallel(lua_State* L) {
    return luaL_error(L,"BindToMessageParallel requires parallel scheduler context, which is unsupported"),0;
}
}

bool actor_message_index(lua_State* L,World& world,Node& actor,const std::string& key) {
    if(actor.class_name!="Actor")return false;
    if(key=="SendMessage") {
        lua_pushcfunction(L,actor_send_message,"Actor.SendMessage");return true;
    }
    if(key=="BindToMessage") {
        lua_pushcfunction(L,actor_bind_message,"Actor.BindToMessage");return true;
    }
    if(key=="BindToMessageParallel") {
        lua_pushcfunction(L,actor_bind_message_parallel,"Actor.BindToMessageParallel");return true;
    }
    (void)world;
    return false;
}

bool enqueue_actor_message(World& world,const std::string& actor_id,
                           const std::string& topic,const Json& arguments) {
    if(topic.size()>kActorTopicLimit||!arguments.is_array()||arguments.size()>kActorMessageArgumentLimit)
        throw std::runtime_error("invalid serialized Actor message");
    auto* actor=find_node(world,actor_id);
    if(!actor||actor->destroyed||actor->class_name!="Actor"||!world.actor_environment)
        throw std::runtime_error("Actor message destination is unavailable");
    if(world.event_argument_refs.size()>=10000||world.events.size()>=10000)return false;
    for(const auto& value:arguments)if(!actor_message_refs_ready(world,value))return false;
    lua_State* L=world.actor_environment;const int base=lua_gettop(L);
    int reference=LUA_NOREF;size_t argument_ref_index=world.event_argument_refs.size();
    const size_t event_count=world.events.size();
    try {
        lua_createtable(L,static_cast<int>(arguments.size()),0);const int table=lua_gettop(L);
        for(size_t i=0;i<arguments.size();++i) {
            push_actor_message_value(L,world,arguments[i]);lua_rawseti(L,table,static_cast<int>(i+1));
        }
        lua_pushvalue(L,table);reference=lua_ref(L,-1);lua_pop(L,1);
        world.event_argument_refs.push_back(reference);
        const auto before=world.events.size();
        emit(world,*actor,actor_signal_key(topic),Json::array(),reference,static_cast<int>(arguments.size()));
        if(world.events.size()==before) {
            lua_unref(L,reference);world.event_argument_refs.pop_back();
            lua_settop(L,base);return false;
        }
        lua_settop(L,base);return true;
    } catch(...) {
        while(world.events.size()>event_count) {
            if(world.events.back().connection&&world.events.back().connection->once)
                world.events.back().connection->once_pending=false;
            world.events.pop_back();
        }
        if(reference!=LUA_NOREF) {
            if(world.event_argument_refs.size()>argument_ref_index)
                world.event_argument_refs.erase(world.event_argument_refs.begin()+static_cast<std::ptrdiff_t>(argument_ref_index));
            lua_unref(L,reference);
        }
        lua_settop(L,base);throw;
    }
}
