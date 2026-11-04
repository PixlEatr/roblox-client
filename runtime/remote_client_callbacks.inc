// Ordinary RemoteFunction callbacks. Wire transport is handled by the session
// owner after matching the reply against a request received in this session.
bool remote_primitive(const Json& value) {
    if(value.is_null()||value.is_boolean()||value.is_number()||value.is_string())return true;
    return value.is_object()&&value.size()==1&&
        ((value.contains("$ref")&&(value["$ref"].is_string()||value["$ref"].is_null()))||
         (value.contains("$bytes_hex")&&value["$bytes_hex"].is_string()));
}
bool remote_components(const Json& value,size_t count) {
    if(!value.is_array()||value.size()!=count)return false;
    for(const auto& v:value)if(!v.is_number()||!std::isfinite(v.get<double>())||
                              std::abs(v.get<double>())>double(std::numeric_limits<float>::max()))return false;
    return true;
}
bool remote_engine_value(const Json& value) {
    if(!value.is_object()||value.size()!=1)return false;
    const auto& tag=value.begin().key();const auto& v=value.begin().value();
    if(tag=="$vector3"||tag=="$color3")return remote_components(v,3);
    if(tag=="$vector2"||tag=="$number_range")return remote_components(v,2);
    if(tag=="$udim")return remote_components(v,2)&&v[1].get<double>()==std::trunc(v[1].get<double>())&&
        v[1].get<double>()>=INT32_MIN&&v[1].get<double>()<=INT32_MAX;
    if(tag=="$udim2"||tag=="$rect") {
        const char* inner=tag=="$udim2"?"$udim":"$vector2";
        return v.is_array()&&v.size()==2&&v[0].is_object()&&v[1].is_object()&&
            v[0].contains(inner)&&v[1].contains(inner)&&remote_engine_value(v[0])&&remote_engine_value(v[1]);
    }
    if(tag=="$cframe")return v.is_object()&&v.size()==2&&v.contains("position")&&v.contains("rotation")&&
        remote_components(v["position"],3)&&remote_components(v["rotation"],9);
    if(tag=="$brickcolor"||tag=="$datetime")return v.is_number()&&std::isfinite(v.get<double>())&&
        v.get<double>()==std::trunc(v.get<double>())&&
        (tag=="$brickcolor"?(v.get<double>()>=0&&v.get<double>()<=65535):
            (v.get<double>()>=-17987443200000.&&v.get<double>()<=253402300799999.));
    if(tag=="$buffer_hex") {
        if(!v.is_string())return false;const auto& s=v.get_ref<const std::string&>();
        return s.size()<=131072&&s.size()%2==0&&s.find_first_not_of("0123456789abcdefABCDEF")==std::string::npos;
    }
    return false;
}
// Lua/reflection bridge: 101391808 selects ValueArray for nonzero raw length
// (and empty tables), ValueTable otherwise. 10138ED84's non-strict fallback
// converts an unrecognized userdata to the null variant. Recognize only Luau's
// explicit newproxy tag here; unsupported engine datatypes remain errors.
bool remote_supported(const Json& value,int depth=0,size_t* remaining=nullptr) {
    size_t limit=10000;if(!remaining)remaining=&limit;
    if(depth>16||!*remaining)return false;--*remaining;
    if(remote_primitive(value))return !value.is_number()||std::isfinite(value.get<double>());
    if(remote_engine_value(value))return true;
    if(!value.is_object()||value.size()!=1)return false;
    if(value.contains("$remote_array")&&value["$remote_array"].is_array()) {
        for(const auto& item:value["$remote_array"])if(!remote_supported(item,depth+1,remaining))return false;
        return true;
    }
    if(value.contains("$remote_map")&&value["$remote_map"].is_array()) {
        for(const auto& pair:value["$remote_map"]) {
            if(!pair.is_array()||pair.size()!=2||
               !(pair[0].is_string()||(pair[0].is_object()&&pair[0].size()==1&&pair[0].contains("$bytes_hex")&&pair[0]["$bytes_hex"].is_string()))||
               !remote_supported(pair[1],depth+1,remaining))return false;
        }
        return true;
    }
    return false;
}
Json capture_remote_value(lua_State* L,World& w,int index,size_t& bytes,size_t& items,
                          std::unordered_set<const void*>& path,int depth=0) {
    if(depth>16||items==0)throw std::runtime_error("Remote value nesting or item limit exceeded");
    --items;index=lua_absindex(L,index);
    if(lua_type(L,index)==LUA_TUSERDATA&&lua_userdatatag(L,index)==LUA_UTAG_LIMIT+1)return nullptr;
    if(lua_type(L,index)==LUA_TBUFFER) {
        size_t size=0;const auto* data=static_cast<const char*>(lua_tobuffer(L,index,&size));
        if(size>bytes)throw std::runtime_error("Remote buffer exceeds byte limit");
        bytes-=size;return Json{{"$buffer_hex",bytes_hex(data,size)}};
    }
    if(lua_type(L,index)!=LUA_TTABLE) {
        auto value=convert_lua_value(L,w,index,0,nullptr,&bytes);
        if(!remote_supported(value))
            throw std::runtime_error("Remote argument datatype is not yet supported");
        if(value.is_object()&&value.contains("$ref")&&!value["$ref"].is_null()) {
            auto* node=find_node(w,value["$ref"].get<std::string>());
            if(!node||node->destroyed||!node->server_owned)
                throw std::runtime_error("Remote argument instance was not received");
        }
        return value;
    }
    const void* identity=lua_topointer(L,index);
    if(!path.insert(identity).second)throw std::runtime_error("tables cannot be cyclic");
    int base=lua_gettop(L);Json out=Json::array();int length=lua_objlen(L,index);
    try {
        if(length>0) {
            if(size_t(length)>items)throw std::runtime_error("Remote array exceeds item limit");
            for(int i=1;i<=length;i++) {
                lua_rawgeti(L,index,i);out.push_back(capture_remote_value(L,w,-1,bytes,items,path,depth+1));lua_pop(L,1);
            }
        } else {
            std::unordered_set<std::string> keys;lua_pushnil(L);
            while(lua_next(L,index)) {
                Json key;
                if(lua_type(L,-2)==LUA_TSTRING)key=convert_lua_value(L,w,-2,0,nullptr,&bytes);
                else if(lua_type(L,-2)==LUA_TNUMBER) {
                    // 101391D40: FCVTZS X0,D0 followed by std::to_string(long long).
                    // Reject out-of-range keys and collisions rather than silently
                    // overwriting entries after the native string conversion.
                    double n=lua_tonumber(L,-2);
                    if(!std::isfinite(n)||n< -9223372036854775808.||n>=9223372036854775808.)
                        throw std::runtime_error("Remote numeric dictionary key exceeds int64 range");
                    auto text=std::to_string(static_cast<int64_t>(n));
                    if(text.size()>bytes)throw std::runtime_error("Remote dictionary key exceeds byte limit");
                    bytes-=text.size();key=std::move(text);
                } else throw std::runtime_error("Remote dictionary key datatype is not yet supported");
                if(!keys.insert(key.dump()).second)throw std::runtime_error("Remote dictionary keys collide after string conversion");
                auto value=capture_remote_value(L,w,-1,bytes,items,path,depth+1);
                out.push_back(Json::array({std::move(key),std::move(value)}));lua_pop(L,1);
            }
        }
    }catch(...){lua_settop(L,base);path.erase(identity);throw;}
    path.erase(identity);
    return Json{{length>0||out.empty()?"$remote_array":"$remote_map",std::move(out)}};
}
void push_remote_value(lua_State* L,World& w,const Json& value) {
    if(value.is_object()&&value.contains("$remote_array")) {
        const auto& items=value["$remote_array"];lua_createtable(L,int(items.size()),0);int table=lua_gettop(L),i=1;
        for(const auto& item:items){push_remote_value(L,w,item);lua_rawseti(L,table,i++);}
    } else if(value.is_object()&&value.contains("$remote_map")) {
        const auto& items=value["$remote_map"];lua_createtable(L,0,int(items.size()));int table=lua_gettop(L);
        for(const auto& pair:items){push_json(L,w,pair[0]);push_remote_value(L,w,pair[1]);lua_rawset(L,table);}
    } else push_json(L,w,value);
}
int invoke_remote_server_continue(lua_State* L,int) {
    if(lua_type(L,1)!=LUA_TBOOLEAN){luaL_error(L,"InvokeServer resumed without a result");return 0;}
    bool success=lua_toboolean(L,1);lua_remove(L,1);
    if(!success){if(lua_gettop(L)==0)lua_pushliteral(L,"InvokeServer failed");lua_error(L);return 0;}
    return lua_gettop(L);
}
void record_remote_call_diagnostic(lua_State* L,World& w,const Node& node,uint64_t request) noexcept {
    if(!w.trace_remote_calls||w.remote_call_diagnostics.size()>=64)return;
    try {
        auto bounded=[](const char* value,size_t max)->std::string {
            if(!value)return {};
            size_t length=0;
            while(length<max&&value[length])++length;
            return std::string(value,length);
        };
        const std::string owner=w.scheduler?w.scheduler->owner(L):std::string();
        Json item{{"request_id",request},{"node_id",bounded(node.id.c_str(),192)},
                  {"node_name",bounded(node.name.c_str(),128)},
                  {"scheduler_owner",bounded(owner.c_str(),128)},
                  {"frames",Json::array()}};
        // Build a bounded instance path from received snapshot nodes only.
        std::vector<std::string> parts;
        const Node* current=&node;
        for(size_t depth=0;current&&depth<32;++depth) {
            const std::string name=instance_name(w,*current);
            parts.push_back(bounded(name.c_str(),128));
            const auto parent=parent_id(w,*current);
            current=parent.empty()?nullptr:find_node(w,parent);
        }
        std::string instance_path;
        for(auto part=parts.rbegin();part!=parts.rend();++part) {
            if(!instance_path.empty())instance_path.push_back('.');
            if(instance_path.size()+part->size()>1024)break;
            instance_path+=*part;
        }
        item["instance_path"]=std::move(instance_path);
        for(int level=1;level<9;++level) {
            lua_Debug frame{};
            if(!lua_getinfo(L,level,"nsl",&frame))break;
            const char* source=frame.source?frame.source:frame.short_src;
            item["frames"].push_back(Json{{"source",bounded(source,256)},
                {"name",bounded(frame.name,128)},{"line",frame.currentline}});
        }
        w.remote_call_diagnostics.push_back(std::move(item));
    } catch(...) {
        // Diagnostics must never affect InvokeServer execution.
    }
}
int invoke_remote_server(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;
    RemoteServerWaiter waiter;
    try {
        if(h->node->class_name!="RemoteFunction"||h->node->destroyed||!h->node->server_owned)
            throw std::runtime_error("received RemoteFunction receiver required");
        if(!w.persistent||!w.scheduler||!lua_isyieldable(L))
            throw std::runtime_error("InvokeServer requires a scheduled persistent client thread");
        if(lua_gettop(L)-1>256||w.remote_server_calls.size()>=64||w.remote_server_waiters.size()>=512)
            throw std::runtime_error("RemoteFunction argument or pending limit exceeded");
        Json values=Json::array();size_t budget=65536,items=10000;std::unordered_set<const void*> path;
        for(int i=2;i<=lua_gettop(L);++i) {
            auto value=capture_remote_value(L,w,i,budget,items,path);
            if(!remote_supported(value)||(value.is_number()&&!std::isfinite(value.get<double>())))
                throw std::runtime_error("RemoteFunction argument datatype is not yet supported");
            if(value.is_object()&&value.contains("$ref")&&!value["$ref"].is_null()) {
                auto* node=find_node(w,value["$ref"].get<std::string>());
                if(!node||node->destroyed||!node->server_owned)
                    throw std::runtime_error("RemoteFunction argument instance was not received");
            }
            values.push_back(std::move(value));
        }
        if(w.next_remote_server_request>=uint64_t(INT64_MAX))throw std::runtime_error("RemoteFunction token exhausted");
        waiter.thread=L;waiter.node=h->node->id;waiter.request=w.next_remote_server_request++;
        Json call{{"node",waiter.node},{"request_id",waiter.request},{"arguments",std::move(values)}};
        size_t size=call.dump().size();
        if(size>128*1024||w.remote_server_call_bytes+size>256*1024)
            throw std::runtime_error("RemoteFunction byte queue limit exceeded");
        lua_pushthread(L);waiter.reference=lua_ref(L,-1);lua_pop(L,1);
        w.remote_server_calls.push_back(std::move(call));
        try {w.remote_server_waiters.push_back(waiter);}catch(...){w.remote_server_calls.erase(w.remote_server_calls.size()-1);throw;}
        w.remote_server_call_bytes+=size;
        record_remote_call_diagnostic(L,w,*h->node,waiter.request);
        w.scheduler->wait_signal(L);
    }catch(const std::exception& error) {
        if(waiter.reference!=LUA_NOREF)lua_unref(L,waiter.reference);
        luaL_error(L,"InvokeServer failed: %s",error.what());return 0;
    }
    lua_settop(L,0);return lua_yield(L,0);
}
void dispatch_remote_server_waiters(lua_State* from,World& w) {
    for(auto it=w.remote_server_waiters.begin();it!=w.remote_server_waiters.end();) {
        auto* node=find_node(w,it->node);
        bool waiting=w.scheduler->completion(it->thread)==LUA_YIELD;
        if(waiting&&node&&!node->destroyed){++it;continue;}
        if(waiting)w.scheduler->wake_signal(it->thread,[](lua_State* target){
            lua_pushboolean(target,false);lua_pushliteral(target,"RemoteFunction was destroyed during InvokeServer");return 2;
        });
        for(auto call=w.remote_server_calls.begin();call!=w.remote_server_calls.end();) {
            if(call->at("request_id")==it->request){w.remote_server_call_bytes-=call->dump().size();call=w.remote_server_calls.erase(call);}else ++call;
        }
        lua_unref(from,it->reference);it=w.remote_server_waiters.erase(it);
    }
}
void complete_remote_server_impl(lua_State* from,World& w,const Json& responses) {
    if(!w.persistent||!responses.is_array()||responses.size()>128)
        throw std::runtime_error("invalid RemoteFunction response batch");
    dispatch_remote_server_waiters(from,w);
    for(const auto& response:responses) {
        auto request=response.at("request_id").get<uint64_t>();
        auto node=response.at("node").get<std::string>();
        auto waiter=std::find_if(w.remote_server_waiters.begin(),w.remote_server_waiters.end(),
            [&](const RemoteServerWaiter& item){return item.request==request&&item.node==node;});
        if(waiter==w.remote_server_waiters.end())continue;
        bool ok=response.at("ok").get<bool>();std::string error;
        Json values=Json::array();
        if(ok) {
            values=response.at("values");
            if(!values.is_array()||values.size()>256||values.dump().size()>256*1024)
                throw std::runtime_error("invalid RemoteFunction return tuple");
            for(const auto& value:values)if(!remote_supported(value)||(value.is_number()&&!std::isfinite(value.get<double>()))) {
                ok=false;error="RemoteFunction result datatype is not yet supported";break;
            }
        }else error=response.at("error").get<std::string>().substr(0,4096);
        w.scheduler->wake_signal(waiter->thread,[&](lua_State* target){
            lua_pushboolean(target,ok);
            if(!ok){lua_pushlstring(target,error.data(),error.size());return 2;}
            for(const auto& value:values)push_remote_value(target,w,value);
            return int(values.size())+1;
        },true);
        lua_unref(from,waiter->reference);w.remote_server_waiters.erase(waiter);
    }
}
int fire_remote_server(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;
    try {
        if((h->node->class_name!="RemoteEvent"&&h->node->class_name!="UnreliableRemoteEvent")||h->node->destroyed||!h->node->server_owned)
            throw std::runtime_error("received RemoteEvent receiver required");
        if(!w.persistent)throw std::runtime_error("FireServer requires a persistent client session");
        const int count=lua_gettop(L)-1;
        if(count>256||w.remote_server_events.size()>=64)
            throw std::runtime_error("RemoteEvent argument or queue limit exceeded");
        Json values=Json::array();size_t budget=65536,items=10000;std::unordered_set<const void*> path;
        for(int i=2;i<=lua_gettop(L);++i) {
            auto value=capture_remote_value(L,w,i,budget,items,path);
            if(!remote_supported(value)||(value.is_number()&&!std::isfinite(value.get<double>())))
                throw std::runtime_error("RemoteEvent argument datatype is not yet supported");
            if(value.is_object()&&value.contains("$ref")&&!value["$ref"].is_null()) {
                auto* node=find_node(w,value["$ref"].get<std::string>());
                if(!node||node->destroyed||!node->server_owned)
                    throw std::runtime_error("RemoteEvent argument instance was not received");
            }
            values.push_back(std::move(value));
        }
        Json event{{"node",h->node->id},{"arguments",std::move(values)}};
        const auto size=event.dump().size();
        if(size>128*1024||w.remote_server_event_bytes+size>256*1024)
            throw std::runtime_error("RemoteEvent byte queue limit exceeded");
        w.remote_server_events.push_back(std::move(event));w.remote_server_event_bytes+=size;
    }catch(const std::exception& error){luaL_error(L,"FireServer failed: %s",error.what());return 0;}
    return 0;
}
void queue_remote_client_invoke(lua_State* from,World& w,Node& node,const Json& args) {
    if(args.size()!=2||!args[0].is_number_integer()||!args[1].is_array()||args[1].size()>256)
        throw std::runtime_error("invalid RemoteOnInvokeClient request");
    auto id=args[0].get<int64_t>();
    if(id<INT32_MIN||id>INT32_MAX)throw std::runtime_error("remote request ID exceeds int32");
    for(const auto& call:w.remote_client_invocations)
        if(call.node==node.id&&call.request==id)throw std::runtime_error("duplicate pending remote invocation");
    if(w.remote_client_invocations.size()>=512)throw std::runtime_error("remote invocation limit exceeded");
    RemoteClientInvocation call;call.node=node.id;call.request=static_cast<int32_t>(id);call.arguments=args[1];
    for(const auto& value:call.arguments)if(!remote_supported(value)) {
        call.error="RemoteFunction argument datatype is not yet supported";break;
    }
    auto bound=w.bindable_callbacks.find(node.id);
    if(bound!=w.bindable_callbacks.end()) {
        lua_getref(from,bound->second.reference);call.callback_ref=lua_ref(from,-1);lua_pop(from,1);
        call.owner=bound->second.owner;call.identity=bound->second.identity;
    }
    w.remote_client_invocations.push_back(std::move(call));
}
void dispatch_remote_client_functions(lua_State* from,World& w) {
    for(auto it=w.remote_client_invocations.begin();it!=w.remote_client_invocations.end();) {
        auto& call=*it;auto* node=find_node(w,call.node);
        if(call.cancelled)call.error="RemoteFunction callback owner was stopped";
        else if(!node||node->destroyed)call.error="RemoteFunction was destroyed during invocation";
        if(call.error.empty()&&!call.callee) {
            if(call.callback_ref==LUA_NOREF) {
                auto bound=w.bindable_callbacks.find(call.node);
                if(bound==w.bindable_callbacks.end()){++it;continue;}
                lua_getref(from,bound->second.reference);call.callback_ref=lua_ref(from,-1);lua_pop(from,1);
                call.owner=bound->second.owner;call.identity=bound->second.identity;
            }
            if(w.scheduler->invocation_capacity()==0){++it;continue;}
            try {
                call.callee=w.scheduler->invoke_callback(from,call.callback_ref,[&](lua_State* target) {
                    for(const auto& value:call.arguments)push_remote_value(target,w,value);
                    return static_cast<int>(call.arguments.size());
                },call.owner,call.identity);
                lua_unref(from,call.callback_ref);call.callback_ref=LUA_NOREF;call.arguments=Json::array();
            }catch(const std::exception& e){call.error=e.what();}
        }
        int status=call.callee?w.scheduler->completion(call.callee):LUA_YIELD;
        if(call.error.empty()&&status==LUA_YIELD){++it;continue;}
        Json values=Json::array();
        if(call.error.empty()&&status==0) {
            try {
                int count=lua_gettop(call.callee);
                if(count>256)throw std::runtime_error("RemoteFunction return count limit exceeded");
                size_t budget=65536,items=10000;std::unordered_set<const void*> path;
                for(int i=1;i<=count;++i) {
                    auto value=capture_remote_value(call.callee,w,i,budget,items,path);
                    if(!remote_supported(value))throw std::runtime_error("RemoteFunction return datatype is not yet supported");
                    values.push_back(std::move(value));
                }
            }catch(const std::exception& e){call.error=e.what();}
        } else if(call.error.empty()) {
            if(status==LUA_BREAK)call.error="RemoteFunction callback exceeded its CPU budget";
            else if(lua_type(call.callee,-1)==LUA_TSTRING) {
                size_t size=0;const char* text=lua_tolstring(call.callee,-1,&size);call.error.assign(text,std::min(size,size_t(4096)));
            } else call.error="RemoteFunction callback failed or was cancelled";
        }
        if(w.remote_client_replies.size()>=512)throw std::runtime_error("remote reply queue limit exceeded");
        Json reply{{"node",call.node},{"request_id",call.request},{"ok",call.error.empty()}};
        if(call.error.empty())reply["values"]=std::move(values);else reply["error"]=call.error.substr(0,4096);
        w.remote_client_replies.push_back(std::move(reply));
        if(call.callee)w.scheduler->release_invocation(call.callee);
        if(call.callback_ref!=LUA_NOREF)lua_unref(from,call.callback_ref);
        it=w.remote_client_invocations.erase(it);
    }
}
