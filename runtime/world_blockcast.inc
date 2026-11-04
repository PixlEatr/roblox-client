// Blockcast uses convex contact against primitives, individual decoded
// triangles, and occupied terrain cells. Bounds only reject candidates.
struct BlockSweep {
    FrameVector origin,direction,half,bounds;
    std::array<FrameVector,3> axes;
};
FrameVector block_support(const BlockSweep& block,const FrameVector& normal) {
    FrameVector point{};
    for(int axis=0;axis<3;++axis)
        point=cast_add_scaled(point,block.axes[axis],
            std::copysign(block.half[axis],query_dot(block.axes[axis],normal)));
    return point;
}
FrameVector blockcast_extent(const std::array<FrameVector,3>& axes,const FrameVector& half) {
    FrameVector extent{};
    for(int i=0;i<3;++i)for(int j=0;j<3;++j)extent[i]+=std::abs(axes[j][i])*half[j];
    return extent;
}
FrameVector primitive_support(const FrameVector& half,int shape,const FrameVector& axis) {
    FrameVector point;
    for(int i=0;i<3;++i)point[i]=std::copysign(half[i],axis[i]);
    if(shape==1)return point;
    if(shape==0||shape==2) {
        const int first=shape==2?1:0;
        double denominator=0;
        for(int i=first;i<3;++i)denominator=std::hypot(denominator,half[i]*axis[i]);
        for(int i=first;i<3;++i)point[i]=denominator>0?half[i]*(half[i]*axis[i]/denominator):0;
        return point;
    }
    point[1]=-half[1];
    auto top=point;top[1]=half[1];
    if(shape==3)top[2]=half[2];
    else {top[0]=half[0];top[2]=-half[2];}
    return query_dot(top,axis)>query_dot(point,axis)?top:point;
}
std::optional<std::array<double,2>> block_sweep_bounds(const BlockSweep& sweep,
        FrameVector minimum,FrameVector maximum,double limit) {
    minimum=query_subtract(minimum,sweep.bounds);
    maximum=cast_add_scaled(maximum,sweep.bounds,1);
    return sphere_sweep_bounds({{},sweep.direction,0},minimum,maximum,limit);
}
template<typename TargetSupport>
std::optional<ConvexContact> sweep_block(const BlockSweep& sweep,double limit,TargetSupport target,size_t& work) {
    return cast_convex(sweep.direction,limit,[&](const FrameVector& axis){return block_support(sweep,axis);},target,work);
}

