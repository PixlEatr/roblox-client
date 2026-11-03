void install_filesystem_compat(lua_State* L) {
    static constexpr const char* functions[]={
        "dofile","loadfile","delfolder","delfile","isfile","isfolder",
        "makefolder","appendfile","listfiles","writefile","readfile"
    };
    for(const char* library:{"fs","filesystem"}) {
        lua_createtable(L,0,sizeof(functions)/sizeof(functions[0]));
        for(const char* name:functions) {
            lua_getglobal(L,name);
            if(!lua_isfunction(L,-1))luaL_error(L,"filesystem global %s is not installed",name);
            lua_setfield(L,-2,name);
        }
        lua_setglobal(L,library);
    }
}
