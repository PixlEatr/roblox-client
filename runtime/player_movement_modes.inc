// Per-player menu choices. Defaults and first-registration reset semantics
// follow the documented PlayerScripts API; these are client-local settings.
constexpr const char* movement_mode_types[]={"ComputerCameraMovementMode","ComputerMovementMode","TouchCameraMovementMode","TouchMovementMode"};
PlayerMovementModes& player_movement_modes(World& w,Node& node) {
    auto found=w.client_services.player_movement_modes.find(node.id);
    if(found!=w.client_services.player_movement_modes.end())return found->second;
    PlayerMovementModes state;
    state.values={std::vector<int>{0,1,2},std::vector<int>{0,1,2},std::vector<int>{0,1,2},std::vector<int>{0,1,4,5}};
    return w.client_services.player_movement_modes.emplace(node.id,std::move(state)).first->second;
}
int player_movement_mode_call(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="PlayerScripts"||h->node->destroyed)return luaL_error(L,"live PlayerScripts receiver required"),0;
    const int kind=lua_tointeger(L,lua_upvalueindex(1)),operation=lua_tointeger(L,lua_upvalueindex(2));
    if(operation==2&&!SourceScheduler::has_capability(L,"RobloxScript"))return luaL_error(L,"RobloxScript capability required"),0;
    int selected=0;
    if(operation==0&&!read_geometry_enum_item(L,2,movement_mode_types[kind],selected))
        return luaL_argerror(L,2,"matching movement-mode enum required"),0;
    auto& state=player_movement_modes(*h->world,*h->node);auto& values=state.values[kind];
    if(operation==2) {
        lua_createtable(L,values.size(),0);int index=0;
        for(int value:values){push_geometry_enum_item(L,movement_mode_types[kind],value);lua_rawseti(L,-2,++index);}
        return 1;
    }
    if(operation==1)values.clear();
    else {
        if(!state.registered[kind]){values.clear();state.registered[kind]=true;}
        if(std::find(values.begin(),values.end(),selected)==values.end()) {
            values.push_back(selected);std::sort(values.begin(),values.end());
        }
    }
    emit(*h->world,*h->node,std::string(movement_mode_types[kind])+"Registered");return 0;
}
bool player_movement_mode_index(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name!="PlayerScripts")return false;
    for(int kind=0;kind<4;++kind) {
        const std::string type=movement_mode_types[kind];
        if(key==type+"Registered") {
            if(!SourceScheduler::has_capability(L,"RobloxScript"))return luaL_error(L,"RobloxScript capability required"),false;
            push_signal(L,signal_for(w,node,key));return true;
        }
        int operation=key=="Register"+type?0:key=="Clear"+type+"s"?1:key=="GetRegistered"+type+"s"?2:-1;
        if(operation<0)continue;
        lua_pushinteger(L,kind);lua_pushinteger(L,operation);
        lua_pushcclosure(L,player_movement_mode_call,key.c_str(),2);return true;
    }
    return false;
}
