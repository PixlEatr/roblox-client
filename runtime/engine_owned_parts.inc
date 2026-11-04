// Host-computed local state for received isolated Parts. This channel never
// changes replication state or emits authored script mutations. The scope is
// supplied by the session owner from the actual received World.client_scope.
Json apply_engine_owned_parts_impl(World& w,const Json& batch) {
    if(!batch.is_object()||batch.size()!=3||!batch.contains("scope")||
       !batch.contains("source_revision")||!batch.contains("frames")||
       !batch["scope"].is_number_integer()||!batch["source_revision"].is_number_integer()||
       !batch["frames"].is_array())
        throw std::runtime_error("invalid owned-part engine batch");
    const auto scope=batch["scope"].get<int64_t>();
    const auto revision=batch["source_revision"].get<int64_t>();
    const Json& frames=batch["frames"];
    if(scope<=0||scope>=0xffffffffLL||revision<0||revision>9007199254740991LL||
       frames.empty()||frames.size()>32)
        throw std::runtime_error("owned-part engine batch exceeds limits");
    auto stale=[](){return Json{{"status","stale_authority"},{"frames_applied",0}};};
    if(double(revision)>w.sequence)return stale();
    auto reference=[](const Json* value)->std::string {
        if(value&&value->is_object()&&value->size()==1&&value->contains("$ref")&&
           (*value)["$ref"].is_string())return (*value)["$ref"].get<std::string>();
        return "";
    };
    auto vector=[](const Json& value) {
        if(!value.is_array()||value.size()!=3)throw std::runtime_error("invalid owned-part velocity");
        for(const auto& item:value)
            if(!item.is_number()||!std::isfinite(item.get<double>())||std::abs(item.get<double>())>=1e9)
                throw std::runtime_error("invalid owned-part velocity");
        return Json{{"$vector3",value}};
    };
    std::unordered_set<std::string> connected,characters;
    for(const auto& [id,node]:w.nodes) {
        if(node.destroyed)continue;
        if(node.class_name=="Player") {
            const auto character=reference(property_value(w,node,"Character"));
            if(!character.empty())characters.insert(character);
        }
        if(node.class_name=="WeldConstraint"||class_is_a(w,node.class_name,"JointInstance"))
            for(const auto* name:{"Part0","Part1"}) {
                const auto part=reference(property_value(w,node,name));
                if(!part.empty())connected.insert(part);
            }
        if(class_is_a(w,node.class_name,"Constraint"))
            for(const auto* name:{"Attachment0","Attachment1"}) {
                Node* attachment=find_node(w,reference(property_value(w,node,name)));
                if(attachment&&!attachment->destroyed)connected.insert(parent_id(w,*attachment));
            }
        if(class_is_a(w,node.class_name,"BodyMover")&&node.class_name!="BodyVelocity")
            connected.insert(parent_id(w,node));
    }
    struct Write {Node* node;Json before,after,old_linear,old_angular,linear,angular;};
    std::vector<Write> writes;
    std::unordered_set<std::string> seen;
    for(const auto& frame:frames) {
        if(!frame.is_object()||frame.size()!=4||!frame.contains("id")||
           !frame["id"].is_string()||!frame.contains("cframe")||
           !frame.contains("linear_velocity")||!frame.contains("angular_velocity"))
            throw std::runtime_error("invalid owned-part engine frame");
        const std::string id=frame["id"].get<std::string>();
        if(id.empty()||id.size()>256||!seen.insert(id).second)
            throw std::runtime_error("duplicate or empty owned-part engine id");
        const Json& cf=frame["cframe"];
        if(!cf.is_object()||cf.size()!=1||!cf.contains("$cframe")||
           !cf["$cframe"].is_object()||cf["$cframe"].size()!=2||
           !cf["$cframe"].contains("position")||!cf["$cframe"].contains("rotation"))
            throw std::runtime_error("owned-part engine requires canonical CFrame");
        (void)pivot_decode(cf);
        Json linear=vector(frame["linear_velocity"]),angular=vector(frame["angular_velocity"]);
        Node* node=find_node(w,id);
        if(!node||node->destroyed||!node->server_owned||node->class_name!="Part"||connected.count(id))return stale();
        const auto owner=node->properties.find("NetworkOwnerV3");
        const Json* anchored=property_value(w,*node,"Anchored");
        if(owner==node->properties.end()||!owner->is_number_integer()||owner->get<int64_t>()!=scope||
           !anchored||!anchored->is_boolean()||anchored->get<bool>())return stale();
        for(const auto* name:{"PhysicsRepRootPart","PhysicsRepRootRef"}) {
            const Json* value=property_value(w,*node,name);
            if(value&&!value->is_null()&&reference(value)!=id)return stale();
        }
        for(const auto* name:{"CFrame","ReplicationPV","Parent","NetworkOwnerV3","Anchored",
                             "PhysicsRepRootPart","PhysicsRepRootRef"}) {
            auto version=node->revisions.find(name);
            if(version!=node->revisions.end()&&(!version->is_number()||version->get<double>()>double(revision)))return stale();
        }
        Node* cursor=node;
        std::unordered_set<std::string> ancestry;
        bool workspace=false;
        while(cursor&&!cursor->destroyed&&ancestry.size()<128&&ancestry.insert(cursor->id).second) {
            if(characters.count(cursor->id)||cursor->class_name=="WorldModel")return stale();
            if(cursor->class_name=="Workspace"){workspace=true;break;}
            cursor=find_node(w,parent_id(w,*cursor));
        }
        if(!workspace)return stale();
        const Json* old_linear=property_value(w,*node,"AssemblyLinearVelocity");
        const Json* old_angular=property_value(w,*node,"AssemblyAngularVelocity");
        writes.push_back({node,previous_part_transform(w,*node),part_fields_from_frame(cf),
                          old_linear?*old_linear:Json(nullptr),old_angular?*old_angular:Json(nullptr),
                          std::move(linear),std::move(angular)});
    }
    // Derive and validate every frame before any visible local property write.
    for(const auto& write:writes) {
        for(auto it=write.after.begin();it!=write.after.end();++it)
            w.local_properties[write.node->id][it.key()]=it.value();
        w.local_properties[write.node->id]["AssemblyLinearVelocity"]=write.linear;
        w.local_properties[write.node->id]["AssemblyAngularVelocity"]=write.angular;
    }
    for(const auto& write:writes) {
        part_transform_events(w,*write.node,write.before,write.after);
        if(write.old_linear!=write.linear)property_events(w,*write.node,"AssemblyLinearVelocity");
        if(write.old_angular!=write.angular)property_events(w,*write.node,"AssemblyAngularVelocity");
    }
    return Json{{"status","applied"},{"frames_applied",writes.size()}};
}
