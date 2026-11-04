// Pair exclusions are distinct from rigid joints and from group-mask queries.
std::string no_collision_reference(World& w,Node& constraint,const char* key) {
    const auto* value=property_value(w,constraint,key);
    if(!value)throw std::runtime_error(std::string("NoCollisionConstraint.")+key+" is unavailable");
    if(value->is_null())return {};
    if(!value->is_object()||!value->contains("$ref")||!value->at("$ref").is_string())
        throw std::runtime_error("invalid NoCollisionConstraint endpoint");
    return value->at("$ref").get<std::string>();
}
int get_no_collision_constraints(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;auto& part=*h->node;
    if(!pivot_part(w,part)){luaL_error(L,"GetNoCollisionConstraints requires a BasePart");return 0;}
    try {
        std::vector<Node*> matches;
        Node* root=part.destroyed?nullptr:part_collision_world(w,part);
        if(root)for(const auto& id:w.order) {
            auto* constraint=find_node(w,id);
            if(!constraint||constraint->destroyed||constraint->class_name!="NoCollisionConstraint")continue;
            auto* enabled=property_value(w,*constraint,"Enabled");
            if(enabled&&enabled->is_boolean()&&!enabled->get<bool>())continue;
            // A malformed unrelated constraint must not break this part's query.
            bool incident=false;
            for(const char* key:{"Part0","Part1"})if(const auto* value=property_value(w,*constraint,key))
                if(value->is_object()&&value->contains("$ref")&&value->at("$ref")==part.id)incident=true;
            if(!incident||part_collision_world(w,*constraint)!=root)continue;
            if(!enabled||!enabled->is_boolean())throw std::runtime_error("NoCollisionConstraint.Enabled is unavailable");
            const auto a=no_collision_reference(w,*constraint,"Part0"),b=no_collision_reference(w,*constraint,"Part1");
            if(a.empty()||b.empty()||a==b)continue;
            auto* first=find_node(w,a);auto* second=find_node(w,b);
            if(!first||!second)throw std::runtime_error("NoCollisionConstraint endpoint is unresolved");
            if(first->destroyed||second->destroyed)continue;
            if(!pivot_part(w,*first)||!pivot_part(w,*second))throw std::runtime_error("NoCollisionConstraint endpoint must be a BasePart");
            if(part_collision_world(w,*first)==root&&part_collision_world(w,*second)==root)matches.push_back(constraint);
        }
        lua_createtable(L,int(matches.size()),0);int i=1;
        for(auto* constraint:matches){push_node(L,w,constraint);lua_rawseti(L,-2,i++);}return 1;
    }catch(const std::exception& e){luaL_error(L,"GetNoCollisionConstraints: %s",e.what());return 0;}
}
void validate_no_collision_assignment(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name!="NoCollisionConstraint"||(key!="Part0"&&key!="Part1")||lua_isnil(L,3))return;
    auto* part=checked_handle(L,3);
    if(part->world!=&w||!pivot_part(w,*part->node))throw std::runtime_error("NoCollisionConstraint endpoint must be a BasePart");
}
