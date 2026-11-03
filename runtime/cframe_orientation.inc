// Ordinary orientation math for authored scripts. Included after the common
// CFrame helpers; all returned values use the existing snapshot userdata.
using FrameQuaternion = std::array<double, 4>; // x, y, z, w

FrameVector frame_cross(const FrameVector& a, const FrameVector& b) {
    return {a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]};
}
double frame_dot(const FrameVector& a, const FrameVector& b) {
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
}
FrameVector frame_unit(lua_State* L, FrameVector v) {
    double length = std::hypot(v[0], v[1], v[2]);
    if (!std::isfinite(length) || length == 0)
        luaL_error(L, "CFrame direction must be finite and nonzero");
    for (double& x : v) x /= length;
    return v;
}
FrameVector finite_frame_vector(lua_State* L, int index) {
    auto v = checked_frame_vector(L, index);
    for (double x : v)
        if (!std::isfinite(x)) luaL_error(L, "CFrame vector must be finite");
    return v;
}
FrameMatrix frame_columns(const FrameVector& x, const FrameVector& y, const FrameVector& z) {
    return {x[0],y[0],z[0], x[1],y[1],z[1], x[2],y[2],z[2]};
}

FrameQuaternion frame_quaternion_unit(lua_State* L, FrameQuaternion q) {
    double scale = 0;
    for (double x : q) {
        if (!std::isfinite(x)) luaL_error(L, "CFrame quaternion must be finite");
        scale = std::max(scale, std::fabs(x));
    }
    if (scale == 0) luaL_error(L, "CFrame quaternion must be nonzero");
    double square = 0;
    for (double& x : q) { x /= scale; square += x*x; }
    double length = std::sqrt(square);
    for (double& x : q) x /= length;
    return q;
}
FrameMatrix frame_quaternion_matrix(const FrameQuaternion& q) {
    double x=q[0], y=q[1], z=q[2], w=q[3];
    return {1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
            2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
            2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)};
}
FrameQuaternion frame_matrix_quaternion(lua_State* L, const FrameMatrix& r) {
    for (double x : r)
        if (!std::isfinite(x)) luaL_error(L, "CFrame rotation must be finite");
    FrameQuaternion q{};
    // Select the largest quaternion component to avoid division by a tiny
    // scalar component near a half turn.
    double trace = r[0]+r[4]+r[8];
    if (trace > 0) {
        double s=2*std::sqrt(trace+1);
        q={(r[7]-r[5])/s, (r[2]-r[6])/s, (r[3]-r[1])/s, s/4};
    } else if (r[0] >= r[4] && r[0] >= r[8]) {
        double s=2*std::sqrt(std::max(0.0, 1+r[0]-r[4]-r[8]));
        q={s/4, (r[1]+r[3])/s, (r[2]+r[6])/s, (r[7]-r[5])/s};
    } else if (r[4] >= r[8]) {
        double s=2*std::sqrt(std::max(0.0, 1+r[4]-r[0]-r[8]));
        q={(r[1]+r[3])/s, s/4, (r[5]+r[7])/s, (r[2]-r[6])/s};
    } else {
        double s=2*std::sqrt(std::max(0.0, 1+r[8]-r[0]-r[4]));
        q={(r[2]+r[6])/s, (r[5]+r[7])/s, s/4, (r[3]-r[1])/s};
    }
    return frame_quaternion_unit(L, q);
}

int cframe_quaternion_new(lua_State* L) {
    FrameVector p;
    FrameQuaternion q;
    for (int i=0; i<3; ++i) {
        p[i]=luaL_checknumber(L, i+1);
        if (!std::isfinite(p[i])) luaL_error(L, "CFrame position must be finite");
    }
    for (int i=0; i<4; ++i) q[i]=luaL_checknumber(L, i+4);
    return push_frame_value(L, p, frame_quaternion_matrix(frame_quaternion_unit(L, q)));
}

