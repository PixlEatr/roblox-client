// Included after the shared BindableEvent/BindableFunction value copier.
void release_bindable_call(lua_State* L,World& w,BindableInvocation& call) {
    if(call.callee)w.scheduler->release_invocation(call.callee);
    for(int ref:{call.caller_ref,call.arguments_ref,call.callback_ref})if(ref!=LUA_NOREF)lua_unref(L,ref);
}
int invoke_bindable_continue(lua_State* L,int) {
    if(lua_type(L,1)!=LUA_TBOOLEAN){luaL_error(L,"BindableFunction resumed without a callback result");return 0;}
    bool success=lua_toboolean(L,1);lua_remove(L,1);
    if(!success){if(lua_gettop(L)==0)lua_pushliteral(L,"BindableFunction callback failed");lua_error(L);return 0;}
    return lua_gettop(L);
}
int invoke_bindable(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;
    if(h->node->class_name!="BindableFunction"||h->node->destroyed){luaL_error(L,"Invoke requires a live BindableFunction");return 0;}
    if(!w.scheduler||!lua_isyieldable(L)){luaL_error(L,"Invoke requires a scheduled yieldable thread");return 0;}
    int count=lua_gettop(L)-1;
    if(count>256||w.bindable_invocations.size()>=512){luaL_error(L,"BindableFunction invocation limit exceeded");return 0;}
    BindableInvocation call;call.caller=L;call.node=h->node->id;call.count=count;
    try {
        lua_createtable(L,count,0);size_t budget=10000;std::unordered_set<const void*> path;
        for(int i=0;i<count;i++){copy_bindable_value(L,i+2,path,budget);lua_rawseti(L,-2,i+1);}
        call.arguments_ref=lua_ref(L,-1);lua_pop(L,1);
        lua_pushthread(L);call.caller_ref=lua_ref(L,-1);lua_pop(L,1);
        auto bound=w.bindable_callbacks.find(call.node);
        if(bound!=w.bindable_callbacks.end()){
            lua_getref(L,bound->second.reference);call.callback_ref=lua_ref(L,-1);lua_pop(L,1);call.owner=bound->second.owner;
        }
        w.scheduler->wait_signal(L);w.bindable_invocations.push_back(call);
    }catch(const std::exception& e){release_bindable_call(L,w,call);luaL_error(L,"Invoke failed: %s",e.what());return 0;}
    lua_settop(L,0);return lua_yield(L,0);
}
void set_bindable_callback(lua_State* L,World& w,Node& node) {
    if(node.destroyed)throw std::runtime_error("callback receiver is destroyed");
    if(!lua_isfunction(L,3)&&!lua_isnil(L,3))throw std::runtime_error("callback requires a function or nil");
    auto previous=w.bindable_callbacks.find(node.id);
    int reference=LUA_NOREF;
    if(!lua_isnil(L,3)){lua_pushvalue(L,3);reference=lua_ref(L,-1);lua_pop(L,1);}
    if(previous!=w.bindable_callbacks.end()){lua_unref(L,previous->second.reference);w.bindable_callbacks.erase(previous);}
    if(reference!=LUA_NOREF)w.bindable_callbacks.emplace(node.id,BindableCallback{reference,w.scheduler?w.scheduler->owner(L):""});
}
void clear_owned_bindables(lua_State* L,World& w,const std::string& owner) {
    for(auto& call:w.remote_client_invocations)if(call.owner==owner&&(call.callee||call.callback_ref!=LUA_NOREF))call.cancelled=true;
    for(auto& call:w.bindable_invocations)if(call.owner==owner&&call.callback_ref!=LUA_NOREF)call.cancelled=true;
    for(auto it=w.bindable_callbacks.begin();it!=w.bindable_callbacks.end();){
        if(it->second.owner==owner){lua_unref(L,it->second.reference);it=w.bindable_callbacks.erase(it);}else ++it;
    }
}
void dispatch_bindable_functions(lua_State* from,World& w,bool cleanup_callbacks=true) {
    for(auto it=w.bindable_callbacks.begin();cleanup_callbacks&&it!=w.bindable_callbacks.end();){
        auto* node=find_node(w,it->first);
        if(!node||node->destroyed){lua_unref(from,it->second.reference);it=w.bindable_callbacks.erase(it);}else ++it;
    }
    for(auto it=w.bindable_invocations.begin();it!=w.bindable_invocations.end();) {
        auto& call=*it;
        if(w.scheduler->completion(call.caller)!=LUA_YIELD){release_bindable_call(from,w,call);it=w.bindable_invocations.erase(it);continue;}
        auto* node=find_node(w,call.node);std::string error;
        if(call.cancelled)error="BindableFunction callback owner was stopped";
        else if(!node||node->destroyed)error="BindableFunction was destroyed while Invoke was waiting";
        if(error.empty()&&!call.callee) {
            if(call.callback_ref==LUA_NOREF){
                auto bound=w.bindable_callbacks.find(call.node);
                if(bound==w.bindable_callbacks.end()){++it;continue;}
                lua_getref(from,bound->second.reference);call.callback_ref=lua_ref(from,-1);lua_pop(from,1);call.owner=bound->second.owner;
            }
            if(w.scheduler->invocation_capacity()==0){++it;continue;}
            try {
                call.callee=w.scheduler->invoke_callback(from,call.callback_ref,[&](lua_State* target){
                    lua_getref(target,call.arguments_ref);int table=lua_gettop(target);
                    for(int i=0;i<call.count;i++)lua_rawgeti(target,table,i+1);
                    lua_remove(target,table);return call.count;
                },call.owner);
                lua_unref(from,call.arguments_ref);call.arguments_ref=LUA_NOREF;
                lua_unref(from,call.callback_ref);call.callback_ref=LUA_NOREF;
            }catch(const std::exception& e){error=e.what();}
        }
        int status=call.callee?w.scheduler->completion(call.callee):LUA_YIELD;
        if(error.empty()&&status==LUA_YIELD){++it;continue;}
        int results=LUA_NOREF,count=0;
        if(error.empty()&&status==0) {
            count=lua_gettop(call.callee);int base=count;
            try {
                if(count>256)throw std::runtime_error("BindableFunction return count limit exceeded");
                lua_createtable(call.callee,count,0);size_t budget=10000;std::unordered_set<const void*> path;
                for(int i=0;i<count;i++){copy_bindable_value(call.callee,i+1,path,budget);lua_rawseti(call.callee,-2,i+1);}
                results=lua_ref(call.callee,-1);
            }catch(const std::exception& e){error=e.what();}
            lua_settop(call.callee,base);
        } else if(error.empty()) {
            if(status==LUA_BREAK)error="BindableFunction callback exceeded its CPU budget";
            else if(lua_type(call.callee,-1)==LUA_TSTRING){size_t size=0;const char* text=lua_tolstring(call.callee,-1,&size);error.assign(text,std::min(size,size_t(4096)));}
            else error="BindableFunction callback failed or was cancelled";
        }
        w.scheduler->wake_signal(call.caller,[&](lua_State* target){
            if(!error.empty()){lua_pushboolean(target,false);lua_pushlstring(target,error.data(),error.size());return 2;}
            lua_pushboolean(target,true);lua_getref(target,results);int table=lua_gettop(target);
            for(int i=0;i<count;i++)lua_rawgeti(target,table,i+1);
            lua_remove(target,table);return count+1;
        });
        if(results!=LUA_NOREF)lua_unref(from,results);
        release_bindable_call(from,w,call);it=w.bindable_invocations.erase(it);
    }
}