std::optional<SpatialCastContact> block_sweep_mesh(World& w,Node& node,const Json& data,
        const BlockSweep& sweep,double limit,size_t& work,bool ignore_water) {
    const int material=data.contains("material")?query_material(data.at("material")):-1;
    if(ignore_water&&material<0)throw std::runtime_error("terrain material data is unavailable for IgnoreWater");
    if(ignore_water&&material==2048)return std::nullopt;
    auto points=query_mesh_world_points(w,node,data,work);
    for(auto& point:points)point=query_subtract(point,sweep.origin);
    auto minimum=points.front(),maximum=minimum;
    for(const auto& p:points)for(int i=0;i<3;++i){minimum[i]=std::min(minimum[i],p[i]);maximum[i]=std::max(maximum[i],p[i]);}
    const auto interval=block_sweep_bounds(sweep,minimum,maximum,limit);
    if(!interval)return std::nullopt;
    if((*interval)[0]==0&&closest_mesh_point(data,points,{})==FrameVector{})return SpatialCastContact{0,{},{},material};
    std::optional<SpatialCastContact> best;
    for(const auto& triangle:data.at("triangles")) {
        const auto ids=query_mesh_indices(triangle,points.size());
        const std::array<FrameVector,3> face{points[ids[0]],points[ids[1]],points[ids[2]]};
        for(int i=0;i<3;++i) {
            minimum[i]=std::min({face[0][i],face[1][i],face[2][i]});
            maximum[i]=std::max({face[0][i],face[1][i],face[2][i]});
        }
        const auto window=block_sweep_bounds(sweep,minimum,maximum,best?best->distance:limit);
        if(!window)continue;
        auto hit=sweep_block(sweep,(*window)[1],[&](const FrameVector& axis) {
            return *std::max_element(face.begin(),face.end(),[&](const FrameVector& a,const FrameVector& b){
                return query_dot(a,axis)<query_dot(b,axis);
            });
        },work);
        if(hit&&(!best||hit->distance<best->distance)) {
            best=SpatialCastContact{hit->distance,hit->point,hit->normal,material};
            if(hit->distance==0)return best;
        }
    }
    return best;
}
std::optional<SpatialCastContact> block_sweep_voxels(const Json& data,const BlockSweep& sweep,
        double limit,size_t& work,bool ignore_water) {
    const QueryVoxelGrid grid(data);
    query_work(work,grid.count);
    auto half=grid.cell;for(double& v:half)v*=.5;
    std::optional<SpatialCastContact> best;
    for(size_t i=0;i<grid.count;++i) {
        if(!grid.occupied(i,ignore_water))continue;
        const auto center=query_subtract(grid.center(grid.coordinates(i)),sweep.origin);
        const auto interval=block_sweep_bounds(sweep,query_subtract(center,half),
            cast_add_scaled(center,half,1),best?best->distance:limit);
        if(!interval)continue;
        const auto hit=sweep_block(sweep,(*interval)[1],[&](const FrameVector& axis){
            return cast_add_scaled(center,primitive_support(half,1,axis),1);
        },work);
        if(hit&&(!best||hit->distance<best->distance)) {
            best=SpatialCastContact{hit->distance,hit->point,hit->normal,grid.material_at(i)};
            if(hit->distance==0)return best;
        }
    }
    return best;
}
int world_root_blockcast(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;auto& root=*h->node;
    try {
        if(root.destroyed||!collision_world_root(root))throw std::runtime_error("Blockcast requires a live WorldRoot");
        if(lua_gettop(L)<4||lua_gettop(L)>5)throw std::runtime_error("Blockcast expects CFrame, size, direction and optional RaycastParams");
        const auto& frame=*checked_cframe(L,2);overlap_finite_frame(frame);
        auto half=raycast_input_vector(L,3);
        for(auto& v:half) {
            if(v<=0||v>512)throw std::runtime_error("size components must be greater than zero and at most 512 studs");
            v*=.5;
        }
        auto direction=raycast_input_vector(L,4);
        const double length=cast_length(direction);
        if(length>1024)throw std::runtime_error("direction exceeds 1024 studs");
        WorldRayFilter defaults;const WorldRayFilter* filter=&defaults;
        if(!lua_isnoneornil(L,5)) {
            const auto* params=checked_raycast_params(L,5);
            if(params->world!=&w)throw std::runtime_error("RaycastParams belongs to another world");
            if(params->margin!=0||params->size_gradient!=0)throw std::runtime_error("Blockcast requires default Margin and SizeGradient");
            filter=&params->filter;
        }
        if(length==0){lua_pushnil(L);return 1;}
        for(auto& v:direction)v/=length;
        const auto axes=overlap_axes(frame);
        const BlockSweep sweep{frame.p,direction,half,blockcast_extent(axes,half),axes};
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
                hit=block_sweep_mesh(w,*node,*data,sweep,limit,work,ignore_water);
            }else if(kind=="voxel_grid"&&!local_value(w,*node,"CFrame")&&!local_value(w,*node,"Size")) {
                hit=block_sweep_voxels(*data,sweep,limit,work,ignore_water);
            }else if(node->class_name=="Terrain") {
                unknown=0;unknown_class=node->class_name;
            }else {
                const auto pose=pivot_frame(w,*node,"CFrame");query_rigid(pose);
                auto target_half=query_size(w,*node);for(double& v:target_half)v*=.5;
                const auto extent=blockcast_extent(overlap_axes(pose),target_half);
                const auto center=query_subtract(pose.p,sweep.origin);
                const auto interval=block_sweep_bounds(sweep,query_subtract(center,extent),cast_add_scaled(center,extent,1),limit);
                if(!interval)continue;
                if(shape<0) {
                    if((*interval)[0]<unknown){unknown=(*interval)[0];unknown_class=node->class_name;}
                    continue;
                }
                const auto inverse=query_inverse_rotation(pose.r);
                const auto contact=sweep_block(sweep,(*interval)[1],[&](const FrameVector& axis) {
                    const auto local=primitive_support(target_half,shape,frame_rotate(inverse,axis));
                    return cast_add_scaled(center,frame_rotate(pose.r,local),1);
                },work);
                if(contact)hit=SpatialCastContact{contact->distance,contact->point,contact->normal};
            }
            if(hit&&hit->distance>0&&(!best||hit->distance<best->distance)){best=hit;instance=node;}
        }
        if(unknown<=(best?best->distance:length))throw std::runtime_error("collision geometry is unavailable for "+unknown_class);
        if(!best){lua_pushnil(L);return 1;}
        best->point=cast_add_scaled(best->point,sweep.origin,1);
        return push_spatial_cast_contact(L,w,instance,*best);
    }catch(const std::exception& e){return luaL_error(L,"Blockcast: %s",e.what()),0;}
}
