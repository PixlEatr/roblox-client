// Local health arithmetic and signals. This does not implement joint breaking,
// regeneration, death animation, or the complete Humanoid state machine.
double humanoid_health_number(World& w,Node& n,const char* key) {
    const Json* value=property_value(w,n,key);
    if(!value||!value->is_number()||!std::isfinite(value->get<double>()))
        throw std::runtime_error(std::string(key)+" is not a known finite health value");
    return value->get<double>();
}
void humanoid_health_events(World& w,Node& n,double old_health,double health) {
    emit(w,n,"HealthChanged",Json::array({health}));
    if(old_health>0&&health<=0) {
        const bool first=w.locally_dead_humanoids.insert(n.id).second;
        if(first)for(auto* ancestor:ancestors(w,parent_id(w,n)))
            if(ancestor->class_name=="Workspace"){emit(w,n,"Died");break;}
    }
}
void humanoid_set_health(World& w,Node& n,const std::string& key,double requested) {
    if(n.destroyed)throw std::runtime_error("health receiver is destroyed");
    if(!std::isfinite(requested))throw std::runtime_error("health must be finite");
    const double old_health=humanoid_health_number(w,n,"Health");
    const bool dead=w.locally_dead_humanoids.count(n.id)||old_health<=0;
    const Json* maximum_value=property_value(w,n,"MaxHealth");
    const bool maximum_known=maximum_value&&maximum_value->is_number()&&std::isfinite(maximum_value->get<double>());
    const double old_maximum=maximum_known?maximum_value->get<double>():0.0;
    double maximum;
    if(key=="MaxHealth")maximum=std::max(0.0,requested);
    else if(maximum_known)maximum=std::max(0.0,old_maximum);
    else if(dead||requested<=old_health)maximum=std::max(0.0,old_health);
    else throw std::runtime_error("healing requires a known MaxHealth");
    // Decreasing known Health is valid without inventing a missing maximum.
    // An explicit MaxHealth write establishes that local setting.
    const double health=dead?0.0:std::clamp(key=="Health"?requested:old_health,0.0,maximum);
    const bool health_changed=health!=old_health,max_changed=key=="MaxHealth"&&(!maximum_known||maximum!=old_maximum);
    if(!health_changed&&!max_changed)return;
    std::vector<Json> entries;
    if(max_changed)entries.push_back(Json{{"kind","set"},{"id",n.id},{"property","MaxHealth"},{"value",maximum}});
    if(health_changed)entries.push_back(Json{{"kind","set"},{"id",n.id},{"property","Health"},{"value",health}});
    check_mutation_budget(w,entries);
    if(max_changed)w.local_properties[n.id]["MaxHealth"]=maximum;
    if(health_changed)w.local_properties[n.id]["Health"]=health;
    append_mutations(w,std::move(entries));
    if(max_changed)property_events(w,n,"MaxHealth");
    if(health_changed)property_events(w,n,"Health");
    humanoid_health_events(w,n,old_health,health);
}
bool humanoid_health_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="Humanoid"||(key!="Health"&&key!="MaxHealth"))return false;
    if(lua_type(L,3)!=LUA_TNUMBER)throw std::runtime_error("health must be a number");
    humanoid_set_health(w,n,key,lua_tonumber(L,3));return true;
}
bool humanoid_forcefield(World& w,Node& n) {
    const std::string owner=parent_id(w,n);std::string root;
    if(const Json* value=property_value(w,n,"RootPart");value&&value->is_object()&&value->contains("$ref")&&(*value)["$ref"].is_string())
        root=(*value)["$ref"].get<std::string>();
    if(root.empty()&&!owner.empty())for(const auto& id:w.order) {
        Node& part=w.nodes.at(id);
        if(!part.destroyed&&parent_id(w,part)==owner&&instance_name(w,part)=="HumanoidRootPart"&&class_is_a(w,part.class_name,"BasePart")) {root=id;break;}
    }
    std::unordered_set<std::string> protected_parents;
    if(!owner.empty())protected_parents.insert(owner);
    if(!root.empty())protected_parents.insert(root);
    for(auto* ancestor:ancestors(w,owner))
        if(ancestor->class_name!="Workspace")protected_parents.insert(ancestor->id);
    for(const auto& id:w.order) {
        Node& field=w.nodes.at(id);
        if(!field.destroyed&&field.class_name=="ForceField"&&protected_parents.count(parent_id(w,field)))return true;
    }
    return false;
}
int humanoid_take_damage(lua_State* L) {
    auto* handle=checked_handle(L,1);World& w=*handle->world;Node& n=*handle->node;
    try {
        if(n.class_name!="Humanoid"||n.destroyed)throw std::runtime_error("TakeDamage requires a live Humanoid receiver");
        if(lua_gettop(L)!=2||lua_type(L,2)!=LUA_TNUMBER||!std::isfinite(lua_tonumber(L,2)))
            throw std::runtime_error("TakeDamage expects one finite number");
        if(humanoid_forcefield(w,n))return 0;
        double health=humanoid_health_number(w,n,"Health"),amount=lua_tonumber(L,2);
        if(health<=0||w.locally_dead_humanoids.count(n.id))return 0;
        // Clamp before subtraction so finite extreme inputs cannot overflow.
        double next;
        if(amount>=0)next=std::max(0.0,health-amount);
        else {
            double maximum=std::max(0.0,humanoid_health_number(w,n,"MaxHealth"));
            double room=maximum-std::max(0.0,health);
            next=-amount>=room?maximum:health-amount;
        }
        humanoid_set_health(w,n,"Health",next);
    }catch(const std::exception& e){luaL_error(L,"TakeDamage failed: %s",e.what());}
    return 0;
}
void humanoid_received_health_change(World& w,Node& n,const std::string& key,const Json& old,const Json& value) {
    if(n.class_name!="Humanoid"||(key!="Health"&&key!="MaxHealth"))return;
    const Json* current=property_value(w,n,"Health");
    if(!current||!current->is_number()||!std::isfinite(current->get<double>()))return;
    const double health=current->get<double>();
    double before=health;
    if(key=="Health"&&old.is_number()&&std::isfinite(old.get<double>()))before=old.get<double>();
    // Received state is authoritative: notify, but do not clamp or emit an
    // authored mutation back into the receipt stream.
    humanoid_health_events(w,n,before,health);
}
