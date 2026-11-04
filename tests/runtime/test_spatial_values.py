"""Authored-Luau coverage for the immutable Region3 datatype."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_cframe_transforms as transforms


class Region3Tests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def test_constructor_properties_and_equality(self):
        self.run_script('''
local minimum=Vector3.new(-2,3,4)
local maximum=Vector3.new(6,11,16)
local region=Region3.new(minimum,maximum)
assert(typeof(region)=="Region3" and type(region)=="userdata")
vec(region.Size,Vector3.new(8,8,12))
vec(region.CFrame.Position,Vector3.new(2,7,10))
vec(region.CFrame.LookVector,-Vector3.zAxis)
assert(region==Region3.new(minimum,maximum))
assert(region~=Region3.new(maximum,minimum))
assert(region~=minimum and region~=nil)
local cframeGlobal=CFrame
CFrame=nil
vec(region.CFrame.Position,Vector3.new(2,7,10))
CFrame=cframeGlobal
''')

    def test_expands_negative_and_positive_bounds_to_grid(self):
        self.run_script('''
local region=Region3.new(Vector3.new(-1.5,1.2,4.1),Vector3.new(2.3,5.1,7.3))
local expanded=region:ExpandToGrid(4)
vec(expanded.CFrame.Position,Vector3.new(0,4,6))
vec(expanded.Size,Vector3.new(8,8,4))
vec(expanded:ExpandToGrid(4).Size,expanded.Size)
''')

    def test_signed_size_and_immutable_fields(self):
        self.run_script('''
local region=Region3.new(Vector3.new(4,8,2),Vector3.new(0,2,6))
vec(region.Size,Vector3.new(-4,-6,4))
vec(region.CFrame.Position,Vector3.new(2,5,4))
assert(not pcall(function()region.Size=Vector3.zero end))
assert(not pcall(function()region.CFrame=CFrame.identity end))
assert(not pcall(function()return region.Unknown end))
local part=Instance.new("Part")
assert(not pcall(function()part:SetAttribute("region",region)end))
assert(not pcall(function()region:ExpandToGrid(4)end))
''')

    def test_bindable_event_preserves_region3_value(self):
        source = transforms.HELPERS + '''
local region=Region3.new(Vector3.new(1,2,3),Vector3.new(5,8,11))
local event=Instance.new("BindableEvent")
local received=nil
event.Event:Connect(function(value)received=value end)
local originalRegion3=Region3
Region3={new=function()error("the replaced Region3 constructor must not be called")end}
event:Fire(region)
task.wait(0)
assert(typeof(received)=="Region3" and received==region)
vec(received.CFrame.Position,Vector3.new(3,5,7))
vec(received.Size,Vector3.new(4,6,8))
Region3=originalRegion3
return region,received
'''
        expected = {"$region3": [[1.0, 2.0, 3.0], [5.0, 8.0, 11.0]]}
        observed = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "source.luau").write_text(source)
            (path / "snapshot.json").write_text(json.dumps(transforms.SNAPSHOT))
            for host in transforms.HOSTS:
                with self.subTest(host=str(host)):
                    completed = subprocess.run(
                        [str(host), "--source", str(path / "source.luau"),
                         "--world-snapshot", str(path / "snapshot.json")],
                        capture_output=True, timeout=10)
                    result = json.loads(completed.stdout)
                    self.assertEqual(completed.returncode, 0, result)
                    self.assertTrue(result.get("ok"), result)
                    self.assertFalse(result.get("task_errors"), result)
                    observed.append(result["values"])
        self.assertEqual(observed, [[expected, expected]] * len(transforms.HOSTS))

    def test_invalid_constructor_and_grid_resolution_are_rejected(self):
        self.run_script('''
for _,attempt in {
    function()Region3.new()end,
    function()Region3.new(Vector3.zero)end,
    function()Region3.new(Vector3.zero,Vector2.zero)end,
    function()Region3.new(Vector3.new(0/0,0,0),Vector3.one)end,
    function()Region3.new(Vector3.zero,Vector3.one):ExpandToGrid(0)end,
    function()Region3.new(Vector3.zero,Vector3.one):ExpandToGrid(math.huge)end,
    function()Region3.new(Vector3.zero,Vector3.one):ExpandToGrid("4")end,
} do assert(not pcall(attempt))end
''')
