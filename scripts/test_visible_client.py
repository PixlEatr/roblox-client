"""Real VM -> local HTTP -> renderer contracts and session-control boundaries."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from persistent_runtime import PersistentLuauHost
from render_movement import SurfaceMovement
from script_console import ScriptConsole
from test_world_snapshot import fixture
from web_console import WebConsole
from world_snapshot import make_world_snapshot


class VisibleClientTests(unittest.TestCase):
    def setUp(self):
        world,_=fixture()
        self.host=PersistentLuauHost(make_world_snapshot(world,123))
        self.addCleanup(self.host.close)
        self.assertTrue(self.host.call({'op':'execute','source':'''local p=game:GetService("Players").LocalPlayer
local pg=game:GetService("CoreGui")
local screen=Instance.new("ScreenGui",pg);screen.Name="RenderTest"
local frame=Instance.new("Frame",screen);frame.Size=UDim2.fromOffset(300,120);frame.Position=UDim2.fromOffset(20,30)
local button=Instance.new("TextButton",frame);button.Name="TestButton";button.Text="Click me";button.Size=UDim2.fromOffset(180,40);button.BackgroundColor3=Color3.fromRGB(32,38,52);button.TextColor3=Color3.new(1,1,1)
renderClicks=0;button.MouseButton1Click:Connect(function()renderClicks+=1 end)
local textbox=Instance.new("TextBox",frame);textbox.Name="TestText";textbox.Position=UDim2.fromOffset(0,50);textbox.Size=UDim2.fromOffset(180,40)
local part=Instance.new("Part",workspace);part.Name="VisiblePart";part.Size=Vector3.new(4,2,6);part.CFrame=CFrame.new(1,3,5);part.Color=Color3.fromRGB(255,0,0)
renderTestButton=button;renderTestScreen=screen;renderTestPart=part
return true''','timeout_ms':200})['ok'])
        self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[1000,700],'device_insets':[0,0,0,0],'core_insets':[0,0,0,0]}}})

    def test_frame_reads_live_vm_local_parts_gui_and_is_read_only(self):
        frame=self.host.call({'op':'render'})['frame']
        part=next(p for p in frame['parts'] if p['name']=='VisiblePart')
        self.assertEqual(part['position']['$vector3'],[1,3,5])
        self.assertEqual(part['size']['$vector3'],[4,2,6])
        button=next(g for g in frame['gui'] if g['name']=='TestButton')
        self.assertEqual(button['box'][:4],[20,30,180,40])
        self.assertEqual(button['text'],'Click me')
        self.assertEqual(button['textColor']['$color3'],[1,1,1])
        self.assertTrue(button['interactive'])
        self.assertLess(len(json.dumps(frame)),800000)
        self.assertNotIn('script_sources',frame)
        before=copy.deepcopy(frame)
        self.assertEqual(self.host.call({'op':'render'})['frame'],before)
        self.host.call({'op':'execute','source':'renderTestPart.Position=Vector3.new(10,20,30);return true'})
        new=next(p for p in self.host.call({'op':'render'})['frame']['parts'] if p['name']=='VisiblePart')
        self.assertEqual(new['position']['$vector3'],[10,20,30])

    def test_huge_unicode_text_is_bounded_and_does_not_destroy_vm(self):
        result=self.host.call({'op':'execute','source':'renderTestButton.Text=string.rep("🌍",10000);return true'})
        self.assertTrue(result['ok'])
        frame=self.host.call({'op':'render'})['frame']
        button=next(g for g in frame['gui'] if g['name']=='TestButton')
        self.assertLessEqual(len(button['text'].encode()),4096)
        self.assertEqual(self.host.call({'op':'execute','source':'return 42'})['values'],[42])

    def test_gui_click_reaches_real_luau_callback_and_hidden_is_rejected(self):
        frame=self.host.call({'op':'render'})['frame']
        button=next(g for g in frame['gui'] if g['name']=='TestButton')
        result=self.host.call({'op':'gui_action','action':{'kind':'click','id':button['id']}})
        self.assertTrue(result['ok'],result)
        result=self.host.call({'op':'execute','source':'return renderClicks'})
        self.assertEqual(result['values'],[1])
        self.host.call({'op':'execute','source':'renderTestScreen.Enabled=false;return true'})
        self.assertEqual(self.host.call({'op':'render'})['frame']['gui'],[])
        self.assertFalse(self.host.call({'op':'gui_action','action':{'kind':'click','id':button['id']}})['ok'])

    def test_activated_input_and_textbox_callbacks(self):
        self.host.call({'op':'execute','source':'renderTestButton.Activated:Connect(function(input,count) renderActivation={input.UserInputType.Name,count} end); renderTestScreen:FindFirstChild("Frame"):FindFirstChild("TestText").FocusLost:Connect(function(enter) renderTextEntered=enter end);return true'})
        frame=self.host.call({'op':'render'})['frame']
        button=next(g for g in frame['gui'] if g['name']=='TestButton')
        textbox=next(g for g in frame['gui'] if g['name']=='TestText')
        self.assertTrue(self.host.call({'op':'gui_action','action':{'kind':'click','id':button['id']}})['ok'])
        self.assertTrue(self.host.call({'op':'gui_action','action':{'kind':'text','id':textbox['id'],'text':'hello'}})['ok'])
        result=self.host.call({'op':'execute','source':'return renderActivation[1],renderActivation[2],renderTextEntered'})
        self.assertEqual(result['values'],['MouseButton1',1,True])

    def test_http_two_views_read_same_vm_and_leave_without_rejoining(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            left=[]
            console=ScriptConsole(Path(directory)/'script.sock',lambda source,timeout_ms=1000:self.host.call({'op':'execute','source':source,'timeout_ms':timeout_ms}),
                render=lambda:self.host.call({'op':'render'}),leave=lambda:left.append(True))
            stop=threading.Event()
            def poll():
                while not stop.is_set():console.poll();stop.wait(.002)
            worker=threading.Thread(target=poll,daemon=True);worker.start()
            views=[WebConsole(socket_path=console.path) for _ in range(2)]
            try:
                for view in views:view.start(open_browser=False)
                def request(view,path,body=None,key=None):
                    client=http.client.HTTPConnection('127.0.0.1',view.port,timeout=5)
                    headers={'Origin':view.url.rstrip('/'),'X-Console-Key':view.key if key is None else key,'Content-Type':'application/json'}
                    client.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
                    response=client.getresponse();data=response.read();client.close();return response.status,data
                for view in views:
                    status,page=request(view,'/play');self.assertEqual(status,200);self.assertIn(view.key.encode(),page)
                    self.assertEqual(request(view,'/world-viewer.js')[0],200)
                    self.assertEqual(request(view,'/api/render',{},'wrong')[0],403)
                frames=[json.loads(request(view,'/api/render',{})[1])['frame'] for view in views]
                self.assertEqual(frames[0],frames[1])
                status,data=request(views[1],'/api/leave',{})
                self.assertEqual(status,200);self.assertTrue(json.loads(data)['ok']);self.assertEqual(left,[True])
                views[1].close();self.assertTrue(views[0].connected())
            finally:
                for view in views:
                    if view.thread:view.close()
                stop.set();worker.join(2);console.close()


class MovementExpiryTests(unittest.TestCase):
    def test_blurred_lost_display_expires_and_stale_competing_input_is_rejected(self):
        now=[0.0];execute=Mock(return_value={'ok':True})
        movement=SurfaceMovement(execute,clock=lambda:now[0])
        move={'client':'a'*32,'sequence':1,'direction':[1,0,0]}
        movement.handle(move)
        with self.assertRaises(ValueError):movement.handle(move)
        with self.assertRaises(ValueError):movement.handle(dict(move,client='b'*32))
        now[0]=1;movement.poll();self.assertFalse(movement.moving)
        self.assertIn('Vector3.new(0,0,0)',execute.call_args.args[0])
        movement.handle(dict(move,client='b'*32));self.assertTrue(movement.moving)
        movement.close();self.assertFalse(movement.moving)

    def test_invalid_numbers_never_become_luau(self):
        execute=Mock();movement=SurfaceMovement(execute)
        for direction in ([float('nan'),0,0],[True,0,0],[0,1,0],[3,0,0],['1);error()',0,0]):
            with self.assertRaises(ValueError):movement.handle({'client':'a'*32,'sequence':1,'direction':direction})
        execute.assert_not_called()


class RenderReconnectTests(unittest.TestCase):
    def test_new_socket_invalidates_cache_even_inside_frame_window(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock')
        self.addCleanup(web.close)
        with patch.object(web,'runtime_identity',side_effect=['first','first','second']),patch('web_console.send_request',side_effect=[{'ok':True,'frame':{'value':1}},{'ok':True,'frame':{'value':2}}]) as send:
            self.assertEqual(web.render_frame()['frame']['value'],1)
            self.assertEqual(web.render_frame()['frame']['value'],1)
            self.assertEqual(web.render_frame()['frame']['value'],2)
            self.assertEqual(send.call_count,2)


if __name__=='__main__':unittest.main()
