// Swept sphere contact against the same snapshot geometry used by picking.
struct SpatialCastContact {
    double distance;
    FrameVector point,normal;
    int material=-1;
};
int push_spatial_cast_contact(lua_State* L,World& w,Node* instance,const SpatialCastContact& hit) {
    const int material=world_ray_hit_material(w,WorldRayHit{instance,hit.distance,hit.normal,{},hit.material});
    const auto point=world_ray_position(hit.point,{},0);
    auto* result=static_cast<RaycastResultValue*>(lua_newuserdatatagged(L,sizeof(RaycastResultValue),0));
    *result={&w,instance,point,hit.normal,hit.distance,material};
    luaL_getmetatable(L,kRaycastResultMeta);lua_setmetatable(L,-2);return 1;
}
struct SphereSweep {
    FrameVector origin,direction;
    double radius;
};
std::optional<std::array<double,2>> sphere_sweep_bounds(const SphereSweep& sweep,
        const FrameVector& minimum,const FrameVector& maximum,double limit) {
    double near=0,far=limit;
    for(int i=0;i<3;++i) {
        const double low=minimum[i]-sweep.radius-sweep.origin[i];
        const double high=maximum[i]+sweep.radius-sweep.origin[i];
        const double d=sweep.direction[i];
        if(d==0){if(low>0||high<0)return std::nullopt;continue;}
        near=std::max(near,(d>0?low:high)/d);
        far=std::min(far,(d>0?high:low)/d);
        if(near>far)return std::nullopt;
    }
    return std::array<double,2>{near,far};
}
template<typename Project>
std::optional<SpatialCastContact> sphere_sweep_convex(const SphereSweep& sweep,
        std::array<double,2> interval,size_t& work,Project project) {
    struct Sample {FrameVector point,offset;double distance2,slope;};
    auto sample=[&](double t) {
        query_work(work);
        const auto center=world_ray_position(sweep.origin,sweep.direction,t);
        const auto closest=project(center);
        const auto offset=query_subtract(center,closest);
        return Sample{closest,offset,query_dot(offset,offset),query_dot(offset,sweep.direction)};
    };
    const double radius2=sweep.radius*sweep.radius;
    double lo=interval[0],hi=interval[1];
    auto start=sample(lo);
    if(lo==0&&start.distance2<=radius2)return std::nullopt;
    auto finish=sample(hi);
    if(start.distance2>radius2&&finish.distance2>radius2) {
        if(start.slope>=0)return std::nullopt;
        if(finish.slope>0) {
            // Squared distance to a convex solid has a monotone derivative
            // along the sweep. Find its minimum before searching for entry.
            double left=lo,right=hi;
            for(int i=0;i<48;++i) {
                const double mid=(left+right)*.5;
                if(sample(mid).slope<0)left=mid;else right=mid;
            }
            hi=(left+right)*.5;
            finish=sample(hi);
        }
        if(finish.distance2>radius2*(1+1e-12))return std::nullopt;
    }
    if(start.distance2<=radius2) {
        hi=lo;finish=start;
    }else if(finish.distance2<=radius2) {
        for(int i=0;i<48;++i) {
            const double mid=(lo+hi)*.5;
            const auto candidate=sample(mid);
            if(candidate.distance2>radius2){lo=mid;start=candidate;}
            else {hi=mid;finish=candidate;}
        }
    }
    // A very small radius can round to a coincident contact sample.
    const auto normal=query_unit(finish.distance2>0?finish.offset:start.offset);
    return SpatialCastContact{hi,finish.point,normal};
}
std::optional<SpatialCastContact> sphere_sweep_mesh(World& w,Node& node,const Json& data,
        const SphereSweep& sweep,double limit,size_t& work,bool ignore_water) {
    const int material=data.contains("material")?query_material(data.at("material")):-1;
    if(ignore_water&&material<0)throw std::runtime_error("terrain material data is unavailable for IgnoreWater");
    if(ignore_water&&material==2048)return std::nullopt;
    const auto points=query_mesh_world_points(w,node,data,work);
    auto minimum=points.front(),maximum=minimum;
    for(const auto& p:points)for(int i=0;i<3;++i){minimum[i]=std::min(minimum[i],p[i]);maximum[i]=std::max(maximum[i],p[i]);}
    const auto interval=sphere_sweep_bounds(sweep,minimum,maximum,limit);
    if(!interval)return std::nullopt;
    if((*interval)[0]==0) {
        const auto delta=query_subtract(sweep.origin,closest_mesh_point(data,points,sweep.origin));
        if(query_dot(delta,delta)<=sweep.radius*sweep.radius)return std::nullopt;
    }
    std::optional<SpatialCastContact> best;
    for(const auto& triangle:data.at("triangles")) {
        const auto ids=query_mesh_indices(triangle,points.size());
        const auto& a=points[ids[0]];const auto& b=points[ids[1]];const auto& c=points[ids[2]];
        for(int i=0;i<3;++i){minimum[i]=std::min({a[i],b[i],c[i]});maximum[i]=std::max({a[i],b[i],c[i]});}
        const auto window=sphere_sweep_bounds(sweep,minimum,maximum,best?best->distance:limit);
        if(!window)continue;
        const auto normal=query_unit(query_cross(query_subtract(b,a),query_subtract(c,a)));
        auto hit=sphere_sweep_convex(sweep,*window,work,[&](const FrameVector& center){
            return closest_triangle_point(center,a,b,c,normal);
        });
        if(hit&&(!best||hit->distance<best->distance)){hit->material=material;best=hit;}
    }
    return best;
}
std::optional<SpatialCastContact> sphere_sweep_voxels(const Json& data,const SphereSweep& sweep,
        double limit,size_t& work,bool ignore_water) {
    const QueryVoxelGrid grid(data);
    query_work(work,grid.count);
    auto half=grid.cell;for(double& v:half)v*=.5;
    const FrameVector minimum{-half[0],-half[1],-half[2]};
    std::optional<SpatialCastContact> best;
    for(size_t index=0;index<grid.count;++index) {
        if(!grid.occupied(index,ignore_water))continue;
        const auto center=grid.center(grid.coordinates(index));
        const SphereSweep local{query_subtract(sweep.origin,center),sweep.direction,sweep.radius};
        const auto interval=sphere_sweep_bounds(local,minimum,half,best?best->distance:limit);
        if(!interval)continue;
        auto project=[&](const FrameVector& p){return closest_primitive_point(p,half,1);};
        if((*interval)[0]==0) {
            const auto offset=query_subtract(local.origin,project(local.origin));
            if(query_dot(offset,offset)<=sweep.radius*sweep.radius)return std::nullopt;
        }
        auto hit=sphere_sweep_convex(local,*interval,work,project);
        if(hit&&(!best||hit->distance<best->distance)) {
            for(int axis=0;axis<3;++axis)hit->point[axis]+=center[axis];
            hit->material=grid.material_at(index);best=hit;
        }
    }
    return best;
}
int world_root_spherecast(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;auto& root=*h->node;
    try {
        if(root.destroyed||!collision_world_root(root))throw std::runtime_error("Spherecast requires a live WorldRoot");
        if(lua_gettop(L)<4||lua_gettop(L)>5)throw std::runtime_error("Spherecast expects position, radius, direction and optional RaycastParams");
        if(lua_type(L,3)!=LUA_TNUMBER)throw std::runtime_error("radius requires a number");
        const double radius=lua_tonumber(L,3);
        if(!std::isfinite(radius)||radius<std::numeric_limits<float>::denorm_min()||radius>256)
            throw std::runtime_error("radius must be greater than zero and at most 256 studs");
        SphereSweep sweep{raycast_input_vector(L,2),raycast_input_vector(L,4),radius};
        const auto length=std::hypot(sweep.direction[0],sweep.direction[1],sweep.direction[2]);
        if(length>1024)throw std::runtime_error("direction exceeds 1024 studs");
        WorldRayFilter defaults;const WorldRayFilter* filter=&defaults;
        if(!lua_isnoneornil(L,5)) {
            const auto* params=checked_raycast_params(L,5);
            if(params->world!=&w)throw std::runtime_error("RaycastParams belongs to another world");
            if(params->margin!=0||params->size_gradient!=0)throw std::runtime_error("Spherecast requires default Margin and SizeGradient");
            filter=&params->filter;
        }
        if(length==0){lua_pushnil(L);return 1;}
        for(double& value:sweep.direction)value/=length;
        CollisionGroupTable groups;
        if(!filter->brute_force)groups=read_collision_groups(w,collision_query_root(w,root));
        size_t work=0;std::optional<SpatialCastContact> best;Node* instance=nullptr;
        double unknown=std::numeric_limits<double>::infinity();
        std::string unknown_class;
        for(const auto& id:w.order) {
            query_work(work);
            auto* node=find_node(w,id);
            if(!node||!spatial_query_candidate(w,*node,root,*filter,groups,work))continue;
            const double limit=best?best->distance:length;
            const auto* data=property_value(w,*node,"CollisionGeometry");
            const int shape=query_closest_primitive_shape(w,*node);
            const auto kind=shape<0&&data&&data->is_object()?data->value("kind",""):std::string();
            const bool ignore_water=filter->ignore_water&&node->class_name=="Terrain";
            std::optional<SpatialCastContact> hit;
            if(kind=="triangle_mesh") {
                hit=sphere_sweep_mesh(w,*node,*data,sweep,limit,work,ignore_water);
            }else if(kind=="voxel_grid"&&!local_value(w,*node,"CFrame")&&!local_value(w,*node,"Size")) {
                hit=sphere_sweep_voxels(*data,sweep,limit,work,ignore_water);
            }else if(node->class_name=="Terrain") {
                unknown=0;unknown_class=node->class_name;
            }else {
                const auto pose=pivot_frame(w,*node,"CFrame");query_rigid(pose);
                auto half=query_size(w,*node);for(double& v:half)v*=.5;
                const auto inverse=query_inverse_rotation(pose.r);
                const SphereSweep local{frame_rotate(inverse,query_subtract(sweep.origin,pose.p)),
                    frame_rotate(inverse,sweep.direction),radius};
                const FrameVector minimum{-half[0],-half[1],-half[2]};
                const auto interval=sphere_sweep_bounds(local,minimum,half,limit);
                if(!interval)continue;
                if(shape<0) {
                    if((*interval)[0]<unknown){unknown=(*interval)[0];unknown_class=node->class_name;}
                    continue;
                }
                hit=sphere_sweep_convex(local,*interval,work,[&](const FrameVector& center){
                    return closest_primitive_point(center,half,shape);
                });
                if(hit) {
                    hit->normal=frame_rotate(pose.r,hit->normal);
                    hit->point=frame_rotate(pose.r,hit->point);
                    for(int i=0;i<3;++i)hit->point[i]+=pose.p[i];
                }
            }
            if(hit&&(!best||hit->distance<best->distance)){best=hit;instance=node;}
        }
        if(unknown<=(best?best->distance:length))throw std::runtime_error("collision geometry is unavailable for "+unknown_class);
        if(!best){lua_pushnil(L);return 1;}
        return push_spatial_cast_contact(L,w,instance,*best);
    }catch(const std::exception& e){return luaL_error(L,"Spherecast: %s",e.what()),0;}
}
