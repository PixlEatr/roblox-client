"""GUI wheel routing with only legacy PlayerMouse listeners."""
import unittest
import test_gui_wheel_events as gui


class GuiLegacyMouseTests(unittest.TestCase):
    execute=gui.GuiWheelEventsTests.execute
    wheel=gui.GuiWheelEventsTests.wheel
    frame=gui.GuiWheelEventsTests.frame

    def setUp(self):
        gui.GuiWheelEventsTests.setUp(self)
        self.execute('''
getgenv().mouse=game:GetService("Players").LocalPlayer:GetMouse()
getgenv().legacy={}
getgenv().legacyForward=mouse.WheelForward:Connect(function(...)
 assert(select("#",...)==0);table.insert(legacy,"forward")
end)
getgenv().legacyBackward=mouse.WheelBackward:Connect(function(...)
 assert(select("#",...)==0);table.insert(legacy,"backward")
end)
''')

    def plain(self):
        self.execute('''child.Parent=scroll.Parent;child.Position=UDim2.fromOffset(200,100)
child.Active=false;connection:Disconnect();fc:Disconnect();bc:Disconnect()''')

    def test_gui_wheel_delivers_legacy_signals_once_per_gesture(self):
        self.wheel(-3,[0,60]);self.wheel(2,[0,-40]);self.wheel(0,[20,0])
        self.assertEqual(self.execute('return table.concat(legacy,","),#events,parentEvents'),['backward,forward',3,3])

    def test_legacy_only_listener_routes_plain_gui_and_disconnect_removes_routing(self):
        self.plain()
        self.assertTrue(self.frame()['wheelTarget'])
        self.wheel()
        self.assertEqual(self.execute('return table.concat(legacy,",")'),['backward'])
        self.execute('legacyBackward:Disconnect();legacyForward:Disconnect()')
        self.assertNotIn('wheelTarget',self.frame())

    def test_destroyed_mouse_does_not_keep_gui_wheel_routing_alive(self):
        self.plain();self.execute('mouse:Destroy()')
        self.assertNotIn('wheelTarget',self.frame())
        self.wheel()
        self.assertEqual(self.execute('return #legacy'),[0])
        self.execute('''mouse=game:GetService("Players").LocalPlayer:GetMouse()
mouse.WheelBackward:Connect(function()table.insert(legacy,"new")end)''')
        self.assertTrue(self.frame()['wheelTarget'])
        self.wheel()
        self.assertEqual(self.execute('return table.concat(legacy,",")'),['new'])

    def test_processed_and_unprocessed_gui_wheel_both_reach_legacy_observer(self):
        self.wheel()
        self.plain();self.wheel()
        self.execute('child.Active=true');self.wheel()
        self.assertEqual(self.execute('return #legacy'),[3])

    def test_invalid_gui_request_does_not_fire_legacy_signal(self):
        result=self.host.call({'op':'gui_action','action':{'id':self.id,'kind':'wheel',
            'delta':[0,20],'position':[1,1],'steps':-1}})
        self.assertFalse(result['ok'])
        self.assertEqual(self.execute('return #legacy'),[0])

    def test_wheel_callback_reads_current_mouse_coordinates_and_camera_ray(self):
        self.execute('''
getgenv().point={}
workspace.CurrentCamera.CFrame=CFrame.identity;workspace.CurrentCamera.FieldOfView=90
mouse.WheelBackward:Connect(function()
 local r=mouse.UnitRay
 local pixel=workspace.CurrentCamera:WorldToScreenPoint(r.Origin+r.Direction*50)
 assert(math.abs(pixel.X-mouse.X)<.001 and math.abs(pixel.Y-mouse.Y)<.001)
 getgenv().point={mouse.X,mouse.Y,mouse.ViewSizeX,mouse.ViewSizeY}
end)
''')
        self.wheel(position=[260,160])
        self.assertEqual(self.execute('return table.unpack(point)'),[250,120,1000,800])

    def test_wheel_callback_picks_world_geometry_and_obeys_filter(self):
        self.execute('''
workspace.CurrentCamera.CFrame=CFrame.identity
local ray=workspace.CurrentCamera:ScreenPointToRay(250,120)
getgenv().pickPart=Instance.new("Part",workspace)
pickPart.Size=Vector3.one*4
pickPart.CFrame=CFrame.lookAt(ray.Direction*10,Vector3.zero)
getgenv().picks={}
mouse.WheelBackward:Connect(function()
 local hit=mouse.Hit
 table.insert(picks,mouse.Target==pickPart)
 if mouse.Target then assert((hit.Position-mouse.UnitRay.Direction*8).Magnitude<.0001)end
end)
''')
        self.wheel(position=[260,160])
        self.execute('mouse.TargetFilter=pickPart')
        self.wheel(position=[260,160])
        self.assertEqual(self.execute('return picks[1],picks[2]'),[True,False])

if __name__=='__main__':unittest.main()
