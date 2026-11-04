// Portable local recordings. This versioned JSON is Rove's format; Roblox's
// private benchmark file format is not inferred from method signatures.
constexpr size_t kVirtualTapeBytes=2*1024*1024,kVirtualTapeEvents=10000;
double virtual_tape_elapsed(std::chrono::steady_clock::time_point from) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now()-from).count();
}
Json virtual_input_prepare(lua_State* L,World& w,const char* method,int count) {
    if(lua_gettop(L)>count+1)throw std::runtime_error("too many input arguments");
    if(!w.input.tape.recording||w.input.tape.replaying)return nullptr;
    Json args=Json::array();
    for(int i=2;i<=count+1;++i) {
        if(lua_isnoneornil(L,i)){args.push_back(nullptr);continue;}
        if(std::string_view(method)=="SetInputTypesToIgnore"&&i==2&&lua_istable(L,i)) {
            Json items=Json::array();const int size=lua_objlen(L,i);
            if(size>23)throw std::runtime_error("too many input types");
            for(int n=1;n<=size;++n){lua_rawgeti(L,i,n);items.push_back(checked_input_enum(L,-1,"UserInputType"));lua_pop(L,1);}
            args.push_back(std::move(items));
        }else args.push_back(capture_value(L,w,i));
    }
    Json event={{"time",virtual_tape_elapsed(w.input.tape.started)},{"method",method},{"args",args}};
    if(w.input.tape.events.size()>=kVirtualTapeEvents||event.dump().size()>kVirtualTapeBytes-w.input.tape.bytes)
        throw std::runtime_error("virtual input recording limit exceeded; stop recording first");
    return event;
}
void virtual_input_commit(World& w,Json event) {
    auto& tape=w.input.tape;
    if(event.is_null()||!tape.recording||tape.replaying)return;
    const auto size=event.dump().size();
    if(tape.events.size()>=kVirtualTapeEvents||size>kVirtualTapeBytes-tape.bytes) {
        tape.last_error="virtual input recording limit exceeded";return;
    }
    tape.bytes+=size;tape.events.push_back(std::move(event));
}
void virtual_input_record_host(World& w,const Json& events) {
    if(w.input.tape.recording&&!w.input.tape.replaying)
        virtual_input_commit(w,Json{{"time",virtual_tape_elapsed(w.input.tape.started)},{"method","__host"},{"args",Json::array({events})}});
}
void virtual_input_record_gui(World& w,const Json& action) {
    if(w.input.tape.recording&&!w.input.tape.replaying&&!w.input.tape.internal_gui)
        virtual_input_commit(w,Json{{"time",virtual_tape_elapsed(w.input.tape.started)},{"method","__gui"},{"args",Json::array({action})}});
}
Json virtual_tape_document(World& w) {
    auto& t=w.input.tape;
    return Json{{"format","rove.virtual-input"},{"version",1},{"duration",t.recording?virtual_tape_elapsed(t.started):t.duration},
        {"additionalLuaState",t.additional_state},{"initialMousePosition",t.initial_mouse},{"events",t.events}};
}
bool virtual_replay_method(lua_State* L,const std::string& method) {
    for(int i=0;i<(int)std::size(kVirtualInputMethods);++i)if(method==kVirtualInputMethods[i]){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_input_send,kVirtualInputMethods[i],1);return true;}
    for(int i=0;i<(int)std::size(kVirtualDeviceMethods);++i)if(method==kVirtualDeviceMethods[i]){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_device_send,kVirtualDeviceMethods[i],1);return true;}
    return false;
}
void virtual_tape_validate(World& w,const Json& document) {
    if(!document.is_object()||document.value("format","")!="rove.virtual-input"||document.value("version",0)!=1)
        throw std::runtime_error("expected a rove.virtual-input version 1 recording");
    const auto& events=document.at("events");
    if(!events.is_array()||events.size()>kVirtualTapeEvents)throw std::runtime_error("invalid recording events");
    if(!document.at("duration").is_number())throw std::runtime_error("invalid recording duration");
    const double duration=document.at("duration").get<double>();
    if(!std::isfinite(duration)||duration<0||duration>3600)throw std::runtime_error("recordings must be at most one hour");
    if(!document.at("additionalLuaState").is_string()||document.at("additionalLuaState").get_ref<const std::string&>().size()>65536)
        throw std::runtime_error("invalid recording additional state");
    double last=0;
    if(document.contains("initialMousePosition")) {
        const auto& p=document["initialMousePosition"];
        if(!p.is_array()||p.size()!=2)throw std::runtime_error("invalid recording initial mouse position");
        for(const auto& component:p)if(!component.is_number()||!std::isfinite(component.get<float>()))throw std::runtime_error("invalid recording initial mouse position");
    }
    for(const auto& event:events) {
        if(!event.is_object()||!event.at("time").is_number()||!event.at("method").is_string()||!event.at("args").is_array())throw std::runtime_error("invalid recording event");
        const double time=event.at("time").get<double>();
        if(!std::isfinite(time)||time<last||time>duration)throw std::runtime_error("recording timestamps must be ordered within duration");last=time;
        const auto method=event.at("method").get<std::string>();const auto& args=event.at("args");int count=-1;
        constexpr int base_counts[]={4,6,3,3,4,4,2},device_counts[]={1,1,3,5,3,3,4,6,1};
        for(int i=0;i<(int)std::size(kVirtualInputMethods);++i)if(method==kVirtualInputMethods[i])count=base_counts[i];
        for(int i=0;i<(int)std::size(kVirtualDeviceMethods);++i)if(method==kVirtualDeviceMethods[i])count=device_counts[i];
        if(method=="__host"||method=="__gui")count=1;
        if(method=="sendRobloxEvent")count=3;if(method=="sendThemeChangeEvent")count=1;
        int minimum=count;
        if(method=="SendKeyEvent")minimum=3;
        if(method=="SendMouseButtonEvent")minimum=4;
        if(method=="SendMouseMoveEvent"||method=="SendMouseMoveDeltaEvent")minimum=2;
        if(method=="SendMouseWheelEvent")minimum=3;
        if(method=="SendTextInputCharacterEvent")minimum=1;
        if(method=="SendScroll")minimum=5;
        if(count<0||args.size()>size_t(count)||args.size()<size_t(minimum))throw std::runtime_error("unknown recording method or wrong argument count");
        auto number=[&](size_t i,double low=-double(std::numeric_limits<float>::max()),double high=double(std::numeric_limits<float>::max()),bool integer=false) {
            if(i>=args.size()||!args[i].is_number())throw std::runtime_error("recording argument must be numeric");
            const double v=args[i].get<double>();
            if(!std::isfinite(v)||v<low||v>high||(integer&&std::trunc(v)!=v))throw std::runtime_error("recording numeric argument is out of range");
        };
        auto boolean=[&](size_t i){if(i>=args.size()||!args[i].is_boolean())throw std::runtime_error("recording argument must be boolean");};
        auto string=[&](size_t i){if(i>=args.size()||!args[i].is_string()||args[i].get_ref<const std::string&>().size()>65536)throw std::runtime_error("recording string is invalid");};
        auto enumeration=[&](size_t i,const char* kind)->int {
            const auto& a=args.at(i);
            if(a.is_object()&&a.contains("$enum")) {
                const auto& e=a.at("$enum");if(e.at("type")!=kind||!e.at("value").is_number_integer())throw std::runtime_error("recording enum has wrong type");
                const int value=e.at("value").get<int>();virtual_enum_value(kind,value);return value;
            }
            if(a.is_string()){const int item=input_enum_item(kind,a.get<std::string>());return kUsiExportEnums[input_enum_kind(kind)].items[item].value;}
            if(a.is_number_integer()){const int value=a.get<int>();virtual_enum_value(kind,value);return value;}
            throw std::runtime_error("recording enum is invalid");
        };
        auto layer=[&](size_t i){if(i<args.size()&&!args[i].is_null()&&(!args[i].is_object()||!args[i].contains("$ref")))throw std::runtime_error("recording layer must be an instance reference");};
        if(method=="SendKeyEvent") {boolean(0);const int key=enumeration(1,"KeyCode");if(key<=0||key>=1000)throw std::runtime_error("recording requires a keyboard key");boolean(2);layer(3);}
        else if(method=="SendMouseButtonEvent") {number(0,INT32_MIN,INT32_MAX);number(1,INT32_MIN,INT32_MAX);number(2,0,2,true);boolean(3);layer(4);if(args.size()>5&&!args[5].is_null())number(5,0,INT32_MAX,true);}
        else if(method=="SendMouseMoveEvent"||method=="SendMouseMoveDeltaEvent"){number(0);number(1);layer(2);}
        else if(method=="SendMouseWheelEvent"){number(0);number(1);boolean(2);layer(3);}
        else if(method=="SendTouchEvent"){number(0,-9007199254740991.,9007199254740991.,true);number(1,0,3,true);number(2);number(3);}
        else if(method=="SendTextInputCharacterEvent"){string(0);layer(1);}
        else if(method=="HandleGamepadConnect"||method=="HandleGamepadDisconnect")number(0,0,INT32_MAX,true);
        else if(method=="HandleGamepadButtonInput"||method=="HandleGamepadAxisInput") {
            number(0,0,INT32_MAX,true);const int key=enumeration(1,"KeyCode");if(key<1000||key>1017)throw std::runtime_error("recording requires a gamepad key");
            if(method=="HandleGamepadButtonInput")number(2,0,3,true);else {number(2);number(3);number(4);}
        }else if(method=="SendAccelerometerEvent"||method=="SendGravityEvent"||method=="SendGyroscopeEvent") {
            for(size_t i=0;i<args.size();++i)number(i);
            if(method=="SendGyroscopeEvent"&&args[0]==0&&args[1]==0&&args[2]==0&&args[3]==0)throw std::runtime_error("recording quaternion must be nonzero");
        }else if(method=="SendScroll") {
            number(0);number(1);number(2,-4096,4096);number(3,-4096,4096);if(!args[4].is_object())throw std::runtime_error("recording scroll options must be a dictionary");layer(5);
            for(auto flag=args[4].begin();flag!=args[4].end();++flag)
                if((flag.key()!="StartOfScroll"&&flag.key()!="IsPartOfGesture"&&flag.key()!="IsResultOfMomentum")||!flag.value().is_boolean())throw std::runtime_error("invalid recording scroll flag");
        }
        else if(method=="SetInputTypesToIgnore") {if((!args[0].is_array()&&!(args[0].is_object()&&args[0].empty()))||args[0].size()>23)throw std::runtime_error("recording filter must be an array");}
        else if(method=="sendRobloxEvent"||method=="sendThemeChangeEvent")for(size_t i=0;i<args.size();++i)string(i);
        else if(method=="__host"&&(!args[0].is_array()||args[0].size()>128))throw std::runtime_error("recording host batch is invalid");
        else if(method=="__gui"&&!args[0].is_object())throw std::runtime_error("recording GUI action is invalid");
        // Restrict tagged values before push_json can construct runtime objects.
        for(const auto& arg:args)if(arg.is_object()) {
            if(arg.contains("$ref")) {
                if(arg.size()!=1||!arg["$ref"].is_string())throw std::runtime_error("invalid recording layer reference");
                auto* node=find_node(w,arg["$ref"].get<std::string>());if(!node||node->destroyed)throw std::runtime_error("recording layer no longer exists");
            }else if(arg.contains("$enum")) {
                const auto& e=arg["$enum"];const auto kind=e.at("type").get<std::string>();
                if((kind!="KeyCode"&&kind!="UserInputType")||!e.at("value").is_number_integer())throw std::runtime_error("invalid recording enum");
                virtual_enum_value(kind.c_str(),e.at("value").get<int>());
            }else if(method!="__gui"&&method!="SendScroll"&&!(method=="SetInputTypesToIgnore"&&arg.empty()))throw std::runtime_error("invalid recording argument");
        }
    }
}
void virtual_playback_stop(World& w,bool completed) {
    auto& t=w.input.tape;if(!t.playing)return;
    t.playing=false;t.replaying=true;
    // Release only input acquired by this playback, preserving unrelated keys.
    for(const auto& [id,node]:t.owned_contacts) {
        auto it=w.input.contacts.find(id);if(it==w.input.contacts.end())continue;
        if(it->second.node!=node)continue;
        const auto c=it->second;Json event={{"phase","cancel"},{"input_type",virtual_enum_value("UserInputType",c.type)["$enum"]["name"]}};
        if(c.type==8)event["key_code"]=virtual_enum_value("KeyCode",c.key)["$enum"]["name"];
        if(c.type==7){event["input_id"]=id.substr(2);event["position"]=c.position;}
        update_input_events(w,Json::array({event}),true);
        virtual_input_pointer(w,nullptr,id,c.node,c.type,3,c.position[0],c.position[1]);
    }
    for(auto [id,key]:t.owned_gamepad_buttons)if(w.input.gamepads.count(id)&&!t.owned_gamepads.count(id))
        virtual_gamepad_publish(w,id,key,3,{});
    for(int id:t.owned_gamepads)virtual_gamepad_disconnect(w,id);
    if(!t.owned_gui_pointer.empty()&&w.gui_pointer_target==t.owned_gui_pointer) {
        if(auto* input=find_node(w,w.gui_pointer_input)) {
            clear_local_property(w,input->id,"UserInputState");
            input->properties["UserInputState"]=virtual_enum_value("UserInputState",3);property_events(w,*input,"UserInputState");
            if(auto* receiver=find_node(w,w.gui_pointer_target);receiver&&!receiver->destroyed)
                emit(w,*receiver,"InputEnded",Json::array({Json{{"$ref",input->id}}}));
        }
        w.gui_pointer_target.clear();w.gui_pointer_input.clear();
    }
    t.owned_gui_pointer.clear();
    t.owned_contacts.clear();t.owned_gamepads.clear();t.owned_gamepad_buttons.clear();t.replaying=false;
    if(completed)if(auto* manager=ensure_client_service(w,"VirtualInputManager"))
        emit(w,*manager,"PlaybackCompleted",Json::array({t.play_additional}));
}
void virtual_start_playback(World& w,const std::string& content) {
    auto& t=w.input.tape;
    if(t.recording||t.playing)throw std::runtime_error("stop the current recording or playback first");
    if(content.size()>kVirtualTapeBytes)throw std::runtime_error("recording exceeds 2 MiB");
    const auto document=Json::parse(content);virtual_tape_validate(w,document);
    t.play_events=document.at("events");t.play_additional=document.at("additionalLuaState");t.play_duration=document.at("duration");
    t.cursor=0;t.play_started=std::chrono::steady_clock::now();t.next_epoch=w.input.dispatch_epoch+1;
    t.last_error.clear();t.playing=true;
    if(document.contains("initialMousePosition")) {
        for(int i=0;i<2;++i)w.input.location[i]=document["initialMousePosition"][i].get<float>();
        w.input.virtual_mouse=true;
    }
}
constexpr const char* kVirtualTapeMethods[]={"StartRecording","StopRecording","StartPlayingJSON","StartPlaying","StopPlaying","Dump","sendRobloxEvent","sendThemeChangeEvent"};
int virtual_tape_method(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;auto& t=w.input.tape;
    if(h->node->class_name!="VirtualInputManager"||h->node->destroyed)return luaL_error(L,"live VirtualInputManager receiver required"),0;
    const int op=lua_tointeger(L,lua_upvalueindex(1));
    try {
        if(op==0) {
            if(t.recording||t.playing)throw std::runtime_error("stop the current recording or playback first");
            t.events=Json::array();t.bytes=0;t.duration=0;t.last_error.clear();t.started=std::chrono::steady_clock::now();t.initial_mouse=w.input.location;t.recording=true;
        }else if(op==1) {
            if(!t.recording)return 0;
            t.duration=virtual_tape_elapsed(t.started);t.recording=false;
            emit(w,*h->node,"RecordingCompleted",Json::array({virtual_tape_document(w).dump()}));
        }else if(op==2||op==3) {
            size_t size;const char* text=luaL_checklstring(L,2,&size);std::string content(text,size);
            if(op==3) {
                // Use the existing VM workspace, never arbitrary host files.
                lua_getglobal(L,"readfile");if(!lua_isfunction(L,-1)){lua_pop(L,1);throw std::runtime_error("VM workspace readfile is unavailable");}
                lua_pushlstring(L,content.data(),content.size());
                if(lua_pcall(L,1,1,0)!=LUA_OK){const std::string error=lua_tostring(L,-1)?lua_tostring(L,-1):"read failed";lua_pop(L,1);throw std::runtime_error(error);}
                if(lua_type(L,-1)!=LUA_TSTRING){lua_pop(L,1);throw std::runtime_error("recording file must contain a string");}
                text=lua_tolstring(L,-1,&size);content.assign(text,size);lua_pop(L,1);
            }
            virtual_start_playback(w,content);
        }else if(op==4)virtual_playback_stop(w,false);
        else if(op==5) {
            auto report=virtual_tape_document(w);report["playing"]=t.playing;report["recording"]=t.recording;report["lastError"]=t.last_error;report["markers"]=t.markers;report["theme"]=t.theme;
            const auto text=report.dump();lua_getglobal(L,"print");lua_pushlstring(L,text.data(),text.size());
            if(lua_pcall(L,1,0,0)!=LUA_OK){lua_pop(L,1);throw std::runtime_error("recording diagnostic output failed");}
        }else {
            auto entry=virtual_input_prepare(L,w,kVirtualTapeMethods[op],op==6?3:1);
            Json fields=Json::array();for(int i=2;i<=(op==6?4:2);++i){size_t n;const char* s=luaL_checklstring(L,i,&n);if(n>4096||!valid_utf8(s,n))throw std::runtime_error("event marker must be valid UTF-8 within 4096 bytes");fields.push_back(std::string(s,n));}
            if(op==7)t.theme=fields[0].get<std::string>();
            if(t.markers.size()>=256)t.markers.erase(t.markers.begin());
            t.markers.push_back(Json{{"method",kVirtualTapeMethods[op]},{"args",fields}});
            virtual_input_commit(w,std::move(entry));
        }
    }catch(const std::exception& e){return luaL_error(L,"VirtualInputManager: %s",e.what()),0;}
    return 0;
}
bool virtual_input_extended_index(lua_State* L,const std::string& key) {
    auto* h=checked_handle(L,1);
    if(key=="AdditionalLuaState"){const auto& s=h->world->input.tape.additional_state;lua_pushlstring(L,s.data(),s.size());return true;}
    if(key=="PlaybackCompleted"||key=="RecordingCompleted"){push_signal(L,signal_for(*h->world,*h->node,key));return true;}
    for(int i=0;i<(int)std::size(kVirtualDeviceMethods);++i)if(key==kVirtualDeviceMethods[i]){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_device_send,kVirtualDeviceMethods[i],1);return true;}
    for(int i=0;i<(int)std::size(kVirtualTapeMethods);++i)if(key==kVirtualTapeMethods[i]){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_tape_method,kVirtualTapeMethods[i],1);return true;}
    return false;
}
bool virtual_input_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(n.class_name!="VirtualInputManager"||key!="AdditionalLuaState")return false;
    size_t size;const char* data=luaL_checklstring(L,3,&size);
    if(size>65536||!valid_utf8(data,size))throw std::runtime_error("AdditionalLuaState must be valid UTF-8 within 64 KiB");
    const std::string value(data,size);
    if(w.input.tape.additional_state!=value){w.input.tape.additional_state=value;property_events(w,n,key);}return true;
}
void progress_virtual_input_playback(lua_State* L,World& w) {
    auto& t=w.input.tape;
    if(!t.playing||w.input.dispatch_epoch<t.next_epoch)return;
    const double elapsed=virtual_tape_elapsed(t.play_started);
    try {
        if(t.cursor>=t.play_events.size()) {
            if(elapsed>=t.play_duration)virtual_playback_stop(w,true);return;
        }
        const auto& event=t.play_events[t.cursor];if(elapsed<event.at("time").get<double>())return;
        const auto method=event.at("method").get<std::string>();const auto& args=event.at("args");
        std::set<std::string> previous;for(const auto& [id,_]:w.input.contacts)previous.insert(id);
        std::set<int> pads;std::set<std::pair<int,int>> buttons;
        for(const auto& [id,keys]:w.input.gamepads){pads.insert(id);for(const auto& [key,input]:keys)if(input.state<2)buttons.insert({id,key});}
        t.replaying=true;
        if(method=="__host")update_input_events(w,args[0],true);
        else if(method=="__gui") {
            Json action=args[0];const auto kind=action.value("kind","");
            if(kind=="text_edit"||kind=="text_select"||kind=="text_blur") {
                action["revision"]=w.text_focus_revision;
                if(action.contains("editorRevision"))action["editorRevision"]=w.text_editor_revision;
            }
            const bool held=!w.gui_pointer_target.empty();
            render_gui_action(w,action);
            if(!held&&!w.gui_pointer_target.empty())t.owned_gui_pointer=w.gui_pointer_target;
            if(w.gui_pointer_target.empty())t.owned_gui_pointer.clear();
        }
        else {
            const int top=lua_gettop(L);
            if(!virtual_replay_method(L,method)) {
                lua_pushinteger(L,method=="sendRobloxEvent"?6:7);lua_pushcclosure(L,virtual_tape_method,method.c_str(),1);
            }
            push_node(L,w,ensure_client_service(w,"VirtualInputManager"));
            for(const auto& arg:args)push_json(L,w,arg);
            const int result=lua_pcall(L,static_cast<int>(args.size())+1,0,0);
            if(result!=LUA_OK){const std::string error=lua_tostring(L,-1)?lua_tostring(L,-1):"input playback failed";lua_settop(L,top);throw std::runtime_error(error);}
            lua_settop(L,top);
        }
        t.replaying=false;
        for(const auto& [id,input]:w.input.contacts)if(!previous.count(id))t.owned_contacts[id]=input.node;
        for(const auto& [id,keys]:w.input.gamepads){if(!pads.count(id))t.owned_gamepads.insert(id);for(const auto& [key,input]:keys)if(input.state<2&&!buttons.count({id,key}))t.owned_gamepad_buttons.insert({id,key});}
        ++t.cursor;t.next_epoch=w.input.dispatch_epoch+2;
    }catch(const std::exception& e) {
        t.replaying=false;t.last_error=e.what();
        try{virtual_playback_stop(w,false);}catch(const std::exception&){t.playing=false;t.replaying=false;}
    }
}
