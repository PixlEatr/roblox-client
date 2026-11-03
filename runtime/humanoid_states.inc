// Humanoid state API backed by local prediction feedback. Requests are queued
// for the controller; this overlay never changes received replication data.
constexpr const char* kHumanoidStateEnumMeta = "WorldSnapshot.HumanoidState.Enum";
constexpr const char* kHumanoidStateItemMeta = "WorldSnapshot.HumanoidState.Item";
constexpr const char* kHumanoidStateNames[] = {
    "FallingDown", "Ragdoll", "GettingUp", "Jumping", "Swimming", "Freefall",
    "Flying", "Landed", "Running", "RunningNoPhysics", "StrafingNoPhysics",
    "Climbing", "Seated", "PlatformStanding", "Dead", "Physics", "None"
};
constexpr int kHumanoidStateValues[] = {0,1,2,3,4,5,6,7,8,10,11,12,13,14,15,16,18};
struct HumanoidStateItem { int value; };
int humanoid_state_from_name(const std::string& name) {
    for(int i=0;i<int(std::size(kHumanoidStateNames));++i)
        if(name==kHumanoidStateNames[i])return kHumanoidStateValues[i];
    return -1;
}
const char* humanoid_state_name(int value) {
    for(int i=0;i<int(std::size(kHumanoidStateValues));++i)
        if(value==kHumanoidStateValues[i])return kHumanoidStateNames[i];
    return nullptr;
}
void push_humanoid_state_item(lua_State* L,int value) {
    if(!humanoid_state_name(value))throw std::runtime_error("invalid HumanoidStateType");
    auto* item=static_cast<HumanoidStateItem*>(lua_newuserdatatagged(L,sizeof(HumanoidStateItem),0));
    item->value=value;luaL_getmetatable(L,kHumanoidStateItemMeta);lua_setmetatable(L,-2);
}
int checked_humanoid_state(lua_State* L,int index) {
    auto* item=static_cast<HumanoidStateItem*>(luaL_checkudata(L,index,kHumanoidStateItemMeta));return item->value;
}
int humanoid_state_item_index(lua_State* L) {
    auto* item=static_cast<HumanoidStateItem*>(luaL_checkudata(L,1,kHumanoidStateItemMeta));
    const char* key=luaL_checkstring(L,2);
    if(std::strcmp(key,"Name")==0)lua_pushstring(L,humanoid_state_name(item->value));
    else if(std::strcmp(key,"Value")==0)lua_pushinteger(L,item->value);
    else if(std::strcmp(key,"EnumType")==0)lua_getfield(L,LUA_REGISTRYINDEX,"Headless.HumanoidStateType");
    else {luaL_error(L,"unknown HumanoidStateType item member");return 0;}return 1;
}
int humanoid_state_item_string(lua_State* L) {
    auto* item=static_cast<HumanoidStateItem*>(luaL_checkudata(L,1,kHumanoidStateItemMeta));
    lua_pushfstring(L,"Enum.HumanoidStateType.%s",humanoid_state_name(item->value));return 1;
}
int humanoid_state_item_equal(lua_State* L) {
    auto* a=static_cast<HumanoidStateItem*>(luaL_checkudata(L,1,kHumanoidStateItemMeta));
    auto* b=has_meta(L,2,kHumanoidStateItemMeta)?static_cast<HumanoidStateItem*>(lua_touserdata(L,2)):nullptr;
    lua_pushboolean(L,b&&a->value==b->value);return 1;
}
int humanoid_state_enum_items(lua_State* L) {
    luaL_checkudata(L,1,kHumanoidStateEnumMeta);
    lua_createtable(L,int(std::size(kHumanoidStateNames)),0);
    for(int i=0;i<int(std::size(kHumanoidStateNames));++i) {
        push_humanoid_state_item(L,kHumanoidStateValues[i]);lua_rawseti(L,-2,i+1);
    }
    return 1;
}
int humanoid_state_enum_index(lua_State* L) {
    const char* key=luaL_checkstring(L,2);
    if(std::strcmp(key,"GetEnumItems")==0){lua_pushcfunction(L,humanoid_state_enum_items,"GetEnumItems");return 1;}
    int value=humanoid_state_from_name(key);if(value<0){luaL_error(L,"unknown HumanoidStateType item: %s",key);return 0;}
    push_humanoid_state_item(L,value);return 1;
}
int humanoid_state_enum_string(lua_State* L){lua_pushliteral(L,"HumanoidStateType");return 1;}
int humanoid_state_readonly(lua_State* L){luaL_error(L,"HumanoidStateType enum is immutable");return 0;}
void install_humanoid_state_enum(lua_State* L) {
    luaL_newmetatable(L,kHumanoidStateItemMeta);
    lua_pushcfunction(L,humanoid_state_item_index,"__index");lua_setfield(L,-2,"__index");
    lua_pushliteral(L,"EnumItem");lua_setfield(L,-2,"__type");
    lua_pushcfunction(L,humanoid_state_readonly,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushcfunction(L,humanoid_state_item_string,"__tostring");lua_setfield(L,-2,"__tostring");
    lua_pushcfunction(L,humanoid_state_item_equal,"__eq");lua_setfield(L,-2,"__eq");lua_pop(L,1);
    luaL_newmetatable(L,kHumanoidStateEnumMeta);
    lua_pushliteral(L,"Enum");lua_setfield(L,-2,"__type");
    lua_pushcfunction(L,humanoid_state_enum_index,"__index");lua_setfield(L,-2,"__index");
    lua_pushcfunction(L,humanoid_state_readonly,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushcfunction(L,humanoid_state_enum_string,"__tostring");lua_setfield(L,-2,"__tostring");lua_pop(L,1);
    // install() supplies the Enum table before publishing it as a global.
    lua_pushvalue(L,-1);
    auto* enum_value=static_cast<int*>(lua_newuserdatatagged(L,sizeof(int),0));*enum_value=1;
    luaL_getmetatable(L,kHumanoidStateEnumMeta);lua_setmetatable(L,-2);
    lua_pushvalue(L,-1);lua_setfield(L,LUA_REGISTRYINDEX,"Headless.HumanoidStateType");
    lua_setfield(L,-2,"HumanoidStateType");lua_pop(L,1);
}
int humanoid_get_state(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="Humanoid"){luaL_error(L,"Humanoid receiver required");return 0;}
    const Json* local=local_value(*h->world,*h->node,"__HumanoidStateFeedback");
    int value=-1;
    if(local&&local->is_number_integer())value=local->get<int>();
    else if(const Json* received=property_value(*h->world,*h->node,"HumanoidStateType")) {
        if(received->is_number_integer())value=received->get<int>();
        else if(received->is_object()&&received->contains("$enum")&&(*received)["$enum"].is_object()&&(*received)["$enum"].contains("value"))value=(*received)["$enum"]["value"].get<int>();
    }
    if(value<0){luaL_error(L,"Humanoid state is unavailable until received state or local physics feedback exists");return 0;}
    try{push_humanoid_state_item(L,value);return 1;}catch(const std::exception& e){luaL_error(L,"Humanoid state unavailable: %s",e.what());return 0;}
}
int humanoid_change_state(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;Node& n=*h->node;
    if(n.class_name!="Humanoid"){luaL_error(L,"Humanoid receiver required");return 0;}
    int state=checked_humanoid_state(L,2);
    if(n.destroyed){luaL_error(L,"cannot change state on a destroyed Humanoid");return 0;}
    const Json* enabled=local_value(w,n,"__HumanoidStateEnabled_"+std::to_string(state));
    if(enabled&&enabled->is_boolean()&&!enabled->get<bool>())return 0;
    const Json* health=property_value(w,n,"Health");if(health&&health->is_number()&&health->get<double>()<=0&&state!=15){lua_pushnil(L);return 1;}
    movement_bindings::enqueue_state(L,n.id,state,humanoid_state_name(state));
    movement_bindings::tag_latest_mutation_cursor(L,static_cast<int>(w.mutations.size()));
    return 0;
}
int humanoid_set_state_enabled(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="Humanoid"){luaL_error(L,"Humanoid receiver required");return 0;}
    if(h->node->destroyed){luaL_error(L,"cannot set state enabled on a destroyed Humanoid");return 0;}
    int state=checked_humanoid_state(L,2);if(lua_type(L,3)!=LUA_TBOOLEAN){luaL_error(L,"SetStateEnabled expects a boolean");return 0;}
    bool enabled=lua_toboolean(L,3)!=0;
    movement_bindings::enqueue_state_enabled(L,h->node->id,state,humanoid_state_name(state),enabled);
    movement_bindings::tag_latest_mutation_cursor(L,static_cast<int>(h->world->mutations.size()));
    h->world->local_properties[h->node->id]["__HumanoidStateEnabled_"+std::to_string(state)]=enabled;return 0;
}
int humanoid_get_state_enabled(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="Humanoid"){luaL_error(L,"Humanoid receiver required");return 0;}
    int state=checked_humanoid_state(L,2);const Json* enabled=local_value(*h->world,*h->node,"__HumanoidStateEnabled_"+std::to_string(state));
    lua_pushboolean(L,!enabled||!enabled->is_boolean()||enabled->get<bool>());return 1;
}
bool humanoid_state_enabled(World& w,Node& n,int state_value) {
    const Json* enabled=local_value(w,n,"__HumanoidStateEnabled_"+std::to_string(state_value));
    return !enabled||!enabled->is_boolean()||enabled->get<bool>();
}
bool humanoid_state_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="Humanoid")return false;
    if(key=="GetState"){lua_pushcfunction(L,humanoid_get_state,"GetState");return true;}
    if(key=="ChangeState"){lua_pushcfunction(L,humanoid_change_state,"ChangeState");return true;}
    if(key=="SetStateEnabled"){lua_pushcfunction(L,humanoid_set_state_enabled,"SetStateEnabled");return true;}
    if(key=="GetStateEnabled"){lua_pushcfunction(L,humanoid_get_state_enabled,"GetStateEnabled");return true;}
    if(key=="StateChanged"){push_signal(L,signal_for(w,n,"StateChanged"));return true;}
    if(key=="Jumping"||key=="FreeFalling"){push_signal(L,signal_for(w,n,key));return true;}
    if(key=="Sit"||key=="PlatformStand") {const Json* value=property_value(w,n,key);if(value&&value->is_boolean())lua_pushboolean(L,value->get<bool>());else lua_pushboolean(L,false);return true;}
    return false;
}
bool humanoid_state_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="Humanoid"||(key!="Sit"&&key!="PlatformStand"))return false;
    if(lua_type(L,3)!=LUA_TBOOLEAN)throw std::runtime_error(key+" must be a boolean");
    if(n.destroyed)throw std::runtime_error("cannot change state on a destroyed Humanoid");
    bool value=lua_toboolean(L,3)!=0;Json entry{{"kind","set"},{"id",n.id},{"property",key},{"value",value}};
    const Json* previous=property_value(w,n,key);
    const bool changed=!previous||*previous!=Json(value);
    // A repeated PlatformStand assignment can cancel an intervening state
    // request before feedback arrives. Preserve its order without emitting a
    // property-change event when the visible value did not change.
    if(changed||key=="PlatformStand") {
        std::vector<Json> entries{entry};check_mutation_budget(w,entries);
        w.local_properties[n.id][key]=value;append_mutations(w,std::move(entries));
        if(changed)property_events(w,n,key);
    }
    return true;
}
