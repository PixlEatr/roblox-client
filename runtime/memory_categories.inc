// Named tags backed by Luau's actual per-thread allocation categories.
// Registry storage belongs to this VM; no process-global category state.
namespace {
char memory_category_registry_key;
int memory_category_set(lua_State* L) {
    size_t length=0;luaL_checklstring(L,1,&length);
    if(length>256)return luaL_error(L,"memory category name exceeds 256 bytes"),0;
    lua_pushlightuserdata(L,&memory_category_registry_key);lua_rawget(L,LUA_REGISTRYINDEX);
    if(lua_isnil(L,-1)) {
        lua_pop(L,1);lua_newtable(L);
        lua_pushlightuserdata(L,&memory_category_registry_key);lua_pushvalue(L,-2);lua_rawset(L,LUA_REGISTRYINDEX);
    }
    if(!lua_istable(L,-1))return luaL_error(L,"invalid VM memory category registry"),0;
    const int categories=lua_gettop(L);
    lua_pushvalue(L,1);lua_rawget(L,categories);
    int category=lua_isnumber(L,-1)?lua_tointeger(L,-1):0;lua_pop(L,1);
    if(!category) {
        category=lua_objlen(L,categories)+1;
        if(category>=LUA_MEMORY_CATEGORIES)return luaL_error(L,"local VM memory category limit exceeded"),0;
        lua_pushvalue(L,1);lua_pushinteger(L,category);lua_rawset(L,categories);
        lua_pushvalue(L,1);lua_rawseti(L,categories,category);
    }
    if(category<1||category>=LUA_MEMORY_CATEGORIES)return luaL_error(L,"invalid VM memory category"),0;
    lua_setmemcat(L,category);return 0;
}
int memory_category_reset(lua_State* L){lua_setmemcat(L,0);return 0;}
void install_memory_category_bindings(lua_State* L) {
    lua_pushcfunction(L,memory_category_set,"debug.setmemorycategory");lua_setfield(L,-2,"setmemorycategory");
    lua_pushcfunction(L,memory_category_reset,"debug.resetmemorycategory");lua_setfield(L,-2,"resetmemorycategory");
}
}
