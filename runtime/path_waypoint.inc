// Public path result values. Path search itself belongs to the engine service.
namespace path_waypoints {
using Json=nlohmann::json;
constexpr const char* meta="LocalLuau.PathWaypoint";
struct Value {float position[3];int action;size_t label_size;};
const char* label(const Value* value){return reinterpret_cast<const char*>(value+1);}
Value* read(lua_State* L,int index) {
    if(!lua_isuserdata(L,index)||!lua_getmetatable(L,index))return nullptr;
    luaL_getmetatable(L,meta);const bool match=lua_rawequal(L,-1,-2);lua_pop(L,2);
    return match?static_cast<Value*>(lua_touserdata(L,index)):nullptr;
}
void push(lua_State* L,const float* position,int action,const char* text,size_t size) {
    auto* value=static_cast<Value*>(lua_newuserdata(L,sizeof(Value)+size));
    for(int i=0;i<3;++i)value->position[i]=position[i];
    value->action=action;value->label_size=size;
    if(size)std::memcpy(value+1,text,size);
    luaL_getmetatable(L,meta);lua_setmetatable(L,-2);
}
int construct(lua_State* L) {
    if(!lua_isvector(L,1)){luaL_argerror(L,1,"Vector3 expected");return 0;}
    int action=0;
    if(!world_bindings::read_geometry_enum_item(L,2,"PathWaypointAction",action)) {
        luaL_argerror(L,2,"Enum.PathWaypointAction expected");return 0;
    }
    size_t size=0;const char* text=luaL_optlstring(L,3,"",&size);
    const auto* source=lua_tovector(L,1);float position[3]={source[0],source[1],source[2]};
    push(L,position,action,text,size);return 1;
}
int index(lua_State* L) {
    const auto* value=static_cast<Value*>(luaL_checkudata(L,1,meta));
    size_t length=0;const char* raw=luaL_checklstring(L,2,&length);const std::string key(raw,length);
    if(key=="Position")lua_pushvector(L,value->position[0],value->position[1],value->position[2]);
    else if(key=="Action")world_bindings::push_geometry_enum_item(L,"PathWaypointAction",value->action);
    else if(key=="Label")lua_pushlstring(L,label(value),value->label_size);
    else {luaL_error(L,"unknown PathWaypoint member");return 0;}
    return 1;
}
int readonly(lua_State* L){luaL_error(L,"PathWaypoint values are immutable");return 0;}
int equal(lua_State* L) {
    const auto* a=read(L,1);const auto* b=read(L,2);
    lua_pushboolean(L,a&&b&&a->action==b->action&&a->label_size==b->label_size&&
        a->position[0]==b->position[0]&&a->position[1]==b->position[1]&&a->position[2]==b->position[2]&&
        std::memcmp(label(a),label(b),a->label_size)==0);return 1;
}
Json scalar(float value) {
    if(std::isnan(value))return Json{{"$number","nan"}};
    if(std::isinf(value))return Json{{"$number",value>0?"inf":"-inf"}};
    return value;
}
Json encode(const Value& value) {
    constexpr char digits[]="0123456789abcdef";std::string hex;hex.reserve(value.label_size*2);
    for(size_t i=0;i<value.label_size;++i){const auto byte=static_cast<unsigned char>(label(&value)[i]);hex+=digits[byte>>4];hex+=digits[byte&15];}
    return Json{{"$path_waypoint",{{"position",{scalar(value.position[0]),scalar(value.position[1]),scalar(value.position[2])}},
        {"action",value.action},{"label_hex",std::move(hex)}}}};
}
void decode(lua_State* L,const Json& data) {
    if(!data.is_object()||data.size()!=3||!data.contains("position")||!data["position"].is_array()||data["position"].size()!=3||
        !data.contains("action")||!data["action"].is_number_integer()||data["action"]<0||data["action"]>2||
        !data.contains("label_hex")||!data["label_hex"].is_string())throw std::runtime_error("invalid PathWaypoint value");
    float position[3];
    for(int i=0;i<3;++i) {
        const auto& v=data["position"][i];
        if(v.is_number())position[i]=static_cast<float>(v.get<double>());
        else if(v==Json{{"$number","nan"}})position[i]=NAN;
        else if(v==Json{{"$number","inf"}})position[i]=INFINITY;
        else if(v==Json{{"$number","-inf"}})position[i]=-INFINITY;
        else throw std::runtime_error("invalid PathWaypoint position");
    }
    const auto& hex=data["label_hex"].get_ref<const std::string&>();
    if(hex.size()%2)throw std::runtime_error("invalid PathWaypoint label encoding");
    auto nibble=[](char c)->int{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;return -1;};
    std::string text;text.reserve(hex.size()/2);
    for(size_t i=0;i<hex.size();i+=2){int a=nibble(hex[i]),b=nibble(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid PathWaypoint label encoding");text+=char(a*16+b);}
    push(L,position,data["action"].get<int>(),text.data(),text.size());
}
void method(lua_State* L,const char* name,lua_CFunction fn){lua_pushcfunction(L,fn,name);lua_setfield(L,-2,name);}
void install(lua_State* L) {
    luaL_newmetatable(L,meta);method(L,"__index",index);method(L,"__newindex",readonly);method(L,"__eq",equal);
    lua_pushliteral(L,"PathWaypoint");lua_setfield(L,-2,"__type");
    lua_pushliteral(L,"PathWaypoint");lua_setfield(L,-2,"__metatable");lua_setreadonly(L,-1,true);lua_pop(L,1);
    lua_newtable(L);method(L,"new",construct);lua_setreadonly(L,-1,true);lua_setglobal(L,"PathWaypoint");
}
}
