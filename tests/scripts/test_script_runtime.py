import unittest

from script_runtime import ClientScriptRuntime
from test_world_snapshot import fixture
from local_runtime import HOST


@unittest.skipUnless(HOST.is_file(), 'build the local runtime first')
class ScriptRuntimeTests(unittest.TestCase):
    def test_local_world_objects_persist_between_source_commands(self):
        world, _ = fixture()
        received_count = len(world.instances)
        runtime = ClientScriptRuntime(lambda: world, 123)
        created = runtime.execute('''
local folder=Instance.new("Folder",workspace)
folder.Name="ScriptCreatedFolder"
local part=Instance.new("Part",folder)
part.Name="ScriptCreatedPart"
part.Anchored=true
print("created",part.Anchored)
return part.Anchored
''')
        self.assertTrue(created['ok'], created)
        self.assertEqual(created['values'], [True])
        self.assertIn('created\ttrue', created['output'])
        second = runtime.execute('''
local part=workspace:FindFirstChild("ScriptCreatedFolder"):FindFirstChild("ScriptCreatedPart")
return part.Anchored,client.propertyOrigin(part,"Anchored")
''')
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [True, 'local'])
        self.assertEqual(len(world.instances), received_count)
        self.assertFalse(created['mutations_replicated_to_server'])

    def test_local_writes_before_script_error_are_preserved(self):
        world, _ = fixture()
        runtime = ClientScriptRuntime(lambda: world, 123)
        first = runtime.execute('''
local part=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
part.Anchored=true
error("authored script stopped")
''')
        self.assertFalse(first['ok'])
        second = runtime.execute('''
local part=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
return part.Anchored
''')
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [True])


if __name__ == '__main__':
    unittest.main()
