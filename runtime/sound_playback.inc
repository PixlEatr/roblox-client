bool audio_load_trace_enabled() noexcept {
    const char* value=std::getenv("RCR_TRACE_AUDIO_LOADS");
    return value&&std::strcmp(value,"1")==0;
}
std::string audio_trace_sha256(const char* data,size_t size) {
    return source_crypto::hex(source_crypto::sha256(
        reinterpret_cast<const uint8_t*>(data),size));
}
double audio_trace_ms(std::chrono::steady_clock::time_point end,std::chrono::steady_clock::time_point start) {
    if(end==std::chrono::steady_clock::time_point{}||start==std::chrono::steady_clock::time_point{})return -1.0;
    const double value=std::chrono::duration<double,std::milli>(end-start).count();
    return std::isfinite(value)?std::max(0.0,value):-1.0;
}
void audio_trace_limit(World& w) noexcept {
    if(w.audio_load_trace_limit_reported)return;
    w.audio_load_trace_limit_reported=true;
    try {
        const std::string marker="__AUDIO_LOAD__{\"event\":\"trace_limit_reached\"}\n";
        const size_t room=w.output.size()<kMaxPrinted?kMaxPrinted-w.output.size():0;
        if(room>=marker.size())w.output+=marker;else w.output_truncated=true;
    } catch(...) {
        w.output_truncated=true;
    }
}
void audio_trace_record(World& w,const Node& node,const AudioLoadTraceState& state,
                        const char* event,const Json& extra=Json::object()) noexcept {
    if(!audio_load_trace_enabled())return;
    if(w.audio_load_trace_record_count>=32) {audio_trace_limit(w);return;}
    try {
        Json record={{"event",event},{"node_id",node.id.substr(0,96)},
                     {"asset_uri_sha256",state.asset_uri_sha256}};
        if(state.request_started) {
            record["byte_count"]=state.byte_count;
            record["bytes_sha256"]=state.bytes_sha256;
        }
        if(extra.is_object())for(auto it=extra.begin();it!=extra.end();++it)
            record[it.key()]=it.value();
        std::string line="__AUDIO_LOAD__"+record.dump()+"\n";
        if(line.size()>1024) {
            record.erase("node_id");
            line="__AUDIO_LOAD__"+record.dump()+"\n";
        }
        ++w.audio_load_trace_record_count;
        const size_t room=w.output.size()<kMaxPrinted?kMaxPrinted-w.output.size():0;
        if(line.size()<=1024&&room>=line.size())w.output+=line;
        else w.output_truncated=true;
    } catch(...) {
        // Trace failures never alter the Sound request or its result.
    }
}
AudioLoadTraceState* audio_trace_assign(World& w,const Node& node,
                                        const std::string& uri,bool force_new) noexcept {
    if(!audio_load_trace_enabled())return nullptr;
    // A live place can contain many server-owned or engine-owned sounds. Keep
    // this diagnostic focused on locally supplied workspace assets so those
    // unrelated sounds cannot consume its small per-World record budget.
    if(uri.compare(0,21,"rbxasset://workspace/")!=0)return nullptr;
    auto found=w.audio_load_trace.find(node.id);
    if(found==w.audio_load_trace.end()) {
        if(w.audio_load_trace_record_count>=32) {audio_trace_limit(w);return nullptr;}
        try {found=w.audio_load_trace.emplace(node.id,AudioLoadTraceState{}).first;}
        catch(...) {return nullptr;}
    }
    try {
        const std::string digest=audio_trace_sha256(uri.data(),uri.size());
        auto& state=found->second;
        if(force_new||!state.assigned||state.asset_uri_sha256!=digest) {
            state=AudioLoadTraceState{};
            state.assigned=true;state.assigned_at=std::chrono::steady_clock::now();
            state.asset_uri_sha256=digest;
            audio_trace_record(w,node,state,"assigned",Json{{"asset_kind","local_workspace"}});
        }
        return &state;
    } catch(...) {return nullptr;}
}
void audio_trace_failed(World& w,const Node& node,const std::string& uri,
                        const char* reason) noexcept {
    if(!audio_load_trace_enabled())return;
    auto* state=audio_trace_assign(w,node,uri,false);
    if(!state||state->failed||state->decoded)return;
    state->failed=true;
    const auto failed_at=std::chrono::steady_clock::now();
    audio_trace_record(w,node,*state,"failed",Json{{"reason",reason},
        {"assigned_to_failure_ms",audio_trace_ms(failed_at,state->assigned_at)},
        {"request_to_failure_ms",audio_trace_ms(failed_at,state->request_at)}});
}
void audio_trace_observed(World& w,const Node& node,const std::string& uri,
                          const char* property,const SoundPlayback& sound) noexcept {
    if(!audio_load_trace_enabled())return;
    auto* state=audio_trace_assign(w,node,uri,false);
    if(!state||state->first_property_observed)return;
    state->first_property_observed=true;
    const auto observed_at=std::chrono::steady_clock::now();
    Json extra={{"property",property},{"is_loaded",sound.loaded},
                {"is_playing",sound.playing},{"time_position",sound.position},
                {"request_to_observation_ms",audio_trace_ms(observed_at,state->request_at)}};
    extra["property"]=std::string(property).substr(0,32);
    if(state->decoded)
        extra["decode_to_observation_ms"]=audio_trace_ms(observed_at,state->decoded_at);
    audio_trace_record(w,node,*state,"first_property_observation",extra);
}

