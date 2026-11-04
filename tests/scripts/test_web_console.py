import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from local_runtime import HOST
from script_console import ScriptConsole
from script_runtime import ClientScriptRuntime
from test_world_snapshot import fixture
from launcher import Launcher
from test_launcher import FakeAPI, FakeController


class WebConsoleTests(unittest.TestCase):
    def test_image_load_receipt_validates_token_and_forwards_without_source(self):
        calls=[]
        self.script_console.gui_action=lambda action:(calls.append(action) or {'ok':True})
        action={'kind':'image_loaded','id':'icon','token':'a'*32}
        self.assertEqual(self.request('/api/gui',raw=json.dumps(action))[0],200)
        for patch in ({'token':'b'*31},{'token':'g'*32},{'token':True},{'loaded':True},{'id':None}):
            self.assertEqual(self.request('/api/gui',raw=json.dumps({**action,**patch}))[0],400)
        self.assertEqual(calls,[action])

    def test_textbox_focus_routes_forward_only_typed_bounded_actions(self):
        calls=[]
        self.script_console.gui_action=lambda action:(calls.append(action) or {'ok':True})
        focus={'kind':'text_focus','id':'box'}
        edit={'kind':'text_edit','id':'box','revision':1,'text':'é'}
        blur={'kind':'text_blur','id':'box','revision':1,'submitted':True}
        selection={'kind':'text_select','id':'box','revision':1,'editorRevision':0,'cursor':3,'anchor':1}
        edit_with_selection={**edit,'editorRevision':0,'cursor':3,'anchor':-1}
        for action in (focus,edit,blur,selection,edit_with_selection):
            self.assertEqual(self.request('/api/gui',raw=json.dumps(action))[0],200)
        for action in ({**focus,'extra':True},{**edit,'text':'é'*2049},{**edit,'revision':True},
                       {**edit,'revision':-1},{**blur,'submitted':1},{**selection,'cursor':0},
                       {**selection,'anchor':0},{**edit_with_selection,'editorRevision':False}):
            self.assertEqual(self.request('/api/gui',raw=json.dumps(action))[0],400)
        self.assertEqual(calls,[focus,edit,blur,selection,edit_with_selection])

    def test_gui_wheel_metadata_is_bounded_and_forwarded(self):
        calls=[]
        self.script_console.gui_action=lambda action:(calls.append(action) or {'ok':True,'values':[]})
        action={'id':'child','kind':'wheel','delta':[0,40],'position':[120.5,80],'steps':-2}
        self.assertEqual(self.request('/api/gui',raw=json.dumps(action))[0],200)
        for patch in ({'steps':True},{'steps':1.5},{'steps':129},{'position':[float('nan'),10]},
                      {'position':[10]},{'position':[40000,0]},{'position':['10',20]},{'extra':True}):
            self.assertEqual(self.request('/api/gui',raw=json.dumps({**action,**patch}))[0],400)
        self.assertEqual(self.request('/api/gui',raw=json.dumps(action),headers={'X-Console-Key':''})[0],403)
        self.assertEqual(calls,[action]);self.assertEqual(self.runtime.executions,0)

    def test_gui_scroll_route_validates_delta_and_never_executes_source(self):
        calls=[]
        self.script_console.gui_action=lambda action:(calls.append(action) or {'ok':True,'values':[]})
        action={'id':'scroll','kind':'scroll','delta':[12.5,-48]}
        status,body,_=self.request('/api/gui',raw=json.dumps(action))
        self.assertEqual(status,200);self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(calls,[action]);self.assertEqual(self.runtime.executions,0)
        for delta in ([True,1],[float('nan'),0],[float('inf'),0],[4097,0],[0],None,{'x':0,'y':20}):
            self.assertEqual(self.request('/api/gui',raw=json.dumps({**action,'delta':delta}))[0],400)
        self.assertEqual(self.request('/api/gui',raw=json.dumps({**action,'source':'return 1'}))[0],400)
        self.assertEqual(self.request('/api/gui',raw=json.dumps(action),headers={'X-Console-Key':''})[0],403)
        self.assertEqual(calls,[action]);self.assertEqual(self.request('/gui-scroll.js')[0],200)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp', prefix='luau-web-')
        self.addCleanup(self.temp.cleanup)
        world, _ = fixture()
        self.runtime = ClientScriptRuntime(lambda: world, 123)
        self.script_console = ScriptConsole(Path(self.temp.name) / 'client.sock', self.runtime.execute)
        self.addCleanup(self.script_console.close)
        self.stop = threading.Event()

        def tick():
            while not self.stop.is_set():
                self.script_console.poll()
                self.stop.wait(.002)

        self.worker = threading.Thread(target=tick, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_worker)
        self.app = Launcher(0, api=FakeAPI(), controller=FakeController(), socket_path=self.script_console.path)
        self.web = self.app.surface
        threading.Thread(target=self.app.server.serve_forever,daemon=True).start()
        self.addCleanup(self.app.close)

    def stop_worker(self):
        self.stop.set()
        self.worker.join(timeout=3)

    def request(self, path, source=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.web.port, timeout=10)
        body = raw if raw is not None else json.dumps({'source': source}) if source is not None else None
        request_headers = {'Origin': self.web.url.rstrip('/'), 'X-Console-Key': self.web.key,
                           'Content-Type': 'application/json','X-Rove-Key':self.app.key}
        request_headers.update(headers or {})
        if path.startswith('/api/'):path=path.replace('/api/','/api/runtime/',1)
        connection.request('POST' if body is not None else 'GET', path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        status, result_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, data, result_headers

    def test_display_route_rejects_invalid_profiles_and_keeps_authentication(self):
        received = []
        self.script_console.display = lambda profile: received.append(profile) or {'ok': True}
        for payload in [{'size': [True, 2]}, {'size': [800, 600], 'unexpected': 1},
                        {'size': [800, 600], 'core_insets': [0, 700, 0, 0]}]:
            self.assertEqual(self.request('/api/display', raw=json.dumps(payload))[0], 400)
        self.assertEqual(self.request('/api/display', raw='{"size":[800,600]}',
                                      headers={'X-Console-Key': ''})[0], 403)
        self.assertEqual(received, [])
        status, body, _ = self.request('/api/display', raw='{"size":[800,600]}')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['ok'])
        self.assertEqual(received[0]['size'], [800, 600])

    def test_display_http_socket_native_resize_without_restarting_vm(self):
        from persistent_runtime import PersistentClientScriptRuntime
        world, _ = fixture()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        self.addCleanup(runtime.close)
        self.script_console.execute = runtime.execute
        self.script_console.display = runtime.update_display
        for width, height in [(800, 600), (1280, 720)]:
            status, body, _ = self.request('/api/display', raw=json.dumps({'size': [width, height]}))
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['ok'])
            status, body, _ = self.request('/api/execute', 'return workspace.CurrentCamera.ViewportSize.X,workspace.CurrentCamera.ViewportSize.Y')
            result = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(result['ok'], result.get('error'))
            self.assertEqual(result['values'], [width, height])
        self.assertEqual(runtime.executions, 2)

    def test_page_assets_status_and_localhost_binding(self):
        self.assertEqual(self.web.server.server_address[0], '127.0.0.1')
        status, page, headers = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(self.web.key.encode(), page)
        self.assertIn(b'Luau script', page)
        self.assertIn(b'<option value="RBXGeneral">RBXGeneral</option>', page)
        self.assertIn(b'<option value="RBXGlobal">RBXGlobal</option>', page)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path in ('/style.css', '/app.js'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertTrue(json.loads(self.request('/api/status')[1])['connected'])

    @unittest.skipUnless(HOST.is_file(), 'build the local runtime first')
    def test_real_luau_multiline_output_errors_and_recovery(self):
        status, body, _ = self.request('/api/execute', '''
local player=game:GetService("Players").LocalPlayer
print("web player",player.UserId)
warn("web warning")
return 2+2
''')
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [4])
        self.assertIn('web player\t123', result['output'])
        self.assertIn('web warning', result['output'])
        failed = json.loads(self.request('/api/execute', 'error("web failure")')[1])
        self.assertFalse(failed['ok'])
        self.assertIn('web failure', failed['error'])
        self.assertTrue(json.loads(self.request('/api/execute', 'return 42')[1])['ok'])

    def test_rejects_cross_site_missing_key_and_invalid_host(self):
        for headers in ({'Origin': 'https://example.com'}, {'X-Console-Key': ''},
                        {'X-Console-Key': 'wrong'}, {'Host': 'example.com'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/execute', 'return 1', headers=headers)[0], 403)
        self.assertEqual(self.runtime.executions, 0)
        self.assertEqual(self.request('/', headers={'Host': 'example.com'})[0], 403)

    def test_invalid_source_and_concurrent_execution_are_rejected(self):
        for raw in ('{"source":123}', '{"source":""}', '[]', 'invalid',
                    '{"source":"return 1","timeout_ms":9999}'):
            self.assertEqual(self.request('/api/execute', raw=raw)[0], 400)
        self.assertEqual(self.request('/api/execute', source='x' * (100 * 1024 * 1024 + 1))[0], 400)
        self.assertEqual(self.request('/../secrets/roblox-cookie.txt')[0], 404)
        self.web.execution_lock.acquire()
        try:
            self.assertEqual(self.request('/api/execute', 'return 1')[0], 409)
        finally:
            self.web.execution_lock.release()
        self.assertEqual(self.runtime.executions, 0)

    def test_disconnected_runtime_is_reported(self):
        self.web.socket_path = Path(self.temp.name) / 'missing.sock'
        self.assertFalse(json.loads(self.request('/api/status')[1])['connected'])
        self.assertEqual(self.request('/api/execute', 'return 1')[0], 503)

    def test_visual_assets_require_current_frame_scope_and_local_authorization(self):
        from unittest.mock import Mock
        from test_render_assets import mesh_bytes
        fetcher = Mock(return_value=mesh_bytes())
        self.web.render_assets.fetcher = fetcher
        payload = json.dumps({'kind': 'mesh', 'reference': 'rbxassetid://7'})
        self.assertEqual(self.request('/api/render-asset', raw=payload)[0], 400)
        self.web.render_assets.observe({'parts': [{'mesh': {'id': 'rbxassetid://7'}}]})
        for headers in ({'Origin': 'https://example.com'}, {'X-Console-Key': ''},
                        {'Host': 'example.com'}):
            self.assertEqual(self.request('/api/render-asset', raw=payload, headers=headers)[0], 403)
        fetcher.assert_not_called()
        self.web.execution_lock.acquire()
        try:
            status, body, _ = self.request('/api/render-asset', raw=payload)
        finally:
            self.web.execution_lock.release()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['mesh']['indices'], [0, 1, 2])
        self.assertEqual(self.runtime.executions, 0)
        self.assertEqual(self.request('/api/render-asset', raw='{}')[0], 400)
        self.assertEqual(self.request('/api/render-asset', raw=' ' * 2049)[0], 413)
        self.web.render_assets.observe({})
        self.assertEqual(self.request('/api/render-asset', raw=payload)[0], 400)
        self.assertEqual(fetcher.call_count, 1)

    def test_font_asset_route_loads_only_a_current_text_choice(self):
        from unittest.mock import Mock
        from test_render_fonts import sfnt,family,descriptor
        fetcher=Mock(side_effect=[family(('rbxasset://fonts/Regular.ttf',400,'normal')),sfnt()])
        self.web.render_assets.fetcher=fetcher
        font=descriptor();payload=json.dumps({'kind':'font','reference':font['family'],'weight':400,'style':0})
        self.assertEqual(self.request('/api/render-asset',raw=payload)[0],400)
        self.web.render_assets.observe({'gui':[{'fontFace':font}]})
        self.assertEqual(self.request('/api/render-asset',raw=payload,headers={'X-Console-Key':''})[0],403)
        status,body,_=self.request('/api/render-asset',raw=payload)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['mime'],'font/ttf')
        self.assertEqual(fetcher.call_count,2)
        for invalid in [{'kind':'font','reference':font['family']},
                        {'kind':'font','reference':font['family'],'weight':True,'style':0},
                        {'kind':'mesh','reference':'rbxassetid://1','weight':400,'style':0}]:
            self.assertEqual(self.request('/api/render-asset',raw=json.dumps(invalid))[0],400)
        self.assertEqual(self.request('/font-loader.js')[0],200)
        self.assertEqual(self.request('/text-layout.js')[0],200)

    def test_late_results_endpoint_preserves_errors_and_requires_local_authorization(self):
        self.script_console.events=lambda after:{'ok':True,'values':[],
            'events':[{'sequence':after+1,'completed_jobs':[{'job_id':7,'ok':False,
                'error':'SendAsync failed: response_timeout','values':[]}]}],'cursor':after+1}
        status,body,_=self.request('/api/events',raw=json.dumps({'after':1}))
        self.assertEqual(status,200)
        self.assertIn('response_timeout',json.loads(body)['events'][0]['completed_jobs'][0]['error'])
        self.assertEqual(self.request('/api/events',raw='{"after":-1}')[0],400)
        self.assertEqual(self.request('/api/events',raw='{"after":1}',headers={'X-Console-Key':''})[0],403)

    def test_chat_input_uses_dedicated_host_operation_and_rejects_source_or_flag_injection(self):
        calls = []
        self.script_console.user_chat = lambda text, channel='RBXGeneral': (
            calls.append((text, channel)) or
            {'ok': True, 'values': [], 'state': 'running', 'job_id': len(calls)})
        status, body, _ = self.request('/api/user-chat', raw=json.dumps({'text': 'chat test'}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['state'], 'running')
        status, body, _ = self.request('/api/user-chat', raw=json.dumps({
            'text': 'global test', 'channel': 'RBXGlobal'}))
        self.assertEqual(status, 200)
        self.assertEqual(calls, [('chat test', 'RBXGeneral'), ('global test', 'RBXGlobal')])
        self.assertEqual(self.runtime.executions, 0)
        for request in ({'text': 'chat test', 'verified': True}, {'text': 'hi', 'source': 'return 1'},
                        {'text': 'bad route', 'channel': 'RBXPrivate'},
                        {'text': ''}, {'text': 'x'*201}, {'text': '\0'}, {'text': 123}):
            self.assertEqual(self.request('/api/user-chat', raw=json.dumps(request))[0], 400)
        self.assertEqual(self.request('/api/user-chat', raw='{"text":"hi"}', headers={'X-Console-Key':''})[0], 403)
        self.assertEqual(calls, [('chat test', 'RBXGeneral'), ('global test', 'RBXGlobal')])

    def test_space_jump_route_executes_only_the_fixed_own_humanoid_intention(self):
        from persistent_runtime import PersistentClientScriptRuntime
        from test_live_physics_trial import ready_world
        world, _ = ready_world()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        self.addCleanup(runtime.close)
        calls = []
        self.script_console.execute = lambda source, *, timeout_ms=1000: (
            calls.append((source, timeout_ms)) or runtime.execute(source, timeout_ms=timeout_ms))
        status, body, _ = self.request('/api/jump', raw=json.dumps({'action': 'jump'}))
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual([intent['kind'] for intent in result['movement_intents']], ['Jump'])
        self.assertEqual(result['local_movement']['mode'], 'local_prediction')
        self.assertFalse(result['local_movement']['server_confirmed'])
        self.assertEqual(len(calls), 1)
        source, timeout_ms = calls[0]
        self.assertEqual(timeout_ms, 1000)
        self.assertIn('Players").LocalPlayer', source)
        self.assertIn('local character = player and player.Character', source)
        self.assertIn('FindFirstChildOfClass("Humanoid")', source)
        self.assertIn('humanoid.Jump = true', source)
        self.assertNotIn('SetStateEnabled', source)
        self.assertNotIn('FireServer', source)
        for payload in ({'action': 'jump', 'source': 'return 1'}, {'action': 'fly'},
                        [], None):
            self.assertEqual(self.request('/api/jump', raw=json.dumps(payload))[0], 400)
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.executions, 1)
        self.web.socket_path = Path(self.temp.name) / 'missing.sock'
        self.assertEqual(self.request('/api/jump', raw='{"action":"jump"}')[0], 503)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
