local started=time()
debug.setmemorycategory("ScriptingDemo")
local retained=table.create(256,"work")
local completed={}
local event=Instance.new("BindableEvent")
spawn(function(elapsed,stamp)
    assert(type(elapsed)=="number" and stamp>=elapsed)
    completed.spawn=true
    event:Fire()
end)
delay(.05,function(elapsed,stamp)
    assert(elapsed>=.04 and stamp>=elapsed)
    completed.delay=true
    event:Fire()
end)
local ok,answer,missing=ypcall(function()
    task.wait(.005)
    return 42,nil
end)
assert(ok and answer==42 and missing==nil)
local unused=coroutine.create(function()error("cancelled coroutine ran")end)
task.cancel(unused)
while not (completed.spawn and completed.delay)do event.Event:Wait()end
assert(retained[256]=="work")
debug.resetmemorycategory()
event:Destroy()
return "Scripting APIs completed",2,coroutine.status(unused)=="dead",time()-started
