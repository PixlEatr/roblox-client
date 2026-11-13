#include "vector2_bindings.hpp"
#include "vector_math_compat.hpp"
#include <atomic>
#include <cstdint>

#include <lua.h>
#include <lualib.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <new>

namespace {
constexpr const char* kMeta = "HeadlessRuntime.Vector2";
struct Value { double x, y; };
struct RustVector2Api {
    uint32_t version;
    int32_t (*apply)(uint32_t, const Value*, const Value*, double, const Value*, size_t, Value*, double*);
};
std::atomic<const RustVector2Api*> rust_api{nullptr};

Value* check(lua_State* L, int index) {
    return static_cast<Value*>(luaL_checkudata(L, index, kMeta));
}
bool is_vector(lua_State* L, int index) {
    if (!lua_isuserdata(L, index) || !lua_getmetatable(L, index)) return false;
    luaL_getmetatable(L, kMeta);
    bool result = lua_rawequal(L, -1, -2) != 0;
    lua_pop(L, 2);
    return result;
}
double finite_number(lua_State* L, int index, const char* label) {
    double value = luaL_checknumber(L, index);
    if (!std::isfinite(value)) luaL_error(L, "%s must be finite", label);
    return value;
}
bool rust_apply(uint32_t op,const Value* a,const Value* b,double scalar,
                const Value* batch,size_t count,Value* out,double* scalar_out=nullptr) {
    const auto* api=rust_api.load();
    return !api || api->apply(op,a,b,scalar,batch,count,out,scalar_out)==0;
}
void require_rust(lua_State* L,bool valid) {
    if(!valid) luaL_error(L,"invalid Vector2 operation");
}
int push_raw(lua_State* L, double x, double y) {
    auto* value = static_cast<Value*>(lua_newuserdatatagged(L, sizeof(Value), 0));
    new (value) Value{x, y};
    luaL_getmetatable(L, kMeta);
    lua_setmetatable(L, -2);
    return 1;
}
int push_value(lua_State* L, double x, double y) {
    if (!std::isfinite(x) || !std::isfinite(y)) {
        luaL_error(L, "Vector2 result must be finite");
        return 0;
    }
    return push_raw(L,x,y);
}
int construct(lua_State* L) {
    if (lua_gettop(L) > 2) {
        luaL_error(L, "Vector2.new expects at most two numbers");
        return 0;
    }
    // Infinity represents an unbounded UISizeConstraint maximum. Keep the
    // existing NaN-input guard; property consumers validate allowed signs.
    // Authored serialization preserves infinity using explicit $number tags.
    double x=lua_isnone(L,1)?0:luaL_checknumber(L,1),y=lua_isnone(L,2)?0:luaL_checknumber(L,2);
    if(std::isnan(x)||std::isnan(y)){luaL_error(L,"Vector2 components must not be NaN");return 0;}
    if(rust_api.load()){Value in{x,y},out{};require_rust(L,rust_apply(22,&in,nullptr,0,nullptr,0,&out));x=out.x;y=out.y;}
    return push_raw(L,x,y);
}
int index(lua_State* L) {
    Value* value = check(L, 1);
    const char* key = luaL_checkstring(L, 2);
    if (std::strcmp(key, "X") == 0) lua_pushnumber(L, value->x);
    else if (std::strcmp(key, "Y") == 0) lua_pushnumber(L, value->y);
    else if (std::strcmp(key, "Magnitude") == 0) {
        if(rust_api.load()){Value out{};double scalar=0;require_rust(L,rust_apply(9,value,nullptr,0,nullptr,0,&out,&scalar));lua_pushnumber(L,scalar);}
        else lua_pushnumber(L,std::hypot(value->x,value->y));
    }
    else if (std::strcmp(key, "Unit") == 0) {
        if(rust_api.load()){Value out{};require_rust(L,rust_apply(8,value,nullptr,0,nullptr,0,&out));return value->x==0&&value->y==0?push_raw(L,out.x,out.y):push_value(L,out.x,out.y);}
        double magnitude = std::hypot(value->x, value->y);
        if (magnitude == 0) {
            const double nan=std::numeric_limits<double>::quiet_NaN();
            return push_raw(L,nan,nan);
        }
        return push_value(L, value->x / magnitude, value->y / magnitude);
    } else {
        luaL_getmetatable(L, kMeta);
        lua_pushvalue(L, 2);
        lua_rawget(L, -2);
        lua_remove(L, -2);
    }
    return 1;
}
int readonly(lua_State* L) {
    luaL_error(L, "Vector2 values are immutable");
    return 0;
}
int tostring_vector(lua_State* L) {
    Value* v = check(L, 1);
    char text[128];
    int size = std::snprintf(text, sizeof(text), "%.17g, %.17g", v->x, v->y);
    lua_pushlstring(L, text, static_cast<size_t>(size));
    return 1;
}
int equal(lua_State* L) {
    if (!is_vector(L, 1) || !is_vector(L, 2)) { lua_pushboolean(L, 0); return 1; }
    Value* a = check(L, 1); Value* b = check(L, 2);
    if(rust_api.load()){Value out{};double equal=0;require_rust(L,rust_apply(21,a,b,0,nullptr,0,&out,&equal));lua_pushboolean(L,equal!=0);}
    else lua_pushboolean(L, a->x == b->x && a->y == b->y);
    return 1;
}
int add(lua_State* L) { Value* a=check(L,1); Value* b=check(L,2);if(rust_api.load()){Value o{};require_rust(L,rust_apply(0,a,b,0,nullptr,0,&o));return push_value(L,o.x,o.y);}return push_value(L,a->x+b->x,a->y+b->y); }
int subtract(lua_State* L) { Value* a=check(L,1); Value* b=check(L,2);if(rust_api.load()){Value o{};require_rust(L,rust_apply(1,a,b,0,nullptr,0,&o));return push_value(L,o.x,o.y);}return push_value(L,a->x-b->x,a->y-b->y); }
int multiply(lua_State* L) {
    Value out{};
    if (is_vector(L,1) && is_vector(L,2)) { Value* a=check(L,1); Value* b=check(L,2);if(rust_api.load()){require_rust(L,rust_apply(2,a,b,0,nullptr,0,&out));return push_value(L,out.x,out.y);}return push_value(L,a->x*b->x,a->y*b->y); }
    if (is_vector(L,1)) { Value* a=check(L,1); double n=finite_number(L,2,"multiplier");if(rust_api.load()){require_rust(L,rust_apply(3,a,nullptr,n,nullptr,0,&out));return push_value(L,out.x,out.y);}return push_value(L,a->x*n,a->y*n); }
    Value* b=check(L,2); double n=finite_number(L,1,"multiplier");if(rust_api.load()){require_rust(L,rust_apply(4,b,nullptr,n,nullptr,0,&out));return push_value(L,out.x,out.y);}return push_value(L,n*b->x,n*b->y);
}
int divide(lua_State* L) {
    Value* a=check(L,1);
    Value out{};
    if (is_vector(L,2)) { Value* b=check(L,2); if(b->x==0||b->y==0){luaL_error(L,"Vector2 divisor components must be nonzero");return 0;}if(rust_api.load()){require_rust(L,rust_apply(5,a,b,0,nullptr,0,&out));return push_value(L,out.x,out.y);}return push_value(L,a->x/b->x,a->y/b->y); }
    double n=finite_number(L,2,"divisor"); if(n==0){luaL_error(L,"Vector2 divisor must be nonzero");return 0;}if(rust_api.load()){require_rust(L,rust_apply(6,a,nullptr,n,nullptr,0,&out));return push_value(L,out.x,out.y);}return push_value(L,a->x/n,a->y/n);
}
int negate(lua_State* L) { Value* a=check(L,1);if(rust_api.load()){Value o{};require_rust(L,rust_apply(7,a,nullptr,0,nullptr,0,&o));return push_value(L,o.x,o.y);}return push_value(L,-a->x,-a->y); }
int dot(lua_State* L) { Value* a=check(L,1); Value* b=check(L,2);if(rust_api.load()){Value o{};double n=0;require_rust(L,rust_apply(10,a,b,0,nullptr,0,&o,&n));lua_pushnumber(L,n);}else lua_pushnumber(L,a->x*b->x+a->y*b->y);return 1; }
int cross(lua_State* L) { Value* a=check(L,1); Value* b=check(L,2);if(rust_api.load()){Value o{};double n=0;require_rust(L,rust_apply(11,a,b,0,nullptr,0,&o,&n));lua_pushnumber(L,n);}else lua_pushnumber(L,a->x*b->y-a->y*b->x);return 1; }
int lerp(lua_State* L) { Value* a=check(L,1); Value* b=check(L,2); double t=finite_number(L,3,"alpha");if(rust_api.load()){Value o{};require_rust(L,rust_apply(12,a,b,t,nullptr,0,&o));return push_value(L,o.x,o.y);}return push_value(L,a->x+(b->x-a->x)*t,a->y+(b->y-a->y)*t); }
int fuzzy_eq(lua_State* L) {
    Value* a=check(L,1);Value* b=check(L,2);
    double epsilon=luaL_optnumber(L,3,1e-5);
    if(!std::isfinite(epsilon)||std::fabs(epsilon)>.1)
        luaL_error(L,"The eps value provided to FuzzyEq should be a small positive value <= 0.1");
    if(rust_api.load()){Value out{};double result=0;require_rust(L,rust_apply(20,a,b,epsilon,nullptr,0,&out,&result));lua_pushboolean(L,result!=0);}
    else lua_pushboolean(L,runtime_fuzzy_component(a->x,b->x,epsilon)&&runtime_fuzzy_component(a->y,b->y,epsilon));
    return 1;
}
int angle(lua_State* L) {
    Value* a=check(L,1);Value* b=check(L,2);
    bool signed_angle=false;
    if(!lua_isnoneornil(L,3)){luaL_checktype(L,3,LUA_TBOOLEAN);signed_angle=lua_toboolean(L,3);}
    if(rust_api.load()){Value out{};double result=0;require_rust(L,rust_apply(13,a,b,signed_angle?1:0,nullptr,0,&out,&result));lua_pushnumber(L,result);}
    else {const double radians=std::atan2(a->x*b->y-a->y*b->x,a->x*b->x+a->y*b->y);lua_pushnumber(L,static_cast<float>(signed_angle?radians:std::fabs(radians)));}
    return 1;
}
int component(lua_State* L, double (*operation)(double), uint32_t rust_operation) { Value* a=check(L,1);if(rust_api.load()){Value o{};require_rust(L,rust_apply(rust_operation,a,nullptr,0,nullptr,0,&o));return push_value(L,o.x,o.y);}return push_value(L,operation(a->x),operation(a->y)); }
int absolute(lua_State* L) { return component(L, std::fabs,14); }
int floor_values(lua_State* L) { return component(L, std::floor,15); }
int ceil_values(lua_State* L) { return component(L, std::ceil,16); }
double sign_value(double value) { return value>0?1.0:value<0?-1.0:0.0; }
int sign_values(lua_State* L) { return component(L,sign_value,17); }
int extrema(lua_State* L,bool maximum) {
    Value* a=check(L,1);double x=a->x,y=a->y;
    Value current=*a;
    for(int i=2;i<=lua_gettop(L);++i){Value* b=check(L,i);if(rust_api.load()){Value out{};require_rust(L,rust_apply(maximum?19:18,&current,b,0,nullptr,0,&out));current=out;}else{x=maximum?std::fmax(x,b->x):std::fmin(x,b->x);y=maximum?std::fmax(y,b->y):std::fmin(y,b->y);}}
    if(rust_api.load()){x=current.x;y=current.y;}
    return push_raw(L,x,y);
}
int min_values(lua_State* L) { return extrema(L,false); }
int max_values(lua_State* L) { return extrema(L,true); }

void method(lua_State* L, const char* name, lua_CFunction callback) {
    lua_pushcfunction(L, callback, name); lua_setfield(L, -2, name);
}
void constant(lua_State* L, const char* name, double x, double y) {
    push_value(L,x,y); lua_setfield(L,-2,name);
}
} // namespace

