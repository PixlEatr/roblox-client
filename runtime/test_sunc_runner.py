"""sUNC runner input limits and the real HttpGet/loadstring path."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import io
import sys
from unittest import mock
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import test_local_compat as compat
import test_cframe_transforms as transforms
import run_sunc
import persistent_runtime
import sunc_client
import terminal_client


class SuncRunnerTests(unittest.TestCase):
    run_script=compat.LocalCompatibilityTests.run_script

    def test_launcher_selects_profile_worker_and_requested_place(self):
        for arguments,expected in [([],sunc_client.PLACE_ID),(['--place=123'],'123')]:
            with self.subTest(arguments=arguments), mock.patch.dict(os.environ,{},clear=False), \
                    mock.patch.object(sys,'argv',['sunc_client.py',*arguments]), \
                    mock.patch.object(sys,'stdout',io.StringIO()), \
                    mock.patch.object(Path,'is_file',return_value=True), \
                    mock.patch.object(terminal_client,'main',return_value=0) as launch:
                self.assertEqual(sunc_client.main(),0)
                launch.assert_called_once()
                self.assertEqual(terminal_client.parse_args().place,expected)
                self.assertEqual(os.environ['RCR_LUAU_LARGE_TEST'],'1')
                self.assertEqual(Path(os.environ['ROBLOX_LUAU_RUST_HOST']),sunc_client.ROOT/'runtime/build/unc-headless-luau')

    def test_requested_place_is_not_used_as_received_place(self):
        self.assertIsNone(run_sunc.actual_place({'place_id':90441122676618}))
        self.assertIsNone(run_sunc.actual_place({'nodes':[{'class_name':'DataModel','properties':{}}]}))
        self.assertEqual(run_sunc.actual_place({'nodes':[{'class_name':'DataModel','properties':{'PlaceId':90441122676618}}]}),90441122676618)

    def test_httpget_executes_actual_response_and_rejects_http_failure(self):
        large_body=b'--'+b'x'*(2300*1024)+b'\nreturn 314159'
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path in ('/source','/large') else 404);self.end_headers()
                self.wfile.write(large_body if self.path=='/large' else b'return 2718, "real HTTP source"' if self.path=='/source' else b'not found')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(lambda:(server.shutdown(),thread.join(2)))
        url=f'http://127.0.0.1:{server.server_port}'
        self.run_script(f'''
local a,b=loadstring(game:HttpGet("{url}/source"))()
assert(a==2718 and b=="real HTTP source")
assert(game:HttpGetAsync("{url}/source")==game:HttpGet("{url}/source",true))
local ok,message=pcall(function()game:HttpGet("{url}/missing")end)
assert(not ok and tostring(message):find("404",1,true))
''')
        self.run_script(f'local body=game:HttpGet("{url}/large");assert(#body=={len(large_body)});assert(loadstring(body)()==314159)')

    def test_large_source_profile_runs_real_nested_compilation(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'large.luau'
            source.write_text('--'+('padding '*30000)+'\nreturn assert(loadstring("return "..string.rep(" ",200*1024).."42"))()')
            env=os.environ.copy();env.pop('RCR_LUAU_LARGE_TEST',None)
            normal=subprocess.run([str(binary),'--source',str(source)],capture_output=True,env=env,timeout=10)
            self.assertEqual(normal.returncode,0,normal.stdout)
            self.assertEqual(json.loads(normal.stdout)['values'],[42])
            env['RCR_LUAU_LARGE_TEST']='1'
            large=subprocess.run([str(binary),'--source',str(source),'--timeout-ms','3000','--memory-mb','128'],capture_output=True,env=env,timeout=10)
            result=json.loads(large.stdout)
            self.assertEqual(large.returncode,0,result);self.assertEqual(result['values'],[42])

    def test_regular_source_file_exact_100_mib_boundary(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'boundary.luau'
            ending=b'\nreturn 100'
            with source.open('wb') as stream:
                stream.write(b'--')
                remaining=100*1024*1024-2-len(ending)
                while remaining:
                    count=min(remaining,1024*1024);stream.write(b'x'*count);remaining-=count
                stream.write(ending)
            env=os.environ.copy();env.pop('RCR_LUAU_LARGE_TEST',None)
            accepted=subprocess.run([str(binary),'--source',str(source),'--timeout-ms','2000'],capture_output=True,env=env,timeout=15)
            self.assertEqual(accepted.returncode,0,accepted.stdout)
            self.assertEqual(json.loads(accepted.stdout)['values'],[100])
            with source.open('ab') as stream:stream.write(b' ')
            rejected=subprocess.run([str(binary),'--source',str(source)],capture_output=True,env=env,timeout=10)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn('104857600',json.loads(rejected.stdout)['error'])

    def test_default_persistent_memory_allows_more_than_old_limit(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.dict(os.environ,{},clear=False), mock.patch.object(persistent_runtime,'RUST_HOST',binary):
            os.environ.pop('RCR_LUAU_LARGE_TEST',None)
            host=persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT);self.addCleanup(host.close)
            self.assertEqual(host.memory_mb,512)
            result=host.call({'op':'execute','timeout_ms':2000,'source':'local t={};for i=1,160 do t[i]=string.rep(string.char(i),1024*1024) end;return #t, #t[160]'})
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['values'],[160,1024*1024])

    def test_large_profile_resumes_root_across_short_polls(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}), mock.patch.object(persistent_runtime,'RUST_HOST',binary):
            with_context=persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT)
            self.addCleanup(with_context.close)
            self.assertEqual(with_context.memory_mb,512)
            first=with_context.call({'op':'execute','timeout_ms':1,'source':'local start=os.clock();local n=0;repeat n+=1 until os.clock()-start>.15;return n>0, "resumed"'})
            self.assertTrue(first['ok'],first)
            self.assertEqual(first['state'],'running',first)
            deadline=time.monotonic()+4
            polls=0
            while time.monotonic()<deadline:
                result=with_context.call({'op':'poll','timeout_ms':5});polls+=1
                self.assertTrue(result['ok'],result)
                for job in result.get('completed_jobs',[]):
                    if job['job_id']==first['job_id']:
                        self.assertTrue(job['ok'],job)
                        self.assertEqual(job['values'],[True,'resumed'])
                        self.assertGreater(polls,1)
                        return
            self.fail('preempted root did not resume')


if __name__=='__main__':unittest.main()
