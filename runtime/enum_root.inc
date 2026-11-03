// Enumerate supported canonical enum objects without consulting a replaceable
// global. The runtime implements a subset of the full engine enum catalog.
int enum_root_get_enums(lua_State* L) {
    if(!lua_rawequal(L,1,lua_upvalueindex(1)))return luaL_argerror(L,1,"Enums expected"),0;
    std::vector<std::string> names;
    lua_pushnil(L);
    while(lua_next(L,lua_upvalueindex(1))) {
        if(lua_type(L,-2)==LUA_TSTRING&&lua_isuserdata(L,-1))names.emplace_back(lua_tostring(L,-2));
        lua_pop(L,1);
    }
    std::sort(names.begin(),names.end());
    lua_createtable(L,int(names.size()),0);int index=1;
    for(const auto& name:names){lua_getfield(L,lua_upvalueindex(1),name.c_str());lua_rawseti(L,-2,index++);}
    return 1;
}
void install_enum_root(lua_State* L) {
    lua_pushvalue(L,-1);lua_pushcclosure(L,enum_root_get_enums,"Enums.GetEnums",1);
    lua_setfield(L,-2,"GetEnums");
}
