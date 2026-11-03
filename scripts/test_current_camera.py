"""Workspace camera lifecycle checked through the real persistent Luau VM."""
import copy
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class CurrentCameraTests(unittest.TestCase):
    def host(self, snapshot=None):
        host = PersistentLuauHost(copy.deepcopy(snapshot or SNAPSHOT))
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 100})
        self.assertTrue(result['ok'], result)
        return result

    def test_camera_exists_locally_before_any_property_read(self):
        result = self.execute(self.host(), '''
local camera=workspace:FindFirstChildOfClass("Camera")
return camera~=nil,camera==workspace.CurrentCamera,
    camera.Parent==workspace,camera==workspace.CurrentCamera,
    camera.FieldOfView,client.propertyOrigin(workspace,"CurrentCamera")
''')
        self.assertEqual(result['values'], [True, True, True, True, 70, 'local'])

    def test_snapshot_camera_reference_and_identity_are_preserved(self):
        snapshot=copy.deepcopy(SNAPSHOT)
        workspace=snapshot['services']['Workspace']
        if isinstance(workspace,dict):workspace=workspace['$ref']
        snapshot['nodes'].append({'id':'fixture-camera','class_name':'Camera',
                                 'name':'SelectedView','parent':workspace,
                                 'properties':{'Archivable':True}})
        node=next(n for n in snapshot['nodes'] if n['id']==workspace)
        node['properties']['CurrentCamera']={'$ref':'fixture-camera'}
        result=self.execute(self.host(snapshot), '''
return workspace.CurrentCamera.Name,
    workspace.CurrentCamera==workspace:FindFirstChild("SelectedView")
''')
        self.assertEqual(result['values'], ['SelectedView', True])

    def test_selection_destroys_old_cameras_and_rejects_wrong_type(self):
        result = self.execute(self.host(), '''
local old=workspace.CurrentCamera
local other=Instance.new("Camera",workspace)
local selected=Instance.new("Camera",workspace)
workspace.CurrentCamera=selected
local rejected=not pcall(function()workspace.CurrentCamera=Instance.new("Folder")end)
return workspace.CurrentCamera==selected,old.Parent==nil,other.Parent==nil,
    rejected,workspace.CurrentCamera==selected
''')
        self.assertEqual(result['values'], [True] * 5)

    def test_replacement_budget_failure_keeps_previous_camera_valid(self):
        result=self.execute(self.host(), '''
local previous=workspace.CurrentCamera
local filler=Instance.new("Folder")
local exhausted=false
for _=1,200 do
    if not pcall(function()filler:SetAttribute("Padding",string.rep("x",1000))end) then
        exhausted=true;break
    end
end
local replaced=pcall(function()workspace.CurrentCamera=nil end)
return exhausted,not replaced,workspace.CurrentCamera==previous,previous.Parent==workspace
''')
        self.assertEqual(result['values'], [True] * 4)

    def test_nil_detachment_and_destroy_create_fresh_cameras(self):
        result = self.execute(self.host(), '''
local first=workspace.CurrentCamera
workspace.CurrentCamera=nil
local second=workspace.CurrentCamera
second.Parent=nil
local third=workspace.CurrentCamera
third:Destroy()
local fourth=workspace.CurrentCamera
return first~=second,second~=third,third~=fourth,fourth.Parent==workspace,
    first.Parent==nil,second.Parent==nil,third.Parent==nil
''')
        self.assertEqual(result['values'], [True] * 7)

    def test_detached_assignment_is_replaced_without_adopting_foreign_camera(self):
        result = self.execute(self.host(), '''
local camera=Instance.new("Camera")
workspace.CurrentCamera=camera
return workspace.CurrentCamera~=camera,camera.Parent==nil,
    workspace.CurrentCamera.Parent==workspace
''')
        self.assertEqual(result['values'], [True] * 3)

    def test_selected_camera_survives_destruction_of_camera_ancestor(self):
        result = self.execute(self.host(), '''
local old=workspace.CurrentCamera
local selected=Instance.new("Camera",old)
workspace.CurrentCamera=selected
return workspace.CurrentCamera==selected,selected.Parent==workspace,old.Parent==nil
''')
        self.assertEqual(result['values'], [True] * 3)

    def test_ancestor_removal_invalidates_current_camera(self):
        result = self.execute(self.host(), '''
local folder=Instance.new("Folder",workspace)
local camera=Instance.new("Camera",folder)
workspace.CurrentCamera=camera
folder.Parent=nil
return workspace.CurrentCamera~=camera,workspace.CurrentCamera.Parent==workspace,
    camera.Parent==folder
''')
        self.assertEqual(result['values'], [True] * 3)

    def test_change_signal_fires_for_replacement_not_reads_or_same_assignment(self):
        host = self.host()
        self.execute(host, '''
shared.changes=0
workspace:GetPropertyChangedSignal("CurrentCamera"):Connect(function()
    assert(workspace.CurrentCamera.Parent==workspace)
    shared.changes+=1
end)
local same=workspace.CurrentCamera
workspace.CurrentCamera=same
workspace.CurrentCamera=nil
''')
        host.call({'op': 'poll'})
        self.assertEqual(self.execute(host, 'return shared.changes')['values'], [1])

    def test_camera_frame_and_focus_aliases_stay_synchronized(self):
        result = self.execute(self.host(), '''
local camera=workspace.CurrentCamera
camera.CFrame=CFrame.new(1,2,3)
local first=camera.CoordinateFrame==camera.CFrame
camera.CoordinateFrame=CFrame.new(4,5,6)
camera.focus=CFrame.new(7,8,9)
return first,camera.CFrame.Position.X,camera.Focus==camera.focus
''')
        self.assertEqual(result['values'], [True, 4, True])


if __name__ == '__main__':
    unittest.main()
