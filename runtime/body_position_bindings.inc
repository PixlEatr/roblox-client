// BodyPosition queries and events consume local controller results. Never
// infer a force from a target alone or write prediction to received properties.
int body_position_last_force(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="BodyPosition") {luaL_error(L,"GetLastForce requires BodyPosition");return 0;}
    const Json* value=local_value(*h->world,*h->node,"__BodyPositionForce");
    if(value)push_vector(L,*value);else lua_pushvector(L,0,0,0);
    return 1;
}

bool body_position_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="BodyPosition")return false;
    if(key=="GetLastForce"||key=="lastForce") {
        lua_pushcfunction(L,body_position_last_force,"GetLastForce");return true;
    }
    if(key=="ReachedTarget") {push_signal(L,signal_for(w,n,key));return true;}
    return false;
}

void body_position_written(World& w,Node& n,const std::string& key) {
    if(n.class_name!="BodyPosition")return;
    if(key=="Parent")w.local_properties[n.id].erase("__BodyPositionForce");
    if(key!="Position")return;
    Json& local=w.local_properties[n.id];
    local["__BodyPositionGeneration"]=local.value("__BodyPositionGeneration",uint64_t(0))+1;
    local.erase("__BodyPositionTargetToken");
}

void update_body_position_feedback(World& w,const Json& records,const std::string& character) {
    if(!records.is_array()||records.size()>1000)throw std::runtime_error("mover feedback must be a bounded array");
    std::unordered_set<std::string> ids;
    auto vector=[](const Json& value) {
        if(!value.is_array()||value.size()!=3)return false;
        for(const auto& item:value)if(!item.is_number()||!std::isfinite(item.get<double>()))return false;
        return true;
    };
    // Validate the whole batch before changing query state or queuing events.
    for(const auto& item:records) {
        if(!item.is_object()||!item.contains("id")||!item["id"].is_string()
            ||!item.contains("target_position")||!vector(item["target_position"])
            ||!item.contains("parent_id")||!item["parent_id"].is_string()
            ||!item.contains("last_force")||!vector(item["last_force"])
            ||!item.contains("reached")||!item["reached"].is_boolean()
            ||!item.contains("target_generation")||!item["target_generation"].is_number_unsigned()
            ||!item.contains("target_revision")||!(item["target_revision"].is_null()||item["target_revision"].is_number_integer())
            ||!ids.insert(item["id"].get<std::string>()).second)
            throw std::runtime_error("invalid BodyPosition feedback record");
    }
    std::unordered_set<std::string> active;
    for(const auto& item:records) {
        Node* mover=find_node(w,item["id"].get<std::string>());
        if(!mover||mover->destroyed||mover->class_name!="BodyPosition")continue;
        Node* part=find_node(w,parent_id(w,*mover));
        if(!part||!class_is_a(w,part->class_name,"BasePart")||part->id!=item["parent_id"].get<std::string>())continue;
        bool owned=is_descendant_of(w,*part,character);
        if(!owned) {
            const Json* root=property_value(w,*part,"AssemblyRootPart");
            Node* root_part=root&&root->is_object()&&root->contains("$ref")&&(*root)["$ref"].is_string()
                ?find_node(w,(*root)["$ref"].get<std::string>()):nullptr;
            owned=root_part&&is_descendant_of(w,*root_part,character);
        }
        if(!owned)continue;
        const Json* goal=property_value(w,*mover,"Position");
        if(!goal||!goal->is_object()||!goal->contains("$vector3")||(*goal)["$vector3"]!=item["target_position"])continue;
        Json& local=w.local_properties[mover->id];
        if(local.value("__BodyPositionGeneration",uint64_t(0))!=item["target_generation"].get<uint64_t>())continue;
        if(mover->revisions.value("Position",Json(nullptr))!=item["target_revision"])continue;
        active.insert(mover->id);
        local["__BodyPositionForce"]=item["last_force"];
        Json token=Json::array({item["target_position"],item["target_revision"],item["target_generation"]});
        if(item["reached"].get<bool>()&&local.value("__BodyPositionTargetToken",Json(nullptr))!=token) {
            emit(w,*mover,"ReachedTarget");local["__BodyPositionTargetToken"]=std::move(token);
        }
    }
    for(auto& [id,properties]:w.local_properties) {
        if(active.count(id)==0)properties.erase("__BodyPositionForce");
    }
}
