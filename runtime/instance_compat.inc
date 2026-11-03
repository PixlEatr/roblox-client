void enforce_scriptability(lua_State* L,World& w,Node& n,std::string& key) {
    bool hidden=n.class_name=="Fire"&&key=="size_xml";bool readable=!hidden;
    auto node=w.scriptability.find(n.id);if(node!=w.scriptability.end()){auto field=node->second.find(key);if(field!=node->second.end())readable=field->second;}
    if(!readable)luaL_error(L,"property is not scriptable");
    if(hidden)key="Size";
}
int compat_property(lua_State* L) {
    auto* h=checked_handle(L,1);std::string key=luaL_checkstring(L,2);int operation=lua_tointeger(L,lua_upvalueindex(1));
    bool hidden=h->node->class_name=="Fire"&&key=="size_xml";std::string canonical=hidden?"Size":key;
    if(canonical!="Name"&&canonical!="Parent"&&canonical!="ClassName"&&!property_descriptor(*h->node,canonical)&&!h->node->properties.contains(canonical)){luaL_error(L,"unknown property");return 0;}
    auto& fields=h->world->scriptability[h->node->id];auto field=fields.find(key);bool previous=field==fields.end()?!hidden:field->second;
    if(operation==2){lua_pushboolean(L,previous);return 1;}
    if(operation==3){luaL_checktype(L,3,LUA_TBOOLEAN);fields[key]=lua_toboolean(L,3);lua_pushboolean(L,previous);return 1;}
    auto original=fields.find(canonical);bool existed=original!=fields.end(),old=existed?original->second:false;fields[canonical]=true;
    lua_pushcfunction(L,operation==0?instance_index:instance_newindex,"property access");lua_pushvalue(L,1);lua_pushlstring(L,canonical.data(),canonical.size());if(operation==1)lua_pushvalue(L,3);
    int result=lua_pcall(L,operation==0?2:3,operation==0?1:0,0);
    if(existed)fields[canonical]=old;else fields.erase(canonical);
    if(result!=LUA_OK){lua_error(L);return 0;}
    lua_pushboolean(L,hidden);return operation==0?2:1;
}
int compat_fps(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));double value=luaL_checknumber(L,1);
    if(!std::isfinite(value)||value<0||value>1000){luaL_error(L,"frame cap must be 0..1000");return 0;}w->render_cap=value;return 0;
}
int compat_scriptbytes(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="ModuleScript"&&h->node->class_name!="LocalScript"&&h->node->class_name!="Script"){luaL_error(L,"script Instance required");return 0;}
    try {
        const Json* code=&h->node->script_code;
        if(!code->is_object()&&!h->node->script_origin.empty()){auto* origin=find_node(*h->world,h->node->script_origin);if(origin)code=&origin->script_code;}
        if(!code->is_object())throw std::runtime_error("script code is unavailable");
        std::string bytes;
        if(code->contains("source"))bytes=Luau::compile(code->at("source").get<std::string>());
        else if(code->contains("bytecode_hex")){
            const auto& hex=code->at("bytecode_hex").get_ref<const std::string&>();
            auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1;};
            if(hex.size()%2)throw std::runtime_error("invalid bytecode encoding");
            for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid bytecode encoding");bytes+=char(a*16+b);}
        }else throw std::runtime_error("script code is unavailable");
        if(lua_toboolean(L,lua_upvalueindex(1)))bytes=source_crypto::hex(source_crypto::sha256(bytes));
        lua_pushlstring(L,bytes.data(),bytes.size());return 1;
    }catch(const std::exception& e){luaL_error(L,"script code: %s",e.what());return 0;}
}
int compat_namecall_continue(lua_State* L,int status){if(status!=LUA_OK){lua_error(L);return 0;}return lua_gettop(L);}
int compat_namecall(lua_State* L) {
    const char* method=lua_namecallatom(L,nullptr);if(!method){luaL_error(L,"missing namecall method");return 0;}
    int arguments=lua_gettop(L);lua_pushcfunction(L,instance_index,"Instance.__index");lua_pushvalue(L,1);lua_pushstring(L,method);lua_call(L,2,1);
    if(!lua_isfunction(L,-1)){luaL_error(L,"Instance member is not callable");return 0;}
    lua_insert(L,1);return lua_callyieldable(L,arguments,LUA_MULTRET);
}
int compat_checkcaller(lua_State* L){auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));lua_pushboolean(L,!w->scheduler||w->scheduler->owner(L).empty());return 1;}
int compat_callingscript(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    const auto owner=w->scheduler?w->scheduler->owner(L):std::string();
    const bool script=owner.compare(0,7,"script:")==0,module=owner.compare(0,7,"module:")==0;
    auto* node=(script||module)?find_node(*w,owner.substr(7)):nullptr;
    if(node&&(node->class_name=="Script"||node->class_name=="LocalScript"||node->class_name=="ModuleScript"))
        push_node(L,*w,node);
    else lua_pushnil(L);
    return 1;
}
int compat_active(lua_State* L){lua_pushboolean(L,false);return 1;} // This host owns no native input window.
int compat_hui(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    lua_getfield(L,LUA_REGISTRYINDEX,"Headless.OverlayRoot");
    if(has_meta(L,-1,kInstanceMeta)&&!checked_handle(L,-1)->node->destroyed)return 1;
    lua_pop(L,1);auto* core=ensure_client_service(*w,"CoreGui");
    lua_pushlightuserdata(L,w);lua_pushcclosurek(L,instance_new,"Instance.new",1,nullptr);lua_pushliteral(L,"ScreenGui");push_node(L,*w,core);lua_call(L,2,1);
    lua_pushvalue(L,-1);lua_setfield(L,LUA_REGISTRYINDEX,"Headless.OverlayRoot");return 1;
}
int compat_connection_toggle(lua_State* L) {
    auto* c=static_cast<ConnectionHandle*>(luaL_checkudata(L,1,kConnectionMeta))->connection;
    bool enable=lua_toboolean(L,lua_upvalueindex(1));
    if(!c->auto_disconnected&&!c->manually_disconnected)c->connected=enable;return 0;
}
int compat_connection_fire(lua_State* L) {
    auto* c=static_cast<ConnectionHandle*>(luaL_checkudata(L,1,kConnectionMeta))->connection;
    if(!c->connected||c->signal->node->destroyed)return 0;
    auto* scheduler=c->signal->world->scheduler;if(!scheduler){luaL_error(L,"connection dispatch requires a scheduler");return 0;}
    int count=lua_gettop(L)-1;
    scheduler->callback(L,c->callback,[&](lua_State* target){for(int i=0;i<count;++i)lua_pushvalue(L,i+2);if(target!=L)lua_xmove(L,target,count);return count;},[c](){return c->connected&&!c->signal->node->destroyed;},c->owner);return 0;
}
bool connection_compat_index(lua_State* L,Connection& c,const char* key) {
    if(std::strcmp(key,"Enabled")==0)lua_pushboolean(L,c.connected);
    else if(std::strcmp(key,"ForeignState")==0)lua_pushboolean(L,false);
    else if(std::strcmp(key,"LuaConnection")==0){lua_getref(L,c.callback);lua_pushboolean(L,lua_isLfunction(L,-1));}
    else if(std::strcmp(key,"Function")==0)lua_getref(L,c.callback);
    else if(std::strcmp(key,"Thread")==0)lua_getref(L,c.creator_ref);
    else if(std::strcmp(key,"Enable")==0||std::strcmp(key,"Disable")==0){lua_pushboolean(L,std::strcmp(key,"Enable")==0);lua_pushcclosurek(L,compat_connection_toggle,key,1,nullptr);}
    else if(std::strcmp(key,"Fire")==0||std::strcmp(key,"Defer")==0)lua_pushcfunction(L,compat_connection_fire,key);
    else return false;return true;
}
int compat_connections(lua_State* L) {
    auto* signal=static_cast<SignalHandle*>(luaL_checkudata(L,1,kSignalMeta))->signal;
    lua_newtable(L);int count=0;for(auto& c:signal->world->connections){if(c->signal!=signal||c->auto_disconnected||c->manually_disconnected)continue;
        auto* value=static_cast<ConnectionHandle*>(lua_newuserdatatagged(L,sizeof(ConnectionHandle),0));value->connection=c.get();luaL_getmetatable(L,kConnectionMeta);lua_setmetatable(L,-2);lua_rawseti(L,-2,++count);
    }return 1;
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
    auto* h=checked_handle(L,1);lua_pushlightuserdata(L,h->node);lua_pushnil(L);lua_rawset(L,LUA_REGISTRYINDEX);return 0;
}
int compat_cache_iscached(lua_State* L) {
    auto* h=checked_handle(L,1);lua_pushlightuserdata(L,h->node);lua_rawget(L,LUA_REGISTRYINDEX);lua_pushboolean(L,!lua_isnil(L,-1));return 1;
}
int compat_cache_replace(lua_State* L) {
    auto* h=checked_handle(L,1);auto* replacement=checked_handle(L,2);
    if(h->world!=replacement->world){luaL_error(L,"replacement must belong to the same world");return 0;}
    lua_pushlightuserdata(L,h->node);lua_pushvalue(L,2);lua_rawset(L,LUA_REGISTRYINDEX);return 0;
}
int compat_instances(lua_State* L) {
    auto* w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));int mode=lua_tointeger(L,lua_upvalueindex(2));
    lua_newtable(L);int count=0;
    for(const auto& id:w->order){auto& n=w->nodes.at(id);if(n.destroyed)continue;
        if(mode==1&&!parent_id(*w,n).empty())continue;
        if(mode==2&&n.class_name!="ModuleScript"&&n.class_name!="LocalScript"&&n.class_name!="Script")continue;
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
    emit(*h->world,*h->node,event,Json::array({player}));return 0;
}
void install_instance_compat(lua_State* L,World& w) {
    for(auto item:{std::pair<const char*,lua_CFunction>{"cloneref",compat_cloneref},{"compareinstances",compat_compareinstances},{"getcallbackvalue",compat_callback},{"fireclickdetector",compat_fireclick},{"getconnections",compat_connections},{"isrbxactive",compat_active},{"isgameactive",compat_active}}){lua_pushcfunction(L,item.second,item.first);lua_setglobal(L,item.first);}
    for(auto item:{std::pair<const char*,lua_CFunction>{"checkcaller",compat_checkcaller},{"gethui",compat_hui},{"setfpscap",compat_fps}}){lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,item.second,item.first,1,nullptr);lua_setglobal(L,item.first);}
    lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,compat_callingscript,"getcallingscript",1,nullptr);
    lua_pushvalue(L,-1);lua_setglobal(L,"getcallingscript");lua_setglobal(L,"getcaller");
    const char* properties[]={"gethiddenproperty","sethiddenproperty","isscriptable","setscriptable"};
    for(int i=0;i<4;++i){lua_pushinteger(L,i);lua_pushcclosurek(L,compat_property,properties[i],1,nullptr);lua_setglobal(L,properties[i]);}
    for(auto name:{"getscriptbytecode","dumpstring","getscripthash"}){lua_pushboolean(L,std::strcmp(name,"getscripthash")==0);lua_pushcclosurek(L,compat_scriptbytes,name,1,nullptr);lua_setglobal(L,name);}
    lua_newtable(L);for(auto item:{std::pair<const char*,lua_CFunction>{"invalidate",compat_cache_invalidate},{"iscached",compat_cache_iscached},{"replace",compat_cache_replace}}){lua_pushcfunction(L,item.second,item.first);lua_setfield(L,-2,item.first);}lua_setglobal(L,"cache");
    const char* names[]={"getinstances","getnilinstances","getscripts"};
    for(int mode=0;mode<3;++mode){lua_pushlightuserdata(L,&w);lua_pushinteger(L,mode);lua_pushcclosurek(L,compat_instances,names[mode],2,nullptr);lua_setglobal(L,names[mode]);}
}
