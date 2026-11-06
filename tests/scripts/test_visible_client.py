"""Real VM -> local HTTP -> renderer contracts and session-control boundaries."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from persistent_runtime import PersistentLuauHost
from render_movement import SurfaceMovement
from script_console import ScriptConsole
from test_world_snapshot import fixture
from web_console import WebConsole
from launcher import Launcher
from test_launcher import FakeAPI, FakeController
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

    def test_textbox_focus_state_events_and_stale_browser_actions(self):
        def execute(source):
            response=self.host.call({'op':'execute','source':source})
            self.assertTrue(response['ok'],response.get('error'))
            self.assertFalse(response.get('task_errors'),response.get('task_errors'))
            return response['values']
        execute('''
box=renderTestScreen.Frame.TestText
input=game:GetService("UserInputService")
focusTrace={}
box.Focused:Connect(function()table.insert(focusTrace,"focused")end)
box.FocusLost:Connect(function(submitted,key)
 table.insert(focusTrace,submitted and (key and key.KeyCode.Name or "script-submit") or "blur")
end)
input.TextBoxFocused:Connect(function(target)assert(target==box);table.insert(focusTrace,"input-focused")end)
input.TextBoxFocusReleased:Connect(function(target)assert(target==box);table.insert(focusTrace,"input-released")end)
assert(input:GetFocusedTextBox()==nil and not box:IsFocused())
box:CaptureFocus();box:CaptureFocus()
assert(box:IsFocused() and input:GetFocusedTextBox()==box and box.Text=="")
''')
        first=self.host.call({'op':'render'})['frame']['textFocus']
        id=first['id']
        self.assertTrue(id)

        def action(kind,**fields):
            result=self.host.call({'op':'gui_action','action':{'id':id,'kind':kind,**fields}})
            self.assertTrue(result['ok'],result.get('error'))
            return result['textFocus']
        self.assertEqual(action('text_edit',revision=first['revision'],text='typed'),first)
        self.assertEqual(execute('return box.Text,table.concat(focusTrace,",")'),['typed','focused,input-focused'])
        self.assertEqual(action('text_blur',revision=first['revision'],submitted=False)['id'],'')
        second=action('text_focus')
        self.assertGreater(second['revision'],first['revision'])
        self.assertEqual(action('text_blur',revision=first['revision'],submitted=True),second)
        self.assertEqual(action('text_edit',revision=first['revision'],text='stale'),second)
        self.assertEqual(execute('return box:IsFocused(),box.Text'),[True,''])
        action('text_blur',revision=second['revision'],submitted=True)
        self.assertEqual(execute('return table.concat(focusTrace,",")'),
                         ['focused,input-focused,blur,input-released,focused,input-focused,Return,input-released'])
        execute('''
box.ClearTextOnFocus=false;box.Text="keep"
box:CaptureFocus();box:ReleaseFocus(true)
assert(not box:IsFocused() and input:GetFocusedTextBox()==nil and box.Text=="keep")
''')
        self.assertEqual(execute('return focusTrace[#focusTrace-1]'),['script-submit'])
        self.assertEqual(execute('''
box:CaptureFocus()
renderTestScreen.Enabled=false
return box:IsFocused(),input:GetFocusedTextBox()==nil
'''),[False,True])

    def test_textbox_selection_updates_and_stale_editor_revisions(self):
        def execute(source):
            result=self.host.call({'op':'execute','source':source})
            self.assertTrue(result['ok'],result.get('error'))
            return result['values']
        execute('''
box=renderTestScreen.Frame.TestText
box.ClearTextOnFocus=false
box.Text="aé🙂z"
box:CaptureFocus()
changes=0
box:GetPropertyChangedSignal("CursorPosition"):Connect(function()changes+=1 end)
''')
        frame=self.host.call({'op':'render'})['frame']
        focus=frame['textFocus']
        self.assertEqual(frame['textEditor'],{'revision':0,'cursor':9,'anchor':-1})
        action={'id':focus['id'],'kind':'text_select','revision':focus['revision'],
                'editorRevision':0,'cursor':2,'anchor':8}
        self.assertTrue(self.host.call({'op':'gui_action','action':action})['ok'])
        self.assertEqual(execute('return box.CursorPosition,box.SelectionStart,changes'),[2,8,1])
        execute('box.Text="ok";box.CursorPosition=1')
        self.assertTrue(self.host.call({'op':'gui_action','action':{**action,'kind':'text_edit','text':'stale'}})['ok'])
        self.assertEqual(execute('return box.Text,box.CursorPosition,box.SelectionStart'),['ok',1,3])
        editor=self.host.call({'op':'render'})['frame']['textEditor']
        self.assertTrue(self.host.call({'op':'gui_action','action':{**action,'kind':'text_edit','text':'é!',
            'editorRevision':editor['revision'],'cursor':3,'anchor':-1}})['ok'])
        self.assertEqual(execute('return box.Text,box.CursorPosition,box.SelectionStart'),['é!',3,-1])

    def test_frame_copy_control_receives_pointer_phases_and_readonly_text(self):
        result=self.host.call({'op':'execute','source':'''local frame=renderTestScreen.Frame
local text=frame.TestText;text.Text="observed result text";text.TextEditable=false
pointerTrace={}
frame.InputBegan:Connect(function(input)
    pointerInput=input;copiedResult=text.Text
    table.insert(pointerTrace,input.UserInputState.Name)
end)
frame.InputEnded:Connect(function(input)
    assert(input==pointerInput)
    table.insert(pointerTrace,input.UserInputState.Name)
end)
return true'''})
        self.assertTrue(result['ok'],result)
        frame=self.host.call({'op':'render'})['frame']
        control=next(g for g in frame['gui'] if g['name']=='Frame')
        text=next(g for g in frame['gui'] if g['name']=='TestText')
        self.assertTrue(control['interactive'])
        self.assertEqual(text['pointerTarget'],control['id'])
        self.assertFalse(text['textEditable'])
        for phase,state in [('pointer_down','Begin'),('pointer_up','End')]:
            result=self.host.call({'op':'gui_action','action':{'kind':phase,'id':control['id']}})
            self.assertTrue(result['ok'],result)
            result=self.host.call({'op':'execute','source':'return pointerTrace[#pointerTrace],copiedResult'})
            self.assertEqual(result['values'],[state,'observed result text'])
        self.assertFalse(self.host.call({'op':'gui_action','action':{'kind':'pointer_up','id':control['id']}})['ok'])
        before=self.host.call({'op':'execute','source':'return #getinstances()'})['values'][0]
        for _ in range(8):
            for phase in ('pointer_down','pointer_up'):
                self.assertTrue(self.host.call({'op':'gui_action','action':{'kind':phase,'id':control['id']}})['ok'])
        self.assertEqual(self.host.call({'op':'execute','source':'return #getinstances()'})['values'][0],before)
        self.host.call({'op':'gui_action','action':{'kind':'pointer_down','id':control['id']}})
        self.host.call({'op':'execute','source':'renderTestScreen.Frame:Destroy();return true'})
        result=self.host.call({'op':'gui_action','action':{'kind':'pointer_up','id':control['id']}})
        self.assertTrue(result['ok'],result)
        result=self.host.call({'op':'execute','source':'return pointerInput.UserInputState.Name'})
        self.assertEqual(result['values'],['Cancel'])

    def test_http_two_views_read_same_vm_and_leave_without_rejoining(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            left=[]
            console=ScriptConsole(Path(directory)/'script.sock',lambda source,timeout_ms=1000:self.host.call({'op':'execute','source':source,'timeout_ms':timeout_ms}),
                render=lambda:self.host.call({'op':'render'}),leave=lambda:left.append(True))
            stop=threading.Event()
            def poll():
                while not stop.is_set():console.poll();stop.wait(.002)
            worker=threading.Thread(target=poll,daemon=True);worker.start()
            app=Launcher(0,api=FakeAPI(),controller=FakeController(),socket_path=console.path)
            views=[app.surface,app.surface]
            self.assertIs(app.surface.server,app.server)
            try:
                threading.Thread(target=app.server.serve_forever,daemon=True).start()
                def request(view,path,body=None,key=None):
                    client=http.client.HTTPConnection('127.0.0.1',view.port,timeout=5)
                    headers={'Origin':view.url.rstrip('/'),'X-Console-Key':view.key if key is None else key,'Content-Type':'application/json'}
                    if path.startswith('/api/'):path=path.replace('/api/','/api/runtime/',1)
                    client.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
                    response=client.getresponse();data=response.read();client.close();return response.status,data
                for view in views:
                    status,page=request(view,'/');self.assertEqual(status,200);self.assertIn(view.key.encode(),page)
                    self.assertEqual(request(view,'/world-viewer.js')[0],200)
                    self.assertEqual(request(view,'/api/render',{},'wrong')[0],403)
                def frame(view):
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        result=json.loads(request(view,'/api/render',{})[1])
                        if result.get('frame') is not None:return result['frame']
                        self.assertTrue(result.get('pending'),result)
                        time.sleep(.01)
                    self.fail('The render sample did not finish')
                frames=[frame(view) for view in views]
                self.assertEqual(frames[0],frames[1])
                status,data=request(views[1],'/api/leave',{})
                self.assertEqual(status,200);self.assertTrue(json.loads(data)['ok']);self.assertEqual(left,[True])
                views[1].close();self.assertTrue(views[0].connected())
            finally:
                app.close()
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
    def test_full_socket_backlog_is_busy_rather_than_disconnected(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        for error,connected in [(TimeoutError(),True),(BlockingIOError(),True),
                                (ConnectionRefusedError(),False),(FileNotFoundError(),False),(ValueError(),False)]:
            with patch('local_transport.probe',side_effect=error):self.assertEqual(web.connected(),connected)

    def wait_frame(self,web):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            result=web.render_frame()
            if result.get('frame') is not None:return result
            time.sleep(.005)
        self.fail('No frame arrived')

    def wait_idle(self,web):
        deadline=time.monotonic()+2
        while web.frame_pending is not None and time.monotonic()<deadline:time.sleep(.005)
        self.assertIsNone(web.frame_pending)

    def test_new_socket_invalidates_cache_even_inside_frame_window(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        identity=['first']
        with patch.object(web,'runtime_identity',side_effect=lambda:identity[0]),patch('web_console.send_request',side_effect=[{'ok':True,'frame':{'value':1}},{'ok':True,'frame':{'value':2}}]) as send:
            self.assertEqual(self.wait_frame(web)['frame']['value'],1)
            self.assertEqual(web.render_frame()['frame']['value'],1)
            identity[0]='second'
            self.assertNotIn('frame',web.render_frame())
            self.assertEqual(self.wait_frame(web)['frame']['value'],2)
            self.assertEqual(send.call_count,2)

    def test_slow_sample_is_shared_without_blocking_http_or_stacking_reads(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def read(*args,**kwargs):
            self.assertEqual(kwargs['timeout'],30);entered.set();release.wait(2)
            return {'ok':True,'frame':{'parts':[],'gui':[]}}
        with patch.object(web,'runtime_identity',return_value='first'),patch('web_console.send_request',side_effect=read) as send:
            self.assertTrue(web.render_frame()['pending']);self.assertTrue(entered.wait(1))
            for _ in range(15):self.assertTrue(web.render_frame()['pending'])
            self.assertEqual(send.call_count,1)
            release.set()
            result=self.wait_frame(web)
            self.assertIsInstance(result['frame'],dict)
            self.assertGreaterEqual(result['sample'],1)

    def test_failed_sample_keeps_last_frame_and_backs_off(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        with patch.object(web,'runtime_identity',return_value='first'),patch('web_console.send_request',side_effect=[{'ok':True,'frame':{'parts':[],'gui':[]}},TimeoutError('timed out')]) as send:
            good=self.wait_frame(web)
            unchanged=web.render_frame(after=good['sample'])
            self.assertEqual(unchanged['sample'],good['sample'])
            self.assertNotIn('frame',unchanged)
            web.frame_time-=1
            self.assertEqual(web.render_frame()['frame'],good['frame'])
            self.wait_idle(web)
            failed=web.render_frame()
            self.assertEqual(failed['frame'],good['frame'])
            self.assertEqual(failed['sample'],good['sample'])
            self.assertIn('busy',failed['message'])
            self.assertNotIn('frame',web.render_frame(after=good['sample']))
            for _ in range(5):web.render_frame()
            self.assertEqual(send.call_count,2)

    def test_invalid_response_releases_pending_sample_for_recovery(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        with patch.object(web,'runtime_identity',return_value='first'),patch('web_console.send_request',side_effect=[None,{'ok':True,'frame':{'parts':[],'gui':[]}}]):
            web.render_frame();self.wait_idle(web)
            self.assertIn('invalid game frame',web.render_frame()['message'])
            web.frame_retry=0
            self.assertIn('frame',self.wait_frame(web))

    def test_late_result_cannot_leak_into_a_replaced_runtime(self):
        from unittest.mock import patch
        web=WebConsole(socket_path='/tmp/unused-visible-client.sock');self.addCleanup(web.close)
        identity=['first'];entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        def read(*args,**kwargs):
            entered.set();release.wait(2)
            return {'ok':True,'frame':{'parts':[],'gui':[],'old':True}}
        with patch.object(web,'runtime_identity',side_effect=lambda:identity[0]),patch('web_console.send_request',side_effect=read):
            web.render_frame();self.assertTrue(entered.wait(1))
            identity[0]='second';self.assertNotIn('frame',web.render_frame())
            release.set();self.wait_idle(web)
            self.assertIsNone(web.frame_cache)
            self.assertIsNone(web.frame_error)


if __name__=='__main__':unittest.main()
