int crypt_random(lua_State* L) {
    int size=luaL_checkinteger(L,1);
    if(size<=0||size>128*1024){luaL_error(L,"random byte count must be 1..131072");return 0;}
    try {
        size_t raw_size=(static_cast<size_t>(size)*3)/4+3;
        auto result=crypto_base64(L,secure_bytes(raw_size),false);
        result.resize(static_cast<size_t>(size));
        lua_pushlstring(L,result.data(),result.size());return 1;
    }catch(const std::exception& e){luaL_error(L,"crypt.random: %s",e.what());return 0;}
}
void install_crypto_opium(lua_State* L) {
    lua_getglobal(L,"crypt");
    if(!lua_istable(L,-1)){lua_pop(L,1);lua_newtable(L);}
    bool readonly=lua_getreadonly(L,-1);
    if(readonly)lua_setreadonly(L,-1,false);
    lua_getfield(L,-1,"generatebytes");
    if(!lua_iscfunction(L,-1)||!lua_getupvalue(L,-1,1))luaL_error(L,"crypt.generatebytes must be installed before Opium crypto");
    lua_remove(L,-2);
    lua_pushcclosurek(L,crypt_random,"crypt.random",1,nullptr);lua_setfield(L,-2,"random");
    lua_getfield(L,-1,"random");lua_setglobal(L,"random");
    for(auto binding:{std::pair<const char*,lua_CFunction>{"lz4compress",lz4_compress},{"lz4decompress",lz4_decompress}}) {
        lua_pushcfunction(L,binding.second,binding.first);lua_pushvalue(L,-1);
        lua_setglobal(L,binding.first);lua_setfield(L,-2,binding.first);
    }
    if(readonly)lua_setreadonly(L,-1,true);
    lua_setglobal(L,"crypt");
}
