#include "world_bindings.hpp"

#include <lualib.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <cmath>
#include <memory>
#include <set>
#include <stdexcept>
#include <unordered_set>

namespace world_bindings {
namespace {
constexpr const char* kInstanceMeta = "WorldSnapshot.Instance";
constexpr const char* kCFrameMeta = "WorldSnapshot.CFrame";
constexpr const char* kSettingsId = "__world_snapshot_settings__";
constexpr size_t kMaxNodes = 20000;
constexpr size_t kMaxMutations = 10000;
constexpr size_t kMaxMutationBytes = 128 * 1024;
constexpr size_t kMaxPrinted = 128 * 1024;
constexpr int kMaxJsonDepth = 32;

struct Handle { World* world; Node* node; };
struct CFrameValue { std::array<double, 3> p{}; std::array<double, 9> r{1,0,0,0,1,0,0,0,1}; };

Handle* checked_handle(lua_State* L, int i) {
    return static_cast<Handle*>(luaL_checkudata(L, i, kInstanceMeta));
}
bool has_meta(lua_State* L, int i, const char* name) {
    if (!lua_getmetatable(L, i)) return false;
    luaL_getmetatable(L, name);
    bool same = lua_rawequal(L, -1, -2) != 0;
    lua_pop(L, 2);
    return same;
}
void push_node(lua_State* L, World& world, Node* node) {
    lua_pushlightuserdata(L, node);
    lua_rawget(L, LUA_REGISTRYINDEX);
    if (lua_isuserdata(L, -1)) return;
    lua_pop(L, 1);
    auto* h = static_cast<Handle*>(lua_newuserdatatagged(L, sizeof(Handle), 0));
    h->world = &world; h->node = node;
    luaL_getmetatable(L, kInstanceMeta); lua_setmetatable(L, -2);
    lua_pushlightuserdata(L, node); lua_pushvalue(L, -2); lua_rawset(L, LUA_REGISTRYINDEX);
}
Node* find_node(World& world, const std::string& id) {
    auto it = world.nodes.find(id);
    return it == world.nodes.end() ? nullptr : &it->second;
}
const Json* local_value(World& world, const Node& node, const std::string& key) {
    auto it = world.local_properties.find(node.id);
    if (it == world.local_properties.end() || !it->second.is_object()) return nullptr;
    auto p = it->second.find(key);
    return p == it->second.end() ? nullptr : &*p;
}
std::string parent_id(World& world, const Node& node) {
    if (const Json* v = local_value(world, node, "Parent")) {
        if (v->is_object() && v->contains("$ref") && (*v)["$ref"].is_string()) return (*v)["$ref"].get<std::string>();
        return {};
    }
    return node.parent;
}
std::string instance_name(World& world, const Node& node) {
    if (const Json* v = local_value(world, node, "Name"); v && v->is_string()) return v->get<std::string>();
    return node.name;
}
std::string origin(World& world, const Node& node, const std::string& key) {
    if (local_value(world, node, key)) return "local";
    if (key == "Name" || key == "ClassName" || key == "Parent") return "received";
    if (node.properties.contains(key)) {
        auto o=node.origins.find(key);
        if(o!=node.origins.end()&&o->is_string()&&o->get<std::string>()=="class_default")return "class_default";
        return "received";
    }
    auto d = world.defaults.find(node.class_name);
    auto o = world.default_origins.find(node.class_name);
    if (d != world.defaults.end() && d->is_object() && d->contains(key) && o != world.default_origins.end()
        && o->is_object() && o->contains(key) && (*o)[key].is_string() && !(*o)[key].get<std::string>().empty())
        return "class_default";
    return "unavailable";
}
const Json* property_value(World& world, const Node& node, const std::string& key) {
    if (const Json* v = local_value(world, node, key)) return v;
    auto p = node.properties.find(key);
    if (p != node.properties.end()) return &*p;
    auto d = world.defaults.find(node.class_name);
    auto o = world.default_origins.find(node.class_name);
    if (d != world.defaults.end() && d->is_object() && d->contains(key) && o != world.default_origins.end()
        && o->is_object() && o->contains(key) && (*o)[key].is_string() && !(*o)[key].get<std::string>().empty())
        return &(*d)[key];
    return nullptr;
}

void push_json(lua_State* L, World& world, const Json& v, int depth = 0);
void push_vector(lua_State* L, const Json& v) {
    if (!v.is_array() || v.size() != 3) throw std::runtime_error("invalid Vector3 snapshot value");
    double a[3];
    for (size_t i = 0; i < 3; ++i) {
        if (!v[i].is_number() || !std::isfinite(a[i] = v[i].get<double>())) throw std::runtime_error("invalid Vector3 component");
    }
    lua_pushvector(L, a[0], a[1], a[2]);
}
void push_cframe(lua_State* L, const Json& v) {
    if (!v.is_object() || !v.contains("position") || !v.contains("rotation") || !v["position"].is_array()
        || v["position"].size() != 3 || !v["rotation"].is_array() || v["rotation"].size() != 9)
        throw std::runtime_error("invalid CFrame snapshot value");
    auto* cf = static_cast<CFrameValue*>(lua_newuserdatatagged(L, sizeof(CFrameValue), 0));
    for (size_t i=0;i<3;i++) { if (!v["position"][i].is_number()) throw std::runtime_error("invalid CFrame position"); cf->p[i]=v["position"][i].get<double>(); }
    for (size_t i=0;i<9;i++) { if (!v["rotation"][i].is_number()) throw std::runtime_error("invalid CFrame rotation"); cf->r[i]=v["rotation"][i].get<double>(); }
    luaL_getmetatable(L, kCFrameMeta); lua_setmetatable(L, -2);
}
void push_json(lua_State* L, World& world, const Json& v, int depth) {
    if (depth > kMaxJsonDepth) throw std::runtime_error("snapshot property nesting limit exceeded");
    if (v.is_null()) lua_pushnil(L);
    else if (v.is_boolean()) lua_pushboolean(L, v.get<bool>());
    else if (v.is_number()) {
        double n = v.get<double>(); if (!std::isfinite(n)) throw std::runtime_error("nonfinite snapshot number");
        lua_pushnumber(L, n);
    } else if (v.is_string()) {
        const auto& s = v.get_ref<const std::string&>(); lua_pushlstring(L, s.data(), s.size());
    } else if (v.is_array()) {
        lua_createtable(L, static_cast<int>(v.size()), 0);
        for (size_t i=0;i<v.size();i++) { push_json(L, world, v[i], depth+1); lua_rawseti(L, -2, static_cast<int>(i+1)); }
    } else if (v.is_object()) {
        if (v.size()==1 && v.contains("$ref")) {
            if (!v["$ref"].is_string()) throw std::runtime_error("invalid instance reference");
            Node* n = find_node(world, v["$ref"].get<std::string>());
            if (!n) throw std::runtime_error("snapshot property references an unknown node");
            push_node(L, world, n);
        } else if (v.size()==1 && v.contains("$vector3")) push_vector(L, v["$vector3"]);
        else if (v.size()==1 && v.contains("$cframe")) push_cframe(L, v["$cframe"]);
        else if (v.size()==1 && v.contains("$bytes_hex")) {
            if(!v["$bytes_hex"].is_string())throw std::runtime_error("invalid byte-string value");
            std::string hex=v["$bytes_hex"].get<std::string>();
            if(hex.size()%2||hex.size()>2*1024*1024)throw std::runtime_error("invalid byte-string length");
            auto nib=[](char c)->int{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1;};
            std::string bytes;bytes.reserve(hex.size()/2);
            for(size_t i=0;i<hex.size();i+=2){int a=nib(hex[i]),b=nib(hex[i+1]);if(a<0||b<0)throw std::runtime_error("invalid byte-string hex");bytes.push_back(char((a<<4)|b));}
            lua_pushlstring(L,bytes.data(),bytes.size());
        }
        else {
            if (v.size() > 10000) throw std::runtime_error("snapshot property table item limit exceeded");
            lua_createtable(L, 0, static_cast<int>(v.size()));
            for (auto it=v.begin();it!=v.end();++it) { lua_pushlstring(L,it.key().data(),it.key().size()); push_json(L,world,it.value(),depth+1); lua_rawset(L,-3); }
        }
    } else throw std::runtime_error("unsupported snapshot property value");
}

bool is_descendant_of(World& world, const Node& node, const std::string& ancestor) {
    std::string p = parent_id(world, node);
    size_t hops=0;
    while (!p.empty() && hops++ <= world.nodes.size()) {
        if (p == ancestor) return true;
        Node* parent = find_node(world,p); if (!parent) return false;
        p = parent_id(world,*parent);
    }
    return false;
}
bool class_is_a(const World& world, const std::string& actual, const std::string& requested) {
    if (actual == requested || requested == "Instance" || requested == "Object") return true;
    std::set<std::string> seen;
    std::vector<std::string> todo{actual};
    while (!todo.empty()) {
        std::string c=todo.back(); todo.pop_back();
        if (!seen.insert(c).second) continue;
        auto it=world.class_parents.find(c);
        if (it==world.class_parents.end()) continue;
        if (it->is_string()) { std::string p=it->get<std::string>(); if(p==requested)return true; todo.push_back(p); }
        else if (it->is_array()) for (const auto& p:*it) if (p.is_string()) { std::string s=p.get<std::string>(); if(s==requested)return true; todo.push_back(s); }
    }
    return false;
}
void push_children(lua_State* L, World& world, const Node& parent, bool recursive) {
    lua_newtable(L); int idx=1;
    for (const std::string& id:world.order) {
        Node& child=world.nodes.at(id);
        bool match = recursive ? is_descendant_of(world,child,parent.id) : parent_id(world,child)==parent.id;
        if (match) { push_node(L,world,&child); lua_rawseti(L,-2,idx++); }
    }
}
int is_a(lua_State* L) {
    Handle* h=checked_handle(L,1); const char* requested=luaL_checkstring(L,2);
    lua_pushboolean(L,class_is_a(*h->world,h->node->class_name,requested)); return 1;
}
int get_children(lua_State* L) { Handle* h=checked_handle(L,1); push_children(L,*h->world,*h->node,false); return 1; }
int get_descendants(lua_State* L) { Handle* h=checked_handle(L,1); push_children(L,*h->world,*h->node,true); return 1; }
int find_child(lua_State* L) {
    Handle* h=checked_handle(L,1); std::string name=luaL_checkstring(L,2); bool recursive=lua_gettop(L)>=3 && lua_toboolean(L,3);
    World& w=*h->world;
    for(const std::string& id:w.order) { Node& n=w.nodes.at(id); if((recursive?is_descendant_of(w,n,h->node->id):parent_id(w,n)==h->node->id) && instance_name(w,n)==name) { push_node(L,w,&n); return 1; } }
    lua_pushnil(L); return 1;
}
int find_class(lua_State* L, bool which) {
    Handle* h=checked_handle(L,1); std::string name=luaL_checkstring(L,2); bool recursive=which && lua_gettop(L)>=3 && lua_toboolean(L,3);
    World& w=*h->world;
    for(const std::string& id:w.order) { Node& n=w.nodes.at(id); bool child=recursive?is_descendant_of(w,n,h->node->id):parent_id(w,n)==h->node->id; if(child && (which?class_is_a(w,n.class_name,name):n.class_name==name)) { push_node(L,w,&n); return 1; } }
    lua_pushnil(L); return 1;
}
int find_child_class(lua_State* L) { return find_class(L,false); }
int find_child_which(lua_State* L) { return find_class(L,true); }
int is_descendant(lua_State* L) {
    Handle* h=checked_handle(L,1); Handle* other=checked_handle(L,2);
    lua_pushboolean(L,is_descendant_of(*h->world,*h->node,other->node->id)); return 1;
}
int get_full_name(lua_State* L) {
    Handle* h=checked_handle(L,1); World& w=*h->world;
    std::vector<std::string> parts; Node* n=h->node; size_t hops=0;
    while(n && hops++<=w.nodes.size()) { parts.push_back(instance_name(w,*n)); std::string p=parent_id(w,*n); n=p.empty()?nullptr:find_node(w,p); }
    std::string out; for(auto it=parts.rbegin();it!=parts.rend();++it){if(!out.empty())out+='.';out+=*it;}
    lua_pushlstring(L,out.data(),out.size()); return 1;
}
int get_service(lua_State* L) {
    Handle* h=checked_handle(L,1); if(h->node->id!=h->world->game_id){luaL_error(L,"GetService requires DataModel");return 0;}
    std::string service=luaL_checkstring(L,2); auto it=h->world->services.find(service);
    if(it==h->world->services.end()){luaL_error(L,"service unavailable in snapshot: %s",service.c_str());return 0;}
    Node* n=find_node(*h->world,it->second); if(!n){luaL_error(L,"snapshot service reference is invalid");return 0;}
    push_node(L,*h->world,n); return 1;
}
int make_user_settings(lua_State*L){
    if(lua_gettop(L)!=0){luaL_error(L,"UserSettings takes no arguments");return 0;}
    auto*w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));
    Node*n=find_node(*w,kSettingsId);if(!n){luaL_error(L,"UserSettings model unavailable");return 0;}
    push_node(L,*w,n);return 1;
}
int find_service(lua_State* L) {
    Handle* h=checked_handle(L,1); if(h->node->id!=h->world->game_id){luaL_error(L,"FindService requires DataModel");return 0;}
    std::string service=luaL_checkstring(L,2); auto it=h->world->services.find(service);
    if(it==h->world->services.end())lua_pushnil(L);else {Node*n=find_node(*h->world,it->second);if(n)push_node(L,*h->world,n);else lua_pushnil(L);} return 1;
}

