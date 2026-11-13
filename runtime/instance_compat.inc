std::string hidden_property_name(const Node& n,const std::string& key) {
    if(n.class_name=="Fire"&&key=="size_xml")return "Size";
    if(n.class_name=="Humanoid"&&key=="Health_XML")return "Health";
    return key;
}
bool reflected_hidden_property(World& w,const Node& n,const std::string& key) {
    // Protected script text is transported as a shared string, but Source is
    // a reflected script property. Its transport type is not a hidden flag.
    if(key=="Source"&&(n.class_name=="Script"||n.class_name=="LocalScript"||n.class_name=="ModuleScript"))return false;
    if((key=="formFactorRaw"||key=="isPlayerScript")&&property_descriptor(n,key))return true;
    if(hidden_property_name(n,key)!=key||key=="Tags"||key=="NetworkOwnerV3"||key=="DefinesCapabilities")return true;
    if(const Json* descriptor=property_descriptor(n,key)) {
        const auto type=descriptor->value("engine_type","");
        if(type=="BinaryString"||type=="SharedString"||type=="SystemAddress")return true;
    }
    auto cls=w.class_property_types.find(n.class_name);
    if(cls!=w.class_property_types.end()&&cls->is_object()) {
        auto type=cls->find(key);
        if(type!=cls->end()&&type->is_number_integer()) {
            int id=type->get<int>();return id==8||id==46||id==47;
        }
    }
    return false;
}
void enforce_scriptability(lua_State* L,World& w,Node& n,std::string& key) {
    bool readable=!reflected_hidden_property(w,n,key);
    auto node=w.scriptability.find(n.id);if(node!=w.scriptability.end()){auto field=node->second.find(key);if(field!=node->second.end())readable=field->second;}
    if(!readable)luaL_error(L,"property is not scriptable");
    key=hidden_property_name(n,key);
}
int compat_property(lua_State* L) {
    auto* h=checked_handle(L,1);std::string key=luaL_checkstring(L,2);int operation=lua_tointeger(L,lua_upvalueindex(1));
    bool hidden=reflected_hidden_property(*h->world,*h->node,key);std::string canonical=hidden_property_name(*h->node,key);
    if(canonical!="Tags"&&canonical!="Name"&&canonical!="Parent"&&canonical!="ClassName"&&!property_descriptor(*h->node,canonical)&&!h->node->properties.contains(canonical)){
        if(operation==2||operation==3){if(operation==3)luaL_checktype(L,3,LUA_TBOOLEAN);lua_pushnil(L);return 1;}
        luaL_error(L,"unknown property");return 0;
    }
    auto& fields=h->world->scriptability[h->node->id];auto field=fields.find(key);bool previous=field==fields.end()?!hidden:field->second;
    if(operation==2){lua_pushboolean(L,previous);return 1;}
    if(operation==3){luaL_checktype(L,3,LUA_TBOOLEAN);fields[key]=lua_toboolean(L,3);lua_pushboolean(L,previous);return 1;}
    // A separate native accessor path leaves per-instance scriptability intact.
    lua_pushboolean(L,true);lua_pushcclosurek(L,operation==0?instance_index:instance_newindex,"property access",1,nullptr);lua_pushvalue(L,1);lua_pushlstring(L,canonical.data(),canonical.size());if(operation==1)lua_pushvalue(L,3);
    int result=lua_pcall(L,operation==0?2:3,operation==0?1:0,0);
    if(result!=LUA_OK){lua_error(L);return 0;}
    lua_pushboolean(L,hidden);return operation==0?2:1;
}
int compat_fps(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));double value=luaL_checknumber(L,1);
    if(!std::isfinite(value)||value<0||value>1000){luaL_error(L,"frame cap must be 0..1000");return 0;}w->render_cap=value;return 0;
}
bool script_bytecode_trace_enabled() noexcept {
    const char* value=std::getenv("RCR_TRACE_SCRIPT_BYTES");
    return value&&std::strcmp(value,"1")==0;
}
std::string script_bytecode_trace_clip(const std::string& value,size_t limit) {
    return value.substr(0,limit);
}
void trace_script_bytecode(World& world,const Node& target,const std::string& effective_id,
                           const std::string& selection,const char* outcome,
                           const std::string* bytes) noexcept {
    if(!script_bytecode_trace_enabled())return;
    try {
        if(world.script_bytecode_trace_count>=127) {
            if(world.script_bytecode_trace_limit_reported)return;
            world.script_bytecode_trace_limit_reported=true;
            ++world.script_bytecode_trace_count;
            const std::string marker="__SCRIPT_BYTES__{\"outcome\":\"trace_limit_reached\"}\n";
            const size_t room=world.output.size()<kMaxPrinted?kMaxPrinted-world.output.size():0;
            if(room>=marker.size())world.output+=marker;else world.output_truncated=true;
            return;
        }
        const std::string effective_name=instance_name(world,target);
        Json record={{"id",script_bytecode_trace_clip(target.id,96)},
                     {"class",script_bytecode_trace_clip(target.class_name,48)},
                     {"name",script_bytecode_trace_clip(effective_name,96)},
                     {"origin_id",script_bytecode_trace_clip(effective_id,96)},
                     {"selection",selection},{"outcome",outcome}};
        std::vector<const Node*> ancestry;
        const Node* current=&target;
        for(int depth=0;current&&depth<8;++depth) {
            ancestry.push_back(current);
            const std::string parent=parent_id(world,*current);
            current=parent.empty()?nullptr:find_node(world,parent);
        }
        std::string path;
        for(auto it=ancestry.rbegin();it!=ancestry.rend();++it) {
            if(!path.empty())path.push_back('/');
            path+=script_bytecode_trace_clip(instance_name(world,**it),24);
        }
        record["path"]=path;
        if(bytes) {
            record["length"]=bytes->size();
            record["byte1"]=bytes->size()>0?Json(static_cast<unsigned char>((*bytes)[0])):Json(nullptr);
            record["byte2"]=bytes->size()>1?Json(static_cast<unsigned char>((*bytes)[1])):Json(nullptr);
            record["sha256"]=source_crypto::hex(source_crypto::sha256(*bytes));
        } else {
            record["length"]=nullptr;record["byte1"]=nullptr;record["byte2"]=nullptr;record["sha256"]=nullptr;
        }
        std::string line="__SCRIPT_BYTES__"+record.dump()+"\n";
        if(line.size()>1024) {
            record["path"]=script_bytecode_trace_clip(path,128);
            record["name"]=script_bytecode_trace_clip(effective_name,32);
            line="__SCRIPT_BYTES__"+record.dump()+"\n";
        }
        ++world.script_bytecode_trace_count;
        const size_t room=world.output.size()<kMaxPrinted?kMaxPrinted-world.output.size():0;
        if(line.size()<=1024&&room>=line.size())world.output+=line;else world.output_truncated=true;
    } catch(...) {
        // Diagnostics must never alter getscriptbytecode results or errors.
    }
}
int compat_scriptbytes(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="ModuleScript"&&h->node->class_name!="LocalScript"&&h->node->class_name!="Script"){luaL_error(L,"script Instance required");return 0;}
    auto& world=*h->world;
    const int mode=static_cast<int>(lua_tointeger(L,lua_upvalueindex(1)));
    const bool tracing=mode==0&&script_bytecode_trace_enabled();
    std::string effective_id=h->node->id,selection="unavailable";
    try {
        const Json* code=&h->node->script_state.code().read();
        auto has_code=[](const Json* value) {
            return value&&value->is_object()
                &&(value->contains("source")||value->contains("bytecode_hex"));
        };
        // Clones can carry catalog metadata such as {reason:"code_unavailable"}
        // before their origin's payload is admitted. Match getscriptclosure's
        // fallback rule: only an actual source/bytecode payload (including an
        // authored empty Source string) takes precedence over script_origin.
        std::string origin_id=h->node->script_state.origin();
        for(int depth=0;!has_code(code)&&!origin_id.empty()&&depth<64;++depth) {
            auto* origin=find_node(world,origin_id);
            if(!origin)break;
            effective_id=origin->id;
            code=&origin->script_state.code().read();
            origin_id=origin->script_state.origin();
        }
        if(!code->is_object()||(!code->contains("source")&&!code->contains("bytecode_hex"))) {
            if(mode==1)throw std::runtime_error("script code is unavailable");
            if(tracing)trace_script_bytecode(world,*h->node,effective_id,"unavailable","nil",nullptr);
            lua_pushnil(L);return 1;
        }
        selection=code->value("bytecode_provenance","")=="pinned_builtin_luau_0_705"?"pinned":
            code->contains("source")?"source":"bytecode";
        std::string bytes;
        if(code->value("bytecode_provenance","")=="pinned_builtin_luau_0_705"){
            if(code->value("bytecode_compiler_commit","")!="93c83a46d7c6c1b040e58e82ef167340a7db1aef"
                ||code->value("bytecode_version",0)!=6||code->value("bytecode_type_version",0)!=3
                ||!code->contains("source")||!code->contains("source_sha256")||!code->contains("bytecode_sha256"))
                throw std::runtime_error("invalid pinned builtin bytecode metadata");
            const auto& source=code->at("source").get_ref<const std::string&>();
            if(source_crypto::hex(source_crypto::sha256(source))!=code->at("source_sha256").get<std::string>())
                throw std::runtime_error("pinned builtin source hash mismatch");
            const auto& hex=code->at("bytecode_hex").get_ref<const std::string&>();
            auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;};
            if(hex.size()%2)throw std::runtime_error("invalid bytecode encoding");
            bytes.reserve(hex.size()/2);
            for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid bytecode encoding");bytes+=char(a*16+b);}
            if(bytes.size()<2||static_cast<unsigned char>(bytes[0])!=6||static_cast<unsigned char>(bytes[1])!=3
                ||source_crypto::hex(source_crypto::sha256(bytes))!=code->at("bytecode_sha256").get<std::string>())
                throw std::runtime_error("pinned builtin bytecode hash or header mismatch");
        } else if(code->contains("source"))bytes=Luau::compile(code->at("source").get<std::string>());
        else if(mode!=2&&code->contains("original_bytecode_hex")){
            selection="original_bytecode";
            if(!code->contains("bytecode_hex")||!code->at("bytecode_hex").is_string()
                ||!code->contains("original_sha256")||!code->at("original_sha256").is_string())
                throw std::runtime_error("invalid original bytecode metadata");
            const auto& encoded=code->at("original_bytecode_hex").get_ref<const std::string&>();
            const auto& normalized=code->at("bytecode_hex").get_ref<const std::string&>();
            if(encoded.empty()||encoded.size()>256*1024||encoded.size()%2
                ||normalized.empty()||normalized.size()>256*1024||normalized.size()%2)
                throw std::runtime_error("invalid original bytecode size");
            auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1;};
            std::string original;original.reserve(encoded.size()/2);
            for(size_t i=0;i<encoded.size();i+=2){int a=nib(encoded[i]),b=nib(encoded[i+1]);if(a<0||b<0)throw std::runtime_error("invalid original bytecode encoding");original+=char(a*16+b);}
            if(source_crypto::hex(source_crypto::sha256(original))!=code->at("original_sha256").get<std::string>())
                throw std::runtime_error("original bytecode digest mismatch");
            bytes=std::move(original);
        }
        else if(code->contains("bytecode_hex")){
            const auto& hex=code->at("bytecode_hex").get_ref<const std::string&>();
            auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1;};
            if(hex.size()%2)throw std::runtime_error("invalid bytecode encoding");
            for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid bytecode encoding");bytes+=char(a*16+b);}
        } else throw std::runtime_error("script code is unavailable");
        if(mode==1)bytes=source_crypto::hex(source_crypto::sha256(bytes));
        if(tracing)trace_script_bytecode(world,*h->node,effective_id,selection,"ok",&bytes);
        lua_pushlstring(L,bytes.data(),bytes.size());return 1;
    }catch(const std::exception& e){if(tracing)trace_script_bytecode(world,*h->node,effective_id,selection,"error",nullptr);luaL_error(L,"script code: %s",e.what());return 0;}
}
int compat_namecall_continue(lua_State* L,int status){if(status!=LUA_OK){lua_error(L);return 0;}return lua_gettop(L);}
int compat_namecall(lua_State* L) {
    const char* method=lua_namecallatom(L,nullptr);if(!method){luaL_error(L,"missing namecall method");return 0;}
    int arguments=lua_gettop(L);lua_pushcfunction(L,instance_index,"Instance.__index");lua_pushvalue(L,1);lua_pushstring(L,method);lua_call(L,2,1);
    if(!lua_isfunction(L,-1)){luaL_error(L,"Instance member is not callable");return 0;}
    lua_insert(L,1);return lua_callyieldable(L,arguments,LUA_MULTRET);
}
int compat_checkcaller(lua_State* L){auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));lua_pushboolean(L,!w->scheduler||w->scheduler->owner(L).empty());return 1;}
int compat_push_thread_script(lua_State* L,World* w,lua_State* thread) {
    const auto owner=w->scheduler?w->scheduler->owner(thread):std::string();
    const bool script=owner.compare(0,7,"script:")==0,module=owner.compare(0,7,"module:")==0;
    auto* node=(script||module)?find_node(*w,owner.substr(7)):nullptr;
    // Destroy removes hierarchy membership; it does not collect a script
    // whose userdata is still alive. The owner identifier holds no Lua ref.
    if(node&&(node->class_name=="Script"||node->class_name=="LocalScript"||node->class_name=="ModuleScript"))
        push_node(L,*w,node);
    else lua_pushnil(L);
    return 1;
}
int compat_callingscript(lua_State* L) {
    return compat_push_thread_script(L,static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1))),L);
}
int compat_scriptfromthread(lua_State* L) {
    luaL_checktype(L,1,LUA_TTHREAD);
    return compat_push_thread_script(L,static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1))),lua_tothread(L,1));
}
int compat_active(lua_State* L){lua_pushboolean(L,false);return 1;} // This host owns no native input window.
Connection* resolve_wait_connection(ConnectionHandle& handle) {
    if(!handle.waiter_proxy||!handle.waiter_world)return nullptr;
    for(auto& connection:handle.waiter_world->connections)
        if(connection&&connection->signal_waiter&&connection->id==handle.connection_id
           &&connection->generation==handle.generation)return connection.get();
    return nullptr;
}
int compat_hui(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    lua_getfield(L,LUA_REGISTRYINDEX,"Headless.OverlayRoot");
    if(has_meta(L,-1,kInstanceMeta)&&!checked_handle(L,-1)->node->destroyed)return 1;
    lua_pop(L,1);
    // A separate local display surface. It is not a descendant of game.CoreGui
    // and never changes that tree's membership signals.
    lua_pushlightuserdata(L,w);lua_pushcclosurek(L,instance_new,"Instance.new",1,nullptr);lua_pushliteral(L,"Folder");lua_call(L,1,1);
    auto* root=checked_handle(L,-1)->node;w->overlay_root=root->id;root->name="RuntimeOverlay";
    lua_pushvalue(L,-1);lua_setfield(L,LUA_REGISTRYINDEX,"Headless.OverlayRoot");return 1;
}
int compat_connection_toggle(lua_State* L) {
    auto* handle=static_cast<ConnectionHandle*>(luaL_checkudata(L,1,kConnectionMeta));
    bool enable=lua_toboolean(L,lua_upvalueindex(1));
    if(handle->foreign_state) {
        if(handle->actor_runtime)
            handle->actor_runtime->set_connection_enabled(handle->owner_actor_id,
                                                           handle->connection_id,
                                                           handle->generation,enable);
        return 0;
    }
    auto* c=handle->waiter_proxy?resolve_wait_connection(*handle):handle->connection;
    if(!c)return 0;
    if(!c->auto_disconnected&&!c->manually_disconnected) {
        c->enabled=enable;
    }
    return 0;
}
int compat_connection_fire(lua_State* L) {
    auto* handle=static_cast<ConnectionHandle*>(luaL_checkudata(L,1,kConnectionMeta));
    if(handle->foreign_state)return luaL_error(L,"cross-VM connection callbacks cannot be fired from this VM"),0;
    if(handle->waiter_proxy)return luaL_error(L,"a signal Wait connection cannot be fired directly"),0;
    auto* c=handle->connection;
    if(!c)return 0;
    if(c->signal_waiter)return luaL_error(L,"a signal Wait connection cannot be fired directly"),0;
    if(c->native_kind!=Connection::NativeKind::None)return 0;
    if(!c->connected||!c->enabled||c->signal->node->destroyed)return 0;
    auto* scheduler=c->signal->world->scheduler;if(!scheduler){luaL_error(L,"connection dispatch requires a scheduler");return 0;}
    int count=lua_gettop(L)-1;
    auto arguments=[&](lua_State* target){for(int i=0;i<count;++i)lua_pushvalue(L,i+2);if(target!=L)lua_xmove(L,target,count);return count;};
    if(lua_toboolean(L,lua_upvalueindex(1)))
        scheduler->callback(L,c->callback,arguments,[connection=c->shared_from_this()](){return connection->connected&&connection->enabled&&!connection->signal->node->destroyed;},c->owner,c->identity,c->creator_ref);
    else {scheduler->callback_now(L,c->callback,arguments,c->owner,c->identity,c->creator_ref);lua_pop(L,1);}
    return 0;
}
int compat_firesignal(lua_State* L) {
    auto* signal=static_cast<SignalHandle*>(luaL_checkudata(L,1,kSignalMeta))->signal;
    if(signal->node->destroyed)return 0;
    auto& w=*signal->world;auto* scheduler=w.scheduler;
    if(!scheduler)return luaL_error(L,"signal dispatch requires a scheduler"),0;
    prune_signal_waiters(w,signal);
    const int count=lua_gettop(L)-1;
    auto arguments=[&](lua_State* target){for(int i=0;i<count;++i)lua_pushvalue(L,i+2);if(target!=L)lua_xmove(L,target,count);return count;};
    // Callbacks may connect/disconnect/reenter; iterate the initial membership.
    std::vector<std::pair<uint64_t,uint64_t>> waiting_snapshot;
    if(auto found=w.waiters.find(signal);found!=w.waiters.end()) {
        waiting_snapshot.reserve(found->second.size());
        for(const auto& waiter:found->second)if(waiter.connection)
            waiting_snapshot.emplace_back(waiter.connection->id,waiter.connection->generation);
    }
    std::vector<std::shared_ptr<Connection>> callbacks;
    for(auto& c:w.connections)if(c->signal==signal&&!c->signal_waiter&&c->native_kind==Connection::NativeKind::None&&
        c->connected&&c->enabled&&(!c->once||!c->once_pending))callbacks.push_back(c);
    for(const auto& c:callbacks) {
        if(!c->connected||!c->enabled||signal->node->destroyed||(c->once&&c->once_pending))continue;
        if(c->once){c->once_pending=true;c->connected=false;c->auto_disconnected=true;}
        scheduler->callback_now(L,c->callback,arguments,c->owner,c->identity,c->creator_ref);lua_pop(L,1);
    }
    prune_signal_waiters(w,signal);
    auto waiting=w.waiters.find(signal);
    std::vector<SignalWaiter> retained;
    if(waiting!=w.waiters.end())for(auto waiter:waiting->second) {
        auto* connection=waiter.connection;
        if(!connection) {
            if(waiter.reference!=LUA_NOREF)lua_unref(waiter.thread,waiter.reference);
            continue;
        }
        const bool existed=std::find(waiting_snapshot.begin(),waiting_snapshot.end(),
            std::pair<uint64_t,uint64_t>{connection->id,connection->generation})!=waiting_snapshot.end();
        // Reentrant callbacks may register new Wait calls. They are not part
        // of this firing's membership snapshot and remain pending.
        if(!existed){retained.push_back(waiter);continue;}
        if(!connection||!connection->connected||connection->manually_disconnected
           ||connection->auto_disconnected) {
            if(waiter.reference!=LUA_NOREF)lua_unref(waiter.thread,waiter.reference);
            continue;
        }
        if(!connection->enabled) {retained.push_back(waiter);continue;}
        connection->connected=false;connection->auto_disconnected=true;
        scheduler->wake_signal(waiter.thread,arguments);
        if(waiter.reference!=LUA_NOREF)lua_unref(waiter.thread,waiter.reference);
    }
    if(retained.empty())w.waiters.erase(signal);
    else w.waiters[signal]=std::move(retained);
    prune_signal_waiters(w,signal);
    return 0;
}
bool reflected_argument_matches(const Json& value,int type) {
    if(type==0||type==3)return value.is_null();
    if(type==2||type==8||type==33)return value.is_string()||(value.is_object()&&value.contains("$bytes_hex"));
    if(type==9)return value.is_boolean();
    if(type==7)return value.is_object()&&value.contains("$enum");
    if(type==10||type==11||type==12) {
        if(!value.is_number()||!std::isfinite(value.get<double>()))return false;
        double number=value.get<double>();
        if(type==10)return number==std::trunc(number)&&number>=INT32_MIN&&number<=INT32_MAX;
        return type==12||std::abs(number)<=std::numeric_limits<float>::max();
    }
    if(type==28||type==69)return value.is_null()||(value.is_object()&&value.contains("$ref"));
    const char* tag=nullptr;
    switch(type) {
        case 13:tag="$udim";break;case 14:tag="$udim2";break;case 18:tag="$brickcolor";break;
        case 19:tag="$color3";break;case 21:tag="$vector2";break;case 22:tag="$vector3";break;
        case 27:tag="$cframe";break;case 29:case 30:tag="$remote_array";break;
        case 31:case 32:tag="$remote_map";break;case 37:tag="$number_range";break;
        case 40:tag="$rect";break;case 48:tag="$datetime";break;case 56:tag="$buffer_hex";break;
        default:return false;
    }
    return value.is_object()&&value.contains(tag);
}
void enqueue_reflected_signal(World& w,Node& node,const std::string& name,Json values) {
    if(!w.persistent||node.destroyed||!node.server_owned)
        throw std::runtime_error("received signal in a persistent session required");
    const auto* signature=received_signal_signature(w,node.class_name,name);
    if(!signature)
        throw std::runtime_error("signal has no received replication signature");
    const auto& types=*signature;
    if(values.size()!=types.size())throw std::runtime_error("signal argument count does not match received signature");
    for(size_t i=0;i<types.size();++i) {
        int type=types[i].get<int>();
        if(!reflected_argument_matches(values[i],type))
            throw std::runtime_error("signal argument datatype does not match received signature");
        if((type==28||type==69)&&values[i].is_null())values[i]=Json{{"$ref",nullptr}};
    }
    Json event{{"node",node.id},{"event",name},{"argument_types",types},{"arguments",std::move(values)}};
    const auto size=event.dump().size();
    if(w.remote_server_events.size()>=64||size>128*1024||w.remote_server_event_bytes+size>256*1024)
        throw std::runtime_error("signal replication queue limit exceeded");
    w.remote_server_events.push_back(std::move(event));w.remote_server_event_bytes+=size;
}
int compat_replicatesignal(lua_State* L) {
    auto* signal=static_cast<SignalHandle*>(luaL_checkudata(L,1,kSignalMeta))->signal;
    auto& w=*signal->world;
    try {
        Json values=Json::array();size_t bytes=65536,items=10000;std::unordered_set<const void*> path;
        if(lua_gettop(L)>257)throw std::runtime_error("signal argument count exceeds limit");
        for(int i=2;i<=lua_gettop(L);++i) {
            Json value;
            const auto* received_signature=received_signal_signature(w,signal->node->class_name,signal->key);
            if(received_signature) {
                const auto& signature=*received_signature;
                if(size_t(i-2)<signature.size()&&signature[i-2]==10&&lua_type(L,i)==LUA_TNUMBER) {
                    // Reflection reads an engine int through Luau's numeric
                    // conversion; wire serialization still receives int32.
                    values.push_back(lua_tointeger(L,i));continue;
                }
            }
            if(capture_ussi_export_enum(L,i,value)||capture_geometry_enum(L,i,value))values.push_back(std::move(value));
            else values.push_back(capture_remote_value(L,w,i,bytes,items,path));
        }
        enqueue_reflected_signal(w,*signal->node,signal->key,std::move(values));
    }catch(const std::exception& error){luaL_error(L,"replicatesignal failed: %s",error.what());}
    return 0;
}
bool connection_compat_index(lua_State* L,Connection& c,const char* key) {
    if(std::strcmp(key,"Enabled")==0)lua_pushboolean(L,c.connected&&c.enabled);
    else if(std::strcmp(key,"ForeignState")==0)lua_pushboolean(L,false);
    // Connect records its creating Luau thread even for native callbacks.
    else if(std::strcmp(key,"LuaConnection")==0)lua_pushboolean(L,c.native_kind==Connection::NativeKind::None
        &&(c.signal_waiter?c.creator_ref!=LUA_NOREF:c.callback!=LUA_NOREF&&c.callback!=LUA_REFNIL));
    else if(std::strcmp(key,"Function")==0){if(c.callback==LUA_NOREF||c.callback==LUA_REFNIL)lua_pushnil(L);else lua_getref(L,c.callback);}
    else if(std::strcmp(key,"Thread")==0){if(c.creator_ref==LUA_NOREF||c.creator_ref==LUA_REFNIL)lua_pushnil(L);else lua_getref(L,c.creator_ref);}
    else if(std::strcmp(key,"Enable")==0||std::strcmp(key,"Disable")==0){lua_pushboolean(L,std::strcmp(key,"Enable")==0);lua_pushcclosurek(L,compat_connection_toggle,key,1,nullptr);}
    else if(std::strcmp(key,"Fire")==0||std::strcmp(key,"Defer")==0){lua_pushboolean(L,std::strcmp(key,"Defer")==0);lua_pushcclosurek(L,compat_connection_fire,key,1,nullptr);}
    else return false;return true;
}
bool connection_handle_compat_index(lua_State* L,ConnectionHandle& handle,const char* key) {
    if(!handle.foreign_state) {
        auto* connection=handle.waiter_proxy?resolve_wait_connection(handle):handle.connection;
        if(connection&&connection_compat_index(L,*connection,key))return true;
        if(!handle.waiter_proxy)return false;
        if(std::strcmp(key,"Enabled")==0||std::strcmp(key,"Connected")==0)lua_pushboolean(L,false);
        else if(std::strcmp(key,"ForeignState")==0)lua_pushboolean(L,false);
        else if(std::strcmp(key,"LuaConnection")==0)lua_pushboolean(L,handle.waiter_lua_connection);
        else if(std::strcmp(key,"Function")==0||std::strcmp(key,"Thread")==0)lua_pushnil(L);
        else if(std::strcmp(key,"Enable")==0||std::strcmp(key,"Disable")==0){lua_pushboolean(L,std::strcmp(key,"Enable")==0);lua_pushcclosurek(L,compat_connection_toggle,key,1,nullptr);}
        else if(std::strcmp(key,"Fire")==0||std::strcmp(key,"Defer")==0){lua_pushboolean(L,std::strcmp(key,"Defer")==0);lua_pushcclosurek(L,compat_connection_fire,key,1,nullptr);}
        else return false;
        return true;
    }
    ActorRuntime::ConnectionView state;
    const bool live=handle.actor_runtime&&handle.actor_runtime->connection_state(
        handle.owner_actor_id,handle.connection_id,handle.generation,state);
    if(std::strcmp(key,"Enabled")==0)lua_pushboolean(L,live&&state.enabled);
    else if(std::strcmp(key,"ForeignState")==0)lua_pushboolean(L,true);
    else if(std::strcmp(key,"LuaConnection")==0)lua_pushboolean(L,live?state.lua_connection:handle.foreign_lua_connection);
    // Luau functions and threads cannot cross VM heaps; never expose a value
    // from the owning Actor's state through a proxy.
    else if(std::strcmp(key,"Function")==0||std::strcmp(key,"Thread")==0)lua_pushnil(L);
    else if(std::strcmp(key,"Enable")==0||std::strcmp(key,"Disable")==0){lua_pushboolean(L,std::strcmp(key,"Enable")==0);lua_pushcclosurek(L,compat_connection_toggle,key,1,nullptr);}
    else if(std::strcmp(key,"Fire")==0||std::strcmp(key,"Defer")==0){lua_pushboolean(L,std::strcmp(key,"Defer")==0);lua_pushcclosurek(L,compat_connection_fire,key,1,nullptr);}
    else return false;
    return true;
}
int compat_connections(lua_State* L) {
    auto* signal=static_cast<SignalHandle*>(luaL_checkudata(L,1,kSignalMeta))->signal;
    prune_signal_waiters(*signal->world,signal);
    // Native signal subscriptions are linked at the head. Enumeration must
    // retain newest-first order, including the actual creating coroutine.
    lua_newtable(L);int count=0;for(auto it=signal->world->connections.rbegin();it!=signal->world->connections.rend();++it){auto& c=*it;if(c->signal!=signal||c->auto_disconnected||c->manually_disconnected)continue;
        auto* value=new_connection_handle(L);
        if(c->signal_waiter){value->waiter_proxy=true;value->waiter_world=signal->world;value->waiter_lua_connection=c->creator_ref!=LUA_NOREF;}
        else {value->connection=c.get();value->ownership=c;}
        value->connection_id=c->id;value->generation=c->generation;luaL_getmetatable(L,kConnectionMeta);lua_setmetatable(L,-2);lua_rawseti(L,-2,++count);
    }
    if(signal->world->actor_runtime) {
        auto foreign=signal->world->actor_runtime->foreign_connections(signal->world->actor_id,
                                                                        signal->node->id,
                                                                        signal->key);
        for(const auto& item:foreign) {
            if(item.owner_actor_id.size()>=sizeof(((ConnectionHandle*)nullptr)->owner_actor_id))continue;
            auto* value=new_connection_handle(L);
            value->actor_runtime=signal->world->actor_runtime;
            value->connection_id=item.connection_id;value->generation=item.generation;value->foreign_state=true;
            value->foreign_lua_connection=item.lua_connection;
            std::memcpy(value->owner_actor_id,item.owner_actor_id.data(),item.owner_actor_id.size());
            luaL_getmetatable(L,kConnectionMeta);lua_setmetatable(L,-2);lua_rawseti(L,-2,++count);
        }
    }
    return 1;
}
int compat_cloneref(lua_State* L) {
    auto* original=checked_handle(L,1);
    auto* copy=static_cast<Handle*>(lua_newuserdatatagged(L,sizeof(Handle),0));*copy=*original;
    luaL_getmetatable(L,kInstanceMeta);lua_setmetatable(L,-2);return 1;
}
int compat_compareinstances(lua_State* L) {
    auto* a=checked_handle(L,1);auto* b=checked_handle(L,2);
    lua_pushboolean(L,a->world==b->world&&a->node==b->node);return 1;
}
int compat_cache_invalidate(lua_State* L) {
    auto* h=checked_handle(L,1);push_instance_cache(L);lua_pushlightuserdata(L,h->node);lua_pushnil(L);lua_rawset(L,-3);return 0;
}
int compat_cache_iscached(lua_State* L) {
    auto* h=checked_handle(L,1);push_instance_cache(L);lua_pushlightuserdata(L,h->node);lua_rawget(L,-2);lua_pushboolean(L,!lua_isnil(L,-1));return 1;
}
int compat_cache_replace(lua_State* L) {
    auto* h=checked_handle(L,1);auto* replacement=checked_handle(L,2);
    if(h->world!=replacement->world){luaL_error(L,"replacement must belong to the same world");return 0;}
    push_instance_cache(L);lua_pushlightuserdata(L,h->node);lua_pushvalue(L,2);lua_rawset(L,-3);return 0;
}
int compat_instances(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));int mode=lua_tointeger(L,lua_upvalueindex(2));
    lua_newtable(L);int count=0;
    for(const auto& id:w->order){auto& n=w->nodes.at(id);if(n.destroyed)continue;
        if(mode==1&&!parent_id(*w,n).empty())continue;
        if(mode==2&&n.class_name!="ModuleScript"&&n.class_name!="LocalScript"&&n.class_name!="Script")continue;
        if(mode==2) {
            // Enumerate script Instances independently of code availability
            // and ancestry. Only Roblox-owned CoreGui/CorePackages scripts are
            // excluded from the default game-script view.
            bool core_script=false;
            for(const char* service:{"CoreGui","CorePackages"}) {
                auto it=w->services.find(service);
                if(it!=w->services.end()&&is_descendant_of(*w,n,it->second)) {
                    core_script=true;break;
                }
            }
            if(core_script)continue;
        }
        push_node(L,*w,&n);lua_rawseti(L,-2,++count);
    }return 1;
}
int compat_callback(lua_State* L) {
    auto* h=checked_handle(L,1);std::string name=luaL_checkstring(L,2);
    if(!((h->node->class_name=="BindableFunction"&&name=="OnInvoke")||
         (h->node->class_name=="RemoteFunction"&&name=="OnClientInvoke"))){luaL_error(L,"unsupported callback property");return 0;}
    auto it=h->world->bindable_callbacks.find(h->node->id);
    if(it==h->world->bindable_callbacks.end())lua_pushnil(L);else lua_getref(L,it->second.reference);return 1;
}
int compat_fireclick(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="ClickDetector"||h->node->destroyed){luaL_error(L,"live ClickDetector required");return 0;}
    double distance=luaL_optnumber(L,2,0);if(!std::isfinite(distance)||distance<0){luaL_error(L,"distance must be finite and nonnegative");return 0;}
    std::string event=luaL_optstring(L,3,"MouseClick");
    if(event!="MouseClick"&&event!="RightMouseClick"&&event!="MouseHoverEnter"&&event!="MouseHoverLeave"){luaL_error(L,"invalid ClickDetector event");return 0;}
    Json player=h->world->local_player.empty()?Json(nullptr):Json{{"$ref",h->world->local_player}};
    if(h->node->server_owned&&h->world->persistent) {
        // Documented reflected left-click action; other action numbers have
        // not been established for this client revision.
        if(event!="MouseClick")return luaL_error(L,"server ClickDetector action is not implemented for this event"),0;
        try {enqueue_reflected_signal(*h->world,*h->node,"MouseActionReplicated",Json::array({player,0}));}
        catch(const std::exception& error){return luaL_error(L,"fireclickdetector failed: %s",error.what()),0;}
    }
    emit(*h->world,*h->node,event,Json::array({player}));return 0;
}
void install_instance_compat(lua_State* L,World& w) {
    lua_pushcfunction(L,compat_replicatesignal,"replicatesignal");lua_setglobal(L,"replicatesignal");
    for(auto item:{std::pair<const char*,lua_CFunction>{"cloneref",compat_cloneref},{"compareinstances",compat_compareinstances},{"getcallbackvalue",compat_callback},{"fireclickdetector",compat_fireclick},{"firesignal",compat_firesignal},{"getconnections",compat_connections},{"isrbxactive",compat_active},{"isgameactive",compat_active}}){lua_pushcfunction(L,item.second,item.first);lua_setglobal(L,item.first);}
    for(auto item:{std::pair<const char*,lua_CFunction>{"checkcaller",compat_checkcaller},{"gethui",compat_hui},{"setfpscap",compat_fps}}){lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,item.second,item.first,1,nullptr);lua_setglobal(L,item.first);}
    lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,compat_callingscript,"getcallingscript",1,nullptr);
    lua_pushvalue(L,-1);lua_setglobal(L,"getcallingscript");lua_setglobal(L,"getcaller");
    lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,compat_scriptfromthread,"getscriptfromthread",1,nullptr);
    lua_pushvalue(L,-1);lua_setglobal(L,"getscriptfromthread");
    lua_setfield(L,LUA_REGISTRYINDEX,"Headless.ScriptFromThread");
    const char* properties[]={"gethiddenproperty","sethiddenproperty","isscriptable","setscriptable"};
    for(int i=0;i<4;++i){lua_pushinteger(L,i);lua_pushcclosurek(L,compat_property,properties[i],1,nullptr);lua_setglobal(L,properties[i]);}
    for(auto name:{"getscriptbytecode","dumpstring","getscripthash"}){lua_pushinteger(L,std::strcmp(name,"getscripthash")==0?1:0);lua_pushcclosurek(L,compat_scriptbytes,name,1,nullptr);lua_setglobal(L,name);}
    lua_pushinteger(L,2);lua_pushcclosurek(L,compat_scriptbytes,"normalized script bytecode",1,nullptr);
    lua_setfield(L,LUA_REGISTRYINDEX,"Headless.NormalizedScriptBytecode");
    lua_newtable(L);for(auto item:{std::pair<const char*,lua_CFunction>{"invalidate",compat_cache_invalidate},{"iscached",compat_cache_iscached},{"replace",compat_cache_replace}}){lua_pushcfunction(L,item.second,item.first);lua_setfield(L,-2,item.first);}
    lua_getfield(L,-1,"iscached");lua_setfield(L,-2,"cached");
    for(const char* name:{"cloneref","compareinstances"}){lua_getglobal(L,name);lua_setfield(L,-2,name);}
    lua_setglobal(L,"cache");
    const char* names[]={"getinstances","getnilinstances","getscripts"};
    for(int mode=0;mode<3;++mode){lua_pushlightuserdata(L,&w);lua_pushinteger(L,mode);lua_pushcclosurek(L,compat_instances,names[mode],2,nullptr);lua_setglobal(L,names[mode]);}
}
