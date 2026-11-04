// Included after the ray geometry and motion receiver helpers.
FrameVector closest_round_point(const FrameVector& point,const FrameVector& half,bool cylinder) {
    const int first=cylinder?1:0;
    FrameVector result=point;
    if(cylinder)result[0]=std::clamp(point[0],-half[0],half[0]);
    // For an exterior point, solve the normal projection onto the ellipse
    // or ellipsoid. Scaling a ray from the center is only correct for a sphere.
    auto norm=[&](double lambda) {
        double sum=0;
        for(int i=first;i<3;++i) {
            const double ratio=point[i]*half[i]/(half[i]*half[i]+lambda);
            sum+=ratio*ratio;
        }
        return sum;
    };
    if(norm(0)<=1)return result;
    double low=0,high=0;
    for(int i=first;i<3;++i)high=std::hypot(high,point[i]*half[i]);
    for(int iteration=0;iteration<512;++iteration) {
        const double mid=low+(high-low)*.5;
        if(mid==low||mid==high)break;
        if(norm(mid)>1)low=mid;else high=mid;
    }
    for(int i=first;i<3;++i) {
        const double square=half[i]*half[i];
        result[i]=point[i]*(square/(square+high));
    }
    return result;
}

FrameVector closest_wedge_point(const FrameVector& point,const FrameVector& half,int shape) {
    auto planes=query_primitive_planes(half,shape);
    for(auto& [normal,extent]:planes) {
        const double length=std::hypot(normal[0],normal[1],normal[2]);
        for(auto& v:normal)v/=length;
        extent/=length;
    }
    auto inside=[&](const FrameVector& candidate) {
        for(const auto& [n,d]:planes) {
            const double tolerance=64*std::numeric_limits<double>::epsilon()*
                (std::abs(d)+std::abs(n[0]*candidate[0])+std::abs(n[1]*candidate[1])+std::abs(n[2]*candidate[2]));
            if(query_dot(n,candidate)-d>tolerance)return false;
        }
        return true;
    };
    if(inside(point))return point;
    double best_distance=std::numeric_limits<double>::infinity();
    FrameVector best{};
    auto admit=[&](const FrameVector& candidate) {
        const auto offset=query_subtract(candidate,point);
        const double distance=query_dot(offset,offset);
        if(distance<best_distance&&inside(candidate)){best=candidate;best_distance=distance;}
    };
    // An exterior projection of a convex polyhedron lies on a face, edge,
    // or vertex. Enumerate the corresponding planes and keep feasible points.
    for(size_t i=0;i<planes.size();++i) {
        const auto& [a,da]=planes[i];
        const double gap=query_dot(a,point)-da;
        FrameVector face;
        for(int axis=0;axis<3;++axis)face[axis]=point[axis]-gap*a[axis];
        admit(face);
        for(size_t j=i+1;j<planes.size();++j) {
            const auto& [b,db]=planes[j];
            const auto line=query_cross(a,b);
            const double length2=query_dot(line,line);
            if(length2==0)continue;
            const auto u=query_cross(b,line),v=query_cross(line,a);
            FrameVector origin,edge;
            for(int axis=0;axis<3;++axis)origin[axis]=(da*u[axis]+db*v[axis])/length2;
            const double along=query_dot(query_subtract(point,origin),line)/length2;
            for(int axis=0;axis<3;++axis)edge[axis]=origin[axis]+along*line[axis];
            admit(edge);
            for(size_t k=j+1;k<planes.size();++k) {
                const auto& [c,dc]=planes[k];
                const double denominator=query_dot(c,line);
                if(denominator==0)continue;
                const double t=(dc-query_dot(c,origin))/denominator;
                FrameVector vertex;
                for(int axis=0;axis<3;++axis)vertex[axis]=origin[axis]+t*line[axis];
                admit(vertex);
            }
        }
    }
    if(!std::isfinite(best_distance))throw std::runtime_error("could not project onto wedge geometry");
    return best;
}

std::vector<FrameVector> query_mesh_world_points(World& w,Node& part,const Json& data,size_t& work) {
    auto points=query_mesh_points(data,work);
    if(local_value(w,part,"CFrame")||local_value(w,part,"Size")) {
        const auto [before,after,scale]=query_geometry_pose(w,part);
        const auto inverse=query_inverse_rotation(before.r);
        for(auto& vertex:points) {
            auto local=frame_rotate(inverse,query_subtract(vertex,before.p));
            for(int axis=0;axis<3;++axis)local[axis]*=scale[axis];
            vertex=frame_rotate(after.r,local);
            for(int axis=0;axis<3;++axis) {
                vertex[axis]+=after.p[axis];
                if(!std::isfinite(vertex[axis])||std::abs(vertex[axis])>std::numeric_limits<float>::max())
                    throw std::runtime_error("decoded mesh vertex exceeds finite Vector3 range");
            }
        }
    }
    return points;
}

