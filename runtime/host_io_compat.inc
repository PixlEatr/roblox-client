// Headless console and teleport queue owned by this VM. Host commands use a
// private registry closure; console input never reads the JSON protocol pipe.
constexpr const char* kHostIORegistry="Headless.HostIO";
NetworkJson capture_host_io(lua_State* L,int index,int depth=0) {
    if(depth>8)throw std::runtime_error("host I/O result nesting exceeds limit");
    index=lua_absindex(L,index);
    if(!lua_istable(L,index))return network_value(L,index);
    size_t count=lua_objlen(L,index);bool array=true;int entries=0;
    lua_pushnil(L);while(lua_next(L,index)){
        if(++entries>512)throw std::runtime_error("host I/O result exceeds limit");
        if(lua_type(L,-2)!=LUA_TNUMBER||lua_tonumber(L,-2)<1||lua_tonumber(L,-2)>count||std::floor(lua_tonumber(L,-2))!=lua_tonumber(L,-2))array=false;
        lua_pop(L,1);
    }
    NetworkJson result=array?NetworkJson::array():NetworkJson::object();
    if(array)for(size_t i=1;i<=count;++i){lua_rawgeti(L,index,i);result.push_back(capture_host_io(L,-1,depth+1));lua_pop(L,1);}
    else {lua_pushnil(L);while(lua_next(L,index)){if(lua_type(L,-2)!=LUA_TSTRING)throw std::runtime_error("invalid host I/O result key");std::string key=lua_tostring(L,-2);result[key]=capture_host_io(L,-1,depth+1);lua_pop(L,1);}}
    return result;
}
int clipboard_set(lua_State* L) {
    luaL_checktype(L,1,LUA_TSTRING);size_t length=0;const char* text=lua_tolstring(L,1,&length);
    if(length>128*1024){luaL_error(L,"clipboard text exceeds 128 KiB");return 0;}
    auto* bindings=static_cast<const SourceBindings*>(lua_touserdata(L,lua_upvalueindex(1)));
    try {
        unsigned char* output=nullptr;size_t size=0;
        if(bindings->codec(101,reinterpret_cast<const unsigned char*>(text),length,&output,&size))throw std::runtime_error("clipboard bridge failed");
        auto release=[&](unsigned char* p){bindings->release(p,size);};std::unique_ptr<unsigned char,decltype(release)> owner(output,release);
        auto result=NetworkJson::parse(output,output+size);
        if(result.contains("error"))throw std::runtime_error(result["error"].get<std::string>());
        return 0;
    }catch(const std::exception& e){luaL_error(L,"setclipboard: %s",e.what());return 0;}
}
void install_host_io(lua_State* L,const SourceBindings* bindings) {
    static const char* source=R"HOSTIO(
local visible,title,text=false,"Luau Console",""
local events,eventBytes={},0
local inputs,nextInput={},0
local queued,queueBytes,nextQueue={},0,0
local function stringArg(value,limit)
 assert(type(value)=="string" and #value<=limit,"expected bounded text")
 return value
end
local function emit(kind,value,id,details)
 assert(#events<256 and eventBytes+#(value or "")<=128*1024,"console event limit exceeded; poll the host")
 local event={kind=kind,text=value,id=id}
 if details then for key,item in details do event[key]=item end end
 table.insert(events,event);eventBytes+=#(value or "")
end
function rconsolecreate()
 if not visible then emit("create",title);visible=true end
end
function rconsolehide()
 if visible then emit("destroy");visible=false end
end
function rconsoleclear() emit("clear");text="" end
function rconsolereset()
 text="";title="Luau Console"
 emit("clear");emit("title",title)
end
function rconsoledestroy()
 rconsolehide();text=""
 for _,entry in inputs do if entry.text==nil then entry.error="console was destroyed" end end
end
function rconsolesettitle(value)
 value=stringArg(value,256);assert(not value:find("%c"),"console title must not contain control characters")
 emit("title",value);title=value
end
local function consolewrite(value,details)
 value=stringArg(value:gsub("[\r\n]"," "),128*1024)
 assert(#text+#value<=128*1024,"console text limit exceeded; clear the console")
 rconsolecreate();emit("print",value,nil,details);text..=value
end
local function consolemessage(details,...)
 local parts={}
 for index=1,select("#",...) do parts[index]=tostring(select(index,...)) end
 consolewrite(table.concat(parts," "),details)
end
function rconsoleprint(...) consolemessage(nil,...) end
function rconsolewarn(...) consolemessage({level="warn"},...) end
function rconsoleinfo(...) consolemessage({level="info"},...) end
function rconsoleerror(...) consolemessage({level="error"},...) end
local consolecolors={
 {"black",0,0,0,"\27[0;30m"},{"red",255,0,0,"\27[0;31m"},
 {"green",0,255,0,"\27[0;32m"},{"yellow",255,255,0,"\27[0;33m"},
 {"blue",0,0,255,"\27[0;34m"},{"magenta",255,0,255,"\27[0;35m"},
 {"cyan",0,255,255,"\27[0;36m"},{"white",255,255,255,"\27[0;37m"},
 {"light_gray",211,211,211,"\27[0;37m"},{"dark_gray",169,169,169,"\27[1;30m"},
 {"light_red",255,102,102,"\27[1;31m"},{"light_green",102,255,102,"\27[1;32m"},
 {"light_blue",102,102,255,"\27[1;34m"},{"light_magenta",255,102,255,"\27[1;35m"},
 {"light_cyan",102,255,255,"\27[1;36m"},{"orange",255,165,0,"\27[0;33m"},
 {"pink",255,192,203,"\27[1;35m"},{"purple",128,0,128,"\27[0;35m"},
 {"brown",165,42,42,"\27[0;33m"},{"light_yellow",255,255,224,"\27[1;33m"},
}
function printconsole(value,color)
 value=stringArg(value,128*1024);assert(type(color)=="userdata","expected Color3")
 local channels={color.R,color.G,color.B};local rgb={}
 for index,channel in channels do
  assert(type(channel)=="number" and channel==channel and channel~=math.huge and channel~=-math.huge,"expected finite Color3")
  rgb[index]=math.max(0,math.min(255,math.floor(channel*255)))
 end
 local closest,distance=consolecolors[1],math.huge
 for _,candidate in consolecolors do
  local dr,dg,db=rgb[1]-candidate[2],rgb[2]-candidate[3],rgb[3]-candidate[4]
  local candidateDistance=dr*dr+dg*dg+db*db
  if candidateDistance<distance then closest,distance=candidate,candidateDistance end
 end
 consolewrite(value,{level="color",color_name=closest[1],color=closest[5]})
end
function rconsoleinput()
 local count=0;for _ in inputs do count+=1 end;assert(count<16,"console input limit exceeded")
 rconsolecreate();nextInput+=1;local id=nextInput
 emit("input",nil,id);local entry={};inputs[id]=entry
 while entry.text==nil and not entry.error do task.wait(.01) end
 inputs[id]=nil;assert(not entry.error,entry.error);return entry.text
end
function rconsoleinputsubmit(id,value)
 local entry=inputs[id];assert(entry and entry.text==nil and not entry.error,"console input request is not pending")
 entry.text=stringArg(value,16*1024)
end
function queue_on_teleport(source)
 source=stringArg(source,128*1024)
 assert(#queued<16 and queueBytes+#source<=256*1024,"teleport queue limit exceeded")
 local fn,err=loadstring(source,"=teleport-source");assert(fn,err)
 nextQueue+=1;table.insert(queued,{id=nextQueue,source=source});queueBytes+=#source
end
function getteleportqueue()
 local result={}
 for index,entry in queued do result[index]=entry.source end
 return result
end
function clearteleportqueue() queued={};queueBytes=0 end
consoleclear=rconsoleclear;consolecreate=rconsolecreate;consoledestroy=rconsoledestroy
consoleinput=rconsoleinput;consoleprint=rconsoleprint
consolewarn=rconsolewarn;consoleinfo=rconsoleinfo
consoleerror=rconsoleerror;consoleerr=rconsoleerror;rconsoleerr=rconsoleerror
rconsoleshow=rconsolecreate;rconsolename=rconsolesettitle;consolesettitle=rconsolesettitle
rprintconsole=printconsole
queueonteleport=queue_on_teleport;queueontp=queue_on_teleport
clear_teleport_queue=clearteleportqueue;cleartpqueue=clearteleportqueue
clear_queue_on_teleport=clearteleportqueue;clearqueueonteleport=clearteleportqueue
return function(command)
 local action=command.action
 if action=="drain" then local result=events;events={};eventBytes=0;return result end
 if action=="state" then
  local pending={};for id,entry in inputs do if entry.text==nil and not entry.error then table.insert(pending,id) end end;table.sort(pending)
  return {visible=visible,title=title,text=text,pending_inputs=pending,queued_scripts=#queued}
 end
 if action=="input" then rconsoleinputsubmit(command.id,command.text);return {accepted=true} end
 if action=="teleport_peek" then return table.clone(queued) end
 if action=="teleport_ack" then
  assert(type(command.ids)=="table","queue IDs required")
  local ids={};for _,id in command.ids do assert(type(id)=="number" and id%1==0,"invalid queue ID");ids[id]=true end
  local remaining={};for _,entry in queued do if ids[entry.id] then queueBytes-=#entry.source else table.insert(remaining,entry) end end
  queued=remaining;return {remaining=#queued}
 end
 error("unknown host I/O command")
end
)HOSTIO";
    auto code=Luau::compile(source);if(luau_load(L,"=host-io",code.data(),code.size(),0))lua_error(L);
    lua_call(L,0,1);lua_setfield(L,LUA_REGISTRYINDEX,kHostIORegistry);
    lua_pushlightuserdata(L,const_cast<SourceBindings*>(bindings));lua_pushcclosurek(L,clipboard_set,"setclipboard",1,nullptr);
    lua_pushvalue(L,-1);lua_setglobal(L,"setclipboard");lua_setglobal(L,"toclipboard");
}
std::string source_host_io(lua_State* L,const std::string& command) {
    int top=lua_gettop(L);
    try {
        lua_getfield(L,LUA_REGISTRYINDEX,kHostIORegistry);
        if(!lua_isfunction(L,-1))throw std::runtime_error("host I/O requires the source runtime");
        push_network_value(L,NetworkJson::parse(command));
        if(lua_pcall(L,1,1,0))throw std::runtime_error(lua_tostring(L,-1));
        auto result=capture_host_io(L,-1).dump(-1,' ',false,NetworkJson::error_handler_t::replace);lua_settop(L,top);return result;
    }catch(...){lua_settop(L,top);throw;}
}
