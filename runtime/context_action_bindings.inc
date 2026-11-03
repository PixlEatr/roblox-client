// Context action dispatch uses the same owner-aware scheduler as ordinary
// LocalScripts. A user yield sinks this event; a CPU slice waits for its result.
constexpr size_t kContextActionLimit=256,kContextInputLimit=2048,kContextTargetLimit=16384;
bool context_before(const std::shared_ptr<ContextActionBinding>& a,const std::shared_ptr<ContextActionBinding>& b) {
    return a->priority!=b->priority?a->priority>b->priority:a->sequence>b->sequence;
}
int context_key_value(const char* name) {
    return kUsiExportEnums[input_enum_kind("KeyCode")].items[input_enum_item("KeyCode",name)].value;
}
bool context_matches(const ContextActionBinding& binding,int type,int key) {
    for(const auto& h:binding.inputs) {
        if(h.kind=="KeyCode"&&key!=0&&h.value==key)return true;
        if(h.kind=="UserInputType"&&h.value==type)return true;
        if(h.kind=="string"&&type==8&&h.text.size()==1) {
            unsigned char c=h.text[0];if(c>='A'&&c<='Z')c+=32;
            if(int(c)==key)return true;
        }
        if(h.kind=="PlayerActions"&&type==8) {
            static const std::array<std::array<int,2>,5> keys={{{context_key_value("W"),context_key_value("Up")},
                {context_key_value("S"),context_key_value("Down")},{context_key_value("A"),context_key_value("Left")},
                {context_key_value("D"),context_key_value("Right")},{context_key_value("Space"),context_key_value("Space")}}};
            if(h.value>=0&&h.value<5&&(key==keys[h.value][0]||key==keys[h.value][1]))return true;
        }
    }
    return false;
}
void context_collect(lua_State* L,World& w) {
    auto& retired=w.context_actions.retired;
    for(auto it=retired.begin();it!=retired.end();) {
        if(it->use_count()==1){if((*it)->callback!=LUA_NOREF)lua_unref(L,(*it)->callback);it=retired.erase(it);}else ++it;
    }
}
void context_queue(World& w,ContextInput event) {
    auto& state=w.context_actions;
    if(event.targets.empty())return;
    if(state.pending.size()>=kContextInputLimit||event.targets.size()>kContextTargetLimit-state.target_count)
        throw std::runtime_error("context action input backlog limit exceeded");
    state.target_count+=event.targets.size();state.pending.push_back(std::move(event));
}
void queue_context_input(World& w,const std::string& node,int type,int key,int input_state,bool processed) {
    // Processed begins/changes belong to the frontend's UI. Releases still
    // reach game bindings so an action begun outside UI cannot remain held.
    if(processed&&input_state<2)return;
    ContextInput event;event.node=node;event.type=type;event.key=key;event.state=input_state;
    for(const auto& binding:w.context_actions.bindings)if(binding->active&&context_matches(*binding,type,key))event.targets.push_back(binding);
    std::stable_sort(event.targets.begin(),event.targets.end(),context_before);context_queue(w,std::move(event));
}
void context_pop(World& w) {
    auto& state=w.context_actions;state.target_count-=state.pending.front().targets.size();state.pending.pop_front();
}
Node* context_cancel_input(World& w) {
    // Native cancellation supplies a fresh neutral InputObject on each call.
    if(w.nodes.size()>=kMaxNodes)throw std::runtime_error("context action InputObject limit exceeded");
    Node value;do{value.id="__context_cancel_"+std::to_string(w.next_local_id++);}while(w.nodes.count(value.id));
    value.name="InputObject";value.class_name="InputObject";value.server_owned=false;
    value.properties={{"KeyCode",input_enum_json("KeyCode",0)},
        {"UserInputType",input_enum_json("UserInputType",input_enum_item("UserInputType","None"))},
        {"UserInputState",input_enum_json("UserInputState",3)},
        {"Position",{{"$vector3",{0,0,0}}}},{"Delta",{{"$vector3",{0,0,0}}}}};
    auto* node=&w.nodes.emplace(value.id,std::move(value)).first->second;w.order.push_back(node->id);return node;
}
int context_dispatch_bridge(lua_State* L) {
    auto& w=*static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));auto& state=w.context_actions;
    const int operation=lua_tointeger(L,lua_upvalueindex(2));
    try {
        if(operation==1){luaL_checktype(L,1,LUA_TTHREAD);lua_pushstring(L,w.scheduler->task_state(lua_tothread(L,1)));return 1;}
        if(operation==2) {
            luaL_checktype(L,1,LUA_TTHREAD);lua_State* child=lua_tothread(L,1);
            // The dispatcher keeps the coroutine alive even if a completed
            // scheduler task has already been collected after a CPU slice.
            // A completed Luau thread with return values still on its stack
            // reports COSUS (the same status as an initial coroutine). Use the
            // scheduler's execution result, with the VM status as the fallback
            // once that scheduler entry was collected.
            const bool done=lua_status(child)==LUA_OK&&(w.scheduler->completion(child)==LUA_OK||
                !std::strcmp(w.scheduler->task_state(child),"untracked"));
            bool pass=false;
            if(done&&lua_gettop(child)>0&&has_meta(child,1,kUsiExportItemMeta)) {
                auto* result=static_cast<UsiExportItemRef*>(lua_touserdata(child,1));
                pass=result->kind==input_enum_kind("ContextActionResult")&&kUsiExportEnums[result->kind].items[result->item].value==1;
            }
            if(!pass&&!state.pending.empty())context_pop(w);
            context_collect(L,w);return 0;
        }
        if(operation==3){state.active=false;context_collect(L,w);return 0;}
        while(!state.pending.empty()) {
            auto& event=state.pending.front();
            while(event.index<event.targets.size()) {
                auto binding=event.targets[event.index];
                if((!binding->active&&!event.force)||binding->callback==LUA_NOREF){++event.index;continue;}
                if(w.scheduler->callback_capacity()==0){lua_pushboolean(L,false);return 1;}
                ++event.index;
                Node* object=event.state==3?context_cancel_input(w):find_node(w,event.node);
                const int input_state=event.state;
                w.scheduler->callback_now(L,binding->callback,[&](lua_State* child){
                    lua_pushlstring(child,binding->name.data(),binding->name.size());
                    push_json(child,w,input_enum_json("UserInputState",input_state));
                    if(object)push_node(child,w,object);else lua_pushnil(child);return 3;
                },binding->owner);return 1;
            }
            context_pop(w);context_collect(L,w);
        }
        lua_pushnil(L);return 1;
    }catch(const std::exception& error){luaL_error(L,"context action dispatch: %s",error.what());return 0;}
}
void ensure_context_dispatcher(lua_State* L,World& w) {
    auto& state=w.context_actions;if(state.dispatcher!=LUA_NOREF)return;
    static constexpr const char* source=R"CONTEXT(
local nextCallback,state,resolve,finish,waitForSlice=...
return function()
 while true do
  local child=nextCallback()
  if child==nil then break end
  if child then
   while state(child)=="ready" do waitForSlice(.001)end
   resolve(child)
  else waitForSlice(.001)end
 end
 finish()
end
)CONTEXT";
    const auto code=Luau::compile(source);
    if(w.load_script(L,"=context-action-dispatcher",code,source)!=LUA_OK)throw std::runtime_error(lua_tostring(L,-1));
    for(int operation=0;operation<4;++operation){lua_pushlightuserdata(L,&w);lua_pushinteger(L,operation);lua_pushcclosure(L,context_dispatch_bridge,"context action bridge",2);}
    lua_getfield(L,LUA_REGISTRYINDEX,"Headless.SchedulerWait");
    if(!lua_isfunction(L,-1))throw std::runtime_error("context action scheduler unavailable");
    if(lua_pcall(L,5,1,0)!=LUA_OK)throw std::runtime_error(lua_tostring(L,-1));
    state.dispatcher=lua_ref(L,-1);lua_pop(L,1);
}
std::shared_ptr<ContextActionBinding> context_find(World& w,const std::string& name) {
    for(const auto& binding:w.context_actions.bindings)if(binding->active&&binding->name==name)return binding;
    return nullptr;
}
void context_cancel(lua_State* L,World& w,const std::shared_ptr<ContextActionBinding>& binding) {
    if(binding->callback==LUA_NOREF)return;
    if(w.scheduler->callback_capacity()>0) {
        auto* input=context_cancel_input(w);
        w.scheduler->callback_now(L,binding->callback,[&](lua_State* child){
            lua_pushlstring(child,binding->name.data(),binding->name.size());
            push_json(child,w,input_enum_json("UserInputState",3));push_node(child,w,input);return 3;
        },binding->owner);
        lua_pop(L,1);return;
    }
    ContextInput event;event.state=3;event.force=true;event.targets.push_back(binding);context_queue(w,std::move(event));
}
void context_retire(lua_State* L,World& w,const std::shared_ptr<ContextActionBinding>& binding,bool cancel) {
    if(!binding->active)return;
    auto& state=w.context_actions;
    binding->active=false;state.bindings.erase(std::remove(state.bindings.begin(),state.bindings.end(),binding),state.bindings.end());state.retired.push_back(binding);
    if(cancel)context_cancel(L,w,binding);
}
void context_reserve(World& w,size_t events,size_t targets) {
    if(events>kContextInputLimit-w.context_actions.pending.size()||targets>kContextTargetLimit-w.context_actions.target_count)
        throw std::runtime_error("context action input backlog limit exceeded");
}
void clear_context_owner(lua_State* L,World& w,const std::string& owner) {
    auto bindings=w.context_actions.bindings;
    for(const auto& binding:bindings)if(binding->owner==owner)context_retire(L,w,binding,false);
    // Force cancels queued by earlier operations must not restart a stopped script.
    for(auto& event:w.context_actions.pending)for(auto& binding:event.targets)
        if(binding->owner==owner&&binding->callback!=LUA_NOREF){lua_unref(L,binding->callback);binding->callback=LUA_NOREF;}
    bindings.clear();context_collect(L,w);
}
std::vector<std::shared_ptr<ContextActionBinding>> context_displaced(World& w,const std::shared_ptr<ContextActionBinding>& incoming) {
    std::vector<std::shared_ptr<ContextActionBinding>> ordered=w.context_actions.bindings,result;
    std::stable_sort(ordered.begin(),ordered.end(),context_before);
    auto visit=[&](int type,int key){
        if(!context_matches(*incoming,type,key))return;
        for(const auto& old:ordered)if(old->active&&old->name!=incoming->name&&context_matches(*old,type,key)) {
            if(context_before(incoming,old)&&std::find(result.begin(),result.end(),old)==result.end())result.push_back(old);
            break;
        }
    };
    const auto& keys=kUsiExportEnums[input_enum_kind("KeyCode")];
    for(size_t i=0;i<keys.count;++i)visit(8,keys.items[i].value);
    for(int type:{0,1,2,3,4,7})visit(type,0);
    // Bindings may be authored before a gamepad backend is attached.
    for(int type=12;type<=19;++type)for(size_t i=0;i<keys.count;++i)visit(type,keys.items[i].value);
    return result;
}
Json context_input_json(const ContextHotkey& input) {
    if(input.kind=="string")return input.text;
    const int kind=input_enum_kind(input.kind.c_str());
    for(size_t i=0;i<kUsiExportEnums[kind].count;++i)if(kUsiExportEnums[kind].items[i].value==input.value)return input_enum_json(input.kind.c_str(),i);
    throw std::runtime_error("invalid context action hotkey");
}
Json context_info(World& w,const std::shared_ptr<ContextActionBinding>& binding) {
    auto ordered=w.context_actions.bindings;std::stable_sort(ordered.begin(),ordered.end(),context_before);
    const auto found=std::find(ordered.begin(),ordered.end(),binding);
    Json info=binding->metadata;info["priorityLevel"]=binding->priority;info["stackOrder"]=ordered.size()-size_t(found-ordered.begin());
    if(binding->touch_button)info["createTouchButton"]=true;
    if(!binding->inputs.empty()){Json inputs=Json::array();for(const auto& input:binding->inputs)inputs.push_back(context_input_json(input));info["inputTypes"]=std::move(inputs);}
    return info;
}
int context_action_method(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;auto& state=w.context_actions;
    if(h->node->class_name!="ContextActionService"||h->node->destroyed||!w.scheduler)return luaL_error(L,"live ContextActionService receiver required"),0;
    const int operation=lua_tointeger(L,lua_upvalueindex(1));
    try {
        context_collect(L,w);
        if(operation==4){Json info=Json::object();for(const auto& binding:state.bindings)if(binding->active)info[binding->name]=context_info(w,binding);push_json(L,w,info);return 1;}
        if(operation==5) {
            context_reserve(w,state.bindings.size(),state.bindings.size());auto bindings=state.bindings;
            for(const auto& binding:bindings)context_retire(L,w,binding,true);return 0;
        }
        size_t length=0;const char* text=luaL_checklstring(L,2,&length);
        if(length>256)throw std::runtime_error("context action name exceeds 256 bytes");
        const std::string name(text,length);auto previous=context_find(w,name);
        if(operation==2){if(previous){context_reserve(w,1,1);context_retire(L,w,previous,true);}return 0;}
        if(operation==3){push_json(L,w,previous?context_info(w,previous):Json::object());return 1;}
        if(operation>=6&&operation<=8) {
            size_t size=0;const char* value=luaL_checklstring(L,3,&size);if(size>4096)throw std::runtime_error("context action label exceeds 4096 bytes");
            if(previous)previous->metadata[operation==6?"description":operation==7?"title":"image"]=std::string(value,size);return 0;
        }
        if(operation==9){lua_pushnil(L);return 1;} // No touch UI is created on a desktop backend.
        if(operation==10){if(!is_authored_value(L,3)||std::string(luaL_typename(L,3))!="UDim2")throw std::runtime_error("touch button position must be UDim2");return 0;}
        if(operation!=0&&operation!=1)throw std::runtime_error("unknown context action method");
        luaL_checktype(L,3,LUA_TFUNCTION);luaL_checktype(L,4,LUA_TBOOLEAN);
        const bool touch=lua_toboolean(L,4);
        if(touch&&input_capability(w,"TouchEnabled"))throw std::runtime_error("context action touch-button UI is not implemented; bind touch input with createTouchButton=false");
        double priority=2000;int first=5;
        if(operation==1){priority=luaL_checknumber(L,5);first=6;}
        if(!std::isfinite(priority)||priority<double(INT32_MIN)||priority>double(INT32_MAX))throw std::runtime_error("context action priority must fit signed 32-bit integer");
        if(lua_gettop(L)-first+1>64)throw std::runtime_error("context action accepts at most 64 input types");
        if(!previous&&state.bindings.size()>=kContextActionLimit)throw std::runtime_error("context action binding limit exceeded");
        auto binding=std::make_shared<ContextActionBinding>();binding->name=name;binding->owner=w.scheduler->owner(L);binding->priority=static_cast<int>(priority);binding->touch_button=touch;binding->sequence=state.next_sequence;
        for(int i=first;i<=lua_gettop(L);++i) {
            ContextHotkey input;
            if(lua_type(L,i)==LUA_TSTRING){size_t n;const char* value=lua_tolstring(L,i,&n);if(n>256)throw std::runtime_error("context action hotkey string exceeds 256 bytes");input.kind="string";input.text.assign(value,n);}
            else if(has_meta(L,i,kUsiExportItemMeta)) {
                auto* item=static_cast<UsiExportItemRef*>(lua_touserdata(L,i));input.kind=kUsiExportEnums[item->kind].name;
                if(input.kind!="KeyCode"&&input.kind!="UserInputType"&&input.kind!="PlayerActions")throw std::runtime_error("context action hotkey has incorrect enum type");
                input.value=kUsiExportEnums[item->kind].items[item->item].value;
            } else throw std::runtime_error("context action hotkey must be KeyCode, UserInputType, PlayerActions or string");
            binding->inputs.push_back(std::move(input));
        }
        auto displaced=context_displaced(w,binding);context_reserve(w,displaced.size()+(previous?1:0),displaced.size()+(previous?1:0));
        ensure_context_dispatcher(L,w);
        binding->callback=lua_ref(L,3);
        if(previous)context_retire(L,w,previous,true);
        for(const auto& old:displaced)if(old->active)context_cancel(L,w,old);
        if(auto reentrant=context_find(w,name))context_retire(L,w,reentrant,false);
        binding->sequence=state.next_sequence++;
        state.bindings.push_back(std::move(binding));return 0;
    }catch(const std::exception& error){luaL_error(L,"ContextActionService: %s",error.what());return 0;}
}
bool context_action_index(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name!="ContextActionService")return false;
    constexpr const char* methods[]={"BindAction","BindActionAtPriority","UnbindAction","GetBoundActionInfo","GetAllBoundActionInfo","UnbindAllActions","SetDescription","SetTitle","SetImage","GetButton","SetPosition"};
    for(int i=0;i<(int)std::size(methods);++i)if(key==methods[i]||(i==0&&key=="BindActionToInputTypes")) {
        lua_pushinteger(L,i);lua_pushcclosure(L,context_action_method,methods[i],1);return true;
    }
    return false;
}
void dispatch_context_actions(lua_State* L,World& w) {
    auto& state=w.context_actions;context_collect(L,w);
    if(state.active&&state.thread) {
        const char* status=w.scheduler->task_state(state.thread);
        if(!strcmp(status,"completed")||!strcmp(status,"cancelled")||!strcmp(status,"untracked"))state.active=false;
    }
    if(!state.active&&!state.pending.empty()&&w.scheduler->callback_capacity()>0) {
        ensure_context_dispatcher(L,w);state.active=true;
        state.thread=w.scheduler->callback(L,state.dispatcher,[](lua_State*){return 0;},{},"__engine_context_actions__");
    }
}
