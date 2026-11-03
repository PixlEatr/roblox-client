"""Keyboard lease safety plus real HTTP/socket/Luau input delivery."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from browser_input import BrowserKeyboard,KEY_CODES,validate_keyboard_request
from persistent_runtime import PersistentClientScriptRuntime
from script_console import ScriptConsole
from test_world_snapshot import fixture
from web_console import WebConsole

CLIENT='keyboard_fixture_client'


class FakeRuntime:
    def __init__(self,keyboard=False):self.input_capabilities={'KeyboardEnabled':keyboard};self.calls=[];self.capabilities=[]
    def update_input_capabilities(self,patch):
        self.capabilities.append(dict(patch));self.input_capabilities.update(patch);return {'ok':True,'values':[]}
    def deliver_input(self,events):self.calls.append(copy.deepcopy(events));return {'ok':True,'values':[]}


class BrowserKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.now=10.;self.runtime=FakeRuntime();self.keyboard=BrowserKeyboard(self.runtime,clock=lambda:self.now)
        self.sequence=0;self.lease=None;self.addCleanup(self.keyboard.close)

    def send(self,action,**extra):
        self.sequence+=1
        request={'action':action,'client_id':CLIENT,'sequence':self.sequence,**extra}
        if action!='attach':request['lease']=self.lease
        result=self.keyboard.handle(request)
        if action=='attach':self.lease=result['lease']
        return result

    def events(self,*events):return self.send('events',events=list(events))
    def key(self,phase='begin',key='W'):return {'phase':phase,'key_code':key}

    def test_mapping_uses_native_key_names_and_preserves_physical_modifiers(self):
        data=json.loads((Path(__file__).resolve().parents[1]/'runtime/tests/references/input-studio-reference.json').read_text())
        names={i['name'] for i in data['enums']['KeyCode']}
        self.assertLessEqual(set(KEY_CODES.values()),names)
        self.assertEqual([KEY_CODES[x] for x in ['KeyW','ArrowUp','ShiftLeft','ShiftRight','Enter','NumpadEnter']],
                         ['W','Up','LeftShift','RightShift','Return','KeypadEnter'])
        self.assertNotIn('Escape',KEY_CODES)

    def test_attach_real_press_release_and_device_availability_until_detach(self):
        self.send('attach');self.assertTrue(self.runtime.input_capabilities['KeyboardEnabled'])
        self.events(self.key());self.assertEqual(self.keyboard.held,{'W'})
        self.events(self.key('end'));self.assertEqual(self.keyboard.held,set())
        self.send('release');self.assertFalse(self.keyboard.focused)
        self.assertTrue(self.runtime.input_capabilities['KeyboardEnabled'])
        self.send('detach');self.assertFalse(self.runtime.input_capabilities['KeyboardEnabled'])
        self.assertEqual(self.runtime.calls[0],[{'phase':'begin','key_code':'W','input_type':'Keyboard','processed':False}])

    def test_replay_is_idempotent_but_reused_and_old_sequences_are_rejected(self):
        self.send('attach');self.events(self.key());request=copy.deepcopy(self.keyboard.last_request)
        self.assertTrue(self.keyboard.handle(request)['ok']);self.assertEqual(len(self.runtime.calls),1)
        changed=copy.deepcopy(request);changed['events'][0]['key_code']='A'
        with self.assertRaises(ValueError):self.keyboard.handle(changed)
        self.events(self.key('end'))
        with self.assertRaises(ValueError):self.keyboard.handle(request)
        self.assertEqual(self.keyboard.held,set())

    def test_invalid_event_batches_do_not_deliver_partial_key_state(self):
        self.send('attach')
        for events in [[self.key(),self.key()], [self.key(),self.key('end','A')],
                       [self.key('end')], [self.key(),{'phase':'begin','key_code':'ButtonA'}]]:
            with self.subTest(events=events),self.assertRaises(ValueError):self.events(*events)
            self.assertEqual(self.keyboard.held,set());self.assertEqual(self.runtime.calls,[])

    def test_expiry_cancels_pressed_keys_and_restores_previous_backend(self):
        self.send('attach');self.events(self.key(),self.key(key='A'))
        self.now+=2.01;self.keyboard.poll()
        self.assertIsNone(self.keyboard.client_id)
        self.assertEqual({e['key_code'] for e in self.runtime.calls[-1]},{'W','A'})
        self.assertTrue(all(e['phase']=='cancel' for e in self.runtime.calls[-1]))
        self.assertFalse(self.runtime.input_capabilities['KeyboardEnabled'])
        with self.assertRaises(ValueError):self.events(self.key())

    def test_existing_keyboard_profile_survives_close(self):
        self.runtime.input_capabilities['KeyboardEnabled']=True
        self.send('attach');self.events(self.key());self.keyboard.close();self.keyboard.close()
        self.assertTrue(self.runtime.input_capabilities['KeyboardEnabled'])
        self.assertEqual(self.runtime.calls[-1][0]['phase'],'cancel')

    def test_heartbeat_refreshes_expiry_and_unfocused_page_can_be_replaced(self):
        self.send('attach');old=self.lease;self.now+=1.9;self.send('heartbeat');self.now+=1.9;self.keyboard.poll()
        self.assertIsNotNone(self.keyboard.client_id)
        with self.assertRaises(ValueError):self.keyboard.handle({'action':'attach','client_id':'second_browser_client','sequence':1})
        self.send('release')
        other=self.keyboard.handle({'action':'attach','client_id':'second_browser_client','sequence':2})
        self.assertNotEqual(other['lease'],old)
        with self.assertRaises(ValueError):self.send('heartbeat')
        self.assertTrue(self.keyboard.focused)

    def test_release_cancels_keys_and_disallows_more_input_until_refocus(self):
        self.send('attach');self.events(self.key());self.send('release')
        self.assertEqual(self.runtime.calls[-1][0]['phase'],'cancel')
        with self.assertRaises(ValueError):self.events(self.key())
        self.send('attach');self.events(self.key());self.assertEqual(self.keyboard.held,{'W'})

    def test_request_shape_types_and_batch_limits(self):
        base={'action':'attach','client_id':CLIENT,'sequence':1}
        bad=[[],dict(base,sequence=True),dict(base,sequence=1.5),dict(base,sequence=0),
             dict(base,client_id='x'),dict(base,source='return 1'),dict(base,action='execute')]
        event={'action':'events','client_id':CLIENT,'sequence':2,'lease':'keyboard_fixture_lease','events':[self.key()]}
        bad += [dict(event,events=[]),dict(event,events=[self.key()]*65),
                dict(event,events=[dict(self.key(),processed=True)]),dict(event,events=[dict(self.key(),phase='change')])]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_keyboard_request(value)
        copied=validate_keyboard_request(event);event['events'][0]['key_code']='A'
        self.assertEqual(copied['events'][0]['key_code'],'W')


class BrowserKeyboardHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/tmp',prefix='keyboard-http-');self.addCleanup(self.temp.cleanup)
        world,_=fixture();self.runtime=PersistentClientScriptRuntime(lambda:world,123)
        self.bridge=BrowserKeyboard(self.runtime)
        self.console=ScriptConsole(Path(self.temp.name)/'client.sock',self.runtime.execute,
                                   keyboard=self.bridge,events=self.runtime.events)
        self.stop=threading.Event();self.errors=[];self.owner_thread=None
        def tick():
            self.owner_thread=threading.get_ident()
            try:
                while not self.stop.is_set():
                    self.console.poll();self.runtime.poll();self.stop.wait(.002)
            except BaseException as error:self.errors.append(error)
            finally:self.console.close();self.runtime.close()
        self.thread=threading.Thread(target=tick,daemon=True);self.thread.start();self.addCleanup(self.stop_worker)
        self.web=WebConsole(socket_path=self.console.path);self.web.start(open_browser=False);self.addCleanup(self.web.close)
        self.sequence=0;self.lease=None

    def stop_worker(self):
        self.stop.set();self.thread.join(4);self.assertFalse(self.thread.is_alive());self.assertEqual(self.errors,[])

    def request(self,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.web.port,timeout=10)
        h={'Origin':self.web.url.rstrip('/'),'X-Console-Key':self.web.key,'Content-Type':'application/json'};h.update(headers or {})
        connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers=h)
        response=connection.getresponse();raw=response.read();status=response.status;connection.close()
        return status,json.loads(raw) if path.startswith('/api/') else raw

    def source(self,code):
        status,result=self.request('/api/execute',{'source':code});self.assertEqual(status,200)
        self.assertTrue(result['ok'],result.get('error'));self.assertFalse(result.get('task_errors'));return result

    def send(self,action,**extra):
        self.sequence+=1;request={'action':action,'client_id':CLIENT,'sequence':self.sequence,**extra}
        if action!='attach':request['lease']=self.lease
        status,result=self.request('/api/input',request);self.assertEqual(status,200)
        self.assertTrue(result['ok'],result.get('error'))
        if action=='attach':self.lease=result['lease']
        return result

    def test_http_socket_native_keyboard_to_cas_and_humanoid_stop(self):
        self.source('''local u=game:GetService("UserInputService");begins=0;ends=0;cancelled=0
u.InputBegan:Connect(function(i,p)assert(not p and i.UserInputState==Enum.UserInputState.Begin);savedInput=i;begins+=1 end)
u.InputEnded:Connect(function(i)assert(i==savedInput);ends+=1 end)
game:GetService("ContextActionService"):BindAction("browser-move",function(_,s)
 local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
 if s==Enum.UserInputState.Begin then h:Move(Vector3.new(0,0,-1),false)
 elseif s==Enum.UserInputState.Cancel or s==Enum.UserInputState.End then h:Move(Vector3.zero,false);cancelled+=1 end
end,false,Enum.KeyCode.W)''')
        self.send('attach')
        result=self.send('events',events=[{'phase':'begin','key_code':'W'}])
        self.assertTrue(any(i.get('vector')==[0,0,-1] for i in result.get('movement_intents',[])),result.get('movement_intents'))
        self.assertEqual(self.source('return begins,game:GetService("UserInputService"):IsKeyDown("W")')['values'],[1,True])
        self.send('release')
        self.assertEqual(self.source('return ends,cancelled,game:GetService("UserInputService"):IsKeyDown("W")')['values'],[1,1,False])
        self.send('detach')
        self.assertEqual(self.source('return game:GetService("UserInputService").KeyboardEnabled')['values'],[False])

    def test_heartbeat_expiry_cancels_native_keys_and_humanoid_movement(self):
        clock=[10.0];self.bridge.clock=lambda:clock[0]
        self.source('''expiredCancelCount=0
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
game:GetService("ContextActionService"):BindAction("expiry-move",function(_,s)
 if s==Enum.UserInputState.Begin then h:Move(Vector3.new(0,0,-1),false)
 elseif s==Enum.UserInputState.Cancel then expiredCancelCount+=1;h:Move(Vector3.zero,false)end
end,false,Enum.KeyCode.W)''')
        self.send('attach');self.send('events',events=[{'phase':'begin','key_code':'W'}])
        clock[0]+=2.1
        self.assertEqual(self.source('''local u=game:GetService("UserInputService")
return u:IsKeyDown("W"),u.KeyboardEnabled,expiredCancelCount''')['values'],[False,False,1])
        moves=[i for result in self.runtime.messages for i in result.get('movement_intents',[])]
        self.assertTrue(any(i.get('vector')==[0,0,-1] for i in moves),moves)
        self.assertEqual(moves[-1]['vector'],[0,0,0])

    def test_authored_wasd_controls_handle_chords_cancellation_replacement_and_stop(self):
        source=(Path(__file__).resolve().parents[1]/'runtime/scripts/browser_keyboard_controls.luau').read_text()
        self.source(source);self.send('attach')
        self.send('events',events=[{'phase':'begin','key_code':'W'},{'phase':'begin','key_code':'Up'},
                                  {'phase':'begin','key_code':'D'}])
        result=self.source('''local d=shared.keyboardControls.direction()
assert(d:FuzzyEq(Vector3.new(1,0,-1).Unit));return d.Magnitude''')
        self.assertAlmostEqual(result['values'][0],1,places=6)
        self.send('events',events=[{'phase':'end','key_code':'W'}])
        self.source('assert(shared.keyboardControls.direction():FuzzyEq(Vector3.new(1,0,-1).Unit))')
        self.send('release');self.source('assert(shared.keyboardControls.direction()==Vector3.zero)')
        self.send('attach');self.send('events',events=[{'phase':'begin','key_code':'Space'}])
        self.source('task.wait(.02)')
        self.assertTrue(any(i['kind']=='Jump' for result in self.runtime.messages
                            for i in result.get('movement_intents',[])))
        self.send('events',events=[{'phase':'end','key_code':'Space'}])
        self.source(source)
        self.source('''local count=0
for _ in pairs(game:GetService("ContextActionService"):GetAllBoundActionInfo())do count+=1 end
assert(count==5);shared.keyboardControls.stop();shared.keyboardControls.stop()
assert(next(game:GetService("ContextActionService"):GetAllBoundActionInfo())==nil)
assert(shared.keyboardControls.direction()==Vector3.zero)''')
        self.source('local c=game:GetService("ContextActionService");for i=1,254 do c:BindAction("occupied"..i,function()end,false)end')
        self.source('local ok,message=pcall(function()\n'+source+'\nend)\n'+"""
