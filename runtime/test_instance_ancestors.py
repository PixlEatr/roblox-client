"""Hierarchy queries on local trees and changing received snapshots."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms


class InstanceAncestorTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def test_nearest_match_excludes_receiver_and_distinguishes_class_inheritance(self):
        self.run_script('''
local outer=Instance.new("Model",workspace);outer.Name="Group"
local inner=Instance.new("Part",outer);inner.Name="Group"
local child=Instance.new("Folder",inner);child.Name="Leaf"
assert(child:FindFirstAncestor("Group")==inner)
assert(child:FindFirstAncestorOfClass("Model")==outer)
assert(child:FindFirstAncestorOfClass("BasePart")==nil)
assert(child:FindFirstAncestorWhichIsA("BasePart")==inner)
assert(child:FindFirstAncestorWhichIsA("PVInstance")==inner)
assert(child:FindFirstAncestorWhichIsA("Instance")==inner)
assert(child:FindFirstAncestor("Leaf")==nil)
assert(child:FindFirstAncestorOfClass("Folder")==nil)
assert(child:FindFirstAncestorWhichIsA("MissingClass")==nil)
assert(outer:IsAncestorOf(child) and child:IsDescendantOf(outer))
assert(not child:IsAncestorOf(outer) and not child:IsAncestorOf(child))
assert(game:IsAncestorOf(child) and not game:IsAncestorOf(game))
assert(game:FindFirstAncestorWhichIsA("Instance")==nil)
''')

    def test_local_rename_reparent_detach_and_destroy(self):
        self.run_script('''
local a=Instance.new("Model",workspace);a.Name="First"
local b=Instance.new("Model",workspace);b.Name="Second"
local folder=Instance.new("Folder",a)
local p=Instance.new("Part",folder)
a.Name="Renamed"
assert(p:FindFirstAncestor("First")==nil)
assert(p:FindFirstAncestor("Renamed")==a)
folder.Parent=b
assert(p:FindFirstAncestorOfClass("Model")==b)
assert(not a:IsAncestorOf(p) and b:IsAncestorOf(p))
folder.Parent=nil
assert(folder:IsAncestorOf(p) and not workspace:IsAncestorOf(p))
assert(p:FindFirstAncestorOfClass("Model")==nil)
folder:Destroy()
assert(p:FindFirstAncestorWhichIsA("Instance")==nil)
assert(not folder:IsAncestorOf(p))
''')

    def test_copied_methods_use_actual_receiver_and_reject_invalid_arguments(self):
        self.run_script('''
local a=Instance.new("Model");a.Name="First"
local b=Instance.new("Model");b.Name="Second"
local x=Instance.new("Folder",a);local y=Instance.new("Folder",b)
local find=x.FindFirstAncestor
assert(find(y,"Second")==b and find(y,"First")==nil)
assert(x.FindFirstAncestorOfClass(y,"Model")==b)
assert(x.FindFirstAncestorWhichIsA(y,"PVInstance")==b)
assert(a.IsAncestorOf(b,y) and not a.IsAncestorOf(b,x))
for _,method in ipairs({find,x.FindFirstAncestorOfClass,x.FindFirstAncestorWhichIsA}) do
    assert(not pcall(method,{},"Model"))
    assert(not pcall(method,x,{}))
    assert(not pcall(method,x))
end
assert(not pcall(a.IsAncestorOf,a,{}))
assert(not pcall(a.IsAncestorOf,a))
a.Name="Prefix"..string.char(0).."Suffix"
assert(x:FindFirstAncestor(a.Name)==a)
assert(x:FindFirstAncestor("Prefix")==nil)
''')

    def test_received_tree_queries_are_read_only(self):
        baseline=self.run_script('')
        results=self.run_script('''
local char=game:GetService("Players").LocalPlayer.Character
local h=char.Humanoid
assert(h:FindFirstAncestor("FixtureCharacter")==char)
assert(h:FindFirstAncestorOfClass("Model")==char)
assert(h:FindFirstAncestorWhichIsA("WorldRoot")==workspace)
assert(workspace:IsAncestorOf(h) and char:IsAncestorOf(h))
''')
        for before,result in zip(baseline,results):
            self.assertEqual(result.get('mutations'),before.get('mutations'))

    def test_unresolved_parent_terminates_search(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        next(n for n in snapshot['nodes'] if n['class_name']=='Model')['parent']='scope:not-received'
        self.run_script('''
local char=game:GetService("Players").LocalPlayer.Character
assert(char.Humanoid:FindFirstAncestorOfClass("Model")==char)
assert(char.Humanoid:FindFirstAncestorWhichIsA("WorldRoot")==nil)
assert(not workspace:IsAncestorOf(char.Humanoid))
''',snapshot)

    def test_received_parent_and_name_updates_are_visible_to_retained_handles(self):
        import persistent_runtime
        path=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime,'RUST_HOST',path):
            host=persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT)
        self.addCleanup(host.close)
        def execute(source):
            result=host.call({'op':'execute','source':source})
            self.assertTrue(result['ok'],result)
        execute('h=game:GetService("Players").LocalPlayer.Character.Humanoid;old=h.Parent;assert(old:IsAncestorOf(h))')
        result=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':9,'actions':[
            {'sequence':8,'kind':'set','id':'scope:character-1','fields':{'Name':'RenamedCharacter'}},
            {'sequence':9,'kind':'set','id':'scope:humanoid-1','fields':{'Parent':{'$ref':'scope:workspace'}}}]}})
        self.assertTrue(result['ok'],result)
        execute('''
assert(not old:IsAncestorOf(h) and workspace:IsAncestorOf(h))
assert(h:FindFirstAncestor("RenamedCharacter")==nil)
assert(h:FindFirstAncestorOfClass("Workspace")==workspace)
assert(old.HumanoidRootPart:FindFirstAncestor("RenamedCharacter")==old)
''')


if __name__=='__main__':unittest.main()
