// A shared pose for BasePart transform properties. Computed values do not make
// unknown received rotations into identity rotations. No joint solver here.
constexpr const char* kPartTransformNames[]={"CFrame","Position","Orientation","Rotation"};
bool part_transform_key(const std::string& key) {
    for(const char* name:kPartTransformNames)if(key==name)return true;return false;
}
FrameVector part_vector(const Json& value) {
    if(!value.is_object()||value.size()!=1||!value.contains("$vector3")||!value["$vector3"].is_array()||value["$vector3"].size()!=3)
        throw std::runtime_error("part transform requires Vector3");
    FrameVector out;for(int i=0;i<3;++i){out[i]=json_number(value["$vector3"][i]);if(!std::isfinite(out[i]))throw std::runtime_error("part transform requires finite coordinates");}
    return out;
}
Json part_fields_from_frame(const Json& value) {
    if(!value.is_object()||!value.contains("$cframe")||!value["$cframe"].is_object())throw std::runtime_error("invalid part CFrame");
    const auto& body=value["$cframe"];
    if(!body.contains("position"))throw std::runtime_error("part position is unavailable");
    Json position{{"$vector3",body["position"]}};part_vector(position);
    Json result{{"CFrame",value},{"Position",position}};
    if(body.contains("rotation")) {
        auto cf=pivot_decode(value);
        for(bool yxz:{false,true}) {
            auto a=frame_euler_angles(cf.r,yxz);for(double& n:a)n*=180/std::acos(-1.0);
            result[yxz?"Orientation":"Rotation"]=Json{{"$vector3",a}};
        }
    }
    return result;
}
Json part_transform_state(World& w,const Node& n) {
    if(const auto* cf=property_value(w,n,"CFrame"))return part_fields_from_frame(*cf);
    Json state=Json::object();
    for(const char* name:kPartTransformNames)if(const auto* value=property_value(w,n,name))state[name]=*value;
    return state;
}
// A complete replacement can repair a malformed received pose. Only the old
// notification state is tolerant; preparation of the new pose stays strict.
Json previous_part_transform(World& w,const Node& n) {
    try{return part_transform_state(w,n);}catch(const std::exception&) {
        Json state=Json::object();
        for(const char* key:kPartTransformNames)if(const auto* value=property_value(w,n,key))state[key]=*value;
        return state;
    }
}
Json prepare_part_transform(World& w,const Node& n,const Json& inputs) {
    if(inputs.contains("ReplicationPV"))return part_fields_from_frame(inputs["ReplicationPV"]);
    if(inputs.contains("CFrame"))return part_fields_from_frame(inputs["CFrame"]);
    Json state=part_transform_state(w,n);
    if(inputs.contains("Position")) {
        part_vector(inputs["Position"]);state["Position"]=inputs["Position"];
        if(state.contains("CFrame"))state["CFrame"]["$cframe"]["position"]=inputs["Position"]["$vector3"];
    }
    if(inputs.contains("Orientation")||inputs.contains("Rotation")) {
        bool yxz=inputs.contains("Orientation");auto angles=part_vector(inputs[yxz?"Orientation":"Rotation"]);
        if(!state.contains("Position"))throw std::runtime_error("part position is unavailable; set Position or CFrame first");
        CFrameValue cf;cf.p=part_vector(state["Position"]);
        for(double& angle:angles)angle*=std::acos(-1.0)/180;
        double cx=std::cos(angles[0]),sx=std::sin(angles[0]),cy=std::cos(angles[1]),sy=std::sin(angles[1]),cz=std::cos(angles[2]),sz=std::sin(angles[2]);
        FrameMatrix rx={1,0,0,0,cx,-sx,0,sx,cx},ry={cy,0,sy,0,1,0,-sy,0,cy},rz={cz,-sz,0,sz,cz,0,0,0,1};
        cf.r=frame_product(yxz?frame_product(ry,rx):frame_product(rx,ry),rz);
        state["CFrame"]=pivot_encode(cf);
    }
    if(state.contains("CFrame"))return part_fields_from_frame(state["CFrame"]);
    return state;
}
std::string part_transform_origin(World& w,const Node& n,const std::string& key) {
    if(!pivot_part(w,n)||key=="CFrame"||!part_transform_key(key)||!property_value(w,n,"CFrame"))return {};
    try {
        auto values=part_transform_state(w,n);
        if(!values.contains(key))return "unavailable";
        if(const auto* stored=property_value(w,n,key);stored&&*stored==values[key])return {};
        auto base=origin(w,n,"CFrame");return base=="received"||base=="derived_received"?"derived_received":base;
    }catch(const std::exception&){return "unavailable";}
}
bool part_transform_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!pivot_part(w,n)||key=="CFrame"||!part_transform_key(key)||!property_value(w,n,"CFrame"))return false;
    auto values=part_transform_state(w,n);
    if(!values.contains(key))throw std::runtime_error("part rotation metadata is unavailable");
    push_json(L,w,values[key]);return true;
}
void part_transform_events(World& w,Node& n,const Json& before,const Json& after) {
    for(const char* key:kPartTransformNames) {
        const bool a=before.contains(key),b=after.contains(key);
        if(a!=b||(a&&before[key]!=after[key]))property_events(w,n,key);
    }
}
bool part_transform_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!pivot_part(w,n)||!part_transform_key(key))return false;
    if(key=="CFrame"){if(!has_meta(L,3,kCFrameMeta))throw std::runtime_error("CFrame must be a CFrame");}
    else if(!lua_isvector(L,3))throw std::runtime_error("part transform must be a Vector3");
    auto before=previous_part_transform(w,n);
    auto after=prepare_part_transform(w,n,Json{{key,convert_lua_value(L,w,3)}});
    std::vector<Json> entries;
    for(auto it=after.begin();it!=after.end();++it)entries.push_back(Json{{"kind","set"},{"id",n.id},{"property",it.key()},{"value",it.value()}});
    check_mutation_budget(w,entries);
    for(auto it=after.begin();it!=after.end();++it)w.local_properties[n.id][it.key()]=it.value();
    append_mutations(w,std::move(entries));part_transform_events(w,n,before,after);return true;
}
bool apply_received_part_transform(World& w,Node& n,const Json& fields,uint64_t sequence) {
    if(!pivot_part(w,n))return false;
    Json inputs=Json::object();
    for(const char* key:kPartTransformNames)if(fields.contains(key))inputs[key]=fields[key];
    if(fields.contains("ReplicationPV")&&fields["ReplicationPV"].is_object()&&fields["ReplicationPV"].contains("$cframe"))inputs["ReplicationPV"]=fields["ReplicationPV"];
    if(inputs.empty())return false;
    auto before=previous_part_transform(w,n);auto after=prepare_part_transform(w,n,inputs);
    const auto* old_pv=property_value(w,n,"ReplicationPV");
    const bool pv_changed=inputs.contains("ReplicationPV")&&(!old_pv||*old_pv!=inputs["ReplicationPV"]);
    // Prepare and validate the whole transform before replacing any component.
    for(const char* key:kPartTransformNames)if(after.contains("CFrame")||after.contains(key))clear_local_property(w,n.id,key);
    std::string authoritative=inputs.contains("ReplicationPV")?"ReplicationPV":inputs.contains("CFrame")?"CFrame":inputs.contains("Orientation")?"Orientation":inputs.contains("Rotation")?"Rotation":"Position";
    for(auto it=after.begin();it!=after.end();++it) {
        n.properties[it.key()]=it.value();n.revisions[it.key()]=sequence;
        bool direct=it.key()==authoritative||(it.key()=="Position"&&inputs.contains("Position")&&authoritative!="CFrame"&&authoritative!="ReplicationPV");
        n.origins[it.key()]=direct?"received":"derived_received";
    }
    if(inputs.contains("ReplicationPV")) {
        clear_local_property(w,n.id,"ReplicationPV");n.properties["ReplicationPV"]=inputs["ReplicationPV"];
        n.revisions["ReplicationPV"]=sequence;n.origins["ReplicationPV"]="received";
    }
    part_transform_events(w,n,before,after);
    if(pv_changed)property_events(w,n,"ReplicationPV");
    return true;
}
