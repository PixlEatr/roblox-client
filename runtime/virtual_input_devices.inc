// Virtual gamepads and sensors share the local InputObject and action paths.
constexpr const char* kVirtualDeviceMethods[]={"HandleGamepadConnect","HandleGamepadDisconnect",
    "HandleGamepadButtonInput","HandleGamepadAxisInput","SendAccelerometerEvent",
    "SendGravityEvent","SendGyroscopeEvent","SendScroll","SetInputTypesToIgnore"};
Json virtual_enum_value(const char* kind,int value) {
    const auto& spec=kUsiExportEnums[input_enum_kind(kind)];
    for(size_t i=0;i<spec.count;++i)if(spec.items[i].value==value)return input_enum_json(kind,i);
    throw std::runtime_error(std::string("invalid ")+kind);
}
int virtual_device_id(lua_State* L,int index) {
    const double v=luaL_checknumber(L,index);
    if(!std::isfinite(v)||v<0||v>INT32_MAX||v!=std::trunc(v))throw std::runtime_error("device ID must be a nonnegative int32");
    return static_cast<int>(v);
}
Node& virtual_device_object(World& w,VirtualDeviceInput& device,int type,int key) {
    if(auto* existing=find_node(w,device.node);existing&&!existing->destroyed)return *existing;
    if(w.nodes.size()>=kMaxNodes)throw std::runtime_error("input instance limit exceeded");
    Node n;n.id=w.allocate_local_id();
    n.name="InputObject";n.class_name="InputObject";n.server_owned=false;
    n.properties={{"UserInputType",virtual_enum_value("UserInputType",type)},
        {"KeyCode",virtual_enum_value("KeyCode",key)},{"UserInputState",virtual_enum_value("UserInputState",device.state)},
        {"Position",{{"$vector3",device.position}}},{"Delta",{{"$vector3",{0,0,0}}}}};
    device.node=n.id;w.order.push_back(n.id);return w.nodes.emplace(n.id,std::move(n)).first->second;
}
void virtual_device_update(World& w,VirtualDeviceInput& device,int type,int key,int state,const std::array<float,3>& position) {
    auto& n=virtual_device_object(w,device,type,key);Json delta=Json::array();
    for(int i=0;i<3;++i)delta.push_back(position[i]-device.position[i]);
    Json fields={{"UserInputType",virtual_enum_value("UserInputType",type)},{"KeyCode",virtual_enum_value("KeyCode",key)},
        {"UserInputState",virtual_enum_value("UserInputState",state)},{"Position",{{"$vector3",position}}},{"Delta",{{"$vector3",delta}}}};
    for(auto it=fields.begin();it!=fields.end();++it){clear_local_property(w,n.id,it.key());if(n.properties[it.key()]!=it.value()){n.properties[it.key()]=it.value();property_events(w,n,it.key());}}
    device.position=position;device.state=state;
}
void virtual_gamepad_publish(World& w,int id,int key,int state,const std::array<float,3>& position) {
    auto found=w.input.gamepads.find(id);if(found==w.input.gamepads.end())throw std::runtime_error("gamepad is not connected");
    const int type=w.input.gamepad_types.at(id);
    if(w.input.ignored_types.count(type)&&state<2)return;
    context_reserve(w,1,w.context_actions.bindings.size());
    auto& device=found->second.at(key);
    virtual_device_update(w,device,type,key,state,position);
    publish_input_event(w,device.node,type,key,state,input_enum_item("UserInputType","Gamepad"+std::to_string(type-11)),false);
}
void virtual_gamepad_disconnect(World& w,int id) {
    auto found=w.input.gamepads.find(id);if(found==w.input.gamepads.end())return;
    const int old_preferred=preferred_input(w);
    const int type=w.input.gamepad_types.at(id);
    context_reserve(w,found->second.size(),found->second.size()*w.context_actions.bindings.size());
    for(auto& [key,input]:found->second)if(input.state<2||input.position!=std::array<float,3>{})
        virtual_gamepad_publish(w,id,key,3,{});
    w.input.gamepads.erase(found);w.input.gamepad_types.erase(id);w.input.navigation_gamepads.erase(type);
    auto* uis=find_node(w,w.services.at("UserInputService"));
    emit(w,*uis,"GamepadDisconnected",Json::array({virtual_enum_value("UserInputType",type)}));
    if(w.input.gamepads.empty()&&!w.input_capabilities.value("GamepadEnabled",false))property_events(w,*uis,"GamepadEnabled");
    if(old_preferred!=preferred_input(w))property_events(w,*uis,"PreferredInput");
}
int virtual_device_send(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;
    if(h->node->class_name!="VirtualInputManager"||h->node->destroyed)return luaL_error(L,"live VirtualInputManager receiver required"),0;
    const int operation=lua_tointeger(L,lua_upvalueindex(1));
    try {
        constexpr int counts[]={1,1,3,5,3,3,4,6,1};
        Json recorded=virtual_input_prepare(L,w,kVirtualDeviceMethods[operation],counts[operation]);
        auto& uis=*ensure_client_service(w,"UserInputService");
        if(operation<4) {
            const int id=virtual_device_id(L,2);
            if(operation==0) {
                if(w.input.gamepads.count(id))return 0;
                if(w.input.gamepads.size()>=8)throw std::runtime_error("at most eight gamepads can be connected");
                if(w.nodes.size()+18>kMaxNodes)throw std::runtime_error("input instance limit exceeded");
                int type=12;
                for(;type<20;++type){bool used=false;for(auto [_,assigned]:w.input.gamepad_types)used|=assigned==type;if(!used)break;}
                const bool enabled=input_capability(w,"GamepadEnabled");
                const int old_preferred=preferred_input(w);
                auto& pad=w.input.gamepads[id];w.input.gamepad_types[id]=type;w.input.navigation_gamepads.insert(type);
                for(int key=1000;key<=1017;++key)virtual_device_object(w,pad[key],type,key);
                if(!enabled)property_events(w,uis,"GamepadEnabled");
                if(old_preferred!=preferred_input(w))property_events(w,uis,"PreferredInput");
                emit(w,uis,"GamepadConnected",Json::array({virtual_enum_value("UserInputType",type)}));
            }else if(operation==1) {
                if(w.input.tape.playing&&w.input.tape.replaying&&!w.input.tape.owned_gamepads.count(id))return 0;
                virtual_gamepad_disconnect(w,id);
            }
            else {
                const auto value=checked_input_enum(L,3,"KeyCode");const int key=value["$enum"]["value"];
                if(key<1000||key>1017)throw std::runtime_error("physical gamepad KeyCode required");
                auto pad=w.input.gamepads.find(id);if(pad==w.input.gamepads.end())throw std::runtime_error("gamepad is not connected");
                if(operation==2) {
                    const double requested=luaL_checknumber(L,4);
                    if(!std::isfinite(requested)||requested!=std::trunc(requested)||requested<0||requested>3)throw std::runtime_error("gamepad state must be Begin, Change, End, or Cancel");
                    const int state=static_cast<int>(requested);
                    if(state>=2&&w.input.tape.playing&&w.input.tape.replaying&&
                       !w.input.tape.owned_gamepads.count(id)&&!w.input.tape.owned_gamepad_buttons.count({id,key}))return 0;
                    if(key>=1016)throw std::runtime_error("thumbsticks require axis input");
                    auto& old=pad->second.at(key);
                    if((state==0&&old.state<2)||(state>=2&&old.state>=2))return 0;
                    if(state==1&&old.state>=2)throw std::runtime_error("gamepad button must begin before change");
                    virtual_gamepad_publish(w,id,key,state,{0,0,state<2?1.f:0.f});
                }else {
                    std::array<float,3> p{};for(int i=0;i<3;++i)p[i]=std::clamp(float(virtual_input_number(L,4+i)),-1.f,1.f);
                    if(key!=1016&&key!=1017&&key!=1006&&key!=1007)throw std::runtime_error("axis input requires a thumbstick or trigger");
                    int state=1;
                    if(key==1006||key==1007){p={0,0,std::max(0.f,p[2])};const auto& old=pad->second.at(key);state=p[2]>0?(old.state>=2?0:1):2;}
                    virtual_gamepad_publish(w,id,key,state,p);
                }
            }
        }else if(operation<=6) {
            std::array<float,3> position{};
            std::array<double,9> rotation{1,0,0,0,1,0,0,0,1};
            if(operation==6) {
                double q[4];double length=0;for(int i=0;i<4;++i){q[i]=virtual_input_number(L,2+i);length=std::hypot(length,q[i]);}
                if(length==0)throw std::runtime_error("gyroscope quaternion must be nonzero");
                for(auto& v:q)v/=length;const double x=q[0],y=q[1],z=q[2],s=q[3];
                rotation={1-2*(y*y+z*z),2*(x*y-z*s),2*(x*z+y*s),2*(x*y+z*s),1-2*(x*x+z*z),2*(y*z-x*s),2*(x*z-y*s),2*(y*z+x*s),1-2*(x*x+y*y)};
                // Rotation InputObject uses XYZ Euler angles in radians.
                position={float(std::atan2(-rotation[5],rotation[8])),float(std::asin(std::clamp(rotation[2],-1.,1.))),float(std::atan2(-rotation[1],rotation[0]))};
            }else for(int i=0;i<3;++i)position[i]=virtual_input_number(L,2+i);
            const int type=operation==6?11:10;if(w.input.ignored_types.count(type))return 0;
            auto& device=w.input.sensors[operation-4];virtual_device_update(w,device,type,0,1,position);
            Json args=Json::array({Json{{"$ref",device.node}}});
            if(operation==6){w.input.sensor_rotation=rotation;args.push_back(Json{{"$cframe",{{"position",{0,0,0}},{"rotation",rotation}}}});}
            emit(w,uis,operation==4?"DeviceAccelerationChanged":operation==5?"DeviceGravityChanged":"DeviceRotationChanged",args);
        }else if(operation==7) {
            const double x=virtual_input_number(L,2),y=virtual_input_number(L,3),dx=virtual_input_number(L,4),dy=virtual_input_number(L,5);
            luaL_checktype(L,6,LUA_TTABLE);auto* layer=virtual_input_layer(L,w,7);
            bool start=false,gesture=false,momentum=false;
            lua_pushnil(L);while(lua_next(L,6)) {
                if(lua_type(L,-2)!=LUA_TSTRING||lua_type(L,-1)!=LUA_TBOOLEAN){lua_pop(L,2);throw std::runtime_error("scroll flags must have string keys and boolean values");}
                const std::string flag=lua_tostring(L,-2);const bool value=lua_toboolean(L,-1);
                if(flag=="StartOfScroll")start=value;else if(flag=="IsPartOfGesture")gesture=value;else if(flag=="IsResultOfMomentum")momentum=value;
                else {lua_pop(L,2);throw std::runtime_error("unexpected SendScroll flag: "+flag);}
                lua_pop(L,1);
            }
            if(std::abs(dx)>4096||std::abs(dy)>4096)throw std::runtime_error("scroll delta exceeds 4096 pixels");
            if(w.input.ignored_types.count(3))return 0;
            context_reserve(w,1,w.context_actions.bindings.size());
            if(!w.input.continuous.count(3)&&w.nodes.size()>=kMaxNodes)throw std::runtime_error("input instance limit exceeded");
            auto* target=virtual_input_target(w,x,y,layer);
            if(!start&&(gesture||momentum)) {
                auto prior=w.input.virtual_targets.find("scroll");
                if(prior!=w.input.virtual_targets.end())if(auto* node=find_node(w,prior->second);node&&!node->destroyed&&surface_visible(w,*node)&&virtual_input_in_layer(w,*node,layer))target=node;
            }
            auto* scroll=target;
            for(int depth=0;scroll&&scroll->class_name!="ScrollingFrame"&&depth<128;++depth)scroll=find_node(w,parent_id(w,*scroll));
            bool processed=false;
            if(scroll) {
                const auto* enabled=property_value(w,*scroll,"ScrollingEnabled");processed=enabled&&*enabled==true;
                ++w.input.tape.internal_gui;
                try{render_gui_action(w,Json{{"kind","scroll"},{"id",scroll->id},{"delta",{dx,dy}}});}
                catch(...){--w.input.tape.internal_gui;throw;}
                --w.input.tape.internal_gui;
            }
            update_input_events(w,Json::array({Json{{"phase","change"},{"input_type","MouseWheel"},
                {"position",{x,y,-dy/40.}},{"delta",{dx,dy,-dy/40.}},{"mouse_location",{x,y}},{"processed",processed}}}),true);
            if((gesture||momentum)&&target)w.input.virtual_targets["scroll"]=target->id;else w.input.virtual_targets.erase("scroll");
            virtual_input_hover(w,target,x,y);
        }else {
            luaL_checktype(L,2,LUA_TTABLE);std::set<int> ignored;
            const int size=lua_objlen(L,2);if(size>23)throw std::runtime_error("too many input types");
            int entries=0;lua_pushnil(L);
            while(lua_next(L,2)) {
                const double index=lua_type(L,-2)==LUA_TNUMBER?lua_tonumber(L,-2):0;
                if(index<1||index>size||index!=std::trunc(index)){lua_pop(L,2);throw std::runtime_error("input filter must be a dense array");}
                ++entries;lua_pop(L,1);
            }
            if(entries!=size)throw std::runtime_error("input filter must be a dense array");
            for(int i=1;i<=size;++i){lua_rawgeti(L,2,i);auto value=checked_input_enum(L,-1,"UserInputType");ignored.insert(value["$enum"]["value"].get<int>());lua_pop(L,1);}
            // End/cancel delivery is still allowed, so changing this filter can
            // never strand a previously pressed key, touch, or controller.
            w.input.ignored_types=std::move(ignored);
        }
        virtual_input_commit(w,std::move(recorded));
    }catch(const std::exception& e){return luaL_error(L,"VirtualInputManager: %s",e.what()),0;}
    return 0;
}
int virtual_device_query(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;
    if(h->node->class_name!="UserInputService"||h->node->destroyed)return luaL_error(L,"live UserInputService receiver required"),0;
    const int operation=lua_tointeger(L,lua_upvalueindex(1));
    try {
        if(operation>=7&&operation<=9) {
            const int sensor=operation-7;auto& input=w.input.sensors[sensor];
            auto& n=virtual_device_object(w,input,sensor==2?11:10,0);push_node(L,w,&n);
            if(sensor==2){push_json(L,w,Json{{"$cframe",{{"position",{0,0,0}},{"rotation",w.input.sensor_rotation}}}});return 2;}return 1;
        }
        if(operation==0||operation==10) {
            lua_newtable(L);int i=1;
            for(int type=12;type<20;++type)for(auto [_,assigned]:w.input.gamepad_types)if(type==assigned&&(operation==0||w.input.navigation_gamepads.count(type))){push_json(L,w,virtual_enum_value("UserInputType",type));lua_rawseti(L,-2,i++);}
            return 1;
        }
        const auto value=checked_input_enum(L,2,"UserInputType");const int type=value["$enum"]["value"];
        if(type<12||type>19)throw std::runtime_error("Gamepad1 through Gamepad8 required");
        int id=-1;for(auto [device,assigned]:w.input.gamepad_types)if(type==assigned)id=device;
        if(operation==1){lua_pushboolean(L,id>=0);return 1;}
        if(operation==11){lua_pushboolean(L,id>=0&&w.input.navigation_gamepads.count(type));return 1;}
        if(operation==12){const bool enabled=virtual_input_bool(L,3);if(enabled)w.input.navigation_gamepads.insert(type);else w.input.navigation_gamepads.erase(type);return 0;}
        if(operation==2||operation==3) {
            lua_newtable(L);int i=1;
            if(id>=0)for(auto& [key,input]:w.input.gamepads.at(id)) {
                if(operation==2)push_node(L,w,&virtual_device_object(w,input,type,key));else push_json(L,w,virtual_enum_value("KeyCode",key));
                lua_rawseti(L,-2,i++);
            }return 1;
        }
        const int key=checked_input_enum(L,3,"KeyCode")["$enum"]["value"];
        const bool supported=id>=0&&w.input.gamepads.at(id).count(key);
        lua_pushboolean(L,operation==4?supported:supported&&w.input.gamepads.at(id).at(key).state<2);return 1;
    }catch(const std::exception& e){return luaL_error(L,"UserInputService: %s",e.what()),0;}
}
bool virtual_input_device_index(lua_State* L,World& w,Node& n,const std::string& key) {
    for(auto name:{"GamepadConnected","GamepadDisconnected","DeviceAccelerationChanged","DeviceGravityChanged","DeviceRotationChanged"})
        if(key==name){push_signal(L,signal_for(w,n,key));return true;}
    constexpr const char* methods[]={"GetConnectedGamepads","GetGamepadConnected","GetGamepadState","GetSupportedGamepadKeyCodes",
        "GamepadSupports","IsGamepadButtonDown","", "GetDeviceAcceleration","GetDeviceGravity","GetDeviceRotation",
        "GetNavigationGamepads","IsNavigationGamepad","SetNavigationGamepad"};
    for(int i=0;i<(int)std::size(methods);++i)if(key==methods[i]&&!key.empty()){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_device_query,methods[i],1);return true;}
    return false;
}
