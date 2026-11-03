// Assembly motion queries and received character membership. No ownership changes.
// API contract: create.roblox.com/docs/reference/engine/classes/BasePart
bool motion_in_character(World& w,Node& part,const std::string& character) {
    Node* cursor=&part;std::unordered_set<std::string> seen;
    while(cursor&&seen.insert(cursor->id).second) {
        if(cursor->destroyed||class_is_a(w,cursor->class_name,"WorldRoot"))return false;
        auto parent=parent_id(w,*cursor);
        if(parent==character)return true;
        cursor=find_node(w,parent);
    }
    return false;
}

Node* current_motion_root(World& w,Node& receiver) {
    Node* player=find_node(w,w.local_player);
    if(!player||player->destroyed||receiver.destroyed||!pivot_part(w,receiver))return nullptr;
    const auto* ref=property_value(w,*player,"Character");
    if(!ref||!ref->is_object()||!ref->contains("$ref")||!(*ref)["$ref"].is_string())return nullptr;
    const auto character=(*ref)["$ref"].get<std::string>();
    if(!motion_in_character(w,receiver,character))return nullptr;
    if(parent_id(w,receiver)==character&&instance_name(w,receiver)=="HumanoidRootPart")return &receiver;
    for(const auto& id:w.order) {
        auto* node=find_node(w,id);
        if(node&&!node->destroyed&&instance_name(w,*node)=="HumanoidRootPart"&&
           parent_id(w,*node)==character&&pivot_part(w,*node))return node;
    }
    return nullptr;
}

std::vector<Node*> received_character_motion_parts(World& w,Node& root) {
    std::vector<Node*> result;
    if(current_motion_root(w,root)!=&root)return result;
    const auto character=parent_id(w,root);
    auto anchored=[&](Node& part) {
        const auto* value=property_value(w,part,"Anchored");
        return value&&value->is_boolean()&&value->get<bool>();
    };
    if(anchored(root))return result;
    // A known joint, including a disabled/destroyed one, supersedes an older
    // AssemblyRootPart reference. Flexible constraints are not rigid evidence.
    if(w.motion_topology_root!=root.id) {
        w.motion_topology_root=root.id;w.motion_topology_parts.clear();
    }
    auto& has_topology=w.motion_topology_parts;
    std::vector<std::string> rigid_ids;
    bool authored_topology=false;
    for(const auto& id:w.order) {
        auto* joint=find_node(w,id);if(!joint||!joint_rigid(*joint))continue;
        rigid_ids.push_back(id);
        if(motion_in_character(w,*joint,character))
            for(const char* key:{"Part0","Part1","Enabled","Parent"})
                if(!joint->server_owned||local_value(w,*joint,key))authored_topology=true;
        for(const char* key:{"Part0","Part1"}) {
            const auto* ref=property_value(w,*joint,key);
            if(ref&&ref->is_object()&&ref->contains("$ref")&&(*ref)["$ref"].is_string())
                has_topology.insert((*ref)["$ref"].get<std::string>());
        }
    }
    std::vector<Node*> connected;
    JointGraph graph(w,&rigid_ids);
    std::unordered_set<std::string> visited{root.id};
    std::deque<Node*> pending{&root};
    while(!pending.empty()) {
        auto* next=pending.front();pending.pop_front();
        for(auto index:graph.incident[next->id]) {
            const auto& edge=graph.edges[index];
            // An unresolved branch cannot prove that branch's membership,
            // but it must not erase another branch's known active path.
            if(!edge.rigid||!edge.active||!edge.unknown.empty())continue;
            auto* other=edge.a==next?edge.b:edge.a;
            if(other&&visited.insert(other->id).second){connected.push_back(other);pending.push_back(other);}
        }
    }
    connected.push_back(&root);
    for(auto* member:connected) {
        if(anchored(*member))return {};
        if(!motion_in_character(w,*member,character))continue;
        auto origin=member->origins.find("AssemblyRootPart");
        auto ref=member->properties.find("AssemblyRootPart");
        if(!authored_topology&&origin!=member->origins.end()&&*origin=="received"&&ref!=member->properties.end()&&
           ref->is_object()&&ref->contains("$ref")&&(*ref)["$ref"].is_string()&&
           (*ref)["$ref"].get<std::string>()!=root.id)continue;
        result.push_back(member);
    }
    // Keep the root first for consumers capturing call-time identity.
    auto root_it=std::find(result.begin(),result.end(),&root);
    if(root_it==result.end())return {};
    std::iter_swap(result.begin(),root_it);
    for(const auto& id:w.order) {
        Node* part=find_node(w,id);
        if(!part||part==&root||has_topology.count(id))continue;
        auto origin=part->origins.find("AssemblyRootPart"),ref=part->properties.find("AssemblyRootPart");
        if(origin==part->origins.end()||*origin!="received"||ref==part->properties.end()||
           !ref->is_object()||!ref->contains("$ref")||!(*ref)["$ref"].is_string()||
           (*ref)["$ref"].get<std::string>()!=root.id)continue;
        if(!part->destroyed&&pivot_part(w,*part)&&!anchored(*part)&&motion_in_character(w,*part,character))
            result.push_back(part);
    }
    return result;
}

