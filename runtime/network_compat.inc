using NetworkJson=nlohmann::json;
#include "source_scheduler.hpp"
NetworkJson network_value(lua_State* L,int index,int depth=0) {
    if(depth>4)throw std::runtime_error("network options nesting limit exceeded");
    index=lua_absindex(L,index);
    if(lua_isnil(L,index))return nullptr;
    if(lua_isboolean(L,index))return lua_toboolean(L,index)!=0;
    if(lua_type(L,index)==LUA_TNUMBER){auto n=lua_tonumber(L,index);if(!std::isfinite(n))throw std::runtime_error("nonfinite network option");if(std::floor(n)==n&&std::abs(n)<9007199254740992.0)return int64_t(n);return n;}
    if(lua_type(L,index)==LUA_TSTRING){size_t size=0;auto* data=lua_tolstring(L,index,&size);if(size>1024*1024)throw std::runtime_error("network option exceeds limit");return std::string(data,size);}
    if(lua_istable(L,index)){NetworkJson result=NetworkJson::object();int count=0;lua_pushnil(L);while(lua_next(L,index)){if(++count>128||lua_type(L,-2)!=LUA_TSTRING)throw std::runtime_error("network options require bounded string keys");size_t size=0;auto* key=lua_tolstring(L,-2,&size);result[std::string(key,size)]=network_value(L,-1,depth+1);lua_pop(L,1);}return result;}
    throw std::runtime_error("unsupported network option type");
}
void push_network_value(lua_State* L,const NetworkJson& value) {
    if(value.is_null())lua_pushnil(L);else if(value.is_boolean())lua_pushboolean(L,value.get<bool>());
    else if(value.is_number())lua_pushnumber(L,value.get<double>());
    else if(value.is_string()){const auto& s=value.get_ref<const std::string&>();lua_pushlstring(L,s.data(),s.size());}
    else if(value.is_array()){lua_createtable(L,value.size(),0);int i=0;for(const auto& item:value){push_network_value(L,item);lua_rawseti(L,-2,++i);}}
    else {lua_createtable(L,0,value.size());for(auto it=value.begin();it!=value.end();++it){push_network_value(L,it.value());lua_setfield(L,-2,it.key().c_str());}}
}
int network_bridge(lua_State* L) {
    try {
        luaL_checktype(L,1,LUA_TTABLE);auto input=network_value(L,1).dump();
        auto* bindings=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
        unsigned char* output=nullptr;size_t length=0;
        if(bindings->codec(100,reinterpret_cast<const unsigned char*>(input.data()),input.size(),&output,&length))throw std::runtime_error("network request exceeds limit");
        auto release=[&](unsigned char* p){bindings->release(p,length);};std::unique_ptr<unsigned char,decltype(release)> owner(output,release);
        auto result=NetworkJson::parse(output,output+length);if(result.contains("error"))throw std::runtime_error(result["error"].get<std::string>());
        push_network_value(L,result);return 1;
    }catch(const std::exception& e){luaL_error(L,"network: %s",e.what());return 0;}
}
std::string network_codec_call(const SourceBindings& bindings,const std::string& input) {
    unsigned char* output=nullptr;size_t length=0;
    if(bindings.codec(100,reinterpret_cast<const unsigned char*>(input.data()),input.size(),&output,&length))
        throw std::runtime_error("network operation failed");
    auto release=[&](unsigned char* p){bindings.release(p,length);};std::unique_ptr<unsigned char,decltype(release)> owner(output,release);
    if(length>24u*1024u*1024u)throw std::runtime_error("network response exceeds local limit");
    return std::string(reinterpret_cast<const char*>(output),length);
}
void network_discard_job(const SourceBindings& bindings,uint64_t id) noexcept {
    try {NetworkJson query={{"operation","discard_network_job"},{"id",id}};(void)network_codec_call(bindings,query.dump());}catch(...){}
}
void network_claim_job(const SourceBindings& bindings,uint64_t id) {
    NetworkJson query={{"operation","claim_network_job"},{"id",id}};
    auto result=NetworkJson::parse(network_codec_call(bindings,query.dump()));
    if(result.contains("error"))throw std::runtime_error(result["error"].get<std::string>());
}
struct NetworkJobDiscardGuard {
    SourceBindings bindings;uint64_t id;bool armed=true;
    ~NetworkJobDiscardGuard() noexcept {if(armed)network_discard_job(bindings,id);}
};
int network_async_continue(lua_State* L,int) {
    auto* scheduler=SourceScheduler::for_thread(L);
    std::string serialized;
    try {
        if(!scheduler||!scheduler->external_continuation_entered(L,serialized)){
            luaL_error(L,"network completion was resumed outside its scheduler");return 0;
        }
        auto result=NetworkJson::parse(serialized);
        if(result.contains("error")){auto message=result["error"].get<std::string>();luaL_error(L,"network: %s",message.c_str());return 0;}
        if(!result.value("ready",false)){luaL_error(L,"network completion was invalid");return 0;}
        const auto& value=result.at("value");
        if(value.contains("error")){auto message=value["error"].get<std::string>();luaL_error(L,"network: %s",message.c_str());return 0;}
        push_network_value(L,value);return 1;
    } catch(const std::exception& e){luaL_error(L,"network: %s",e.what());return 0;}
}
int network_async_await(lua_State* L) {
    try {
        luaL_checktype(L,1,LUA_TTABLE);
        auto request=network_value(L,1);
        const auto operation=request.value("operation",std::string());
        if(operation!="http"&&operation!="connect"&&operation!="send"&&operation!="close")
            throw std::runtime_error("unsupported asynchronous network request");
        auto* scheduler=SourceScheduler::for_thread(L);
        if(!scheduler)throw std::runtime_error("network await requires a managed scheduler thread");
        if(!lua_isyieldable(L))throw std::runtime_error("network await requires a yieldable thread");
        const auto* raw=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
        const SourceBindings bindings=*raw;
        NetworkJson start={{"operation","start"},{"request",request}};
        auto started=NetworkJson::parse(network_codec_call(bindings,start.dump()));
        if(started.contains("error"))throw std::runtime_error(started["error"].get<std::string>());
        const uint64_t id=started.at("id").get<uint64_t>();
        NetworkJobDiscardGuard discard_guard{bindings,id};
        auto poll=[bindings,id]() mutable -> std::optional<std::string> {
            NetworkJson query={{"operation","result"},{"id",id}};
            auto result=network_codec_call(bindings,query.dump());
            if(result=="{\"ready\":false}")return std::nullopt;
            return result;
        };
        auto discard=[bindings,id]() mutable {
            network_discard_job(bindings,id);
        };
        auto claim=[bindings,id]() mutable {network_claim_job(bindings,id);};
        if(!scheduler->wait_external(L,std::move(poll),std::move(discard),std::move(claim)))throw std::runtime_error("network await capacity exceeded");
        discard_guard.armed=false;
        lua_settop(L,0);return lua_yield(L,0);
    } catch(const std::exception& e){luaL_error(L,"network: %s",e.what());return 0;}
}
int network_socket_read(lua_State* L) {
    try {
        NetworkJson id=network_value(L,1);
        if(!id.is_number()&&!id.is_string())throw std::runtime_error("invalid WebSocket id");
        auto* scheduler=SourceScheduler::for_thread(L);
        if(!scheduler)throw std::runtime_error("WebSocket read requires a managed scheduler thread");
        if(!lua_isyieldable(L))throw std::runtime_error("WebSocket read requires a yieldable thread");
        const auto* raw=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
        const SourceBindings bindings=*raw;
        auto poll=[bindings,id]() mutable -> std::optional<std::string> {
            NetworkJson query={{"operation","poll_ready"},{"id",id}};
            auto result=network_codec_call(bindings,query.dump());
            if(result=="{\"ready\":false}")return std::nullopt;
            return result;
        };
        if(!scheduler->wait_socket_read(L,std::move(poll)))throw std::runtime_error("WebSocket read capacity exceeded");
        lua_settop(L,0);return lua_yield(L,0);
    } catch(const std::exception& e){luaL_error(L,"network: %s",e.what());return 0;}
}
int network_defer_response(lua_State* L) {
    try {
        luaL_checktype(L,1,LUA_TFUNCTION);
        auto* scheduler=SourceScheduler::for_thread(L);
        if(!scheduler)throw std::runtime_error("network receiver requires a managed scheduler thread");
        scheduler->defer_response(L,1);return 0;
    } catch(const std::exception& e){luaL_error(L,"network receiver: %s",e.what());return 0;}
}
void install_network_compat(lua_State* L,const SourceBindings* bindings) {
    static const char* source=R"NETWORK(
local bridge,asyncAwait,socketRead,networkDefer,normalizedScriptBytecode=...
local encode,decode=crypt.base64encode,crypt.base64decode
local function await(request)
 if request.operation=="http" or request.operation=="connect" or request.operation=="send" or request.operation=="close" then return asyncAwait(request) end
 local id=bridge({operation="start",request=request}).id
 while true do
  local result=bridge({operation="result",id=id})
  if result.ready then assert(not result.value.error,result.value.error);return result.value end
  task.wait(0.005)
 end
end
local methods={GET=true,POST=true,PUT=true,PATCH=true,DELETE=true,HEAD=true,OPTIONS=true}
local compileSource=loadstring
function decompile(script)
 local bytes=normalizedScriptBytecode(script)
 assert(type(bytes)=="string","script bytecode is unavailable")
 assert(#bytes<=512*1024,"bytecode exceeds 512 KiB")
 local source=await({operation="decompile",BytecodeBase64=encode(bytes)}).Source
 local fn,err=compileSource(source,"=decompiled-source")
 assert(fn,"decompiler returned invalid source: "..tostring(err))
 return source
end
local function requestHeaders(headers,cookies)
 if cookies==nil then return headers end
 assert(type(cookies)=="table","request Cookies must be a table")
 local merged={};local count=0
 if headers~=nil then
  assert(type(headers)=="table","request Headers must be a table")
  for name,value in pairs(headers) do
   count+=1;assert(count<=128,"request Headers exceed limit");merged[name]=value
  end
 end
 local values={};local size=0;count=0
 for name,value in pairs(cookies) do
  count+=1;assert(count<=128,"request Cookies exceed limit")
  assert(type(name)=="string" and type(value)=="string","request Cookies must contain text")
  size+=#name+#value+3;assert(size<=8192,"request Cookie header exceeds limit")
  table.insert(values,name.."="..value)
 end
 if #values>0 then
  table.sort(values)
  local key="Cookie"
  for name in pairs(merged) do
   if type(name)=="string" and string.lower(name)=="cookie" then key=name;break end
  end
  if merged[key]~=nil then
   assert(type(merged[key])=="string","Cookie header must be text")
   table.insert(values,1,merged[key])
  end
  merged[key]=table.concat(values,"; ")
 end
 return merged
end
function request(options)
 assert(type(options)=="table","request options required")
 local method=options.Method or "GET"
 assert(type(method)=="string","HTTP method must be text")
 method=string.upper(method)
 assert(methods[method],"Invalid HTTP method")
 local request={operation="http",Url=options.Url,Method=method,Headers=requestHeaders(options.Headers,options.Cookies)}
 if options.Body~=nil then request.BodyBase64=encode(options.Body) end
 local result=await(request);result.Body=decode(result.BodyBase64);result.BodyBase64=nil
 return result
end
http_request=request;http={request=request}
local httpRequest=request
function HttpGet(url, cache)
 assert(type(url)=="string","HttpGet URL must be text")
 assert(cache==nil or type(cache)=="boolean","HttpGet cache flag must be boolean")
 local result=httpRequest({Url=url,Method="GET"})
 assert(result.Success,"HttpGet failed: HTTP "..tostring(result.StatusCode).." "..tostring(result.StatusMessage))
 return result.Body
end
httpget=HttpGet;http.HttpGet=HttpGet
WebSocket={}
function WebSocket.connect(url)
 local id=await({operation="connect",Url=url}).id
 local messages=Instance.new("BindableEvent");local closed=Instance.new("BindableEvent")
 local active=true;local closeNotified=false;local socket={OnMessage=messages.Event,OnClose=closed.Event}
 local function fireClosed() if closeNotified then return end;closeNotified=true;closed:Fire() end
 function socket:Send(data,binary)
  assert(active,"WebSocket is closed")
  await({operation="send",id=id,BodyBase64=encode(data),binary=binary==true})
 end
 function socket:Close()
  if not active then return end
  active=false;await({operation="close",id=id});fireClosed()
 end
 -- Return the socket before delivering frames already buffered with the
 -- upgrade response, so an immediate listener can observe the first event.
 networkDefer(function()
  while active do
   local ok,result=pcall(socketRead,id)
   if not active then break end
   if not ok then active=false;fireClosed();warn(result);break end
   for _,data in ipairs(result.messages or {}) do messages:Fire(decode(data)) end
   if result.error then active=false;fireClosed();warn(result.error);break end
   if result.closed then
    active=false;fireClosed()
    if result.transport_error then warn(result.transport_error) end
    break
   end
  end
 end)
 return socket
end
WebSocket.Connect=WebSocket.connect
ws=WebSocket;Websocket=WebSocket;websocket=WebSocket
return function(self,url,cache)
 assert(typeof(self)=="Instance" and self.ClassName=="DataModel","HttpGet requires DataModel")
 return HttpGet(url,cache)
end
)NETWORK";
    auto code=Luau::compile(source);if(luau_load(L,"=network-compat",code.data(),code.size(),0))lua_error(L);
    lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));lua_pushcclosurek(L,network_bridge,"network bridge",1,nullptr);
    lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));lua_pushcclosurek(L,network_async_await,"network await",1,network_async_continue);
    lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));lua_pushcclosurek(L,network_socket_read,"WebSocket read",1,network_async_continue);
    lua_pushcfunction(L,network_defer_response,"network receiver defer");
    lua_getfield(L,LUA_REGISTRYINDEX,"Headless.NormalizedScriptBytecode");
    lua_call(L,5,1);lua_setfield(L,LUA_REGISTRYINDEX,"Headless.HttpGet");
}
