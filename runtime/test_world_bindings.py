"""Synthetic snapshot-to-Luau integration tests; never uses a live capture."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from local_runtime import HOST, run_source

RUNTIME = Path(__file__).resolve().parent
SNAPSHOT = json.loads((RUNTIME / 'tests/world_snapshot.json').read_text())


@unittest.skipUnless(HOST.is_file(), 'build the local Luau host first')
class WorldBindingsTests(unittest.TestCase):
    def test_repeated_class_queries_preserve_transitive_branches_and_cycles(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot.setdefault('class_parents', {}).update({
            'FixtureLeaf': ['FixtureLeft', 'FixtureRight'],
            'FixtureLeft': 'FixtureAncestor',
            'FixtureRight': ['FixtureLoop', 17],
            'FixtureLoop': 'FixtureRight',
        })
        snapshot['nodes'].append({
            'id': 'fixture:class-leaf', 'class_name': 'FixtureLeaf',
            'name': 'ClassLeaf', 'parent': 'scope:workspace', 'properties': {},
        })
        result = run_source('''
local leaf=workspace.ClassLeaf
for _=1,100 do
    assert(not leaf:IsA("UnrelatedClass"))
    assert(leaf:IsA("FixtureAncestor") and leaf:IsA("FixtureRight"))
    assert(leaf:IsA("FixtureLeft") and leaf:IsA("FixtureLoop"))
    assert(leaf:IsA("FixtureLeaf") and leaf:IsA("Instance") and leaf:IsA("Object"))
end
assert(not workspace:IsA("FixtureAncestor"))
return true
''', world_snapshot=snapshot, timeout_ms=1000)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True])

    def script_mutations(self, result, snapshot=None):
        # Startup now creates client-owned services and a current camera.
        # Verify that exact prefix independently before checking script effects.
        baseline=run_source('return true',world_snapshot=snapshot or SNAPSHOT)
        self.assertTrue(baseline['ok'],baseline)
        prefix=baseline['mutations']
        self.assertEqual(result['mutations'][:len(prefix)],prefix)
        return result['mutations'][len(prefix):]

    def test_script_reads_world_and_records_only_local_mutations(self):
        original = copy.deepcopy(SNAPSHOT)
        result = run_source((RUNTIME / 'tests/world_script.luau').read_text(),
                            world_snapshot=SNAPSHOT, timeout_ms=1000)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'][0], True)
        self.assertEqual(result['values'][-1], {'$vector3': [4.0, 5.0, 6.0]})
        self.assertIn('world local simulation\tFixturePlayer', result['output'])
        self.assertIn('WARN: mutation is local only', result['output'])
        mutations=self.script_mutations(result)
        self.assertEqual([(m['kind'], m.get('property')) for m in mutations],
                         [('set', 'Anchored'), ('set', 'Parent'), ('create', None),
                          ('set', 'Parent'), ('set', 'Name')])
        created = next(m['node'] for m in mutations if m['kind'] == 'create')
        self.assertEqual(created['class_name'], 'Folder')
        self.assertTrue(created['id'].startswith('local:'))
        self.assertEqual(SNAPSHOT, original)

    def test_missing_property_remains_unavailable(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id'] == 'scope:root-1')
        root['properties'].pop('Anchored')
        result = run_source('local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart"); return p.Anchored',
                            world_snapshot=snapshot)
        self.assertFalse(result['ok'])
        self.assertIn('Anchored', result['error'])
        self.assertEqual(self.script_mutations(result,snapshot), [])

    def test_per_node_provenance_marks_verified_class_default(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id'] == 'scope:root-1')
        # The exporter writes the reconstructed value and its evidence tag on
        # the node; the VM must preserve the distinct origin.
        root['properties']['Anchored'] = False
        root.setdefault('origins', {})['Anchored'] = 'class_default'
        result = run_source('local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart"); return p.Anchored,client.propertyOrigin(p,"Anchored")',
                            world_snapshot=snapshot)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, 'class_default'])

    def test_known_local_writes_have_local_provenance_when_received_value_is_missing(self):
        result = run_source('local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart"); local ok=pcall(function() p.CanCollide=false end); return ok,client.propertyOrigin(p,"CanCollide")',
                            world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, 'local'])
        mutations=self.script_mutations(result)
        self.assertEqual([(m['kind'],m['property'],m['value']) for m in mutations],
                         [('set','CanCollide',False)])

    def test_special_numbers_unresolved_refs_and_incomplete_cframe(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id'] == 'scope:root-1')
        root['properties']['Special'] = {
            'positive': {'$number': 'inf'},
            'negative': {'$number': '-inf'},
            'nan': {'$number': 'nan'},
        }
        root['properties']['MissingRef'] = {'$ref': 'scope:streamed-out'}
        root['properties']['CFrame'] = {'$cframe': {
            'position': [1, 2, 3], 'rotation_id': 'packed:unknown'}}
        source = '''
local root = game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
local special = root.Special
local rotation_ok = pcall(function() return root.CFrame.XVector end)
return root.MissingRef == nil, special.positive, special.negative, special.nan,
       root.CFrame.Position, rotation_ok, client.propertyOrigin(root, "MissingRef")
'''
        result = run_source(source, world_snapshot=snapshot)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, {'$number': 'inf'},
                         {'$number': '-inf'}, {'$number': 'nan'},
                         {'$vector3': [1.0, 2.0, 3.0]}, False,
                         'unresolved_reference'])

    def test_new_part_uses_verified_local_constructor_default(self):
        result = run_source('local part=Instance.new("Part"); return part.Anchored, client.propertyOrigin(part,"Anchored")',
                            world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, 'class_default'])
        created = next(m['node'] for m in self.script_mutations(result) if m['kind'] == 'create')
        self.assertFalse(created['properties']['Anchored'])
        self.assertEqual(created['origins']['Anchored'], 'class_default')
        self.assertEqual(created['properties']['Size']['$vector3'], [4, 1.2000000476837158, 2])

    def test_measured_constructor_names_defaults_inheritance_and_types(self):
        result = run_source('''
local s=Instance.new("StringValue")
assert(s.Name=="Value" and s.Value=="" and s:IsA("ValueBase"))
s.Value="hello"
local h=Instance.new("Humanoid")
assert(h.Health==100 and h.WalkSpeed==16)
local m=Instance.new("MeshPart")
assert(m:IsA("BasePart") and m:IsA("PVInstance") and not m.Anchored)
assert(m.Position==Vector3.new(0,0,0))
m.Position=Vector3.new(1,2,3)
assert(m.CFrame.Position==m.Position)
local object=Instance.new("ObjectValue")
assert(object.Value==nil)
object.Value=m
assert(object.Value==m)
local badString=pcall(function() s.Value=true end)
local badNumber=pcall(function() h.WalkSpeed="fast" end)
local badRef=pcall(function() object.Value=17 end)
local badReadonly=pcall(function() h.RootPart=m end)
return s.Value,badString,badNumber,badRef,badReadonly
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['hello', False, False, False, False])

    def test_all_reflected_classes_obey_measured_creatability(self):
        registry = json.loads((RUNTIME / 'class-constructors-745.json').read_text())['classes']
        self.assertEqual(sum(c['creatable'] for c in registry.values()), 341)
        names = sorted(registry)
        # Bound each VM's mutation output while checking the complete class set.
        for start in range(0, len(names), 10):
            batch = names[start:start+10]
            lines = []
            for name in batch:
                reference = registry[name]
                if reference['creatable']:
                    lines.append(f'''do
local obj=Instance.new({json.dumps(name)})
assert(obj.ClassName=={json.dumps(name)})
assert(obj.Name=={json.dumps(reference['name'])})
assert(obj:IsA({json.dumps(reference['parent'])}))
assert(obj.Parent==nil)
end''')
                else:
                    lines.append(f'assert(not pcall(Instance.new,{json.dumps(name)}))')
            result = run_source('\n'.join(lines) + '\nreturn true', world_snapshot=SNAPSHOT)
            self.assertTrue(result['ok'], (batch, result))

    def test_run_service_modes_are_callable_methods(self):
        result = run_source('''
local rs=game:GetService("RunService")
return rs:IsClient(),rs:IsServer(),rs:IsStudio(),rs:IsRunning(),type(rs.IsClient)
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, False, False, True, 'function'])

    def test_origin_tags_survive_snapshot_reimport_for_instance_fields(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id'] == 'scope:root-1')
        root['origins'] = {'Anchored': 'local', 'Name': 'issued_session',
                           'Parent': 'derived_received'}
        result = run_source('local root=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart"); return client.propertyOrigin(root,"Anchored"), client.propertyOrigin(root,"Name"), client.propertyOrigin(root,"Parent")',
                            world_snapshot=snapshot)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['local', 'issued_session', 'derived_received'])

    def test_cframe_position_writes_stay_consistent_and_anchored_is_typed(self):
        source = '''
local root=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
root.CFrame=CFrame.new(5,6,7)
assert(root.Position == Vector3.new(5,6,7))
root.Position=Vector3.new(8,9,10)
assert(root.CFrame.Position == Vector3.new(8,9,10))
local ok=pcall(function() root.Anchored="false" end)
return ok, root.Position, root.CFrame.Position
'''
        result = run_source(source, world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, {'$vector3': [8.0, 9.0, 10.0]},
                         {'$vector3': [8.0, 9.0, 10.0]}])
        mutations=self.script_mutations(result)
        self.assertEqual(len(mutations),8)
        for offset,position in [(0,[5,6,7]),(4,[8,9,10])]:
            batch=mutations[offset:offset+4]
            self.assertTrue(all(m['id']=='scope:root-1' and m['kind']=='set' for m in batch))
            self.assertEqual({m['property']:m['value'] for m in batch},{
                'CFrame':{'$cframe':{'position':position,'rotation':[1,0,0,0,1,0,0,0,1]}},
                'Position':{'$vector3':position},'Orientation':{'$vector3':[0,0,0]},
                'Rotation':{'$vector3':[0,0,0]}})


if __name__ == '__main__':
    unittest.main()