std::vector<Node*> character_velocity_write_targets(World& w,Node& part) {
    auto* root=current_motion_root(w,part);if(!root)return {};
    auto members=received_character_motion_parts(w,*root);
    if(std::find(members.begin(),members.end(),&part)==members.end())return {};
    return members;
}

std::string character_motion_root_id(World& w,Node& part) {
    auto members=character_velocity_write_targets(w,part);
    return members.empty()?std::string():members.front()->id;
}

Handle* checked_motion_part(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->destroyed||!pivot_part(*h->world,*h->node))
        luaL_error(L,"part motion query requires a live BasePart");
    return h;
}
const Json* explicit_motion_value(World& w,Node& n,const char* key) {
    return explicit_part_value(w,n,key);
}
FrameVector motion_vector(World& w,Node& n,const char* key) {
    const auto* value=explicit_motion_value(w,n,key);
    if(!value&&!n.server_owned)value=property_value(w,n,key);
    if(!value)throw std::runtime_error(std::string(key)+" is unavailable");
    return part_vector(*value);
}
int part_is_grounded(lua_State* L) {
    auto* h=checked_motion_part(L);auto& w=*h->world;auto& n=*h->node;
    try {
        // A physical floor contact does not make an unanchored assembly fixed.
        const auto* own=property_value(w,n,"Anchored");
        if(own&&own->is_boolean()&&own->get<bool>()){lua_pushboolean(L,true);return 1;}
        JointGraph graph(w);auto members=graph.connected(n,true);members.push_back(&n);
        bool unknown=false;
        for(auto* part:members) {
            const auto* anchored=property_value(w,*part,"Anchored");
            if(!anchored||!anchored->is_boolean()){unknown=true;continue;}
            if(anchored->get<bool>()){lua_pushboolean(L,true);return 1;}
        }
        if(unknown)throw std::runtime_error("assembly Anchored state is unavailable");
        lua_pushboolean(L,false);return 1;
    }catch(const std::exception& e){luaL_error(L,"IsGrounded: %s",e.what());return 0;}
}
int part_velocity_at_position(lua_State* L) {
    auto* h=checked_motion_part(L);auto& w=*h->world;auto& n=*h->node;
    if(!lua_isvector(L,2)){luaL_argerror(L,2,"Vector3 expected");return 0;}
    const float* raw=lua_tovector(L,2);FrameVector point{raw[0],raw[1],raw[2]};
    for(double v:point)if(!std::isfinite(v)){luaL_argerror(L,2,"finite Vector3 expected");return 0;}
    try {
        Node* source=&n;
        // Newly authored rigid members share their selected root's motion.
        // Received assembly properties already describe that member's assembly.
        if(!n.server_owned) {
            JointGraph graph(w);
            if(auto* root=assembly_root(w,n,graph))source=root;
        }
        auto linear=motion_vector(w,*source,"AssemblyLinearVelocity");
        auto angular=motion_vector(w,*source,"AssemblyAngularVelocity");
        if(angular[0]!=0||angular[1]!=0||angular[2]!=0) {
            FrameVector center;
            if(part_mass_authored(w,*source,"AssemblyCenterOfMass"))
                center=part_vector(part_mass_value(w,*source,"AssemblyCenterOfMass"));
            else if(const auto* value=explicit_motion_value(w,*source,"AssemblyCenterOfMass"))
                center=part_vector(*value);
            else {
                if(source->server_owned)
                    throw std::runtime_error("assembly center of mass is unavailable");
                center=part_vector(part_mass_value(w,*source,"AssemblyCenterOfMass"));
            }
            FrameVector r{point[0]-center[0],point[1]-center[1],point[2]-center[2]};
            FrameVector spin{angular[1]*r[2]-angular[2]*r[1],
                             angular[2]*r[0]-angular[0]*r[2],
                             angular[0]*r[1]-angular[1]*r[0]};
            for(int i=0;i<3;++i)linear[i]+=spin[i];
        }
        for(double value:linear)if(!std::isfinite(value)||std::abs(value)>std::numeric_limits<float>::max())
            throw std::runtime_error("point velocity exceeds finite Vector3 range");
        lua_pushvector(L,linear[0],linear[1],linear[2]);return 1;
    }catch(const std::exception& e){luaL_error(L,"GetVelocityAtPosition: %s",e.what());return 0;}
}
