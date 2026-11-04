// Derived properties for the local player's legacy Mouse. Host cursor pixels
// are relative to the whole display; Mouse.X/Y are relative to the GUI inset.
bool mouse_projection_property(World& w,const Node& n,const std::string& key) {
    return (key=="X"||key=="Y"||key=="ViewSizeX"||key=="ViewSizeY"||key=="UnitRay"||key=="Origin"||key=="Hit"||key=="Target"||key=="TargetSurface")&&
        class_is_a(w,n.class_name,"Mouse");
}
int mouse_pixel(double value) {
    // Browser coordinates may be fractional or outside the viewport. Keep the
    // public integer type without invoking undefined out-of-range conversion.
    return static_cast<int>(std::clamp(std::trunc(value),double(std::numeric_limits<int>::min()),double(std::numeric_limits<int>::max())));
}
bool mouse_projection_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!mouse_projection_property(w,n,key))return false;
    if(n.destroyed||local_player_mouse(w)!=&n)throw std::runtime_error("live local PlayerMouse required");
    if(key=="X"||key=="Y") {
        const int axis=key=="X"?0:1;
        lua_pushinteger(L,mouse_pixel(double(w.input.location[axis])-w.display["core_insets"][axis].get<double>()));
        return true;
    }
    if(key=="ViewSizeX"||key=="ViewSizeY") {
        lua_pushinteger(L,mouse_pixel(w.display["size"][key=="ViewSizeX"?0:1].get<double>()));return true;
    }
    const auto& size=w.display["size"];const auto& margins=w.display["core_insets"];
    for(int axis=0;axis<2;++axis)
        if(size[axis].get<double>()<=margins[axis].get<double>()+margins[axis+2].get<double>())
            throw std::runtime_error("mouse ray requires an attached display");
    auto service=w.services.find("Workspace");
    auto* workspace=service==w.services.end()?nullptr:find_node(w,service->second);
    if(!workspace||workspace->destroyed)throw std::runtime_error("Workspace is unavailable");
    auto* camera=ensure_current_camera(w,*workspace);
    if(!camera)throw std::runtime_error("CurrentCamera is unavailable");
    Handle handle{&w,camera};auto cf=camera_frame(L,handle);
    const auto& viewport=camera_viewport(w,*camera);
    auto direction=camera_pixel_direction(handle,viewport,cf,w.input.location[0],w.input.location[1]);
    if(key=="UnitRay") {
        push_authored_value(L,Json{{"$ray",Json::array({Json{{"$vector3",cf.p}},Json{{"$vector3",direction}}})}});
        return true;
    }
    FrameVector point=cf.p;
    if(key=="Hit"||key=="Target"||key=="TargetSurface") {
        std::string filter;
        if(const auto* value=property_value(w,n,"TargetFilter");value&&!value->is_null()) {
            if(!value->is_object()||!value->contains("$ref")||!value->at("$ref").is_string())throw std::runtime_error("Mouse.TargetFilter has an invalid reference");
            filter=value->at("$ref").get<std::string>();
        }
        auto hit=query_mouse_world(w,*workspace,cf.p,direction,filter);
        if(key=="Target"){if(hit.node)push_node(L,w,hit.node);else lua_pushnil(L);return true;}
        if(key=="TargetSurface") {
            int axis=0;for(int i=1;i<3;++i)if(std::abs(hit.local_normal[i])>std::abs(hit.local_normal[axis]))axis=i;
            push_geometry_item(L,kGeometryNormal,axis+(hit.local_normal[axis]<0?3:0));return true;
        }
        for(int i=0;i<3;++i)point[i]+=direction[i]*hit.distance;
    }
    // Look toward the pointer with a world-up basis, independent of camera roll.
    // At a vertical pole use a finite alternate up axis.
    FrameVector right={-direction[2],0,direction[0]};
    double length=std::hypot(right[0],right[2]);
    if(length<1e-12){right={direction[1],-direction[0],0};length=std::hypot(right[0],right[1]);}
    for(double& component:right)component/=length;
    FrameVector up={right[1]*direction[2]-right[2]*direction[1],
                    right[2]*direction[0]-right[0]*direction[2],
                    right[0]*direction[1]-right[1]*direction[0]};
    push_frame_value(L,point,{right[0],up[0],-direction[0],right[1],up[1],-direction[1],right[2],up[2],-direction[2]});
    return true;
}
