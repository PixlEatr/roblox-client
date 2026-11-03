int linear_velocity_enum_kind(const std::string& key) {
    if(key=="RelativeTo")return kActuatorRelativeTo;
    if(key=="VelocityConstraintMode")return kVelocityConstraintMode;
    if(key=="ForceLimitMode")return kForceLimitMode;
    return -1;
}
bool linear_velocity_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="LinearVelocity")return false;
    int kind=linear_velocity_enum_kind(key);if(kind<0)return false;
    const Json* value=property_value(w,n,key);
    if(value&&value->is_object()&&value->contains("$enum")) {
        const auto& item=(*value)["$enum"];
        if(!item.is_object()||item.value("type","")!=kGeometryEnums[kind].name||!item.contains("value"))
            throw std::runtime_error("incorrect LinearVelocity enum type");
        value=&item.at("value");
    }
    if(!value||!value->is_number_integer()||value->get<int>()<0||value->get<int>()>=kGeometryEnums[kind].count)
        throw std::runtime_error("LinearVelocity enum is unavailable");
    push_geometry_item(L,kind,value->get<int>());return true;
}
