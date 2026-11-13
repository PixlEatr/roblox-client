// Local synthetic input uses the same InputObject/state/event path as host
// input. It never changes the attached-device capability report.
Json virtual_input_prepare(lua_State*,World&,const char*,int);
void virtual_input_commit(World&,Json);
bool virtual_input_extended_index(lua_State*,const std::string&);
void virtual_input_record_gui(World&,const Json&);
constexpr const char* kVirtualInputMethods[]={"SendKeyEvent","SendMouseButtonEvent","SendMouseMoveEvent","SendMouseMoveDeltaEvent","SendMouseWheelEvent","SendTouchEvent","SendTextInputCharacterEvent"};
Node* virtual_input_layer(lua_State* L,World& w,int index) {
    if(lua_isnoneornil(L,index))return nullptr;
    auto* h=checked_handle(L,index);
    if(h->world!=&w||h->node->destroyed)throw std::runtime_error("live layer collector required");
    if(h->node->id==w.game_id)return nullptr;
    if(!class_is_a(w,h->node->class_name,"LayerCollector")&&h->node->class_name!="PlayerGui"&&h->node->class_name!="CoreGui")
        throw std::runtime_error("layerCollector must be a LayerCollector, GUI container, game, or nil");
    return h->node;
}
double virtual_input_number(lua_State* L,int index,bool integer=false) {
    const double value=luaL_checknumber(L,index);
    if(!std::isfinite(value)||!std::isfinite(static_cast<float>(value))||
       (integer&&(value<INT32_MIN||value>INT32_MAX)))throw std::runtime_error("input coordinate is out of range");
    return integer?std::trunc(value):static_cast<float>(value);
}
bool virtual_input_bool(lua_State* L,int index) {
    luaL_checktype(L,index,LUA_TBOOLEAN);return lua_toboolean(L,index);
}
bool virtual_input_contains(const GuiBox& b,double x,double y) {
    if(!b.attached||b.width<=0||b.height<=0)return false;
    const double angle=-b.rotation*3.14159265358979323846/180;
    const double dx=x-b.x-b.width*.5,dy=y-b.y-b.height*.5;
    const double px=dx*std::cos(angle)-dy*std::sin(angle)+b.width*.5;
    const double py=dx*std::sin(angle)+dy*std::cos(angle)+b.height*.5;
    return px>=0&&py>=0&&px<b.width&&py<b.height;
}
bool virtual_input_in_layer(World& w,Node& n,Node* layer) {
    return !layer||n.id==layer->id||is_descendant_of(w,n,layer->id);
}
Node* virtual_input_target(World& w,double x,double y,Node* layer) {
    GuiLayoutPass layout{w};Node* result=nullptr;double best_order=-INFINITY,best_z=-INFINITY;
    for(const auto& id:w.order) {
        auto* n=find_node(w,id);
        if(!n||n->destroyed||!gui_object(w,*n)||!surface_visible(w,*n)||!virtual_input_in_layer(w,*n,layer))continue;
        if(const auto* enabled=property_value(w,*n,"Interactable");enabled&&*enabled==false)continue;
        try {
            if(!virtual_input_contains(layout.box(*n),x,y))continue;
            bool clipped=false;double order=0,z=1;
            if(const auto* v=property_value(w,*n,"ZIndex");v&&v->is_number())z=v->get<double>();
            auto* parent=find_node(w,parent_id(w,*n));
            for(int depth=0;parent&&depth<128;++depth,parent=find_node(w,parent_id(w,*parent))) {
                if(parent->class_name=="ScreenGui") {
                    if(const auto* v=property_value(w,*parent,"DisplayOrder");v&&v->is_number())order=v->get<double>();
                    break;
                }
                if(gui_object(w,*parent))if(const auto* clip=property_value(w,*parent,"ClipsDescendants");clip&&*clip==true)
                    if(!virtual_input_contains(layout.box(*parent),x,y)){clipped=true;break;}
            }
            if(!clipped&&(order>best_order||(order==best_order&&z>=best_z))){result=n;best_order=order;best_z=z;}
        }catch(const std::exception&){/* A GUI with unresolved layout cannot receive a hit. */}
    }
    return result;
}
Node* virtual_input_pointer_target(World& w,Node* target) {
    for(int depth=0;target&&gui_object(w,*target)&&depth<128;++depth,target=find_node(w,parent_id(w,*target))) {
        if(class_is_a(w,target->class_name,"GuiButton")||target->class_name=="TextBox")return target;
        if(const auto* active=property_value(w,*target,"Active");active&&*active==true)return target;
        for(const auto& connection:w.connections)if(connection->connected&&connection->enabled&&connection->signal->node==target&&
            (connection->signal->key=="InputBegan"||connection->signal->key=="InputEnded"))return target;
    }
    return nullptr;
}
void virtual_input_hover(World& w,Node* target,double x,double y) {
    const std::string id=target?target->id:"";
    if(w.input.virtual_hover==id)return;
    if(auto* old=find_node(w,w.input.virtual_hover);old&&!old->destroyed)emit(w,*old,"MouseLeave",Json::array({x,y}));
    w.input.virtual_hover=id;
    if(target)emit(w,*target,"MouseEnter",Json::array({x,y}));
}
void virtual_input_pointer(World& w,Node* target,const std::string& identity,const std::string& input,
                           int type,int state,double x,double y,int repeat=1) {
    const bool down=state==0,up=state==2,cancel=state==3;
    Node* receiver=nullptr;
    if(down) {
        receiver=virtual_input_pointer_target(w,target);
        if(receiver)w.input.virtual_targets[identity]=receiver->id;
        if(type==0||type==7) {
            if(receiver&&receiver->class_name=="TextBox")capture_textbox(w,*receiver);
            else release_textbox(w,false);
        }
    }else if(auto it=w.input.virtual_targets.find(identity);it!=w.input.virtual_targets.end())receiver=find_node(w,it->second);
    if(receiver&&!receiver->destroyed) {
        const Json args=Json::array({Json{{"$ref",input}}});
        emit(w,*receiver,down?"InputBegan":state==1?"InputChanged":"InputEnded",args);
        if(class_is_a(w,receiver->class_name,"GuiButton")) {
            const bool left=type==0||type==7;
            if((left||type==1)&&(down||up))emit(w,*receiver,
                left?(down?"MouseButton1Down":"MouseButton1Up"):(down?"MouseButton2Down":"MouseButton2Up"),Json::array({x,y}));
            const auto* interactable=property_value(w,*receiver,"Interactable");
            if(up&&(!interactable||*interactable!=false)&&surface_visible(w,*receiver)&&
               target&&(target==receiver||is_descendant_of(w,*target,receiver->id))) {
                if(left||type==1)emit(w,*receiver,left?"MouseButton1Click":"MouseButton2Click");
                if(left)emit(w,*receiver,"Activated",Json::array({Json{{"$ref",input}},std::max(1,repeat)}));
            }
        }
    }
    if(up||cancel)w.input.virtual_targets.erase(identity);
}
void virtual_input_text(World& w,const std::string& value,Node* layer) {
    if(value.size()>65536||!valid_utf8(value.data(),value.size())||value.find('\0')!=std::string::npos)
        throw std::runtime_error("text input must be valid UTF-8 within 64 KiB");
    auto* box=focused_textbox(w);
    if(!box||!textbox_editable(w,*box)||!virtual_input_in_layer(w,*box,layer)||value.empty())return;
    const auto old=textbox_source(w,*box);
    int start=textbox_byte_boundary(old,w.text_cursor)-1,end=start;
    if(w.text_selection>=1){const int anchor=textbox_byte_boundary(old,w.text_selection)-1;start=std::min(start,anchor);end=std::max(end,anchor);}
    if(old.size()-(end-start)+value.size()>65536)throw std::runtime_error("text input exceeds the local editor limit");
    std::string text(old.substr(0,start));text+=value;text+=old.substr(end);
    write_textbox(w,*box,text);++w.text_editor_revision;
    update_textbox_selection(w,*box,start+static_cast<int>(value.size())+1,-1);
}
void virtual_input_edit_key(World& w,const std::string& key,Node* layer,const std::string& input) {
    auto* box=focused_textbox(w);if(!box||!virtual_input_in_layer(w,*box,layer))return;
    auto held=[&](const char* name){const auto item=input_enum_item("KeyCode",name);return w.input.contacts.count("8:"+std::to_string(kUsiExportEnums[input_enum_kind("KeyCode")].items[item].value))!=0;};
    const bool shift=held("LeftShift")||held("RightShift"),control=held("LeftControl")||held("RightControl")||held("LeftMeta")||held("RightMeta");
    const std::string text(textbox_source(w,*box));const int cursor=textbox_byte_boundary(text,w.text_cursor);
    const int anchor=w.text_selection>=1?textbox_byte_boundary(text,w.text_selection):cursor;
    auto previous=[&](int at){return textbox_byte_boundary(text,std::max(1,at-1));};
    auto next=[&](int at){int offset=std::min(int(text.size()),at);while(offset<int(text.size())&&(static_cast<unsigned char>(text[offset])&0xc0)==0x80)++offset;return offset+1;};
    if(key=="Escape"){release_textbox(w,false,Json{{"$ref",input}});return;}
    if(key=="Return"||key=="KeypadEnter") {
        const auto* multiline=property_value(w,*box,"MultiLine");
        if(multiline&&*multiline==true){if(textbox_editable(w,*box))virtual_input_text(w,"\n",layer);}
        else release_textbox(w,true,Json{{"$ref",input}});
        return;
    }
    if(key=="A"&&control){update_textbox_selection(w,*box,int(text.size())+1,1);return;}
    if(key=="Backspace"||key=="Delete") {
        if(!textbox_editable(w,*box))return;
        int begin=std::min(cursor,anchor),end=std::max(cursor,anchor);
        if(begin==end){if(key=="Backspace")begin=previous(begin);else end=next(end);}
        if(begin!=end){write_textbox(w,*box,text.substr(0,begin-1)+text.substr(end-1));++w.text_editor_revision;update_textbox_selection(w,*box,begin,-1);}return;
    }
    int target=cursor;
    if(key=="Left")target=!shift&&anchor!=cursor?std::min(anchor,cursor):previous(cursor);
    else if(key=="Right")target=!shift&&anchor!=cursor?std::max(anchor,cursor):next(cursor);
    else if(key=="Home") {
        const auto line=cursor>1?text.rfind('\n',cursor-2):std::string::npos;
        target=control||line==std::string::npos?1:int(line)+2;
    }else if(key=="End") {
        const auto line=text.find('\n',cursor-1);target=control||line==std::string::npos?int(text.size())+1:int(line)+1;
    }else return;
    update_textbox_selection(w,*box,target,shift?(w.text_selection>=1?anchor:cursor):-1);
}
int virtual_input_send(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;
    if(h->node->class_name!="VirtualInputManager"||h->node->destroyed)return luaL_error(L,"live VirtualInputManager receiver required"),0;
    const int operation=lua_tointeger(L,lua_upvalueindex(1));
    try {
        constexpr int counts[]={4,6,3,3,4,4,2};
        Json recorded=virtual_input_prepare(L,w,kVirtualInputMethods[operation],counts[operation]);
        Json event;Node* layer=nullptr;Node* target=nullptr;std::string identity,input;
        int type=8,state=0,repeat=1;double x=0,y=0;
        if(operation==0) {
            const bool down=virtual_input_bool(L,2);
            auto key=checked_input_enum(L,3,"KeyCode");const int code=key["$enum"]["value"].get<int>();
            if(code<=0||code>=1000)throw std::runtime_error("keyboard key required");
            const bool repeated=virtual_input_bool(L,4);layer=virtual_input_layer(L,w,5);
            if(down&&w.input.ignored_types.count(8))return 0;
            identity="8:"+std::to_string(code);const bool held=w.input.contacts.count(identity)!=0;
            if((!down&&!held)||(down&&held&&!repeated))return 0;
            state=down?(held?1:0):2;
            event={{"phase",state==0?"begin":state==1?"change":"end"},{"input_type","Keyboard"},{"key_code",key["$enum"]["name"]}};
            if(auto* box=focused_textbox(w);box&&virtual_input_in_layer(w,*box,layer))event["processed"]=true;
        }else if(operation==6) {
            size_t count=0;const char* text=luaL_checklstring(L,2,&count);layer=virtual_input_layer(L,w,3);
            if(w.input.ignored_types.count(20))return 0;
            virtual_input_text(w,std::string(text,count),layer);virtual_input_commit(w,std::move(recorded));return 0;
        }else if(operation==5) {
            const double id=luaL_checknumber(L,2),requested=luaL_checknumber(L,3);
            if(!std::isfinite(id)||std::trunc(id)!=id||std::abs(id)>9007199254740991.0)throw std::runtime_error("touch ID must be an exactly represented integer");
            if(!std::isfinite(requested)||std::trunc(requested)!=requested||requested<0||requested>3)throw std::runtime_error("touch state must be Begin, Change, End, or Cancel");
            state=static_cast<int>(requested);type=7;x=virtual_input_number(L,4);y=virtual_input_number(L,5);
            const std::string token="virtual:"+std::to_string(static_cast<int64_t>(id));identity="7:"+token;
            event={{"phase",state==0?"begin":state==1?"change":state==2?"end":"cancel"},{"input_type","Touch"},{"input_id",token},{"position",{x,y,0}}};
            auto held=w.input.contacts.find(identity);
            if(w.input.ignored_types.count(7)&&(state<2||held==w.input.contacts.end()))return 0;
            if(held!=w.input.contacts.end()) {
                input=held->second.node;
                event["delta"]={x-held->second.position[0],y-held->second.position[1],0};
            }
            target=virtual_input_target(w,x,y,nullptr);
        }else {
            x=virtual_input_number(L,2,operation==1);y=virtual_input_number(L,3,operation==1);
            if(operation==1) {
                const double button=luaL_checknumber(L,4);
                if(!std::isfinite(button)||std::trunc(button)!=button||button<0||button>2)throw std::runtime_error("mouse button must be 0, 1, or 2");
                type=static_cast<int>(button);const bool down=virtual_input_bool(L,5);layer=virtual_input_layer(L,w,6);
                const double count=luaL_optnumber(L,7,0);
                if(!std::isfinite(count)||count<0||count>INT32_MAX||std::trunc(count)!=count)throw std::runtime_error("repeatCount must be a nonnegative integer");
                repeat=std::max(1,static_cast<int>(count));identity=std::to_string(type)+":0";
                if(down&&w.input.ignored_types.count(type))return 0;
                auto held=w.input.contacts.find(identity);
                if((down&&held!=w.input.contacts.end())||(!down&&held==w.input.contacts.end()))return 0;
                if(held!=w.input.contacts.end())input=held->second.node;
                state=down?0:2;
                event={{"phase",down?"begin":"end"},{"input_type","MouseButton"+std::to_string(type+1)},{"position",{x,y,0}},{"mouse_location",{x,y}}};
            }else if(operation==4) {
                const bool forward=virtual_input_bool(L,4);layer=virtual_input_layer(L,w,5);type=3;state=1;
                const int steps=forward?1:-1;
                event={{"phase","change"},{"input_type","MouseWheel"},{"position",{x,y,steps}},{"delta",{0,0,steps}},{"mouse_location",{x,y}}};
            }else {
                layer=virtual_input_layer(L,w,4);type=4;state=1;
                double dx=x-w.input.location[0],dy=y-w.input.location[1];
                if(operation==3){dx=x;dy=y;x+=w.input.location[0];y+=w.input.location[1];}
                event={{"phase","change"},{"input_type","MouseMovement"},{"position",{x,y,0}},{"delta",{dx,dy,0}},{"mouse_location",{x,y}}};
            }
            target=virtual_input_target(w,x,y,layer);
        }
        if((type==3||type==4)&&w.input.ignored_types.count(type))return 0;
        if(state>=2&&w.input.tape.replaying&&!identity.empty()) {
            auto held=w.input.contacts.find(identity);
            auto owned=w.input.tape.owned_contacts.end();
            auto recorded=w.input.tape.owned_contacts.find(identity);
            if(held!=w.input.contacts.end()&&(recorded==owned||recorded->second!=held->second.node))return 0;
        }
        ensure_client_service(w,"UserInputService");
        if(type==3&&target) {
            const int steps=event["position"][2].get<int>();
            ++w.input.tape.internal_gui;
            try{render_gui_action(w,Json{{"kind","wheel"},{"id",target->id},{"position",{x,y}},{"steps",steps},{"delta",{0,-40*steps}}});}
            catch(...){--w.input.tape.internal_gui;throw;}
            --w.input.tape.internal_gui;
            w.input.virtual_mouse=true;virtual_input_hover(w,target,x,y);virtual_input_commit(w,std::move(recorded));return 0;
        }
        if(target) {
            auto* receiver=virtual_input_pointer_target(w,target);
            if(receiver)event["processed"]=true;
        }
        update_input_events(w,Json::array({event}),true);
        if(type==8&&state<2)virtual_input_edit_key(w,event["key_code"].get<std::string>(),layer,w.input.contacts.at(identity).node);
        if(type<=4)virtual_input_hover(w,target,x,y);
        if(type==4) {
            input=w.input.continuous.at(type);
            if(target)emit(w,*target,"InputChanged",Json::array({Json{{"$ref",input}}}));
        }else if(type<=2||type==7) {
            if(state==0)input=w.input.contacts.at(identity).node;
            virtual_input_pointer(w,target,identity,input,type,state,x,y,repeat);
        }
        virtual_input_commit(w,std::move(recorded));
    }catch(const std::exception& e){return luaL_error(L,"VirtualInputManager: %s",e.what()),0;}
    return 0;
}
int virtual_input_wait_continue(lua_State*,int){return 0;}
int virtual_input_wait(lua_State* L) {
    auto* h=checked_handle(L,1);auto& w=*h->world;
    if(h->node->class_name!="VirtualInputManager"||h->node->destroyed)return luaL_error(L,"live VirtualInputManager receiver required"),0;
    if(!w.scheduler||!lua_isyieldable(L))return luaL_error(L,"input barrier requires a scheduled yieldable thread"),0;
    if(w.input.virtual_waiters.size()>=128)return luaL_error(L,"input barrier limit exceeded"),0;
    lua_pushthread(L);const int ref=lua_ref(L,-1);lua_pop(L,1);
    w.input.virtual_waiters.push_back({L,ref,w.input.dispatch_epoch+2});
    w.scheduler->wait_signal(L);lua_settop(L,0);return lua_yield(L,0);
}
void progress_virtual_input_waiters(lua_State* L,World& w) {
    ++w.input.dispatch_epoch;
    for(auto it=w.input.virtual_waiters.begin();it!=w.input.virtual_waiters.end();) {
        const bool finished=w.scheduler->completion(it->thread)!=LUA_YIELD;
        // A callback may itself wait on this barrier. Wait for delivery turns,
        // not for arbitrary yielding user handlers to finish (a circular wait).
        if(finished||(it->epoch<=w.input.dispatch_epoch&&w.events.empty())) {
            if(!finished)w.scheduler->wake_signal(it->thread,[](lua_State*){return 0;});
            lua_unref(L,it->reference);it=w.input.virtual_waiters.erase(it);
        }else ++it;
    }
}
bool virtual_input_index(lua_State* L,const std::string& key) {
    for(int i=0;i<(int)std::size(kVirtualInputMethods);++i)if(key==kVirtualInputMethods[i]){lua_pushinteger(L,i);lua_pushcclosure(L,virtual_input_send,kVirtualInputMethods[i],1);return true;}
    if(key=="WaitForInputEventsProcessed"){lua_pushcclosurek(L,virtual_input_wait,key.c_str(),0,virtual_input_wait_continue);return true;}
    return virtual_input_extended_index(L,key);
}
#include "virtual_input_devices.inc"
#include "virtual_input_recording.inc"
