// RotationCurveKey datatype. Included by world_bindings.cpp after CFrame and
// geometry enum support are declared; install_rotation_curve_key is called
// after those bindings are installed.
namespace rotation_curve_key {
constexpr const char* kMeta = "WorldSnapshot.RotationCurveKey";
constexpr const char* kInterpolation = "KeyInterpolationMode";
struct Value {
    double time = 0;
    CFrameValue frame;
    int interpolation = 2; // Enum.KeyInterpolationMode.Cubic
    bool left_assigned = false;
    bool right_assigned = false;
    double left = 0;
    double right = 0;
};

Value* read(lua_State* L, int index) {
    if (!lua_isuserdata(L,index) || !lua_getmetatable(L,index)) return nullptr;
    luaL_getmetatable(L,kMeta);
    bool same=lua_rawequal(L,-1,-2)!=0;
    lua_pop(L,2);
    return same ? static_cast<Value*>(lua_touserdata(L,index)) : nullptr;
}
int mode_number(lua_State* L,int index) {
    int value=0;
    if(!read_geometry_enum_item(L,index,kInterpolation,value) || value<0 || value>2)
        luaL_argerror(L,index,"Enum.KeyInterpolationMode expected");
    return value;
}
void push_mode(lua_State* L,int value) {
    if(!push_geometry_enum_item(L,kInterpolation,value)) lua_pushnil(L);
}
int construct(lua_State* L) {
    int count=lua_gettop(L);
    if(count<2 || count>3) return luaL_error(L,"RotationCurveKey.new expects time, CFrame, and optional interpolation mode"),0;
    if(lua_type(L,1)!=LUA_TNUMBER) return luaL_argerror(L,1,"number expected"),0;
    if(!has_meta(L,2,kCFrameMeta)) return luaL_argerror(L,2,"CFrame expected"),0;
    int mode=count==3?mode_number(L,3):2;
    auto* out=static_cast<Value*>(lua_newuserdatadtor(L,sizeof(Value),
        [](lua_State*,void* raw){static_cast<Value*>(raw)->~Value();}));
    new(out)Value;
    out->time=lua_tonumber(L,1);
    out->frame=*static_cast<CFrameValue*>(lua_touserdata(L,2));
    out->interpolation=mode;
    luaL_getmetatable(L,kMeta);lua_setmetatable(L,-2);
    return 1;
}
int index(lua_State* L) {
    auto* value=static_cast<Value*>(luaL_checkudata(L,1,kMeta));
    const char* key=luaL_checkstring(L,2);
    if(std::strcmp(key,"Time")==0) lua_pushnumber(L,value->time);
    else if(std::strcmp(key,"Value")==0) {
        auto* frame=new_cframe(L);*frame=value->frame;
        luaL_getmetatable(L,kCFrameMeta);lua_setmetatable(L,-2);
    } else if(std::strcmp(key,"Interpolation")==0) push_mode(L,value->interpolation);
    else if(std::strcmp(key,"LeftTangent")==0) {
        if(value->left_assigned) lua_pushnumber(L,value->left); else lua_pushnil(L);
    } else if(std::strcmp(key,"RightTangent")==0) {
        if(value->right_assigned) lua_pushnumber(L,value->right); else lua_pushnil(L);
    } else return luaL_error(L,"unknown RotationCurveKey member '%s'",key),0;
    return 1;
}
int newindex(lua_State* L) {
    auto* value=static_cast<Value*>(luaL_checkudata(L,1,kMeta));
    const char* key=luaL_checkstring(L,2);
    if(std::strcmp(key,"Time")==0) {
        if(lua_type(L,3)!=LUA_TNUMBER)return luaL_error(L,"Time must be a number"),0;
        value->time=lua_tonumber(L,3);
    } else if(std::strcmp(key,"Value")==0) {
        if(!has_meta(L,3,kCFrameMeta))return luaL_error(L,"Value must be a CFrame"),0;
        value->frame=*static_cast<CFrameValue*>(lua_touserdata(L,3));
    } else if(std::strcmp(key,"Interpolation")==0) {
        value->interpolation=mode_number(L,3);
        if(value->interpolation!=2) {value->right_assigned=false;value->right=0;}
    } else if(std::strcmp(key,"LeftTangent")==0 || std::strcmp(key,"RightTangent")==0) {
        bool right=std::strcmp(key,"RightTangent")==0;
        if(lua_type(L,3)!=LUA_TNUMBER)return luaL_error(L,"%s must be a number",key),0;
        if(right&&value->interpolation!=2)return luaL_error(L,"RightTangent requires Cubic interpolation"),0;
        if(right){value->right=lua_tonumber(L,3);value->right_assigned=true;}
        else {value->left=lua_tonumber(L,3);value->left_assigned=true;}
    } else return luaL_error(L,"unknown RotationCurveKey member '%s'",key),0;
    return 0;
}
void install_rotation_curve_key(lua_State* L) {
    luaL_newmetatable(L,kMeta);
    lua_pushcfunction(L,index,"__index");lua_setfield(L,-2,"__index");
    lua_pushcfunction(L,newindex,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushliteral(L,"RotationCurveKey");lua_setfield(L,-2,"__type");
    lua_pushliteral(L,"RotationCurveKey");lua_setfield(L,-2,"__metatable");
    lua_setreadonly(L,-1,true);lua_pop(L,1);
    lua_newtable(L);lua_pushcfunction(L,construct,"new");lua_setfield(L,-2,"new");
    lua_setreadonly(L,-1,true);lua_setglobal(L,"RotationCurveKey");
}
Json encode(const Value& value) {
    Json frame={{"position",Json::array()},{"rotation",Json::array()}};
    if(!value.frame.rotation_known)throw std::runtime_error("RotationCurveKey rotation is unavailable");
    for(double n:value.frame.p)frame["position"].push_back(encode_number(n));
    for(double n:value.frame.r)frame["rotation"].push_back(encode_number(n));
    return Json{{"$rotation_curve_key",Json::array({encode_number(value.time),frame,value.interpolation,
        value.left_assigned?encode_number(value.left):Json(nullptr),
        value.right_assigned?encode_number(value.right):Json(nullptr)})}};
}
bool decode(lua_State* L,const Json& input) {
    if(!input.is_object()||input.size()!=1||!input.contains("$rotation_curve_key"))return false;
    const auto& data=input.at("$rotation_curve_key");
    if(!data.is_array()||data.size()!=5||!data[2].is_number_integer()||data[2]<0||data[2]>2)
        throw std::runtime_error("invalid RotationCurveKey components");
    Value value;value.time=json_number(data[0]);value.interpolation=data[2].get<int>();
    const auto& frame=data[1];
    if(!frame.is_object()||!frame.contains("position")||!frame["position"].is_array()||frame["position"].size()!=3
        ||!frame.contains("rotation")||!frame["rotation"].is_array()||frame["rotation"].size()!=9)
        throw std::runtime_error("invalid RotationCurveKey CFrame");
    for(size_t i=0;i<3;++i)value.frame.p[i]=json_number(frame["position"][i]);
    for(size_t i=0;i<9;++i)value.frame.r[i]=json_number(frame["rotation"][i]);
    if(!data[3].is_null()){value.left=json_number(data[3]);value.left_assigned=true;}
    if(!data[4].is_null()){
        if(value.interpolation!=2)throw std::runtime_error("RotationCurveKey right tangent requires Cubic interpolation");
        value.right=json_number(data[4]);value.right_assigned=true;
    }
    auto* out=static_cast<Value*>(lua_newuserdatadtor(L,sizeof(Value),
        [](lua_State*,void* raw){static_cast<Value*>(raw)->~Value();}));
    new(out)Value(std::move(value));
    luaL_getmetatable(L,kMeta);lua_setmetatable(L,-2);
    return true;
}
}