int cframe_components(lua_State* L) { auto* c=static_cast<CFrameValue*>(luaL_checkudata(L,1,kCFrameMeta)); for(double v:c->p)lua_pushnumber(L,v);for(double v:c->r)lua_pushnumber(L,v);return 12; }
int cframe_index(lua_State* L) {
    auto* c=static_cast<CFrameValue*>(luaL_checkudata(L,1,kCFrameMeta)); const char* k=luaL_checkstring(L,2);
    if(std::strcmp(k,"Position")==0)lua_pushvector(L,c->p[0],c->p[1],c->p[2]);
    else if(std::strcmp(k,"XVector")==0)lua_pushvector(L,c->r[0],c->r[3],c->r[6]);
    else if(std::strcmp(k,"YVector")==0)lua_pushvector(L,c->r[1],c->r[4],c->r[7]);
    else if(std::strcmp(k,"ZVector")==0)lua_pushvector(L,c->r[2],c->r[5],c->r[8]);
    else if(std::strcmp(k,"LookVector")==0)lua_pushvector(L,-c->r[2],-c->r[5],-c->r[8]);
    else if(std::strcmp(k,"GetComponents")==0)lua_pushcfunction(L,cframe_components,"GetComponents");
    else lua_pushnil(L); return 1;
}
int instance_index(lua_State* L) {
    Handle* h=checked_handle(L,1); World& w=*h->world; Node& n=*h->node; std::string key=luaL_checkstring(L,2);
    if(key=="Name"){auto s=instance_name(w,n);lua_pushlstring(L,s.data(),s.size());return 1;}
    if(key=="ClassName"){lua_pushlstring(L,n.class_name.data(),n.class_name.size());return 1;}
    if(key=="Parent"){std::string p=parent_id(w,n);if(p.empty())lua_pushnil(L);else{Node* parent=find_node(w,p);if(!parent)throw std::runtime_error("snapshot parent is unavailable");push_node(L,w,parent);}return 1;}
    if(n.id==w.game_id && key=="JobId" && !n.properties.contains("JobId")){luaL_error(L,"JobId is unavailable in this snapshot");return 0;}
    if(n.class_name=="Players" && key=="LocalPlayer"){Node* player=find_node(w,w.local_player);if(player)push_node(L,w,player);else lua_pushnil(L);return 1;}
    if(key=="GetService"){lua_pushcfunction(L,get_service,"GetService");return 1;}
    if(key=="FindService"){lua_pushcfunction(L,find_service,"FindService");return 1;}
    if(key=="GetChildren"){lua_pushcfunction(L,get_children,"GetChildren");return 1;}
    if(key=="GetDescendants"){lua_pushcfunction(L,get_descendants,"GetDescendants");return 1;}
    if(key=="FindFirstChild"){lua_pushcfunction(L,find_child,"FindFirstChild");return 1;}
    if(key=="FindFirstChildOfClass"){lua_pushcfunction(L,find_child_class,"FindFirstChildOfClass");return 1;}
    if(key=="FindFirstChildWhichIsA"){lua_pushcfunction(L,find_child_which,"FindFirstChildWhichIsA");return 1;}
    if(key=="IsA"){lua_pushcfunction(L,is_a,"IsA");return 1;}
    if(key=="IsDescendantOf"){lua_pushcfunction(L,is_descendant,"IsDescendantOf");return 1;}
    if(key=="GetFullName"){lua_pushcfunction(L,get_full_name,"GetFullName");return 1;}
    if(n.class_name=="RunService" && key=="IsStudio"){lua_pushboolean(L,false);return 1;}
    if(n.class_name=="RunService" && key=="IsRunning"){lua_pushboolean(L,true);return 1;}
    const Json* value=property_value(w,n,key);
    if(!value){luaL_error(L,"property '%s' unavailable in snapshot for %s (%s)",key.c_str(),n.name.c_str(),n.class_name.c_str());return 0;}
    push_json(L,w,*value);return 1;
}

