// Local motion smoothing. Explicit dt is deterministic; the default uses the
// last source-scheduler Heartbeat interval, initially 1/60 second.
using DampVector=std::array<float,3>;
struct DampResult {DampVector value,velocity;};
DampResult damp_vector(const DampVector& current,const DampVector& target,const DampVector& velocity,
                       int dimensions,float smooth,float speed,float dt) {
    DampResult out{current,velocity};
    if(smooth<.0001f)return {target,{}};
    if(dt<=0)return out;
    const float omega=2.0f/smooth,x=omega*dt;
    const float decay=1.0f/(1.0f+x+.48f*x*x+.235f*x*x*x);
    DampVector change{};
    double length=0;
    for(int i=0;i<dimensions;++i){change[i]=current[i]-target[i];length+=double(change[i])*change[i];}
    length=std::sqrt(length);
    const double limit=double(speed)*smooth;
    if(dimensions==1)change[0]=static_cast<float>(std::min(std::max(double(change[0]),-limit),limit));
    else if(length>std::fabs(limit))for(int i=0;i<dimensions;++i)change[i]=static_cast<float>(change[i]*(limit/length));
    double overshoot=0;
    for(int i=0;i<dimensions;++i) {
        const float clamped_target=current[i]-change[i];
        const float temp=(velocity[i]+omega*change[i])*dt;
        out.velocity[i]=(velocity[i]-omega*temp)*decay;
        out.value[i]=clamped_target+(change[i]+temp)*decay;
        overshoot+=double(target[i]-current[i])*(out.value[i]-target[i]);
    }
    if(overshoot>0)return {target,{}};
    return out;
}
void damp_require_finite(lua_State* L,const DampResult& out,int dimensions) {
    for(int i=0;i<dimensions;++i)if(!std::isfinite(out.value[i])||!std::isfinite(out.velocity[i]))
        luaL_error(L,"SmoothDamp result exceeds finite float32 range");
}
DampVector damp_frame_vector(lua_State* L,const FrameVector& value) {
    DampVector out{};
    for(int i=0;i<3;++i) {
        out[i]=static_cast<float>(value[i]);
        if(!std::isfinite(out[i]))luaL_error(L,"SmoothDamp requires finite float32 CFrame components");
    }
    return out;
}
int damp_cframe(lua_State* L,float smooth,float speed,float dt) {
    const auto* current=checked_cframe(L,2);const auto* target=checked_cframe(L,3);
    CFrameValue velocity;
    velocity.r={};
    // Native SmoothDamp initializes a zero velocity when its variant argument
    // is not a CFrame. A CFrame velocity is an opaque payload, not a rotation:
    // the first matrix row holds angular velocity; the other six slots persist.
    if(has_meta(L,4,kCFrameMeta))velocity=*checked_cframe(L,4);
    const auto current_position=damp_frame_vector(L,current->p);
    const auto target_position=damp_frame_vector(L,target->p);
    const auto linear_velocity=damp_frame_vector(L,velocity.p);
    const auto angular_velocity=damp_frame_vector(L,{velocity.r[0],velocity.r[1],velocity.r[2]});
    for(const auto* frame:std::array<const CFrameValue*,3>{current,target,&velocity})for(double v:frame->r)if(!std::isfinite(static_cast<float>(v)))
        return luaL_error(L,"SmoothDamp requires finite CFrame components"),0;
    if(smooth<.0001f) {
        push_frame_value(L,target->p,target->r);
        push_frame_value(L,{},FrameMatrix{});return 2;
    }
    if(dt<=0){push_frame_value(L,current->p,current->r);push_frame_value(L,velocity.p,velocity.r);return 2;}
    const auto linear=damp_vector(current_position,target_position,linear_velocity,3,smooth,speed,dt);
    FrameMatrix target_inverse{target->r[0],target->r[3],target->r[6],
                               target->r[1],target->r[4],target->r[7],
                               target->r[2],target->r[5],target->r[8]};
    auto q=frame_matrix_quaternion(L,frame_product(current->r,target_inverse));
    if(q[3]<0)for(double& component:q)component=-component;
    const double sine=std::hypot(q[0],q[1],q[2]);
    const double angle=2*std::atan2(sine,q[3]);
    const auto error=damp_frame_vector(L,sine>0?FrameVector{q[0]*angle/sine,q[1]*angle/sine,q[2]*angle/sine}:FrameVector{});
    const auto angular=damp_vector(error,{},angular_velocity,3,smooth,speed,dt);
    damp_require_finite(L,linear,3);damp_require_finite(L,angular,3);
    const double rotation_length=std::hypot(angular.value[0],angular.value[1],angular.value[2]);
    const double scale=rotation_length>0?std::sin(rotation_length/2)/rotation_length:.5;
    FrameQuaternion rotation{angular.value[0]*scale,angular.value[1]*scale,angular.value[2]*scale,std::cos(rotation_length/2)};
    auto matrix=frame_product(frame_quaternion_matrix(rotation),target->r);
    for(double& component:matrix)component=static_cast<float>(component);
    push_frame_value(L,{linear.value[0],linear.value[1],linear.value[2]},matrix);
    for(int i=0;i<3;++i){velocity.p[i]=linear.velocity[i];velocity.r[i]=angular.velocity[i];}
    push_frame_value(L,velocity.p,velocity.r);return 2;
}

