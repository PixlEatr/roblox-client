// Local keyboard/mouse/touch input. Only the host supplies device events;
// scripts cannot make server receipts impersonate hardware state.
int input_enum_kind(const char* name) {
    for(int k=0;k<(int)std::size(kUsiExportEnums);++k)if(!strcmp(kUsiExportEnums[k].name,name))return k;
    throw std::runtime_error("missing input enum metadata");
}
int input_enum_item(const char* kind,const std::string& name) {
    const int k=input_enum_kind(kind);
    for(size_t i=0;i<kUsiExportEnums[k].count;++i)
        if(export_enum_name_matches(k,i,name.data(),name.size()))return static_cast<int>(i);
    throw std::runtime_error(std::string("unknown ")+kind+" name");
}
Json input_enum_json(const char* kind,int item) {
    const auto& entry=kUsiExportEnums[input_enum_kind(kind)].items[item];
    return Json{{"$enum",{{"type",kind},{"name",entry.name},{"value",entry.value}}}};
}
Json checked_input_enum(lua_State* L,int index,const char* name) {
    const int kind=input_enum_kind(name);int item=-1;
    if(has_meta(L,index,kUsiExportItemMeta)) {
        auto* e=static_cast<UsiExportItemRef*>(lua_touserdata(L,index));
        if(e->kind==kind)item=e->item;
    } else if(lua_type(L,index)==LUA_TSTRING) {
        size_t n;const char* s=lua_tolstring(L,index,&n);
        for(size_t i=0;i<kUsiExportEnums[kind].count;++i)if(export_enum_name_matches(kind,i,s,n)){item=static_cast<int>(i);break;}
    } else if(lua_type(L,index)==LUA_TNUMBER) {
        const double value=lua_tonumber(L,index);
        for(size_t i=0;i<kUsiExportEnums[kind].count;++i)if(value==kUsiExportEnums[kind].items[i].value){item=static_cast<int>(i);break;}
    }
    if(item<0)throw std::runtime_error(std::string("unable to cast value to ")+name);
    return input_enum_json(name,item);
}
const Json* input_default(const Node& n,const std::string& key) {
    if(n.class_name!="UserInputService")return nullptr;
    static const Json behavior=input_enum_json("MouseBehavior",0),enabled=true,sensitivity=1.0;
    if(key=="MouseBehavior")return &behavior;
    if(key=="MouseIconEnabled")return &enabled;
    if(key=="MouseDeltaSensitivity")return &sensitivity;
    return nullptr;
}
bool input_event_name(const std::string& key) {
    if(key=="WindowFocused"||key=="WindowFocusReleased")return true;
    return key=="InputBegan"||key=="InputChanged"||key=="InputEnded"||key=="LastInputTypeChanged"||
        key=="TouchStarted"||key=="TouchMoved"||key=="TouchEnded";
}
bool mouse_event_name(const std::string& key) {
    return key=="Button1Down"||key=="Button1Up"||key=="Button2Down"||key=="Button2Up"||
        key=="Move"||key=="WheelForward"||key=="WheelBackward";
}
Node* local_player_mouse(World& w) {
    auto* player=find_node(w,w.local_player);
    if(!player||player->destroyed)return nullptr;
    auto found=w.client_services.player_mice.find(player->id);
    if(found==w.client_services.player_mice.end())return nullptr;
    auto* mouse=find_node(w,found->second);
    return mouse&&!mouse->destroyed?mouse:nullptr;
}
void publish_mouse_event(World& w,int type,int state,double wheel=0) {
    // GetMouse owns the instance lifetime; hardware input never creates one.
    auto* mouse=local_player_mouse(w);if(!mouse)return;
    const char* signal=nullptr;
    if(type==0)signal=state==0?"Button1Down":state>=2?"Button1Up":nullptr;
    else if(type==1)signal=state==0?"Button2Down":state>=2?"Button2Up":nullptr;
    else if(type==4&&state==1)signal="Move";
    else if(type==3&&state==1)signal=wheel>0?"WheelForward":wheel<0?"WheelBackward":nullptr;
    if(signal)emit(w,*mouse,signal,Json::array());
}
int input_query(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;
    if(h->node->class_name!="UserInputService"||h->node->destroyed)return luaL_error(L,"live UserInputService receiver required"),0;
    const int operation=lua_tointeger(L,lua_upvalueindex(1));
    try {
        if(operation<=1) {
            auto value=checked_input_enum(L,2,operation==0?"KeyCode":"UserInputType");
            const int wanted=value["$enum"]["value"].get<int>();bool pressed=false;
            for(const auto& [id,c]:w.input.contacts)
                if(operation==0?(c.type==8&&c.key==wanted):(c.type>=0&&c.type<=2&&c.type==wanted)){pressed=true;break;}
            lua_pushboolean(L,pressed);return 1;
        }
        if(operation<=3) {
            lua_newtable(L);int index=1;
            for(const auto& [id,c]:w.input.contacts)if(operation==2?c.type==8:c.type>=0&&c.type<=2) {
                push_node(L,w,find_node(w,c.node));lua_rawseti(L,-2,index++);
            }
            return 1;
        }
        if(operation==4) {
            const auto& spec=kUsiExportEnums[input_enum_kind("UserInputType")];
            for(size_t i=0;i<spec.count;++i)if(spec.items[i].value==w.input.last_type){push_json(L,w,input_enum_json("UserInputType",i));return 1;}
        }
        if(operation==5) {push_json(L,w,Json{{"$vector2",{w.input.location[0],w.input.location[1]}}});return 1;}
        if(operation==6) {
            const auto* behavior=property_value(w,*h->node,"MouseBehavior");
            const bool locked=behavior&&behavior->at("$enum").at("value")!=0;
            const auto* configured=property_value(w,*h->node,"MouseDeltaSensitivity");
            const float sensitivity=configured?static_cast<float>(json_number(*configured)):1.0f;
            const float x=locked?w.input.frame_delta[0]*sensitivity:0.0f;
            const float y=locked?w.input.frame_delta[1]*sensitivity:0.0f;
            return push_engine_vector2(L,x,y);
        }
        throw std::runtime_error("invalid input query");
    }catch(const std::exception& e){luaL_error(L,"UserInputService: %s",e.what());return 0;}
}
bool input_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(class_is_a(w,n.class_name,"Mouse")&&mouse_event_name(key)) {
        push_signal(L,signal_for(w,n,key));return true;
    }
    if(n.class_name!="UserInputService")return false;
    if(input_event_name(key)){push_signal(L,signal_for(w,n,key));return true;}
    constexpr const char* methods[]={"IsKeyDown","IsMouseButtonPressed","GetKeysPressed","GetMouseButtonsPressed","GetLastInputType","GetMouseLocation","GetMouseDelta"};
    for(int i=0;i<(int)std::size(methods);++i)if(key==methods[i]) {
        lua_pushinteger(L,i);lua_pushcclosure(L,input_query,methods[i],1);return true;
    }
    if(const Json* fallback=input_default(n,key)){const auto* value=property_value(w,n,key);push_json(L,w,value?*value:*fallback);return true;}
    return false;
}
bool input_write(lua_State* L,World& w,Node& n,const std::string& key) {
    Json value;
    if(n.class_name=="UserInputService") {
        if(key=="MouseBehavior")value=checked_input_enum(L,3,"MouseBehavior");
        else if(key=="MouseDeltaSensitivity") {
            // Studio uses numeric conversion (other variants become zero),
            // float32 precision and a signed +/-10 clamp; NaN is retained.
            value=encode_number(static_cast<float>(std::clamp(lua_tonumber(L,3),-10.0,10.0)));
        }
        else if(key=="MouseIconEnabled") {
            if(lua_type(L,3)!=LUA_TBOOLEAN)throw std::runtime_error("MouseIconEnabled must be boolean");value=bool(lua_toboolean(L,3));
        } else return false;
    } else if(n.class_name=="InputObject") {
        if(key=="KeyCode")value=checked_input_enum(L,3,"KeyCode");
        else if(key=="UserInputState"||key=="UserInputType")value=checked_input_enum(L,3,key.c_str());
        else if(key=="Position"||key=="Delta") {
            if(!lua_isvector(L,3))throw std::runtime_error("input vector must be Vector3");
            const float* v=lua_tovector(L,3);for(int i=0;i<3;++i)if(!std::isfinite(v[i]))throw std::runtime_error("input vector must be finite");
            value=Json{{"$vector3",{v[0],v[1],v[2]}}};
        } else return false;
    } else return false;
    const auto* previous=property_value(w,n,key);
    if(!previous||*previous!=value){clear_local_property(w,n.id,key);n.properties[key]=std::move(value);property_events(w,n,key);}
    return true;
}
struct HostInputEvent {
    std::string phase,identity;int type=22,key=0,type_item=0,key_item=0,state=0;bool processed=false,continuous=false;
    Json position,delta;std::array<float,2> mouse{};bool has_mouse=false;
};
Json input_vector(const Json& event,const char* field) {
    if(!event.contains(field))return nullptr;
    const auto& v=event.at(field);if(!v.is_array()||v.size()!=3)throw std::runtime_error("input vector requires three components");
    Json result=Json::array();for(const auto& component:v) {
        if(!component.is_number()||!std::isfinite(component.get<double>())||!std::isfinite(static_cast<float>(component.get<double>())))throw std::runtime_error("input vector must fit finite float32");
        result.push_back(static_cast<float>(component.get<double>()));
    }
    return Json{{"$vector3",result}};
}
// Shared dispatch for validated host device events and GUI input. Device
// attachment/capability leases are owned by the caller, not signal delivery.
void publish_input_event(World& w,const std::string& id,int type,int key,int state,int type_item,bool processed,double wheel=0) {
    auto service=w.services.find("UserInputService");
    Node* uis=service==w.services.end()?nullptr:find_node(w,service->second);
    if(!uis||uis->destroyed)throw std::runtime_error("input service unavailable");
    if(w.input.last_type!=type){w.input.last_type=type;emit(w,*uis,"LastInputTypeChanged",Json::array({input_enum_json("UserInputType",type_item)}));}
    const Json args=Json::array({Json{{"$ref",id}},processed});
    emit(w,*uis,state==0?"InputBegan":state==1?"InputChanged":"InputEnded",args);
    if(type==9&&(state==0||state==2))emit(w,*uis,state==0?"WindowFocused":"WindowFocusReleased",Json::array());
    if(type==7)emit(w,*uis,state==0?"TouchStarted":state==1?"TouchMoved":"TouchEnded",args);
    // Legacy signals observe delivered mouse input without a processed argument
    // or a sink result. Use the validated host values, not script-writable fields.
    publish_mouse_event(w,type,state,wheel);
    queue_context_input(w,id,type,key,state,processed);
}
void update_input_events(World& w,const Json& batch) {
    if(!batch.is_array()||batch.size()>128)throw std::runtime_error("input batch must contain at most 128 events");
    context_reserve(w,batch.size(),batch.size()*w.context_actions.bindings.size());
    std::vector<HostInputEvent> events;auto active=w.input.contacts;auto continuous=w.input.continuous;
    size_t created=0;
    for(const auto& value:batch) {
        if(!value.is_object()||value.size()>9)throw std::runtime_error("invalid input event");
        for(auto field=value.begin();field!=value.end();++field)if(field.key()!="phase"&&field.key()!="input_type"&&field.key()!="key_code"&&field.key()!="input_id"&&field.key()!="position"&&field.key()!="delta"&&field.key()!="mouse_location"&&field.key()!="processed")throw std::runtime_error("unknown input event field");
        HostInputEvent e;e.phase=value.at("phase").get<std::string>();
        e.state=e.phase=="begin"?0:e.phase=="change"?1:e.phase=="end"?2:e.phase=="cancel"?3:-1;
        if(e.state<0)throw std::runtime_error("invalid input phase");
        e.type_item=input_enum_item("UserInputType",value.at("input_type").get<std::string>());
        e.type=kUsiExportEnums[input_enum_kind("UserInputType")].items[e.type_item].value;
        if(!(e.type>=0&&e.type<=4)&&e.type!=7&&e.type!=8&&e.type!=9)throw std::runtime_error("input bridge supports keyboard, mouse, touch and focus events");
        const char* capability=e.type==8?"KeyboardEnabled":e.type==7?"TouchEnabled":"MouseEnabled";
        if(e.type!=9&&!input_capability(w,capability))throw std::runtime_error("input device capability is disabled");
        if(e.type==9&&e.state!=0&&e.state!=2)throw std::runtime_error("window focus requires begin or end phase");
        e.key_item=input_enum_item("KeyCode",value.value("key_code",std::string("None")));
        e.key=kUsiExportEnums[input_enum_kind("KeyCode")].items[e.key_item].value;
        if((e.type==8&&e.key==0)||(e.type!=8&&e.key!=0))throw std::runtime_error("key_code must identify a keyboard key only");
        std::string token=value.value("input_id",std::string());
        if(e.type==7&&(token.empty()||token.size()>64))throw std::runtime_error("touch requires input_id of 1 to 64 bytes");
        if(e.type!=7&&!token.empty())throw std::runtime_error("input_id is only supported for touch");
        e.identity=std::to_string(e.type)+":"+(e.type==7?token:std::to_string(e.key));
        e.continuous=e.type==3||e.type==4;
        if(e.continuous) {
            if(e.state!=1)throw std::runtime_error("mouse movement and wheel require change phase");
            if(!continuous.count(e.type)){continuous[e.type]="";++created;}
        } else {
            if(e.state==0) {
                if(active.count(e.identity))throw std::runtime_error("input is already pressed");
                active[e.identity]=InputContact{"",e.type,e.key};++created;
                if(active.size()>128)throw std::runtime_error("pressed input limit exceeded");
            } else {
                if(!active.count(e.identity))throw std::runtime_error("input must begin before change or end");
                if(e.state>=2)active.erase(e.identity);
            }
        }
        e.position=input_vector(value,"position");e.delta=input_vector(value,"delta");
        if(value.contains("processed")){if(!value["processed"].is_boolean())throw std::runtime_error("processed must be boolean");e.processed=value["processed"].get<bool>();}
        if(value.contains("mouse_location")) {
            const auto& pos=value["mouse_location"];if(e.type>4||!pos.is_array()||pos.size()!=2)throw std::runtime_error("mouse_location requires two mouse coordinates");
            for(int i=0;i<2;++i){if(!pos[i].is_number()||!std::isfinite(pos[i].get<double>())||!std::isfinite(static_cast<float>(pos[i].get<double>())))throw std::runtime_error("mouse location must fit finite float32");e.mouse[i]=static_cast<float>(pos[i].get<double>());}e.has_mouse=true;
        }
        events.push_back(std::move(e));
    }
    if(created>kMaxNodes-w.nodes.size())throw std::runtime_error("input instance limit exceeded");
    auto service=w.services.find("UserInputService");Node* uis=service==w.services.end()?nullptr:find_node(w,service->second);
    if(!uis||uis->destroyed)throw std::runtime_error("input service unavailable");
    for(const auto& e:events) {
        std::string id=e.continuous?w.input.continuous[e.type]:e.state==0?"":w.input.contacts.at(e.identity).node;
        if(id.empty()) {
            Node node;do{node.id="__local_input_"+std::to_string(w.next_local_id++);}while(w.nodes.count(node.id));
            node.name="InputObject";node.class_name="InputObject";node.server_owned=false;
            node.properties={{"KeyCode",input_enum_json("KeyCode",e.key_item)},{"UserInputType",input_enum_json("UserInputType",e.type_item)},
                {"UserInputState",input_enum_json("UserInputState",4)},{"Position",{{"$vector3",{0,0,0}}}},{"Delta",{{"$vector3",{0,0,0}}}}};
            id=node.id;w.nodes.emplace(id,std::move(node));w.order.push_back(id);
            if(e.continuous)w.input.continuous[e.type]=id;else w.input.contacts[e.identity]=InputContact{id,e.type,e.key};
        }
        auto& node=w.nodes.at(id);
        auto set=[&](const char* field,const Json& v){if(v.is_null())return;clear_local_property(w,id,field);if(node.properties[field]!=v){node.properties[field]=v;property_events(w,node,field);}};
        set("UserInputType",input_enum_json("UserInputType",e.type_item));
        set("KeyCode",input_enum_json("KeyCode",e.key_item));
        set("UserInputState",input_enum_json("UserInputState",e.state));
        set("Position",e.position);
        Json delta=e.delta.is_null()?Json{{"$vector3",{0,0,0}}}:e.delta;
        if(e.type==4) {
            const auto* configured=property_value(w,*uis,"MouseDeltaSensitivity");
            const float sensitivity=configured?static_cast<float>(json_number(*configured)):1.0f;
            for(int i=0;i<3;++i)delta["$vector3"][i]=encode_number(static_cast<float>(json_number(delta["$vector3"][i]))*sensitivity);
        }
        set("Delta",delta);
        if(e.state>=2)w.input.contacts.erase(e.identity);
        if(e.has_mouse)w.input.location=e.mouse;
        if(e.type==4&&!e.delta.is_null())for(int i=0;i<2;++i)w.input.pending_delta[i]=static_cast<float>(std::clamp(double(w.input.pending_delta[i])+e.delta["$vector3"][i].get<double>(),-double(std::numeric_limits<float>::max()),double(std::numeric_limits<float>::max())));
        const Json& wheel=e.position.is_null()?e.delta:e.position;
        const double wheel_steps=e.type==3&&!wheel.is_null()?wheel["$vector3"][2].get<double>():0;
        publish_input_event(w,id,e.type,e.key,e.state,e.type_item,e.processed,wheel_steps);
    }
}
void cancel_disabled_inputs(World& w) {
    auto service=w.services.find("UserInputService");
    Node* uis=service==w.services.end()?nullptr:find_node(w,service->second);
    for(auto it=w.input.contacts.begin();it!=w.input.contacts.end();) {
        const auto contact=it->second;
        // Window focus is independent of whether a mouse is attached.
        if(contact.type==9){++it;continue;}
        const char* capability=contact.type==8?"KeyboardEnabled":contact.type==7?"TouchEnabled":"MouseEnabled";
        if(input_capability(w,capability)){++it;continue;}
        auto* node=find_node(w,contact.node);it=w.input.contacts.erase(it);
        if(node) {
            clear_local_property(w,node->id,"UserInputState");
            node->properties["UserInputState"]=input_enum_json("UserInputState",3);
            property_events(w,*node,"UserInputState");
            if(uis&&!uis->destroyed) {
                const Json args=Json::array({Json{{"$ref",node->id}},false});
                emit(w,*uis,"InputEnded",args);
                if(contact.type==7)emit(w,*uis,"TouchEnded",args);
                publish_mouse_event(w,contact.type,3);
                queue_context_input(w,node->id,contact.type,contact.key,3,false);
            }
        }
    }
    if(!input_capability(w,"MouseEnabled")){w.input.pending_delta={};w.input.frame_delta={};}
}
void begin_input_frame(World& w) {
    w.input.frame_delta=w.input.pending_delta;w.input.pending_delta={};
}
