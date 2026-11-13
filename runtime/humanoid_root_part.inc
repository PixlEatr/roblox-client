// Humanoid.RootPart is read-only and NotReplicated. Resolve it from the
// Humanoid's own rig when the transport does not carry an explicit reference.
// This resolver models standard rigs; custom root names and multi-part
// assembly-root selection require engine topology not present in snapshots.
Node* humanoid_root_part(World& w,Node& humanoid) {
    if(humanoid.class_name!="Humanoid"||humanoid.destroyed)return nullptr;
    const std::string model_id=parent_id(w,humanoid);
    Node* model=find_node(w,model_id);
    if(!model||model->destroyed||!class_is_a(w,model->class_name,"Model"))return nullptr;

    // Preserve an explicit, valid engine reference when supplied by a source
    // that can observe it. Reject stale, foreign, or non-BasePart references
    // instead of silently substituting a different part from another rig.
    if(const Json* explicit_value=property_value(w,humanoid,"RootPart");explicit_value&&!explicit_value->is_null()) {
        if(!explicit_value->is_object()||explicit_value->size()!=1||!explicit_value->contains("$ref")||!(*explicit_value)["$ref"].is_string())return nullptr;
        Node* referenced=find_node(w,(*explicit_value)["$ref"].get<std::string>());
        if(!referenced||referenced->destroyed||!class_is_a(w,referenced->class_name,"BasePart")||parent_id(w,*referenced)!=model_id)return nullptr;
        return referenced;
    }

    for(const auto& id:w.order) {
        Node& candidate=w.nodes.at(id);
        if(!candidate.destroyed&&parent_id(w,candidate)==model_id
           &&instance_name(w,candidate)=="HumanoidRootPart"
           &&class_is_a(w,candidate.class_name,"BasePart"))return &candidate;
    }
    return nullptr;
}

std::string humanoid_root_part_origin(World& w,const Node& humanoid) {
    if(Node* root=humanoid_root_part(w,const_cast<Node&>(humanoid))) {
        auto explicit_origin=humanoid.origins.find("RootPart");
        auto explicit_value=humanoid.properties.find("RootPart");
        if(explicit_value!=humanoid.properties.end()&&explicit_value->is_object()
           &&explicit_value->contains("$ref")&&(*explicit_value)["$ref"].is_string()
           &&(*explicit_value)["$ref"].get<std::string>()==root->id
           &&explicit_origin!=humanoid.origins.end()&&explicit_origin->is_string()
           &&explicit_origin->get<std::string>()=="received")return "received";
        return root->server_owned?"derived_received":"local";
    }
    auto explicit_value=humanoid.properties.find("RootPart");
    if(explicit_value!=humanoid.properties.end()&&explicit_value->is_object()
       &&explicit_value->contains("$ref"))return "unresolved_reference";
    auto explicit_origin=humanoid.origins.find("RootPart");
    if(explicit_origin!=humanoid.origins.end()&&explicit_origin->is_string()
       &&explicit_origin->get<std::string>()=="class_default")return "class_default";
    if(!humanoid.server_owned)return "class_default";
    return "derived_received";
}

void watch_humanoid_root_part(World& w,Node& humanoid,const std::string& key) {
    if(humanoid.class_name!="Humanoid"||(key!="RootPart"&&key!="Changed")
       ||w.humanoid_root_part_watches.count(humanoid.id))return;
    Node* root=humanoid_root_part(w,humanoid);
    w.humanoid_root_part_watches[humanoid.id]=root?Json{{"$ref",root->id}}:Json(nullptr);
}

bool sync_humanoid_root_part_watch(World& w,Node& humanoid) {
    auto it=w.humanoid_root_part_watches.find(humanoid.id);
    if(it==w.humanoid_root_part_watches.end())return false;
    Node* root=humanoid_root_part(w,humanoid);
    Json current=root?Json{{"$ref",root->id}}:Json(nullptr);
    if(current==it->second)return false;
    it->second=std::move(current);
    return true;
}

void refresh_humanoid_root_part_events(World& w) {
    if(w.humanoid_root_part_watches.empty())return;
    std::vector<std::string> changed;
    for(auto it=w.humanoid_root_part_watches.begin();it!=w.humanoid_root_part_watches.end();) {
        Node* humanoid=find_node(w,it->first);
        if(!humanoid||humanoid->destroyed) {it=w.humanoid_root_part_watches.erase(it);continue;}
        if(sync_humanoid_root_part_watch(w,*humanoid))changed.push_back(humanoid->id);
        ++it;
    }
    // Update every observed value before dispatch: callbacks may reenter the
    // hierarchy or destroy a Humanoid, but cannot invalidate map iterators.
    for(const auto& id:changed) {
        Node* humanoid=find_node(w,id);
        if(!humanoid||humanoid->destroyed)continue;
        emit(w,*humanoid,"Changed",Json::array({"RootPart"}));
        emit(w,*humanoid,"property:RootPart");
    }
}
