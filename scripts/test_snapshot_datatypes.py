"""Engine datatypes keep their identity through snapshots, updates and events."""
import copy
import unittest

from persistent_runtime import PersistentLuauHost
from test_world_snapshot import fixture
from world_snapshot import _encode, make_world_snapshot, make_world_delta


def typed_fixture():
    world, _ = fixture()
    values = {
        'Size': (14, [.5, 12, .25, -3]),
        'AnchorPoint': (21, [.25, .75]),
        'BackgroundColor3': (19, [.2, .4, .8]),
        'Corner': (13, [.2, 7]),
        'Range': (37, [2, 5]),
        'Slice': (40, [1, 2, 9, 12]),
        'ByteColor': (20, [255, 128, 0]),
        'TransparencySequence': (35, [[0, .2, .1], [1, .8, .2]]),
        'ColorSequence': (38, [[0, 1, 0, 0, 0], [1, 0, 0, 1, 0]]),
    }
    fields = []
    for name, (kind, _) in values.items():
        fields.append(len(world.schema['properties']))
        world.schema['properties'].append({'name': name, 'type': kind})
    world.schema['classes'].append({'name': 'Frame', 'properties': fields})
    key = (1, 50)
    world.instances[key] = {'class': 6, 'parent': (1, 6), 'properties': {
        'Name': 'TypedFrame', 'Archivable': True,
        **{name: copy.deepcopy(value) for name, (_, value) in values.items()}}}
    return world, key


class SnapshotDatatypeTests(unittest.TestCase):
    def test_color_and_vector_have_distinct_schema_types(self):
        self.assertEqual(_encode([.1, .2, .3], 19), {'$color3': [.1, .2, .3]})
        self.assertEqual(_encode([.1, .2, .3], 22), {'$vector3': [.1, .2, .3]})
        self.assertEqual(_encode([255, 128, 0], 20), {'$color3': [1, 128/255, 0]})

    def test_nested_gui_datatypes_use_native_constructor_tags(self):
        self.assertEqual(_encode([.5, 12, .25, -3], 14), {
            '$udim2': [{'$udim': [.5, 12]}, {'$udim': [.25, -3]}]})
        self.assertEqual(_encode([1, 2, 9, 12], 40), {
            '$rect': [{'$vector2': [1, 2]}, {'$vector2': [9, 12]}]})
        self.assertEqual(_encode([2, 5], 37), {'$number_range': [2, 5]})

    def test_export_preserves_wire_values_and_received_provenance(self):
        world, key = typed_fixture()
        original = copy.deepcopy(world.instances)
        snapshot = make_world_snapshot(world, 123)
        row = next(n for n in snapshot['nodes'] if n['id'] == '1:50')
        self.assertEqual(world.instances, original)
        self.assertEqual(row['origins']['BackgroundColor3'], 'received')
        world.instances[key]['properties']['Size'][0] = .9
        self.assertEqual(row['properties']['Size']['$udim2'][0]['$udim'][0], .5)

    def test_event_arguments_use_the_same_type_encoding(self):
        world, key = typed_fixture()
        kinds = [13, 14, 19, 20, 21, 35, 37, 38, 40]
        source = [[.5, 2], [.5, 2, 1, 3], [1, 0, 0], [255, 0, 0], [1, 2],
                  [[0, 1, 0], [1, 0, 0]], [1, 2],
                  [[0, 1, 0, 0, 0], [1, 0, 1, 0, 0]], [0, 0, 2, 3]]
        world.schema['events'] = [{'name': 'Typed', 'arguments': [{'type': t} for t in kinds]}]
        world.actions.append({'sequence': world.revision+1, 'key': key, 'kind': 'event',
                              'event': 0, 'arguments': source})
        delta = make_world_delta(world, world.revision)
        self.assertEqual(delta['actions'][0]['arguments'], [_encode(v, t) for v, t in zip(source, kinds)])

    def test_invalid_datatype_shapes_and_unknown_color_envelopes_rejected(self):
        for value, kind in [([1], 13), ([1, 2, 3], 14), ([True, 0], 21),
                            ([256, 0, 0], 20), ([1.5, 0, 0], 20),
                            ([[0, 1]], 35), ([[0, 1, 0, 0, 1]], 38)]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                _encode(value, kind)
        self.assertEqual(_encode([1, 2, 3]), [1, 2, 3])


class SnapshotDatatypeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.world, self.key = typed_fixture()
        self.host = PersistentLuauHost(make_world_snapshot(self.world, 123))
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result.get('task_errors', []), [])
        return result['values']

    def test_gui_dimensions_colors_and_methods_are_real_datatypes(self):
        self.assertEqual(self.execute('''
local f=workspace.TypedFrame
return typeof(f.Size),f.Size.X.Scale,f.Size.X.Offset,f.Size.Y.Offset,
    typeof(f.AnchorPoint),f.AnchorPoint.Y,typeof(f.BackgroundColor3),
    math.abs(f.BackgroundColor3:Lerp(Color3.new(1,1,1),.5).R-.6)<1e-6,
    typeof(f.Corner),f.Corner.Offset,typeof(f.Range),f.Range.Max,
    typeof(f.Slice),f.Slice.Width,f.Slice.Height,f.ByteColor:ToHex()
'''), ['UDim2', .5, 12, -3, 'Vector2', .75, 'Color3', True,
       'UDim', 7, 'NumberRange', 5, 'Rect', 8, 10, 'ff8000'])

    def test_sequences_keep_keypoint_values_and_envelopes(self):
        self.assertEqual(self.execute('''
local f=workspace.TypedFrame
local n,c=f.TransparencySequence.Keypoints,f.ColorSequence.Keypoints
return typeof(f.TransparencySequence),#n,n[1].Time,n[1].Value,n[2].Envelope,
    typeof(f.ColorSequence),#c,c[2].Time,c[1].Value.R,c[2].Value.B
'''), ['NumberSequence', 2, 0, .2, .2, 'ColorSequence', 2, 1, 1, 1])

    def test_live_delta_and_change_callback_keep_type_identity(self):
        self.execute('''getgenv().seen=nil
workspace.TypedFrame:GetPropertyChangedSignal("Size"):Connect(function()
    seen=workspace.TypedFrame.Size
end)''')
        revision = self.world.revision
        self.world.instances[self.key]['properties']['Size'] = [1, 17, 0, 23]
        self.world.touch(self.key, ['Size'])
        result = self.host.call({'op': 'update', 'delta': make_world_delta(self.world, revision), 'timeout_ms': 100})
        self.assertTrue(result['ok'])
        self.assertEqual(self.execute('return typeof(seen),seen.X.Offset,seen.Y.Offset,client.propertyOrigin(workspace.TypedFrame,"Size")'),
                         ['UDim2', 17, 23, 'received'])

    def test_local_write_clone_and_attribute_preserve_received_value_types(self):
        self.assertEqual(self.execute('''
local f=workspace.TypedFrame
f.Size=f.Size+UDim2.fromOffset(3,4)
f:SetAttribute("SavedSize",f.Size)
local clone=f:Clone()
return typeof(clone.Size),clone.Size.X.Offset,clone.Size.Y.Offset,
    clone:GetAttribute("SavedSize")==clone.Size,
    clone.BackgroundColor3==f.BackgroundColor3
'''), ['UDim2', 15, 1, True, True])

    def test_json_preserves_nuls_and_rejects_unsupported_table_shapes(self):
        result = self.execute(r'''
local http=game:GetService("HttpService")
local encoded=http:JSONEncode({["a\0b"]="x\0y",nested={true,false}})
local decoded=http:JSONDecode(encoded)
local sparseOk,sparseError=pcall(function()
    http:JSONEncode({[1]="one",[3]="three"})
end)
local mixedOk,mixedError=pcall(function()
    http:JSONEncode({[1]="one",key="two"})
end)
return encoded,decoded["a\0b"],#decoded.nested,sparseOk,sparseError,mixedOk,mixedError
''')
        self.assertEqual(result[:3], [
            '{"a\\u0000b":"x\\u0000y","nested":[true,false]}', 'x\0y', 2])
        self.assertFalse(result[3])
        self.assertIn('contiguous from 1', result[4])
        self.assertFalse(result[5])
        self.assertIn('mix array and object keys', result[6])

    def test_json_enforces_the_table_item_budget_at_the_boundary(self):
        self.assertEqual(self.execute('''
local http=game:GetService("HttpService")
local maximum={}
for i=1,999999 do maximum[i]=false end
local accepted,encoded=pcall(function() return http:JSONEncode(maximum) end)
local excess={}
for i=1,1000000 do excess[i]=false end
local rejected,message=pcall(function() http:JSONEncode(excess) end)
return accepted,#encoded,rejected,string.find(message,"1000000 items",1,true)~=nil
'''), [True, 5999995, False, True])


if __name__ == '__main__':
    unittest.main()
