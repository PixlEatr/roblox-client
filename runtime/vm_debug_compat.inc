// Introspection of this process's pinned Luau VM, never the Roblox process.
Closure* local_lclosure(lua_State* L,int index) {
    luaL_checktype(L,index,LUA_TFUNCTION);
    if(!lua_isLfunction(L,index)){luaL_error(L,"Luau closure required");return nullptr;}
    return clvalue(luaA_toobject(L,index));
}
int vm_constants(lua_State* L) {
    auto* p=local_lclosure(L,1)->l.p;lua_createtable(L,p->sizek,0);
    for(int i=0;i<p->sizek;++i){if(ttisfunction(&p->k[i]))lua_pushnil(L);else luaA_pushvalue(L,&p->k[i]);lua_rawseti(L,-2,i+1);}return 1;
}
int vm_constant(lua_State* L) {
    auto* p=local_lclosure(L,1)->l.p;int i=luaL_checkinteger(L,2);
    if(i<1||i>p->sizek){luaL_error(L,"constant index out of range");return 0;}
    // Resolved imported functions occupy cache slots, not literal constants.
    if(ttisfunction(&p->k[i-1]))lua_pushnil(L);else luaA_pushvalue(L,&p->k[i-1]);return 1;
}
int vm_setconstant(lua_State* L) {
    auto* p=local_lclosure(L,1)->l.p;int i=luaL_checkinteger(L,2);luaL_checkany(L,3);
    if(i<1||i>p->sizek){luaL_error(L,"constant index out of range");return 0;}
    const TValue* value=luaA_toobject(L,3);TValue* slot=&p->k[i-1];
    if(ttype(value)!=ttype(slot)||(!ttisnil(value)&&!ttisboolean(value)&&!ttisnumber(value)&&!ttisstring(value))){luaL_error(L,"constant replacement must preserve its primitive type");return 0;}
    setobj(L,slot,value);luaC_barrier(L,p,value);lua_setsafeenv(L,LUA_GLOBALSINDEX,false);return 0;
}
CallInfo* local_frame(lua_State* L) {
    int level=luaL_checkinteger(L,1);
    if(level<1||level>=L->ci-L->base_ci){luaL_error(L,"stack level out of range");return nullptr;}
    auto* frame=L->ci-level;
    if(!isLfunction(frame->func)||(frame->flags&LUA_CALLINFO_NATIVE)){luaL_error(L,"interpreted Luau frame required");return nullptr;}
    return frame;
}
int vm_getstack(lua_State* L) {
    // The numeric form keeps its existing calling-frame convention. The
    // thread overload inspects a suspended coroutine in this same local VM,
    // with zero-based levels matching debug.info(thread, level, ...).
    lua_State* target=lua_isthread(L,1)?lua_tothread(L,1):L;
    const bool other=lua_isthread(L,1);
    int index_arg=other?3:2;
    int level=other?luaL_checkinteger(L,2):0;
    if(other&&(target==L||(lua_status(target)!=LUA_YIELD&&lua_status(target)!=LUA_BREAK))) {
        luaL_error(L,"suspended coroutine required");return 0;
    }
    if(other&&(level<0||level>=target->ci-target->base_ci)) {
        luaL_error(L,"stack level out of range");return 0;
    }
    auto frame_for=[&](){return other?target->ci-level:local_frame(L);};
    auto* frame=frame_for();
    if(!isLfunction(frame->func)||(frame->flags&LUA_CALLINFO_NATIVE)) {
        luaL_error(L,"interpreted Luau frame required");return 0;
    }
    int count=int(frame->top-frame->base);
    if(!lua_isnoneornil(L,index_arg)){int index=luaL_checkinteger(L,index_arg);if(index<1||index>count){luaL_error(L,"stack index out of range");return 0;}TValue value=frame->base[index-1];luaA_pushvalue(L,&value);return 1;}
    lua_createtable(L,count,0);frame=frame_for();
    for(int i=0;i<count;++i){TValue value=frame->base[i];luaA_pushvalue(L,&value);lua_rawseti(L,-2,i+1);}return 1;
}
int vm_setstack(lua_State* L) {
    auto* frame=local_frame(L);int index=luaL_checkinteger(L,2);luaL_checkany(L,3);
    if(index<1||index>frame->top-frame->base){luaL_error(L,"stack index out of range");return 0;}
    luaC_threadbarrier(L);setobj2s(L,frame->base+index-1,luaA_toobject(L,3));return 0;
}
struct VMObjects {lua_State* L;std::vector<GCObject*> objects;Proto* prototype=nullptr;bool tables=false;};
bool vm_visit(void* raw,lua_Page*,GCObject* object) {
    auto& state=*static_cast<VMObjects*>(raw);
    if(isdead(state.L->global,object))return false;
    bool wanted=state.prototype?(object->gch.tt==LUA_TFUNCTION&&!object->cl.isC&&object->cl.l.p==state.prototype):
        object->gch.tt==LUA_TFUNCTION||object->gch.tt==LUA_TUSERDATA||(state.tables&&object->gch.tt==LUA_TTABLE);
    if(wanted)state.objects.push_back(object);return false;
}
struct PauseGC {lua_State* L;size_t threshold;PauseGC(lua_State* state):L(state),threshold(L->global->GCthreshold){lua_gc(L,LUA_GCSTOP,0);}~PauseGC(){L->global->GCthreshold=threshold;}};
void push_gc_object(lua_State* L,GCObject* object) {
    TValue value;value.value.gc=object;value.tt=object->gch.tt;luaA_pushvalue(L,&value);
}
int vm_getgc(lua_State* L) {
    PauseGC pause(L);VMObjects state{L,{},nullptr,lua_toboolean(L,1)!=0};luaM_visitgco(L,&state,vm_visit);
    lua_createtable(L,state.objects.size(),0);int i=0;for(auto* object:state.objects){push_gc_object(L,object);lua_rawseti(L,-2,++i);}return 1;
}
void push_prototype(lua_State* L,Closure* parent,Proto* p,bool active) {
    PauseGC pause(L);VMObjects state{L,{},p,false};luaM_visitgco(L,&state,vm_visit);
    if(active) {
        lua_createtable(L,state.objects.size(),0);int index=0;
        for(auto* object:state.objects){push_gc_object(L,object);lua_rawseti(L,-2,++index);}
        // Closed prototypes with no captures are executable independently.
        if(state.objects.empty()&&p->nups==0){auto* closure=luaF_newLclosure(L,0,parent->env,p);TValue value;setclvalue(L,&value,closure);luaA_pushvalue(L,&value);lua_rawseti(L,-2,1);}
    } else if(!state.objects.empty())push_gc_object(L,state.objects.front());
    else if(p->nups==0){auto* closure=luaF_newLclosure(L,0,parent->env,p);TValue value;setclvalue(L,&value,closure);luaA_pushvalue(L,&value);}
    else {luaL_error(L,"prototype has captures but no instantiated closure");}
}
int vm_getproto(lua_State* L) {
    auto* f=local_lclosure(L,1);int i=luaL_checkinteger(L,2);
    if(i<1||i>f->l.p->sizep){luaL_error(L,"prototype index out of range");return 0;}
    push_prototype(L,f,f->l.p->p[i-1],lua_toboolean(L,3));return 1;
}
int vm_getprotos(lua_State* L) {
    auto* f=local_lclosure(L,1);lua_createtable(L,f->l.p->sizep,0);
    for(int i=0;i<f->l.p->sizep;++i){push_prototype(L,f,f->l.p->p[i],false);lua_rawseti(L,-2,i+1);}return 1;
}
int vm_hookfunction(lua_State* L) {
    auto* original=local_lclosure(L,1);auto* replacement=local_lclosure(L,2);
    if(original==replacement){lua_clonefunction(L,1);return 1;}
    if(replacement->nupvalues>original->nupvalues){luaL_error(L,"replacement needs more captured slots than the target closure");return 0;}
    lua_clonefunction(L,1);lua_getfenv(L,1);lua_setfenv(L,-2);
    original->l.p=replacement->l.p;original->env=replacement->env;original->stacksize=replacement->stacksize;original->preload=replacement->preload;
    luaC_objbarrier(L,original,original->l.p);luaC_objbarrier(L,original,original->env);
    for(int i=0;i<replacement->nupvalues;++i){setobj(L,&original->l.uprefs[i],&replacement->l.uprefs[i]);luaC_barrier(L,original,&original->l.uprefs[i]);}
    // nupvalues also records this GC allocation's size. Keep its capacity;
    // the replacement Proto determines which capture slots bytecode uses.
    for(int i=replacement->nupvalues;i<original->nupvalues;++i)setnilvalue(&original->l.uprefs[i]);
    lua_setsafeenv(L,LUA_GLOBALSINDEX,false);return 1;
}
int vm_ownclosure(lua_State* L) {
    luaL_checktype(L,1,LUA_TFUNCTION);lua_getfenv(L,1);lua_pushvalue(L,LUA_GLOBALSINDEX);
    lua_pushboolean(L,lua_rawequal(L,-1,-2));return 1;
}
int vm_namecall(lua_State* L){const char* name=lua_namecallatom(L,nullptr);if(name)lua_pushstring(L,name);else lua_pushnil(L);return 1;}
void install_vm_debug(lua_State* L) {
    lua_getglobal(L,"debug");
    for(auto item:{std::pair<const char*,lua_CFunction>{"getconstant",vm_constant},{"getconstants",vm_constants},{"setconstant",vm_setconstant},{"getstack",vm_getstack},{"setstack",vm_setstack},{"getproto",vm_getproto},{"getprotos",vm_getprotos}}){lua_pushcfunction(L,item.second,item.first);lua_setfield(L,-2,item.first);}lua_pop(L,1);
    for(auto item:{std::pair<const char*,lua_CFunction>{"getgc",vm_getgc},{"hookfunction",vm_hookfunction},{"replaceclosure",vm_hookfunction},{"isexecutorclosure",vm_ownclosure},{"checkclosure",vm_ownclosure},{"isourclosure",vm_ownclosure},{"getnamecallmethod",vm_namecall}}){lua_pushcfunction(L,item.second,item.first);lua_setglobal(L,item.first);}
}