int cframe_look(lua_State* L, bool target_point) {
    auto p=finite_frame_vector(L, 1);
    auto direction=finite_frame_vector(L, 2);
    if (target_point)
        for (int i=0; i<3; ++i) direction[i]-=p[i];
    auto forward=frame_unit(L, direction);
    auto up=lua_isnoneornil(L, 3) ? FrameVector{0,1,0} : frame_unit(L, finite_frame_vector(L, 3));
    auto right=frame_cross(forward, up);
    if (std::hypot(right[0], right[1], right[2]) < 1e-12) {
        // The up direction cannot determine roll when parallel to the view.
        // Use X at the vertical pole, and Z if X is itself parallel.
        up=std::fabs(forward[0]) < .9 ? FrameVector{1,0,0} : FrameVector{0,0,1};
        right=frame_cross(forward, up);
    }
    right=frame_unit(L, right);
    up=frame_cross(right, forward);
    FrameVector back{-forward[0],-forward[1],-forward[2]};
    return push_frame_value(L, p, frame_columns(right, up, back));
}
int cframe_look_at(lua_State* L) { return cframe_look(L, true); }
int cframe_look_along(lua_State* L) { return cframe_look(L, false); }

int cframe_lerp(lua_State* L) {
    auto* a=checked_cframe(L, 1);
    auto* b=checked_cframe(L, 2);
    double alpha=luaL_checknumber(L, 3);
    if (!std::isfinite(alpha)) luaL_error(L, "CFrame interpolation alpha must be finite");
    if (alpha == 0) return push_frame_value(L, a->p, a->r);
    if (alpha == 1) return push_frame_value(L, b->p, b->r);
    auto qa=frame_matrix_quaternion(L, a->r), qb=frame_matrix_quaternion(L, b->r);
    double dot=0;
    for (int i=0; i<4; ++i) dot+=qa[i]*qb[i];
    if (dot < 0) { for (double& v : qb) v=-v; dot=-dot; }
    dot=std::clamp(dot, 0.0, 1.0);
    double sine=std::sqrt(std::max(0.0, 1-dot*dot));
    double wa=1-alpha, wb=alpha;
    if (sine > 1e-8) {
        double angle=std::atan2(sine, dot);
        wa=std::sin((1-alpha)*angle)/sine;
        wb=std::sin(alpha*angle)/sine;
    }
    FrameQuaternion q;
    for (int i=0; i<4; ++i) q[i]=wa*qa[i]+wb*qb[i];
    FrameVector p;
    for (int i=0; i<3; ++i) p[i]=a->p[i]+alpha*(b->p[i]-a->p[i]);
    return push_frame_value(L, p, frame_quaternion_matrix(frame_quaternion_unit(L, q)));
}

int cframe_to_axis_angle(lua_State* L) {
    auto q=frame_matrix_quaternion(L, checked_cframe(L, 1)->r);
    if (q[3] < 0) for (double& v : q) v=-v;
    double length=std::hypot(q[0], q[1], q[2]);
    if (length == 0) lua_pushvector(L, 1,0,0);
    else lua_pushvector(L, q[0]/length, q[1]/length, q[2]/length);
    lua_pushnumber(L, 2*std::atan2(length, q[3]));
    return 2;
}

double frame_rotation_angle(lua_State* L,const FrameMatrix& left,const FrameMatrix& right) {
    auto a=frame_matrix_quaternion(L,left);
    auto b=frame_matrix_quaternion(L,right);
    // The vector part of conjugate(a)*b remains accurate near zero angle.
    FrameVector relative{
        a[3]*b[0]-a[0]*b[3]-a[1]*b[2]+a[2]*b[1],
        a[3]*b[1]+a[0]*b[2]-a[1]*b[3]-a[2]*b[0],
        a[3]*b[2]-a[0]*b[1]+a[1]*b[0]-a[2]*b[3]};
    double scalar=0;
    for (int i=0; i<4; ++i) scalar+=a[i]*b[i];
    return 2*std::atan2(std::hypot(relative[0],relative[1],relative[2]), std::fabs(scalar));
}
int cframe_angle_between(lua_State* L) {
    lua_pushnumber(L,frame_rotation_angle(L,checked_cframe(L,1)->r,checked_cframe(L,2)->r));
    return 1;
}

