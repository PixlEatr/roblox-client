"""Documented API behavior, independent of the obfuscated sUNC runner/score."""
import unittest
import test_local_compat as compat


class SuncSemanticsTests(unittest.TestCase):
    run_script = compat.LocalCompatibilityTests.run_script

    def test_gc_filters_distinguish_signed_zero_and_find_nan_values(self):
        self.run_script('''
local positive=function()return 0,"zero-match" end
local negative=function()return -0.0,"zero-match" end
local nan=0/0
local captured=function()return nan,"captured-nan" end
nan=nan+0 -- Retain a real upvalue instead of a compiler-propagated constant.
local found=filtergc("function",{Constants={-0.0,"zero-match"},IgnoreExecutor=false})
assert(table.find(found,negative) and not table.find(found,positive))
local up=filtergc("function",{Upvalues={nan},IgnoreExecutor=false})
assert(table.find(up,captured))
local marker={};local value=setmetatable({value=nan},{__index=marker})
assert(filtergc("table",{Values={nan},Metatable=getmetatable(value)},true)==value)
''')

    def test_executor_environment_is_shared_but_thread_writes_are_separate(self):
        self.run_script('''
local root=getgenv()
assert(root~=getfenv(0))
root.sharedValue="root"
getfenv().threadValue="thread"
assert(sharedValue=="root" and root.threadValue==nil)
sharedValue="shadow";assert(root.sharedValue=="root")
sharedValue=nil;assert(sharedValue=="root")
local observed
task.spawn(function() observed=getgenv();observed.childValue=17 end)
assert(observed==root and childValue==17)
assert(assert(loadstring("return getgenv(), sharedValue"))()==root)
''')

    def test_clones_and_hooks_cover_every_closure_pair(self):
        self.run_script('''
local function make(kind,replacement)
 if kind=="C" then return clonefunction(replacement and type or tostring) end
 local f=replacement and function(...)return "new",select("#",...),... end
                    or function(...)return "old",select("#",...),... end
 return kind=="NC" and newcclosure(f) or f
end
local function same(a,b)
 assert(a.n==b.n)
 for i=1,a.n do assert(a[i]==b[i],tostring(i)) end
end
for _,a in {"L","NC","C"} do for _,b in {"L","NC","C"} do
 local target,replacement=make(a,false),make(b,true)
 local alias=target
 local before=table.pack(target("hello",nil,8))
 local after=table.pack(replacement("hello",nil,8))
 local independent=clonefunction(target)
 local old=hookfunction(target,replacement)
 same(table.pack(alias("hello",nil,8)),after)
 same(table.pack(old("hello",nil,8)),before)
 same(table.pack(independent("hello",nil,8)),before)
 local hookedClone=clonefunction(target)
 hookfunction(target,make("NC",false))
 same(table.pack(hookedClone("hello",nil,8)),after)
 restorefunction(target)
 same(table.pack(target("hello",nil,8)),before)
 assert(not pcall(restorefunction,target))
end end
assert(not pcall(restorefunction,function()end))
''')

    def test_native_hooks_keep_yields_results_errors_and_captures(self):
        self.run_script('''
local target=clonefunction(tostring)
local replacement=newcclosure(function(value)
 local a,b=coroutine.yield("waiting",value)
 return a,nil,b
end)
local old=hookfunction(target,replacement)
local thread=coroutine.create(function()return target(12)end)
local ok,label,value=coroutine.resume(thread)
assert(ok and label=="waiting" and value==12)
local result=table.pack(coroutine.resume(thread,3,4))
assert(result.n==4 and result[1] and result[2]==3 and result[3]==nil and result[4]==4)
restorefunction(target);assert(target(3)=="3" and old(4)=="4")
local marker={}
local wrapped=newcclosure(function()coroutine.yield();error(marker)end)
assert(debug.getinfo(wrapped).nups==0)
assert(not pcall(debug.getupvalues,wrapped))
local copy=clonefunction(wrapped)
local t=coroutine.create(copy);assert(coroutine.resume(t))
local success,err=coroutine.resume(t);assert(not success and err==marker)
for i=1,200 do
 local captured={i};local f=function()return captured end
 local original=hookfunction(f,newcclosure(function()return 0 end))
 assert(f()==0 and original()==captured)
 restorefunction(f);assert(f()==captured)
end
''')

    def test_function_hash_tracks_code_and_constants_not_source_name(self):
        self.run_script('''
local a=assert(loadstring('return function(...)return "a",... end',"=one"))()
local b=assert(loadstring('return function(...)return "a",... end',"=two"))()
local hash=getfunctionhash(a)
assert(#hash==96 and hash:match("^[0-9a-f]+$") and hash==getfunctionhash(b))
assert(hash==getfunctionhash(clonefunction(a)))
local c=assert(loadstring('return function(...)return "b",... end'))()
assert(hash~=getfunctionhash(c))
debug.setconstant(b,1,"changed");assert(hash~=getfunctionhash(b))
assert(not pcall(getfunctionhash,print))
''')

    def test_filtergc_matches_real_values_and_combines_filters(self):
        self.run_script('''
local marker,newMeta={},{}
local t=setmetatable({key=marker,other="needle"},newMeta)
local f=function() return marker,"unique-filtergc-literal" end
local all=filtergc("function",{IgnoreExecutor=false,Constants={"unique-filtergc-literal"},Upvalues={marker}})
assert(table.find(all,f))
assert(not table.find(filtergc("function",{Constants={"unique-filtergc-literal"}}),f))
assert(filtergc("table",{Keys={"key","other"},Values={marker,"needle"},
 KeyValuePairs={key=marker},Metatable=newMeta},true)==t)
assert(filtergc("table",{KeyValuePairs={key=marker,missing=true},Metatable=newMeta},true)==nil)
assert(table.find(filtergc("function",{IgnoreExecutor=false,Hash=getfunctionhash(f)}),f))
assert(not pcall(filtergc,"thread",{}))
assert(not pcall(filtergc,"function",{Name=7}))
''')

    def test_signal_and_connection_fire_are_immediate_defer_is_queued(self):
        self.run_script('''
local event=Instance.new("BindableEvent")
local calls={};local argument={};local thread
event.Event:Connect(function(...)
 table.insert(calls,table.pack(...));thread=coroutine.running()
end)
local connection=getconnections(event.Event)[1]
connection:Fire(argument,nil,7)
assert(#calls==1 and calls[1].n==3 and calls[1][1]==argument and calls[1][3]==7)
assert(thread~=coroutine.running())
connection:Defer("later");assert(#calls==1)
task.wait();assert(#calls==2 and calls[2][1]=="later")
firesignal(event.Event,argument,nil,9)
assert(#calls==3 and calls[3].n==3 and calls[3][1]==argument and calls[3][3]==9)
connection:Disable();firesignal(event.Event,10);assert(#calls==3)
connection:Enable();connection:Disconnect();firesignal(event.Event,11);assert(#calls==3)
''')

    def test_instance_cache_is_discoverable_and_workspace_alias_is_real(self):
        self.run_script('''
assert(game.workspace==workspace and game.Workspace==workspace)
local parent=Instance.new("Folder");local part=Instance.new("Part",parent)
local found
for _,value in getreg() do
 if type(value)=="table" then
  for key,cached in value do if rawequal(cached,part) then found=value end end
 end
end
assert(found)
cache.invalidate(part)
for _,cached in found do assert(not rawequal(cached,part)) end
local fresh=parent.Part
assert(fresh~=part and compareinstances(fresh,part) and cache.iscached(fresh))
''')

    def test_blocked_file_extensions_fail_atomically(self):
        self.run_script('''
for _,extension in {"exe","com","scr","pif","cpl","msc","bat","cmd","ps1","psd1",
 "vbs","vbe","js","jse","wsf","wsh","hta","scf","lnk","zip","rar","7z","cab",
 "iso","img","xml","msi","msp","reg","inf","url"} do
 local name="blocked/child/file."..extension:upper()
 assert(not pcall(writefile,name,"data"),extension)
 assert(not pcall(appendfile,name,"data"),extension)
 assert(not isfile(name) and not isfolder("blocked"))
end
writefile("fine.lua","return 1");appendfile("fine.lua"," --ok")
assert(assert(loadfile("fine.lua"))()==1)
''')

    def test_inactive_prototypes_preserve_metadata_but_cannot_execute(self):
        self.run_script('''
local count=0
local function outer(value)
 local function inner(a)
  count+=1
  local function child()return "child constant" end
  return value,a,child
 end
 return inner
end
local view=debug.getproto(outer,1)
assert(type(view)=="function" and islclosure(view))
assert(debug.info(view,"n")=="inner")
assert(debug.info(view,"a")==1)
assert(not pcall(view))
assert(not pcall(clonefunction(view)))
assert(not pcall(newcclosure(view)))
assert(not coroutine.resume(coroutine.create(view)))
local mt=setmetatable({},{__call=view});assert(not pcall(function()mt()end))
assert(not pcall(hookfunction,view,function()end))
assert(not pcall(hookfunction,function()end,view))
assert(count==0 and #debug.getproto(outer,1,true)==0)
local nested=debug.getprotos(view)[1]
assert(debug.getconstant(nested,1)=="child constant" and not pcall(nested))
local live=outer(73)
assert(table.find(debug.getproto(outer,1,true),live))
local value,arg=live("x");assert(value==73 and arg=="x" and count==1)
assert(not pcall(debug.getproto(outer,1)) and count==1)
''')

    def test_debug_numeric_levels_and_integer_bounds(self):
        self.run_script('''
local function read()
 local literal="numeric-level-constant"
 local found=false
 for _,value in debug.getconstants(1) do if value==literal then found=true end end
 assert(found)
 local function nested()return 5 end
 assert(#debug.getprotos(1)==1)
 return nested
end
read()
local function hasConstant()return "before" end
for _,index in {0,-1,0.5,2^60,math.huge,0/0} do
 assert(not pcall(debug.getconstant,hasConstant,index))
 assert(not pcall(debug.getproto,read,index))
 assert(not pcall(debug.getstack,index))
end
assert(not pcall(debug.getconstant,0,1))
''')

    def test_upvalue_levels_and_primitive_userdata_metatables(self):
        self.run_script('''
local captured={value=1}
local function change()
 assert(debug.getupvalue(1,1)==captured)
 assert(debug.getupvalues(1)[1]==captured)
 debug.setupvalue(1,1,{value=9})
 return captured.value
end
change();assert(debug.getupvalue(change,1).value==9)
local ref=1
local function edit()debug.setupvalue(1,1,ref+1)end
ref=2;edit();assert(ref==3)
for _,f in {print,newcclosure(function()end)} do
 assert(not pcall(debug.getupvalue,f,1))
 assert(not pcall(debug.getupvalues,f))
 assert(not pcall(debug.setupvalue,f,1,0))
end
local object=newproxy(true);local meta={__index={answer=42},__metatable="locked"}
assert(setrawmetatable(object,meta)==object and object.answer==42)
assert(getrawmetatable(object)==meta)
local old=getrawmetatable(0)
assert(setrawmetatable(0,{__index={answer=42}})==0 and (17).answer==42)
setrawmetatable(0,old)
assert(isexecutorclosure(debug.getconstant) and isexecutorclosure(cache.invalidate))
assert(isexecutorclosure(WebSocket.connect) and isexecutorclosure(Drawing.new))
assert(not isexecutorclosure(debug.info))
''')

    def test_touch_booleans_and_numbers_share_the_same_event_semantics(self):
        self.run_script('''
local a,b=Instance.new("Part"),Instance.new("Part")
local touches,ends=0,0
a.Touched:Connect(function(other)assert(other==b);touches+=1 end)
b.TouchEnded:Connect(function(other)assert(other==a);ends+=1 end)
firetouchinterest(a,b,true);firetouchinterest(a,b,0)
firetouchinterest(a,b,false);firetouchinterest(a,b,1)
task.wait()
assert(touches==2 and ends==2)
assert(not pcall(firetouchinterest,a,b,2))
''')


if __name__ == "__main__":
    unittest.main()
