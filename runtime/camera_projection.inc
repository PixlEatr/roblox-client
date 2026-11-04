// Host viewport geometry and ordinary perspective camera math. Included after
// CFrame helpers and property_events. No rendering, occlusion or VR transforms.
Json validated_viewport(const Json& supplied) {
    if(!supplied.is_object()||!supplied.contains("size"))throw std::runtime_error("viewport requires a size");
    Json result{{"size",Json::array({1,1})},{"device_safe_insets",Json::array({0,0,0,0})},{"gui_inset",Json::array({0,0})}};
    for(auto it=supplied.begin();it!=supplied.end();++it) {
        if(!result.contains(it.key()))throw std::runtime_error("unknown viewport field");
        const size_t count=it.key()=="device_safe_insets"?4:2;
        if(!it.value().is_array()||it.value().size()!=count)throw std::runtime_error("invalid viewport field shape");
        for(const auto& v:it.value())if(!v.is_number()||!std::isfinite(v.get<double>())||v.get<double>()<0||v.get<double>()>1e7)
            throw std::runtime_error("invalid viewport measurement");
        result[it.key()]=it.value();
    }
    const auto& size=result["size"];const auto& safe=result["device_safe_insets"];const auto& gui=result["gui_inset"];
    for(int i=0;i<2;++i)if(size[i].get<double>()<=safe[i].get<double>()+safe[i+2].get<double>()+gui[i].get<double>())
        throw std::runtime_error("viewport insets consume the viewport");
    return result;
}
const Json& camera_viewport(const World& w,const Node& n) {
    const auto it=w.camera_viewports.find(n.id);
    static const Json initial=validated_viewport(Json{{"size",Json::array({1,1})}});
    return it==w.camera_viewports.end()?initial:it->second;
}
Json camera_viewport_size(const Json& viewport) {
    const auto& size=viewport["size"];const auto& safe=viewport["device_safe_insets"];
    return Json::array({size[0].get<double>()-safe[0].get<double>()-safe[2].get<double>(),size[1].get<double>()-safe[1].get<double>()-safe[3].get<double>()});
}
void sync_camera_viewport(World& w,Node& n) {
    if(w.viewport.empty())return;
    Json before=camera_viewport_size(camera_viewport(w,n));
    w.camera_viewports[n.id]=w.viewport;
    if(before!=camera_viewport_size(w.viewport))property_events(w,n,"ViewportSize");
}
void update_viewport(World& w,const Json& supplied) {
    auto validated=validated_viewport(supplied); // Validate before changing state/events.
    w.viewport=std::move(validated);
    auto workspace=w.services.find("Workspace");if(workspace==w.services.end())return;
    auto* ws=find_node(w,workspace->second);if(!ws)return;
    const auto* current=property_value(w,*ws,"CurrentCamera");
    if(current&&current->is_object()&&current->contains("$ref")&&(*current)["$ref"].is_string())
        if(auto* camera=find_node(w,(*current)["$ref"].get<std::string>());camera&&!camera->destroyed&&camera->class_name=="Camera")sync_camera_viewport(w,*camera);
}
Handle* checked_camera(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="Camera"||h->node->destroyed)luaL_error(L,"camera method requires a live Camera");
    return h;
}
CFrameValue camera_frame(lua_State* L,Handle& h) {
    const auto* value=property_value(*h.world,*h.node,"CFrame");
    if(!value)throw std::runtime_error("camera CFrame is unavailable");
    push_json(L,*h.world,*value);
    auto* frame=checked_cframe(L,-1);CFrameValue copy=*frame;lua_pop(L,1);
    for(double n:copy.p)if(!std::isfinite(n))throw std::runtime_error("camera position must be finite");
    for(double n:copy.r)if(!std::isfinite(n))throw std::runtime_error("camera rotation must be finite");
    return copy;
}
double camera_focal_length(Handle& h,const Json& viewport) {
    const auto* value=property_value(*h.world,*h.node,"FieldOfView");
    if(!value||!value->is_number())throw std::runtime_error("camera FieldOfView is unavailable");
    double angle=value->get<double>();
    if(!std::isfinite(angle)||angle<1||angle>120)throw std::runtime_error("invalid camera FieldOfView");
    return viewport["size"][1].get<double>()/(2*std::tan(angle*std::acos(-1.0)/360));
}
double camera_near_plane_z(Handle& h) {
    const auto* value=property_value(*h.world,*h.node,"NearPlaneZ");
    if(!value||!value->is_number())throw std::runtime_error("camera NearPlaneZ is unavailable");
    double z=value->get<double>();
    if(!std::isfinite(z))throw std::runtime_error("invalid camera NearPlaneZ");
    return z;
}
int camera_project(lua_State* L) {
    auto* h=checked_camera(L);auto point=checked_frame_vector(L,2);
    bool screen=lua_toboolean(L,lua_upvalueindex(1));
    try {
        const auto& vp=camera_viewport(*h->world,*h->node);auto cf=camera_frame(L,*h);
        FrameVector offset;for(int i=0;i<3;++i){if(!std::isfinite(point[i]))throw std::runtime_error("world point must be finite");offset[i]=point[i]-cf.p[i];}
        FrameMatrix inverse={cf.r[0],cf.r[3],cf.r[6],cf.r[1],cf.r[4],cf.r[7],cf.r[2],cf.r[5],cf.r[8]};
        auto p=frame_rotate(inverse,offset);double depth=-p[2],focal=camera_focal_length(*h,vp);
        double x=vp["size"][0].get<double>()/2+focal*p[0]/depth-vp["device_safe_insets"][0].get<double>();
        double y=vp["size"][1].get<double>()/2-focal*p[1]/depth-vp["device_safe_insets"][1].get<double>();
        const auto safe=camera_viewport_size(vp);
        bool visible=depth>0&&x>=0&&y>=0&&x<=safe[0].get<double>()&&y<=safe[1].get<double>();
        if(screen){x-=vp["gui_inset"][0].get<double>();y-=vp["gui_inset"][1].get<double>();}
        lua_pushvector(L,float(x),float(y),float(depth));lua_pushboolean(L,visible);return 2;
    }catch(const std::exception& e){luaL_error(L,"camera projection: %s",e.what());return 0;}
}
// Perspective direction from full-surface pixels. Mouse and Camera use the
// same lens and rotation; their ray origins follow different public contracts.
FrameVector camera_pixel_direction(Handle& h,const Json& vp,const CFrameValue& cf,double x,double y) {
    if(!std::isfinite(x)||!std::isfinite(y))throw std::runtime_error("ray coordinates must be finite");
    double focal=camera_focal_length(h,vp);
    FrameVector local={(x-vp["size"][0].get<double>()/2)/focal,(vp["size"][1].get<double>()/2-y)/focal,-1};
    auto direction=frame_rotate(cf.r,local);double length=std::hypot(direction[0],direction[1],direction[2]);
    if(!std::isfinite(length)||length==0)throw std::runtime_error("camera transform has no finite ray direction");
    for(double& component:direction)component/=length;
    return direction;
}
int camera_point_to_ray(lua_State* L) {
    auto* h=checked_camera(L);double x=luaL_checknumber(L,2),y=luaL_checknumber(L,3),depth=luaL_optnumber(L,4,0);
    bool screen=lua_toboolean(L,lua_upvalueindex(1));
    try {
        if(!std::isfinite(x)||!std::isfinite(y)||!std::isfinite(depth))throw std::runtime_error("ray coordinates must be finite");
        const auto& vp=camera_viewport(*h->world,*h->node);auto cf=camera_frame(L,*h);
        x+=vp["device_safe_insets"][0].get<double>();y+=vp["device_safe_insets"][1].get<double>();
        if(screen){x+=vp["gui_inset"][0].get<double>();y+=vp["gui_inset"][1].get<double>();}
        auto direction=camera_pixel_direction(*h,vp,cf,x,y);
        double forward=-(direction[0]*cf.r[2]+direction[1]*cf.r[5]+direction[2]*cf.r[8]);
        double origin_depth=depth-camera_near_plane_z(*h)/forward;
        if(!std::isfinite(forward)||forward<=0||!std::isfinite(origin_depth))throw std::runtime_error("camera transform has no finite near-plane intersection");
        Json origin=Json::array(),unit=Json::array();
        for(int i=0;i<3;++i){origin.push_back(cf.p[i]+origin_depth*direction[i]);unit.push_back(direction[i]);}
        push_authored_value(L,Json{{"$ray",Json::array({Json{{"$vector3",origin}},Json{{"$vector3",unit}}})}});return 1;
    }catch(const std::exception& e){luaL_error(L,"camera ray: %s",e.what());return 0;}
}
bool camera_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="Camera")return false;
    if(key=="ViewportSize") {
        push_authored_value(L,Json{{"$vector2",camera_viewport_size(camera_viewport(w,n))}});return true;
    }
    bool project=key=="WorldToViewportPoint"||key=="WorldToScreenPoint";
    bool ray=key=="ViewportPointToRay"||key=="ScreenPointToRay";
    if(!project&&!ray)return false;
    lua_pushboolean(L,key=="WorldToScreenPoint"||key=="ScreenPointToRay");
    lua_pushcclosure(L,project?camera_project:camera_point_to_ray,key.c_str(),1);return true;
}