Json vector_to_json(lua_State* L,int i){const LUA_VECTOR_TYPE* v=lua_tovector(L,i);if(!v)throw std::runtime_error("invalid vector");return Json{{"$vector3",Json::array({double(v[0]),double(v[1]),double(v[2])})}};}
std::string bytes_hex(const char* data,size_t size){static const char*hex="0123456789abcdef";std::string out;out.reserve(size*2);for(size_t i=0;i<size;i++){unsigned char c=data[i];out.push_back(hex[c>>4]);out.push_back(hex[c&15]);}return out;}
Json convert_lua_value(lua_State* L,World& w,int i,int depth=0,std::unordered_set<const void*>* seen=nullptr){
    if(depth>kMaxJsonDepth)throw std::runtime_error("mutation nesting limit exceeded");
    switch(lua_type(L,i)){
    case LUA_TNIL:return nullptr;case LUA_TBOOLEAN:return lua_toboolean(L,i)!=0;
    case LUA_TNUMBER:{double n=lua_tonumber(L,i);if(!std::isfinite(n))throw std::runtime_error("cannot save a nonfinite number");return n;}
    case LUA_TSTRING:{size_t n;const char*s=lua_tolstring(L,i,&n);return Json{{"$bytes_hex",bytes_hex(s,n)}};}
    case LUA_TVECTOR:return vector_to_json(L,i);
    case LUA_TUSERDATA:
        if(has_meta(L,i,kInstanceMeta)){auto*h=static_cast<Handle*>(lua_touserdata(L,i));return Json{{"$ref",h->node->id}};}
        if(has_meta(L,i,kCFrameMeta)){auto*c=static_cast<CFrameValue*>(lua_touserdata(L,i));return Json{{"$cframe",{{"position",c->p},{"rotation",c->r}}}};}
        throw std::runtime_error("unsupported userdata mutation");
    case LUA_TTABLE:{
        const void* ptr=lua_topointer(L,i);std::unordered_set<const void*> local_seen;if(!seen)seen=&local_seen;if(!seen->insert(ptr).second)throw std::runtime_error("cyclic table mutation unsupported");
        Json out=Json::object();size_t count=0;lua_pushnil(L);while(lua_next(L,i)){if(++count>5000){lua_pop(L,2);throw std::runtime_error("table mutation item limit exceeded");}std::string k;if(lua_type(L,-2)==LUA_TSTRING){size_t n;const char*s=lua_tolstring(L,-2,&n);k.assign(s,n);}else if(lua_type(L,-2)==LUA_TNUMBER){double n=lua_tonumber(L,-2);k=std::to_string(n);}else{lua_pop(L,2);throw std::runtime_error("table mutation keys must be strings or numbers");}out[k]=convert_lua_value(L,w,-1,depth+1,seen);lua_pop(L,1);}seen->erase(ptr);return out;
    }
    default:throw std::runtime_error("unsupported local property value");
    }
}
int instance_newindex(lua_State* L){
    Handle*h=checked_handle(L,1);World&w=*h->world;Node&n=*h->node;std::string key=luaL_checkstring(L,2);Json value;
    if(key=="ClassName" || key=="JobId") {luaL_error(L,"property is read-only");return 0;}
    try{
        if(key=="Parent"){
            if(lua_isnil(L,3))value=nullptr;
            else {Handle*p=checked_handle(L,3);if(p->world!=&w)throw std::runtime_error("parent belongs to a different snapshot");if(p->node==&n||is_descendant_of(w,*p->node,n.id))throw std::runtime_error("parent change would create a cycle");value=Json{{"$ref",p->node->id}};}
        } else if(key=="Name") {size_t len;const char*s=luaL_checklstring(L,3,&len);if(len>256)throw std::runtime_error("Name exceeds 256 bytes");value=std::string(s,len);}
        else {if(origin(w,n,key)=="unavailable")throw std::runtime_error("property absent from snapshot; local creation is unsupported");value=convert_lua_value(L,w,3);}
        Json entry{{"node_id",n.id},{"property",key},{"value",value}};
        size_t bytes=entry.dump().size();
        if(w.mutations.size()>=kMaxMutations||bytes>kMaxMutationBytes-w.mutation_bytes)throw std::runtime_error("local mutation output limit exceeded");
        w.local_properties[n.id][key]=value;
        w.mutations.push_back(std::move(entry));w.mutation_bytes+=bytes;
    }catch(const std::exception&e){luaL_error(L,"local property write failed: %s",e.what());return 0;}
    return 0;
}
int instance_tostring(lua_State*L){Handle*h=checked_handle(L,1);auto s=instance_name(*h->world,*h->node);lua_pushlstring(L,s.data(),s.size());return 1;}
int instance_equal(lua_State*L){auto*a=static_cast<Handle*>(lua_touserdata(L,1));auto*b=static_cast<Handle*>(lua_touserdata(L,2));bool aa=has_meta(L,1,kInstanceMeta),bb=has_meta(L,2,kInstanceMeta);lua_pushboolean(L,aa&&bb&&a&&b&&a->node==b->node);return 1;}
int cframe_tostring(lua_State*L){auto*c=static_cast<CFrameValue*>(luaL_checkudata(L,1,kCFrameMeta));lua_pushfstring(L,"%f, %f, %f",c->p[0],c->p[1],c->p[2]);return 1;}
int cframe_equal(lua_State*L){auto*a=static_cast<CFrameValue*>(lua_touserdata(L,1));auto*b=static_cast<CFrameValue*>(lua_touserdata(L,2));bool aa=has_meta(L,1,kCFrameMeta),bb=has_meta(L,2,kCFrameMeta);bool eq=aa&&bb&&a&&b&&a->p==b->p&&a->r==b->r;lua_pushboolean(L,eq);return 1;}
int print_capture(lua_State*L){World*w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));bool warn=lua_toboolean(L,lua_upvalueindex(2));std::string line;if(warn)line="WARN: ";for(int i=1;i<=lua_gettop(L);i++){if(i>1)line+='\t';size_t n=0;const char*s=luaL_tolstring(L,i,&n);line.append(s,n);lua_pop(L,1);}line+='\n';size_t room=w->output.size()<kMaxPrinted?kMaxPrinted-w->output.size():0;if(line.size()>room){w->output.append(line.data(),room);w->output_truncated=true;}else w->output+=line;return 0;}
int property_origin(lua_State*L){Handle*h=checked_handle(L,1);std::string k=luaL_checkstring(L,2);std::string s=origin(*h->world,*h->node,k);lua_pushlstring(L,s.data(),s.size());return 1;}
int client_index(lua_State*L){World*w=static_cast<World*>(lua_touserdata(L,lua_upvalueindex(1)));const char*k=luaL_checkstring(L,2);if(std::strcmp(k,"snapshot")==0)lua_pushboolean(L,1);else if(std::strcmp(k,"sequence")==0){if(w->sequence>0)lua_pushnumber(L,w->sequence);else lua_pushnil(L);}else if(std::strcmp(k,"propertyOrigin")==0)lua_pushcfunction(L,property_origin,"propertyOrigin");else lua_pushnil(L);return 1;}

} // namespace

