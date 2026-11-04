// Introspection is confined to this VM. No remote process data or score fixtures.
void hash_u32(std::string& out,uint32_t value) {
    for(int i=0;i<4;++i)out+=char(value>>(i*8));
}
void hash_prototype(std::string& out,const Proto* p,int depth=0) {
    if(depth>128||out.size()>64*1024*1024)throw std::runtime_error("function hash exceeds prototype traversal limit");
    // Constants precede instructions; source paths and debug names do not affect
    // behavior. Tag and length delimiters avoid ambiguous constant sequences.
    out+=char(p->numparams);out+=char(p->is_vararg);out+=char(p->nups);
    hash_u32(out,p->sizek);
    std::vector<bool> imports(p->sizek,false);
    for(int pc=0;pc<p->sizecode;) {
        auto op=LuauOpcode(LUAU_INSN_OP(p->code[pc]));
        if(op==LOP_GETIMPORT) {
            int index=LUAU_INSN_D(p->code[pc]);
            if(index>=0&&index<p->sizek)imports[index]=true;
        }
        pc+=Luau::getOpLength(op);
    }
    for(int i=0;i<p->sizek;++i) {
        const auto& value=p->k[i];
        if(imports[i]){out+='I';continue;}
        out+=char(ttype(&value));
        if(ttisstring(&value)) {
            auto* s=tsvalue(&value);hash_u32(out,s->len);out.append(getstr(s),s->len);
        }else if(ttisnumber(&value)) {
            uint64_t bits;double n=nvalue(&value);std::memcpy(&bits,&n,sizeof(bits));
            for(int j=0;j<8;++j)out+=char(bits>>(j*8));
        }else if(ttisboolean(&value))out+=char(bvalue(&value));
        else if(ttisvector(&value)) {
            for(int j=0;j<LUA_VECTOR_SIZE;++j) {
                uint32_t bits;float n=vvalue(&value)[j];std::memcpy(&bits,&n,sizeof(bits));hash_u32(out,bits);
            }
        }else if(ttistable(&value)) {
            // DUPTABLE templates contain keys that are not necessarily present
            // as separate string constants. Sort keys to avoid hash-slot order.
            auto* table=hvalue(&value);std::vector<std::string> keys;
            for(int j=0;j<sizenode(table);++j)if(ttisstring(gkey(&table->node[j]))) {
                auto* key=tsvalue(gkey(&table->node[j]));keys.emplace_back(getstr(key),key->len);
            }
            std::sort(keys.begin(),keys.end());hash_u32(out,uint32_t(keys.size()));
            for(const auto& key:keys){hash_u32(out,uint32_t(key.size()));out+=key;}
        }else if(isLfunction(&value))hash_prototype(out,clvalue(&value)->l.p,depth+1);
        // Template/import objects carry no process-specific pointer in a hash.
    }
    hash_u32(out,p->sizecode);
    for(int pc=0;pc<p->sizecode;) {
        Instruction word=p->code[pc];auto op=LuauOpcode(LUAU_INSN_OP(word));
        if(op==LOP_BREAK&&p->debuginsn){op=LuauOpcode(p->debuginsn[pc]);word=(word&~255u)|op;}
        // These instructions rewrite C as a hash-slot hint during execution.
        if(op==LOP_GETGLOBAL||op==LOP_SETGLOBAL||op==LOP_GETTABLEKS||op==LOP_SETTABLEKS||op==LOP_NAMECALL)
            word&=0x00ffffffu;
        hash_u32(out,word);
        const int length=Luau::getOpLength(op);
        for(int j=1;j<length;++j)hash_u32(out,p->code[pc+j]);
        pc+=length;
    }
    hash_u32(out,p->sizep);for(int i=0;i<p->sizep;++i)hash_prototype(out,p->p[i],depth+1);
}
std::string function_hash(const Proto* p) {
    std::string input;hash_prototype(input,p);
    void* library=crypto_library();
    if(!library)throw std::runtime_error("OpenSSL 3 is required for SHA-384");
    using Digest=int(*)(void*,const char*,const char*,const void*,size_t,unsigned char*,size_t*);
    auto digest=reinterpret_cast<Digest>(dlsym(library,"EVP_Q_digest"));
    unsigned char bytes[64];size_t length=0;
    if(!digest||digest(nullptr,"sha384",nullptr,input.data(),input.size(),bytes,&length)!=1||length!=48)
        throw std::runtime_error("SHA-384 operation failed");
    static constexpr char hex[]="0123456789abcdef";std::string result;result.reserve(96);
    for(size_t i=0;i<length;++i){result+=hex[bytes[i]>>4];result+=hex[bytes[i]&15];}
    return result;
}
int vm_functionhash(lua_State* L) {
    auto* closure=local_lclosure(L,1);
    try {auto value=function_hash(closure->l.p);lua_pushlstring(L,value.data(),value.size());return 1;}
    catch(const std::exception& e){return luaL_error(L,"getfunctionhash: %s",e.what()),0;}
}
bool filter_equal(lua_State* L,int left,int right) {
    if(lua_type(L,left)==LUA_TNUMBER&&lua_type(L,right)==LUA_TNUMBER) {
        double a=lua_tonumber(L,left),b=lua_tonumber(L,right);
        // Reflection compares numeric representations: NaN is searchable,
        // and the sign of a zero constant/upvalue remains observable.
        if(std::isnan(a)||std::isnan(b))return std::isnan(a)&&std::isnan(b);
        if(a==0&&b==0)return std::signbit(a)==std::signbit(b);
    }
    return lua_rawequal(L,left,right);
}
bool filter_member(lua_State* L,int wanted,Closure* closure,bool constants) {
    if(closure->isC)return false;
    const int count=constants?closure->l.p->sizek:closure->l.p->nups;
    for(int i=0;i<count;++i) {
        const TValue* value=constants?&closure->l.p->k[i]:&closure->l.uprefs[i];
        if(!constants&&ttisupval(value))value=upvalue(value)->v;
        luaA_pushvalue(L,value);const bool match=filter_equal(L,wanted,-1);lua_pop(L,1);
        if(match)return true;
    }
    return false;
}
bool filter_function(lua_State* L,int candidate,int options) {
    auto* closure=clvalue(luaA_toobject(L,candidate));
    lua_rawgetfield(L,options,"IgnoreExecutor");
    bool ignore=lua_isnil(L,-1)||lua_toboolean(L,-1);lua_pop(L,1);
    if(ignore&&is_executor_closure(L,candidate))return false;
    lua_rawgetfield(L,options,"Name");
    if(!lua_isnil(L,-1)) {
        auto* name=closure->isC?closure->c.debugname:closure->l.p->debugname;
        size_t length;const char* requested=lua_tolstring(L,-1,&length);
        bool equal=name&&length==name->len&&std::memcmp(requested,getstr(name),length)==0;
        lua_pop(L,1);if(!equal)return false;
    }else lua_pop(L,1);
    for(auto field:{"Constants","Upvalues"}) {
        lua_rawgetfield(L,options,field);
        if(lua_istable(L,-1)) {
            if(closure->isC){lua_pop(L,1);return false;}
            lua_pushnil(L);
            while(lua_next(L,-2)) {
                if(!filter_member(L,lua_gettop(L),closure,field[0]=='C')){lua_pop(L,3);return false;}
                lua_pop(L,1);
            }
        }
        lua_pop(L,1);
    }
    lua_rawgetfield(L,options,"Hash");
    if(!lua_isnil(L,-1)) {
        if(closure->isC){lua_pop(L,1);return false;}
        size_t length;const char* text=lua_tolstring(L,-1,&length);
        bool equal=function_hash(closure->l.p)==std::string(text,length);lua_pop(L,1);
        if(!equal)return false;
    }else lua_pop(L,1);
    return true;
}
bool filter_table(lua_State* L,int candidate,int options) {
    lua_rawgetfield(L,options,"Metatable");
    if(!lua_isnil(L,-1)) {
        if(!lua_getmetatable(L,candidate)){lua_pop(L,1);return false;}
        bool equal=lua_rawequal(L,-1,-2);lua_pop(L,2);if(!equal)return false;
    }else lua_pop(L,1);
    for(auto field:{"Keys","Values","KeyValuePairs"}) {
        lua_rawgetfield(L,options,field);
        if(lua_istable(L,-1)) {
            lua_pushnil(L);
            while(lua_next(L,-2)) {
                bool matched=false;
                if(std::strcmp(field,"Values")==0) {
                    int wanted=lua_gettop(L);lua_pushnil(L);
                    while(lua_next(L,candidate)) {
                        matched=filter_equal(L,wanted,-1);lua_pop(L,1);
                        if(matched){lua_pop(L,1);break;}
                    }
                }else {
                    lua_pushvalue(L,std::strcmp(field,"Keys")==0?-1:-2);lua_rawget(L,candidate);
                    matched=std::strcmp(field,"Keys")==0?!lua_isnil(L,-1):filter_equal(L,-1,-2);
                    lua_pop(L,1);
                }
                if(!matched){lua_pop(L,3);return false;}
                lua_pop(L,1);
            }
        }
        lua_pop(L,1);
    }
    return true;
}
int vm_filtergc(lua_State* L) {
    const char* kind=luaL_checkstring(L,1);luaL_checktype(L,2,LUA_TTABLE);
    const bool functions=std::strcmp(kind,"function")==0;
    if(!functions&&std::strcmp(kind,"table")!=0)return luaL_error(L,"filter type must be function or table"),0;
    if(!lua_isnoneornil(L,3))luaL_checktype(L,3,LUA_TBOOLEAN);
    const bool one=lua_toboolean(L,3);
    for(auto item:{std::pair<const char*,int>{"Name",LUA_TSTRING},{"Hash",LUA_TSTRING},{"IgnoreExecutor",LUA_TBOOLEAN},
                 {"Constants",LUA_TTABLE},{"Upvalues",LUA_TTABLE},{"Keys",LUA_TTABLE},{"Values",LUA_TTABLE},
                 {"KeyValuePairs",LUA_TTABLE},{"Metatable",LUA_TTABLE}}) {
        lua_rawgetfield(L,2,item.first);
        if(!lua_isnil(L,-1)&&lua_type(L,-1)!=item.second)return luaL_error(L,"invalid %s filter",item.first),0;
        lua_pop(L,1);
    }
    try {
        PauseGC pause(L);VMObjects state{L,{},nullptr,!functions};luaM_visitgco(L,&state,vm_visit);
        lua_newtable(L);int result=lua_gettop(L),count=0;
        for(auto* object:state.objects) {
            if(object->gch.tt!=(functions?LUA_TFUNCTION:LUA_TTABLE))continue;
            push_gc_object(L,object);const int candidate=lua_gettop(L);
            const bool match=functions?filter_function(L,candidate,2):filter_table(L,candidate,2);
            if(match){if(one)return 1;lua_rawseti(L,result,++count);}else lua_pop(L,1);
        }
        if(one)lua_pushnil(L);return 1;
    }catch(const std::exception& e){return luaL_error(L,"filtergc: %s",e.what()),0;}
}
