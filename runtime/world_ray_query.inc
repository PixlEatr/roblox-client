// Synchronous geometric picking against the current VM world. The decoded
// mesh/voxel payloads use the same world-space format as physics_ground.py.
// No asset decoding, collision response, or network requests occur here.
struct WorldRayHit { Node* node=nullptr;double distance=1000;FrameVector normal{},local_normal{};int material=-1; };
struct ShapeRayHit { bool hit=false;double distance=0;FrameVector normal{};int material=-1; };
struct WorldRayFilter {
    std::vector<std::string> exclude,include,legacy;
    bool include_active=false,legacy_include=false,respect_collide=false,brute_force=false,ignore_water=false;
    bool terrain_cells_cubes=false;
    std::string collision_group="Default";
};
int query_material(const Json& value) {
    const Json* number=&value;
    if(value.is_object()&&value.contains("$enum")) {
        const auto& e=value.at("$enum");
        if(!e.is_object()||e.value("type","")!="Material")throw std::runtime_error("ray query requires Enum.Material");
        number=&e.at("value");
    }
    if(number->is_number_integer())for(const auto& item:kUsiMaterialItems)if(*number==item.value)return item.value;
    throw std::runtime_error("ray query has invalid material");
}
double query_dot(const FrameVector& a,const FrameVector& b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
FrameVector query_cross(const FrameVector& a,const FrameVector& b){return {a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]};}
FrameVector query_subtract(const FrameVector& a,const FrameVector& b){return {a[0]-b[0],a[1]-b[1],a[2]-b[2]};}
FrameVector query_unit(FrameVector v){double length=std::hypot(v[0],v[1],v[2]);if(!std::isfinite(length)||length==0)throw std::runtime_error("ray query has invalid geometry normal");for(double& x:v)x/=length;return v;}
void query_work(size_t& work,size_t amount=1){if(amount>1000000-work)throw std::runtime_error("ray query geometry work limit exceeded");work+=amount;}
FrameVector query_vector(const Json& a) {
    if(!a.is_array()||a.size()!=3)throw std::runtime_error("ray query requires three geometry components");
    FrameVector v;for(int i=0;i<3;++i){if(!a[i].is_number())throw std::runtime_error("ray query geometry must be numeric");v[i]=a[i].get<double>();if(!std::isfinite(v[i])||std::abs(v[i])>std::numeric_limits<float>::max())throw std::runtime_error("ray query geometry must fit finite Vector3");}return v;
}
FrameVector query_size(World& w,Node& n) {
    const auto* value=property_value(w,n,"Size");
    if(!value||!value->is_object()||!value->contains("$vector3"))throw std::runtime_error("ray query part Size is unavailable");
    auto size=query_vector(value->at("$vector3"));for(double x:size)if(x<=0)throw std::runtime_error("ray query part Size must be positive");return size;
}
FrameMatrix query_inverse_rotation(const FrameMatrix& r){return {r[0],r[3],r[6],r[1],r[4],r[7],r[2],r[5],r[8]};}
void query_rigid(const CFrameValue& cf) {
    for(int a=0;a<3;++a)for(int b=0;b<3;++b) {
        double dot=0;for(int k=0;k<3;++k)dot+=cf.r[k*3+a]*cf.r[k*3+b];
        if(!std::isfinite(dot)||std::abs(dot-(a==b?1.0:0.0))>1e-4)throw std::runtime_error("ray query needs a rigid part rotation");
    }
    const auto& r=cf.r;
    const double determinant=r[0]*(r[4]*r[8]-r[5]*r[7])-r[1]*(r[3]*r[8]-r[5]*r[6])+r[2]*(r[3]*r[7]-r[4]*r[6]);
    if(std::abs(determinant-1)>1e-4)throw std::runtime_error("ray query needs a right-handed part rotation");
}
// Intersect a convex shape expressed as outward planes n.p <= extent.
ShapeRayHit query_planes(const FrameVector& o,const FrameVector& d,const std::vector<std::pair<FrameVector,double>>& planes,double limit) {
    double near=-std::numeric_limits<double>::infinity(),far=std::numeric_limits<double>::infinity();
    FrameVector entry{},exit{};
    for(const auto& [n,extent]:planes) {
        const double denominator=query_dot(n,d),gap=extent-query_dot(n,o);
        if(denominator==0){if(gap<0)return {};continue;}
        const double t=gap/denominator;
        if(denominator<0){if(t>near){near=t;entry=n;}}
        else if(t<far){far=t;exit=n;}
        if(near>far)return {};
    }
    const double t=near>=0?near:far;
    if(!std::isfinite(t)||t<0||t>limit)return {};
    return {true,t,query_unit(near>=0?entry:exit)};
}
std::vector<std::pair<FrameVector,double>> query_box_planes(const FrameVector& half) {
    return {{{1,0,0},half[0]},{{-1,0,0},half[0]},{{0,1,0},half[1]},{{0,-1,0},half[1]},{{0,0,1},half[2]},{{0,0,-1},half[2]}};
}
ShapeRayHit query_round(const FrameVector& o,const FrameVector& d,const FrameVector& h,bool cylinder,double limit) {
    double a=0,b=0,c=-1;const int first=cylinder?1:0;
    for(int i=first;i<3;++i){const double p=o[i]/h[i],v=d[i]/h[i];a+=v*v;b+=2*p*v;c+=p*p;}
    ShapeRayHit best;
    auto admit=[&](double t,FrameVector normal){if(std::isfinite(t)&&t>=0&&t<=limit&&(!best.hit||t<best.distance))best={true,t,query_unit(normal)};};
    const double discriminant=b*b-4*a*c;
    if(a>0&&discriminant>=0) {
        const double root=std::sqrt(discriminant),q=-.5*(b+std::copysign(root,b));
        const double t0=q/a,t1=q!=0?c/q:-b/(2*a);
        for(double t:{t0,t1})if(!cylinder||std::abs(o[0]+t*d[0])<=h[0]) {
            FrameVector normal{};for(int i=first;i<3;++i)normal[i]=(o[i]+t*d[i])/(h[i]*h[i]);admit(t,normal);
        }
    }
    if(cylinder&&d[0]!=0)for(double sign:{-1.0,1.0}) {
        const double t=(sign*h[0]-o[0])/d[0];const double y=(o[1]+t*d[1])/h[1],z=(o[2]+t*d[2])/h[2];
        if(y*y+z*z<=1)admit(t,{sign,0,0});
    }
    return best;
}
std::vector<std::pair<FrameVector,double>> query_primitive_planes(const FrameVector& half,int shape) {
    auto planes=query_box_planes(half);
    if(shape==3)planes.push_back({{0,1/half[1],-1/half[2]},0});
    // Corner wedge apex is (+X,+Y,-Z), matching the renderer's primitive.
    if(shape==4){planes.push_back({{-1/half[0],1/half[1],0},0});planes.push_back({{0,1/half[1],1/half[2]},0});}
    return planes;
}
ShapeRayHit query_primitive(const FrameVector& o,const FrameVector& d,const FrameVector& half,int shape,double limit) {
    if(shape==0||shape==2)return query_round(o,d,half,shape==2,limit);
    return query_planes(o,d,query_primitive_planes(half,shape),limit);
}
int query_part_shape(World& w,Node& n) {
    if(n.class_name=="WedgePart")return 3;
    if(n.class_name=="CornerWedgePart")return 4;
    if(n.class_name=="TrussPart"||class_is_a(w,n.class_name,"TriangleMeshPart"))return -1;
    const Json* value=property_value(w,n,"Shape");
    if(value&&value->is_object()&&value->contains("$enum"))value=&value->at("$enum").at("value");
    if(value){if(!value->is_number_integer()||*value<0||*value>4)throw std::runtime_error("ray query has invalid Part.Shape");return value->get<int>();}
    if(n.class_name=="Part")throw std::runtime_error("ray query Part.Shape is unavailable");
    // Seat/VehicleSeat/SpawnLocation are ordinary box primitives.
    if(n.class_name=="Seat"||n.class_name=="VehicleSeat"||n.class_name=="SpawnLocation")return 1;
    return -1;
}
bool query_within(World& w,Node& node,const std::string& ancestor,size_t& work) {
    if(ancestor.empty())return false;
    Node* cursor=&node;
    for(size_t depth=0;cursor&&depth<=w.nodes.size();++depth) {
        query_work(work);if(cursor->id==ancestor)return true;
        cursor=find_node(w,parent_id(w,*cursor));
    }
    if(cursor)throw std::runtime_error("ray query hierarchy contains a cycle");return false;
}
bool query_world_member(World& w,Node& node,Node& root,size_t& work) {
    auto* cursor=&node;
    for(size_t depth=0;cursor&&depth<=w.nodes.size();++depth) {
        query_work(work);
        if(cursor->id==root.id)return true;
        if(cursor->class_name=="Workspace"||cursor->class_name=="WorldModel")return false;
        cursor=find_node(w,parent_id(w,*cursor));
    }
    if(cursor)throw std::runtime_error("ray query hierarchy contains a cycle");return false;
}
bool query_matches(World& w,Node& node,const std::vector<std::string>& ids,size_t& work) {
    for(const auto& id:ids)if(query_within(w,node,id,work))return true;
    return false;
}
bool spatial_query_candidate(World& w,Node& node,Node& root,const WorldRayFilter& filter,const CollisionGroupTable& groups,size_t& work) {
    if(node.destroyed||!pivot_part(w,node)||!query_world_member(w,node,root,work))return false;
    if(query_matches(w,node,filter.exclude,work))return false;
    if(filter.include_active&&!query_matches(w,node,filter.include,work))return false;
    if(query_matches(w,node,filter.legacy,work)!=filter.legacy_include)return false;
    if(filter.brute_force)return true;
    const auto* can_query=property_value(w,node,"CanQuery");const auto* collide=property_value(w,node,"CanCollide");
    if(filter.respect_collide){if(collide&&*collide==false)return false;}
    else if(can_query&&*can_query==false&&collide&&*collide==false)return false;
    return collision_pair(groups,filter.collision_group,part_collision_group(w,node),true);
}
ShapeRayHit query_triangle(const FrameVector& o,const FrameVector& d,const FrameVector& a,const FrameVector& b,const FrameVector& c,double limit) {
    const auto ab=query_subtract(b,a),ac=query_subtract(c,a),normal=query_cross(ab,ac);
    const double area=std::hypot(normal[0],normal[1],normal[2]);
    if(!std::isfinite(area)||area<=1e-12)throw std::runtime_error("ray query decoded mesh has a degenerate triangle");
    const auto p=query_cross(d,ac);const double det=query_dot(ab,p);
    if(std::abs(det)<=area*std::hypot(d[0],d[1],d[2])*1e-12)return {};
    const auto offset=query_subtract(o,a);const double u=query_dot(offset,p)/det;
    if(u<0||u>1)return {};
    const auto q=query_cross(offset,ab);const double v=query_dot(d,q)/det;
    if(v<0||u+v>1)return {};
    const double t=query_dot(ac,q)/det;if(t<0||t>limit)return {};
    auto n=query_unit(normal);if(query_dot(n,d)>0)for(double& x:n)x=-x;
    return {true,t,n};
}
std::vector<FrameVector> query_mesh_points(const Json& data,size_t& work) {
    const auto& vertices=data.at("vertices");const auto& triangles=data.at("triangles");
    if(!vertices.is_array()||vertices.empty()||vertices.size()>65536||!triangles.is_array()||triangles.empty()||triangles.size()>131072)
        throw std::runtime_error("decoded collision mesh size is invalid");
    query_work(work,vertices.size()+triangles.size());
    std::vector<FrameVector> points;points.reserve(vertices.size());
    for(const auto& value:vertices)points.push_back(query_vector(value));
    return points;
}
std::array<size_t,3> query_mesh_indices(const Json& triangle,size_t count) {
    if(!triangle.is_array()||triangle.size()!=3)throw std::runtime_error("collision triangle requires three indices");
    std::array<size_t,3> indices;
    for(int i=0;i<3;++i) {
        if(!triangle[i].is_number_integer()||triangle[i]<0||triangle[i]>=count)
            throw std::runtime_error("collision triangle index out of range");
        indices[i]=triangle[i].get<size_t>();
    }
    return indices;
}
struct QueryVoxelGrid {
    FrameVector base,cell;
    const Json& occupancy;
    const Json* materials;
    int common;
    std::array<size_t,3> shape{};
    size_t count=1;
    explicit QueryVoxelGrid(const Json& data):
        base(query_vector(data.at("origin"))),cell(query_vector(data.at("cell_size"))),
        occupancy(data.at("occupancy")),materials(data.contains("materials")?&data.at("materials"):nullptr),
        common(data.contains("material")?query_material(data.at("material")):-1) {
        const auto& dims=data.at("dimensions");
        if(!dims.is_array()||dims.size()!=3||!occupancy.is_array())throw std::runtime_error("voxel grid shape is invalid");
        for(int i=0;i<3;++i) {
            if(cell[i]<=0||!dims[i].is_number_integer()||dims[i]<=0||dims[i]>1000000)
                throw std::runtime_error("voxel dimensions are invalid");
            shape[i]=dims[i].get<size_t>();
            if(shape[i]>1000000/count)throw std::runtime_error("voxel count limit exceeded");
            count*=shape[i];
        }
        if(occupancy.size()!=count)throw std::runtime_error("voxel occupancy length differs from dimensions");
        if(materials&&(!materials->is_array()||materials->size()!=count))
            throw std::runtime_error("voxel material count differs from dimensions");
    }
    int material_at(size_t i) const {return materials?query_material((*materials)[i]):common;}
    bool occupied(size_t i,bool ignore_water) const {
        if(!occupancy[i].is_boolean())throw std::runtime_error("voxel occupancy must be boolean");
        if(!occupancy[i].get<bool>())return false;
        const int material=material_at(i);
        if(ignore_water&&material<0)throw std::runtime_error("terrain material data is unavailable for IgnoreWater");
        return !ignore_water||material!=2048;
    }
    std::array<size_t,3> coordinates(size_t i) const {
        return {i/(shape[1]*shape[2]),(i/shape[2])%shape[1],i%shape[2]};
    }
    FrameVector center(const std::array<size_t,3>& xyz) const {
        FrameVector result;
        for(int i=0;i<3;++i)result[i]=base[i]+cell[i]*(double(xyz[i])+.5);
        return result;
    }
};
ShapeRayHit query_decoded_geometry(const Json& data,const FrameVector& o,const FrameVector& d,double limit,size_t& work,bool ignore_water=false) {
    if(!data.is_object()||!data.contains("kind")||!data["kind"].is_string())throw std::runtime_error("ray query collision payload lacks kind");
    ShapeRayHit best;
    if(data["kind"]=="triangle_mesh") {
        if(ignore_water) {
            if(!data.contains("material"))throw std::runtime_error("terrain material data is unavailable for IgnoreWater");
            if(query_material(data.at("material"))==2048)return best;
        }
        const auto points=query_mesh_points(data,work);
        for(const auto& triangle:data.at("triangles")) {
            const auto indices=query_mesh_indices(triangle,points.size());
            auto hit=query_triangle(o,d,points[indices[0]],points[indices[1]],points[indices[2]],best.hit?best.distance:limit);
            if(hit.hit&&(!best.hit||hit.distance<best.distance))best=hit;
        }
        if(best.hit&&data.contains("material"))best.material=query_material(data.at("material"));
        return best;
    }
    if(data["kind"]=="voxel_grid") {
        const QueryVoxelGrid grid(data);
        const auto& shape=grid.shape;
        FrameVector half=grid.cell;for(auto& v:half)v*=.5;
        query_work(work,grid.count);
        for(size_t index=0;index<grid.count;++index) {
            if(!grid.occupied(index,ignore_water))continue;
            const auto xyz=grid.coordinates(index);
            const auto center=grid.center(xyz);
            // Only exposed voxel faces are surfaces. Adjacent occupied cells
            // must not create an artificial hit inside a solid volume.
            auto local=query_subtract(o,center);
            for(int axis=0;axis<3;++axis)for(int sign:{-1,1}) {
                query_work(work);if(d[axis]==0)continue;
                const bool neighbor=sign<0?xyz[axis]>0:xyz[axis]+1<shape[axis];
                if(neighbor) {
                    const size_t stride=axis==0?shape[1]*shape[2]:axis==1?shape[2]:1;
                    if(grid.occupied(sign<0?index-stride:index+stride,ignore_water))continue;
                }
                const double t=(sign*half[axis]-local[axis])/d[axis];
                if(t<0||t>(best.hit?best.distance:limit))continue;
                bool within=true;for(int k=0;k<3;++k)if(k!=axis&&std::abs(local[k]+t*d[k])>half[k])within=false;
                if(within){FrameVector normal{};normal[axis]=sign;if(!best.hit||t<best.distance)best={true,t,normal,grid.material_at(index)};}
            }
        }
        return best;
    }
    throw std::runtime_error("ray query collision geometry kind is unsupported");
}
struct QueryGeometryPose { CFrameValue before,after;FrameVector scale; };
QueryGeometryPose query_geometry_pose(World& w,Node& node) {
    if(!node.properties.contains("CFrame"))throw std::runtime_error("decoded geometry source pose is unavailable");
    const auto before=pivot_decode(node.properties.at("CFrame")),after=pivot_frame(w,node,"CFrame");
    query_rigid(before);query_rigid(after);
    FrameVector scale{1,1,1};
    if(local_value(w,node,"Size")) {
        if(!node.properties.contains("Size"))throw std::runtime_error("decoded geometry source Size is unavailable");
        const auto original=query_vector(node.properties.at("Size").at("$vector3")),current=query_size(w,node);
        for(int i=0;i<3;++i){if(original[i]<=0)throw std::runtime_error("decoded geometry source Size is invalid");scale[i]=current[i]/original[i];}
    }
    return {before,after,scale};
}
ShapeRayHit query_decoded(World& w,Node& node,const Json& data,const FrameVector& o,const FrameVector& d,double limit,size_t& work,bool ignore_water=false) {
    if(!local_value(w,node,"CFrame")&&!local_value(w,node,"Size"))
        return query_decoded_geometry(data,o,d,limit,work,ignore_water);
    // Transform the ray back through local pose/size edits, retaining its
    // world-distance parameter for triangle meshes and voxel surfaces.
    const auto [before,after,scale]=query_geometry_pose(w,node);
    const auto inverse=query_inverse_rotation(after.r);
    auto source_o=frame_rotate(inverse,query_subtract(o,after.p)),source_d=frame_rotate(inverse,d);
    for(int i=0;i<3;++i){source_o[i]/=scale[i];source_d[i]/=scale[i];}
    source_o=frame_rotate(before.r,source_o);source_d=frame_rotate(before.r,source_d);
    for(int i=0;i<3;++i){source_o[i]+=before.p[i];if(!std::isfinite(source_o[i])||!std::isfinite(source_d[i]))throw std::runtime_error("decoded geometry transform exceeds finite range");}
    auto hit=query_decoded_geometry(data,source_o,source_d,limit,work,ignore_water);
    if(hit.hit) {
        auto normal=frame_rotate(query_inverse_rotation(before.r),hit.normal);
        for(int i=0;i<3;++i)normal[i]/=scale[i];
        hit.normal=query_unit(frame_rotate(after.r,normal));
    }
    return hit;
}
WorldRayHit query_world_ray(World& w,Node& workspace,const FrameVector& o,const FrameVector& d,double distance,const WorldRayFilter& filter) {
    WorldRayHit best;best.distance=distance;size_t work=0;double unknown_distance=std::numeric_limits<double>::infinity();std::string unknown;
    CollisionGroupTable groups;
    if(!filter.brute_force)groups=read_collision_groups(w,collision_query_root(w,workspace));
    for(const auto& id:w.order) {
        query_work(work);auto* node=find_node(w,id);
        if(!node||!spatial_query_candidate(w,*node,workspace,filter,groups,work))continue;
        if(const auto* geometry=property_value(w,*node,"CollisionGeometry")) {
            if(filter.terrain_cells_cubes&&node->class_name=="Terrain"&&
                    (!geometry->is_object()||geometry->value("kind","")!="voxel_grid"))
                throw std::runtime_error("terrainCellsAreCubes requires decoded voxel_grid terrain cells");
            auto hit=query_decoded(w,*node,*geometry,o,d,best.distance,work,filter.ignore_water&&node->class_name=="Terrain");
            if(hit.hit&&(!best.node||hit.distance<best.distance)) {
                FrameVector local=hit.normal;
                if(const auto* cf=property_value(w,*node,"CFrame")){auto pose=pivot_decode(*cf);query_rigid(pose);local=frame_rotate(query_inverse_rotation(pose.r),hit.normal);}
                best={node,hit.distance,hit.normal,local,hit.material};
            }
            continue;
        }
        if(node->class_name=="Terrain"){unknown_distance=0;unknown="terrain collision voxels are unavailable";continue;}
        auto pose=pivot_frame(w,*node,"CFrame");query_rigid(pose);auto half=query_size(w,*node);for(double& value:half)value/=2;
        auto inverse=query_inverse_rotation(pose.r);
        auto local_o=frame_rotate(inverse,query_subtract(o,pose.p)),local_d=frame_rotate(inverse,d);
        const int shape=query_part_shape(w,*node);
        if(shape<0) {
            auto bound=query_planes(local_o,local_d,query_box_planes(half),best.distance);
            const bool inside=std::abs(local_o[0])<half[0]&&std::abs(local_o[1])<half[1]&&std::abs(local_o[2])<half[2];
            if((bound.hit||inside)&&(inside?0:bound.distance)<unknown_distance){unknown_distance=inside?0:bound.distance;unknown="collision geometry is unavailable for "+node->class_name;}
            continue;
        }
        auto hit=query_primitive(local_o,local_d,half,shape,best.distance);
        if(hit.hit&&(!best.node||hit.distance<best.distance))best={node,hit.distance,frame_rotate(pose.r,hit.normal),hit.normal};
    }
    if(unknown_distance<=best.distance)throw std::runtime_error(unknown);
    return best;
}
WorldRayHit query_mouse_world(World& w,Node& workspace,const FrameVector& o,const FrameVector& d,const std::string& filter) {
    WorldRayFilter params;
    if(!filter.empty())params.exclude.push_back(filter);
    if(auto* player=find_node(w,w.local_player))if(const auto* value=property_value(w,*player,"Character");value&&value->is_object()&&value->contains("$ref"))
        params.exclude.push_back(value->at("$ref").get<std::string>());
    return query_world_ray(w,workspace,o,d,1000,params);
}