// Headless audio timelines are driven by real decoded asset duration and the
// monotonic clock. This host does not open an audio output device.
SoundPlayback& sound_state(World& w,Node& node) {
    auto found=w.sounds.find(node.id);
    if(found!=w.sounds.end())return found->second;
    if(w.sounds.size()>=512)throw std::runtime_error("local sound limit exceeded");
    return w.sounds.emplace(node.id,SoundPlayback{}).first->second;
}
std::string sound_asset(World& w,Node& node) {
    const auto* value=property_value(w,node,"SoundId");
    return value&&value->is_string()?value->get<std::string>():"";
}
void sound_playing(World& w,Node& node,SoundPlayback& state,bool playing) {
    if(state.playing==playing)return;
    state.playing=playing;
    for(const char* key:{"Playing","IsPlaying","IsPaused"})property_events(w,node,key);
}
void sound_advance(World& w,Node& node,SoundPlayback& state) {
    auto now=std::chrono::steady_clock::now();
    double delta=state.previous==std::chrono::steady_clock::time_point{}?0:std::chrono::duration<double>(now-state.previous).count();
    state.previous=now;
    if(!state.playing||!state.loaded||delta<=0)return;
    const auto* speed_value=property_value(w,node,"PlaybackSpeed");
    double speed=speed_value&&speed_value->is_number()?speed_value->get<double>():1;
    if(!std::isfinite(speed)||speed<0)return;
    const double position=state.position;
    state.position+=delta*speed;
    if(state.position>=state.duration) {
        const auto* looped=property_value(w,node,"Looped");
        if(looped&&looped->is_boolean()&&looped->get<bool>()) {
            const double count=std::floor(state.position/state.duration);
            state.loops+=unsigned(std::min(count,double(UINT_MAX-state.loops)));
            state.position=std::fmod(state.position,state.duration);
            emit(w,node,"DidLoop",Json::array({state.asset,state.loops}));
        }else {
            state.position=0;sound_playing(w,node,state,false);
            emit(w,node,"Ended",Json::array({state.asset}));
        }
    }
    if(position!=state.position)property_events(w,node,"TimePosition");
}
bool sound_finish_load(World& w,Node& node,SoundPlayback& state) {
    if(!state.decoding.valid()||state.decoding.wait_for(std::chrono::seconds(0))!=std::future_status::ready)
        return state.loaded;
    auto completed=std::move(state.decoding);state.decoding={};
    try {
        const auto info=completed.get();
        state.duration=info.duration;state.loaded=true;state.previous=info.available_at;
        if(audio_load_trace_enabled())if(auto* trace=audio_trace_assign(w,node,state.asset,false)) {
            trace->decoded=true;trace->failed=false;trace->decoded_at=info.available_at;
            audio_trace_record(w,node,*trace,"completed",Json{
                {"request_to_decode_ms",audio_trace_ms(info.available_at,trace->request_at)}});
        }
        property_events(w,node,"IsLoaded");property_events(w,node,"TimeLength");
        emit(w,node,"Loaded",Json::array());
        if(!state.pending_action.empty()) {
            sound_playing(w,node,state,true);
            emit(w,node,state.pending_action=="Play"?"Played":"Resumed",Json::array({state.asset}));
        }
    }catch(const std::exception&){state.loaded=false;audio_trace_failed(w,node,state.asset,"decode_failed");}
    state.pending_action.clear();return state.loaded;
}
bool sound_load(lua_State* L,World& w,Node& node,SoundPlayback& state) {
    const std::string uri=sound_asset(w,node);
    if(uri!=state.asset) {state=SoundPlayback{};state.asset=uri;}
    audio_trace_assign(w,node,uri,false);
    if(state.attempted)return sound_finish_load(w,node,state);
    state.attempted=true;
    // Local workspace URIs have a registered resolver. Other content needs an
    // asset provider; it must not be reported as loaded on the basis of its ID.
    if(uri.compare(0,21,"rbxasset://workspace/")!=0) {
        audio_trace_failed(w,node,uri,"unsupported_uri_scheme");return false;
    }
    const int top=lua_gettop(L);
    try {
        lua_getfield(L,LUA_REGISTRYINDEX,"Headless.CustomAssetResolver");
        if(!lua_isfunction(L,-1)){lua_settop(L,top);audio_trace_failed(w,node,uri,"resolver_unavailable");return false;}
        lua_pushlstring(L,uri.data(),uri.size());
        if(lua_pcall(L,1,1,0)!=LUA_OK||lua_type(L,-1)!=LUA_TSTRING){lua_settop(L,top);audio_trace_failed(w,node,uri,"resolver_failed");return false;}
        size_t length=0;const char* bytes=lua_tolstring(L,-1,&length);
        // The worker owns a copy of the bytes and never touches Lua or World.
        // Completion is applied on this thread at an ordinary scheduler tick.
        AudioLoadTraceState* trace=audio_trace_assign(w,node,uri,false);
        if(trace) {
            trace->request_at=std::chrono::steady_clock::now();trace->request_started=true;trace->byte_count=length;
            trace->bytes_sha256=audio_trace_sha256(bytes,length);
        }
        state.decoding=audio_asset::prepare(bytes,length);
        if(trace)audio_trace_record(w,node,*trace,"pending",Json{
            {"request_start_offset_ms",audio_trace_ms(trace->request_at,trace->assigned_at)}});
        lua_settop(L,top);
        return sound_finish_load(w,node,state);
    }catch(const std::exception&){lua_settop(L,top);audio_trace_failed(w,node,uri,"decode_request_rejected");return false;}
}
void sound_action(lua_State* L,World& w,Node& node,SoundPlayback& state,const std::string& action) {
    sound_advance(w,node,state);
    if(action=="Play"||action=="Resume") {
        const bool loaded=sound_load(L,w,node,state);
        if(action=="Play") {state.position=state.requested_position;state.loops=0;property_events(w,node,"TimePosition");}
        if(!loaded){state.pending_action=state.decoding.valid()?action:"";return;}
        state.previous=std::chrono::steady_clock::now();sound_playing(w,node,state,true);
        emit(w,node,action=="Play"?"Played":"Resumed",Json::array({state.asset}));
    }else {
        state.pending_action.clear();
        sound_playing(w,node,state,false);
        if(action=="Stop") {state.position=0;state.loops=0;property_events(w,node,"TimePosition");}
        emit(w,node,action=="Stop"?"Stopped":"Paused",Json::array({state.asset}));
    }
}
int sound_method(lua_State* L) {
    auto* handle=checked_handle(L,1);
    if(handle->node->class_name!="Sound"||handle->node->destroyed)return luaL_error(L,"live Sound required"),0;
    try {
        auto& state=sound_state(*handle->world,*handle->node);
        sound_action(L,*handle->world,*handle->node,state,lua_tostring(L,lua_upvalueindex(1)));
    }catch(const std::exception& e){return luaL_error(L,"Sound playback: %s",e.what()),0;}
    return 0;
}
bool sound_index(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name!="Sound")return false;
    for(const char* name:{"Play","Pause","Resume","Stop"}) {
        std::string legacy=name;legacy[0]=char(legacy[0]-'A'+'a');
        if(key==name||key==legacy){lua_pushstring(L,name);lua_pushcclosure(L,sound_method,name,1);return true;}
    }
    for(const char* name:{"Loaded","Played","Paused","Resumed","Stopped","Ended","DidLoop"})
        if(key==name){push_signal(L,signal_for(w,node,key));return true;}
    if(key!="TimePosition"&&key!="TimeLength"&&key!="IsLoaded"&&key!="Playing"&&key!="IsPlaying"&&key!="isPlaying"&&key!="IsPaused")return false;
    auto& state=sound_state(w,node);
    sound_load(L,w,node,state);
    sound_advance(w,node,state);
    audio_trace_observed(w,node,sound_asset(w,node),key.c_str(),state);
    if(key=="TimePosition")lua_pushnumber(L,state.position);
    else if(key=="TimeLength")lua_pushnumber(L,state.duration);
    else if(key=="IsLoaded")lua_pushboolean(L,state.loaded);
    else lua_pushboolean(L,key=="IsPaused"?!state.playing:state.playing);
    return true;
}
bool sound_write(lua_State* L,World& w,Node& node,const std::string& key) {
    if(node.class_name!="Sound")return false;
    if(key=="IsLoaded"||key=="TimeLength"||key=="IsPlaying"||key=="isPlaying"||key=="IsPaused")
        throw std::runtime_error("Sound playback property is read-only");
    if(key!="TimePosition"&&key!="SoundId"&&key!="PlaybackSpeed"&&key!="Playing"&&key!="Looped")return false;
    if(node.destroyed)throw std::runtime_error("Sound is destroyed");
    Json value;
    if(key=="SoundId") {size_t size=0;const char* text=luaL_checklstring(L,3,&size);if(size>16384)throw std::runtime_error("SoundId is too long");value=std::string(text,size);}
    else if(key=="Looped"||key=="Playing") {luaL_checktype(L,3,LUA_TBOOLEAN);value=bool(lua_toboolean(L,3));}
    else {double number=luaL_checknumber(L,3);if(!std::isfinite(number)||number<0)throw std::runtime_error("Sound playback value must be finite and nonnegative");if(key=="PlaybackSpeed"){number=float(number);if(!std::isfinite(number))throw std::runtime_error("PlaybackSpeed exceeds float range");}value=number;}
    std::vector<Json> mutations{Json{{"kind","set"},{"id",node.id},{"property",key},{"value",value}}};
    check_mutation_budget(w,mutations);
    auto& state=sound_state(w,node);sound_advance(w,node,state);
    w.local_properties[node.id][key]=value;append_mutations(w,std::move(mutations));
    if(key=="SoundId") {
        const bool loaded=state.loaded,playing=state.playing;
        state=SoundPlayback{};state.asset=value.get<std::string>();
        audio_trace_assign(w,node,state.asset,true);
        if(loaded){property_events(w,node,"IsLoaded");property_events(w,node,"TimeLength");}
        if(playing)for(const char* name:{"Playing","IsPlaying","IsPaused"})property_events(w,node,name);
        sound_load(L,w,node,state);
    }else if(key=="TimePosition") {state.position=state.requested_position=value.get<double>();state.previous=std::chrono::steady_clock::now();}
    else if(key=="Playing")sound_action(L,w,node,state,value.get<bool>()?"Resume":"Pause");
    property_events(w,node,key);return true;
}
void advance_sounds(World& w) {
    for(auto it=w.sounds.begin();it!=w.sounds.end();) {
        auto* node=find_node(w,it->first);
        if(!node||node->destroyed)it=w.sounds.erase(it);
        else {sound_finish_load(w,*node,it->second);sound_advance(w,*node,it->second);++it;}
    }
}