World parse_snapshot(const std::string& text) {
    Json root=Json::parse(text);
    if(!root.is_object()||root.value("version",0)!=1)throw std::runtime_error("unsupported world snapshot version");
    if(!root.contains("nodes")||!root["nodes"].is_array()||root["nodes"].size()>kMaxNodes)throw std::runtime_error("snapshot nodes must be an array of at most 20000 records");
    if(!root.contains("services")||!root["services"].is_object()||root["services"].size()>128)throw std::runtime_error("snapshot services must be an object");
    World w;
    if(root.contains("local_player")&&root["local_player"].is_string())w.local_player=root["local_player"].get<std::string>();
    if(root.contains("job_id")&&root["job_id"].is_string())w.job_id=root["job_id"].get<std::string>();
    if(root.contains("sequence")&&root["sequence"].is_number())w.sequence=root["sequence"].get<double>();
    if(root.contains("class_parents"))w.class_parents=root["class_parents"];
    if(root.contains("defaults"))w.defaults=root["defaults"];
    if(root.contains("default_origins"))w.default_origins=root["default_origins"];
    if(!w.class_parents.is_object()||!w.defaults.is_object()||!w.default_origins.is_object())throw std::runtime_error("invalid class metadata/defaults");
    for(const Json& record:root["nodes"]){
        if(!record.is_object()||!record.contains("id")||!record["id"].is_string()||!record.contains("class_name")||!record["class_name"].is_string()||!record.contains("name")||!record["name"].is_string())throw std::runtime_error("node requires string id, class_name, and name");
        Node n;n.id=record["id"].get<std::string>();n.class_name=record["class_name"].get<std::string>();n.name=record["name"].get<std::string>();
        if(n.id.empty()||n.id.size()>256||n.class_name.empty()||n.class_name.size()>256||n.name.size()>256)throw std::runtime_error("invalid node metadata");
        if(record.contains("parent")&&!record["parent"].is_null()){if(!record["parent"].is_string())throw std::runtime_error("node parent must be a node id or null");n.parent=record["parent"].get<std::string>();}
        if(record.contains("properties")){if(!record["properties"].is_object())throw std::runtime_error("node properties must be an object");n.properties=record["properties"];}
        if(record.contains("origins")){if(!record["origins"].is_object())throw std::runtime_error("node origins must be an object");n.origins=record["origins"];}
        if(!w.nodes.emplace(n.id,std::move(n)).second)throw std::runtime_error("duplicate node id");w.order.push_back(record["id"].get<std::string>());
    }
    for(const auto&[id,node]:w.nodes)if(node.class_name=="DataModel"){if(!w.game_id.empty())throw std::runtime_error("snapshot has multiple DataModel nodes");w.game_id=id;}
    if(w.game_id.empty()){Node game;game.id="__world_snapshot_datamodel__";game.class_name="DataModel";game.name="Game";w.game_id=game.id;w.nodes.emplace(game.id,std::move(game));}
    Node settings;settings.id=kSettingsId;settings.class_name="UserSettings";settings.name="GlobalBasicSettings";w.nodes.emplace(settings.id,std::move(settings));
    for(auto&[id,n]:w.nodes){if(id==kSettingsId||id==w.game_id)continue;if(!n.parent.empty()&&!w.nodes.count(n.parent))throw std::runtime_error("node parent reference is unknown");}
    for(auto it=root["services"].begin();it!=root["services"].end();++it){std::string id;if(it.value().is_string())id=it.value().get<std::string>();else if(it.value().is_object()&&it.value().contains("$ref")&&it.value()["$ref"].is_string())id=it.value()["$ref"].get<std::string>();else throw std::runtime_error("service values must be node ids or $ref records");if(!w.nodes.count(id)||id==w.game_id)throw std::runtime_error("service references unknown node");w.services.emplace(it.key(),id);Node& node=w.nodes.at(id);if(node.parent.empty())node.parent=w.game_id;}
    if(!w.local_player.empty()&&!w.nodes.count(w.local_player))throw std::runtime_error("local_player references unknown node");
    for(auto&[id,n]:w.nodes){std::set<std::string>seen;std::string p=n.parent;while(!p.empty()){if(p==id||!seen.insert(p).second)throw std::runtime_error("snapshot parent cycle");Node* parent=find_node(w,p);if(!parent)break;p=parent->parent;}}
    return w;
}

