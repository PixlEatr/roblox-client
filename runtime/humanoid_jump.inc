// Local scheduler requests only. The prediction controller decides physical support.
bool humanoid_jump_setting(const Node& n,const std::string& key) {
    return n.class_name=="Humanoid"&&(key=="UseJumpPower"||key=="JumpPower"||key=="JumpHeight");
}
bool humanoid_jump_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="Humanoid"||key!="Jump")return false;
    if(lua_type(L,3)!=LUA_TBOOLEAN)throw std::runtime_error("Jump must be a boolean");
    if(n.destroyed)throw std::runtime_error("cannot request a jump on a destroyed Humanoid");
    const bool requested=lua_toboolean(L,3)!=0;
    const Json* previous=property_value(w,n,"Jump");
    const bool changed=!previous||*previous!=Json(requested);
    std::vector<Json> entries;
    if(changed)entries.push_back(Json{{"kind","set"},{"id",n.id},{"property","Jump"},{"value",requested}});
    check_mutation_budget(w,entries);
    movement_bindings::request_jump(L,n.id,requested&&humanoid_state_enabled(w,n,3),static_cast<int>(w.mutations.size()));
    if(requested)w.pending_jump_requests.insert(n.id);
    w.local_properties[n.id]["Jump"]=requested;
    append_mutations(w,std::move(entries));
    if(changed)property_events(w,n,"Jump");
    return true;
}
void flush_jump_requests(lua_State* L,World& w) {
    for(const auto& id:w.pending_jump_requests) {
        Node* node=find_node(w,id);
        Json settings=Json::object();
        if(!node||node->destroyed)movement_bindings::request_jump(L,id,false);
        else {
            // Capture effective settings at the scheduler boundary, including
            // writes made after Jump=true. Missing received settings stay absent.
            for(const char* key:{"UseJumpPower","JumpPower","JumpHeight","Health","PlatformStand","Sit"})
                if(const Json* value=property_value(w,*node,key))settings[key]=*value;
            const Json* value=property_value(w,*node,"Jump");
            const bool changed=value&&*value!=Json(false);
            w.local_properties[id]["Jump"]=false;
            if(changed)property_events(w,*node,"Jump");
        }
        movement_bindings::finish_jump(L,id,settings.dump());
    }
    w.pending_jump_requests.clear();
}
