"""sUNC-related behavior through real persistent local sessions, never a score shim."""
import copy
import hashlib
import json
import unittest

import test_script_execution as script_tests
from test_script_execution import snapshot, add_script
from world_snapshot import _received_value
from types import SimpleNamespace
from remote_server_events import RemoteServerEvents
from world_replication import World


class SuncRuntimeTests(unittest.TestCase):
    host = script_tests.ScriptExecutionTests.host
    execute = script_tests.ScriptExecutionTests.execute
    completed = script_tests.ScriptExecutionTests.completed

    def run_source(self, host, source):
        result = self.completed(host, self.execute(host, source, timeout_ms=100))
        self.assertTrue(result['ok'], result)
        return result

    def test_thread_origin_survives_environment_tampering_and_destruction(self):
        data=snapshot();add_script(data,'Owner','''
local owner=script
script=nil
return {owner=owner,thread=coroutine.create(function()return 7 end),
 fn=function()return 9 end}
''')
        result=self.run_source(self.host(data),'''
local value=require(game:GetService("ReplicatedStorage").Owner)
assert(getscriptfromthread(value.thread)==value.owner)
assert(not isexecutorclosure(value.fn))
setfenv(value.fn,getgenv());assert(not isexecutorclosure(value.fn))
local own=function()end;setfenv(own,getrenv());assert(isexecutorclosure(own))
assert(isexecutorclosure(getrenv) and isexecutorclosure(getscriptclosure))
script=value.owner;assert(getscriptfromthread(coroutine.running())==nil)
value.owner:Destroy();assert(getscriptfromthread(value.thread)==value.owner)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_completed_received_creation_exposes_constructor_defaults_and_aliases(self):
        data=snapshot()
        data['class_property_types']={'Part':{'Massless':9,'Transparency':11},
                                     'ImageLabel':{'Active':9},'LocalScript':{'Source':47}}
        workspace=data['services']['Workspace']
        data['nodes'].extend([
            {'id':'1:330','name':'KnownPart','class_name':'Part','parent':workspace,
             'properties':{},'constructor_defaults_known':True},
            {'id':'1:331','name':'UnknownPart','class_name':'Part','parent':workspace,'properties':{}},
            {'id':'1:332','name':'KnownImage','class_name':'ImageLabel','parent':workspace,
             'properties':{},'constructor_defaults_known':True},
            {'id':'1:333','name':'RemoteScript','class_name':'LocalScript','parent':workspace,
             'properties':{}}])
        data['script_sources']['1:333']={'source':'return 41'}
        result=self.run_source(self.host(data),'''
assert(workspace.KnownPart.Massless==false and workspace.KnownPart.Transparency==0)
workspace.KnownPart.Transparency=.7;assert(workspace.KnownPart.Transparency==.7)
assert(workspace.KnownImage.Active==false)
assert(not pcall(function()return workspace.UnknownPart.Massless end))
assert(game.Players.LocalPlayer.character==game.Players.LocalPlayer.Character)
local script=workspace.RemoteScript
local before=getscripthash(script);script.Source="return 42"
assert(script.Source=="return 42" and getscripthash(script)~=before)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_engine_globals_are_live_fallbacks_for_executor_jobs(self):
        host=self.host()
        self.run_source(host,'''getgenv().sharedProbe=41;getfenv().localProbe=7
assert(getgenv().localProbe==nil)
local old=getrenv().warn;getrenv().warn="updated"
assert(warn=="updated");getrenv().warn=old
local oldGame=getrenv().game;getrenv().game=nil
assert(game==nil);getrenv().game=oldGame
return true''')
        result=self.run_source(host,'return sharedProbe,localProbe,getgenv().localProbe')
        self.assertEqual(result['values'],[41,7,None])

    def test_thread_identity_is_inherited_and_enforces_bound_engine_access(self):
        result=self.run_source(self.host(),'''
assert(getthreadidentity()==8)
for _,id in {0,-0,8,math.huge} do setthreadidentity(id);assert(checkcaller()) end
setthreadidentity(8)
setthreadidentity(6)
local identity;task.spawn(function()identity=getthreadidentity();setthreadidentity(2)end)
assert(identity==6 and getthreadidentity()==6)
assert(not pcall(Instance.new,"Player"))
setthreadidentity(8);local p=Instance.new("Player")
assert(p.ClassName=="Player" and p.Parent==nil and p.UserId==0)
p.GameplayPaused=true;assert(p.GameplayPaused)
setthreadidentity(2);assert(not pcall(function()p.GameplayPaused=false end))
assert(p.GameplayPaused)
setthreadidentity(0);assert(not pcall(function()return game.DataCost end))
setthreadidentity(8);p:Destroy();return true
''')
        self.assertEqual(result['values'],[True])

    def test_hidden_properties_read_actual_aliases_tags_and_received_bytes(self):
        data=snapshot()
        data['class_property_types']={'MeshPart':{'AeroMeshData':46}}
        data['nodes'].append({'id':'fixture:mesh','name':'ReceivedMesh','class_name':'MeshPart',
                              'parent':data['services']['Workspace'],'properties':{'AeroMeshData':{'$bytes_hex':'00ff4142'}}})
        result=self.run_source(self.host(data),'''
local h=Instance.new("Humanoid");h.Health=73
assert(gethiddenproperty(h,"Health_XML")==73 and not isscriptable(h,"Health_XML"))
assert(sethiddenproperty(h,"Health_XML",31) and h.Health==31)
local p=Instance.new("Part");p:AddTag("beta");p:AddTag("alpha")
assert(gethiddenproperty(p,"Tags")=="alpha"..string.char(0).."beta")
assert(not isscriptable(p,"Tags") and not pcall(function()return p.Tags end))
assert(setscriptable(p,"Tags",true)==false)
assert(p.Tags==gethiddenproperty(p,"Tags"));setscriptable(p,"Tags",false)
local mesh
for _,v in getinstances() do if v.Name=="ReceivedMesh" then mesh=v end end
local bytes,hidden=gethiddenproperty(mesh,"AeroMeshData")
assert(bytes==string.char(0,255).."AB" and hidden)
assert(not pcall(function()return mesh.AeroMeshData end))
return true
''')
        self.assertEqual(result['values'],[True])

    def test_received_shared_string_resolution_is_session_scoped(self):
        data=b'\0\xffmesh payload';key=hashlib.blake2b(data,digest_size=16).digest()
        reference={'shared':key.hex()}
        first=SimpleNamespace(shared={key:data});second=SimpleNamespace(shared={})
        self.assertEqual(_received_value(first,reference,46),{'$bytes_hex':data.hex()})
        self.assertEqual(_received_value(second,reference,46),reference)

    def test_unknown_scriptability_and_received_hidden_enum(self):
        data=snapshot()
        data['nodes'].append({'id':'1:390','name':'ReceivedFormFactor','class_name':'Part',
                              'parent':data['services']['Workspace'],'properties':{'formFactorRaw':3}})
        result=self.run_source(self.host(data),'''
local p=workspace.ReceivedFormFactor
assert(isscriptable(p,"UnknownField")==nil)
assert(setscriptable(p,"UnknownField",true)==nil)
assert(not pcall(setscriptable,p,"UnknownField",7))
assert(not pcall(gethiddenproperty,p,"UnknownField"))
assert(not isscriptable(p,"formFactorRaw"))
assert(not pcall(function()return p.formFactorRaw end))
local value,hidden=gethiddenproperty(p,"formFactorRaw")
assert(value==Enum.FormFactor.Custom and hidden)
assert(setscriptable(p,"formFactorRaw",true)==false)
assert(p.formFactorRaw==Enum.FormFactor.Custom)
p.formFactorRaw=Enum.FormFactor.Plate
assert(p.formFactorRaw==Enum.FormFactor.Plate)
assert(not pcall(function()p.formFactorRaw=Enum.CameraType.Custom end))
local another=Instance.new("Part")
assert(not isscriptable(another,"formFactorRaw"))
assert(setscriptable(p,"formFactorRaw",false)==true)
assert(gethiddenproperty(p,"formFactorRaw")==Enum.FormFactor.Plate)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_reflected_integer_arguments_use_native_numeric_conversion(self):
        data=snapshot()
        data['class_events']={'ClickDetector':['MouseActionReplicated']}
        data['class_event_signatures']={'ClickDetector':{'MouseActionReplicated':[28,10]}}
        data['nodes'].append({'id':'1:391','name':'Detector','class_name':'ClickDetector',
                              'parent':data['services']['Workspace'],'properties':{}})
        result=self.run_source(self.host(data),'''
local event=workspace.Detector.MouseActionReplicated
replicatesignal(event,game.Players.LocalPlayer,3.75)
replicatesignal(event,game.Players.LocalPlayer,-3.75)
return true
''')
        self.assertEqual([event['arguments'][1] for event in result['remote_server_events']],[3,-3])

    def test_callbacks_retain_their_registered_identity(self):
        result=self.run_source(self.host(),'''
local signal=Instance.new("BindableEvent")
local method=Instance.new("BindableFunction")
local ids={}
setthreadidentity(6)
signal.Event:Connect(function()table.insert(ids,getthreadidentity())end)
method.OnInvoke=function()return getthreadidentity()end
setthreadidentity(0)
firesignal(signal.Event)
getconnections(signal.Event)[1]:Defer()
signal:Fire()
task.wait()
assert(#ids==3 and ids[1]==6 and ids[2]==6 and ids[3]==6)
assert(method:Invoke()==6 and getthreadidentity()==0)
setthreadidentity(8)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_connection_enumeration_keeps_newest_creator_and_disabled_entries(self):
        result=self.run_source(self.host(),'''
local event=Instance.new("BindableEvent")
local main=coroutine.running()
local first=event.Event:Connect(function()end)
local second,creator
task.spawn(function()
 creator=coroutine.running()
 second=event.Event:Connect(function()end)
end)
local list=getconnections(event.Event)
assert(#list==2 and list[1].Thread==creator and list[2].Thread==main)
list[1]:Disable()
assert(not getconnections(event.Event)[1].Enabled)
assert(getconnections(event.Event)[1].Thread==creator)
list[1]:Disconnect()
list=getconnections(event.Event)
assert(#list==1 and list[1].Thread==main)
first:Disconnect();assert(#getconnections(event.Event)==0)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_filesystem_default_accepts_asset_payload_above_previous_limit(self):
        result=self.run_source(self.host(),'''
local data=string.rep(string.char(0,255,41,42),512*1024)
writefile("assets/payload.bin",data)
assert(readfile("assets/payload.bin")==data)
assert(getfileinfo("assets/payload.bin")==2*1024*1024)
local perFile,total=setworkspacequota(100*1024*1024,256*1024*1024)
assert(perFile==100*1024*1024 and total==256*1024*1024)
assert(not pcall(setworkspacequota,perFile+1,total))
return true
''')
        self.assertEqual(result['values'],[True])

    def test_replicatesignal_queues_only_received_matching_signatures(self):
        data=snapshot()
        data['class_events']={'ClickDetector':['MouseActionReplicated'],
                              'ProximityPrompt':['TriggeredActionReplicated']}
        data['class_event_signatures']={'ClickDetector':{'MouseActionReplicated':[28,10]},
                                        'ProximityPrompt':{'TriggeredActionReplicated':[28]}}
        data['nodes'].append({'id':'1:77','name':'Detector','class_name':'ClickDetector',
                              'parent':data['services']['Workspace'],'properties':{}})
        data['nodes'].append({'id':'1:78','name':'Prompt','class_name':'ProximityPrompt',
                              'parent':data['services']['Workspace'],'properties':{}})
        host=self.host(data)
        result=self.run_source(host,'''
local d=workspace.Detector
local p=game.Players.LocalPlayer
assert(not pcall(replicatesignal,d.MouseActionReplicated))
assert(not pcall(replicatesignal,d.MouseActionReplicated,p,"invalid"))
assert(not pcall(replicatesignal,d.MouseActionReplicated,Instance.new("Player"),0))
assert(not pcall(replicatesignal,Instance.new("BindableEvent").Event))
replicatesignal(d.MouseActionReplicated,p,0)
fireclickdetector(d)
fireproximityprompt(workspace.Prompt)
return true
''')
        self.assertEqual(result['values'],[True])
        events=result['remote_server_events']
        self.assertEqual(len(events),3)
        self.assertEqual(events[0],{'node':'1:77','event':'MouseActionReplicated',
                                  'argument_types':[28,10],
                                  'arguments':[{'$ref':data['local_player']},0]})
        self.assertEqual(events[0],events[1])
        self.assertEqual(events[2],{'node':'1:78','event':'TriggeredActionReplicated',
                                  'argument_types':[28],'arguments':[{'$ref':data['local_player']}]})

    def test_overlay_has_its_own_display_surface_outside_coregui(self):
        data=snapshot();data['display']={'size':[800,600],'device_insets':[0,0,0,0],'core_insets':[0,0,0,0]}
        result=self.run_source(self.host(data),'''
local observed=0
game.CoreGui.DescendantAdded:Connect(function()observed+=1 end)
local overlay=gethui();assert(overlay:IsA("Folder") and overlay.Parent==nil)
local f=Instance.new("Frame",overlay);f.Size=UDim2.fromOffset(100,50)
assert(f.AbsoluteSize==Vector2.new(100,50))
local screen=Instance.new("ScreenGui",overlay)
local nested=Instance.new("Frame",screen);nested.Size=UDim2.fromScale(1,1)
assert(nested.AbsoluteSize==Vector2.new(800,600))
task.wait();assert(observed==0 and gethui()==overlay)
overlay:Destroy();assert(gethui()~=overlay and gethui().Parent==nil)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_reflected_signal_uses_received_signature_and_roundtrips_wire(self):
        schema={'classes':[{'id':0,'name':'ClickDetector','properties':[],'events':[0]},
                           {'id':1,'name':'Player','properties':[],'events':[]}],
                'events':[{'id':0,'class':0,'name':'MouseActionReplicated','arguments':[{'type':28},{'type':10}]}]}
        world=World(schema)
        world.instances[(1,77)]={'class':0,'properties':{}}
        world.instances[(1,23)]={'class':1,'properties':{}}
        event={'node':'1:77','event':'MouseActionReplicated','argument_types':[28,10],
               'arguments':[{'$ref':'1:23'},0]}
        sender=RemoteServerEvents();sent=[]
        sender.send(world,event,lambda c,p:sent.append((c,p)))
        decoded=World(copy.deepcopy(schema));decoded.packet(sent[0][1])
        self.assertEqual(decoded.events[-1]['arguments'],[(1,23),0])
        self.assertEqual(sender.summary()['reflected_records_submitted'],1)
        self.assertFalse(sender.summary()['server_delivery_confirmed'])
        for changed in ({'argument_types':[28,12]}, {'arguments':[{'$ref':'1:23'},.5]},
                        {'arguments':[{'$ref':'local:1'},0]}, {'event':'UnknownEvent'}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                sender.send(world,{**event,**changed},lambda c,p:sent.append((c,p)))
        self.assertEqual(len(sent),1)

    def test_decompile_reconstructs_real_bytecode_without_executing_it(self):
        result=self.run_source(self.host(),'''
local m=Instance.new("ModuleScript")
m.Source='shared.decompilerExecuted=true;local function f(n) return n*3 end;return f(7)'
local source=decompile(m)
assert(type(source)=="string" and source:find("Reconstructed locally",1,true))
assert(shared.decompilerExecuted==nil)
assert(assert(loadstring(source))()==21 and shared.decompilerExecuted)
assert(not pcall(decompile,Instance.new("Folder")))
return true
''')
        self.assertEqual(result['values'],[True])

    def test_script_touches_use_current_received_ownership_and_contact_transport(self):
        data=snapshot()
        for key,name in [('1:42','A'),('130:7','B')]:
            data['nodes'].append({'id':key,'name':name,'class_name':'Part',
                                  'parent':data['services']['Workspace'],'properties':{}})
        result=self.run_source(self.host(data),'''
firetouchinterest(workspace.A,workspace.B,true)
firetouchinterest(workspace.A,workspace.B,1)
return true
''')
        events=result['remote_server_events'];self.assertEqual(len(events),2)
        world=World({'classes':[{'name':'Part','properties':[],'events':[]}]})
        world.client_scope=5
        world.instances[(1,42)]={'class':0,'properties':{'NetworkOwnerV3':5}}
        world.instances[(130,7)]={'class':0,'properties':{'NetworkOwnerV3':1}}
        sent=[];sender=RemoteServerEvents()
        for event in events:
            sender.send(world,event,lambda *args,**kw:sent.append((args,kw)))
        self.assertEqual(sent[0],((7,bytes.fromhex('86 01 2a000000 8201 07000000 00 00')),{'application':4}))
        self.assertEqual(sent[1][0][1][-2:],b'\x03\x00')
        self.assertEqual(sender.summary()['script_contact_records_submitted'],2)
        self.assertFalse(sender.summary()['server_delivery_confirmed'])
        world.instances[(1,42)]['properties']['NetworkOwnerV3']=6
        with self.assertRaises(ValueError):sender.send(world,events[0],lambda *args,**kw:sent.append((args,kw)))
        world.instances[(1,42)]['properties']['NetworkOwnerV3']=5
        other=World(world.schema);other.instances=world.instances;other.client_scope=5
        with self.assertRaises(ValueError):sender.send(other,events[0],lambda *args,**kw:sent.append((args,kw)))
        self.assertEqual(len(sent),2)


if __name__=='__main__':unittest.main()
