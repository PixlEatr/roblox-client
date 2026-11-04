"""Actual Luau -> remote wire -> actual Luau typed value round trips."""
import unittest

import test_remote_server_rpc as rpc_tests
from remote_client_rpc import variant
from world_replication import World
from world_snapshot import _encode
from wire_reader import Reader


class RemoteDatatypeTests(unittest.TestCase):
    setUp = rpc_tests.InvokeServerTests.setUp
    host = rpc_tests.InvokeServerTests.host
    execute = rpc_tests.InvokeServerTests.execute
    transmit = rpc_tests.InvokeServerTests.transmit
    reply = rpc_tests.InvokeServerTests.reply
    complete = rpc_tests.InvokeServerTests.complete

    def test_real_vm_nested_engine_values_keep_types_and_components(self):
        host = self.host()
        job = self.execute(host, '''
local t=workspace.Remote:InvokeServer({
 v3=Vector3.new(1.25,-2.5,3),v2=Vector2.new(4,-5),color=Color3.new(.25,.5,1),
 dim=UDim.new(.5,-42),dims=UDim2.new(.25,123,.75,-456),
 rect=Rect.new(-2,-3,7,8),range=NumberRange.new(-2,9),
 brick=BrickColor.new(21),date=DateTime.fromUnixTimestampMillis(1234567890123),
 bytes=buffer.fromstring(string.char(0,255,1,128)),empty=buffer.create(0),
})
assert(typeof(t.v3)=="Vector3" and t.v3==Vector3.new(1.25,-2.5,3))
assert(typeof(t.v2)=="Vector2" and t.v2.X==4 and t.v2.Y==-5)
assert(typeof(t.color)=="Color3" and t.color.R==.25 and t.color.G==.5 and t.color.B==1)
assert(typeof(t.dim)=="UDim" and t.dim.Scale==.5 and t.dim.Offset==-42)
assert(typeof(t.dims)=="UDim2" and t.dims.X.Offset==123 and t.dims.Y.Scale==.75 and t.dims.Y.Offset==-456)
assert(typeof(t.rect)=="Rect" and t.rect.Min.X==-2 and t.rect.Max.Y==8)
assert(typeof(t.range)=="NumberRange" and t.range.Min==-2 and t.range.Max==9)
assert(typeof(t.brick)=="BrickColor" and t.brick.Number==21)
assert(typeof(t.date)=="DateTime" and t.date.UnixTimestampMillis==1234567890123)
assert(typeof(t.bytes)=="buffer" and buffer.tostring(t.bytes)==string.char(0,255,1,128))
assert(buffer.len(t.empty)==0)
return true
''')
        self.assertEqual(job['state'], 'running', job)
        call = job['remote_server_calls'][0]
        self.rpc.send(self.world, call, self.transmit)
        # Reply crosses the real wire reader and the snapshot type encoder.
        self.reply(call['arguments']); results = []; self.rpc.observe(self.world, results)
        done = self.complete(host, job, results)
        self.assertEqual(done['values'], [True], done)

    def test_known_wire_bytes_and_independent_decode(self):
        cases = [
            ({'$vector3':[1,2,3]}, '160000803f0000004000004040'),
            ({'$udim':[.5,-2]}, '0d0000003ffeffffff'),
            ({'$brickcolor':21}, '121500'),
            ({'$datetime':-1}, '30ffffffffffffffff'),
            ({'$buffer_hex':'00ff'}, '38000200ff'),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                wire=variant(self.world,value)
                self.assertEqual(wire.hex(),expected)
                reader=Reader(bytes.fromhex(expected))
                decoded=self.world.variant(reader,set(),0,preserve_types=True)
                reader.finish()
                self.assertEqual(_encode(decoded),value)

    def test_cframe_rotation_selectors_and_quaternions_round_trip_as_userdata(self):
        host=self.host()
        job=self.execute(host, '''
local frames={CFrame.new(1,2,3),CFrame.new(-4,5,6)*CFrame.Angles(.3,-.8,1.2),
 CFrame.Angles(math.pi,0,0),CFrame.Angles(0,math.pi,0),CFrame.Angles(0,0,math.pi)}
local received=workspace.Remote:InvokeServer(frames)
for i,frame in frames do
 assert(typeof(received[i])=="CFrame")
 assert((received[i].Position-frame.Position).Magnitude<1e-5)
 assert((received[i].LookVector-frame.LookVector).Magnitude<.0002)
 assert((received[i].UpVector-frame.UpVector).Magnitude<.0002)
end
return true
''')
        self.assertEqual(job['state'],'running',job)
        call=job['remote_server_calls'][0]
        self.rpc.send(self.world,call,self.transmit)
        self.reply(call['arguments']);results=[];self.rpc.observe(self.world,results)
        done=self.complete(host,job,results)
        self.assertEqual(done['values'],[True],done)

    def test_cframe_known_axis_bytes_and_invalid_matrix(self):
        identity={'$cframe':{'position':[1,2,3], 'rotation':[1,0,0,0,1,0,0,0,1]}}
        self.assertEqual(variant(self.world,identity).hex(),'1b0000803f000000400000404002')
        for matrix in ([1]*9, [-1,0,0,0,1,0,0,0,1]):
            with self.assertRaises(ValueError):
                variant(self.world,{'$cframe':{'position':[0,0,0],'rotation':matrix}})

    def test_malformed_values_rejected_without_silent_coercion(self):
        for value in ({'$vector3':[1,2]}, {'$vector3':[True,2,3]},
                      {'$color3':[1,float('inf'),0]}, {'$vector2':[1e100,0]},
                      {'$udim':[0,2**31]}, {'$udim':[0,.5]},
                      {'$udim2':[{'$vector2':[1,2]},{'$udim':[0,1]}]},
                      {'$brickcolor':-1}, {'$datetime':253402300800000},
                      {'$buffer_hex':'0'}, {'$buffer_hex':'00 ff'},
                      {'$buffer_hex':'ff'*65537}):
            with self.subTest(value=str(value)[:100]), self.assertRaises(ValueError):
                variant(self.world,value)

    def test_oversize_buffers_fail_before_queueing_and_vm_recovers(self):
        host=self.host()
        done=self.execute(host, '''
assert(not pcall(function()workspace.Remote:InvokeServer(buffer.create(65537))end))
assert(not pcall(function()workspace.Remote:InvokeServer({buffer.create(40000),buffer.create(40000)})end))
return true
''')
        self.assertEqual(done['values'],[True],done)
        self.assertEqual(done['remote_server_calls'],[])
        self.assertEqual(done['active_remote_server_requests'],[])


if __name__ == '__main__':
    unittest.main()