int cframe_rotation_between(lua_State* L) {
    auto from=frame_unit(L, finite_frame_vector(L, 1));
    auto to=frame_unit(L, finite_frame_vector(L, 2));
    auto axis=frame_cross(from, to);
    double sine=std::hypot(axis[0],axis[1],axis[2]);
    double cosine=std::clamp(frame_dot(from, to), -1.0, 1.0);
    if (sine < 1e-12 && cosine > 0)
        return push_frame_value(L, {}, {1,0,0,0,1,0,0,0,1});
    if (sine < 1e-12) {
        int least=0;
        for (int i=1; i<3; ++i) if (std::fabs(from[i]) < std::fabs(from[least])) least=i;
        FrameVector basis{}; basis[least]=1;
        axis=frame_cross(from, basis);
    }
    axis=frame_unit(L, axis);
    double half=std::atan2(sine, cosine)/2;
    double s=std::sin(half);
    return push_frame_value(L, {}, frame_quaternion_matrix({axis[0]*s,axis[1]*s,axis[2]*s,std::cos(half)}));
}

FrameVector frame_euler_angles(const FrameMatrix& r,bool yxz) {
    double x, y, z;
    if (yxz) {
        x=std::asin(std::clamp(-r[5], -1.0, 1.0));
        if (std::fabs(std::cos(x)) > 1e-7) { y=std::atan2(r[2],r[8]); z=std::atan2(r[3],r[4]); }
        else { y=std::atan2(-r[6],r[0]); z=0; }
    } else {
        y=std::asin(std::clamp(r[2], -1.0, 1.0));
        if (std::fabs(std::cos(y)) > 1e-7) { x=std::atan2(-r[5],r[8]); z=std::atan2(-r[1],r[0]); }
        else { x=std::atan2(r[7],r[4]); z=0; }
    }
    return {x,y,z};
}
int cframe_to_euler(lua_State* L, bool yxz) {
    const auto& r=checked_cframe(L,1)->r;
    for(double v:r)if(!std::isfinite(v))luaL_error(L,"CFrame rotation must be finite");
    auto angles=frame_euler_angles(r,yxz);
    for(double value:angles)lua_pushnumber(L,value);
    return 3;
}
int cframe_to_euler_xyz(lua_State* L) { return cframe_to_euler(L, false); }
int cframe_to_euler_yxz(lua_State* L) { return cframe_to_euler(L, true); }

int cframe_orthonormalize(lua_State* L) {
    auto* value=checked_cframe(L, 1);
    auto x=frame_unit(L, {value->r[0],value->r[3],value->r[6]});
    FrameVector y{value->r[1],value->r[4],value->r[7]};
    auto z=frame_unit(L, frame_cross(x,y));
    y=frame_cross(z,x);
    return push_frame_value(L, value->p, frame_columns(x,y,z));
}

