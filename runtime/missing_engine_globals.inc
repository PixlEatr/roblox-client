// Documented singleton globals whose truth does not depend on an installed
// Roblox client: settings() and PluginManager(). version() is supplied by the
// authored binding as this runtime's package version, never as a Roblox build.
int make_global_settings(lua_State* L) {
    if(lua_gettop(L)!=0){luaL_error(L,"settings takes no arguments");return 0;}
    Runtime* ctx=runtime(L);
    push_service(L,&ctx->global_settings);
    return 1;
}
int make_plugin_manager(lua_State* L) {
    if(lua_gettop(L)!=0){luaL_error(L,"PluginManager takes no arguments");return 0;}
    Runtime* ctx=runtime(L);
    push_service(L,&ctx->plugin_manager);
    return 1;
}
int global_settings_get_flag(lua_State* L) {
    if(!handle(L,1)||handle(L,1)->value->class_name!="GlobalSettings")
        return luaL_error(L,"GlobalSettings:GetFFlag requires GlobalSettings"),0;
    luaL_checkstring(L,2);
    return luaL_error(L,"engine flag values are unavailable in this local runtime"),0;
}
int global_settings_get_variable(lua_State* L) {
    if(!handle(L,1)||handle(L,1)->value->class_name!="GlobalSettings")
        return luaL_error(L,"GlobalSettings:GetFVariable requires GlobalSettings"),0;
    luaL_checkstring(L,2);
    return luaL_error(L,"engine variable values are unavailable in this local runtime"),0;
}
int plugin_manager_security_method(lua_State* L) {
    if(!handle(L,1)||handle(L,1)->value->class_name!="PluginManager")
        return luaL_error(L,"PluginManager method requires PluginManager"),0;
    return luaL_error(L,"PluginSecurity is unavailable in this local runtime"),0;
}
