// Local, scheduler-driven Tween instances. Property writes use the ordinary
// Instance setter so datatype checks, CFrame derivation and signals still apply.
constexpr size_t kTweenLimit=256;
constexpr size_t kTweenPropertyLimit=32;

bool tween_active(const TweenPlaybackState& t){return t.state==1||t.state==2;}

bool tween_numeric_tree(const Json& value,int depth=0) {
    if(depth>4)return false;
    if(value.is_number())return std::isfinite(value.get<double>());
    if(!value.is_array()||value.empty()||value.size()>12)return false;
    for(const auto& component:value)if(!tween_numeric_tree(component,depth+1))return false;
    return true;
}
bool tween_shape(const Json& a,const Json& b) {
    if(a.is_boolean()&&b.is_boolean())return true;
    if(a.is_number()&&b.is_number())return tween_numeric_tree(a)&&tween_numeric_tree(b);
    if(!a.is_object()||!b.is_object()||a.size()!=1||b.size()!=1)return false;
    const std::string tag=a.begin().key();
    if(!b.contains(tag))return false;
    if(tag=="$cframe")return a[tag].is_object()&&b[tag].is_object();
    if(tag=="$udim2"||tag=="$rect") {
        if(!a[tag].is_array()||!b[tag].is_array()||a[tag].size()!=2||b[tag].size()!=2)return false;
        return tween_shape(a[tag][0],b[tag][0])&&tween_shape(a[tag][1],b[tag][1]);
    }
    if(tag!="$vector3"&&tag!="$vector2"&&tag!="$color3"&&tag!="$udim"&&tag!="$udim2")return false;
    if(!tween_numeric_tree(a[tag])||!tween_numeric_tree(b[tag]))return false;
    return a[tag].size()==b[tag].size();
}
Json tween_linear(const Json& a,const Json& b,double alpha) {
    if(a.is_number())return a.get<double>()+(b.get<double>()-a.get<double>())*alpha;
    if(a.is_object()) {
        Json out=Json::object();
        for(auto it=a.begin();it!=a.end();++it)out[it.key()]=tween_linear(it.value(),b.at(it.key()),alpha);
        return out;
    }
    Json out=Json::array();
    for(size_t i=0;i<a.size();++i)out.push_back(tween_linear(a[i],b[i],alpha));
    return out;
}
Json tween_interpolate(lua_State* L,World& w,const Json& a,const Json& b,double alpha) {
    if(alpha==0)return a;
    if(alpha==1)return b;
    if(a.is_boolean()) {
        // Native booleans interpolate numerically, then convert nonzero to
        // true. false->true changes on the first nonzero step; true->false
        // remains true until the interpolated value reaches exactly zero.
        const double start=a.get<bool>()?1.:0.,goal=b.get<bool>()?1.:0.;
        return start+(goal-start)*alpha!=0.;
    }
    if(a.is_number())return tween_linear(a,b,alpha);
    const std::string tag=a.begin().key();
    if(tag=="$cframe") {
        const int top=lua_gettop(L);
        lua_pushcfunction(L,cframe_lerp,"Tween CFrame interpolation");
        push_json(L,w,a);push_json(L,w,b);lua_pushnumber(L,alpha);
        if(lua_pcall(L,3,1,0)!=LUA_OK) {
            std::string error=lua_tostring(L,-1);lua_settop(L,top);
            throw std::runtime_error(error);
        }
        Json value=convert_lua_value(L,w,-1);lua_settop(L,top);return value;
    }
    return Json{{tag,tween_linear(a[tag],b[tag],alpha)}};
}
void tween_set(lua_State* L,World& w,Node& target,const std::string& key,const Json& value) {
    const int top=lua_gettop(L);
    lua_pushcfunction(L,instance_newindex,"Tween property update");
    push_node(L,w,&target);lua_pushlstring(L,key.data(),key.size());push_json(L,w,value);
    if(lua_pcall(L,3,0,0)!=LUA_OK) {
        std::string error=lua_tostring(L,-1);lua_settop(L,top);
        throw std::runtime_error(error);
    }
    lua_settop(L,top);
}
void tween_state(World& w,Node& node,TweenPlaybackState& t,int state) {
    if(t.state!=state){t.state=state;property_events(w,node,"PlaybackState");}
}
void tween_finished(lua_State* L,World& w,Node& node,TweenPlaybackState& t,int state) {
    tween_state(w,node,t,state);
    if(w.event_argument_refs.size()>=10000)throw std::runtime_error("Tween callback argument budget exceeded");
    lua_createtable(L,1,0);push_geometry_item(L,kTweenPlaybackState,state);lua_rawseti(L,-2,1);
    const int reference=lua_ref(L,-1);lua_pop(L,1);w.event_argument_refs.push_back(reference);
    emit(w,node,"Completed",Json::array(),reference,1);
}
void tween_step(lua_State* L,World& w,Node& node,TweenPlaybackState& t,
                std::chrono::steady_clock::time_point now,bool force=false) {
    if(!tween_active(t))return;
    Node* target=find_node(w,t.target);
    if(node.destroyed||!target){t.state=5;return;}
    // The scheduler may pump repeatedly while a Lua job waits. Treat those
    // pumps as one frame rather than producing thousands of property writes.
    if(!force&&std::chrono::duration<double>(now-t.updated).count()<1./60.)return;
    t.elapsed+=std::max(0.,std::chrono::duration<double>(now-t.updated).count());t.updated=now;
    const double travel=t.duration*(t.reverses?2.:1.);
    const double cycle=t.delay+travel;
    const bool complete=t.repeats>=0&&(cycle==0||t.elapsed>=cycle*(double(t.repeats)+1.));
    double alpha=0;
    if(complete)alpha=t.reverses?0.:1.;
    else {
        const double phase=cycle>0?std::fmod(t.elapsed,cycle):0;
        if(phase<t.delay){tween_state(w,node,t,1);return;}
        tween_state(w,node,t,2);
        const double progress=phase-t.delay;
        alpha=t.duration==0?(t.reverses?0.:1.):
            (t.reverses&&progress>t.duration?2.-progress/t.duration:progress/t.duration);
        alpha=tween_ease(t.style,t.direction,alpha);
    }
    for(auto it=t.goals.begin();it!=t.goals.end();++it) {
        Json value=tween_interpolate(L,w,t.starts.at(it.key()),it.value(),alpha);
        const Json* current=property_value(w,*target,it.key());
        if(!current||*current!=value)tween_set(L,w,*target,it.key(),value);
    }
    if(complete)tween_finished(L,w,node,t,4);
}
void advance_tweens(lua_State* L,World& w) {
    const auto now=std::chrono::steady_clock::now();
    for(auto it=w.tweens.begin();it!=w.tweens.end();) {
        Node* node=find_node(w,it->first);
        if(!node||node->destroyed){it=w.tweens.erase(it);continue;}
        try{tween_step(L,w,*node,it->second,now);}
        catch(const std::exception& error) {
            // A target removed/retyped by a received update cannot poison
            // the scheduler. Retire its tween through the normal callback.
            const std::string message=std::string("WARN: Tween cancelled: ")+error.what()+"\n";
            if(message.size()<=kMaxPrinted-w.output.size())w.output+=message;
            tween_finished(L,w,*node,it->second,5);
        }
        ++it;
    }
}
int tween_method(lua_State* L) {
    auto* h=checked_handle(L,1);World& w=*h->world;
    try {
        auto found=w.tweens.find(h->node->id);
        if(h->node->destroyed||found==w.tweens.end())throw std::runtime_error("live Tween required");
        auto& t=found->second;
        const std::string method=lua_tostring(L,lua_upvalueindex(1));
        const auto now=std::chrono::steady_clock::now();
        tween_step(L,w,*h->node,t,now,true);
        if(method=="Pause") {
            if(t.state==2)tween_state(w,*h->node,t,3);
        } else if(method=="Cancel") {
            if(t.state!=0&&t.state!=5){t.elapsed=0;tween_finished(L,w,*h->node,t,5);}
        } else if(!tween_active(t)) {
            Node* target=find_node(w,t.target);
            if(!target)throw std::runtime_error("Tween target is unavailable");
            if(t.state!=3) {
                Json starts=Json::object();
                for(auto goal=t.goals.begin();goal!=t.goals.end();++goal) {
                    const Json* current=property_value(w,*target,goal.key());
                    if(!current||!tween_shape(*current,goal.value()))throw std::runtime_error("Tween target property changed type");
                    starts[goal.key()]=*current;
                }
                t.starts=std::move(starts);t.elapsed=0;
            }
            for(auto& [id,other]:w.tweens)if(id!=h->node->id&&other.target==t.target&&tween_active(other)) {
                bool overlap=false;
                for(auto goal=t.goals.begin();goal!=t.goals.end();++goal)if(other.goals.contains(goal.key()))overlap=true;
                if(overlap)if(auto* previous=find_node(w,id))tween_finished(L,w,*previous,other,5);
            }
            t.updated=now;tween_state(w,*h->node,t,t.elapsed<t.delay?1:2);
        }
        return 0;
    }catch(const std::exception& error){luaL_error(L,"Tween playback failed: %s",error.what());return 0;}
}
int tween_create(lua_State* L) {
    auto* service=checked_handle(L,1);World& w=*service->world;
    auto* target=checked_handle(L,2);auto* info=checked_tween_info(L,3);luaL_checktype(L,4,LUA_TTABLE);
    try {
        if(service->node->class_name!="TweenService"||service->node->destroyed||target->world!=&w||target->node->destroyed)
            throw std::runtime_error("TweenService and live same-world target required");
        for(auto it=w.tweens.begin();it!=w.tweens.end();) {
            Node* node=find_node(w,it->first);
            if(!node||node->destroyed)it=w.tweens.erase(it);else ++it;
        }
        if(w.tweens.size()>=kTweenLimit)throw std::runtime_error("Tween instance budget exceeded");
        if(info->time>86400||info->delay_time>86400||info->repeat_count < -1)
            throw std::runtime_error("Tween duration/repeat count exceeds supported limits");
        TweenPlaybackState state;state.target=target->node->id;state.goals=Json::object();
        state.duration=info->time;state.delay=info->delay_time;state.style=info->style;
        state.direction=info->direction;state.repeats=int(info->repeat_count);state.reverses=info->reverses;
        lua_pushnil(L);
        while(lua_next(L,4)) {
            if(lua_type(L,-2)!=LUA_TSTRING||state.goals.size()>=kTweenPropertyLimit)throw std::runtime_error("Tween goal keys must be bounded property names");
            size_t length;const char* raw=lua_tolstring(L,-2,&length);std::string key(raw,length);
            if(length==0||length>128)throw std::runtime_error("invalid Tween goal property");
            if(lua_type(L,-1)!=LUA_TNUMBER&&lua_type(L,-1)!=LUA_TBOOLEAN&&!lua_isvector(L,-1)&&!has_meta(L,-1,kCFrameMeta)&&!is_authored_value(L,-1))
                throw std::runtime_error("unsupported Tween goal datatype");
            Json goal=convert_lua_value(L,w,-1);
            const Json* current=property_value(w,*target->node,key);
            if(!current||!tween_shape(*current,goal))throw std::runtime_error("Tween goal must match an existing supported property datatype");
            if(const Json* descriptor=property_descriptor(*target->node,key))if(!descriptor->value("writable",false))
                throw std::runtime_error("Tween goal property is read-only");
            if(key=="ClassName"||key=="JobId"||display_property(w,*target->node,key))throw std::runtime_error("Tween goal property is read-only");
            state.goals[key]=std::move(goal);lua_pop(L,1);
        }
        Node* node=animation_node(w,"Tween","Tween");
        w.tweens.emplace(node->id,std::move(state));push_node(L,w,node);return 1;
    }catch(const std::exception& error){luaL_error(L,"TweenService:Create failed: %s",error.what());return 0;}
}
bool tween_playback_index(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name=="TweenService"&&key=="SmoothDamp"){lua_pushcfunction(L,tween_smooth_damp,"SmoothDamp");return true;}
    if(node.class_name=="TweenService"&&key=="Create"){lua_pushcfunction(L,tween_create,"Create");return true;}
    if(node.class_name!="Tween")return false;
    auto found=w.tweens.find(node.id);if(found==w.tweens.end())return false;
    auto& t=found->second;
    if(key=="Play"||key=="Pause"||key=="Cancel") {
        lua_pushlstring(L,key.data(),key.size());lua_pushcclosure(L,tween_method,key.c_str(),1);
    } else if(key=="PlaybackState")push_geometry_item(L,kTweenPlaybackState,t.state);
    else if(key=="Instance")push_node(L,w,find_node(w,t.target));
    else if(key=="TweenInfo")push_tween_info(L,TweenInfoValue{t.duration,t.style,t.direction,t.repeats,t.reverses,t.delay});
    else if(key=="Completed")push_signal(L,signal_for(w,node,"Completed"));
    else return false;
    return true;
}