FrameVector closest_triangle_point(const FrameVector& point,const FrameVector& a,
        const FrameVector& b,const FrameVector& c,const FrameVector& normal) {
    const double gap=query_dot(query_subtract(point,a),normal);
    FrameVector projected;
    for(int axis=0;axis<3;++axis)projected[axis]=point[axis]-gap*normal[axis];
    bool on_face=true;
    FrameVector closest{};
    double best=std::numeric_limits<double>::infinity();
    auto admit=[&](const FrameVector& candidate) {
        const auto delta=query_subtract(candidate,point);
        const double distance=query_dot(delta,delta);
        if(distance<best){best=distance;closest=candidate;}
    };
    const std::array<FrameVector,3> vertices{a,b,c};
    for(int i=0;i<3;++i) {
        const auto& start=vertices[i];
        const auto edge=query_subtract(vertices[(i+1)%3],start);
        if(query_dot(query_cross(edge,query_subtract(projected,start)),normal)<0)on_face=false;
        const double t=std::clamp(query_dot(query_subtract(point,start),edge)/query_dot(edge,edge),0.0,1.0);
        FrameVector candidate;
        for(int axis=0;axis<3;++axis)candidate[axis]=start[axis]+t*edge[axis];
        admit(candidate);
    }
    return on_face?projected:closest;
}

FrameVector closest_mesh_point(const Json& data,const std::vector<FrameVector>& points,const FrameVector& point) {
    FrameVector closest{};
    double best=std::numeric_limits<double>::infinity(),solid_angle=0;
    std::unordered_map<size_t,int> directed_edges;
    for(const auto& triangle:data.at("triangles")) {
        const auto ids=query_mesh_indices(triangle,points.size());
        const auto& a=points[ids[0]];const auto& b=points[ids[1]];const auto& c=points[ids[2]];
        const auto normal=query_unit(query_cross(query_subtract(b,a),query_subtract(c,a)));
        const auto candidate=closest_triangle_point(point,a,b,c,normal);
        const auto delta=query_subtract(candidate,point);
        const double distance=query_dot(delta,delta);
        if(distance<best){best=distance;closest=candidate;}
        for(int i=0;i<3;++i) {
            const auto j=ids[i],k=ids[(i+1)%3];
            directed_edges[std::min(j,k)*points.size()+std::max(j,k)]+=j<k?1:-1;
        }
        if(best!=0) {
            const auto u=query_unit(query_subtract(a,point)),v=query_unit(query_subtract(b,point)),
                z=query_unit(query_subtract(c,point));
            solid_angle+=2*std::atan2(query_dot(u,query_cross(v,z)),1+query_dot(u,v)+query_dot(v,z)+query_dot(z,u));
        }
    }
    // A closed, consistently oriented mesh has a nonzero winding number
    // inside. Open collision surfaces still have a nearest point.
    if(best==0||(std::abs(solid_angle)>2*std::acos(-1.0)&&
            std::all_of(directed_edges.begin(),directed_edges.end(),[](const auto& edge){return edge.second==0;})))
        return point;
    return closest;
}

int query_closest_primitive_shape(World& w,Node& part) {
    const int shape=query_part_shape(w,part);
    if(shape>=0||!class_is_a(w,part.class_name,"TriangleMeshPart"))return shape;
    const auto* fidelity=property_value(w,part,"CollisionFidelity");
    if(fidelity&&fidelity->is_object()&&fidelity->contains("$enum"))fidelity=&fidelity->at("$enum").at("value");
    return fidelity&&*fidelity==2?1:shape; // Enum.CollisionFidelity.Box
}
FrameVector closest_primitive_point(const FrameVector& point,const FrameVector& half,int shape) {
    if(shape==0||shape==2)return closest_round_point(point,half,shape==2);
    if(shape!=1)return closest_wedge_point(point,half,shape);
    FrameVector result;
    for(int axis=0;axis<3;++axis)result[axis]=std::clamp(point[axis],-half[axis],half[axis]);
    return result;
}

int part_closest_point_on_surface(lua_State* L) {
    auto* h=checked_motion_part(L);auto& w=*h->world;auto& part=*h->node;
    if(!lua_isvector(L,2))return luaL_argerror(L,2,"Vector3 expected"),0;
    const float* raw=lua_tovector(L,2);const FrameVector point{raw[0],raw[1],raw[2]};
    for(double v:point)if(!std::isfinite(v))return luaL_argerror(L,2,"finite Vector3 expected"),0;
    try {
        const int shape=query_closest_primitive_shape(w,part);
        FrameVector result;
        if(shape<0) {
            const auto* data=property_value(w,part,"CollisionGeometry");
            if(!data||!data->is_object()||data->value("kind","")!="triangle_mesh")
                throw std::runtime_error("collision shape is unavailable for "+part.class_name);
            size_t work=0;
            result=closest_mesh_point(*data,query_mesh_world_points(w,part,*data,work),point);
        }else {
            const auto pose=pivot_frame(w,part,"CFrame");query_rigid(pose);
            auto half=query_size(w,part);for(double& v:half)v*=.5;
            const auto local=frame_rotate(query_inverse_rotation(pose.r),query_subtract(point,pose.p));
            const auto closest=closest_primitive_point(local,half,shape);
            if(closest==local){lua_pushvalue(L,2);return 1;}
            result=frame_rotate(pose.r,closest);
            for(int axis=0;axis<3;++axis)result[axis]+=pose.p[axis];
        }
        for(double v:result)
            if(!std::isfinite(v)||std::abs(v)>std::numeric_limits<float>::max())
                throw std::runtime_error("closest point exceeds finite Vector3 range");
        lua_pushvector(L,result[0],result[1],result[2]);return 1;
    }catch(const std::exception& e){return luaL_error(L,"GetClosestPointOnSurface: %s",e.what()),0;}
}
