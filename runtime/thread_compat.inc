int thread_compat_current(lua_State* L) {
    lua_pushthread(L);
    return 1;
}

int thread_compat_all(lua_State* L) {
    lua_newtable(L);
    int result=lua_gettop(L),index=0;
    lua_pushnil(L);
    while(lua_next(L,LUA_REGISTRYINDEX)) {
        if(lua_isthread(L,-1)&&lua_tothread(L,-1)!=L) {
            lua_pushvalue(L,-1);
            lua_rawseti(L,result,++index);
        }
        lua_pop(L,1);
    }
    return 1;
}

int thread_compat_environment(lua_State* L) {
    luaL_checktype(L,1,LUA_TTHREAD);
    lua_getfenv(L,1);
    return 1;
}

int thread_compat_script_from(lua_State* L,int index) {
    lua_getfenv(L,index);
    lua_pushliteral(L,"script");
    lua_rawget(L,-2);
    lua_remove(L,-2);
    return 1;
}

int thread_compat_script_thread(lua_State* L) {
    luaL_checktype(L,1,LUA_TTHREAD);
    return thread_compat_script_from(L,1);
}

int thread_compat_calling_script(lua_State* L) {
    lua_pushthread(L);
    int result=thread_compat_script_from(L,-1);
    lua_remove(L,-2);
    return result;
}

int thread_compat_run(lua_State* L) {
    luaL_checktype(L,1,LUA_TTHREAD);
    lua_State* target=lua_tothread(L,1);
    int target_status=lua_status(target);
    if(target_status!=LUA_OK&&target_status!=LUA_YIELD) {
        luaL_error(L,"target thread is not in a runnable state");
        return 0;
    }
    size_t length=0;
    const char* source=luaL_checklstring(L,2,&length);
    if(length>local_test_source_limit(100*1024*1024)) {
        luaL_error(L,"run_on_thread source exceeds configured input limit");
        return 0;
    }
    int arguments=lua_gettop(L)-2;
    std::string bytecode;
    try {bytecode=Luau::compile(std::string(source,length));}
    catch(const std::exception& error) {luaL_error(L,"run_on_thread compilation failed: %s",error.what());return 0;}

    lua_State* child=lua_newthread(L);
    int child_index=lua_gettop(L);
    lua_getfenv(L,1);
    lua_xmove(L,child,1);
    lua_pushthread(child);
    lua_insert(child,-2);
    lua_setfenv(child,-2);
    lua_pop(child,1);

    if(luau_load(child,"=run_on_thread",bytecode.data(),bytecode.size(),0)!=0) {
        size_t size=0;
        const char* message=lua_tolstring(child,-1,&size);
        luaL_error(L,"run_on_thread failed: %s",message?std::string(message,size).c_str():"source compilation failed");
        return 0;
    }

    lua_getglobal(L,"task");
    if(!lua_istable(L,-1)) {luaL_error(L,"run_on_thread requires the source scheduler");return 0;}
    lua_getfield(L,-1,"defer");
    if(!lua_isfunction(L,-1)) {luaL_error(L,"run_on_thread requires the source scheduler");return 0;}
    lua_pushvalue(L,child_index);
    for(int i=0;i<arguments;++i)lua_pushvalue(L,i+3);
    lua_call(L,arguments+1,0);
    lua_settop(L,child_index);
    return 1;
}

void install_thread_compat(lua_State* L) {
    for(auto binding:{std::pair<const char*,lua_CFunction>{
        {"getcurrentthread",thread_compat_current},
        {"getallthreads",thread_compat_all},{"getthreads",thread_compat_all},
        {"gettenv",thread_compat_environment},{"getthreadenv",thread_compat_environment},
        {"run_on_thread",thread_compat_run},{"runonthread",thread_compat_run},
        {"getscriptfromthread",thread_compat_script_thread},
        {"getcallingscript",thread_compat_calling_script},{"getcaller",thread_compat_calling_script}
    }}) {
        lua_pushcfunction(L,binding.second,binding.first);
        lua_setglobal(L,binding.first);
    }
}
