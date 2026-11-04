"""Local launcher request boundaries and owned-process lifecycle checks."""
import contextlib
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

import launcher


class FakeAPI:
    def account(self):
        return {'id':123,'name':'Fixture','displayName':'Fixture','avatar':None,'savedPrivateServer':False}
    def friends(self):
        return {'friends':[],'warning':None}
    def games(self):
        return {'games':[],'favoriteIds':[],'recentIds':[]}


class FakeController:
    def __init__(self):self.calls=[]
    def status(self):return {'state':'idle','game':None,'events':[],'error':None,'editorUrl':None}
    def play(self,target,game):self.calls.append((target,game));return {'state':'preparing'}
    def leave(self):self.calls.append('leave');return {'state':'idle'}
    def close(self):pass


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.controller=FakeController()
        self.app=launcher.Launcher(0,api=FakeAPI(),controller=self.controller)
        self.thread=threading.Thread(target=self.app.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.app.close)

    def request(self,path='/',method='GET',body=None,headers=None):
        client=http.client.HTTPConnection('127.0.0.1',self.app.port,timeout=3)
        default={'Host':f'127.0.0.1:{self.app.port}','X-Rove-Key':self.app.key}
        default.update(headers or {})
        client.request(method,path,json.dumps(body) if body is not None else None,default)
        result=client.getresponse();data=result.read();client.close();return result.status,data

    def test_page_has_only_local_key_and_no_cookie(self):
        status,body=self.request()
        self.assertEqual(status,200)
        self.assertIn(self.app.key.encode(),body)
        self.assertNotIn(b'ROBLOSECURITY',body)
        self.assertNotIn(b'roblox-cookie',body)

    def test_workspace_url_and_session_links_use_the_same_interface(self):
        _,home=self.request('/')
        for path in ('/workspace','/workspace/','/workspace?renderer=software'):
            status,page=self.request(path)
            self.assertEqual(status,200)
            self.assertEqual(page,home)
        with patch.object(self.app.surface,'connected',return_value=True):
            status,body=self.request('/api/sessions')
        session=json.loads(body)['sessions'][0]
        self.assertEqual(status,200)
        self.assertEqual(session['url'],self.app.url+'workspace')
        self.assertEqual(session['editorUrl'],session['url'])

    def test_every_page_script_loads_and_stale_backend_is_rejected(self):
        from desktop_backend import ready
        _,page=self.request('/workspace')
        scripts=re.findall(rb'<script\b[^>]*\bsrc="([^"]+)"',page)
        self.assertTrue(scripts)
        for script in scripts:
            path=script.decode()
            self.assertEqual(self.request(path)[0],200,path)
            status,body=self.request(path,method='HEAD')
            self.assertEqual(status,200,path);self.assertEqual(body,b'')
        with patch('launcher_setup.readiness',return_value={'launcherVersion':2}),patch.object(self.app.surface,'connected',return_value=False):
            self.assertEqual(ready(self.app.port),self.app.url)
            with tempfile.TemporaryDirectory() as folder:
                assets=Path(folder)
                (assets/'index.html').write_bytes(page.replace(b'/workspace.js',b'/missing-module.js'))
                with patch.object(launcher,'ASSETS',assets):
                    self.assertIsNone(ready(self.app.port))

    def test_new_ui_module_is_served_without_restarting_or_exposing_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            assets=Path(folder)
            with patch.object(launcher,'ASSETS',assets):
                (assets/'new-module.js').write_text('window.NewModule={};')
                (assets/'private.txt').write_text('private fixture')
                (assets/'linked.js').symlink_to(assets/'private.txt')
                self.assertEqual(self.request('/new-module.js'),(200,b'window.NewModule={};'))
                self.assertEqual(self.request('/linked.js')[0],404)
                self.assertNotEqual(self.request('/private.txt')[0],200)
                self.assertNotEqual(self.request('/../private.txt')[0],200)

    def test_private_data_and_actions_require_local_credentials(self):
        self.assertEqual(self.request('/api/account',headers={'X-Rove-Key':'wrong'})[0],403)
        self.assertEqual(self.request('/api/account',headers={'Host':'attacker.example'})[0],403)
        for origin in ('https://attacker.example','null',''):
            status,_=self.request('/api/play','POST',{'target':'1818','game':{}},headers={'Origin':origin})
            self.assertEqual(status,403)
        self.assertFalse(self.controller.calls)

    def test_play_and_leave_dispatch_only_after_valid_request(self):
        headers={'Origin':f'http://127.0.0.1:{self.app.port}'}
        status,_=self.request('/api/play','POST',{'target':'1818','game':{'name':'Fixture'}},headers)
        self.assertEqual(status,200)
        self.assertEqual(self.controller.calls,[('1818',{'name':'Fixture'})])
        self.assertEqual(self.request('/api/leave','POST',{},headers)[0],200)
        self.assertEqual(self.controller.calls[-1],'leave')
        self.assertEqual(self.request('/api/play','POST',{'target':'1818','game':'bad'},headers)[0],400)

    def test_process_start_is_blocked_before_selector_when_lease_is_owned(self):
        controller=launcher.GameController()
        with patch.object(launcher,'acquire_session_lease',side_effect=RuntimeError('Another client owns the session')),patch.object(launcher,'prepare_target') as select,patch.object(launcher.subprocess,'Popen') as spawn:
            controller.play('1818',{})
            deadline=time.monotonic()+2
            while controller.worker_running and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(controller.status()['state'],'error')
            select.assert_not_called();spawn.assert_not_called()

    def test_cancel_preparing_does_not_spawn_game(self):
        controller=launcher.GameController()
        entered=threading.Event();release=threading.Event()
        def select(target):entered.set();release.wait(2);return 1818
        with patch.object(launcher,'acquire_session_lease',return_value=contextlib.nullcontext()),patch.object(launcher,'prepare_target',side_effect=select),patch.object(launcher.subprocess,'Popen') as spawn:
            controller.play('1818',{})
            self.assertTrue(entered.wait(1));controller.leave();release.set()
            deadline=time.monotonic()+2
            while controller.worker_running and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(controller.status()['state'],'idle');spawn.assert_not_called()

    def test_unsafe_thumbnail_hosts_are_not_exposed(self):
        self.assertIsNone(launcher.image_url('https://rbxcdn.com.evil.test/a.png'))
        self.assertIsNone(launcher.image_url('http://t1.rbxcdn.com/a.png'))
        self.assertEqual(launcher.image_url('https://t1.rbxcdn.com/a.png'),'https://t1.rbxcdn.com/a.png')


if __name__=='__main__':unittest.main()