int tween_smooth_damp(lua_State* L) {
    auto* service=checked_handle(L,1);
    if(service->node->class_name!="TweenService"||service->node->destroyed)
        return luaL_error(L,"SmoothDamp requires TweenService"),0;
    const float smooth=static_cast<float>(luaL_checknumber(L,5));
    const float speed=static_cast<float>(luaL_optnumber(L,6,std::numeric_limits<float>::infinity()));
    const float dt=static_cast<float>(luaL_optnumber(L,7,service->world->heartbeat_delta));
    if(!std::isfinite(smooth)||!std::isfinite(dt)||std::isnan(speed))
        return luaL_error(L,"SmoothDamp timing must be finite and maxSpeed must not be NaN"),0;
    if(has_meta(L,2,kCFrameMeta))return damp_cframe(L,smooth,speed,dt);
    DampVector current{},target{},velocity{};
    int dimensions=0;
    const bool scalar=lua_type(L,2)==LUA_TNUMBER;
    const bool vector3=lua_isvector(L,2);
    double vx,vy;
    const bool vector2=read_vector2(L,2,vx,vy);
    if(scalar)dimensions=1;
    else if(vector2)dimensions=2;
    else if(vector3)dimensions=3;
    else return luaL_error(L,"SmoothDamp supports numbers, Vector2, Vector3 and CFrame"),0;
    for(int arg=2;arg<=4;++arg) {
        auto& values=arg==2?current:arg==3?target:velocity;
        if(scalar) {
            luaL_checktype(L,arg,LUA_TNUMBER);values[0]=static_cast<float>(lua_tonumber(L,arg));
        } else if(vector3) {
            const auto* v=luaL_checkvector(L,arg);
            for(int i=0;i<3;++i)values[i]=v[i];
        } else {
            double x,y;
            if(!read_vector2(L,arg,x,y))return luaL_error(L,"SmoothDamp values must all have the same datatype"),0;
            values[0]=static_cast<float>(x);values[1]=static_cast<float>(y);
        }
        for(int i=0;i<dimensions;++i)if(!std::isfinite(values[i]))
            return luaL_error(L,"SmoothDamp values must have finite float32 components"),0;
    }
    const auto output=damp_vector(current,target,velocity,dimensions,smooth,speed,dt);
    damp_require_finite(L,output,dimensions);
    const auto& result=output.value;const auto& next_velocity=output.velocity;
    if(scalar){lua_pushnumber(L,result[0]);lua_pushnumber(L,next_velocity[0]);}
    else if(vector2){push_vector2(L,result[0],result[1]);push_vector2(L,next_velocity[0],next_velocity[1]);}
    else {lua_pushvector(L,result[0],result[1],result[2]);lua_pushvector(L,next_velocity[0],next_velocity[1],next_velocity[2]);}
    return 2;
}
