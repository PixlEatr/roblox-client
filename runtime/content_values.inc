// Public Content values. Object identities remain owned by this VM's World,
// whose node storage (including destroyed nodes) outlives all Lua handles.
constexpr const char* kContentMeta="LocalLuau.Content";
struct ContentValue {
    int source=0;
    World* world=nullptr;
    Node* node=nullptr;
    size_t uri_size=0;
};
const char* content_uri(const ContentValue& value){return reinterpret_cast<const char*>(&value+1);}
ContentValue* read_content(lua_State* L,int index) {
    return has_meta(L,index,kContentMeta)?static_cast<ContentValue*>(lua_touserdata(L,index)):nullptr;
}
void push_content(lua_State* L,int source,const char* text=nullptr,size_t length=0,World* world=nullptr,Node* node=nullptr) {
    auto* value=static_cast<ContentValue*>(lua_newuserdata(L,sizeof(ContentValue)+length));
    *value=ContentValue{source,world,node,length};
    if(length)std::memcpy(value+1,text,length);
    luaL_getmetatable(L,kContentMeta);lua_setmetatable(L,-2);
}
int content_from_uri(lua_State* L) {
    luaL_checktype(L,1,LUA_TSTRING);size_t size=0;const char* text=lua_tolstring(L,1,&size);
    push_content(L,size?1:0,text,size);return 1;
}
int content_from_asset(lua_State* L) {
    const double id=luaL_checknumber(L,1);
    if(!std::isfinite(id))return luaL_argerror(L,1,"finite asset ID expected"),0;
    if(id==0){push_content(L,0);return 1;}
    lua_pushnumber(L,id);size_t size=0;const char* value=lua_tolstring(L,-1,&size);
    std::string uri="rbxassetid://";uri.append(value,size);lua_pop(L,1);
    push_content(L,1,uri.data(),uri.size());return 1;
}
int content_from_object(lua_State* L) {
    auto* handle=checked_handle(L,1);
    push_content(L,2,nullptr,0,handle->world,handle->node);return 1;
}
int content_index(lua_State* L) {
    auto* value=static_cast<ContentValue*>(luaL_checkudata(L,1,kContentMeta));
    size_t size=0;const char* key=luaL_checklstring(L,2,&size);const std::string name(key,size);
    if(name=="SourceType")push_geometry_enum_item(L,"ContentSourceType",value->source);
    else if(name=="Uri") {if(value->source==1)lua_pushlstring(L,content_uri(*value),value->uri_size);else lua_pushnil(L);}
    else if(name=="Object") {if(value->source==2)push_node(L,*value->world,value->node);else lua_pushnil(L);}
    else if(name=="Opaque")lua_pushnil(L);
    else return luaL_error(L,"unknown Content member"),0;
    return 1;
}
int content_readonly(lua_State* L){return luaL_error(L,"Content values are immutable"),0;}
int content_equal(lua_State* L) {
    auto* a=read_content(L,1);auto* b=read_content(L,2);
    lua_pushboolean(L,a&&b&&a->source==b->source&&a->world==b->world&&a->node==b->node&&
        a->uri_size==b->uri_size&&std::memcmp(content_uri(*a),content_uri(*b),a->uri_size)==0);
    return 1;
}
void install_content_impl(lua_State* L) {
    luaL_newmetatable(L,kContentMeta);
    lua_pushcfunction(L,content_index,"__index");lua_setfield(L,-2,"__index");
    lua_pushcfunction(L,content_equal,"__eq");lua_setfield(L,-2,"__eq");
    lua_pushcfunction(L,content_readonly,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushliteral(L,"Content");lua_setfield(L,-2,"__type");
    lua_pushliteral(L,"Content");lua_setfield(L,-2,"__metatable");lua_setreadonly(L,-1,true);lua_pop(L,1);
    lua_newtable(L);
    lua_pushcfunction(L,content_from_uri,"fromUri");lua_setfield(L,-2,"fromUri");
    lua_pushcfunction(L,content_from_asset,"fromAssetId");lua_setfield(L,-2,"fromAssetId");
    lua_pushcfunction(L,content_from_object,"fromObject");lua_setfield(L,-2,"fromObject");
    push_content(L,0);lua_setfield(L,-2,"none");lua_setreadonly(L,-1,true);lua_setglobal(L,"Content");
}
Json encode_content(const ContentValue& value) {
    Json data{{"source_type",value.source}};
    if(value.source==1) {
        constexpr char digits[]="0123456789abcdef";std::string hex;hex.reserve(value.uri_size*2);
        for(size_t i=0;i<value.uri_size;++i){auto c=static_cast<unsigned char>(content_uri(value)[i]);hex+=digits[c>>4];hex+=digits[c&15];}
        data["uri_hex"]=std::move(hex);
    }else if(value.source==2)data["object_id"]=value.node->id;
    return Json{{"$content",std::move(data)}};
}
bool decode_content(lua_State* L,const Json& encoded,World* world) {
    if(!encoded.is_object()||encoded.size()!=1||!encoded.contains("$content"))return false;
    const auto& value=encoded["$content"];
    if(!value.is_object()||!value.contains("source_type")||!value["source_type"].is_number_integer())
        throw std::runtime_error("invalid Content value");
    const int source=value["source_type"].get<int>();
    if(source==0&&value.size()==1)push_content(L,0);
    else if(source==1&&value.size()==2&&value.contains("uri_hex")&&value["uri_hex"].is_string()) {
        const auto& hex=value["uri_hex"].get_ref<const std::string&>();
        if(hex.empty()||hex.size()%2||hex.size()>2*1024*1024)throw std::runtime_error("invalid Content URI encoding");
        auto nib=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;};
        std::string text;text.reserve(hex.size()/2);
        for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid Content URI encoding");text+=char(a*16+b);}
        push_content(L,1,text.data(),text.size());
    }else if(source==2&&value.size()==2&&value.contains("object_id")&&value["object_id"].is_string()) {
        if(!world)throw std::runtime_error("Content object requires its World");
        auto* node=find_node(*world,value["object_id"].get<std::string>());
        if(!node)throw std::runtime_error("Content object is unavailable");
        push_content(L,2,nullptr,0,world,node);
    }else throw std::runtime_error("invalid or unsupported Content source");
    return true;
}