void install(lua_State*L,World&w){
    luaL_newmetatable(L,kInstanceMeta);
    lua_pushcfunction(L,instance_index,"__index");lua_setfield(L,-2,"__index");
    lua_pushcfunction(L,instance_newindex,"__newindex");lua_setfield(L,-2,"__newindex");
    lua_pushcfunction(L,instance_tostring,"__tostring");lua_setfield(L,-2,"__tostring");
    lua_pushcfunction(L,instance_equal,"__eq");lua_setfield(L,-2,"__eq");
    lua_pushliteral(L,"Instance");lua_setfield(L,-2,"__type");lua_pushliteral(L,"world snapshot Instance");lua_setfield(L,-2,"__metatable");lua_pop(L,1);
    luaL_newmetatable(L,kCFrameMeta);lua_pushcfunction(L,cframe_index,"__index");lua_setfield(L,-2,"__index");lua_pushcfunction(L,cframe_tostring,"__tostring");lua_setfield(L,-2,"__tostring");lua_pushcfunction(L,cframe_equal,"__eq");lua_setfield(L,-2,"__eq");lua_pushliteral(L,"CFrame");lua_setfield(L,-2,"__type");lua_pushliteral(L,"snapshot CFrame");lua_setfield(L,-2,"__metatable");lua_pop(L,1);
    Node*game=find_node(w,w.game_id);push_node(L,w,game);lua_setglobal(L,"game");
    auto ws=w.services.find("Workspace");if(ws!=w.services.end()){push_node(L,w,find_node(w,ws->second));lua_setglobal(L,"workspace");}
    auto players=w.services.find("Players");if(players!=w.services.end()){
        Node*p=find_node(w,players->second);if(p&&p->properties.contains("LocalPlayer")&&p->properties["LocalPlayer"].is_object()){
            const Json&lp=p->properties["LocalPlayer"];if(lp.contains("$ref")&&lp["$ref"].is_string())w.local_player=lp["$ref"].get<std::string>();
        }
    }
    lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,print_capture,"print",1,nullptr);lua_setglobal(L,"print");
    lua_pushlightuserdata(L,&w);lua_pushboolean(L,1);lua_pushcclosurek(L,print_capture,"warn",2,nullptr);lua_setglobal(L,"warn");
    lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,make_user_settings,"UserSettings",1,nullptr);lua_setglobal(L,"UserSettings");
    lua_createtable(L,0,3);lua_pushlightuserdata(L,&w);lua_pushcclosurek(L,client_index,"client.__index",1,nullptr);lua_setfield(L,-2,"__index");lua_pushliteral(L,"world snapshot client");lua_setfield(L,-2,"__metatable");lua_setmetatable(L,-2);lua_setglobal(L,"client");
}
Json capture_value(lua_State* L,World& world,int index){return convert_lua_value(L,world,index);}
} // namespace world_bindings
