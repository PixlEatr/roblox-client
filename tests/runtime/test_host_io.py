"""Exercise the missing UNC aliases through actual backing implementations."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import persistent_runtime
import test_local_compat as compat
import test_cframe_transforms as transforms


class HostIOTests(unittest.TestCase):
    run_script=compat.LocalCompatibilityTests.run_script

    def host(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        patch=mock.patch.object(persistent_runtime,'RUST_HOST',binary);patch.start();self.addCleanup(patch.stop)
        host=persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT);self.addCleanup(host.close);return host

    def execute(self,host,source):
        result=host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertTrue(result['ok'],result);return result

    def finish(self,host,job):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            batch=host.call({'op':'poll','timeout_ms':20})
            for result in batch.get('completed_jobs',[]):
                if result['job_id']==job:return result
            time.sleep(.01)
        self.fail('pending console input did not finish')

    def test_all_aliases_are_the_same_backing_function(self):
        self.run_script('''
assert(consoleclear==rconsoleclear and consolecreate==rconsolecreate)
assert(consoledestroy==rconsoledestroy and consoleinput==rconsoleinput)
assert(consoleprint==rconsoleprint and rconsolename==rconsolesettitle)
assert(consolesettitle==rconsolesettitle and queueonteleport==queue_on_teleport)
assert(toclipboard==setclipboard)
''')

    def test_console_state_events_clear_and_close(self):
        host=self.host();result=self.execute(host,'consolecreate();consolesettitle("Example");consoleprint("hello");consoleprint(" world")')
        self.assertEqual([e['kind'] for e in result['console_events']],['create','title','print','print'])
        state=host.host_io('state');self.assertEqual(state['text'],'hello world');self.assertEqual(state['title'],'Example');self.assertTrue(state['visible'])
        self.assertFalse(host.call({'op':'poll'}).get('console_events'))
        self.execute(host,'consoleclear()');self.assertEqual(host.host_io('state')['text'],'')
        self.execute(host,'consoledestroy()');self.assertFalse(host.host_io('state')['visible'])
        self.execute(host,'consoleprint(setmetatable({},{__tostring=function()return "table output"end}));assert(not pcall(consolesettitle,"bad"..string.char(27)))')
        self.assertEqual(host.host_io('state')['text'],'table output')

    def test_console_input_yields_and_consumes_exact_response(self):
        host=self.host();result=self.execute(host,'return consoleinput()')
        self.assertEqual(result['state'],'running')
        request=host.host_io('state')['pending_inputs'][0]
        self.execute(host,'getgenv().stillResponsive=true')
        self.assertTrue(host.host_io('input',id=request,text='hello λ')['accepted'])
        done=self.finish(host,result['job_id']);self.assertTrue(done['ok'],done);self.assertEqual(done['values'],['hello λ'])
        self.assertEqual(host.host_io('state')['pending_inputs'],[])
        with self.assertRaises(RuntimeError):host.host_io('input',id=request,text='duplicate')

    def test_console_destroy_cancels_waiter(self):
        host=self.host();result=self.execute(host,'return rconsoleinput()')
        closed=self.execute(host,'rconsoledestroy()')
        done=next((r for r in closed.get('completed_jobs',[]) if r['job_id']==result['job_id']),None)
        if done is None:done=self.finish(host,result['job_id'])
        self.assertFalse(done['ok']);self.assertIn('console was destroyed',done['error'])

    def test_queue_transfers_only_to_new_vm_once_and_preserves_order(self):
        host=self.host()
        self.execute(host,'queueonteleport("getgenv().order={1}");queue_on_teleport("table.insert(order,2)");assert(order==nil)')
        self.assertEqual(len(host.host_io('teleport_peek')),2)
        destination=host.handoff_teleport(transforms.SNAPSHOT);self.addCleanup(destination.close)
        self.assertTrue(host.closed)
        self.assertEqual(self.execute(destination,'return table.concat(order,",")')['values'],['1,2'])
        self.assertEqual(destination.host_io('teleport_peek'),[])
        self.assertTrue(all(r['ok'] for r in destination.teleport_results))

    def test_queue_validation_limits_and_failed_destination_preserve_queue(self):
        host=self.host()
        self.execute(host,'assert(not pcall(queueonteleport,"local ="));for i=1,16 do queueonteleport("return "..i) end;assert(not pcall(queueonteleport,"return 17"))')
        before=host.host_io('teleport_peek')
        with self.assertRaises(Exception):host.handoff_teleport({'invalid':True})
        self.assertEqual(host.host_io('teleport_peek'),before)
        self.assertFalse(host.closed)

    def test_terminal_input_encoding_and_console_rendering(self):
        import terminal_client
        import io
        source=terminal_client.console_input_source(':input 17 quote " slash \\ λ\x00')
        expected=','.join(map(str,'quote " slash \\ λ\x00'.encode()))
        self.run_script('local got;function rconsoleinputsubmit(id,text)assert(id==17);got=text end;'+source+f';assert(got==string.char({expected}))')
        output=io.StringIO()
        with mock.patch.object(terminal_client.sys,'stdout',output):
            terminal_client.show_result({'ok':True,'console_events':[{'kind':'print','text':'exact text'},{'kind':'input','id':4}]})
        self.assertTrue(output.getvalue().startswith('exact text'))
        self.assertIn(':input 4',output.getvalue())
        with self.assertRaises(ValueError):terminal_client.console_input_source(':input nope value')

    @unittest.skipUnless(os.environ.get('UNC_TEST_SYSTEM_CLIPBOARD')=='1','opt in to a real clipboard roundtrip')
    def test_actual_macos_clipboard_preserving_all_original_formats(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);helper=path/'clipboard-test'
            source=ROOT/'tests/fixtures/clipboard_roundtrip.m'
            built=subprocess.run(['/usr/bin/clang','-arch','arm64','-fobjc-arc',str(source),'-framework','AppKit','-framework','Foundation','-o',str(helper)],capture_output=True,text=True,timeout=30)
            self.assertEqual(built.returncode,0,built.stderr)
            scripts=[]
            for name in ('setclipboard','toclipboard'):
                p=path/(name+'.luau');p.write_text(name+'('+json.dumps('UNC clipboard λ\nroundtrip',ensure_ascii=False)+');return true');scripts.append(str(p))
            result=subprocess.run([str(helper),str(binary),*scripts],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('previous formats restored',result.stdout)



if __name__=='__main__':unittest.main()