extern "C" int local_luau_set_rust_vector2_api(const void* raw) {
    const auto* api=static_cast<const RustVector2Api*>(raw);
    if(!api||api->version!=1||!api->apply)return 0;
    const RustVector2Api* expected=nullptr;
    return rust_api.compare_exchange_strong(expected,api)||expected==api;
}

void install_vector2_bindings(lua_State* L) {
    luaL_newmetatable(L, kMeta);
    method(L,"__index",index); method(L,"__newindex",readonly);
    method(L,"__tostring",tostring_vector); method(L,"__eq",equal);
    method(L,"__add",add); method(L,"__sub",subtract); method(L,"__mul",multiply);
    method(L,"__div",divide); method(L,"__unm",negate);
    method(L,"Dot",dot); method(L,"Cross",cross); method(L,"Lerp",lerp);
    method(L,"FuzzyEq",fuzzy_eq);method(L,"Angle",angle);
    method(L,"Abs",absolute); method(L,"Floor",floor_values); method(L,"Ceil",ceil_values);
    method(L,"Sign",sign_values); method(L,"Min",min_values); method(L,"Max",max_values);
    lua_pushliteral(L,"Vector2"); lua_setfield(L,-2,"__type");
    lua_pushliteral(L,"immutable Vector2"); lua_setfield(L,-2,"__metatable");
    lua_pop(L,1);

    lua_createtable(L,0,12);
    method(L,"new",construct);
    constant(L,"zero",0,0); constant(L,"one",1,1);
    constant(L,"xAxis",1,0); constant(L,"yAxis",0,1);
    method(L,"Min",min_values); method(L,"Max",max_values);
    lua_setreadonly(L,-1,true); lua_setglobal(L,"Vector2");
}

bool read_vector2(lua_State* L, int index, double& x, double& y) {
    if (!is_vector(L,index)) return false;
    Value* value=check(L,index); x=value->x; y=value->y; return true;
}
int push_vector2(lua_State* L, double x, double y) { return push_value(L,x,y); }

int push_engine_vector2(lua_State* L, double x, double y) { return push_raw(L,x,y); }
