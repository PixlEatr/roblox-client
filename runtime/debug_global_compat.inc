#include <climits>

int debug_global_registry(lua_State* L) {
    lua_pushvalue(L,LUA_REGISTRYINDEX);
    return 1;
}

int debug_global_robloxenv(lua_State* L) {
    TValue value;
    sethvalue(L,&value,L->global->mainthread->gt);
    luaA_pushobject(L,&value);
    return 1;
}

int debug_global_setnamecall(lua_State* L) {
    size_t length=0;
    const char* method=luaL_checklstring(L,1,&length);
    L->namecall=luaS_newlstr(L,method,length);
    luaC_threadbarrier(L);
    return 0;
}

int debug_global_validlevel(lua_State* L) {
    int level=luaL_checkinteger(L,1);
    if(level<0) {
        lua_pushboolean(L,false);
        return 1;
    }
    lua_Debug info{};
    int valid=lua_getinfo(L,level,"f",&info);
    if(valid)lua_pop(L,1);
    lua_pushboolean(L,valid!=0);
    return 1;
}

int debug_global_getcallstack(lua_State* L) {
    lua_State* thread=L;
    int argument=1;
    if(lua_isthread(L,argument)) {
        thread=lua_tothread(L,argument);
        ++argument;
    } else if(!lua_isnoneornil(L,argument)&&!lua_isnumber(L,argument)) {
        luaL_argerror(L,argument,"thread or number expected");
    }

    int level=thread==L?0:1;
    if(!lua_isnoneornil(L,argument)) {
        level=luaL_checkinteger(L,argument);
        if(level<0)level=0;
    }

    lua_newtable(L);
    int output=lua_gettop(L);
    int index=1;
    lua_Debug info{};
    while(lua_getinfo(thread,level,"slnf",&info)) {
        if(thread!=L)lua_xmove(thread,L,1);
        int function=lua_gettop(L);
        lua_newtable(L);

        lua_pushvalue(L,function);
        lua_setfield(L,-2,"func");
        lua_pushstring(L,info.name?info.name:"anonymous");
        lua_setfield(L,-2,"name");
        lua_pushstring(L,info.short_src?info.short_src:"?");
        lua_setfield(L,-2,"source");
        lua_pushinteger(L,info.currentline);
        lua_setfield(L,-2,"line");

        lua_remove(L,function);
        lua_rawseti(L,output,index++);
        if(level==INT_MAX)break;
        ++level;
    }
    return 1;
}

void debug_global_bind(lua_State* L,int debug_index,const char* name,lua_CFunction function) {
    lua_pushcfunction(L,function,name);
    lua_pushvalue(L,-1);
    lua_setglobal(L,name);
    lua_setfield(L,debug_index,name);
}

void install_debug_globals(lua_State* L) {
    lua_getglobal(L,"debug");
    if(!lua_istable(L,-1)) {
        lua_pop(L,1);
        lua_newtable(L);
        lua_pushvalue(L,-1);
        lua_setglobal(L,"debug");
    }
    int debug=lua_gettop(L);
    for(auto binding:{
        std::pair<const char*,lua_CFunction>{"getinfo",compat_getinfo},
        {"getfuncinfo",compat_getinfo},
        {"getfunctioninfo",compat_getinfo},
        {"getconst",vm_constant},
        {"getconsts",vm_constants},
        {"getconstant",vm_constant},
        {"getconstants",vm_constants},
        {"setconst",vm_setconstant},
        {"setconsts",vm_setconstant},
        {"setconstant",vm_setconstant},
        {"getupval",compat_upvalue},
        {"getupvals",compat_upvalues},
        {"getupvalue",compat_upvalue},
        {"getupvalues",compat_upvalues},
        {"setupval",compat_setupvalue},
        {"setupvals",compat_setupvalue},
        {"setupvalue",compat_setupvalue},
        {"getstack",vm_getstack},
        {"setstack",vm_setstack},
        {"setcallstack",vm_setstack},
        {"getcallstack",debug_global_getcallstack},
        {"getproto",vm_getproto},
        {"getprotos",vm_getprotos},
        {"validlevel",debug_global_validlevel},
        {"isvalidlevel",debug_global_validlevel},
        {"getregistry",debug_global_registry},
        {"getreg",debug_global_registry},
        {"getgarbagecollector",vm_getgc},
        {"getgenv_access",getgenv},
        {"getrobloxenv",debug_global_robloxenv},
        {"getnamecallmethod",vm_namecall},
        {"get_namecall_method",vm_namecall},
        {"get_namecall",vm_namecall},
        {"getnamecall",vm_namecall},
        {"setnamecallmethod",debug_global_setnamecall},
        {"setnamecall",debug_global_setnamecall},
        {"set_namecall",debug_global_setnamecall},
    })debug_global_bind(L,debug,binding.first,binding.second);
    lua_pop(L,1);
}
