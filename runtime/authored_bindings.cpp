#include "authored_bindings.hpp"
#include "world_bindings.hpp"
#include "spatial_values.hpp"
#include "physical_properties.hpp"
#include "color3_bindings.hpp"
#include "vector2_bindings.hpp"
#include "udim_bindings.hpp"
#include "range_bindings.hpp"
#include "brickcolor_bindings.hpp"
#include "datetime_bindings.hpp"
#include "sequence_bindings.hpp"
#include "closure_bindings.hpp"
#include <lualib.h>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <iterator>
#include <new>
#include <stdexcept>
#include <string>
#include <initializer_list>
#include <utility>
#include "integer_vectors.inc"
#include "ray_bindings.inc"
#include "direction_sets.inc"
#include "font_values.inc"
#include "path_waypoint.inc"

namespace {
using Json=nlohmann::json;
char constructors_key;
struct Type {const char* meta;const char* tag;const char* library;};
const Type types[]={
    {"LocalLuau.PathWaypoint","$path_waypoint","PathWaypoint"},
    {"LocalLuau.Font","$font","Font"},
    {"LocalLuau.Axes","$axes","Axes"},
    {"LocalLuau.Faces","$faces","Faces"},
    {"LocalLuau.Vector2int16","$vector2int16","Vector2int16"},
    {"LocalLuau.Vector3int16","$vector3int16","Vector3int16"},
    {"LocalLuau.Region3int16","$region3int16","Region3int16"},
    {"LocalRuntime.Ray","$ray","Ray"},
    {"LocalRuntime.Color3","$color3","Color3"},
    {"HeadlessRuntime.Vector2","$vector2","Vector2"},
    {"LocalLuau.UDim","$udim","UDim"},
    {"LocalLuau.UDim2","$udim2","UDim2"},
    {"LocalRuntime.NumberRange","$number_range","NumberRange"},
    {"LocalRuntime.Rect","$rect","Rect"},
    {"LocalRuntime.BrickColor","$brickcolor","BrickColor"},
    {"LocalLuau.DateTime","$datetime","DateTime"},
    {"HeadlessRuntime.NumberSequenceKeypoint","$number_keypoint","NumberSequenceKeypoint"},
    {"HeadlessRuntime.NumberSequence","$number_sequence","NumberSequence"},
    {"HeadlessRuntime.ColorSequenceKeypoint","$color_keypoint","ColorSequenceKeypoint"},
    {"HeadlessRuntime.ColorSequence","$color_sequence","ColorSequence"},
};
const Type* identify(lua_State* L,int index) {
    if(!lua_isuserdata(L,index)||!lua_getmetatable(L,index))return nullptr;
    for(const auto& type:types){luaL_getmetatable(L,type.meta);bool equal=lua_rawequal(L,-1,-2);lua_pop(L,1);if(equal){lua_pop(L,1);return &type;}}
    lua_pop(L,1);return nullptr;
}
Json number(double value){if(std::isnan(value))return Json{{"$number","nan"}};if(std::isinf(value))return Json{{"$number",value>0?"inf":"-inf"}};return value;}
Json field(lua_State* L,int index,const char* name) {
    lua_getfield(L,index,name);Json out;
    if(lua_isnumber(L,-1))out=number(lua_tonumber(L,-1));
    else if(!capture_authored_value(L,-1,out)){lua_pop(L,1);throw std::runtime_error("unsupported datatype field");}
    lua_pop(L,1);return out;
}
void constructor(lua_State* L,const Type& type) {
    lua_pushlightuserdata(L,&constructors_key);lua_rawget(L,LUA_REGISTRYINDEX);
    if(!lua_istable(L,-1)){lua_pop(L,1);throw std::runtime_error("source datatypes are not installed");}
    lua_getfield(L,-1,type.library);lua_remove(L,-2);
    lua_getfield(L,-1,std::string(type.tag)=="$datetime"?"fromUnixTimestampMillis":"new");lua_remove(L,-2);
}
void component(lua_State* L,const Json& value) {
    if(value.is_number()){double n=value.get<double>();if(!std::isfinite(n))throw std::runtime_error("invalid datatype component");lua_pushnumber(L,n);}
    else if(value.is_object()&&value.size()==1&&value.contains("$number")) {
        auto name=value.at("$number").get<std::string>();if(name=="nan")lua_pushnumber(L,NAN);else if(name=="inf")lua_pushnumber(L,INFINITY);else if(name=="-inf")lua_pushnumber(L,-INFINITY);else throw std::runtime_error("invalid special datatype number");
    } else if(!push_authored_value(L,value))throw std::runtime_error("invalid nested datatype");
}
}
void install_authored_bindings(lua_State* L) {
    world_bindings::install_geometry_value_enums(L);
    integer_values::install(L);
    direction_sets::install(L);
    font_values::install(L);
    path_waypoints::install(L);
    install_color3_bindings(L);install_vector2_bindings(L);install_udim_bindings(L);
    install_range_bindings(L);install_brickcolor_bindings(L);install_datetime_bindings(L);
    install_sequence_bindings(L);install_closure_bindings(L);install_ray_bindings(L);
    spatial_values::install_spatial_values(L);install_physical_properties(L);
    lua_newtable(L);
    for(const auto& type:types){lua_getglobal(L,type.library);lua_setfield(L,-2,type.library);}
    lua_pushlightuserdata(L,&constructors_key);lua_pushvalue(L,-2);lua_rawset(L,LUA_REGISTRYINDEX);lua_pop(L,1);
}
bool is_authored_value(lua_State* L,int index){return world_bindings::is_tween_info(L,index)||spatial_values::is_spatial_value(L,index)||is_physical_properties(L,index)||identify(L,index)!=nullptr;}
bool read_authored_ray(lua_State* L,int index,std::array<double,3>& origin,
                       std::array<double,3>& direction) {
    const auto* type=identify(L,index);
    if(!type||std::strcmp(type->tag,"$ray")!=0)return false;
    const auto* ray=checked_ray(L,index);
    for(int i=0;i<3;++i){origin[i]=ray->origin[i];direction[i]=ray->direction[i];}
    return true;
}
bool capture_authored_value(lua_State* L,int index,Json& value) {
    if(world_bindings::capture_tween_info(L,index,value)||spatial_values::capture_spatial_value(L,index,value)||capture_physical_properties(L,index,value))return true;
    index=lua_absindex(L,index);const auto* type=identify(L,index);if(!type)return false;
    const std::string tag=type->tag;Json data=Json::array();
    if(tag=="$path_waypoint"){value=path_waypoints::encode(*path_waypoints::read(L,index));return true;}
    if(tag=="$font"){value=font_values::encode(*font_values::read(L,index));return true;}
    else if(tag=="$axes"||tag=="$faces")data=direction_sets::read(L,index)->mask;
    else if(tag=="$ray") {
        const auto* ray=checked_ray(L,index);
        for(const float* vector:{ray->origin,ray->direction}) {
            Json components=Json::array();for(int i=0;i<3;++i)components.push_back(number(vector[i]));
            data.push_back(Json{{"$vector3",std::move(components)}});
        }
    }
    else if(tag=="$color3")for(auto key:{"R","G","B"})data.push_back(field(L,index,key));
    else if(tag=="$vector2"||tag=="$vector2int16")for(auto key:{"X","Y"})data.push_back(field(L,index,key));
    else if(tag=="$vector3int16")for(auto key:{"X","Y","Z"})data.push_back(field(L,index,key));
    else if(tag=="$region3int16")for(auto key:{"Min","Max"})data.push_back(field(L,index,key));
    else if(tag=="$udim")for(auto key:{"Scale","Offset"})data.push_back(field(L,index,key));
    else if(tag=="$udim2")for(auto key:{"X","Y"})data.push_back(field(L,index,key));
    else if(tag=="$number_range"||tag=="$rect")for(auto key:{"Min","Max"})data.push_back(field(L,index,key));
    else if(tag=="$brickcolor")data=field(L,index,"Number");
    else if(tag=="$datetime")data=field(L,index,"UnixTimestampMillis");
    else if(tag=="$number_keypoint")for(auto key:{"Time","Value","Envelope"})data.push_back(field(L,index,key));
    else if(tag=="$color_keypoint")for(auto key:{"Time","Value"})data.push_back(field(L,index,key));
    else {
        lua_getfield(L,index,"Keypoints");const int table=lua_gettop(L);int count=lua_objlen(L,table);
        if(count>1024){lua_pop(L,1);throw std::runtime_error("sequence result exceeds limit");}
        for(int i=1;i<=count;i++){lua_rawgeti(L,table,i);Json item;if(!capture_authored_value(L,-1,item)){lua_pop(L,2);throw std::runtime_error("invalid sequence keypoint");}lua_pop(L,1);data.push_back(std::move(item));}
        lua_pop(L,1);
    }
    value=Json{{tag,std::move(data)}};return true;
}
bool push_authored_value(lua_State* L,const Json& value) {
    if(world_bindings::push_tween_info_json(L,value)||spatial_values::push_spatial_value(L,value)||push_physical_properties_json(L,value))return true;
    if(!value.is_object()||value.size()!=1)return false;
    const Type* type=nullptr;for(const auto& candidate:types)if(value.contains(candidate.tag)){type=&candidate;break;}
    if(!type)return false;
    const auto& data=value.at(type->tag);const std::string tag=type->tag;
    if(tag=="$path_waypoint"){path_waypoints::decode(L,data);return true;}
    if(tag=="$font"){font_values::push(L,font_values::decode(data));return true;}
    if(tag=="$axes"||tag=="$faces") {
        const int maximum=tag=="$axes"?7:63;
        if(!data.is_number_integer()||data<0||data>maximum)throw std::runtime_error("invalid direction-set mask");
        direction_sets::push(L,data.get<unsigned>(),tag=="$axes");return true;
    }
    if(tag=="$color3"&&data.is_array()&&data.size()==3){push_color3(L,data[0].get<double>(),data[1].get<double>(),data[2].get<double>());return true;}
    constructor(L,*type);int count=0;
    if(tag=="$ray") {
        if(!data.is_array()||data.size()!=2)throw std::runtime_error("invalid Ray component count");
        for(const auto& vector:data) {
            if(!vector.is_object()||vector.size()!=1||!vector.contains("$vector3")||!vector["$vector3"].is_array()||vector["$vector3"].size()!=3)
                throw std::runtime_error("invalid Ray vector");
            float components[3];
            for(int i=0;i<3;++i) {
                const auto& value=vector["$vector3"][i];
                if(!value.is_number()&&!(value.is_object()&&value.size()==1&&value.contains("$number")))throw std::runtime_error("invalid Ray scalar");
                component(L,value);components[i]=float(lua_tonumber(L,-1));lua_pop(L,1);
            }
            lua_pushvector(L,components[0],components[1],components[2]);
        }
        count=2;
    } else if(tag=="$number_sequence"||tag=="$color_sequence") {
        if(!data.is_array()||data.size()>1024)throw std::runtime_error("invalid sequence value");
        lua_createtable(L,static_cast<int>(data.size()),0);
        for(size_t i=0;i<data.size();i++){component(L,data[i]);lua_rawseti(L,-2,static_cast<int>(i+1));}count=1;
    } else if(tag=="$brickcolor"||tag=="$datetime") {component(L,data);count=1;}
    else {
        size_t expected=tag=="$color3"||tag=="$number_keypoint"||tag=="$vector3int16"?3:2;
        if(!data.is_array()||data.size()!=expected)throw std::runtime_error("invalid datatype component count");
        for(const auto& item:data){component(L,item);++count;}
    }
    // Constructors are cached native closures, not user-overridable globals.
    if(lua_pcall(L,count,1,0)!=LUA_OK){lua_pop(L,1);throw std::runtime_error("unsupported or invalid datatype value");}
    return true;
}

nlohmann::json authored_font_from_enum(int value) {
    if(value<0||value>=int(std::size(font_values::legacy)))throw std::runtime_error("Font enum has no constructible family");
    const auto& font=font_values::legacy[value];
    return font_values::encode({font_values::family_name(font.family),font.weight,font.style});
}
int authored_font_to_enum(const nlohmann::json& value) {
    if(!value.is_object()||value.size()!=1||!value.contains("$font"))throw std::runtime_error("Font value required");
    const auto font=font_values::decode(value["$font"]);
    // Prefer current Arimo aliases to the removed Arial enum names.
    for(int i=int(std::size(font_values::legacy))-1;i>=0;--i) {
        const auto& entry=font_values::legacy[i];
        if(font.family==font_values::family_name(entry.family)&&font.weight==entry.weight&&font.style==entry.style)return i;
    }
    return 100;
}
