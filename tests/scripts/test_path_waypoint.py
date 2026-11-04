"""Waypoint values and binary-safe local value transport in the native VM."""
import unittest

import test_script_execution as script_tests
from test_script_execution import snapshot


class PathWaypointTests(unittest.TestCase):
    host = script_tests.ScriptExecutionTests.host
    execute = script_tests.ScriptExecutionTests.execute
    completed = script_tests.ScriptExecutionTests.completed

    def run_source(self, host, source):
        result=self.completed(host,self.execute(host,source))
        self.assertTrue(result['ok'],result)
        return result

    def test_construction_fields_action_identity_and_binary_label(self):
        result=self.run_source(self.host(),'''
local label="door"..string.char(0,255).."waypoint"
local p=PathWaypoint.new(Vector3.new(1,2,3),Enum.PathWaypointAction.Jump,label)
assert(typeof(p)=="PathWaypoint" and type(p)=="userdata")
assert(p.Position==Vector3.new(1,2,3) and p.Action==Enum.PathWaypointAction.Jump)
assert(p.Label==label)
assert(p==PathWaypoint.new(p.Position,p.Action,p.Label))
assert(p~=PathWaypoint.new(p.Position,Enum.PathWaypointAction.Walk,p.Label))
assert(PathWaypoint.new(Vector3.zero,Enum.PathWaypointAction.Walk).Label=="")
assert(not pcall(function()p.Label="changed"end))
assert(not pcall(function()return p.Unknown end))
assert(not pcall(function()PathWaypoint.new=nil end))
return p
''')
        self.assertEqual(result['values'],[{'$path_waypoint':{
            'position':[1,2,3],'action':1,'label_hex':b'door\0\xffwaypoint'.hex()}}])

    def test_constructor_rejects_wrong_datatypes(self):
        self.run_source(self.host(),'''
assert(not pcall(PathWaypoint.new))
assert(not pcall(PathWaypoint.new,{},Enum.PathWaypointAction.Walk))
assert(not pcall(PathWaypoint.new,Vector3.zero))
for _,bad in {Enum.KeyCode.W,{},1,"Walk",false}do
 assert(not pcall(PathWaypoint.new,Vector3.zero,bad))
end
assert(not pcall(PathWaypoint.new,Vector3.zero,Enum.PathWaypointAction.Walk,{}))
return true
''')

    def test_snapshot_roundtrip_uses_native_value_decoder(self):
        data=snapshot()
        payload={'$path_waypoint':{'position':[1.25,2,3],'action':2,'label_hex':'00ff41'}}
        data['nodes'].append({'id':'waypoint-fixture','name':'WaypointFixture','class_name':'Folder',
            'parent':data['services']['Workspace'],'properties':{'Payload':payload}})
        result=self.run_source(self.host(data),'''
PathWaypoint=nil
local p=workspace.WaypointFixture.Payload
assert(typeof(p)=="PathWaypoint" and p.Position==Vector3.new(1.25,2,3))
assert(p.Action==Enum.PathWaypointAction.Custom and p.Label==string.char(0,255,65))
local b=Instance.new("BindableFunction")
b.OnInvoke=function(value)assert(value==p);return value end
return b:Invoke(p)
''')
        self.assertEqual(result['values'],[payload])

    def test_malformed_snapshot_values_do_not_create_waypoints(self):
        for payload in [
            {'position':[1,2],'action':0,'label_hex':''},
            {'position':[1,2,3],'action':3,'label_hex':''},
            {'position':[1,2,3],'action':0,'label_hex':'zz'},
            {'position':[1,2,3],'action':0,'label_hex':'a'}]:
            data=snapshot();data['nodes'].append({'id':'waypoint-fixture','name':'WaypointFixture','class_name':'Folder',
                'parent':data['services']['Workspace'],'properties':{'Payload':{'$path_waypoint':payload}}})
            with self.subTest(payload=payload):
                self.run_source(self.host(data),'assert(not pcall(function()return workspace.WaypointFixture.Payload end));return true')