assert(not ok and string.find(message,"binding limit"))
local c=game:GetService("ContextActionService");local count=0
for name in pairs(c:GetAllBoundActionInfo())do assert(not string.find(name,"BrowserKeyboard_",1,true));count+=1 end
assert(count==254);c:UnbindAllActions()
""")


    def test_keyboard_does_not_wait_for_the_web_script_execution_lock(self):
        self.send('attach');self.web.execution_lock.acquire()
        try:self.send('events',events=[{'phase':'begin','key_code':'A'}])
        finally:self.web.execution_lock.release()
        self.assertEqual(self.source('return game:GetService("UserInputService"):IsKeyDown("A")')['values'],[True])

    def test_route_validation_authentication_and_assets(self):
        for payload in [{'source':'return 1'},{'action':'events','events':[]}]:
            self.assertEqual(self.request('/api/input',payload)[0],400)
        valid={'action':'attach','client_id':CLIENT,'sequence':1}
        for headers in [{'X-Console-Key':''},{'Origin':'https://example.com'},{'Host':'example.com'}]:
            self.assertEqual(self.request('/api/input',valid,headers)[0],403)
        for asset in ['/input.js','/keyboard-codes.json']:
            status,body=self.request(asset);self.assertEqual(status,200);self.assertTrue(body)
        page=self.request('/')[1];self.assertIn(b'Game input controls',page);self.assertIn(b'/input.js',page)
        self.console.keyboard=None
        status,result=self.request('/api/input',valid)
        self.assertEqual(status,200);self.assertFalse(result['ok'])
        self.assertIn('Restart the terminal client',result['error'])

    def test_key_and_capability_changes_stay_on_the_owner_thread(self):
        seen=[];original=self.runtime.deliver_input
        def deliver(events):seen.append(threading.get_ident());return original(events)
        self.runtime.deliver_input=deliver
        self.send('attach');self.send('events',events=[{'phase':'begin','key_code':'W'}]);self.send('release')
        self.assertTrue(seen);self.assertEqual(set(seen),{self.owner_thread})

if __name__=='__main__':unittest.main()
