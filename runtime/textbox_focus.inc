bool textbox_focusable(World& w,Node& n) {
    const auto* interactable=property_value(w,n,"Interactable");
    return n.class_name=="TextBox"&&(!interactable||*interactable!=false)&&surface_visible(w,n);
}
void textbox_service_event(World& w,Node& n,const char* signal) {
    const auto service=w.services.find("UserInputService");
    if(service!=w.services.end())if(auto* input=find_node(w,service->second))
        emit(w,*input,signal,Json::array({Json{{"$ref",n.id}}}));
}
bool textbox_selection_key(const Node& n,const std::string& key) {
    return n.class_name=="TextBox"&&(key=="CursorPosition"||key=="SelectionStart");
}
std::string_view textbox_source(World& w,Node& n) {
    const auto* value=property_value(w,n,"Text");
    return value&&value->is_string()?std::string_view(value->get_ref<const std::string&>()):std::string_view{};
}
int textbox_byte_boundary(std::string_view text,int position) {
    size_t offset=std::min(static_cast<size_t>(std::max(1,position)-1),text.size());
    while(offset>0&&offset<text.size()&&(static_cast<unsigned char>(text[offset])&0xc0)==0x80)--offset;
    return static_cast<int>(offset)+1;
}
bool update_textbox_selection(World& w,Node& n,int cursor,int selection) {
    if(cursor>=1) {
        const auto text=textbox_source(w,n);
        cursor=textbox_byte_boundary(text,cursor);
        if(selection!=-1)selection=textbox_byte_boundary(text,selection);
        if(selection==cursor)selection=-1;
    }
    const bool cursor_changed=cursor!=w.text_cursor,selection_changed=selection!=w.text_selection;
    w.text_cursor=cursor;
    w.text_selection=selection;
    if(cursor_changed)property_events(w,n,"CursorPosition");
    if(selection_changed)property_events(w,n,"SelectionStart");
    return cursor_changed||selection_changed;
}
void authored_textbox_text_changed(World& w,Node& n) {
    if(w.focused_textbox!=n.id)return;
    ++w.text_editor_revision;
    update_textbox_selection(w,n,w.text_cursor,w.text_selection);
}
void release_textbox(World& w,bool submitted,Json input=nullptr) {
    if(w.focused_textbox.empty())return;
    auto* n=find_node(w,w.focused_textbox);
    w.focused_textbox.clear();
    ++w.text_focus_revision;
    if(!n){w.text_cursor=-1;w.text_selection=-1;return;}
    update_textbox_selection(w,*n,-1,-1);
    emit(w,*n,"FocusLost",Json::array({submitted,std::move(input)}));
    textbox_service_event(w,*n,"TextBoxFocusReleased");
}
Node* focused_textbox(World& w) {
    auto* n=find_node(w,w.focused_textbox);
    if(n&&textbox_focusable(w,*n))return n;
    release_textbox(w,false);
    return nullptr;
}
bool textbox_editable(World& w,Node& n) {
    const auto* editable=property_value(w,n,"TextEditable");
    return !editable||*editable!=false;
}
void write_textbox(World& w,Node& n,const Json& text) {
    const auto* previous=property_value(w,n,"Text");
    if(previous&&*previous==text)return;
    const auto old_content=text_content(w,n);
    w.local_properties[n.id]["Text"]=text;
    property_events(w,n,"Text");
    if(old_content!=text_content(w,n))property_events(w,n,"ContentText");
}
void capture_textbox(World& w,Node& n) {
    if(!textbox_focusable(w,n)||focused_textbox(w)==&n)return;
    release_textbox(w,false);
    w.focused_textbox=n.id;
    ++w.text_focus_revision;
    w.text_editor_revision=0;
    const auto* clear=property_value(w,n,"ClearTextOnFocus");
    if(clear&&*clear==true&&textbox_editable(w,n))write_textbox(w,n,"");
    update_textbox_selection(w,n,static_cast<int>(textbox_source(w,n).size())+1,-1);
    emit(w,n,"Focused");
    textbox_service_event(w,n,"TextBoxFocused");
}
int textbox_capture_focus(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="TextBox"){luaL_error(L,"TextBox required");return 0;}
    capture_textbox(*h->world,*h->node);
    return 0;
}
int textbox_release_focus(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="TextBox"){luaL_error(L,"TextBox required");return 0;}
    if(!lua_isnoneornil(L,2))luaL_checktype(L,2,LUA_TBOOLEAN);
    if(focused_textbox(*h->world)==h->node)release_textbox(*h->world,lua_toboolean(L,2));
    return 0;
}
int textbox_is_focused(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="TextBox"){luaL_error(L,"TextBox required");return 0;}
    lua_pushboolean(L,focused_textbox(*h->world)==h->node);
    return 1;
}
int input_focused_textbox(lua_State* L) {
    auto* h=checked_handle(L,1);
    if(h->node->class_name!="UserInputService"){luaL_error(L,"UserInputService required");return 0;}
    if(auto* n=focused_textbox(*h->world))push_node(L,*h->world,n);
    else lua_pushnil(L);
    return 1;
}
bool textbox_focus_index(lua_State* L,World& w,Node& n,const std::string& key) {
    if(textbox_selection_key(n,key)) {
        lua_pushinteger(L,focused_textbox(w)==&n?(key=="CursorPosition"?w.text_cursor:w.text_selection):-1);
        return true;
    }
    if(n.class_name=="TextBox") {
        if(key=="CaptureFocus"){lua_pushcfunction(L,textbox_capture_focus,"CaptureFocus");return true;}
        if(key=="ReleaseFocus"){lua_pushcfunction(L,textbox_release_focus,"ReleaseFocus");return true;}
        if(key=="IsFocused"){lua_pushcfunction(L,textbox_is_focused,"IsFocused");return true;}
    }
    if(n.class_name=="UserInputService") {
        if(key=="GetFocusedTextBox"){lua_pushcfunction(L,input_focused_textbox,"GetFocusedTextBox");return true;}
        if(key=="TextBoxFocused"||key=="TextBoxFocusReleased"){push_signal(L,signal_for(w,n,key));return true;}
    }
    return false;
}
bool textbox_selection_write(lua_State* L,World& w,Node& n,const std::string& key) {
    if(!textbox_selection_key(n,key))return false;
    if(lua_type(L,3)!=LUA_TNUMBER)throw std::runtime_error("TextBox position requires an integer");
    const double value=lua_tonumber(L,3);
    if(!std::isfinite(value)||std::floor(value)!=value||value<-1||value>std::numeric_limits<int>::max())
        throw std::runtime_error("TextBox position is out of range");
    if(focused_textbox(w)==&n) {
        const int position=static_cast<int>(value);
        const int cursor=key=="CursorPosition"?std::max(1,position):w.text_cursor;
        const int selection=key=="SelectionStart"?position:w.text_selection;
        if(update_textbox_selection(w,n,cursor,selection))++w.text_editor_revision;
    }
    return true;
}
