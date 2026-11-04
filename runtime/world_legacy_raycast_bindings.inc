// Ray-based APIs used before WorldRoot:Raycast. Geometry and material lookup
// are shared; argument positions and tuple returns retain the legacy contract.
bool world_legacy_ray_method(const std::string& name) {
    return name=="FindPartOnRay"||name=="findPartOnRay"||
        name=="FindPartOnRayWithIgnoreList"||name=="FindPartOnRayWithWhitelist";
}
bool legacy_ray_flag(lua_State* L,int index) {
    if(lua_isnoneornil(L,index))return false;
    if(lua_type(L,index)!=LUA_TBOOLEAN)throw std::runtime_error("legacy ray flag requires a boolean");
    return lua_toboolean(L,index);
}
int world_root_legacy_ray(lua_State* L) {
    const auto* h=checked_handle(L,1);const std::string method=lua_tostring(L,lua_upvalueindex(1));
    try {
        if(h->node->destroyed||!collision_world_root(*h->node))throw std::runtime_error("ray query requires a live WorldRoot");
        const bool include=method=="FindPartOnRayWithWhitelist";
        const bool list=include||method=="FindPartOnRayWithIgnoreList";
        if(lua_gettop(L)<(list?3:2)||lua_gettop(L)>(include?4:5))
            throw std::runtime_error("legacy ray query received the wrong number of arguments");
        FrameVector origin{},direction{};
        if(!read_authored_ray(L,2,origin,direction))throw std::runtime_error("legacy ray query requires Ray");
        for(int i=0;i<3;++i)if(!std::isfinite(origin[i])||!std::isfinite(direction[i]))
            throw std::runtime_error("legacy ray query requires finite Ray components");
        const double length=std::hypot(direction[0],direction[1],direction[2]);
        if(length>15000)throw std::runtime_error("ray direction exceeds 15000 studs");
        auto& w=*h->world;WorldRayFilter filter;
        if(list) {
            auto entries=legacy_query_filter_list(L,w,3);
            if(include){filter.include_active=true;filter.include=std::move(entries);}
            else filter.exclude=std::move(entries);
        } else if(!lua_isnoneornil(L,3)) {
            if(!has_meta(L,3,kInstanceMeta))throw std::runtime_error("ignoreDescendantsInstance requires an Instance or nil");
            const auto* instance=static_cast<Handle*>(lua_touserdata(L,3));
            if(instance->world!=&w)throw std::runtime_error("ignored Instance belongs to another world");
            filter.exclude.push_back(instance->node->id);
        }
        if(include)filter.ignore_water=legacy_ray_flag(L,4);
        else {
            filter.terrain_cells_cubes=legacy_ray_flag(L,4);
            filter.ignore_water=legacy_ray_flag(L,5);
        }
        WorldRayHit hit;
        if(length>0) {
            for(double& component:direction)component/=length;
            hit=query_world_ray(w,*h->node,origin,direction,length,filter);
        }
        const auto position=world_ray_position(origin,direction,hit.node?hit.distance:length);
        const int material=hit.node?world_ray_hit_material(w,hit):1792; // Material.Air on a miss.
        if(hit.node)push_node(L,w,hit.node);else lua_pushnil(L);
        lua_pushvector(L,position[0],position[1],position[2]);
        lua_pushvector(L,hit.normal[0],hit.normal[1],hit.normal[2]);
        push_ussi_export_enum_json(L,Json{{"$enum",{{"type","Material"},{"value",material}}}});
        return 4;
    }catch(const std::exception& e){return luaL_error(L,"%s: %s",method.c_str(),e.what()),0;}
}
