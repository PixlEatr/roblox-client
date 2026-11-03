// Render callbacks start in priority order. Each may yield independently;
// CPU-sliced callbacks finish their initial invocation before priority advances.
constexpr size_t kRenderBindingLimit=256;

void retire_render_binding(lua_State* L,const std::shared_ptr<RenderStepBinding>& binding) {
    binding->active=false;
    if(binding->callback!=LUA_NOREF){lua_unref(L,binding->callback);binding->callback=LUA_NOREF;}
}
void clear_render_owner(lua_State* L,World& w,const std::string& owner) {
    auto& bindings=w.render_steps.bindings;
    for(auto it=bindings.begin();it!=bindings.end();) {
        if((*it)->owner==owner){retire_render_binding(L,*it);it=bindings.erase(it);}else ++it;
    }
}
int render_dispatch_bridge(lua_State* L) {
    auto& w=*static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    const int operation=lua_tointeger(L,lua_upvalueindex(2));auto& frame=w.render_steps;
    try {
        if(operation==1) {
            luaL_checktype(L,1,LUA_TTHREAD);
            lua_pushstring(L,w.scheduler->task_state(lua_tothread(L,1)));return 1;
        }
        if(operation==2) {
            auto service=w.services.find("RunService");
            if(service!=w.services.end())if(auto* node=find_node(w,service->second))if(!node->destroyed) {
                emit(w,*node,"RenderStepped",Json::array({frame.dt}));
                emit(w,*node,"PreRender",Json::array({frame.dt}));
            }
            frame.active=false;frame.group.clear();return 0;
        }
        for(;;) {
            while(frame.group_index<frame.group.size()) {
                auto binding=frame.group[frame.group_index];
                if(!binding->active){++frame.group_index;continue;}
                if(w.scheduler->callback_capacity()==0){lua_pushboolean(L,false);return 1;}
                ++frame.group_index;
                w.scheduler->callback_now(L,binding->callback,[&](lua_State* child){lua_pushnumber(child,frame.dt);return 1;},binding->owner);
                return 1;
            }
            // Snapshot one priority group at a time. Newly bound higher
            // priorities participate in this frame; same/lower priorities wait
            // for the next frame, as observed in Studio 0.745.
            bool found=false;int priority=0;
            for(const auto& binding:frame.bindings)if(binding->active&&(!frame.has_priority||binding->priority>frame.priority)) {
                if(!found||binding->priority<priority){priority=binding->priority;found=true;}
            }
            if(!found){lua_pushnil(L);return 1;}
            frame.priority=priority;frame.has_priority=true;frame.group.clear();frame.group_index=0;
            for(const auto& binding:frame.bindings)if(binding->active&&binding->priority==priority)frame.group.push_back(binding);
        }
    }catch(const std::exception& error){luaL_error(L,"render-step dispatch: %s",error.what());return 0;}
}
void ensure_render_dispatcher(lua_State* L,World& w) {
    if(w.render_steps.dispatcher!=LUA_NOREF)return;
    static constexpr const char* source=R"RENDER(
local nextCallback, state, finish, waitForSlice = ...
return function()
    while true do
        local child=nextCallback()
        if child==nil then break end
        if child then
            while state(child)=="ready" do waitForSlice(.001) end
        else waitForSlice(.001) end
    end
    finish()
end
)RENDER";
    const std::string code=Luau::compile(source);
    if(w.load_script(L,"=render-step-dispatcher",code,source)!=LUA_OK)
        throw std::runtime_error(lua_tostring(L,-1));
    for(int operation=0;operation<3;++operation) {
        lua_pushlightuserdata(L,&w);lua_pushinteger(L,operation);
        lua_pushcclosure(L,render_dispatch_bridge,"render-step bridge",2);
    }
    lua_getfield(L,LUA_REGISTRYINDEX,"Headless.SchedulerWait");
    if(!lua_isfunction(L,-1))throw std::runtime_error("render-step scheduler is unavailable");
    if(lua_pcall(L,4,1,0)!=LUA_OK)throw std::runtime_error(lua_tostring(L,-1));
    w.render_steps.dispatcher=lua_ref(L,-1);lua_pop(L,1);
}
int run_service_bind_render(lua_State* L) {
    auto* handle=checked_handle(L,1);auto& w=*handle->world;
    if(handle->node->class_name!="RunService"||handle->node->destroyed||!w.scheduler)
        return luaL_error(L,"BindToRenderStep requires live RunService and scheduler"),0;
    size_t length=0;const char* name=luaL_checklstring(L,2,&length);
    const double priority=luaL_checknumber(L,3);luaL_checktype(L,4,LUA_TFUNCTION);
    if(length>256)return luaL_error(L,"render binding name exceeds 256 bytes"),0;
    if(!std::isfinite(priority)||priority<double(INT32_MIN)||priority>double(INT32_MAX))
        return luaL_error(L,"render priority must fit a finite signed 32-bit integer"),0;
    try {
        if(w.render_steps.bindings.size()>=kRenderBindingLimit)throw std::runtime_error("render binding limit exceeded");
        ensure_render_dispatcher(L,w);
        auto binding=std::make_shared<RenderStepBinding>();
        binding->name.assign(name,length);binding->priority=static_cast<int>(priority);
        binding->owner=w.scheduler->owner(L);binding->sequence=w.render_steps.next_sequence++;
        binding->callback=lua_ref(L,4);w.render_steps.bindings.push_back(std::move(binding));return 0;
    }catch(const std::exception& error){luaL_error(L,"BindToRenderStep: %s",error.what());return 0;}
}
int run_service_unbind_render(lua_State* L) {
    auto* handle=checked_handle(L,1);auto& w=*handle->world;
    if(handle->node->class_name!="RunService")return luaL_error(L,"RunService receiver required"),0;
    size_t length=0;const char* value=luaL_checklstring(L,2,&length);const std::string name(value,length);
    auto& bindings=w.render_steps.bindings;
    for(auto it=bindings.begin();it!=bindings.end();) {
        if((*it)->name==name){retire_render_binding(L,*it);it=bindings.erase(it);}else ++it;
    }
    return 0;
}
void start_render_frame(lua_State* L,World& w,Node& service,double dt) {
    auto& frame=w.render_steps;
    begin_input_frame(w);
    if(frame.bindings.empty()) {
        emit(w,service,"RenderStepped",Json::array({dt}));emit(w,service,"PreRender",Json::array({dt}));return;
    }
    frame.active=true;frame.has_priority=false;frame.group.clear();frame.group_index=0;frame.dt=dt;
    frame.thread=w.scheduler->callback(L,frame.dispatcher,[](lua_State*){return 0;},{},"__engine_render_step__");
}
