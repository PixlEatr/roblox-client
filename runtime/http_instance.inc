int instance_http_get_continue(lua_State* L,int status) {
    if(status!=LUA_OK){lua_error(L);return 0;}
    luaL_checktype(L,1,LUA_TTABLE);lua_getfield(L,1,"Success");bool success=lua_toboolean(L,-1);lua_pop(L,1);
    if(!success){lua_getfield(L,1,"StatusCode");int code=lua_tointeger(L,-1);luaL_error(L,"HttpGet failed with HTTP status %d",code);return 0;}
    lua_getfield(L,1,"Body");luaL_checktype(L,-1,LUA_TSTRING);return 1;
}
int instance_http_get(lua_State* L) {
    auto* h=checked_handle(L,1);if(h->node->class_name!="DataModel"){luaL_error(L,"HttpGet requires the DataModel");return 0;}
    luaL_checktype(L,2,LUA_TSTRING);if(!lua_isnoneornil(L,3))luaL_checktype(L,3,LUA_TBOOLEAN);
    lua_newtable(L);lua_pushvalue(L,2);lua_setfield(L,-2,"Url");lua_pushliteral(L,"GET");lua_setfield(L,-2,"Method");
    lua_replace(L,1);lua_settop(L,1);lua_getglobal(L,"request");
    if(!lua_isfunction(L,-1)){luaL_error(L,"HttpGet requires source networking support");return 0;}
    lua_insert(L,1);return lua_callyieldable(L,1,1);
}