int geometry_rotation_order(lua_State* L,int index) {
    return lua_isnoneornil(L,index)?0:checked_geometry_value(L,index,kGeometryRotationOrder);
}
int cframe_from_euler_general(lua_State* L) {
    double x=finite_frame_angle(L,1),y=finite_frame_angle(L,2),z=finite_frame_angle(L,3);
    int order=geometry_rotation_order(L,4);
    double cx=std::cos(x),sx=std::sin(x),cy=std::cos(y),sy=std::sin(y),cz=std::cos(z),sz=std::sin(z);
    FrameMatrix rotations[3]={{1,0,0,0,cx,-sx,0,sx,cx},
        {cy,0,sy,0,1,0,-sy,0,cy},{cz,-sz,0,sz,cz,0,0,0,1}};
    FrameMatrix result{1,0,0,0,1,0,0,0,1};
    const char* axes=kGeometryEnums[kGeometryRotationOrder].names[order];
    for(int i=0;i<3;++i)result=frame_product(result,rotations[axes[i]-'X']);
    return push_frame_value(L,{},result);
}
int cframe_to_euler_general(lua_State* L) {
    const auto& r=checked_cframe(L,1)->r;
    int order=geometry_rotation_order(L,2);
    for(double v:r)if(!std::isfinite(v))luaL_error(L,"CFrame rotation must be finite");
    const char* names=kGeometryEnums[kGeometryRotationOrder].names[order];
    int axes[3]={names[0]-'X',names[1]-'X',names[2]-'X'};
    // Conjugating by an axis permutation reduces every order to XYZ. An odd
    // permutation reverses handedness, so its recovered angles change sign.
    int inversions=(axes[0]>axes[1])+(axes[0]>axes[2])+(axes[1]>axes[2]);
    double sign=(inversions%2)?-1:1;
    FrameMatrix m;
    for(int row=0;row<3;++row)for(int col=0;col<3;++col)m[row*3+col]=r[axes[row]*3+axes[col]];
    FrameVector local;
    local[1]=std::asin(std::clamp(m[2],-1.0,1.0));
    if(std::fabs(std::cos(local[1]))>1e-7) {
        local[0]=std::atan2(-m[5],m[8]);local[2]=std::atan2(-m[1],m[0]);
    } else {local[0]=std::atan2(m[7],m[4]);local[2]=0;}
    FrameVector angles;
    for(int i=0;i<3;++i)angles[axes[i]]=sign*local[i];
    for(double angle:angles)lua_pushnumber(L,angle);
    return 3;
}

int cframe_fuzzy_eq(lua_State* L) {
    const auto* a=checked_cframe(L,1);const auto* b=checked_cframe(L,2);
    double epsilon=luaL_optnumber(L,3,1e-5);
    if(!std::isfinite(epsilon)||std::fabs(epsilon)>.1)
        luaL_error(L,"The eps value provided to FuzzyEq should be a small positive value <= 0.1");
    bool equal=true;
    for(size_t i=0;i<3;++i)equal=equal&&runtime_fuzzy_component(a->p[i],b->p[i],epsilon);
    // Orientation tolerance is the total relative angle, not a comparison of
    // matrix elements (which would depend on the world-space basis).
    equal=equal&&(a->r==b->r||frame_rotation_angle(L,a->r,b->r)<=std::fabs(epsilon)*(1+1e-12));
    lua_pushboolean(L,equal);return 1;
}

void install_frame_orientation_methods(lua_State* L) {
    frame_method(L,"FuzzyEq",cframe_fuzzy_eq);
    frame_method(L,"ToEulerAngles",cframe_to_euler_general);
    frame_method(L,"Lerp",cframe_lerp);
    frame_method(L,"ToAxisAngle",cframe_to_axis_angle);
    frame_method(L,"AngleBetween",cframe_angle_between);
    frame_method(L,"ToEulerAnglesXYZ",cframe_to_euler_xyz);
    frame_method(L,"ToEulerAnglesYXZ",cframe_to_euler_yxz);
    frame_method(L,"ToOrientation",cframe_to_euler_yxz);
    frame_method(L,"Orthonormalize",cframe_orthonormalize);
    frame_method(L,"components",cframe_components);
}
void install_frame_orientation_constructors(lua_State* L) {
    frame_method(L,"fromEulerAngles",cframe_from_euler_general);
    frame_method(L,"lookAt",cframe_look_at);
    frame_method(L,"lookAlong",cframe_look_along);
    frame_method(L,"fromRotationBetweenVectors",cframe_rotation_between);
}
